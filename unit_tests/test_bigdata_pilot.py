from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from retrieval_arena.bigdata_pilot import (
    _run_selected_retrieval_config,
    assemble_baseline_bundle,
    bigdata_pilot,
    load_pilot_plan,
    resolve_stage_list,
    select_run_from_experiment_manifest,
)
from retrieval_arena.cli import main
from retrieval_arena.errors import ValidationError
from retrieval_arena.manifests import write_manifest


CREATED_AT = "2026-05-26T00:00:00+00:00"


class BigDataPilotTests(unittest.TestCase):
    @contextmanager
    def workspace_tempdir(self) -> Iterator[Path]:
        root = Path(".tmp_unit_tests")
        root.mkdir(exist_ok=True)
        path = root / f"tmp_{uuid.uuid4().hex}"
        path.mkdir()
        try:
            yield path.resolve()
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def write_plan(self, path: Path, **overrides: Any) -> Path:
        plan: dict[str, Any] = {
            "schema_version": "retrieval_arena.bigdata2026_pilot_plan.v1",
            "pilot_id": "toy_pilot",
            "corpus_id": "toy_docs",
            "query_set_id": "toy-queries",
            "snapshot_pair": {
                "before": {"snapshot_id": "before", "source_descriptor": "before_source.json"},
                "after": {"snapshot_id": "after", "source_descriptor": "after_source.json"},
                "comparison_plan": "comparison.json",
            },
            "retrieval": {
                "before_config": "retrieval_before.yaml",
                "after_config": "retrieval_after.yaml",
                "selected_tests": ["rag_lexical_topk"],
                "selected_dataset": "toy_docs",
            },
            "comparisons": [
                {
                    "comparison_id": "rag_lexical_topk",
                    "before_run_selector": {"dataset": "toy_docs", "test": "rag_lexical_topk"},
                    "after_run_selector": {"dataset": "toy_docs", "test": "rag_lexical_topk"},
                }
            ],
            "output_root": "reports/pilot",
            "calibration_store": "calibration/pilot",
            "stages": ["snapshot_compare", "retrieval_before", "retrieval_after", "drift", "audit", "measurements", "baseline_bundle"],
            "reuse_policy": {
                "reuse_imports": True,
                "reuse_prepared_snapshots": True,
                "reuse_retrieval_runs": False,
                "overwrite_reports": True,
            },
            "baseline_bundle": {
                "include_raw": False,
                "include_processed": "selected",
                "include_graphs": "selected",
                "include_reports": True,
                "include_analysis": "selected",
                "include_results": True,
                "require_safe_to_redistribute": True,
            },
        }
        plan.update(overrides)
        path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def test_valid_pilot_plan_parsing_resolves_paths_relative_to_plan(self):
        with self.workspace_tempdir() as tmp:
            plan_path = self.write_plan(tmp / "pilot.json")

            plan = load_pilot_plan(plan_path)

            self.assertEqual(plan["pilot_id"], "toy_pilot")
            self.assertEqual(Path(plan["snapshot_pair"]["comparison_plan"]), tmp / "comparison.json")
            self.assertEqual(Path(plan["retrieval"]["before_config"]), tmp / "retrieval_before.yaml")
            self.assertEqual(Path(plan["output_root"]), tmp / "reports" / "pilot")

    def test_include_raw_true_is_rejected(self):
        with self.workspace_tempdir() as tmp:
            plan_path = self.write_plan(tmp / "pilot.json")
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["baseline_bundle"]["include_raw"] = True
            plan_path.write_text(json.dumps(raw), encoding="utf-8")

            with self.assertRaisesRegex(ValidationError, "include_raw"):
                load_pilot_plan(plan_path)

    def test_dry_run_resolves_stages_without_writing_outputs(self):
        with self.workspace_tempdir() as tmp:
            plan_path = self.write_plan(tmp / "pilot.json")

            result = bigdata_pilot(plan_path, dry_run=True, created_at=CREATED_AT)

            self.assertTrue(result["ok"])
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["manifest"]["stage_status"], "dry_run")
            self.assertFalse((tmp / "reports").exists())

    def test_prerequisites_stage_checks_sources_configs_build_context_and_docker(self):
        with self.workspace_tempdir() as tmp:
            source = tmp / "source"
            docs = source / "docs"
            docs.mkdir(parents=True)
            (docs / "index.md").write_text("# Hello\n", encoding="utf-8")
            for name, snapshot_id in (("before_source.json", "before"), ("after_source.json", "after")):
                (tmp / name).write_text(
                    json.dumps(
                        {
                            "schema_version": "retrieval_arena.corpus_source_descriptor.v1",
                            "corpus_id": "toy_docs",
                            "snapshot_id": snapshot_id,
                            "source_type": "local",
                            "source_path": str(source),
                            "docs_root": "docs",
                            "destination_workspace": str(tmp / "raw"),
                            "include": ["*.md"],
                            "exclude": [],
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            (tmp / "comparison.json").write_text(
                json.dumps(
                    {
                        "schema_version": "retrieval_arena.corpus_snapshot_comparison_plan.v1",
                        "comparison_id": "toy_pair",
                        "corpus_id": "toy_docs",
                        "before_descriptor": "before_source.json",
                        "after_descriptor": "after_source.json",
                        "output_dir": "reports/snapshot_comparison",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            build_context = tmp / "build_context"
            build_context.mkdir()
            retrieval_yaml = "\n".join(
                [
                    "experiment_name: toy",
                    "datasets:",
                    "  - name: toy_docs",
                    "    path: dataset",
                    "tests:",
                    "  - name: rag_lexical_topk",
                    "    image: toy:latest",
                    "    build_context: build_context",
                    "    config:",
                    "      top_k: 1",
                    "scoring:",
                    "  method: lexical_baseline",
                    "output_dir: results",
                    "",
                ]
            )
            (tmp / "retrieval_before.yaml").write_text(retrieval_yaml, encoding="utf-8")
            (tmp / "retrieval_after.yaml").write_text(retrieval_yaml, encoding="utf-8")
            plan_path = self.write_plan(tmp / "pilot.json", stages=["prerequisites", "snapshot_compare"])

            with patch("retrieval_arena.bigdata_pilot._docker_server_version", return_value="27.5.1"):
                result = bigdata_pilot(plan_path, stage="prerequisites", created_at=CREATED_AT)

            self.assertTrue(result["ok"])
            self.assertEqual(result["manifest"]["stage_status"], "completed")
            checks = result["manifest"]["stages"][0]["checks"]
            self.assertIn("docker_engine", {item["name"] for item in checks})
            self.assertIn("local_source_before", {item["name"] for item in checks})

    def test_stage_and_from_stage_resolution(self):
        stages = ["snapshot_compare", "retrieval_before", "retrieval_after", "drift", "audit"]

        self.assertEqual(resolve_stage_list(stages, stage="drift"), ["snapshot_compare", "retrieval_before", "retrieval_after", "drift"])
        self.assertEqual(resolve_stage_list(stages, from_stage="drift"), ["drift", "audit"])
        with self.assertRaisesRegex(ValidationError, "cannot be used together"):
            resolve_stage_list(stages, stage="drift", from_stage="audit")

    def test_run_selection_from_experiment_manifest(self):
        with self.workspace_tempdir() as tmp:
            manifest = self.write_experiment_manifest(tmp, [{"dataset": "toy_docs", "test": "rag_lexical_topk", "run_id": "a"}])

            selected = select_run_from_experiment_manifest(manifest, {"dataset": "toy_docs", "test": "rag_lexical_topk"})

            self.assertEqual(selected, tmp / "runs" / "toy_docs__rag_lexical_topk__a")

    def test_run_selection_no_match_and_ambiguous_match_fail(self):
        with self.workspace_tempdir() as tmp:
            manifest = self.write_experiment_manifest(
                tmp,
                [
                    {"dataset": "toy_docs", "test": "rag_lexical_topk", "run_id": "a"},
                    {"dataset": "toy_docs", "test": "rag_lexical_topk", "run_id": "b"},
                ],
            )

            with self.assertRaisesRegex(ValidationError, "No run matched"):
                select_run_from_experiment_manifest(manifest, {"dataset": "other", "test": "rag_lexical_topk"})
            with self.assertRaisesRegex(ValidationError, "Ambiguous"):
                select_run_from_experiment_manifest(manifest, {"dataset": "toy_docs", "test": "rag_lexical_topk"})
            self.assertEqual(
                select_run_from_experiment_manifest(manifest, {"dataset": "toy_docs", "test": "rag_lexical_topk", "run_id": "b"}),
                tmp / "runs" / "toy_docs__rag_lexical_topk__b",
            )

    def test_selected_retrieval_config_runs_only_requested_tests(self):
        with self.workspace_tempdir() as tmp:
            retrieval_yaml = "\n".join(
                [
                    "experiment_name: toy",
                    "datasets:",
                    "  - name: toy_docs",
                    "    path: dataset",
                    "tests:",
                    "  - name: rag_lexical_topk",
                    "    image: lexical:latest",
                    "    config:",
                    "      top_k: 1",
                    "  - name: rag_graph_rerank",
                    "    image: graph:latest",
                    "    config:",
                    "      top_k: 1",
                    "scoring:",
                    "  method: lexical_baseline",
                    "output_dir: results",
                    "",
                ]
            )
            config_path = tmp / "retrieval.yaml"
            config_path.write_text(retrieval_yaml, encoding="utf-8")
            seen: list[list[str]] = []

            def fake_run_experiment(config):
                seen.append([test.name for test in config.tests])
                return []

            with patch("retrieval_arena.bigdata_pilot.run_experiment", side_effect=fake_run_experiment):
                _run_selected_retrieval_config(config_path, ["rag_graph_rerank"])

            self.assertEqual(seen, [["rag_graph_rerank"]])

    def test_selected_retrieval_config_rejects_missing_selected_test(self):
        with self.workspace_tempdir() as tmp:
            config_path = tmp / "retrieval.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "experiment_name: toy",
                        "datasets:",
                        "  - name: toy_docs",
                        "    path: dataset",
                        "tests:",
                        "  - name: rag_lexical_topk",
                        "    image: lexical:latest",
                        "    config:",
                        "      top_k: 1",
                        "scoring:",
                        "  method: lexical_baseline",
                        "output_dir: results",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValidationError, "missing selected tests"):
                _run_selected_retrieval_config(config_path, ["missing_test"])

    def test_baseline_bundle_excludes_raw_directories(self):
        with self.workspace_tempdir() as tmp:
            output_root = tmp / "reports"
            (output_root / "raw").mkdir(parents=True)
            (output_root / "raw" / "source.md").write_text("third-party docs\n", encoding="utf-8")
            (output_root / "snapshot_comparison" / "before" / "dataset" / "corpus").mkdir(parents=True)
            (output_root / "snapshot_comparison" / "before" / "dataset" / "corpus" / "copied.md").write_text("third-party docs\n", encoding="utf-8")
            (output_root / "snapshot_comparison" / "before" / "dataset" / "questions.jsonl").write_text('{"question_id":"q1","question":"Q?"}\n', encoding="utf-8")
            (output_root / "snapshot_comparison" / "before" / "dataset" / "answers.jsonl").write_text('{"question_id":"q1","answer":"A"}\n', encoding="utf-8")
            (output_root / "comparisons" / "c1").mkdir(parents=True)
            (output_root / "comparisons" / "c1" / "retrieval_drift_summary.md").write_text("# Drift\n", encoding="utf-8")
            run_dir = tmp / "run"
            run_dir.mkdir()
            (run_dir / "retrieval_replay_manifest.json").write_text("{}\n", encoding="utf-8")
            (run_dir / "scores.json").write_text("{}\n", encoding="utf-8")
            (run_dir / "predictions.jsonl").write_text('{"generated_answer":"third-party docs"}\n', encoding="utf-8")

            bundle = assemble_baseline_bundle(
                output_root,
                tmp / "calibration",
                pilot_id="toy_pilot",
                comparisons=[{"comparison_id": "c1", "before_run_dir": run_dir}],
            )

            self.assertFalse((tmp / "calibration" / "raw").exists())
            self.assertFalse((tmp / "calibration" / "snapshot_comparison" / "before" / "dataset" / "corpus").exists())
            self.assertFalse((tmp / "calibration" / "snapshot_comparison" / "before" / "dataset" / "questions.jsonl").exists())
            self.assertFalse((tmp / "calibration" / "snapshot_comparison" / "before" / "dataset" / "answers.jsonl").exists())
            self.assertTrue((tmp / "calibration" / "comparisons" / "c1" / "retrieval_drift_summary.md").exists())
            self.assertTrue((tmp / "calibration" / "retrieval" / "c1" / "before" / "retrieval_replay_manifest.json").exists())
            self.assertFalse((tmp / "calibration" / "retrieval" / "c1" / "before" / "predictions.jsonl").exists())
            self.assertFalse(bundle["include_raw"])

    def test_cli_smoke_for_dry_run(self):
        with self.workspace_tempdir() as tmp:
            plan_path = self.write_plan(tmp / "pilot.json")

            exit_code = main(["study", "bigdata", "pilot", "--plan", str(plan_path), "--dry-run"])

            self.assertEqual(exit_code, 0)
            self.assertFalse((tmp / "reports").exists())

    def write_experiment_manifest(self, tmp: Path, rows: list[dict[str, str]]) -> Path:
        run_entries = []
        for row in rows:
            run_dir = tmp / "runs" / f"{row['dataset']}__{row['test']}__{row['run_id']}"
            run_dir.mkdir(parents=True)
            run_manifest = run_dir / "retrieval_replay_manifest.json"
            write_manifest(
                run_manifest,
                {
                    "schema_version": "retrieval_arena.replay_manifest.v1",
                    "created_at": CREATED_AT,
                    "manifest_type": "retrieval_replay",
                    "run_id": row["run_id"],
                    "experiment_name": "toy",
                    "dataset": row["dataset"],
                    "test": row["test"],
                    "query_set_id": "toy-queries",
                },
            )
            run_entries.append({**row, "path": run_manifest.relative_to(tmp).as_posix(), "manifest_hash": "0" * 64, "query_set_id": "toy-queries"})
        manifest_path = tmp / "experiment_manifest.json"
        write_manifest(
            manifest_path,
            {
                "schema_version": "retrieval_arena.experiment_manifest.v1",
                "created_at": CREATED_AT,
                "manifest_type": "experiment",
                "experiment_name": "toy",
                "run_manifest_count": len(run_entries),
                "run_manifests": run_entries,
            },
        )
        return manifest_path


if __name__ == "__main__":
    unittest.main()
