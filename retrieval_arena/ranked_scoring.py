from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from typing import Any

from .errors import ValidationError
from .schemas import read_jsonl, write_jsonl

ENGINE_NAME = "ir_measures"
ENGINE_VERSION = "0.4.3"
ENGINE_WHEEL_SHA256 = "3d8b3b5283e4a77df85f9f6c8a1c2c99a7421b78fa014ef97f2ddab3037d3dcf"
SCHEMA_DATASET = "retrieval_arena.ranked_dataset.v2"
SCHEMA_PREDICTION = "retrieval_arena.ranked_prediction.v2"
SCHEMA_POLICY = "retrieval_arena.ranked_metric_policy.v2"
SCHEMA_SCORE = "retrieval_arena.ranked_item_score.v2"
SCHEMA_AGGREGATE = "retrieval_arena.ranked_aggregate.v2"
SCHEMA_REPLAY = "retrieval_arena.ranked_replay.v2"


def _sha256_json(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _exact(row: dict[str, Any], keys: set[str], owner: str) -> None:
    if set(row) != keys:
        raise ValidationError(f"{owner} fields must be exactly {sorted(keys)}")


def _text(value: Any, owner: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{owner} must be a non-empty string")
    return value


def validate_ranked_inputs(
    dataset: dict[str, Any],
    qrels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, Any]]]:
    dataset_keys = {
        "schema_version", "dataset_id", "corpus_id", "query_set_id", "support_surface_id",
        "corpus_snapshot_id", "graph_snapshot_id", "ranked_item_granularity", "qrels_scope",
        "relevance_scale", "judgment_policy_id", "judgment_provenance_sha256",
        "ordered_query_ids", "support_group_by_query", "qrels_sha256", "dataset_manifest_sha256",
    }
    _exact(dataset, dataset_keys, "ranked dataset")
    if dataset["schema_version"] != SCHEMA_DATASET or dataset["ranked_item_granularity"] != "support_target":
        raise ValidationError("ranked dataset schema or item granularity is unsupported")
    if dataset["qrels_scope"] not in {"complete", "pooled"}:
        raise ValidationError("qrels_scope must be complete or pooled")
    if dataset["relevance_scale"] not in {"binary", "integer_0_3"}:
        raise ValidationError("relevance_scale is unsupported")
    ordered = dataset["ordered_query_ids"]
    groups = dataset["support_group_by_query"]
    if not isinstance(ordered, list) or not ordered or ordered != list(dict.fromkeys(ordered)):
        raise ValidationError("ordered_query_ids must be non-empty and unique")
    if not isinstance(groups, dict) or list(groups) != ordered:
        raise ValidationError("support_group_by_query must follow ordered_query_ids")
    for qid in ordered:
        _text(qid, "query_id")
        _text(groups[qid], f"support group for {qid}")

    qrel_keys = {"query_id", "support_target_id", "relevance", "judgment_id", "judgment_provenance_sha256"}
    qrels_by_query: dict[str, dict[str, int]] = {qid: {} for qid in ordered}
    for row in qrels:
        _exact(row, qrel_keys, "qrel")
        qid = _text(row["query_id"], "qrel query_id")
        target = _text(row["support_target_id"], "qrel support_target_id")
        relevance = row["relevance"]
        if qid not in qrels_by_query or isinstance(relevance, bool) or not isinstance(relevance, int) or relevance not in range(4):
            raise ValidationError("qrel query or relevance is invalid")
        if dataset["relevance_scale"] == "binary" and relevance not in {0, 1}:
            raise ValidationError("binary qrels require relevance 0 or 1")
        if target in qrels_by_query[qid]:
            raise ValidationError(f"duplicate qrel pair: {qid}/{target}")
        qrels_by_query[qid][target] = relevance
    for qid, values in qrels_by_query.items():
        if not any(value > 0 for value in values.values()):
            raise ValidationError(f"zero_relevant_items:{qid}")

    policy_keys = {
        "schema_version", "policy_id", "primary_metric", "secondary_metrics", "context_item_budget_k",
        "gain", "discount", "aggregation", "uncertainty_unit", "qrels_scope", "unjudged_policy",
        "tie_policy", "duplicate_policy", "engine_name", "engine_version", "engine_artifact_sha256",
        "provider_name", "provider_version", "parity_oracle_name", "parity_oracle_version",
        "parity_corpus_sha256", "policy_sha256",
    }
    _exact(policy, policy_keys, "metric policy")
    if policy["schema_version"] != SCHEMA_POLICY:
        raise ValidationError("unsupported metric policy schema")
    expected = {
        "primary_metric": "evidence_recall", "secondary_metrics": ["complete_support", "ndcg", "reciprocal_rank"],
        "gain": "identity", "discount": "log2_rank_plus_one", "aggregation": "query_macro",
        "uncertainty_unit": "connected_support_group", "tie_policy": "score_desc_target_id_asc",
        "duplicate_policy": "reject", "engine_name": ENGINE_NAME, "engine_version": ENGINE_VERSION,
        "engine_artifact_sha256": ENGINE_WHEEL_SHA256,
    }
    for name, value in expected.items():
        if policy[name] != value:
            raise ValidationError(f"metric policy mismatch:{name}")
    k = policy["context_item_budget_k"]
    if isinstance(k, bool) or not isinstance(k, int) or k < 1 or policy["qrels_scope"] != dataset["qrels_scope"]:
        raise ValidationError("metric cutoff or qrels scope is invalid")

    prediction_keys = {
        "schema_version", "query_id", "context_item_budget_k", "max_rank", "retrieval_exhausted",
        "items", "duplicate_collapse_count", "duplicate_collapse_sha256",
    }
    item_keys = {"support_target_id", "rank", "score", "source_rank", "source_item_id", "source_provenance_sha256"}
    predictions_by_query: dict[str, dict[str, Any]] = {}
    for row in predictions:
        _exact(row, prediction_keys, "ranked prediction")
        qid = _text(row["query_id"], "prediction query_id")
        if row["schema_version"] != SCHEMA_PREDICTION or qid not in qrels_by_query or qid in predictions_by_query:
            raise ValidationError(f"unknown or duplicate prediction:{qid}")
        if row["context_item_budget_k"] != k or not isinstance(row["retrieval_exhausted"], bool):
            raise ValidationError(f"prediction budget/exhaustion invalid:{qid}")
        items = row["items"]
        if not isinstance(items, list) or row["max_rank"] != len(items):
            raise ValidationError(f"prediction item shape invalid:{qid}")
        if len(items) < k and not row["retrieval_exhausted"]:
            raise ValidationError(f"truncated_run:{qid}")
        targets: list[str] = []
        for rank, item in enumerate(items, 1):
            _exact(item, item_keys, "ranked item")
            target = _text(item["support_target_id"], "support_target_id")
            score = item["score"]
            if item["rank"] != rank or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValidationError(f"rank or score invalid:{qid}/{rank}")
            targets.append(target)
        if len(targets) != len(set(targets)):
            raise ValidationError(f"duplicate canonical target:{qid}")
        expected_order = sorted(items, key=lambda item: (-float(item["score"]), item["support_target_id"]))
        if items != expected_order:
            raise ValidationError(f"tie/order policy violated:{qid}")
        if dataset["qrels_scope"] == "complete" and set(targets[:k]) - set(qrels_by_query[qid]):
            raise ValidationError(f"unjudged target under complete qrels:{qid}")
        predictions_by_query[qid] = row
    if list(predictions_by_query) != ordered:
        raise ValidationError("predictions must exactly follow ordered_query_ids")
    return qrels_by_query, predictions_by_query


def score_ranked(
    dataset: dict[str, Any],
    qrels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    qrels_by_query, runs = validate_ranked_inputs(dataset, qrels, predictions, policy)
    try:
        import ir_measures
    except ImportError as exc:
        raise ValidationError("ranked metric engine unavailable: ir_measures==0.4.3") from exc
    if getattr(ir_measures, "__version__", None) != ENGINE_VERSION:
        raise ValidationError("ranked metric engine version mismatch")
    if policy["provider_name"] != "pytrec_eval" or importlib.metadata.version("pytrec-eval-terrier") != policy["provider_version"]:
        raise ValidationError("ranked metric provider identity mismatch")
    k = policy["context_item_budget_k"]
    measures = [ir_measures.parse_measure(f"Recall@{k}"), ir_measures.parse_measure(f"nDCG@{k}"), ir_measures.parse_measure(f"RR@{k}")]
    engine_qrels = [ir_measures.Qrel(qid, target, rel) for qid, values in qrels_by_query.items() for target, rel in values.items()]
    engine_run = [ir_measures.ScoredDoc(qid, item["support_target_id"], float(item["score"])) for qid, row in runs.items() for item in row["items"]]
    calculated: dict[str, dict[str, float]] = {qid: {} for qid in runs}
    for metric in ir_measures.iter_calc(measures, engine_qrels, engine_run):
        calculated[str(metric.query_id)][str(metric.measure)] = float(metric.value)
    scores: list[dict[str, Any]] = []
    for qid in dataset["ordered_query_ids"]:
        positives = {target for target, rel in qrels_by_query[qid].items() if rel > 0}
        top = runs[qid]["items"][:k]
        retrieved = {item["support_target_id"] for item in top}
        unjudged = sorted(retrieved - set(qrels_by_query[qid]))
        unavailable: dict[str, str] = {}
        ndcg: float | None = calculated[qid][f"nDCG@{k}"]
        if dataset["qrels_scope"] == "pooled" and unjudged:
            ndcg = None
            unavailable["ndcg"] = "unjudged_top_k"
        row = {
            "schema_version": SCHEMA_SCORE, "query_id": qid,
            "support_group_id": dataset["support_group_by_query"][qid], "cutoff": k,
            "evidence_recall": calculated[qid][f"R@{k}"],
            "complete_support": 1.0 if positives <= retrieved else 0.0,
            "ndcg": ndcg, "reciprocal_rank": calculated[qid][f"RR@{k}"],
            "available_metrics": sorted({"evidence_recall", "complete_support", "reciprocal_rank", *([] if ndcg is None else ["ndcg"])}),
            "unavailable_metrics": unavailable, "positive_qrel_count": len(positives),
            "retrieved_count": len(top), "judged_top_k_count": len(retrieved - set(unjudged)),
        }
        row["engine_record_sha256"] = _sha256_json(row)
        scores.append(row)
    def mean(name: str) -> float | None:
        values = [float(row[name]) for row in scores if row[name] is not None]
        return sum(values) / len(values) if values else None
    aggregate = {
        "schema_version": SCHEMA_AGGREGATE, "dataset_id": dataset["dataset_id"],
        "policy_id": policy["policy_id"], "cutoff": k, "query_count": len(scores),
        "support_group_count": len(set(dataset["support_group_by_query"].values())),
        "macro_evidence_recall": mean("evidence_recall"), "macro_complete_support": mean("complete_support"),
        "macro_ndcg": mean("ndcg"), "mrr": mean("reciprocal_rank"),
        "available_counts": {name: sum(name in row["available_metrics"] for row in scores) for name in ("evidence_recall", "complete_support", "ndcg", "reciprocal_rank")},
        "unavailable_counts": {"ndcg": sum("ndcg" in row["unavailable_metrics"] for row in scores)},
        "unavailable_reasons": sorted({reason for row in scores for reason in row["unavailable_metrics"].values()}),
        "per_query_scores_sha256": _sha256_json(scores),
    }
    aggregate["aggregate_sha256"] = _sha256_json(aggregate)
    return scores, aggregate


def score_ranked_files(dataset_path: Path, qrels_path: Path, predictions_path: Path, policy_path: Path, output_dir: Path) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    qrels = read_jsonl(qrels_path)
    predictions = read_jsonl(predictions_path)
    if dataset.get("qrels_sha256") != hashlib.sha256(qrels_path.read_bytes()).hexdigest():
        raise ValidationError("ranked dataset qrels_sha256 mismatch")
    scores, aggregate = score_ranked(dataset, qrels, predictions, policy)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "ranked_item_scores.v2.jsonl", scores)
    aggregate_text = json.dumps(aggregate, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    (output_dir / "ranked_aggregate.v2.json").write_text(aggregate_text, encoding="utf-8", newline="\n")
    replay = {
        "schema_version": SCHEMA_REPLAY,
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "qrels_sha256": hashlib.sha256(qrels_path.read_bytes()).hexdigest(),
        "predictions_sha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "per_query_scores_sha256": hashlib.sha256((output_dir / "ranked_item_scores.v2.jsonl").read_bytes()).hexdigest(),
        "aggregate_sha256": hashlib.sha256((output_dir / "ranked_aggregate.v2.json").read_bytes()).hexdigest(),
        "engine_name": ENGINE_NAME, "engine_version": ENGINE_VERSION,
        "engine_artifact_sha256": ENGINE_WHEEL_SHA256,
        "provider_name": policy["provider_name"], "provider_version": policy["provider_version"],
        "context_item_budget_k": policy["context_item_budget_k"],
        "ordered_query_ids": dataset["ordered_query_ids"],
        "ordered_support_group_ids": list(dict.fromkeys(dataset["support_group_by_query"].values())),
    }
    replay["replay_sha256"] = _sha256_json(replay)
    (output_dir / "ranked_replay.v2.json").write_text(json.dumps(replay, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return {"scores": scores, "aggregate": aggregate, "replay": replay}
