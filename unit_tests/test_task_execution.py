from __future__ import annotations

import contextlib
import copy
import hashlib
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
    _validate_provider_observation,
    build_execution_plan,
    build_synthetic_restart_fixture,
    builtin_synthetic_implementations,
    execute_task,
    publish_task_execution,
    replay_task_execution,
    semantic_event_projection,
    validate_execution_bundle,
    validate_execution_failure,
    validate_execution_request,
    validate_replay_verification,
    write_canonical_json,
)


ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / ".tmp_unit_tests"
ADVERSARIAL_PROVIDER = ROOT / "unit_tests" / "fixtures" / "adversarial_task_provider.py"
ADVERSARIAL_ENVIRONMENT = ROOT / "unit_tests" / "fixtures" / "adversarial_environment_provider.py"
ADVERSARIAL_DOWNSTREAM = ROOT / "unit_tests" / "fixtures" / "adversarial_downstream_provider.py"


def execute_fixture(*, scenario: str = "success", native_variant: str = "command_a") -> tuple[dict, dict]:
    fixture = build_synthetic_restart_fixture(
        snapshot_id=f"{scenario}-{native_variant}",
        scenario=scenario,
        native_variant=native_variant,
    )
    bundle = execute_task(**fixture, implementations=builtin_synthetic_implementations())
    return fixture, bundle


def failure_fixture(probe: str) -> tuple[dict, dict]:
    implementations = builtin_synthetic_implementations()
    implementations["retrieval_arena.synthetic.restart_agent"] = ProviderImplementation(
        ADVERSARIAL_PROVIDER
    )
    fixture = build_synthetic_restart_fixture(implementations=implementations)
    fixture["request"]["provider_parameters"]["probe"] = probe
    fixture["request"]["request_sha256"] = self_hash(
        fixture["request"], "request_sha256"
    )
    return fixture, execute_task(**fixture, implementations=implementations)


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
        self.assertEqual(len(bundle["native_receipts"]), 5)
        self.assertTrue(all(row["status"] == "passed" for row in bundle["native_receipts"]))
        self.assertEqual(bundle["native_receipts"][2]["operation_id"], "cleanup_fixture")
        self.assertEqual(bundle["replay_receipt"]["status"], "passed")
        self.assertEqual(bundle["lifecycle"][7]["disposition"], "completed")
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
                self.assertEqual(len(bundle["native_receipts"]), 5)
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
        implementations["retrieval_arena.synthetic.restart_agent"] = ProviderImplementation(
            ADVERSARIAL_PROVIDER
        )
        with self.assertRaisesRegex(ValidationError, "artifact drift"):
            execute_task(**fixture, implementations=implementations)

    def test_provider_identity_is_derived_from_exact_source_bytes(self):
        implementations = builtin_synthetic_implementations()
        fixture = build_synthetic_restart_fixture(implementations=implementations)
        agent = next(
            row for row in fixture["registry"]["providers"]
            if row["provider_kind"] == "agent_adapter"
        )
        import hashlib
        expected = hashlib.sha256(
            implementations[agent["entrypoint_id"]].artifact_path.read_bytes()
        ).hexdigest()
        self.assertEqual(agent["implementation_sha256"], expected)
        self.assertEqual(set(ProviderImplementation.__dataclass_fields__), {"artifact_path"})

    def test_undeclared_filesystem_process_and_network_are_terminally_receipted(self):
        expected_capability = {
            "filesystem": "filesystem_read",
            "process": "process",
            "network": "network",
        }
        for probe, capability in expected_capability.items():
            with self.subTest(probe=probe):
                fixture, failure = failure_fixture(probe)
                self.assertEqual(failure["status"], "rejected")
                self.assertEqual(failure["failed_phase"], "execute")
                self.assertIn("provider_capability_violation", failure["reason_codes"])
                self.assertEqual(failure["native_receipts"][0]["status"], "failed")
                self.assertEqual(
                    failure["native_receipts"][0]["capabilities_used"], [capability]
                )
                cleanup = failure["native_receipts"][-1]
                self.assertEqual(cleanup["operation_id"], "cleanup_fixture")
                self.assertEqual(cleanup["status"], "passed")
                self.assertTrue(failure["cleanup"]["passed"])
                self.assertEqual(failure["lifecycle"][2]["disposition"], "failed")
                self.assertEqual(failure["lifecycle"][-1]["disposition"], "completed")
                self.assertEqual(
                    failure["efficiency"]["source_receipt_sha256s"],
                    sorted(row["receipt_sha256"] for row in failure["native_receipts"]),
                )
                self.assertEqual(failure["retention_policy"], fixture["request"]["retention_policy"])

    def test_provider_exception_and_malformed_output_retain_failure_and_cleanup(self):
        for probe, error_class in (("exception", "ValueError"), ("malformed", "ValidationError")):
            with self.subTest(probe=probe):
                _, failure = failure_fixture(probe)
                failed = failure["native_receipts"][0]
                self.assertEqual(failed["status"], "failed")
                self.assertEqual(failed["failure"]["error_class"], error_class)
                self.assertEqual(failure["native_receipts"][-1]["operation_id"], "cleanup_fixture")
                self.assertTrue(failure["cleanup"]["attempted"])

    def test_declared_filesystem_capability_is_confined_to_pack_roots(self):
        implementations = builtin_synthetic_implementations()
        implementations[
            "retrieval_arena.synthetic.restart_environment"
        ] = ProviderImplementation(ADVERSARIAL_ENVIRONMENT)
        fixture = build_synthetic_restart_fixture(implementations=implementations)
        failure = execute_task(**fixture, implementations=implementations)
        failed = next(row for row in failure["native_receipts"] if row["status"] == "failed")
        self.assertEqual(failed["provider_kind"], "environment_adapter")
        self.assertEqual(failed["capabilities_used"], ["filesystem_read"])
        self.assertEqual(failed["failure"]["reason_code"], "provider_capability_violation")
        self.assertTrue(failure["cleanup"]["passed"])

    def test_normalizer_and_oracle_failures_retain_prior_cleanup(self):
        for entrypoint, phase in (
            ("retrieval_arena.synthetic.restart_normalizer", "observe"),
            ("retrieval_arena.synthetic.restart_oracle", "finalize"),
        ):
            with self.subTest(phase=phase):
                implementations = builtin_synthetic_implementations()
                implementations[entrypoint] = ProviderImplementation(ADVERSARIAL_DOWNSTREAM)
                fixture = build_synthetic_restart_fixture(implementations=implementations)
                failure = execute_task(**fixture, implementations=implementations)
                self.assertEqual(failure["failed_phase"], phase)
                self.assertTrue(failure["cleanup"]["passed"])
                cleanup_index = next(
                    index for index, row in enumerate(failure["native_receipts"])
                    if row["operation_id"] == "cleanup_fixture"
                )
                failed_index = next(
                    index for index, row in enumerate(failure["native_receipts"])
                    if row["status"] == "failed"
                )
                self.assertLess(cleanup_index, failed_index)

    def test_cleanup_provider_failure_is_terminal_and_not_predeclared_complete(self):
        fixture = build_synthetic_restart_fixture(scenario="cleanup_provider_exception")
        failure = execute_task(**fixture, implementations=builtin_synthetic_implementations())
        self.assertEqual(failure["failed_phase"], "cleanup")
        self.assertFalse(failure["cleanup"]["passed"])
        self.assertEqual(failure["native_receipts"][-1]["status"], "failed")
        self.assertEqual(failure["lifecycle"][-1]["disposition"], "failed")
        self.assertIn("cleanup_failed", failure["reason_codes"])

    def test_operation_schema_matrix_terminalizes_every_malformed_object(self):
        matrix = {
            "agent_malformed_object": ("agent_adapter", "execute"),
            "environment_malformed_object": ("environment_adapter", "execute"),
            "cleanup_malformed_object": ("environment_adapter", "cleanup"),
            "normalizer_malformed_object": ("event_normalizer", "observe"),
            "oracle_malformed_object": ("oracle_provider", "finalize"),
        }
        for scenario, (provider_kind, phase) in matrix.items():
            with self.subTest(scenario=scenario):
                _, failure = execute_fixture(scenario=scenario)
                failed = [
                    row for row in failure["native_receipts"]
                    if row["status"] == "failed"
                ]
                self.assertEqual(len(failed), 1)
                self.assertEqual(failed[0]["provider_kind"], provider_kind)
                self.assertEqual(
                    failed[0]["failure"]["reason_code"], "provider_contract_failure"
                )
                self.assertEqual(failure["failed_phase"], phase)

    def test_operation_contract_matrix_rejects_missing_extra_and_wrong_types(self):
        _, bundle = execute_fixture()
        providers = {
            row["provider_kind"]: row for row in bundle["registry"]["providers"]
        }
        wrong_type_fields = {
            "propose_actions": "actions",
            "execute_fixture": "native_command",
            "cleanup_fixture": "cleanup_passed",
            "normalize_observations": "normalized_events",
            "evaluate_assertions": "verdicts",
        }
        wrong_values = {
            "actions": {},
            "native_command": 1,
            "cleanup_passed": "yes",
            "normalized_events": {},
            "verdicts": [],
        }
        for receipt in bundle["native_receipts"]:
            provider = providers[receipt["provider_kind"]]
            operation = receipt["operation_id"]
            valid = receipt["observation_payload"]
            _validate_provider_observation(
                provider, operation, receipt["request_payload"], valid
            )
            variants = {}
            missing = copy.deepcopy(valid)
            missing.pop(sorted(missing)[0])
            variants["missing"] = missing
            extra = copy.deepcopy(valid)
            extra["undeclared"] = True
            variants["extra"] = extra
            wrong = copy.deepcopy(valid)
            field = wrong_type_fields[operation]
            wrong[field] = wrong_values[field]
            variants["wrong_type"] = wrong
            for shape, changed in variants.items():
                with self.subTest(operation=operation, shape=shape):
                    with self.assertRaises(ValidationError):
                        _validate_provider_observation(
                            provider,
                            operation,
                            receipt["request_payload"],
                            changed,
                        )

    def test_cleanup_failure_lifecycle_marks_downstream_phases_not_reached(self):
        _, failure = execute_fixture(scenario="cleanup_malformed_object")
        dispositions = {
            row["phase"]: row["disposition"] for row in failure["lifecycle"]
        }
        self.assertEqual(dispositions["admit"], "completed")
        self.assertEqual(dispositions["initialize"], "completed")
        self.assertEqual(dispositions["execute"], "completed")
        self.assertEqual(dispositions["cleanup"], "failed")
        self.assertEqual(dispositions["publish"], "pending_externalization")
        for phase in ("observe", "finalize", "score", "replay"):
            self.assertEqual(dispositions[phase], "not_reached")

    def test_failure_lifecycle_matrix_uses_only_observed_phase_transitions(self):
        scenarios = []
        _, agent_failure = failure_fixture("exception")
        scenarios.append(
            (agent_failure, {"admit", "initialize", "cleanup"}, {"execute"})
        )
        for entrypoint, completed, failed in (
            (
                "retrieval_arena.synthetic.restart_normalizer",
                {"admit", "initialize", "execute", "cleanup"},
                {"observe"},
            ),
            (
                "retrieval_arena.synthetic.restart_oracle",
                {"admit", "initialize", "execute", "cleanup", "observe"},
                {"finalize"},
            ),
        ):
            implementations = builtin_synthetic_implementations()
            implementations[entrypoint] = ProviderImplementation(ADVERSARIAL_DOWNSTREAM)
            fixture = build_synthetic_restart_fixture(implementations=implementations)
            failure = execute_task(**fixture, implementations=implementations)
            scenarios.append((failure, completed, failed))
        _, cleanup_failure = execute_fixture(scenario="cleanup_provider_exception")
        scenarios.append(
            (cleanup_failure, {"admit", "initialize", "execute"}, {"cleanup"})
        )
        for failure, completed, failed in scenarios:
            dispositions = {
                row["phase"]: row["disposition"] for row in failure["lifecycle"]
            }
            self.assertEqual(
                {phase for phase, status in dispositions.items() if status == "completed"},
                completed,
            )
            self.assertEqual(
                {phase for phase, status in dispositions.items() if status == "failed"},
                failed,
            )
            for phase in {"observe", "finalize", "score", "replay"} - completed - failed:
                self.assertEqual(dispositions[phase], "not_reached")
            self.assertEqual(dispositions["publish"], "pending_externalization")

    def test_terminal_failure_rejects_cleanup_lifecycle_or_evidence_rewrite(self):
        _, failure = failure_fixture("exception")
        changed = copy.deepcopy(failure)
        changed["lifecycle"][-1]["disposition"] = "failed"
        changed["failure_sha256"] = self_hash(changed, "failure_sha256")
        with self.assertRaisesRegex(ValidationError, "lifecycle mismatch"):
            validate_execution_failure(changed)

        changed = copy.deepcopy(failure)
        changed["cleanup"]["receipt_sha256"] = "0" * 64
        changed["failure_sha256"] = self_hash(changed, "failure_sha256")
        with self.assertRaisesRegex(ValidationError, "cleanup receipt is missing"):
            validate_execution_failure(changed)

        changed = copy.deepcopy(failure)
        changed["reason_codes"] = ["invented-reason"]
        changed["failure_sha256"] = self_hash(changed, "failure_sha256")
        with self.assertRaisesRegex(ValidationError, "reasons differ from receipts"):
            validate_execution_failure(changed)

        changed = copy.deepcopy(failure)
        changed["efficiency"]["measured_resources"][0]["value"] += 1
        changed["efficiency"]["efficiency_sha256"] = self_hash(
            changed["efficiency"], "efficiency_sha256"
        )
        changed["failure_sha256"] = self_hash(changed, "failure_sha256")
        with self.assertRaisesRegex(ValidationError, "efficiency differs from receipts"):
            validate_execution_failure(changed)

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

    def test_rehashed_oracle_mutation_is_rejected_before_replay(self):
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
        with self.assertRaisesRegex(ValidationError, "replay receipt oracle results mismatch"):
            validate_execution_bundle(changed)

    def test_replay_is_byte_stable_and_invokes_only_the_oracle(self):
        _, bundle = execute_fixture()
        implementations = builtin_synthetic_implementations()
        first = replay_task_execution(bundle, implementations)
        second = replay_task_execution(bundle, implementations)
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["oracle_replay_receipt"]["provider_kind"], "oracle_provider")
        self.assertEqual(first["oracle_replay_receipt"]["status"], "passed")
        self.assertEqual(first["environment_invocations"], 0)
        self.assertEqual(first["service_executions"], 0)

    def test_rehashed_replay_receipt_cannot_claim_a_failed_or_non_oracle_call(self):
        _, bundle = execute_fixture()
        verification = replay_task_execution(bundle, builtin_synthetic_implementations())
        changed = copy.deepcopy(verification)
        changed["oracle_replay_receipt"]["provider_kind"] = "environment_adapter"
        changed["oracle_replay_receipt"]["receipt_sha256"] = self_hash(
            changed["oracle_replay_receipt"], "receipt_sha256"
        )
        changed["oracle_replay_receipt_sha256"] = changed["oracle_replay_receipt"]["receipt_sha256"]
        changed["verification_sha256"] = self_hash(changed, "verification_sha256")
        with self.assertRaisesRegex(ValidationError, "not a passed oracle invocation"):
            validate_replay_verification(changed, bundle)

    def test_rehashed_replay_payload_mutation_is_bound_to_retained_evidence(self):
        _, bundle = execute_fixture()
        for payload_name, hash_name in (
            ("request_payload", "request_sha256"),
            ("observation_payload", "observation_sha256"),
        ):
            with self.subTest(record="bundle", payload=payload_name):
                changed = copy.deepcopy(bundle)
                receipt = changed["replay_receipt"]
                receipt[payload_name]["mutation"] = True
                receipt[hash_name] = hashlib.sha256(
                    canonical_json(receipt[payload_name])
                ).hexdigest()
                receipt["receipt_sha256"] = self_hash(receipt, "receipt_sha256")
                changed["bundle_sha256"] = self_hash(changed, "bundle_sha256")
                with self.assertRaisesRegex(ValidationError, "replay receipt .* mismatch"):
                    validate_execution_bundle(changed)

        verification = replay_task_execution(bundle, builtin_synthetic_implementations())
        for payload_name, hash_name in (
            ("request_payload", "request_sha256"),
            ("observation_payload", "observation_sha256"),
        ):
            with self.subTest(record="verification", payload=payload_name):
                changed = copy.deepcopy(verification)
                receipt = changed["oracle_replay_receipt"]
                receipt[payload_name]["mutation"] = True
                receipt[hash_name] = hashlib.sha256(
                    canonical_json(receipt[payload_name])
                ).hexdigest()
                receipt["receipt_sha256"] = self_hash(receipt, "receipt_sha256")
                changed["oracle_replay_receipt_sha256"] = receipt["receipt_sha256"]
                changed["verification_sha256"] = self_hash(
                    changed, "verification_sha256"
                )
                with self.assertRaisesRegex(ValidationError, "replay receipt .* mismatch"):
                    validate_replay_verification(changed, bundle)

        changed = copy.deepcopy(verification)
        changed["oracle_provider_manifest_sha256"] = "0" * 64
        changed["verification_sha256"] = self_hash(changed, "verification_sha256")
        with self.assertRaisesRegex(ValidationError, "not a passed oracle invocation"):
            validate_replay_verification(changed, bundle)

    def test_publication_is_compact_content_addressed_and_secret_free(self):
        _, bundle = execute_fixture()
        SCRATCH.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as root_text:
            output = Path(root_text) / "published"
            summary = publish_task_execution(output, bundle)
            self.assertEqual(summary["terminal_record_sha256"], bundle["bundle_sha256"])
            self.assertEqual(len(summary["detailed_artifacts"]), 7)
            self.assertNotIn("request_payload", json.dumps(summary))
            self.assertNotIn("secret", json.dumps(summary).lower())
            for row in summary["detailed_artifacts"]:
                data = (output / row["path"]).read_bytes()
                import hashlib
                self.assertEqual(hashlib.sha256(data).hexdigest(), row["sha256"])

    def test_terminal_provider_failure_is_self_contained_and_publishable(self):
        _, failure = failure_fixture("exception")
        SCRATCH.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as root_text:
            output = Path(root_text) / "failure"
            summary = publish_task_execution(output, failure)
            self.assertEqual(summary["record_kind"], "terminal_failure")
            self.assertEqual(summary["terminal_record_sha256"], failure["failure_sha256"])
            self.assertTrue((output / "execution_failure.v1.json").is_file())
            self.assertTrue((output / "native_receipts.v2.json").is_file())
            retained = json.loads((output / "execution_failure.v1.json").read_text(encoding="utf-8"))
            self.assertEqual(retained["failure_sha256"], failure["failure_sha256"])

    def test_cli_publishes_provider_failure_instead_of_losing_it(self):
        fixture = build_synthetic_restart_fixture(scenario="provider_exception")
        SCRATCH.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=SCRATCH) as root_text:
            root = Path(root_text)
            paths = {}
            for name, value in fixture.items():
                paths[name] = root / f"{name}.json"
                write_canonical_json(paths[name], value)
            command = [
                "task", "execute-synthetic",
                *sum(
                    ([f"--{name}", str(paths[name])] for name in ("pack", "task", "trial", "registry", "request")),
                    [],
                ),
                "--out-dir", str(root / "failure-output"),
            ]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(command), 0)
            published = root / "failure-output"
            self.assertTrue((published / "summary.v2.json").is_file())
            failure_path = published / "execution_failure.v1.json"
            self.assertTrue(failure_path.is_file())
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    cli.main(
                        [
                            "task", "report", "--bundle", str(failure_path),
                            "--out-dir", str(root / "failure-report"),
                        ]
                    ),
                    0,
                )
            self.assertTrue((root / "failure-report" / "task_report_record.v2.json").is_file())

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
            self.assertTrue((root / "execution" / "summary.v2.json").is_file())
            self.assertTrue((root / "score.json").is_file())
            self.assertTrue((root / "comparison.json").is_file())
            self.assertTrue((root / "report" / "task_report_record.v2.json").is_file())
            self.assertTrue((root / "replay.json").is_file())

    def test_execution_core_has_no_dynamic_loading_network_or_subprocess_surface(self):
        source = (ROOT / "retrieval_arena" / "task_execution.py").read_text(encoding="utf-8")
        provider = (ROOT / "retrieval_arena" / "synthetic_task_providers.py").read_text(encoding="utf-8")
        for forbidden in ("import importlib", "import subprocess", "import requests", "docspectra"):
            self.assertNotIn(forbidden, source.lower())
        self.assertNotIn("import ", provider.lower())
        self.assertIn('"open_socket": socket.socket', source)
        self.assertIn('"system_call": os.system', source)


if __name__ == "__main__":
    unittest.main()
