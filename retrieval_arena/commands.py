from __future__ import annotations

from pathlib import Path
from typing import Any

from .git_provenance import describe_git_provenance, plan_git_comparison
from .harness import run_config, validate_phase1
from .replay_compare import compare_run_dirs
from .snapshot_diff import compare_snapshot_bundles
from .snapshots import write_corpus_snapshot_manifest, write_graph_snapshot_manifest, write_support_surface_manifest


def run_experiment(config_path: Path | str) -> dict[str, Any]:
    rows = run_config(config_path)
    return {"ok": True, "summary": f"RetrievalArena run complete: {len(rows)} dataset/test runs", "run_count": len(rows)}


def validate_experiment(config_path: Path | str) -> dict[str, Any]:
    validate_phase1(config_path)
    return {"ok": True, "summary": "RetrievalArena Phase 1 validation passed"}


def git_provenance(path: Path, *, ref: str | None = None) -> dict[str, Any]:
    provenance = describe_git_provenance(path, ref=ref)
    return {"ok": True, "summary": "Git provenance captured", "provenance": provenance}


def git_comparison_plan(repo_path: Path, *, left_ref: str, right_ref: str, output_dir: Path | None = None) -> dict[str, Any]:
    plan = plan_git_comparison(repo_path, left_ref=left_ref, right_ref=right_ref, output_dir=output_dir)
    return {"ok": True, "summary": "Git comparison plan created", "plan": plan}


def replay_compare(expected_run_dir: Path, actual_run_dir: Path, *, out_path: Path | None = None) -> dict[str, Any]:
    report = compare_run_dirs(expected_run_dir, actual_run_dir, out_path=out_path)
    status = "PASSED" if report["passed"] else "FAILED"
    summary = report["summary"]
    return {
        "ok": bool(report["passed"]),
        "summary": f"Replay fidelity {status}: {summary['changed_artifact_count']} changed artifacts, {summary['changed_question_count']} changed questions",
        "report": report,
        "written_artifacts": [str(out_path)] if out_path is not None else [],
    }


def snapshot_manifest(
    dataset_path: Path,
    out_dir: Path,
    *,
    corpus_id: str,
    snapshot_id: str,
    corpus_snapshot_id: str | None = None,
    query_set_id: str | None = None,
    extraction_version: str = "unknown",
    parser_version: str = "unknown",
    graph_extraction_version: str = "unknown",
    source_name: str | None = None,
    source_url: str | None = None,
    source_commit: str | None = None,
    source_release: str | None = None,
    source_timestamp: str | None = None,
    manifest_types: set[str] | None = None,
) -> dict[str, Any]:
    requested_types = manifest_types or {"corpus", "graph", "support"}
    valid_types = {"corpus", "graph", "support"}
    unknown_types = requested_types - valid_types
    if unknown_types:
        from .errors import RetrievalArenaError

        raise RetrievalArenaError(f"Unknown snapshot manifest types: {sorted(unknown_types)}")

    written: list[Path] = []
    manifests: dict[str, dict[str, Any]] = {}
    if "corpus" in requested_types:
        path = out_dir / "corpus_snapshot_manifest.json"
        manifests["corpus"] = write_corpus_snapshot_manifest(
            path,
            dataset_path,
            corpus_id=corpus_id,
            snapshot_id=snapshot_id,
            extraction_version=extraction_version,
            parser_version=parser_version,
            source_name=source_name,
            source_url=source_url,
            source_commit=source_commit,
            source_release=source_release,
            source_timestamp=source_timestamp,
        )
        written.append(path)
    resolved_corpus_snapshot_id = corpus_snapshot_id or snapshot_id
    if "graph" in requested_types:
        path = out_dir / "graph_snapshot_manifest.json"
        manifests["graph"] = write_graph_snapshot_manifest(
            path,
            dataset_path,
            corpus_id=corpus_id,
            snapshot_id=snapshot_id,
            corpus_snapshot_id=resolved_corpus_snapshot_id,
            graph_extraction_version=graph_extraction_version,
        )
        written.append(path)
    if "support" in requested_types:
        if not query_set_id:
            from .errors import RetrievalArenaError

            raise RetrievalArenaError("--query-set-id is required when writing support manifests.")
        path = out_dir / "support_surface_manifest.json"
        manifests["support"] = write_support_surface_manifest(
            path,
            dataset_path,
            corpus_id=corpus_id,
            snapshot_id=snapshot_id,
            corpus_snapshot_id=resolved_corpus_snapshot_id,
            query_set_id=query_set_id,
        )
        written.append(path)
    return {
        "ok": True,
        "summary": f"RetrievalArena snapshot manifests written: {len(written)}",
        "written_artifacts": [str(path) for path in written],
        "manifests": manifests,
    }


def snapshot_diff(before: Path, after: Path, *, out_path: Path | None = None, markdown_out_path: Path | None = None) -> dict[str, Any]:
    report = compare_snapshot_bundles(before, after, out_path=out_path, markdown_out_path=markdown_out_path)
    status = "PASSED" if report["passed"] else "FAILED"
    summary = report["summary"]
    return {
        "ok": bool(report["passed"]),
        "summary": (
            "Snapshot diff "
            f"{status}: files +{summary['added_file_count']} "
            f"-{summary['removed_file_count']} "
            f"changed {summary['changed_file_count']}; "
            f"edges +{summary['added_edge_count']} "
            f"-{summary['removed_edge_count']}; "
            f"support questions changed {summary['changed_support_question_count']}"
        ),
        "report": report,
        "written_artifacts": [str(path) for path in (out_path, markdown_out_path) if path is not None],
    }
