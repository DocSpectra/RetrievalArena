from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .commands import (
    git_comparison_plan,
    git_provenance,
    replay_compare,
    run_experiment,
    snapshot_diff,
    snapshot_manifest,
    validate_experiment,
)
from .corpus.comparison import run_corpus_snapshot_comparison
from .errors import RetrievalArenaError
from .replay_manifests import manifest_json_text


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
    corpus = subparsers.add_parser("corpus", help="Import and compare corpus snapshots.")
    corpus_subparsers = corpus.add_subparsers(dest="corpus_command", required=True)
    corpus_compare = corpus_subparsers.add_parser("compare-snapshots", help="Run a corpus source comparison plan.")
    corpus_compare.add_argument("--plan", required=True, help="Path to corpus snapshot comparison plan JSON.")
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
                result = git_comparison_plan(
                    Path(args.repo),
                    left_ref=args.left_ref,
                    right_ref=args.right_ref,
                    output_dir=Path(args.out_dir) if args.out_dir else None,
                )
                print(manifest_json_text(result["plan"]), end="")
            else:
                if not args.config:
                    raise RetrievalArenaError("run requires --config.")
                print(run_experiment(args.config)["summary"])
        elif args.command == "validate":
            print(validate_experiment(args.config)["summary"])
        elif args.command == "provenance" and args.provenance_command == "git":
            print(manifest_json_text(git_provenance(Path(args.path), ref=args.ref)["provenance"]), end="")
        elif args.command == "replay" and args.replay_command == "compare":
            result = replay_compare(
                Path(args.expected),
                Path(args.actual),
                out_path=Path(args.out) if args.out else None,
            )
            print(result["summary"])
        elif args.command == "corpus" and args.corpus_command == "compare-snapshots":
            result = run_corpus_snapshot_comparison(Path(args.plan))
            print(result["summary"])
        elif args.command == "snapshot" and args.snapshot_command == "manifest":
            dataset_path = Path(args.dataset)
            out_dir = Path(args.out_dir)
            requested_types = {item.strip() for item in args.types.split(",") if item.strip()}
            result = snapshot_manifest(
                dataset_path,
                out_dir,
                corpus_id=args.corpus_id,
                snapshot_id=args.snapshot_id,
                corpus_snapshot_id=args.corpus_snapshot_id,
                query_set_id=args.query_set_id,
                extraction_version=args.extraction_version,
                parser_version=args.parser_version,
                graph_extraction_version=args.graph_extraction_version,
                source_name=args.source_name,
                source_url=args.source_url,
                source_commit=args.source_commit,
                source_release=args.source_release,
                source_timestamp=args.source_timestamp,
                manifest_types=requested_types,
            )
            print(result["summary"])
        elif args.command == "snapshot" and args.snapshot_command == "diff":
            result = snapshot_diff(
                Path(args.before),
                Path(args.after),
                out_path=Path(args.out) if args.out else None,
                markdown_out_path=Path(args.markdown_out) if args.markdown_out else None,
            )
            print(result["summary"])
        return 0
    except RetrievalArenaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
