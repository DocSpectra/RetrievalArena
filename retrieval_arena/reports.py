from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def render_profile_report(result: dict[str, Any]) -> str:
    """Render automatic and calibration families without a composite score."""
    lines = [
        "# Retrieval Arena evaluation profile report", "",
        f"- Profile: `{result['evaluation_profile']}`",
        f"- Classification: `{result['classification']}`",
        f"- Result family: `{result['result_family']}`",
        "- Composite metric: none", "",
    ]
    if result["result_family"] == "automatic":
        aggregate = result["metric_families"]["standard_ranked_retrieval"]["aggregate"]
        diagnostic = result["metric_families"]["project_specific_diagnostics"]["requirement_aggregate"]
        audit_hashes = result["qrel_audit_hashes"]
        lines.extend([
            "## Standard ranked retrieval", "",
            f"- Source qrels scope: `{result['source_qrels_scope']}`",
            f"- Effective destination qrels scope: `{result['qrels_scope']}`",
            f"- Macro evidence Recall@k: {aggregate['macro_evidence_recall']}",
            f"- Macro nDCG@k: {aggregate['macro_ndcg']}",
            f"- MRR@k: {aggregate['mrr']}", "",
            "## Qrel disposition audit", "",
            f"- Destination-qrel inventory SHA-256: `{audit_hashes['destination_qrel_inventory_sha256']}`",
            f"- Source-qrel dispositions SHA-256: `{audit_hashes['source_qrel_dispositions_sha256']}`",
            f"- Destination top-k judgments SHA-256: `{audit_hashes['destination_top_k_judgments_sha256']}`",
            f"- Effective scoring view SHA-256: `{audit_hashes['effective_scoring_view_sha256']}`", "",
            "## Project-specific diagnostics", "",
            f"- RequirementCoverage@k: {diagnostic['macro_requirement_coverage']}",
            f"- AllRequirements@k: {diagnostic['macro_all_requirements']}", "",
            "These diagnostics are not standard Recall and do not enter a composite.",
        ])
    elif result["result_family"] == "calibration":
        bpref = result["metric_families"]["calibrated_relevance"]["bpref_aggregate"]
        lines.extend([
            "## Human-calibrated relevance", "",
            f"- Provider-owned BPref: {bpref['macro_bpref']}",
            "- Unjudged items are ignored under BPref semantics.", "",
            "Calibrated answer and attribution judgments remain separate families.",
        ])
    else:
        raise ValueError("unsupported evaluation result family")
    return "\n".join(lines) + "\n"


def write_run_scores(run_dir: Path, item_scores: list[dict[str, Any]], aggregate: dict[str, Any]) -> None:
    with (run_dir / "item_scores.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in item_scores:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (run_dir / "scores.json").write_text(json.dumps(aggregate, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(experiment_dir: Path, rows: list[dict[str, Any]]) -> None:
    experiment_dir.mkdir(parents=True, exist_ok=True)
    fields = ["dataset", "test", "run_id", "image", "build_context", "match_percent", "mean_f1", "mean_precision", "mean_recall", "mean_lexical_overlap", "num_questions", "run_dir"]
    with (experiment_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    lines = ["# RetrievalArena Summary", "", "| Dataset | Test | Match % | Mean F1 | Questions |", "|---|---|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['dataset']} | {row['test']} | {row['match_percent']:.3f} | {row['mean_f1']:.3f} | {row['num_questions']} |")
    lines.append("")
    (experiment_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
