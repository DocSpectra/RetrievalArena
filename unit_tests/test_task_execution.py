from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from retrieval_arena import cli
from retrieval_arena.errors import ValidationError
from retrieval_arena.task_completion import (
    build_task_replay,
    canonical_json,
    score_task,
    self_hash,
)
from retrieval_arena.task_execution import (
    EVENT_CLASS_INVENTORY,
    EXECUTION_PHASES,
    ProviderImplementation,
    build_execution_plan,
    build_synthetic_restart_fixture,
    builtin_synthetic_implementations,
    execute_task,
    publish_task_execution,
    replay_task_execution,
    semantic_event_projection,
    validate_execution_bundle,
    validate_execution_request,
    write_canonical_json,
)


ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".tmp_unit_tests"


def execute_fixture(*, scenario: str = "success", native_variant: str = "command_a") -> tuple[dict, dict]:
    fixture = build_synthetic_restart_fixture(
        snapshot_id=f"{scenario}-{native_variant}",
        scenario=scenario,
        native_variant=native_variant,
    )
    bundle = execute_task(**fixture, implementations=builtin_synthetic_implementations())
    return fixture, bundle


class TaskExecutionTests(unittest.TestCase):
    def test_plan_is_non_consuming_write_free_and_covers_full_lifecycle(self):
        fixture = build_synthetic_restart_fixture()
        plan = build_execution_plan(**fixture)
        self.assertEqual(plan["ordered_phases"], EXECUTION_PHASES)
        self.assertEqual(plan["event_class_inventory"], EVENT_CLASS_INVENTORY)
        self.assertEqual(plan["consumption"], "non_consuming")
        self.assertEqual(plan["writes"], 0)
        self.assertEqual(plan["runtime_network"], "forbidden")

    def test_success_retains_harness_owned_receipts_events_oracles_score_and_replay(self):
        _, bundle = execute_fixture()
        self.assertEqual(bundle["status"], "accepted")
        self.assertTrue(bundle["score"]["task_success"])
        self.assertEqual(
            [row["phase"] for row in bundle["lifecycle"]], EXECUTION_PHASES
        )
        self.assertEqual(bundle["lifecycle"][6]["disposition"], "pending_externalization")
        self.assertEqual(
            {row["event_class"] for row in bundle["events"]},
            set(EVENT_CLASS_INVENTORY),
        )
        self.assertEqual(len(bundle["native_receipts"]), 4)
        self.assertEqual(len(bundle["oracle_results"]), 9)
        self.assertEqual(
            bundle["replay"]["task_score_sha256"], bundle["score"]["score_sha256"]
        )
        self.assertNotIn("agent_success_claim", canonical_json(bundle["score"]).decode())

    def test_equivalent_native_commands_normalize_to_identical_semantics(self):
        _, first = execute_fixture(native_variant="command_a")
        _, second = execute_fixture(native_variant="command_b")
        self.assertEqual(
            semantic_event_projection(first["events"]),
            semantic_event_projection(second["events"]),
        )
        environment_a = next(
            row for row in first["native_receipts"] if row["provider_kind"] == "environment_adapter"
        )
        environment_b = next(
            row for row in second["native_receipts"] if row["provider_kind"] == "environment_adapter"
        )
        self.assertNotEqual(environment_a["observation_sha256"], environment_b["observation_sha256"])

    def test_adversarial_scenarios_fail_their_oracle_or_cleanup_gates(self):
        expected = {
            "skipped_restart": {"required_oracle_failed"},
            "stale_response": {"required_oracle_failed"},
            "request_before_restart": {"required_oracle_failed"},
            "mock_substitution": {"critical_safety_violation", "required_oracle_failed"},
            "cleanup_failure": {"cleanup_failed", "required_oracle_failed"},
        }
        for scenario, reasons in expected.items():
            with self.subTest(scenario=scenario):
                _, bundle = execute_fixture(scenario=scenario)
                self.assertEqual(bundle["status"], "failed")
                self.assertFalse(bundle["score"]["task_success"])
                self.assertTrue(reasons <= set(bundle["reason_codes"]))
                self.assertEqual(len(bundle["native_receipts"]), 4)
                self.assertTrue(bundle["efficiency"]["measured_resources"])
                self.assertEqual(
                    bundle["retention_policy"]["failed_run_resources"], "retain"
                )

    def test_optional_token_classes_remain_unavailable_without_a_composite(self):
        _, bundle = execute_fixture()
        efficiency = bundle["efficiency"]
        self.assertIsNone(efficiency["cached_input_tokens"])
        self.assertIsNone(efficiency["reasoning_tokens"])
        self.assertEqual(efficiency["token_accounting"]["cached_input_tokens"], "unavailable")
        self.assertEqual(efficiency["token_accounting"]["reasoning_tokens"], "unavailable")
        self.assertIsNone(bundle["pack"]["aggregation_policy"]["composite_metric"])

    def test_provider_policy_and_implementation_drift_are_rejected_before_execution(self):
        fixture = build_synthetic_restart_fixture()
        changed = copy.deepcopy(fixture)
        changed["registry"]["runtime_network"] = "allowed"
        changed["registry"]["registry_sha256"] = self_hash(
            changed["registry"], "registry_sha256"
        )
        changed["request"]["registry_sha256"] = changed["registry"]["registry_sha256"]
        changed["request"]["request_sha256"] = self_hash(
            changed["request"], "request_sha256"
        )
        with self.assertRaisesRegex(ValidationError, "forbid network"):
            validate_execution_request(**changed)

        implementations = builtin_synthetic_implementations()
        key = next(iter(implementations))
        original = implementations[key]
        implementations[key] = ProviderImplementation(
            original.entrypoint_id, "0" * 64, original.invoke
        )
        with self.assertRaisesRegex(ValidationError, "artifact drift"):
            execute_task(**fixture, implementations=implementations)

    def test_receipt_and_score_mutations_are_rejected_even_when_rehashed(self):
        _, bundle = execute_fixture()
        receipt_mutation = copy.deepcopy(bundle)
        receipt = receipt_mutation["native_receipts"][0]
        receipt["finished_at_ns"] += 1
        receipt["receipt_sha256"] = self_hash(receipt, "receipt_sha256")
        receipt_mutation["bundle_sha256"] = self_hash(
            receipt_mutation, "bundle_sha256"
        )
        with self.assertRaises(ValidationError):
            validate_execution_bundle(receipt_mutation)

        score_mutation = copy.deepcopy(bundle)
        score_mutation["score"]["task_success"] = False
        score_mutation["score"]["score_sha256"] = self_hash(
            score_mutation["score"], "score_sha256"
        )
        score_mutation["bundle_sha256"] = self_hash(score_mutation, "bundle_sha256")
        with self.assertRaisesRegex(ValidationError, "score differs from retained evidence"):
            validate_execution_bundle(score_mutation)

    def test_rehashed_oracle_mutation_is_rejected_by_deterministic_replay(self):
        _, bundle = execute_fixture()
        changed = copy.deepcopy(bundle)
        result = next(
            row for row in changed["oracle_results"] if row["assertion_id"] == "a7-no-mock"
        )
        result["verdict"] = "fail"
        result["violation_id"] = "violation:a7-no-mock"
        result["observed_value_sha256"] = "f" * 64
        result["result_sha256"] = self_hash(result, "result_sha256")
        changed["score"] = score_task(
            changed["pack"],
            changed["task"],
            changed["trial"],
            changed["oracle_results"],
            changed["efficiency"],
        )
        changed["replay"] = build_task_replay(
            changed["pack"],
            changed["task"],
            changed["trial"],
            changed["events"],
            changed["oracle_results"],
            changed["score"],
        )
        changed["status"] = "failed"
        changed["reason_codes"] = ["critical_safety_violation", "required_oracle_failed"]
        changed["bundle_sha256"] = self_hash(changed, "bundle_sha256")
        validate_execution_bundle(changed)
        with self.assertRaisesRegex(ValidationError, "deterministic replay"):
            replay_task_execution(changed, builtin_synthetic_implementations())

    def test_replay_is_byte_stable_and_invokes_only_the_oracle(self):
        _, bundle = execute_fixture()
        counters: dict[str, int] = {}
        implementations = builtin_synthetic_implementations()
        wrapped: dict[str, ProviderImplementation] = {}
        for entrypoint, implementation in implementations.items():
            def invoke(payload: dict, *, key: str = entrypoint, target=implementation.invoke) -> dict:
                counters[key] = counters.get(key, 0) + 1
                return target(payload)

            wrapped[entrypoint] = ProviderImplementation(
                entrypoint, implementation.artifact_sha256, invoke
            )
        first = replay_task_execution(bundle, wrapped)
        second = replay_task_execution(bundle, wrapped)
        oracle_entrypoint = next(
            row["entrypoint_id"]
            for row in bundle["registry"]["providers"]
            if row["provider_kind"] == "oracle_provider"
        )
        self.assertEqual(counters, {oracle_entrypoint: 2})
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["environment_invocations"], 0)
        self.assertEqual(first["service_executions"], 0)

    def test_publication_is_compact_content_addressed_and_secret_free(self):
        _, bundle = execute_fixture()
        SCRATCH.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as root_text:
            output = Path(root_text) / "published"
            summary = publish_task_execution(output, bundle)
            self.assertEqual(summary["bundle_sha256"], bundle["bundle_sha256"])
            self.assertEqual(len(summary["detailed_artifacts"]), 7)
            self.assertNotIn("request_payload", json.dumps(summary))
            self.assertNotIn("secret", json.dumps(summary).lower())
            for row in summary["detailed_artifacts"]:
                data = (output / row["path"]).read_bytes()
                import hashlib
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])

    def test_cli_supports_validate_plan_execute_score_compare_report_and_replay(self):
        before = build_synthetic_restart_fixture(snapshot_id="before")
        after = build_synthetic_restart_fixture(snapshot_id="after")
        before_bundle = execute_task(
            **before, implementations=builtin_synthetic_implementations()
        )
        after_bundle = execute_task(
            **after, implementations=builtin_synthetic_implementations()
        )
        SCRATCH.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as root_text:
            root = Path(root_text)
            paths: dict[str, Path] = {}
            for name, value in before.items():
                paths[name] = root / f"{name}.json"
                write_canonical_json(paths[name], value)
            for name, value in (
                ("oracles", before_bundle["oracle_results"]),
                ("efficiency", before_bundle["efficiency"]),
                ("bundle", before_bundle),
                ("before_score", before_bundle["score"]),
                ("after_trial", after_bundle["trial"]),
                ("after_score", after_bundle["score"]),
            ):
                paths[name] = root / f"{name}.json"
                write_canonical_json(paths[name], value)

            commands = [
                ["task", "validate-pack", "--pack", str(paths["pack"]), "--task", str(paths["task"])],
                ["task", "plan", *sum(([f"--{name}", str(paths[name])] for name in ("pack", "task", "trial", "registry", "request")), [])],
                ["task", "execute-synthetic", *sum(([f"--{name}", str(paths[name])] for name in ("pack", "task", "trial", "registry", "request")), []), "--out-dir", str(root / "execution")],
                ["task", "score", "--pack", str(paths["pack"]), "--task", str(paths["task"]), "--trial", str(paths["trial"]), "--oracles", str(paths["oracles"]), "--efficiency", str(paths["efficiency"]), "--out", str(root / "score.json")],
                ["task", "compare", "--pair-id", "synthetic-pair", "--before-trial", str(paths["trial"]), "--before-score", str(paths["before_score"]), "--after-trial", str(paths["after_trial"]), "--after-score", str(paths["after_score"]), "--out", str(root / "comparison.json")],
                ["task", "report", "--bundle", str(paths["bundle"]), "--out-dir", str(root / "report")],
                ["task", "replay", "--bundle", str(paths["bundle"]), "--out", str(root / "replay.json")],
            ]
            for command in commands:
                with self.subTest(command=command[:2]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(cli.main(command), 0)
            self.assertTrue((root / "execution" / "summary.v1.json").is_file())
            self.assertTrue((root / "score.json").is_file())
            self.assertTrue((root / "comparison.json").is_file())
            self.assertTrue((root / "report" / "task_report_record.v1.json").is_file())
            self.assertTrue((root / "replay.json").is_file())

    def test_execution_core_has_no_dynamic_loading_network_or_subprocess_surface(self):
        source = (ROOT / "retrieval_arena" / "task_execution.py").read_text(encoding="utf-8")
        for forbidden in ("import importlib", "import subprocess", "import socket", "import requests", "docspectra"):
            self.assertNotIn(forbidden, source.lower())


if __name__ == "__main__":
    unittest.main()
