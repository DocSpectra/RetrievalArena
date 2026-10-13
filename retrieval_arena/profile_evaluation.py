from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .dual_profile import (
    PROFILE_AUTOMATIC,
    PROFILE_CALIBRATION,
    SCHEMA_AUTOMATIC_RESULT,
    SCHEMA_CALIBRATION_RESULT,
    compose_destination_qrels,
    score_bpref,
    score_requirements,
    self_hash,
    sha256_file,
    sha256_json,
    validate_bertscore_provider,
    validate_bertscore_scores,
    validate_bindings,
    validate_lineage,
    validate_profile_policy,
    validate_requirements,
    validate_destination_qrel_inventory,
)
from .errors import ValidationError
from .audit import build_profile_audit
from .evidence_export import build_profile_evidence
from .ranked_scoring import score_ranked, validate_ranked_inputs
from .replay_manifests import build_profile_replay_manifest
from .reports import render_profile_report
from .review_packets import import_judgments, validate_review_packet, validate_review_policy
from .schemas import read_jsonl


SCHEMA_CONFIG = "retrieval_arena.dual_profile_config.v1"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"JSON artifact must be an object:{path}")
    return value


def _resolve(base: Path, value: Any, owner: str, optional: bool = False) -> Path | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{owner} must be a non-empty path string")
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def validate_dual_profile_config(config: dict[str, Any], config_path: Path) -> dict[str, Path | None]:
    fields = {
        "schema_version",
        "automatic_qrels_snapshot_id",
        "profile_policy_path",
        "ranked_dataset_path",
        "automatic_qrels_path",
        "ranked_predictions_path",
        "ranked_metric_policy_path",
        "requirements_path",
        "bindings_path",
        "lineage_path",
        "requirement_predictions_path",
        "answer_pairs_path",
        "bertscore_provider_path",
        "bertscore_scores_path",
        "review_policy_path",
        "review_packet_path",
        "judgment_artifact_path",
    }
    if set(config) != fields:
        raise ValidationError(f"dual-profile config fields must be exactly {sorted(fields)}")
    if config["schema_version"] != SCHEMA_CONFIG:
        raise ValidationError("unsupported dual-profile config schema")
    if not isinstance(config["automatic_qrels_snapshot_id"], str) or not config["automatic_qrels_snapshot_id"]:
        raise ValidationError("automatic_qrels_snapshot_id must be a non-empty string")
    if config["bertscore_scores_path"] is not None and config["bertscore_provider_path"] is None:
        raise ValidationError("BERTScore scores artifact requires a registered provider artifact")
    optional = {
        "answer_pairs_path",
        "bertscore_provider_path",
        "bertscore_scores_path",
        "review_policy_path",
        "review_packet_path",
        "judgment_artifact_path",
    }
    base = config_path.parent
    paths = {
        name: _resolve(base, value, name, name in optional)
        for name, value in config.items()
        if name not in {"schema_version", "automatic_qrels_snapshot_id"}
    }
    for name, path in paths.items():
        if path is not None and not path.is_file():
            if name in {"bertscore_provider_path", "bertscore_scores_path"}:
                # Missing optional provider material is represented as unavailable.
                continue
            raise ValidationError(f"dual-profile input artifact missing:{name}")
    return paths


def _lexical_scores(answer_pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for pair in answer_pairs:
        if set(pair) != {"query_id", "generated_answer", "reference_answer"}:
            raise ValidationError("answer pair fields must be query_id, generated_answer, reference_answer")
        qid = pair["query_id"]
        if not isinstance(qid, str) or not qid or qid in seen:
            raise ValidationError("answer pair query_id is empty or duplicate")
        seen.add(qid)
        generated = pair["generated_answer"]
        reference = pair["reference_answer"]
        if not isinstance(generated, str) or not isinstance(reference, str):
            raise ValidationError("answer pair texts must be strings")
        generated_tokens = re.findall(r"\w+", generated.lower())
        reference_tokens = re.findall(r"\w+", reference.lower())
        generated_counts = {token: generated_tokens.count(token) for token in set(generated_tokens)}
        reference_counts = {token: reference_tokens.count(token) for token in set(reference_tokens)}
        overlap = sum(min(generated_counts.get(token, 0), reference_counts.get(token, 0)) for token in set(generated_counts) | set(reference_counts))
        precision = overlap / len(generated_tokens) if generated_tokens else 0.0
        recall = overlap / len(reference_tokens) if reference_tokens else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        rows.append({"query_id": qid, "precision": precision, "recall": recall, "f1": f1})
    return rows


def _rankings(predictions: list[dict[str, Any]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in predictions:
        qid = row.get("query_id")
        items = row.get("items")
        if not isinstance(qid, str) or not isinstance(items, list):
            raise ValidationError("ranked prediction shape invalid for profile evaluation")
        result[qid] = [item["support_target_id"] for item in items]
    return result


def _score_ranked_profile(
    dataset: dict[str, Any],
    qrels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    ranked_policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    validate_ranked_inputs(dataset, qrels, predictions, ranked_policy, allow_zero_relevant=True)
    positive_queries = {
        row["query_id"] for row in qrels
        if isinstance(row.get("relevance"), int) and not isinstance(row.get("relevance"), bool) and row["relevance"] > 0
    }
    ordered = dataset.get("ordered_query_ids", [])
    unavailable_ids = [qid for qid in ordered if qid not in positive_queries]

    def bind_profile_scope(
        aggregate: dict[str, Any], available_count: int, unavailable_count: int, scope: str
    ) -> dict[str, Any]:
        aggregate.pop("aggregate_sha256", None)
        aggregate.update(
            {
                "profile_total_query_count": len(ordered),
                "profile_available_query_count": available_count,
                "profile_unavailable_query_count": unavailable_count,
                "profile_aggregate_scope": scope,
            }
        )
        aggregate["aggregate_sha256"] = sha256_json(aggregate)
        return aggregate

    if not unavailable_ids:
        scores, aggregate = score_ranked(dataset, qrels, predictions, ranked_policy)
        return scores, bind_profile_scope(aggregate, len(ordered), 0, "all_queries"), []
    available_ids = [qid for qid in ordered if qid in positive_queries]
    by_prediction = {row["query_id"]: row for row in predictions}
    unavailable = [
        {
            "query_id": qid,
            "support_group_id": dataset["support_group_by_query"][qid],
            "cutoff": ranked_policy["context_item_budget_k"],
            "metrics": ["evidence_recall", "complete_support", "ndcg", "reciprocal_rank"],
            "availability": "unavailable",
            "unavailable_reason": "zero_relevant_items",
            "retrieved_count": min(
                len(by_prediction[qid]["items"]), ranked_policy["context_item_budget_k"]
            ),
        }
        for qid in unavailable_ids
    ]
    if not available_ids:
        aggregate = {
            "schema_version": "retrieval_arena.ranked_aggregate.v2",
            "dataset_id": dataset["dataset_id"],
            "policy_id": ranked_policy["policy_id"],
            "cutoff": ranked_policy["context_item_budget_k"],
            "query_count": 0,
            "support_group_count": 0,
            "macro_evidence_recall": None,
            "macro_complete_support": None,
            "macro_ndcg": None,
            "mrr": None,
            "available_counts": {name: 0 for name in ("evidence_recall", "complete_support", "ndcg", "reciprocal_rank")},
            "unavailable_counts": {"ndcg": 0},
            "unavailable_reasons": ["zero_relevant_items"],
            "per_query_scores_sha256": sha256_json([]),
        }
        aggregate = bind_profile_scope(
            aggregate, 0, len(unavailable_ids), "unavailable_no_admitted_positive_queries"
        )
        return [], aggregate, unavailable
    subset_dataset = dict(dataset)
    subset_dataset["ordered_query_ids"] = available_ids
    subset_dataset["support_group_by_query"] = {
        qid: dataset["support_group_by_query"][qid] for qid in available_ids
    }
    subset_qrels = [row for row in qrels if row["query_id"] in positive_queries]
    subset_predictions = [row for row in predictions if row["query_id"] in positive_queries]
    scores, aggregate = score_ranked(subset_dataset, subset_qrels, subset_predictions, ranked_policy)
    return scores, bind_profile_scope(
        aggregate, len(available_ids), len(unavailable_ids), "available_queries_only"
    ), unavailable


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")


def evaluate_profile_files(config_path: Path, output_dir: Path) -> dict[str, Any]:
    config_path = config_path.resolve()
    config = _read_json(config_path)
    paths = validate_dual_profile_config(config, config_path)
    policy = validate_profile_policy(_read_json(paths["profile_policy_path"]))  # type: ignore[arg-type]
    profile = policy["evaluation_profile"]
    human_paths = (paths["review_policy_path"], paths["review_packet_path"], paths["judgment_artifact_path"])
    if profile == PROFILE_AUTOMATIC and any(path is not None for path in human_paths):
        raise ValidationError("profile mixing: operational_automatic rejects human judgment inputs")
    if profile == PROFILE_CALIBRATION and any(path is None for path in human_paths):
        raise ValidationError("scientific_calibration requires review policy, packet, and judgments")

    dataset = _read_json(paths["ranked_dataset_path"])  # type: ignore[arg-type]
    qrels = read_jsonl(paths["automatic_qrels_path"])  # type: ignore[arg-type]
    if dataset.get("qrels_sha256") != sha256_file(paths["automatic_qrels_path"]):  # type: ignore[arg-type]
        raise ValidationError("ranked dataset automatic_qrels_path hash mismatch")
    ranked_predictions = read_jsonl(paths["ranked_predictions_path"])  # type: ignore[arg-type]
    ranked_policy = _read_json(paths["ranked_metric_policy_path"])  # type: ignore[arg-type]
    requirements_artifact = _read_json(paths["requirements_path"])  # type: ignore[arg-type]
    requirements = validate_requirements(requirements_artifact)
    bindings_artifact = _read_json(paths["bindings_path"])  # type: ignore[arg-type]
    bindings = validate_bindings(bindings_artifact, requirements)
    lineage_artifact = _read_json(paths["lineage_path"])  # type: ignore[arg-type]
    _, lineage_counts = validate_lineage(lineage_artifact)
    if requirements_artifact["dataset_id"] != dataset["dataset_id"] or requirements_artifact["query_set_id"] != dataset["query_set_id"]:
        raise ValidationError("evidence requirement dataset/query identity mismatch")
    if bindings_artifact["dataset_id"] != dataset["dataset_id"] or bindings_artifact["query_set_id"] != dataset["query_set_id"]:
        raise ValidationError("requirement binding dataset/query identity mismatch")
    if lineage_artifact["destination_snapshot_id"] != dataset["corpus_snapshot_id"]:
        raise ValidationError("support lineage destination snapshot mismatch")
    if policy["requirements_sha256"] != requirements_artifact["requirements_sha256"]:
        raise ValidationError("profile policy evidence-requirement hash mismatch")
    if policy["bindings_sha256"] != bindings_artifact["bindings_sha256"]:
        raise ValidationError("profile policy requirement-binding hash mismatch")
    if policy["lineage_policy_id"] != lineage_artifact["policy_id"] or policy["lineage_sha256"] != lineage_artifact["lineage_sha256"]:
        raise ValidationError("profile policy support-lineage identity mismatch")
    if policy["context_item_budget_k"] != ranked_policy["context_item_budget_k"]:
        raise ValidationError("profile and ranked-v2 context-item cutoff mismatch")
    requirement_predictions = read_jsonl(paths["requirement_predictions_path"])  # type: ignore[arg-type]
    requirement_rows, requirement_aggregate = score_requirements(
        requirements,
        bindings,
        requirement_predictions,
        dataset["corpus_snapshot_id"],
        policy["context_item_budget_k"],
    )
    input_hashes: dict[str, str | None] = {
        name: sha256_file(path) if path is not None and path.is_file() else None
        for name, path in paths.items()
    }
    input_hashes["config"] = sha256_file(config_path)
    destination_qrel_inventory = compose_destination_qrels(
        qrels,
        lineage_artifact,
        config["automatic_qrels_snapshot_id"],
        dataset["corpus_snapshot_id"],
        dataset["ordered_query_ids"],
        dataset["relevance_scale"],
        source_qrels_scope=dataset["qrels_scope"],
        ranked_predictions=ranked_predictions,
        cutoff=policy["context_item_budget_k"],
        ranked_dataset_sha256=input_hashes["ranked_dataset_path"],  # type: ignore[arg-type]
        ranked_policy_sha256=input_hashes["ranked_metric_policy_path"],  # type: ignore[arg-type]
    )
    validate_destination_qrel_inventory(
        destination_qrel_inventory,
        qrels,
        lineage_artifact,
        config["automatic_qrels_snapshot_id"],
        dataset["corpus_snapshot_id"],
        dataset["ordered_query_ids"],
        dataset["relevance_scale"],
        source_qrels_scope=dataset["qrels_scope"],
        ranked_predictions=ranked_predictions,
        cutoff=policy["context_item_budget_k"],
        ranked_dataset_sha256=input_hashes["ranked_dataset_path"],  # type: ignore[arg-type]
        ranked_policy_sha256=input_hashes["ranked_metric_policy_path"],  # type: ignore[arg-type]
    )
    destination_qrels = destination_qrel_inventory["qrels"]

    if profile == PROFILE_AUTOMATIC:
        effective_dataset = dict(dataset)
        effective_dataset["qrels_scope"] = destination_qrel_inventory[
            "effective_qrels_scope"
        ]
        effective_policy = dict(ranked_policy)
        effective_policy["qrels_scope"] = destination_qrel_inventory[
            "effective_qrels_scope"
        ]
        effective_policy["policy_sha256"] = self_hash(
            effective_policy, "policy_sha256"
        )
        ranked_scores, ranked_aggregate, unavailable_ranked = _score_ranked_profile(
            effective_dataset, destination_qrels, ranked_predictions, effective_policy
        )
        answer_pairs: list[dict[str, Any]] = []
        lexical_rows: list[dict[str, Any]] = []
        if paths["answer_pairs_path"] is not None:
            answer_pairs = read_jsonl(paths["answer_pairs_path"])  # type: ignore[arg-type]
            lexical_rows = _lexical_scores(answer_pairs)
        bertscore = {
            "availability": "unavailable",
            "unavailable_reason": "not_requested",
            "provider": None,
            "scores": [],
            "declared_blind_spot": "does_not_establish_factuality_support_attribution_or_contradiction_absence",
        }
        provider_path = paths["bertscore_provider_path"]
        scores_path = paths["bertscore_scores_path"]
        if provider_path is not None:
            if not provider_path.is_file():
                bertscore["unavailable_reason"] = "registered_provider_artifact_missing"
            elif scores_path is None or not scores_path.is_file():
                bertscore["unavailable_reason"] = "registered_scores_artifact_missing"
            elif not answer_pairs:
                bertscore["unavailable_reason"] = "answer_pairs_missing"
            else:
                try:
                    provider = validate_bertscore_provider(_read_json(provider_path), provider_path.parent)
                except ValidationError as exc:
                    if not str(exc).startswith("BERTScore registered artifact missing:"):
                        raise
                    bertscore["unavailable_reason"] = str(exc).split(":", 1)[1] + "_artifact_missing"
                else:
                    scores_artifact = _read_json(scores_path)
                    scores = validate_bertscore_scores(scores_artifact, provider, sha256_json(answer_pairs))
                    if set(scores) != {row["query_id"] for row in answer_pairs}:
                        raise ValidationError("BERTScore scores must exactly cover answer pairs")
                    bertscore = {
                        "availability": "available",
                        "unavailable_reason": None,
                        "provider": {
                            "provider_id": provider["provider_id"],
                            "provider_sha256": provider["provider_sha256"],
                            "package_name": provider["package_name"],
                            "package_version": provider["package_version"],
                            "package_artifact_sha256": provider["package_artifact_sha256"],
                            "checkpoint_id": provider["checkpoint_id"],
                            "checkpoint_revision": provider["checkpoint_revision"],
                            "model_artifact_sha256": provider["model_artifact_sha256"],
                            "tokenizer_id": provider["tokenizer_id"],
                            "tokenizer_revision": provider["tokenizer_revision"],
                            "tokenizer_artifact_sha256": provider["tokenizer_artifact_sha256"],
                            "baseline_policy": provider["baseline_policy"],
                            "baseline_artifact_sha256": provider["baseline_artifact_sha256"],
                            "rescaling_policy": provider["rescaling_policy"],
                            "language": provider["language"],
                            "device": provider["device"],
                            "dtype": provider["dtype"],
                            "runtime_download": False,
                        },
                        "scores": [{"query_id": qid, **scores[qid]} for qid in sorted(scores)],
                        "declared_blind_spot": "does_not_establish_factuality_support_attribution_or_contradiction_absence",
                    }
        judged_top_k = destination_qrel_inventory["judged_top_k_count"]
        unjudged_top_k = destination_qrel_inventory["unjudged_top_k_count"]
        qrel_audit_hashes = {
            "destination_qrel_inventory_sha256": destination_qrel_inventory[
                "inventory_sha256"
            ],
            "source_qrel_dispositions_sha256": destination_qrel_inventory[
                "source_qrel_dispositions_sha256"
            ],
            "destination_top_k_judgments_sha256": destination_qrel_inventory[
                "destination_top_k_judgments_sha256"
            ],
            "effective_scoring_view_sha256": destination_qrel_inventory[
                "effective_scoring_view"
            ]["view_sha256"],
        }
        result = {
            "schema_version": SCHEMA_AUTOMATIC_RESULT,
            "evaluation_profile": PROFILE_AUTOMATIC,
            "result_family": "automatic",
            "classification": policy["classification"],
            "dataset_id": dataset["dataset_id"],
            "corpus_snapshot_id": dataset["corpus_snapshot_id"],
            "policy_id": policy["policy_id"],
            "judgment_source": "automatic_qrels_and_exact_lineage",
            "source_qrels_scope": dataset["qrels_scope"],
            "qrels_scope": destination_qrel_inventory["effective_qrels_scope"],
            "qrel_audit_hashes": qrel_audit_hashes,
            "metric_families": {
                "standard_ranked_retrieval": {
                    "classification": "standard_ir_metrics_provider_owned",
                    "per_query": ranked_scores,
                    "aggregate": ranked_aggregate,
                    "unavailable_queries": unavailable_ranked,
                },
                "project_specific_diagnostics": {
                    "classification": "project_specific_not_standard_recall",
                    "requirement_per_query": requirement_rows,
                    "requirement_aggregate": requirement_aggregate,
                    "lineage_counts_by_class": lineage_counts,
                    "transferred_qrels": destination_qrels,
                    "transferred_qrel_count": len(destination_qrels),
                    "destination_qrel_inventory": destination_qrel_inventory,
                },
                "automatic_answer_diagnostics": {
                    "classification": "diagnostic_only_non_composite",
                    "lexical": lexical_rows,
                    "bertscore": bertscore,
                },
            },
            "inventory": {
                "judged_top_k_count": judged_top_k,
                "unjudged_top_k_count": unjudged_top_k,
                "novel_candidate_count": lineage_counts["novel_support_unjudged"],
                "unresolved_candidate_count": lineage_counts["semantic_rewrite_unresolved"],
                "removed_support_count": lineage_counts["support_removed"],
            },
            "calibration": {"availability": "absent_valid", "required": False},
            "composite_metric": None,
            "input_hashes": dict(sorted(input_hashes.items())),
            "result_sha256": "0" * 64,
        }
        result["result_sha256"] = self_hash(result, "result_sha256")
    else:
        review_policy = validate_review_policy(_read_json(paths["review_policy_path"]))  # type: ignore[arg-type]
        packet = validate_review_packet(_read_json(paths["review_packet_path"]))  # type: ignore[arg-type]
        if review_policy["dataset_id"] != dataset["dataset_id"] or review_policy["query_set_id"] != dataset["query_set_id"]:
            raise ValidationError("calibration review policy dataset/query identity mismatch")
        if packet["dataset_id"] != dataset["dataset_id"] or packet["query_set_id"] != dataset["query_set_id"]:
            raise ValidationError("calibration review packet dataset/query identity mismatch")
        if dataset["corpus_snapshot_id"] not in packet["ordered_snapshot_ids"]:
            raise ValidationError("calibration packet does not admit the ranked snapshot")
        judgment_import = import_judgments(
            _read_json(paths["judgment_artifact_path"]),  # type: ignore[arg-type]
            packet,
            review_policy,
        )
        if policy["classification"] == "confirmatory" and judgment_import["classification"] != "confirmatory":
            raise ValidationError("exploratory judgments cannot produce confirmatory calibration")
        rankings = _rankings(ranked_predictions)
        ordered = dataset["ordered_query_ids"]
        if set(rankings) != set(ordered):
            raise ValidationError("calibration ranking query set mismatch")
        bpref_rows, bpref_aggregate = score_bpref(
            ordered,
            rankings,
            judgment_import["judgments_by_query"],
            ranked_policy["provider_name"],
            ranked_policy["provider_version"],
        )
        result = {
            "schema_version": SCHEMA_CALIBRATION_RESULT,
            "evaluation_profile": PROFILE_CALIBRATION,
            "result_family": "calibration",
            "classification": judgment_import["classification"],
            "dataset_id": dataset["dataset_id"],
            "policy_id": policy["policy_id"],
            "judgment_source": "immutable_imported_judgment_artifact",
            "qrels_scope": "calibration_pool",
            "metric_families": {
                "calibrated_relevance": {
                    "classification": "human_calibrated_separate_family",
                    "bpref_per_query": bpref_rows,
                    "bpref_aggregate": bpref_aggregate,
                    "answer_bearing_relevance": judgment_import["calibrated_family_means"].get("answer_bearing_relevance"),
                },
                "calibrated_answer_quality": {
                    "semantic_answer_quality": judgment_import["calibrated_family_means"].get("semantic_answer_quality"),
                    "paraphrase_robustness": judgment_import["calibrated_family_means"].get("paraphrase_robustness"),
                },
                "calibrated_attribution": {
                    "attribution_correctness": judgment_import["calibrated_family_means"].get("attribution_correctness"),
                },
                "assessor_agreement": judgment_import["agreement"],
            },
            "automatic_family_reference": {
                "ranked_dataset_sha256": input_hashes["ranked_dataset_path"],
                "automatic_qrels_sha256": input_hashes["automatic_qrels_path"],
                "ranked_predictions_sha256": input_hashes["ranked_predictions_path"],
                "not_combined": True,
            },
            "composite_metric": None,
            "judgment_import_sha256": judgment_import["import_sha256"],
            "input_hashes": dict(sorted(input_hashes.items())),
            "result_sha256": "0" * 64,
        }
        result["result_sha256"] = self_hash(result, "result_sha256")

    replay = build_profile_replay_manifest(profile=profile, policy=policy, input_hashes=input_hashes, result=result)
    family = "automatic" if profile == PROFILE_AUTOMATIC else "calibration"
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / f"{family}_result.v1.json"
    replay_path = output_dir / f"{family}_replay.v1.json"
    audit = build_profile_audit(result, replay)
    evidence = build_profile_evidence(result, replay)
    _write_json(result_path, result)
    _write_json(replay_path, replay)
    _write_json(output_dir / f"{family}_audit.v1.json", audit)
    _write_json(output_dir / f"{family}_evidence.v1.json", evidence)
    (output_dir / f"{family}_report.v1.md").write_text(render_profile_report(result), encoding="utf-8", newline="\n")
    return {"result": result, "replay": replay, "written_artifacts": sorted(path.name for path in output_dir.iterdir())}
