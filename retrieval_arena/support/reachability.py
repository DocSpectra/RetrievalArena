"""Support reachability surface (S036): how far the support set is from the root.

Computed at import from the corpus, the directed link graph, the question set
and the support audit alone -- no answer text, no model, no retrieval run. It
answers "could a lane that starts at ``index.md`` reach the documents the
questions depend on?" before any campaign starts. Pure functions over files;
the output carries no timestamp so the same inputs give the same bytes.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..diagnostics import _distances_from_targets
from ..errors import ValidationError
from ..schemas import read_jsonl
from ..snapshots import corpus_doc_id, support_doc_ids_from_row


SUPPORT_REACHABILITY_SCHEMA_VERSION = "retrieval_arena.support_reachability.v1"
SUPPORT_REACHABILITY_METHOD = "support_reachability_v1"
SUPPORT_REACHABILITY_FILE = "support_reachability.v1.json"
UNREACHABLE = "unreachable"
HOP_THRESHOLDS = (1, 2, 3, 8)
QUESTION_HOP_THRESHOLD = 3
ROOT_DOC_NAME = "index"
# The S025 candidate corpora carry flat ids (``<stem>__<sha1(path)[:8]>``); the
# root of such a corpus is the one document whose stem is exactly ``index``.
FLAT_ROOT_RE = re.compile(r"^index__[0-9a-f]{8}$")


def compute_support_reachability(dataset_path: Path) -> dict[str, Any]:
    """Return the reachability surface for one prepared dataset.

    A dataset missing ``corpus/``, ``graph_edges.csv``, ``questions.jsonl`` or
    ``faq_support_audit.jsonl`` (or whose corpus has no root document) gets
    ``status: unavailable`` with the missing input named and no numbers.
    """
    dataset_path = Path(dataset_path)
    surface: dict[str, Any] = {
        "schema_version": SUPPORT_REACHABILITY_SCHEMA_VERSION,
        "method": SUPPORT_REACHABILITY_METHOD,
        "hop_thresholds": list(HOP_THRESHOLDS),
        "question_hop_threshold": QUESTION_HOP_THRESHOLD,
    }
    corpus_dir = dataset_path / "corpus"
    edges_path = dataset_path / "graph_edges.csv"
    questions_path = dataset_path / "questions.jsonl"
    audit_path = dataset_path / "faq_support_audit.jsonl"
    for path, reason in (
        (corpus_dir, "corpus_missing"),
        (edges_path, "graph_edges_missing"),
        (questions_path, "questions_missing"),
        (audit_path, "support_audit_missing"),
    ):
        if not path.exists():
            return {**surface, "status": "unavailable", "reason": reason}

    documents = _corpus_documents(corpus_dir)
    root = _root_doc_id(documents)
    if root is None:
        return {**surface, "status": "unavailable", "reason": "root_missing"}
    edges = _read_edges(edges_path)
    nodes = sorted(set(documents) | {node for edge in edges for node in edge})
    out_adjacency: dict[str, set[str]] = defaultdict(set)
    undirected: dict[str, set[str]] = defaultdict(set)
    in_degree: Counter[str] = Counter()
    out_degree: Counter[str] = Counter()
    for source, target in edges:
        out_adjacency[source].add(target)
        undirected[source].add(target)
        undirected[target].add(source)
        out_degree[source] += 1
        in_degree[target] += 1
    directed_hops = _distances_from_targets([root], dict(out_adjacency))
    undirected_hops = _distances_from_targets([root], dict(undirected))

    question_order = [str(row["question_id"]) for row in read_jsonl(questions_path)]
    audit_rows: dict[str, dict[str, Any]] = {}
    for line_number, row in enumerate(read_jsonl(audit_path), start=1):
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            raise ValidationError(f"faq_support_audit.jsonl line {line_number} requires question_id.")
        audit_rows[question_id] = {
            "targets": support_doc_ids_from_row(row, line_number=line_number),
            "label": str(row.get("support_label", "unknown")),
        }
    for question_id in audit_rows:
        if question_id not in question_order:
            question_order.append(question_id)

    corpus_count = len(documents)

    def target_record(doc_id: str) -> dict[str, Any]:
        size = documents.get(doc_id)
        return {
            "doc_id": doc_id,
            "in_corpus": doc_id in documents,
            "in_degree": in_degree.get(doc_id, 0),
            "out_degree": out_degree.get(doc_id, 0),
            "lines": None if size is None else size["lines"],
            "chars": None if size is None else size["chars"],
            "hops_from_root": directed_hops.get(doc_id, UNREACHABLE),
            "hops_from_root_undirected": undirected_hops.get(doc_id, UNREACHABLE),
        }

    questions: list[dict[str, Any]] = []
    distinct_targets: set[str] = set()
    for question_id in question_order:
        audit = audit_rows.get(question_id)
        targets = [] if audit is None else list(audit["targets"])
        distinct_targets.update(targets)
        records = [target_record(doc_id) for doc_id in targets]
        hops = [record["hops_from_root"] for record in records if record["hops_from_root"] != UNREACHABLE]
        questions.append(
            {
                "question_id": question_id,
                "support_label": None if audit is None else audit["label"],
                "support_documents": len(targets),
                "support_fraction_of_corpus": _fraction(len(targets), corpus_count),
                "targets": records,
                "min_hops_from_root": min(hops) if hops else UNREACHABLE,
                "has_target_within_3_hops": any(hop <= QUESTION_HOP_THRESHOLD for hop in hops),
                "all_targets_orphans": bool(records) and all(record["in_degree"] == 0 for record in records),
            }
        )
    target_records = {doc_id: target_record(doc_id) for doc_id in sorted(distinct_targets)}
    reachable = [record["hops_from_root"] for record in target_records.values() if record["hops_from_root"] != UNREACHABLE]
    reachable_undirected = [
        record["hops_from_root_undirected"]
        for record in target_records.values()
        if record["hops_from_root_undirected"] != UNREACHABLE
    ]
    labels = Counter(str(row["support_label"]) for row in questions if row["support_label"] is not None)
    with_targets = [row for row in questions if row["targets"]]
    aggregate = {
        "question_count": len(questions),
        "questions_with_targets": len(with_targets),
        "corpus_document_count": corpus_count,
        "support_documents": len(target_records),
        "support_fraction_of_corpus": _fraction(len(target_records), corpus_count),
        "targets_reachable": len(reachable),
        "targets_unreachable": len(target_records) - len(reachable),
        "median_hops": statistics.median(reachable) if reachable else None,
        "targets_reachable_undirected": len(reachable_undirected),
        "targets_unreachable_undirected": len(target_records) - len(reachable_undirected),
        "median_hops_undirected": statistics.median(reachable_undirected) if reachable_undirected else None,
        "orphan_fraction": _fraction(sum(1 for r in target_records.values() if r["in_degree"] == 0), len(target_records)),
        "questions_with_target_within_3_hops_fraction": _fraction(
            sum(1 for row in with_targets if row["has_target_within_3_hops"]), len(with_targets)
        ),
        "questions_all_targets_orphan_fraction": _fraction(
            sum(1 for row in with_targets if row["all_targets_orphans"]), len(with_targets)
        ),
        "support_class_mix": dict(sorted(labels.items())),
    }
    for threshold in HOP_THRESHOLDS:
        aggregate[f"fraction_within_{threshold}_hops"] = _fraction(
            sum(1 for hop in reachable if hop <= threshold), len(target_records)
        )
        aggregate[f"fraction_within_{threshold}_hops_undirected"] = _fraction(
            sum(1 for hop in reachable_undirected if hop <= threshold), len(target_records)
        )
    return {
        **surface,
        "status": "ok",
        "reason": None,
        "root_doc_id": root,
        "graph": {"node_count": len(nodes), "edge_count": len(edges)},
        "aggregate": aggregate,
        "targets": list(target_records.values()),
        "questions": questions,
    }


def write_support_reachability(dataset_path: Path) -> tuple[Path, dict[str, Any]]:
    """Compute the surface and write it beside ``faq_support_audit.jsonl``."""
    surface = compute_support_reachability(dataset_path)
    destination = Path(dataset_path) / SUPPORT_REACHABILITY_FILE
    destination.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination, surface


def _fraction(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _corpus_documents(corpus_dir: Path) -> dict[str, dict[str, int]]:
    documents: dict[str, dict[str, int]] = {}
    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        documents[corpus_doc_id(path.relative_to(corpus_dir).as_posix())] = {
            "lines": len(text.splitlines()),
            "chars": len(text),
        }
    return documents


def _root_doc_id(documents: dict[str, Any]) -> str | None:
    if ROOT_DOC_NAME in documents:
        return ROOT_DOC_NAME
    flat = [doc_id for doc_id in documents if FLAT_ROOT_RE.match(doc_id)]
    return flat[0] if len(flat) == 1 else None


def _read_edges(path: Path) -> list[tuple[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["source", "target"]:
            raise ValidationError("graph_edges.csv header must be source,target")
        return sorted({(row["source"], row["target"]) for row in reader if row.get("source") and row.get("target")})
