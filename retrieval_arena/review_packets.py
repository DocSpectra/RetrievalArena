from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any

from .dual_profile import _exact, _finite_unit, _integer, _sha, _text, self_hash, sha256_json
from .errors import ValidationError


SCHEMA_REVIEW_POLICY = "retrieval_arena.review_policy.v1"
SCHEMA_REVIEW_CANDIDATE = "retrieval_arena.review_candidate.v1"
SCHEMA_REVIEW_PACKET = "retrieval_arena.review_packet.v1"
SCHEMA_JUDGMENT = "retrieval_arena.judgment_artifact.v1"
SCHEMA_JUDGMENT_IMPORT = "retrieval_arena.judgment_import.v1"

REDACTED_FIELDS = {
    "condition_labels",
    "system_labels",
    "outcome_labels",
    "ranks",
    "deltas",
    "lineage_classes",
}


def validate_review_policy(policy: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "packet_id",
        "dataset_id",
        "query_set_id",
        "ordered_snapshot_ids",
        "seed",
        "primary_judgment_ceiling",
        "duplicate_rate",
        "sampling_method",
        "stratification_fields",
        "allocation_method",
        "minimum_one_when_feasible",
        "stopping_rule",
        "adjudication_policy",
        "assessor_classes",
        "allowed_assessor_ids",
        "guidelines_sha256",
        "outcome_revealed",
        "policy_sha256",
    }
    _exact(policy, fields, "review policy")
    if policy["schema_version"] != SCHEMA_REVIEW_POLICY:
        raise ValidationError("unsupported review policy schema")
    for name in ("packet_id", "dataset_id", "query_set_id"):
        _text(policy[name], name)
    snapshots = policy["ordered_snapshot_ids"]
    if not isinstance(snapshots, list) or not snapshots or snapshots != list(dict.fromkeys(snapshots)):
        raise ValidationError("ordered_snapshot_ids must be non-empty and unique")
    _integer(policy["seed"], "review seed")
    ceiling = _integer(policy["primary_judgment_ceiling"], "primary_judgment_ceiling", 1)
    if ceiling > 240:
        raise ValidationError("primary judgment ceiling exceeds project governance maximum 240")
    if policy["duplicate_rate"] != 0.10:
        raise ValidationError("duplicate_rate must be the project-adopted S24 precedent value 0.10")
    expected = {
        "sampling_method": "stratified_simple_random_without_replacement",
        "stratification_fields": ["snapshot_id", "candidate_class", "rank_band"],
        "allocation_method": "proportional_largest_remainder",
        "minimum_one_when_feasible": True,
        "stopping_rule": "frame_exhausted_or_fixed_quotas_complete",
        "adjudication_policy": "blind_all_duplicate_disagreements_s24_precedent",
    }
    for name, value in expected.items():
        if policy[name] != value:
            raise ValidationError(f"review policy mismatch:{name}")
    for name in ("assessor_classes", "allowed_assessor_ids"):
        values = policy[name]
        if not isinstance(values, list) or not values or values != list(dict.fromkeys(values)):
            raise ValidationError(f"{name} must be non-empty and unique")
        for value in values:
            _text(value, name)
    _sha(policy["guidelines_sha256"], "guidelines_sha256")
    if not isinstance(policy["outcome_revealed"], bool):
        raise ValidationError("outcome_revealed must be boolean")
    if _sha(policy["policy_sha256"], "policy_sha256") != self_hash(policy, "policy_sha256"):
        raise ValidationError("review policy self-hash mismatch")
    return policy


def validate_review_candidates(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    fields = {
        "schema_version",
        "query_id",
        "support_target_id",
        "snapshot_id",
        "candidate_class",
        "rank_band",
        "query_text",
        "requirement_texts",
        "candidate_evidence",
        "bounded_context",
        "condition_label",
        "system_label",
        "outcome_label",
        "rank",
        "delta",
        "lineage_class",
        "candidate_provenance_sha256",
    }
    result: list[dict[str, Any]] = []
    for row in rows:
        _exact(row, fields, "review candidate")
        if row["schema_version"] != SCHEMA_REVIEW_CANDIDATE:
            raise ValidationError("unsupported review candidate schema")
        for name in (
            "query_id",
            "support_target_id",
            "snapshot_id",
            "candidate_class",
            "rank_band",
            "query_text",
            "candidate_evidence",
            "bounded_context",
            "condition_label",
            "system_label",
            "outcome_label",
            "lineage_class",
        ):
            _text(row[name], name)
        if row["snapshot_id"] not in policy["ordered_snapshot_ids"]:
            raise ValidationError("review candidate snapshot is not registered")
        if not isinstance(row["requirement_texts"], list) or not row["requirement_texts"]:
            raise ValidationError("review candidate requirement_texts must be non-empty")
        for text in row["requirement_texts"]:
            _text(text, "review candidate requirement text")
        _integer(row["rank"], "review candidate rank", 1)
        if isinstance(row["delta"], bool) or not isinstance(row["delta"], (int, float)) or not math.isfinite(float(row["delta"])):
            raise ValidationError("review candidate delta must be finite")
        _sha(row["candidate_provenance_sha256"], "candidate_provenance_sha256")
        result.append(row)
    if not result:
        raise ValidationError("review candidate frame must be non-empty")
    return result


def _largest_remainder_allocations(strata: dict[tuple[str, str, str], list[dict[str, Any]]], total: int) -> dict[tuple[str, str, str], int]:
    sizes = {key: len(rows) for key, rows in strata.items()}
    frame_count = sum(sizes.values())
    if total >= frame_count:
        return sizes
    keys = sorted(strata)
    allocations = {key: 0 for key in keys}
    if total >= len(keys):
        for key in keys:
            allocations[key] = 1
        remaining = total - len(keys)
        residual_capacity = {key: sizes[key] - 1 for key in keys}
    else:
        remaining = total
        residual_capacity = sizes.copy()
    if remaining == 0:
        return allocations
    capacity_total = sum(residual_capacity.values())
    if capacity_total == 0:
        return allocations
    quotas = {key: remaining * residual_capacity[key] / capacity_total for key in keys}
    for key in keys:
        floor = min(residual_capacity[key], math.floor(quotas[key]))
        allocations[key] += floor
    left = total - sum(allocations.values())
    order = sorted(keys, key=lambda key: (-(quotas[key] - math.floor(quotas[key])), key))
    for key in order:
        if left == 0:
            break
        if allocations[key] < sizes[key]:
            allocations[key] += 1
            left -= 1
    if left:
        for key in keys:
            while left and allocations[key] < sizes[key]:
                allocations[key] += 1
                left -= 1
    return allocations


def build_review_packet(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    validate_review_policy(policy)
    rows = validate_review_candidates(rows, policy)
    by_pair: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_pair[(row["snapshot_id"], row["query_id"], row["support_target_id"])].append(row)
    frame: list[dict[str, Any]] = []
    for key in sorted(by_pair):
        candidates = sorted(
            by_pair[key],
            key=lambda row: (row["condition_label"], row["system_label"], row["rank"], row["candidate_provenance_sha256"]),
        )
        first = candidates[0]
        visible_identity = {
            (row["query_text"], tuple(row["requirement_texts"]), row["candidate_evidence"], row["bounded_context"])
            for row in candidates
        }
        if len(visible_identity) != 1:
            raise ValidationError(f"deduplicated review pair has inconsistent visible evidence:{key}")
        classes = sorted({row["candidate_class"] for row in candidates})
        rank_bands = sorted({row["rank_band"] for row in candidates})
        if len(classes) != 1 or len(rank_bands) != 1:
            raise ValidationError(f"deduplicated review pair has inconsistent stratum:{key}")
        pair_id = sha256_json({"snapshot_id": key[0], "query_id": key[1], "support_target_id": key[2]})
        frame.append(
            {
                "pair_id": pair_id,
                "snapshot_id": key[0],
                "query_id": key[1],
                "support_target_id": key[2],
                "candidate_class": classes[0],
                "rank_band": rank_bands[0],
                "query_text": first["query_text"],
                "requirement_texts": first["requirement_texts"],
                "candidate_evidence": first["candidate_evidence"],
                "bounded_context": first["bounded_context"],
                "condition_labels": sorted({row["condition_label"] for row in candidates}),
                "system_labels": sorted({row["system_label"] for row in candidates}),
                "outcome_labels": sorted({row["outcome_label"] for row in candidates}),
                "ranks": sorted({row["rank"] for row in candidates}),
                "deltas": sorted({float(row["delta"]) for row in candidates}),
                "lineage_classes": sorted({row["lineage_class"] for row in candidates}),
                "candidate_provenance_sha256s": sorted({row["candidate_provenance_sha256"] for row in candidates}),
            }
        )
    strata: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in frame:
        strata[(row["snapshot_id"], row["candidate_class"], row["rank_band"])].append(row)
    primary_count = min(len(frame), policy["primary_judgment_ceiling"])
    allocations = _largest_remainder_allocations(strata, primary_count)
    selected: list[dict[str, Any]] = []
    inclusion_probabilities: dict[str, float] = {}
    for index, key in enumerate(sorted(strata)):
        population = sorted(strata[key], key=lambda row: row["pair_id"])
        rng = random.Random(policy["seed"] + index)
        chosen = rng.sample(population, allocations[key])
        chosen.sort(key=lambda row: row["pair_id"])
        selected.extend(chosen)
        probability = allocations[key] / len(population)
        for row in population:
            inclusion_probabilities[row["pair_id"]] = probability
    selected.sort(key=lambda row: row["pair_id"])
    duplicate_count = math.ceil(0.10 * len(selected))
    duplicate_rng = random.Random(policy["seed"] ^ 0x5A17)
    duplicate_ids = sorted(row["pair_id"] for row in duplicate_rng.sample(selected, duplicate_count)) if selected else []
    assessor_rows = [
        {
            "assessment_id": sha256_json({"packet_id": policy["packet_id"], "pair_id": row["pair_id"], "kind": "primary"}),
            "pair_id": row["pair_id"],
            "assessment_kind": "primary",
            "query_id": row["query_id"],
            "support_target_id": row["support_target_id"],
            "query_text": row["query_text"],
            "requirement_texts": row["requirement_texts"],
            "candidate_evidence": row["candidate_evidence"],
            "bounded_context": row["bounded_context"],
        }
        for row in selected
    ]
    assessor_rows.extend(
        {
            **{key: value for key, value in row.items() if key != "assessment_id"},
            "assessment_id": sha256_json({"packet_id": policy["packet_id"], "pair_id": row["pair_id"], "kind": "duplicate"}),
            "assessment_kind": "duplicate",
        }
        for row in assessor_rows[:]
        if row["pair_id"] in duplicate_ids
    )
    order_rng = random.Random(policy["seed"] ^ 0xA44E)
    order_rng.shuffle(assessor_rows)
    frame_hash = sha256_json(frame)
    selected_hash = sha256_json(selected)
    assessor_hash = sha256_json(assessor_rows)
    packet = {
        "schema_version": SCHEMA_REVIEW_PACKET,
        "packet_id": policy["packet_id"],
        "dataset_id": policy["dataset_id"],
        "query_set_id": policy["query_set_id"],
        "ordered_snapshot_ids": policy["ordered_snapshot_ids"],
        "review_policy_sha256": policy["policy_sha256"],
        "classification": "exploratory" if policy["outcome_revealed"] else "confirmatory",
        "frame_count": len(frame),
        "frame_sha256": frame_hash,
        "candidate_pool_sha256": sha256_json(rows),
        "primary_count": len(selected),
        "duplicate_count": duplicate_count,
        "maximum_adjudication_count": duplicate_count,
        "maximum_action_count": len(selected) + 2 * duplicate_count,
        "governance_maximum_action_count_at_ceiling": 288,
        "stratum_allocations": [
            {
                "snapshot_id": key[0],
                "candidate_class": key[1],
                "rank_band": key[2],
                "frame_count": len(strata[key]),
                "sample_count": allocations[key],
                "inclusion_probability": allocations[key] / len(strata[key]),
            }
            for key in sorted(strata)
        ],
        "selected_pair_ids": [row["pair_id"] for row in selected],
        "duplicate_pair_ids": duplicate_ids,
        "inclusion_probabilities": dict(sorted(inclusion_probabilities.items())),
        "selected_frame_sha256": selected_hash,
        "assessor_view_records": assessor_rows,
        "assessor_view_sha256": assessor_hash,
        "packet_sha256": "0" * 64,
    }
    packet["packet_sha256"] = self_hash(packet, "packet_sha256")
    validate_review_packet(packet)
    return packet


def validate_review_packet(packet: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "packet_id",
        "dataset_id",
        "query_set_id",
        "ordered_snapshot_ids",
        "review_policy_sha256",
        "classification",
        "frame_count",
        "frame_sha256",
        "candidate_pool_sha256",
        "primary_count",
        "duplicate_count",
        "maximum_adjudication_count",
        "maximum_action_count",
        "governance_maximum_action_count_at_ceiling",
        "stratum_allocations",
        "selected_pair_ids",
        "duplicate_pair_ids",
        "inclusion_probabilities",
        "selected_frame_sha256",
        "assessor_view_records",
        "assessor_view_sha256",
        "packet_sha256",
    }
    _exact(packet, fields, "review packet")
    if packet["schema_version"] != SCHEMA_REVIEW_PACKET:
        raise ValidationError("unsupported review packet schema")
    if packet["classification"] not in {"confirmatory", "exploratory"}:
        raise ValidationError("review packet classification invalid")
    for name in ("review_policy_sha256", "frame_sha256", "candidate_pool_sha256", "selected_frame_sha256", "assessor_view_sha256"):
        _sha(packet[name], name)
    primary = _integer(packet["primary_count"], "primary_count")
    duplicate = _integer(packet["duplicate_count"], "duplicate_count")
    if primary > 240 or duplicate != math.ceil(0.10 * primary):
        raise ValidationError("review packet governance count mismatch")
    if packet["maximum_adjudication_count"] != duplicate or packet["maximum_action_count"] != primary + 2 * duplicate:
        raise ValidationError("review packet action arithmetic mismatch")
    if packet["maximum_action_count"] > 288 or packet["governance_maximum_action_count_at_ceiling"] != 288:
        raise ValidationError("review packet exceeds the 288-action arithmetic maximum")
    if set(packet["duplicate_pair_ids"]) - set(packet["selected_pair_ids"]):
        raise ValidationError("duplicate sample must be drawn from primary sample")
    if len(packet["selected_pair_ids"]) != primary or len(set(packet["selected_pair_ids"])) != primary:
        raise ValidationError("review packet primary sample count mismatch")
    if len(packet["duplicate_pair_ids"]) != duplicate or len(set(packet["duplicate_pair_ids"])) != duplicate:
        raise ValidationError("review packet duplicate sample count mismatch")
    allocations = packet["stratum_allocations"]
    if not isinstance(allocations, list) or sum(row.get("sample_count", -1) for row in allocations if isinstance(row, dict)) != primary:
        raise ValidationError("review packet stratum allocation count mismatch")
    for row in allocations:
        _exact(row, {"snapshot_id", "candidate_class", "rank_band", "frame_count", "sample_count", "inclusion_probability"}, "review stratum allocation")
        frame_count = _integer(row["frame_count"], "stratum frame_count", 1)
        sample_count = _integer(row["sample_count"], "stratum sample_count")
        if sample_count > frame_count or row["inclusion_probability"] != sample_count / frame_count:
            raise ValidationError("review packet stratum inclusion probability mismatch")
    assessment_ids: set[str] = set()
    primary_pairs: set[str] = set()
    duplicate_pairs: set[str] = set()
    for row in packet["assessor_view_records"]:
        if set(row) & REDACTED_FIELDS:
            raise ValidationError("condition-label leakage in assessor view")
        _exact(
            row,
            {
                "assessment_id",
                "pair_id",
                "assessment_kind",
                "query_id",
                "support_target_id",
                "query_text",
                "requirement_texts",
                "candidate_evidence",
                "bounded_context",
            },
            "assessor view record",
        )
        if row["assessment_kind"] not in {"primary", "duplicate"}:
            raise ValidationError("assessor view assessment_kind invalid")
        if row["assessment_id"] in assessment_ids:
            raise ValidationError("duplicate assessor-view assessment_id")
        assessment_ids.add(row["assessment_id"])
        (primary_pairs if row["assessment_kind"] == "primary" else duplicate_pairs).add(row["pair_id"])
    if primary_pairs != set(packet["selected_pair_ids"]) or duplicate_pairs != set(packet["duplicate_pair_ids"]):
        raise ValidationError("assessor view does not exactly project the frozen samples")
    if sha256_json(packet["assessor_view_records"]) != packet["assessor_view_sha256"]:
        raise ValidationError("assessor view hash mismatch")
    if _sha(packet["packet_sha256"], "packet_sha256") != self_hash(packet, "packet_sha256"):
        raise ValidationError("review packet self-hash mismatch")
    return packet


def _cohens_kappa(primary: list[str], duplicate: list[str]) -> tuple[float, float | None, dict[str, int]]:
    if len(primary) != len(duplicate):
        raise ValidationError("agreement vectors must be the same length")
    if not primary:
        return 0.0, None, {}
    exact = sum(left == right for left, right in zip(primary, duplicate)) / len(primary)
    labels = sorted(set(primary) | set(duplicate))
    left_counts = Counter(primary)
    right_counts = Counter(duplicate)
    expected = sum((left_counts[label] / len(primary)) * (right_counts[label] / len(primary)) for label in labels)
    kappa = None if expected == 1.0 else (exact - expected) / (1.0 - expected)
    raw = {f"{left}|{right}": count for (left, right), count in sorted(Counter(zip(primary, duplicate)).items())}
    return exact, kappa, raw


def import_judgments(artifact: dict[str, Any], packet: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    validate_review_packet(packet)
    validate_review_policy(policy)
    if packet["review_policy_sha256"] != policy["policy_sha256"] or packet["packet_id"] != policy["packet_id"]:
        raise ValidationError("review packet/policy identity mismatch")
    fields = {
        "schema_version",
        "artifact_id",
        "packet_id",
        "packet_sha256",
        "candidate_pool_sha256",
        "review_policy_sha256",
        "dataset_id",
        "query_set_id",
        "ordered_snapshot_ids",
        "classification",
        "assessor_classes",
        "assessor_ids",
        "outcome_revealed_at_import",
        "records",
        "artifact_sha256",
    }
    _exact(artifact, fields, "judgment artifact")
    if artifact["schema_version"] != SCHEMA_JUDGMENT:
        raise ValidationError("unsupported judgment artifact schema")
    identity_pairs = {
        "packet_id": packet["packet_id"],
        "packet_sha256": packet["packet_sha256"],
        "candidate_pool_sha256": packet["candidate_pool_sha256"],
        "review_policy_sha256": policy["policy_sha256"],
        "dataset_id": packet["dataset_id"],
        "query_set_id": packet["query_set_id"],
        "ordered_snapshot_ids": packet["ordered_snapshot_ids"],
    }
    for name, expected in identity_pairs.items():
        if artifact[name] != expected:
            raise ValidationError(f"judgment artifact identity mismatch:{name}")
    if artifact["assessor_classes"] != policy["assessor_classes"]:
        raise ValidationError("judgment artifact assessor class mismatch")
    if (
        not isinstance(artifact["assessor_ids"], list)
        or not artifact["assessor_ids"]
        or artifact["assessor_ids"] != list(dict.fromkeys(artifact["assessor_ids"]))
        or set(artifact["assessor_ids"]) - set(policy["allowed_assessor_ids"])
    ):
        raise ValidationError("judgment artifact contains unknown assessor")
    if artifact["classification"] not in {"confirmatory", "exploratory"}:
        raise ValidationError("judgment classification invalid")
    if packet["classification"] == "exploratory" or artifact["outcome_revealed_at_import"]:
        if artifact["classification"] != "exploratory":
            raise ValidationError("post-reveal judgment artifact must be exploratory")
    assessment_rows = {row["assessment_id"]: row for row in packet["assessor_view_records"]}
    record_fields = {
        "assessment_id",
        "pair_id",
        "assessment_kind",
        "assessor_id",
        "label",
        "relevance",
        "answer_bearing_relevance",
        "semantic_answer_quality",
        "attribution_correctness",
        "paraphrase_robustness",
        "adjudicates_assessment_ids",
        "record_sha256",
    }
    seen: set[str] = set()
    seen_assessors: set[str] = set()
    labels_by_pair: dict[str, dict[str, str]] = defaultdict(dict)
    assessment_ids_by_pair: dict[str, dict[str, str]] = defaultdict(dict)
    qrels: dict[str, dict[str, int]] = defaultdict(dict)
    calibrated_families: dict[str, list[float]] = defaultdict(list)
    final_records_by_pair: dict[str, dict[str, Any]] = {}
    adjudications: list[dict[str, Any]] = []
    for row in artifact["records"]:
        _exact(row, record_fields, "judgment record")
        assessment_id = _text(row["assessment_id"], "assessment_id")
        if assessment_id in seen:
            raise ValidationError("duplicate judgment assessment_id")
        seen.add(assessment_id)
        if row["assessment_kind"] == "adjudication":
            if not isinstance(row["adjudicates_assessment_ids"], list) or len(row["adjudicates_assessment_ids"]) != 2:
                raise ValidationError("adjudication must bind exactly two assessment IDs")
            adjudications.append(row)
        else:
            source = assessment_rows.get(assessment_id)
            if source is None:
                raise ValidationError("judgment references an assessment outside the frozen packet")
            if row["pair_id"] != source["pair_id"] or row["assessment_kind"] != source["assessment_kind"]:
                raise ValidationError("judgment assessment identity mismatch")
            if row["adjudicates_assessment_ids"] != []:
                raise ValidationError("non-adjudication record cannot reference adjudication inputs")
        if row["assessor_id"] not in policy["allowed_assessor_ids"]:
            raise ValidationError("unknown judgment assessor")
        seen_assessors.add(row["assessor_id"])
        if row["label"] not in {"relevant", "nonrelevant"} or row["relevance"] not in {0, 1, 2, 3}:
            raise ValidationError("judgment relevance label invalid")
        for name in ("answer_bearing_relevance", "semantic_answer_quality", "attribution_correctness", "paraphrase_robustness"):
            if row[name] is not None:
                _finite_unit(row[name], name)
        if _sha(row["record_sha256"], "record_sha256") != self_hash(row, "record_sha256"):
            raise ValidationError("judgment record self-hash mismatch")
        if row["assessment_kind"] in {"primary", "duplicate"}:
            labels_by_pair[row["pair_id"]][row["assessment_kind"]] = row["label"]
            assessment_ids_by_pair[row["pair_id"]][row["assessment_kind"]] = assessment_id
            source = assessment_rows[assessment_id]
            if row["assessment_kind"] == "primary":
                final_records_by_pair[row["pair_id"]] = row
                qrels[source["query_id"]][source["support_target_id"]] = row["relevance"]
    expected_assessment_ids = set(assessment_rows)
    if not expected_assessment_ids <= seen:
        raise ValidationError("judgment artifact is incomplete for the frozen assessment packet")
    if seen_assessors != set(artifact["assessor_ids"]):
        raise ValidationError("judgment artifact assessor inventory mismatch")
    primary_labels = []
    duplicate_labels = []
    used_adjudication_ids: set[str] = set()
    for pair_id in packet["duplicate_pair_ids"]:
        labels = labels_by_pair.get(pair_id, {})
        if set(labels) != {"primary", "duplicate"}:
            raise ValidationError("duplicate assessment is incomplete")
        primary_labels.append(labels["primary"])
        duplicate_labels.append(labels["duplicate"])
        expected_ids = sorted(assessment_ids_by_pair[pair_id].values())
        matching = [
            row for row in adjudications
            if row["pair_id"] == pair_id and sorted(row["adjudicates_assessment_ids"]) == expected_ids
        ]
        disagreement = labels["primary"] != labels["duplicate"]
        if disagreement and len(matching) != 1:
            raise ValidationError("every duplicate disagreement requires exactly one blind adjudication")
        if not disagreement and matching:
            raise ValidationError("adjudication is permitted only for a duplicate disagreement")
        if matching:
            used_adjudication_ids.add(matching[0]["assessment_id"])
            final_records_by_pair[pair_id] = matching[0]
            primary_id = assessment_ids_by_pair[pair_id]["primary"]
            source = assessment_rows[primary_id]
            qrels[source["query_id"]][source["support_target_id"]] = matching[0]["relevance"]
    if len(adjudications) > packet["maximum_adjudication_count"]:
        raise ValidationError("judgment artifact exceeds the adjudication action maximum")
    if used_adjudication_ids != {row["assessment_id"] for row in adjudications}:
        raise ValidationError("adjudication does not match a frozen duplicate disagreement")
    for row in final_records_by_pair.values():
        for name in ("answer_bearing_relevance", "semantic_answer_quality", "attribution_correctness", "paraphrase_robustness"):
            if row[name] is not None:
                calibrated_families[name].append(float(row[name]))
    exact, kappa, raw = _cohens_kappa(primary_labels, duplicate_labels)
    if _sha(artifact["artifact_sha256"], "artifact_sha256") != self_hash(artifact, "artifact_sha256"):
        raise ValidationError("judgment artifact self-hash mismatch")
    result = {
        "schema_version": SCHEMA_JUDGMENT_IMPORT,
        "artifact_id": artifact["artifact_id"],
        "artifact_sha256": artifact["artifact_sha256"],
        "packet_sha256": packet["packet_sha256"],
        "classification": artifact["classification"],
        "judgment_count": len(artifact["records"]),
        "primary_count": packet["primary_count"],
        "duplicate_count": packet["duplicate_count"],
        "agreement": {
            "raw_label_counts": {
                "primary": dict(sorted(Counter(primary_labels).items())),
                "duplicate": dict(sorted(Counter(duplicate_labels).items())),
            },
            "raw_label_pair_counts": raw,
            "exact_agreement": exact,
            "cohens_kappa": kappa,
            "agreement_pair_count": len(primary_labels),
            "interpretation": "assessor_agreement_not_retrieval_effectiveness",
        },
        "calibrated_family_means": {
            name: (sum(values) / len(values) if values else None)
            for name, values in sorted(calibrated_families.items())
        },
        "judgments_by_query": {qid: dict(sorted(values.items())) for qid, values in sorted(qrels.items())},
        "import_sha256": "0" * 64,
    }
    result["import_sha256"] = self_hash(result, "import_sha256")
    return result
