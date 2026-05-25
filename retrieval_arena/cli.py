from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import RetrievalArenaError
from .git_provenance import describe_git_provenance, plan_git_comparison
from .harness import run_config, validate_phase1
from .replay_compare import compare_run_dirs
from .replay_manifests import manifest_json_text
from .snapshot_diff import compare_snapshot_bundles
from .snapshots import write_corpus_snapshot_manifest, write_graph_snapshot_manifest, write_support_surface_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrieval-arena")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run a RetrievalArena experiment.")
    run.add_argument("--config", help="Path to experiment YAML config.")
    run_subparsers = run.add_subparsers(dest="run_command")
    compare_git = run_subparsers.add_parser("compare-git", help="Plan a branch-aware comparison without checkout.")
    compare_git.add_argument("--config", required=True, help="Path to experiment YAML config.")
    compare_git.add_argument("--left-ref", required=True, help="Left Git ref.")
    compare_git.add_argument("--right-ref", required=True, help="Right Git ref.")
    compare_git.add_argument("--repo", default=".", help="Git repository path to resolve refs.")
    compare_git.add_argument("--out-dir", help="Base output directory for planned isolated runs.")
    validate = subparsers.add_parser("validate", help="Run Phase 1 automated validation gate.")
    validate.add_argument("--config", required=True, help="Path to experiment YAML config.")
    provenance = subparsers.add_parser("provenance", help="Inspect provenance sources.")
    provenance_subparsers = provenance.add_subparsers(dest="provenance_command", required=True)
    git = provenance_subparsers.add_parser("git", help="Print Git provenance for a path.")
    git.add_argument("--path", required=True, help="Path to inspect.")
    git.add_argument("--ref", help="Optional ref to validate without checkout.")
    replay = subparsers.add_parser("replay", help="Compare replay outputs.")
    replay_subparsers = replay.add_subparsers(dest="replay_command", required=True)
    replay_compare = replay_subparsers.add_parser("compare", help="Compare two completed run directories.")
    replay_compare.add_argument("--expected", required=True, help="Expected run directory.")
    replay_compare.add_argument("--actual", required=True, help="Actual run directory.")
    replay_compare.add_argument("--out", help="Optional path for replay_fidelity_report.json.")
    snapshot = subparsers.add_parser("snapshot", help="Generate snapshot observability artifacts.")
    snapshot_subparsers = snapshot.add_subparsers(dest="snapshot_command", required=True)
    manifest = snapshot_subparsers.add_parser("manifest", help="Write snapshot manifests for a prepared dataset.")
    manifest.add_argument("--dataset", required=True, help="Prepared dataset directory.")
    manifest.add_argument("--out-dir", required=True, help="Directory for written manifests.")
    manifest.add_argument("--corpus-id", required=True, help="Stable corpus identifier.")
    manifest.add_argument("--snapshot-id", required=True, help="Stable snapshot identifier.")
    manifest.add_argument("--corpus-snapshot-id", help="Corpus snapshot identifier referenced by graph/support manifests.")
    manifest.add_argument("--query-set-id", help="Query set identifier for support-surface manifests.")
    manifest.add_argument("--extraction-version", default="unknown", help="Corpus extraction version.")
    manifest.add_argument("--parser-version", default="unknown", help="Corpus parser version.")
    manifest.add_argument("--graph-extraction-version", default="unknown", help="Graph extraction version.")
    manifest.add_argument("--source-name")
    manifest.add_argument("--source-url")
    manifest.add_argument("--source-commit")
    manifest.add_argument("--source-release")
    manifest.add_argument("--source-timestamp")
    manifest.add_argument("--types", default="corpus,graph,support", help="Comma-separated manifest types: corpus,graph,support.")
    snapshot_diff = snapshot_subparsers.add_parser("diff", help="Compare two completed snapshot manifest bundles.")
    snapshot_diff.add_argument("--before", required=True, help="Before snapshot directory or corpus manifest path.")
    snapshot_diff.add_argument("--after", required=True, help="After snapshot directory or corpus manifest path.")
    snapshot_diff.add_argument("--out", help="Optional path for snapshot_diff.json.")
    snapshot_diff.add_argument("--markdown-out", help="Optional path for snapshot_diff.md.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            if args.run_command == "compare-git":
                plan = plan_git_comparison(
                    Path(args.repo),
                    left_ref=args.left_ref,
                    right_ref=args.right_ref,
                    output_dir=Path(args.out_dir) if args.out_dir else None,
                )
                print(manifest_json_text(plan), end="")
            else:
                if not args.config:
                    raise RetrievalArenaError("run requires --config.")
                rows = run_config(args.config)
                print(f"RetrievalArena run complete: {len(rows)} dataset/test runs")
        elif args.command == "validate":
            validate_phase1(args.config)
            print("RetrievalArena Phase 1 validation passed")
        elif args.command == "provenance" and args.provenance_command == "git":
            print(manifest_json_text(describe_git_provenance(Path(args.path), ref=args.ref)), end="")
        elif args.command == "replay" and args.replay_command == "compare":
            report = compare_run_dirs(
                Path(args.expected),
                Path(args.actual),
                out_path=Path(args.out) if args.out else None,
            )
            status = "PASSED" if report["passed"] else "FAILED"
            changed_artifacts = report["summary"]["changed_artifact_count"]
            changed_questions = report["summary"]["changed_question_count"]
            print(f"Replay fidelity {status}: {changed_artifacts} changed artifacts, {changed_questions} changed questions")
        elif args.command == "snapshot" and args.snapshot_command == "manifest":
            dataset_path = Path(args.dataset)
            out_dir = Path(args.out_dir)
            requested_types = {item.strip() for item in args.types.split(",") if item.strip()}
            valid_types = {"corpus", "graph", "support"}
            unknown_types = requested_types - valid_types
            if unknown_types:
                raise RetrievalArenaError(f"Unknown snapshot manifest types: {sorted(unknown_types)}")
            written: list[Path] = []
            if "corpus" in requested_types:
                write_corpus_snapshot_manifest(
                    out_dir / "corpus_snapshot_manifest.json",
                    dataset_path,
                    corpus_id=args.corpus_id,
                    snapshot_id=args.snapshot_id,
                    extraction_version=args.extraction_version,
                    parser_version=args.parser_version,
                    source_name=args.source_name,
                    source_url=args.source_url,
                    source_commit=args.source_commit,
                    source_release=args.source_release,
                    source_timestamp=args.source_timestamp,
                )
                written.append(out_dir / "corpus_snapshot_manifest.json")
            corpus_snapshot_id = args.corpus_snapshot_id or args.snapshot_id
            if "graph" in requested_types:
                write_graph_snapshot_manifest(
                    out_dir / "graph_snapshot_manifest.json",
                    dataset_path,
                    corpus_id=args.corpus_id,
                    snapshot_id=args.snapshot_id,
                    corpus_snapshot_id=corpus_snapshot_id,
                    graph_extraction_version=args.graph_extraction_version,
                )
                written.append(out_dir / "graph_snapshot_manifest.json")
            if "support" in requested_types:
                if not args.query_set_id:
                    raise RetrievalArenaError("--query-set-id is required when writing support manifests.")
                write_support_surface_manifest(
                    out_dir / "support_surface_manifest.json",
                    dataset_path,
                    corpus_id=args.corpus_id,
                    snapshot_id=args.snapshot_id,
                    corpus_snapshot_id=corpus_snapshot_id,
                    query_set_id=args.query_set_id,
                )
                written.append(out_dir / "support_surface_manifest.json")
            print(f"RetrievalArena snapshot manifests written: {len(written)}")
        elif args.command == "snapshot" and args.snapshot_command == "diff":
            report = compare_snapshot_bundles(
                Path(args.before),
                Path(args.after),
                out_path=Path(args.out) if args.out else None,
                markdown_out_path=Path(args.markdown_out) if args.markdown_out else None,
            )
            status = "PASSED" if report["passed"] else "FAILED"
            summary = report["summary"]
            print(
                "Snapshot diff "
                f"{status}: files +{summary['added_file_count']} "
                f"-{summary['removed_file_count']} "
                f"changed {summary['changed_file_count']}; "
                f"edges +{summary['added_edge_count']} "
                f"-{summary['removed_edge_count']}; "
                f"support questions changed {summary['changed_support_question_count']}"
            )
        return 0
    except RetrievalArenaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
