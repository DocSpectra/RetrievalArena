from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from .errors import ValidationError


PROFILE_AUTOMATIC = "operational_automatic"
PROFILE_CALIBRATION = "scientific_calibration"
SCHEMA_POLICY = "retrieval_arena.evaluation_profile_policy.v1"
SCHEMA_REQUIREMENTS = "retrieval_arena.evidence_requirements.v1"
SCHEMA_BINDINGS = "retrieval_arena.requirement_bindings.v1"
SCHEMA_REQUIREMENT_PREDICTION = "retrieval_arena.requirement_prediction.v1"
SCHEMA_LINEAGE = "retrieval_arena.support_lineage.v1"
SCHEMA_DESTINATION_QRELS = "retrieval_arena.destination_qrel_inventory.v2"
SCHEMA_BERTSCORE_PROVIDER = "retrieval_arena.bertscore_provider.v1"
SCHEMA_BERTSCORE_SCORES = "retrieval_arena.bertscore_scores.v1"
SCHEMA_AUTOMATIC_RESULT = "retrieval_arena.automatic_result.v1"
SCHEMA_CALIBRATION_RESULT = "retrieval_arena.calibration_result.v1"
SCHEMA_AUTOMATIC_REPLAY = "retrieval_arena.automatic_replay.v1"
SCHEMA_CALIBRATION_REPLAY = "retrieval_arena.calibration_replay.v1"

TRANSFER_CLASSES = {
    "identity_unchanged",
    "exact_target_relocation",
    "exact_evidence_unit_preserved",
}
NONTRANSFER_CLASSES = {
    "semantic_rewrite_unresolved",
    "novel_support_unjudged",
    "support_removed",
}
ALL_LINEAGE_CLASSES = TRANSFER_CLASSES | NONTRANSFER_CLASSES


def canonical_json(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"value is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_artifact(path: Path) -> str:
    if path.is_file():
        return sha256_file(path)
    if path.is_dir():
        inventory = [
            {
                "path": item.relative_to(path).as_posix(),
                "size_bytes": item.stat().st_size,
                "sha256": sha256_file(item),
            }
            for item in sorted((item for item in path.rglob("*") if item.is_file()), key=lambda item: item.relative_to(path).as_posix())
        ]
        if not inventory:
            raise ValidationError("registered artifact directory must be non-empty")
        return sha256_json(inventory)
    raise ValidationError("registered artifact path must be a file or directory")


def self_hash(value: dict[str, Any], field: str) -> str:
    return sha256_json({key: item for key, item in value.items() if key != field})


def _exact(value: dict[str, Any], fields: set[str], owner: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"{owner} fields must be exactly {sorted(fields)}")


def _text(value: Any, owner: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{owner} must be a non-empty string")
    return value


def _sha(value: Any, owner: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValidationError(f"{owner} must be a lowercase SHA-256")
    return value


def _optional_sha(value: Any, owner: str) -> str | None:
    if value is None:
        return None
    return _sha(value, owner)


def _integer(value: Any, owner: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{owner} must be an integer >= {minimum}")
    return value


def _finite_unit(value: Any, owner: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{owner} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValidationError(f"{owner} must be finite and in [0, 1]")
    return result


def validate_profile_policy(policy: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "schema_version",
        "policy_id",
        "evaluation_profile",
        "classification",
        "context_item_budget_k",
        "requirements_sha256",
        "bindings_sha256",
        "lineage_policy_id",
        "lineage_sha256",
        "calibration_requirement",
        "standard_metric_family",
        "project_diagnostic_family",
        "automatic_answer_family",
        "calibrated_relevance_family",
        "calibrated_answer_family",
        "calibrated_attribution_family",
        "composite_metric",
        "runtime_network",
        "interactive_human_work",
        "policy_sha256",
    }
    _exact(policy, fields, "evaluation profile policy")
    if policy["schema_version"] != SCHEMA_POLICY:
        raise ValidationError("unsupported evaluation profile policy schema")
    profile = policy["evaluation_profile"]
    if profile not in {PROFILE_AUTOMATIC, PROFILE_CALIBRATION}:
        raise ValidationError("unsupported evaluation_profile")
    if policy["classification"] not in {"confirmatory", "exploratory"}:
        raise ValidationError("classification must be confirmatory or exploratory")
    _integer(policy["context_item_budget_k"], "context_item_budget_k", 1)
    _sha(policy["requirements_sha256"], "requirements_sha256")
    _sha(policy["bindings_sha256"], "bindings_sha256")
    _text(policy["lineage_policy_id"], "lineage_policy_id")
    _sha(policy["lineage_sha256"], "lineage_sha256")
    requirement = policy["calibration_requirement"]
    if requirement not in {"prohibited", "optional", "required"}:
        raise ValidationError("calibration_requirement is unsupported")
    if profile == PROFILE_AUTOMATIC and requirement == "required":
        raise ValidationError("operational_automatic cannot require calibration")
    if profile == PROFILE_CALIBRATION and requirement != "required":
        raise ValidationError("scientific_calibration requires a completed judgment artifact")
    expected = {
        "standard_metric_family": ["evidence_recall", "ndcg", "reciprocal_rank"],
        "project_diagnostic_family": ["complete_support", "requirement_coverage", "all_requirements", "support_lineage"],
        "automatic_answer_family": ["lexical", "bertscore_precision", "bertscore_recall", "bertscore_f1"],
        "calibrated_relevance_family": ["bpref", "answer_bearing_relevance"],
        "calibrated_answer_family": ["semantic_answer_quality", "paraphrase_robustness"],
        "calibrated_attribution_family": ["attribution_correctness"],
        "composite_metric": None,
        "runtime_network": "forbidden",
        "interactive_human_work": "forbidden",
    }
    for name, expected_value in expected.items():
        if policy[name] != expected_value:
            raise ValidationError(f"evaluation profile policy mismatch:{name}")
    if profile == PROFILE_AUTOMATIC and policy["classification"] == "confirmatory" and requirement == "optional":
        pass
    if _sha(policy["policy_sha256"], "policy_sha256") != self_hash(policy, "policy_sha256"):
        raise ValidationError("evaluation profile policy self-hash mismatch")
    return policy


def validate_requirements(artifact: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    fields = {
        "schema_version",
        "dataset_id",
        "query_set_id",
        "ordered_query_ids",
        "requirements",
        "requirements_sha256",
    }
    _exact(artifact, fields, "evidence requirements artifact")
    if artifact["schema_version"] != SCHEMA_REQUIREMENTS:
        raise ValidationError("unsupported evidence requirements schema")
    ordered = artifact["ordered_query_ids"]
    if not isinstance(ordered, list) or not ordered or ordered != list(dict.fromkeys(ordered)):
        raise ValidationError("ordered_query_ids must be non-empty and unique")
    rows = artifact["requirements"]
    if not isinstance(rows, list) or not rows:
        raise ValidationError("requirements must be non-empty")
    by_query: dict[str, list[dict[str, Any]]] = {qid: [] for qid in ordered}
    seen: set[tuple[str, str]] = set()
    fields_row = {
        "query_id",
        "evidence_requirement_id",
        "requirement_text",
        "requirement_provenance_sha256",
    }
    for row in rows:
        _exact(row, fields_row, "evidence requirement")
        qid = _text(row["query_id"], "requirement query_id")
        rid = _text(row["evidence_requirement_id"], "evidence_requirement_id")
        _text(row["requirement_text"], "requirement_text")
        _sha(row["requirement_provenance_sha256"], "requirement_provenance_sha256")
        if qid not in by_query or (qid, rid) in seen:
            raise ValidationError(f"unknown or duplicate evidence requirement:{qid}/{rid}")
        seen.add((qid, rid))
        by_query[qid].append(row)
    if any(not values for values in by_query.values()):
        raise ValidationError("every query requires at least one evidence requirement")
    if rows != sorted(rows, key=lambda row: (ordered.index(row["query_id"]), row["evidence_requirement_id"])):
        raise ValidationError("requirements must follow query and requirement order")
    if _sha(artifact["requirements_sha256"], "requirements_sha256") != self_hash(artifact, "requirements_sha256"):
        raise ValidationError("requirements artifact self-hash mismatch")
    return by_query


def validate_bindings(
    artifact: dict[str, Any],
    requirements_by_query: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], dict[str, list[dict[str, Any]]]]:
    fields = {
        "schema_version",
        "dataset_id",
        "query_set_id",
        "ordered_snapshot_ids",
        "bindings",
        "bindings_sha256",
    }
    _exact(artifact, fields, "requirement bindings artifact")
    if artifact["schema_version"] != SCHEMA_BINDINGS:
        raise ValidationError("unsupported requirement bindings schema")
    snapshots = artifact["ordered_snapshot_ids"]
    if not isinstance(snapshots, list) or not snapshots or snapshots != list(dict.fromkeys(snapshots)):
        raise ValidationError("ordered_snapshot_ids must be non-empty and unique")
    known = {
        (qid, row["evidence_requirement_id"])
        for qid, rows in requirements_by_query.items()
        for row in rows
    }
    result: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {
        pair: {snapshot: [] for snapshot in snapshots} for pair in known
    }
    row_fields = {
        "query_id",
        "evidence_requirement_id",
        "corpus_snapshot_id",
        "support_target_id",
        "evidence_unit_id",
        "normalized_evidence_sha256",
        "bounded_context_sha256",
        "structural_owner",
        "repository_provenance_sha256",
        "document_provenance_sha256",
    }
    seen: set[tuple[str, str, str, str, str]] = set()
    for row in artifact["bindings"]:
        _exact(row, row_fields, "requirement binding")
        qid = _text(row["query_id"], "binding query_id")
        rid = _text(row["evidence_requirement_id"], "binding requirement_id")
        snapshot = _text(row["corpus_snapshot_id"], "binding snapshot")
        target = _text(row["support_target_id"], "binding support_target_id")
        unit = _text(row["evidence_unit_id"], "binding evidence_unit_id")
        for name in (
            "normalized_evidence_sha256",
            "bounded_context_sha256",
            "repository_provenance_sha256",
            "document_provenance_sha256",
        ):
            _sha(row[name], name)
        _text(row["structural_owner"], "binding structural_owner")
        key = (qid, rid, snapshot, target, unit)
        if (qid, rid) not in known or snapshot not in snapshots or key in seen:
            raise ValidationError(f"unknown or duplicate requirement binding:{key}")
        seen.add(key)
        result[(qid, rid)][snapshot].append(row)
    for pair in result:
        for snapshot in snapshots:
            result[pair][snapshot].sort(key=lambda row: (row["support_target_id"], row["evidence_unit_id"]))
    if _sha(artifact["bindings_sha256"], "bindings_sha256") != self_hash(artifact, "bindings_sha256"):
        raise ValidationError("bindings artifact self-hash mismatch")
    return result


def validate_lineage(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    fields = {
        "schema_version",
        "policy_id",
        "source_snapshot_id",
        "destination_snapshot_id",
        "records",
        "lineage_sha256",
    }
    _exact(artifact, fields, "support lineage artifact")
    if artifact["schema_version"] != SCHEMA_LINEAGE:
        raise ValidationError("unsupported support lineage schema")
    policy_id = _text(artifact["policy_id"], "lineage policy_id")
    source_snapshot = _text(artifact["source_snapshot_id"], "source_snapshot_id")
    destination_snapshot = _text(artifact["destination_snapshot_id"], "destination_snapshot_id")
    if source_snapshot == destination_snapshot:
        raise ValidationError("lineage snapshots must differ")
    record_fields = {
        "query_id",
        "evidence_requirement_id",
        "source_snapshot_id",
        "destination_snapshot_id",
        "source_support_target_id",
        "destination_support_target_id",
        "source_evidence_unit_id",
        "destination_evidence_unit_id",
        "source_normalized_evidence_sha256",
        "destination_normalized_evidence_sha256",
        "source_bounded_context_sha256",
        "destination_bounded_context_sha256",
        "source_structural_owner",
        "destination_structural_owner",
        "repository_provenance_sha256",
        "source_document_provenance_sha256",
        "destination_document_provenance_sha256",
        "repository_document_lineage_exact",
        "transfer_class",
        "mapping_shape",
        "decomposed_exact_edge",
        "ambiguous",
        "contradictory_destination",
        "conflicting",
        "record_sha256",
    }
    seen: set[tuple[str, str, str, str, str, str]] = set()
    graph: dict[str, set[str]] = {}
    admitted: list[dict[str, Any]] = []
    counts = {name: 0 for name in sorted(ALL_LINEAGE_CLASSES)}
    for row in artifact["records"]:
        _exact(row, record_fields, "support lineage record")
        if row["source_snapshot_id"] != source_snapshot or row["destination_snapshot_id"] != destination_snapshot:
            raise ValidationError("support lineage record snapshot mismatch")
        transfer_class = row["transfer_class"]
        if transfer_class not in ALL_LINEAGE_CLASSES:
            raise ValidationError(f"unsupported transfer class:{transfer_class}")
        if row["mapping_shape"] not in {"one_to_one", "many_to_many_decomposed"}:
            raise ValidationError("unsupported lineage mapping_shape")
        for name in (
            "query_id",
            "evidence_requirement_id",
            "source_support_target_id",
            "destination_support_target_id",
            "source_evidence_unit_id",
            "destination_evidence_unit_id",
            "source_structural_owner",
            "destination_structural_owner",
        ):
            _text(row[name], name)
        for name in (
            "source_normalized_evidence_sha256",
            "destination_normalized_evidence_sha256",
            "source_bounded_context_sha256",
            "destination_bounded_context_sha256",
            "repository_provenance_sha256",
            "source_document_provenance_sha256",
            "destination_document_provenance_sha256",
        ):
            _sha(row[name], name)
        for name in (
            "repository_document_lineage_exact",
            "decomposed_exact_edge",
            "ambiguous",
            "contradictory_destination",
            "conflicting",
        ):
            if not isinstance(row[name], bool):
                raise ValidationError(f"{name} must be boolean")
        if _sha(row["record_sha256"], "record_sha256") != self_hash(row, "record_sha256"):
            raise ValidationError("support lineage record self-hash mismatch")
        edge_key = (
            row["query_id"],
            row["evidence_requirement_id"],
            row["source_support_target_id"],
            row["destination_support_target_id"],
            row["source_evidence_unit_id"],
            row["destination_evidence_unit_id"],
        )
        if edge_key in seen:
            raise ValidationError("duplicate lineage edge")
        seen.add(edge_key)
        exact_identity = (
            row["source_normalized_evidence_sha256"] == row["destination_normalized_evidence_sha256"]
            and row["source_bounded_context_sha256"] == row["destination_bounded_context_sha256"]
            and row["source_structural_owner"] == row["destination_structural_owner"]
        )
        hazards = row["ambiguous"] or row["contradictory_destination"] or row["conflicting"]
        if transfer_class in TRANSFER_CLASSES:
            if not exact_identity or hazards or not row["repository_document_lineage_exact"]:
                raise ValidationError(f"automatic transfer predicate failed:{transfer_class}")
            if transfer_class == "identity_unchanged" and row["source_support_target_id"] != row["destination_support_target_id"]:
                raise ValidationError("identity_unchanged requires the same target")
            if transfer_class == "identity_unchanged" and row["source_document_provenance_sha256"] != row["destination_document_provenance_sha256"]:
                raise ValidationError("identity_unchanged requires exact document provenance")
            if row["mapping_shape"] == "many_to_many_decomposed" and not row["decomposed_exact_edge"]:
                raise ValidationError("many-to-many lineage requires decomposed exact edges")
            if row["source_support_target_id"] != row["destination_support_target_id"]:
                graph.setdefault(row["source_support_target_id"], set()).add(row["destination_support_target_id"])
            admitted.append(row)
        else:
            if row["repository_document_lineage_exact"] and transfer_class in {"semantic_rewrite_unresolved", "novel_support_unjudged"}:
                # Provenance may exist, but it may not promote these classes.
                pass
        counts[transfer_class] += 1
    outgoing: dict[tuple[str, str, str], set[str]] = {}
    incoming: dict[tuple[str, str, str], set[str]] = {}
    for row in admitted:
        source_key = (row["query_id"], row["evidence_requirement_id"], row["source_support_target_id"])
        destination_key = (row["query_id"], row["evidence_requirement_id"], row["destination_support_target_id"])
        outgoing.setdefault(source_key, set()).add(row["destination_support_target_id"])
        incoming.setdefault(destination_key, set()).add(row["source_support_target_id"])
    for row in admitted:
        source_key = (row["query_id"], row["evidence_requirement_id"], row["source_support_target_id"])
        destination_key = (row["query_id"], row["evidence_requirement_id"], row["destination_support_target_id"])
        if (len(outgoing[source_key]) > 1 or len(incoming[destination_key]) > 1) and row["mapping_shape"] != "many_to_many_decomposed":
            raise ValidationError("unsupported many-to-many lineage without exact decomposition")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValidationError("cyclic lineage graph")
        if node in visited:
            return
        visiting.add(node)
        for target in graph.get(node, set()):
            visit(target)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)
    if _sha(artifact["lineage_sha256"], "lineage_sha256") != self_hash(artifact, "lineage_sha256"):
        raise ValidationError("support lineage artifact self-hash mismatch")
    return admitted, counts


def transfer_qrels(
    source_qrels: list[dict[str, Any]], lineage_artifact: dict[str, Any]
) -> list[dict[str, Any]]:
    """Transfer only positive qrels over admitted exact evidence-unit edges."""
    admitted, _ = validate_lineage(lineage_artifact)
    qrel_fields = {"query_id", "support_target_id", "relevance", "judgment_id", "judgment_provenance_sha256"}
    source: dict[tuple[str, str], int] = {}
    for row in source_qrels:
        _exact(row, qrel_fields, "source qrel")
        qid = _text(row["query_id"], "source qrel query_id")
        target = _text(row["support_target_id"], "source qrel support_target_id")
        relevance = row["relevance"]
        if isinstance(relevance, bool) or not isinstance(relevance, int) or relevance not in range(4):
            raise ValidationError("source qrel relevance must be integer 0..3")
        _text(row["judgment_id"], "source qrel judgment_id")
        _sha(row["judgment_provenance_sha256"], "source qrel judgment_provenance_sha256")
        if (qid, target) in source:
            raise ValidationError("duplicate source qrel pair")
        source[(qid, target)] = relevance
    transferred: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in admitted:
        source_key = (edge["query_id"], edge["source_support_target_id"])
        relevance = source.get(source_key)
        if relevance is None or relevance <= 0:
            continue
        destination_key = (edge["query_id"], edge["destination_support_target_id"])
        existing = transferred.get(destination_key)
        if existing is not None and existing["relevance"] != relevance:
            raise ValidationError("conflicting exact-lineage qrel transfer")
        if existing is None:
            transferred[destination_key] = {
                "query_id": edge["query_id"],
                "support_target_id": edge["destination_support_target_id"],
                "relevance": relevance,
                "judgment_id": f"exact-lineage:{edge['record_sha256']}",
                "judgment_provenance_sha256": edge["record_sha256"],
            }
    return [transferred[key] for key in sorted(transferred)]


def compose_destination_qrels(
    source_qrels: list[dict[str, Any]],
    lineage_artifact: dict[str, Any],
    source_snapshot_id: str,
    destination_snapshot_id: str,
    ordered_query_ids: list[str],
    relevance_scale: str,
    *,
    source_qrels_scope: str,
    ranked_predictions: list[dict[str, Any]],
    cutoff: int,
    ranked_dataset_sha256: str,
    ranked_policy_sha256: str,
) -> dict[str, Any]:
    """Compose the destination inventory from admitted positive transfers only.

    Source qrels are never copied directly. A source target appears at the
    destination only when an admitted exact edge names it as the destination;
    source negatives and non-transfer lineage classes remain unjudged.
    """
    _text(source_snapshot_id, "automatic qrels source snapshot")
    _text(destination_snapshot_id, "automatic qrels destination snapshot")
    validate_lineage(lineage_artifact)
    if lineage_artifact["source_snapshot_id"] != source_snapshot_id:
        raise ValidationError("automatic qrels source snapshot does not match support lineage")
    if lineage_artifact["destination_snapshot_id"] != destination_snapshot_id:
        raise ValidationError("automatic qrels destination snapshot does not match support lineage")
    if (
        not isinstance(ordered_query_ids, list)
        or not ordered_query_ids
        or ordered_query_ids != list(dict.fromkeys(ordered_query_ids))
    ):
        raise ValidationError("destination qrel composition requires ordered unique query IDs")
    if relevance_scale not in {"binary", "integer_0_3"}:
        raise ValidationError("destination qrel composition relevance scale is unsupported")
    if source_qrels_scope not in {"complete", "pooled"}:
        raise ValidationError("source qrels scope must be complete or pooled")
    if isinstance(cutoff, bool) or not isinstance(cutoff, int) or cutoff < 1:
        raise ValidationError("destination qrel composition cutoff is invalid")
    _sha(ranked_dataset_sha256, "ranked_dataset_sha256")
    _sha(ranked_policy_sha256, "ranked_policy_sha256")
    admitted_queries = set(ordered_query_ids)
    for row in source_qrels:
        if row.get("query_id") not in admitted_queries:
            raise ValidationError("source qrel query is outside the destination query inventory")
        if relevance_scale == "binary" and row.get("relevance") not in {0, 1}:
            raise ValidationError("binary source qrels require relevance 0 or 1")
    qrels = transfer_qrels(source_qrels, lineage_artifact)
    lineage_by_source: dict[tuple[str, str], list[dict[str, Any]]] = {}
    lineage_by_destination: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in lineage_artifact["records"]:
        lineage_by_source.setdefault(
            (row["query_id"], row["source_support_target_id"]), []
        ).append(row)
        lineage_by_destination.setdefault(
            (row["query_id"], row["destination_support_target_id"]), []
        ).append(row)

    def lineage_references(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            [
                {
                    "destination_support_target_id": row["destination_support_target_id"],
                    "record_sha256": row["record_sha256"],
                    "transfer_class": row["transfer_class"],
                }
                for row in rows
            ],
            key=lambda row: (
                row["destination_support_target_id"],
                row["transfer_class"],
                row["record_sha256"],
            ),
        )

    source_dispositions: list[dict[str, Any]] = []
    for row in sorted(source_qrels, key=lambda item: (item["query_id"], item["support_target_id"])):
        key = (row["query_id"], row["support_target_id"])
        lineage_rows = lineage_by_source.get(key, [])
        exact_rows = [item for item in lineage_rows if item["transfer_class"] in TRANSFER_CLASSES]
        if row["relevance"] <= 0:
            disposition = "not_transferred"
            reason = "source_nonpositive_judgment"
            destinations: list[str] = []
        elif exact_rows:
            disposition = "transferred"
            reason = "exact_positive_transfer"
            destinations = sorted({item["destination_support_target_id"] for item in exact_rows})
        else:
            disposition = "not_transferred"
            classes = {item["transfer_class"] for item in lineage_rows}
            if classes == {"semantic_rewrite_unresolved"}:
                reason = "semantic_rewrite_unresolved"
            elif classes == {"support_removed"}:
                reason = "support_removed"
            elif classes == {"novel_support_unjudged"}:
                reason = "novel_support_unjudged"
            else:
                reason = "no_admitted_exact_lineage_edge"
            destinations = []
        disposition_row = {
            "query_id": row["query_id"],
            "source_support_target_id": row["support_target_id"],
            "source_judgment_id": row["judgment_id"],
            "source_judgment_provenance_sha256": row["judgment_provenance_sha256"],
            "source_relevance": row["relevance"],
            "disposition": disposition,
            "reason": reason,
            "lineage_records": lineage_references(lineage_rows),
            "destination_support_target_ids": destinations,
            "disposition_sha256": "0" * 64,
        }
        disposition_row["disposition_sha256"] = self_hash(
            disposition_row, "disposition_sha256"
        )
        source_dispositions.append(disposition_row)

    qrels_by_destination = {
        (row["query_id"], row["support_target_id"]): row for row in qrels
    }
    disposition_by_source = {
        (row["query_id"], row["source_support_target_id"]): row
        for row in source_dispositions
    }
    predictions_by_query: dict[str, dict[str, Any]] = {}
    for prediction in ranked_predictions:
        qid = prediction.get("query_id")
        if qid not in admitted_queries or qid in predictions_by_query:
            raise ValidationError("destination qrel audit prediction query invalid")
        predictions_by_query[qid] = prediction
    if list(predictions_by_query) != ordered_query_ids:
        raise ValidationError("destination qrel audit predictions must follow ordered query IDs")

    top_k_judgments: list[dict[str, Any]] = []
    for qid in ordered_query_ids:
        items = predictions_by_query[qid].get("items")
        if not isinstance(items, list):
            raise ValidationError("destination qrel audit prediction items invalid")
        for expected_rank, item in enumerate(items[:cutoff], 1):
            if not isinstance(item, dict) or item.get("rank") != expected_rank:
                raise ValidationError("destination qrel audit prediction rank invalid")
            target = _text(item.get("support_target_id"), "destination top-k target")
            key = (qid, target)
            judgment = qrels_by_destination.get(key)
            lineage_rows = lineage_by_destination.get(key, [])
            source_disposition_hashes = sorted(
                {
                    disposition_by_source[(qid, lineage["source_support_target_id"])][
                        "disposition_sha256"
                    ]
                    for lineage in lineage_rows
                    if (qid, lineage["source_support_target_id"]) in disposition_by_source
                }
            )
            if judgment is not None:
                status = "judged"
                reason = "exact_positive_transfer"
                relevance: int | None = judgment["relevance"]
                judgment_id: str | None = judgment["judgment_id"]
                provenance: str | None = judgment["judgment_provenance_sha256"]
            else:
                status = "unjudged"
                relevance = None
                judgment_id = None
                provenance = None
                classes = {lineage["transfer_class"] for lineage in lineage_rows}
                if "semantic_rewrite_unresolved" in classes:
                    reason = "semantic_rewrite_unresolved"
                elif "novel_support_unjudged" in classes:
                    reason = "novel_support_unjudged"
                elif "support_removed" in classes:
                    reason = "support_removed"
                elif classes.intersection(TRANSFER_CLASSES) and any(
                    disposition_by_source[(qid, lineage["source_support_target_id"])][
                        "reason"
                    ]
                    == "source_nonpositive_judgment"
                    for lineage in lineage_rows
                    if (qid, lineage["source_support_target_id"]) in disposition_by_source
                ):
                    reason = "source_nonpositive_judgment"
                else:
                    reason = "no_admitted_exact_lineage_edge"
            top_k_row = {
                "query_id": qid,
                "destination_support_target_id": target,
                "rank": expected_rank,
                "judgment_status": status,
                "reason": reason,
                "relevance": relevance,
                "judgment_id": judgment_id,
                "judgment_provenance_sha256": provenance,
                "lineage_records": lineage_references(lineage_rows),
                "source_qrel_disposition_sha256s": source_disposition_hashes,
                "pair_sha256": "0" * 64,
            }
            top_k_row["pair_sha256"] = self_hash(top_k_row, "pair_sha256")
            top_k_judgments.append(top_k_row)

    judged_count = sum(row["judgment_status"] == "judged" for row in top_k_judgments)
    unjudged_count = len(top_k_judgments) - judged_count
    effective_qrels_scope = (
        "pooled" if source_qrels_scope == "pooled" or unjudged_count else "complete"
    )
    effective_scoring_view = {
        "source_ranked_dataset_sha256": ranked_dataset_sha256,
        "source_ranked_policy_sha256": ranked_policy_sha256,
        "source_qrels_scope": source_qrels_scope,
        "effective_qrels_scope": effective_qrels_scope,
        "context_item_budget_k": cutoff,
        "derivation_reason": (
            "destination_top_k_contains_unjudged_targets"
            if unjudged_count
            else "destination_top_k_fully_judged"
        ),
        "view_sha256": "0" * 64,
    }
    effective_scoring_view["view_sha256"] = self_hash(
        effective_scoring_view, "view_sha256"
    )

    source_pairs = {
        (row["query_id"], row["support_target_id"]) for row in source_qrels
    }
    disposition_pairs = {
        (row["query_id"], row["source_support_target_id"])
        for row in source_dispositions
    }
    if source_pairs != disposition_pairs or len(source_pairs) != len(source_dispositions):
        raise ValidationError("source qrel disposition conservation failed")
    transferred_rows = [
        (row["query_id"], destination)
        for row in source_dispositions
        if row["disposition"] == "transferred"
        for destination in row["destination_support_target_ids"]
    ]
    transferred_pairs = set(transferred_rows)
    destination_qrel_pairs = {
        (row["query_id"], row["support_target_id"]) for row in qrels
    }
    if len(transferred_rows) != len(transferred_pairs):
        raise ValidationError("destination qrel has multiple source dispositions")
    if transferred_pairs != destination_qrel_pairs:
        raise ValidationError("transferred source disposition conservation failed")
    if any(
        row["destination_support_target_ids"]
        for row in source_dispositions
        if row["disposition"] != "transferred"
    ):
        raise ValidationError("non-transfer source disposition created a destination qrel")
    expected_top_k_pairs = [
        (qid, item["support_target_id"], rank)
        for qid in ordered_query_ids
        for rank, item in enumerate(predictions_by_query[qid]["items"][:cutoff], 1)
    ]
    observed_top_k_pairs = [
        (row["query_id"], row["destination_support_target_id"], row["rank"])
        for row in top_k_judgments
    ]
    if observed_top_k_pairs != expected_top_k_pairs or len(set(observed_top_k_pairs)) != len(
        observed_top_k_pairs
    ):
        raise ValidationError("destination top-k judgment conservation failed")
    judged_pairs = {
        (row["query_id"], row["destination_support_target_id"])
        for row in top_k_judgments
        if row["judgment_status"] == "judged"
    }
    expected_judged_pairs = {
        (qid, target)
        for qid, target, _ in expected_top_k_pairs
        if (qid, target) in destination_qrel_pairs
    }
    if judged_pairs != expected_judged_pairs:
        raise ValidationError("destination top-k judged partition failed")
    if judged_count + unjudged_count != len(top_k_judgments):
        raise ValidationError("destination top-k judgment count conservation failed")
    inventory = {
        "schema_version": SCHEMA_DESTINATION_QRELS,
        "source_snapshot_id": source_snapshot_id,
        "destination_snapshot_id": destination_snapshot_id,
        "lineage_policy_id": lineage_artifact["policy_id"],
        "lineage_sha256": lineage_artifact["lineage_sha256"],
        "source_qrels_sha256": sha256_json(source_qrels),
        "composition_policy": "positive_source_judgments_over_admitted_exact_edges_only",
        "negative_transfer_policy": "forbidden_source_negatives_remain_unjudged",
        "ordered_query_ids_sha256": sha256_json(ordered_query_ids),
        "relevance_scale": relevance_scale,
        "source_qrels_scope": source_qrels_scope,
        "effective_qrels_scope": effective_qrels_scope,
        "qrels": qrels,
        "destination_qrels_sha256": sha256_json(qrels),
        "source_qrel_dispositions": source_dispositions,
        "source_qrel_dispositions_sha256": sha256_json(source_dispositions),
        "destination_top_k_judgments": top_k_judgments,
        "destination_top_k_judgments_sha256": sha256_json(top_k_judgments),
        "judged_top_k_count": judged_count,
        "unjudged_top_k_count": unjudged_count,
        "effective_scoring_view": effective_scoring_view,
        "inventory_sha256": "0" * 64,
    }
    inventory["inventory_sha256"] = self_hash(inventory, "inventory_sha256")
    return inventory


def validate_destination_qrel_inventory(
    artifact: dict[str, Any],
    source_qrels: list[dict[str, Any]],
    lineage_artifact: dict[str, Any],
    source_snapshot_id: str,
    destination_snapshot_id: str,
    ordered_query_ids: list[str],
    relevance_scale: str,
    *,
    source_qrels_scope: str,
    ranked_predictions: list[dict[str, Any]],
    cutoff: int,
    ranked_dataset_sha256: str,
    ranked_policy_sha256: str,
) -> dict[str, Any]:
    """Recompose and require exact closed-world destination-qrel audit evidence."""
    expected = compose_destination_qrels(
        source_qrels,
        lineage_artifact,
        source_snapshot_id,
        destination_snapshot_id,
        ordered_query_ids,
        relevance_scale,
        source_qrels_scope=source_qrels_scope,
        ranked_predictions=ranked_predictions,
        cutoff=cutoff,
        ranked_dataset_sha256=ranked_dataset_sha256,
        ranked_policy_sha256=ranked_policy_sha256,
    )
    if artifact != expected:
        raise ValidationError("destination qrel inventory exact recomposition mismatch")
    return artifact


def score_requirements(
    requirements_by_query: dict[str, list[dict[str, Any]]],
    bindings: dict[tuple[str, str], dict[str, list[dict[str, Any]]]],
    predictions: list[dict[str, Any]],
    snapshot_id: str,
    cutoff: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    by_query = {row["query_id"]: row for row in predictions}
    if set(by_query) != set(requirements_by_query):
        raise ValidationError("requirement predictions must exactly cover ordered queries")
    rows: list[dict[str, Any]] = []
    for qid, requirements in requirements_by_query.items():
        prediction = by_query[qid]
        _exact(
            prediction,
            {"schema_version", "query_id", "corpus_snapshot_id", "context_item_budget_k", "ranked_support_target_ids"},
            "requirement prediction",
        )
        if prediction["schema_version"] != SCHEMA_REQUIREMENT_PREDICTION:
            raise ValidationError("unsupported requirement prediction schema")
        if prediction["corpus_snapshot_id"] != snapshot_id or prediction["context_item_budget_k"] != cutoff:
            raise ValidationError(f"requirement prediction snapshot/cutoff mismatch:{qid}")
        targets = prediction.get("ranked_support_target_ids")
        if not isinstance(targets, list) or not all(isinstance(item, str) and item for item in targets):
            raise ValidationError(f"ranked_support_target_ids invalid:{qid}")
        if len(targets) != len(set(targets)):
            raise ValidationError(f"duplicate requirement prediction target:{qid}")
        top = set(targets[:cutoff])
        covered: list[str] = []
        uncovered: list[str] = []
        evidence_units: dict[str, list[str]] = {}
        for requirement in requirements:
            rid = requirement["evidence_requirement_id"]
            matches = [
                binding
                for binding in bindings[(qid, rid)].get(snapshot_id, [])
                if binding["support_target_id"] in top
            ]
            evidence_units[rid] = sorted(binding["evidence_unit_id"] for binding in matches)
            (covered if matches else uncovered).append(rid)
        coverage = len(covered) / len(requirements)
        rows.append(
            {
                "query_id": qid,
                "corpus_snapshot_id": snapshot_id,
                "cutoff": cutoff,
                "metric_classification": "project_specific_diagnostic",
                "requirement_coverage": coverage,
                "all_requirements": 1.0 if not uncovered else 0.0,
                "covered_requirement_ids": covered,
                "uncovered_requirement_ids": uncovered,
                "matched_evidence_unit_ids_by_requirement": evidence_units,
            }
        )
    aggregate = {
        "macro_requirement_coverage": sum(row["requirement_coverage"] for row in rows) / len(rows),
        "macro_all_requirements": sum(row["all_requirements"] for row in rows) / len(rows),
    }
    return rows, aggregate


def validate_bertscore_provider(registration: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    fields = {
        "schema_version",
        "provider_id",
        "package_name",
        "package_version",
        "package_artifact_path",
        "package_artifact_sha256",
        "checkpoint_id",
        "checkpoint_revision",
        "model_artifact_path",
        "model_artifact_sha256",
        "tokenizer_id",
        "tokenizer_revision",
        "tokenizer_artifact_path",
        "tokenizer_artifact_sha256",
        "baseline_policy",
        "baseline_artifact_path",
        "baseline_artifact_sha256",
        "rescaling_policy",
        "language",
        "device",
        "dtype",
        "runtime_download",
        "provider_sha256",
    }
    _exact(registration, fields, "BERTScore provider registration")
    if registration["schema_version"] != SCHEMA_BERTSCORE_PROVIDER:
        raise ValidationError("unsupported BERTScore provider schema")
    if registration["package_name"] != "bert-score" or registration["runtime_download"] is not False:
        raise ValidationError("BERTScore provider forbids package substitution and runtime download")
    for name in (
        "provider_id",
        "package_version",
        "checkpoint_id",
        "checkpoint_revision",
        "tokenizer_id",
        "tokenizer_revision",
        "language",
        "device",
        "dtype",
    ):
        _text(registration[name], name)
    if registration["device"] not in {"cpu", "cuda"} or registration["dtype"] != "float32":
        raise ValidationError("unsupported BERTScore device/dtype policy")
    if registration["baseline_policy"] not in {"none", "registered_artifact"}:
        raise ValidationError("unsupported BERTScore baseline policy")
    if registration["rescaling_policy"] not in {"none", "rescale_with_registered_baseline"}:
        raise ValidationError("unsupported BERTScore rescaling policy")
    def admit_file(path_value: Any, expected_hash: Any, owner: str, optional: bool = False) -> Path | None:
        if path_value is None and expected_hash is None and optional:
            return None
        path = Path(_text(path_value, f"{owner}_path"))
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        if not path.exists() or not (path.is_file() or path.is_dir()):
            raise ValidationError(f"BERTScore registered artifact missing:{owner}")
        if sha256_artifact(path) != _sha(expected_hash, f"{owner}_sha256"):
            raise ValidationError(f"BERTScore registered artifact hash mismatch:{owner}")
        return path

    admit_file(registration["package_artifact_path"], registration["package_artifact_sha256"], "package")
    admit_file(registration["model_artifact_path"], registration["model_artifact_sha256"], "model")
    admit_file(registration["tokenizer_artifact_path"], registration["tokenizer_artifact_sha256"], "tokenizer")
    baseline = admit_file(
        registration["baseline_artifact_path"],
        registration["baseline_artifact_sha256"],
        "baseline",
        optional=True,
    )
    if registration["baseline_policy"] == "registered_artifact" and baseline is None:
        raise ValidationError("registered BERTScore baseline artifact is required")
    if registration["baseline_policy"] == "none" and baseline is not None:
        raise ValidationError("BERTScore baseline artifact forbidden by policy")
    if _sha(registration["provider_sha256"], "provider_sha256") != self_hash(registration, "provider_sha256"):
        raise ValidationError("BERTScore provider self-hash mismatch")
    return registration


def validate_bertscore_scores(
    artifact: dict[str, Any],
    provider: dict[str, Any],
    answer_pairs_sha256: str,
) -> dict[str, dict[str, float]]:
    fields = {
        "schema_version",
        "provider_id",
        "provider_sha256",
        "answer_pairs_sha256",
        "records",
        "scores_sha256",
    }
    _exact(artifact, fields, "BERTScore scores artifact")
    if artifact["schema_version"] != SCHEMA_BERTSCORE_SCORES:
        raise ValidationError("unsupported BERTScore scores schema")
    if artifact["provider_id"] != provider["provider_id"] or artifact["provider_sha256"] != provider["provider_sha256"]:
        raise ValidationError("unregistered BERTScore provider substitution")
    if artifact["answer_pairs_sha256"] != answer_pairs_sha256:
        raise ValidationError("BERTScore answer-pair identity mismatch")
    result: dict[str, dict[str, float]] = {}
    for row in artifact["records"]:
        _exact(row, {"query_id", "precision", "recall", "f1"}, "BERTScore record")
        qid = _text(row["query_id"], "BERTScore query_id")
        if qid in result:
            raise ValidationError(f"duplicate BERTScore record:{qid}")
        result[qid] = {
            "precision": _finite_unit(row["precision"], "BERTScore precision"),
            "recall": _finite_unit(row["recall"], "BERTScore recall"),
            "f1": _finite_unit(row["f1"], "BERTScore f1"),
        }
    if _sha(artifact["scores_sha256"], "scores_sha256") != self_hash(artifact, "scores_sha256"):
        raise ValidationError("BERTScore scores self-hash mismatch")
    return result


def run_bertscore_offline(
    registration: dict[str, Any],
    base_dir: Path,
    answer_pairs: list[dict[str, str]],
) -> dict[str, Any]:
    """Run only the exact registered local BERTScore distribution tree."""
    validate_bertscore_provider(registration, base_dir)
    seen_queries: set[str] = set()
    for row in answer_pairs:
        _exact(row, {"query_id", "generated_answer", "reference_answer"}, "BERTScore answer pair")
        qid = _text(row["query_id"], "BERTScore answer-pair query_id")
        _text(row["generated_answer"], "BERTScore generated answer")
        _text(row["reference_answer"], "BERTScore reference answer")
        if qid in seen_queries:
            raise ValidationError(f"duplicate BERTScore answer pair:{qid}")
        seen_queries.add(qid)
    package_root = Path(registration["package_artifact_path"])
    if not package_root.is_absolute():
        package_root = (base_dir / package_root).resolve()
    if not package_root.is_dir():
        raise ValidationError("BERTScore executable package artifact must be a registered distribution directory")
    package_dir = package_root / "bert_score"
    metadata_dirs = sorted(package_root.glob("*.dist-info"))
    if not (package_dir / "__init__.py").is_file() or len(metadata_dirs) != 1:
        raise ValidationError("BERTScore registered distribution tree is incomplete or ambiguous")
    metadata_path = metadata_dirs[0] / "METADATA"
    if not metadata_path.is_file():
        raise ValidationError("BERTScore registered distribution metadata missing")
    metadata_fields: dict[str, str] = {}
    for line in metadata_path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            if name in {"Name", "Version"} and name not in metadata_fields:
                metadata_fields[name] = value.strip()
    if metadata_fields != {"Name": registration["package_name"], "Version": registration["package_version"]}:
        raise ValidationError("BERTScore registered distribution name/version mismatch")
    model_path = Path(registration["model_artifact_path"])
    if not model_path.is_absolute():
        model_path = (base_dir / model_path).resolve()
    tokenizer_path = Path(registration["tokenizer_artifact_path"])
    if not tokenizer_path.is_absolute():
        tokenizer_path = (base_dir / tokenizer_path).resolve()
    if tokenizer_path != model_path:
        raise ValidationError("BERTScore runtime requires the pinned tokenizer to be co-located with the local checkpoint")
    baseline_path = registration["baseline_artifact_path"]
    if baseline_path is not None:
        baseline_path = str((base_dir / baseline_path).resolve()) if not Path(baseline_path).is_absolute() else baseline_path
    previous = {name: os.environ.get(name) for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    previous_modules = {
        name: module
        for name, module in list(sys.modules.items())
        if name == "bert_score" or name.startswith("bert_score.")
    }
    for name in previous_modules:
        del sys.modules[name]
    previous_dont_write_bytecode = sys.dont_write_bytecode
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(package_root))
    try:
        importlib.invalidate_caches()
        try:
            provider_module = importlib.import_module("bert_score")
        except ImportError as exc:
            raise ValidationError("BERTScore registered provider import unavailable") from exc
        loaded_provider_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "bert_score" or name.startswith("bert_score.")
        }
        if not loaded_provider_modules:
            raise ValidationError("BERTScore registered provider import unavailable")
        for name, module in loaded_provider_modules.items():
            module_file = getattr(module, "__file__", None)
            if not isinstance(module_file, str) or not Path(module_file).resolve().is_relative_to(package_root):
                raise ValidationError(f"ambient BERTScore provider substitution:{name}")
        score_callable = getattr(provider_module, "score", None)
        if not callable(score_callable):
            raise ValidationError("BERTScore registered provider has no score callable")
        precision, recall, f1 = score_callable(
            [row["generated_answer"] for row in answer_pairs],
            [row["reference_answer"] for row in answer_pairs],
            model_type=str(model_path),
            lang=registration["language"],
            device=registration["device"],
            rescale_with_baseline=registration["rescaling_policy"] == "rescale_with_registered_baseline",
            baseline_path=baseline_path,
            verbose=False,
        )
        for name, module in list(sys.modules.items()):
            if name == "bert_score" or name.startswith("bert_score."):
                module_file = getattr(module, "__file__", None)
                if not isinstance(module_file, str) or not Path(module_file).resolve().is_relative_to(package_root):
                    raise ValidationError(f"ambient BERTScore provider substitution:{name}")
    finally:
        if sys.path and sys.path[0] == str(package_root):
            sys.path.pop(0)
        else:
            try:
                sys.path.remove(str(package_root))
            except ValueError:
                pass
        for name in list(sys.modules):
            if name == "bert_score" or name.startswith("bert_score."):
                del sys.modules[name]
        sys.modules.update(previous_modules)
        sys.dont_write_bytecode = previous_dont_write_bytecode
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    try:
        output_lengths = (len(precision), len(recall), len(f1))
    except TypeError as exc:
        raise ValidationError("BERTScore provider outputs must be sized sequences") from exc
    if output_lengths != (len(answer_pairs), len(answer_pairs), len(answer_pairs)):
        raise ValidationError("BERTScore provider output count must exactly match answer pairs")
    records = []
    for index, row in enumerate(answer_pairs):
        records.append(
            {
                "query_id": row["query_id"],
                "precision": float(precision[index]),
                "recall": float(recall[index]),
                "f1": float(f1[index]),
            }
        )
    result = {
        "schema_version": SCHEMA_BERTSCORE_SCORES,
        "provider_id": registration["provider_id"],
        "provider_sha256": registration["provider_sha256"],
        "answer_pairs_sha256": sha256_json(answer_pairs),
        "records": records,
        "scores_sha256": "0" * 64,
    }
    result["scores_sha256"] = self_hash(result, "scores_sha256")
    validate_bertscore_provider(registration, base_dir)
    validate_bertscore_scores(result, registration, sha256_json(answer_pairs))
    return result


def score_bpref(
    ordered_query_ids: list[str],
    rankings: dict[str, list[str]],
    judgments: dict[str, dict[str, int]],
    provider_name: str,
    provider_version: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if provider_name != "pytrec_eval" or importlib.metadata.version("pytrec-eval-terrier") != provider_version:
        raise ValidationError("BPref provider identity mismatch")
    try:
        import ir_measures
    except ImportError as exc:
        raise ValidationError("BPref engine unavailable") from exc
    if getattr(ir_measures, "__version__", None) != "0.4.3":
        raise ValidationError("BPref engine version mismatch")
    rows: list[dict[str, Any]] = []
    available_qrels = []
    available_run = []
    available_queries: list[str] = []
    for qid in ordered_query_ids:
        values = judgments.get(qid, {})
        positive_count = sum(value > 0 for value in values.values())
        negative_count = sum(value == 0 for value in values.values())
        if positive_count == 0 or negative_count == 0:
            reason = "zero_positive_judgments" if positive_count == 0 else "zero_negative_judgments"
            rows.append(
                {
                    "query_id": qid,
                    "bpref": None,
                    "availability": "unavailable",
                    "unavailable_reason": reason,
                    "judged_positive_count": positive_count,
                    "judged_negative_count": negative_count,
                    "unjudged_count": sum(target not in values for target in rankings[qid]),
                }
            )
            continue
        available_queries.append(qid)
        available_qrels.extend(ir_measures.Qrel(qid, target, relevance) for target, relevance in values.items())
        available_run.extend(
            ir_measures.ScoredDoc(qid, target, float(len(rankings[qid]) - rank))
            for rank, target in enumerate(rankings[qid])
        )
    calculated: dict[str, float] = {}
    if available_queries:
        for metric in ir_measures.iter_calc([ir_measures.parse_measure("BPref")], available_qrels, available_run):
            calculated[str(metric.query_id)] = float(metric.value)
    by_query = {row["query_id"]: row for row in rows}
    for qid in available_queries:
        values = judgments[qid]
        by_query[qid] = {
            "query_id": qid,
            "bpref": calculated[qid],
            "availability": "available",
            "unavailable_reason": None,
            "judged_positive_count": sum(value > 0 for value in values.values()),
            "judged_negative_count": sum(value == 0 for value in values.values()),
            "unjudged_count": sum(target not in values for target in rankings[qid]),
        }
    ordered_rows = [by_query[qid] for qid in ordered_query_ids]
    available = [row["bpref"] for row in ordered_rows if row["bpref"] is not None]
    aggregate = {
        "metric": "BPref",
        "metric_classification": "standard_ir_calibration_only",
        "provider_name": provider_name,
        "provider_version": provider_version,
        "macro_bpref": sum(available) / len(available) if len(available) == len(ordered_rows) else None,
        "available_query_count": len(available),
        "unavailable_query_count": len(ordered_rows) - len(available),
        "unavailable_reason": None if len(available) == len(ordered_rows) else "incomplete_query_availability",
    }
    return ordered_rows, aggregate


def build_result_replay(
    *,
    profile: str,
    policy: dict[str, Any],
    input_hashes: dict[str, str | None],
    result: dict[str, Any],
) -> dict[str, Any]:
    if profile == PROFILE_AUTOMATIC:
        schema = SCHEMA_AUTOMATIC_REPLAY
    elif profile == PROFILE_CALIBRATION:
        schema = SCHEMA_CALIBRATION_REPLAY
    else:
        raise ValidationError("unsupported replay profile")
    replay = {
        "schema_version": schema,
        "evaluation_profile": profile,
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "classification": policy["classification"],
        "input_hashes": dict(sorted(input_hashes.items())),
        "result_schema_version": result["schema_version"],
        "result_sha256": sha256_json(result),
        "result_family": "automatic" if profile == PROFILE_AUTOMATIC else "calibration",
        "replay_sha256": "0" * 64,
    }
    replay["replay_sha256"] = self_hash(replay, "replay_sha256")
    return replay
