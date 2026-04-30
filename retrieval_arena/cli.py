from __future__ import annotations

import argparse
import sys

from .errors import RetrievalArenaError
from .harness import run_config, validate_phase1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrieval-arena")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="Run a RetrievalArena experiment.")
    run.add_argument("--config", required=True, help="Path to experiment YAML config.")
    validate = subparsers.add_parser("validate", help="Run Phase 1 automated validation gate.")
    validate.add_argument("--config", required=True, help="Path to experiment YAML config.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            rows = run_config(args.config)
            print(f"RetrievalArena run complete: {len(rows)} dataset/test runs")
        elif args.command == "validate":
            validate_phase1(args.config)
            print("RetrievalArena Phase 1 validation passed")
        return 0
    except RetrievalArenaError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())