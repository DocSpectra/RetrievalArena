from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .config import ExperimentConfig, TestConfig, load_config
from .docker import build_image, run_container
from .errors import RetrievalArenaError, ValidationError
from .reports import write_run_scores, write_summary
from .schemas import validate_dataset, validate_metadata, validate_predictions, write_jsonl
from .scoring import score_predictions


def git_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def stable_run_id(dataset_name: str, test_name: str) -> str:
    return hashlib.sha1(f"{dataset_name}\0{test_name}".encode("utf-8")).hexdigest()[:10]


def prepare_input(dataset_path: Path, test: TestConfig, input_dir: Path) -> None:
    if input_dir.exists():
        shutil.rmtree(input_dir)
    input_dir.mkdir(parents=True)
    shutil.copytree(dataset_path / "corpus", input_dir / "corpus")
    for filename in ["questions.jsonl", "answers.jsonl", "graph_edges.csv", "graph_metrics.json", "faq_support_audit.jsonl"]:
        source = dataset_path / filename
        if source.exists():
            shutil.copy2(source, input_dir / filename)
    (input_dir / "config.yaml").write_text(json.dumps(test.config, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_experiment(config: ExperimentConfig, *, output_dir: Path | None = None) -> list[dict[str, Any]]:
    root_output = (output_dir or config.output_dir).resolve()
    experiment_dir = root_output / config.experiment_name
    runs_dir = experiment_dir / "runs"
    experiment_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    repo_root = config.config_path.parent.parent

    for dataset in config.datasets:
        validate_dataset(dataset.path)
    for test in config.tests:
        build_image(test)

    for dataset in config.datasets:
        questions, answers = validate_dataset(dataset.path)
        for test in config.tests:
            run_id = stable_run_id(dataset.name, test.name)
            run_dir = runs_dir / f"{dataset.name}__{test.name}__{run_id}"
            input_dir = run_dir / "input"
            output_subdir = run_dir / "container_output"
            if run_dir.exists():
                shutil.rmtree(run_dir)
            run_dir.mkdir(parents=True)
            prepare_input(dataset.path, test, input_dir)
            started_at = datetime.now(timezone.utc).isoformat()
            run_container(test, input_dir, output_subdir)
            metadata = validate_metadata(output_subdir / "metadata.json")
            predictions = validate_predictions(output_subdir / "predictions.jsonl", questions)
            shutil.copy2(output_subdir / "predictions.jsonl", run_dir / "predictions.jsonl")
            shutil.copy2(output_subdir / "metadata.json", run_dir / "metadata.json")
            item_scores, aggregate = score_predictions(predictions, answers, config.scoring.match_threshold)
            aggregate.update({
                "dataset": dataset.name,
                "test": test.name,
                "image": test.image,
                "build_context": str(test.build_context) if test.build_context else "",
                "run_id": run_id,
                "started_at": started_at,
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "config_path": str(config.config_path),
                "retrieval_arena_version": __version__,
                "git_commit": git_commit(repo_root),
                "container_metadata": metadata,
            })
            write_run_scores(run_dir, item_scores, aggregate)
            summary_rows.append({**aggregate, "run_dir": str(run_dir)})
    write_summary(experiment_dir, summary_rows)
    return summary_rows


def run_config(config_path: str | Path) -> list[dict[str, Any]]:
    return run_experiment(load_config(config_path))


def assert_expected_oracle_scores(rows: list[dict[str, Any]]) -> None:
    by_test = {row["test"]: row for row in rows}
    required = {"oracle_perfect", "oracle_empty", "oracle_partial"}
    missing = required - set(by_test)
    if missing:
        raise ValidationError(f"Phase 1 validation config must include oracle tests: {sorted(missing)}")
    if by_test["oracle_perfect"]["match_percent"] != 1.0 or by_test["oracle_perfect"]["mean_f1"] != 1.0:
        raise ValidationError("oracle_perfect must score exactly 1.0 match_percent and mean_f1.")
    if by_test["oracle_empty"]["match_percent"] != 0.0 or by_test["oracle_empty"]["mean_f1"] != 0.0:
        raise ValidationError("oracle_empty must score exactly 0.0 match_percent and mean_f1.")
    partial = by_test["oracle_partial"]
    if not (0.0 < partial["mean_f1"] < 1.0):
        raise ValidationError("oracle_partial must produce intermediate mean_f1.")


def comparable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ["dataset", "test", "match_percent", "mean_f1", "mean_precision", "mean_recall", "mean_lexical_overlap", "num_questions"]
    return [{key: row[key] for key in keys} for row in sorted(rows, key=lambda r: (r["dataset"], r["test"]))]


def validate_phase1(config_path: str | Path) -> None:
    config = load_config(config_path)
    for dataset in config.datasets:
        validate_dataset(dataset.path)
    validation_root = config.output_dir / "_validation_phase1"
    first = run_experiment(config, output_dir=validation_root / "run1")
    assert_expected_oracle_scores(first)
    second = run_experiment(config, output_dir=validation_root / "run2")
    assert_expected_oracle_scores(second)
    if comparable(first) != comparable(second):
        raise RetrievalArenaError("Reproducibility check failed: two deterministic validation runs produced different scores.")

    bad_dir = validation_root / "contract_negative"
    bad_dir.mkdir(parents=True, exist_ok=True)
    questions, _ = validate_dataset(config.datasets[0].path)
    write_jsonl(bad_dir / "predictions.jsonl", [{"question_id": questions[0]["question_id"], "question": questions[0]["question"], "generated_answer": "", "retrieved_context": []}])
    try:
        validate_predictions(bad_dir / "predictions.jsonl", questions)
    except ValidationError:
        pass
    else:
        raise RetrievalArenaError("Contract negative check failed: missing predictions were accepted.")
