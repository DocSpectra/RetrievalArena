from __future__ import annotations

import copy
import hashlib
import unittest
from pathlib import Path

from retrieval_arena.errors import ValidationError
from retrieval_arena.task_completion import (
    SCHEMA_EFFICIENCY,
    SCHEMA_ORACLE_RESULT,
    SCHEMA_PROVIDER_REFERENCE,
    SCHEMA_TASK_DEFINITION,
    SCHEMA_TASK_EVENT,
    SCHEMA_TASK_PACK,
    SCHEMA_TRIAL_IDENTITY,
    build_task_replay,
    canonical_json,
    compare_task_scores,
    load_contract_json,
    score_task,
    self_hash,
    validate_efficiency,
    validate_provider_reference,
    validate_task_definition,
    validate_task_events,
    validate_task_pack,
    validate_task_replay,
    validate_task_score,
    validate_trial_identity,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hashed(value: dict, field: str) -> dict:
    value[field] = self_hash(value, field)
    return value


def provider(provider_id: str, kind: str, capabilities: list[str]) -> dict:
    return hashed(
        {
            "schema_version": SCHEMA_PROVIDER_REFERENCE,
            "provider_id": provider_id,
            "provider_kind": kind,
            "version": "1.0.0",
            "artifact_sha256": sha(f"artifact:{provider_id}"),
            "entrypoint_id": f"providers.{provider_id}",
            "capabilities": capabilities,
            "provider_sha256": "0" * 64,
        },
        "provider_sha256",
    )


def assertion(
    assertion_id: str,
    assertion_class: str,
    *,
    required: bool = True,
    critical: bool = False,
    predecessors: list[str] | None = None,
) -> dict:
    return hashed(
        {
            "assertion_id": assertion_id,
            "assertion_class": assertion_class,
            "required": required,
            "critical": critical,
            "predecessor_assertion_ids": sorted(predecessors or []),
            "provider_id": "oracle",
            "parameters_sha256": sha(f"parameters:{assertion_id}"),
            "assertion_sha256": "0" * 64,
        },
        "assertion_sha256",
    )


def task_definition(*, missing_policy: str = "failure") -> dict:
    assertions = [
        assertion("a0-initial", "initial_state"),
        assertion("a1-action", "semantic_action", predecessors=["a0-initial"]),
        assertion("a2-order", "temporal_order", predecessors=["a1-action"]),
        assertion("a3-terminal", "terminal_state", predecessors=["a2-order"]),
        assertion("a4-causal", "causal", predecessors=["a3-terminal"]),
        assertion("a5-critical", "critical_safety", critical=True),
        assertion("a6-prohibition", "prohibition", required=False),
        assertion("a7-cleanup", "cleanup", predecessors=["a4-causal"]),
    ]
    return hashed(
        {
            "schema_version": SCHEMA_TASK_DEFINITION,
            "task_id": "restart-web",
            "goal": "Restart the fixture web service and verify its home page.",
            "fixture_id": "web-fixture",
            "context_input_sha256s": [sha("context")],
            "required_capabilities": ["filesystem_read", "filesystem_write"],
            "writable_root_ids": ["scratch"],
            "oracle_references": ["oracle"],
            "assertions": assertions,
            "missing_evidence_policy": missing_policy,
            "success_policy": {
                "required_oracles": "all",
                "critical_safety_oracles": "all",
                "cleanup": "required",
                "composite_metric": None,
            },
            "cleanup_policy": {"required": True, "restoration_required": True},
            "task_sha256": "0" * 64,
        },
        "task_sha256",
    )


def task_pack(task: dict, *, stochastic: bool = False) -> dict:
    references = [
        provider("agent", "agent_adapter", []),
        provider("environment", "environment_adapter", ["filesystem_read", "filesystem_write"]),
        provider("normalizer", "event_normalizer", []),
        provider("oracle", "oracle_provider", ["filesystem_read"]),
    ]
    references.sort(key=lambda row: (row["provider_kind"], row["provider_id"]))
    capabilities = {name: "forbidden" for name in (
        "container",
        "filesystem_read",
        "filesystem_write",
        "network",
        "privileges",
        "process",
        "secrets",
    )}
    capabilities["filesystem_read"] = "declared"
    capabilities["filesystem_write"] = "declared"
    return hashed(
        {
            "schema_version": SCHEMA_TASK_PACK,
            "pack_id": "tiny-portable-pack",
            "pack_version": "1.0.0",
            "publisher": "retrieval-arena-tests",
            "license": "CC0-1.0",
            "supported_contract_version": SCHEMA_TASK_DEFINITION,
            "task_definition_sha256s": [
                {"task_id": task["task_id"], "task_sha256": task["task_sha256"]}
            ],
            "fixture_identities": [
                {"fixture_id": "web-fixture", "artifact_sha256": sha("fixture")}
            ],
            "provider_references": references,
            "declared_capabilities": capabilities,
            "writable_roots": [
                {"root_id": "scratch", "path": "C:/portable/scratch", "cleanup_required": True}
            ],
            "cleanup_policy": {
                "cleanup_required": True,
                "missing_cleanup_disposition": "failure",
            },
            "documentation_binding_policy": {
                "only_variable": "documentation_snapshot",
                "required_pair_labels": ["before", "after"],
            },
            "budgets": {
                "wall_time_ms": 60_000,
                "active_time_ms": 30_000,
                "input_tokens": None,
                "cached_input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "context_tokens": 4_000,
                "context_items": 20,
                "tool_calls": 20,
                "retries": 0,
            },
            "aggregation_policy": {"task_weighting": "macro_equal", "composite_metric": None},
            "replication_policy": {
                "replicate_count": 2 if stochastic else 1,
                "stochastic_execution": stochastic,
                "seed_policy": "required" if stochastic else "unsupported",
            },
            "pack_sha256": "0" * 64,
        },
        "pack_sha256",
    )


def rebind_pack(pack: dict, task: dict) -> dict:
    pack["task_definition_sha256s"] = [
        {"task_id": task["task_id"], "task_sha256": task["task_sha256"]}
    ]
    pack["pack_sha256"] = self_hash(pack, "pack_sha256")
    return pack


def trial_identity(pack: dict, task: dict, snapshot: str, *, trial_id: str | None = None) -> dict:
    by_kind: dict[str, list[str]] = {
        "agent_adapter": [],
        "environment_adapter": [],
        "event_normalizer": [],
        "oracle_provider": [],
    }
    for row in pack["provider_references"]:
        by_kind[row["provider_kind"]].append(row["provider_sha256"])
    stochastic = pack["replication_policy"]["stochastic_execution"]
    return hashed(
        {
            "schema_version": SCHEMA_TRIAL_IDENTITY,
            "trial_id": trial_id or f"trial-{snapshot}",
            "pack_id": pack["pack_id"],
            "pack_sha256": pack["pack_sha256"],
            "task_id": task["task_id"],
            "task_sha256": task["task_sha256"],
            "fixture_id": task["fixture_id"],
            "fixture_sha256": sha("fixture"),
            "documentation_snapshot_id": snapshot,
            "documentation_snapshot_sha256": sha(f"snapshot:{snapshot}"),
            "provider_bindings": {
                "agent_adapter": by_kind["agent_adapter"][0],
                "environment_adapter": by_kind["environment_adapter"][0],
                "event_normalizer": by_kind["event_normalizer"][0],
                "oracle_providers": sorted(by_kind["oracle_provider"]),
            },
            "model_identity_sha256": None,
            "runtime_identity_sha256": sha("runtime"),
            "prompt_sha256": sha("prompt"),
            "decoding_policy_sha256": sha("decoding"),
            "tool_policy_sha256": sha("tools"),
            "context_policy_sha256": sha("context-policy"),
            "budget_policy_sha256": sha("budgets"),
            "scoring_policy_sha256": sha("scoring"),
            "replicate_index": 0,
            "seed": 7 if stochastic else None,
            "trial_sha256": "0" * 64,
        },
        "trial_sha256",
    )


def efficiency(trial: dict, *, wall_time: int = 1000) -> dict:
    active_time = max(0, wall_time - 100)
    first_action_time = min(100, wall_time)
    first_verification_time = max(0, wall_time - 200)
    return hashed(
        {
            "schema_version": SCHEMA_EFFICIENCY,
            "trial_id": trial["trial_id"],
            "trial_sha256": trial["trial_sha256"],
            "wall_time_ms": wall_time,
            "active_time_ms": active_time,
            "time_to_first_valid_action_ms": first_action_time,
            "time_to_first_successful_verification_ms": first_verification_time,
            "input_tokens": 100,
            "cached_input_tokens": None,
            "output_tokens": 20,
            "reasoning_tokens": None,
            "context_tokens": 500,
            "context_items": 4,
            "tool_calls": 5,
            "successful_tool_calls": 4,
            "failed_actions": 1,
            "retries": 0,
            "provider_cost": None,
            "provider_cost_currency": None,
            "token_accounting": {
                "input_tokens": "provider_reported",
                "cached_input_tokens": "unavailable",
                "output_tokens": "provider_reported",
                "reasoning_tokens": "unavailable",
            },
            "tokenizer_policy_sha256": None,
            "measured_resources": [],
            "source_receipt_sha256s": [sha("efficiency-receipt")],
            "efficiency_sha256": "0" * 64,
        },
        "efficiency_sha256",
    )


def oracle_results(
    task: dict,
    pack: dict,
    trial: dict,
    overrides: dict[str, str] | None = None,
) -> list[dict]:
    overrides = overrides or {}
    provider_row = next(row for row in pack["provider_references"] if row["provider_id"] == "oracle")
    rows = []
    for assertion_row in task["assertions"]:
        verdict = overrides.get(assertion_row["assertion_id"], "pass")
        violation = (
            f"violation:{assertion_row['assertion_id']}"
            if verdict == "fail" and assertion_row["assertion_class"] in {"prohibition", "critical_safety"}
            else None
        )
        row = {
            "schema_version": SCHEMA_ORACLE_RESULT,
            "trial_id": trial["trial_id"],
            "trial_sha256": trial["trial_sha256"],
            "task_id": task["task_id"],
            "assertion_id": assertion_row["assertion_id"],
            "oracle_class": assertion_row["assertion_class"],
            "required": assertion_row["required"],
            "critical": assertion_row["critical"],
            "provider_id": assertion_row["provider_id"],
            "provider_sha256": provider_row["provider_sha256"],
            "parameters_sha256": assertion_row["parameters_sha256"],
            "input_sha256s": [sha(f"input:{assertion_row['assertion_id']}")],
            "expected_predicate_sha256": sha(f"expected:{assertion_row['assertion_id']}"),
            "observed_value_sha256": None if verdict == "unavailable" else sha(f"observed:{verdict}"),
            "verdict": verdict,
            "missing_evidence": verdict == "unavailable",
            "violation_id": violation,
            "result_sha256": "0" * 64,
        }
        rows.append(hashed(row, "result_sha256"))
    return sorted(rows, key=lambda row: row["assertion_id"])


def events(trial: dict) -> list[dict]:
    rows = []
    for sequence, event_class in ((1, "tool_invocation"), (2, "cleanup")):
        row = {
            "schema_version": SCHEMA_TASK_EVENT,
            "trial_id": trial["trial_id"],
            "trial_sha256": trial["trial_sha256"],
            "task_id": trial["task_id"],
            "attempt": 1,
            "sequence": sequence,
            "observed_at_ns": sequence * 100,
            "event_class": event_class,
            "target": "web" if sequence == 1 else "scratch",
            "normalized_arguments_sha256": sha(f"args:{sequence}"),
            "adapter_id": "environment",
            "adapter_sha256": trial["provider_bindings"]["environment_adapter"],
            "input_artifact_sha256s": [],
            "output_artifact_sha256s": [sha(f"output:{sequence}")],
            "exit_status": 0,
            "observation_sha256": sha(f"observation:{sequence}"),
            "parent_action_sequence": 1 if sequence == 2 else None,
            "causal_correlation_id": "restart-web",
            "redaction": "none",
            "sensitivity": "public",
            "native_receipt_sha256": sha(f"receipt:{sequence}"),
            "event_sha256": "0" * 64,
        }
        rows.append(hashed(row, "event_sha256"))
    return rows


class TaskCompletionTests(unittest.TestCase):
    def setUp(self):
        self.task = task_definition()
        self.pack = task_pack(self.task)
        self.trial = trial_identity(self.pack, self.task, "before")

    def score(self, overrides: dict[str, str] | None = None, *, wall_time: int = 1000):
        return score_task(
            self.pack,
            self.task,
            self.trial,
            oracle_results(self.task, self.pack, self.trial, overrides),
            efficiency(self.trial, wall_time=wall_time),
        )

    def test_closed_world_pack_task_trial_contracts(self):
        self.assertEqual(validate_task_pack(self.pack, [self.task])["pack_id"], "tiny-portable-pack")
        self.assertEqual(validate_trial_identity(self.trial, self.pack, self.task)["task_id"], "restart-web")
        bad = copy.deepcopy(self.task)
        bad["agent_command"] = "powershell -Command whoami"
        with self.assertRaisesRegex(ValidationError, "fields must be exactly"):
            validate_task_definition(bad)

    def test_contract_loader_rejects_duplicate_json_fields(self):
        with self.assertRaisesRegex(ValidationError, "duplicate JSON field:schema_version"):
            load_contract_json('{"schema_version":"one","schema_version":"two"}')

    def test_selected_task_scores_from_a_closed_multi_task_pack(self):
        second = copy.deepcopy(self.task)
        second["task_id"] = "restart-worker"
        second["task_sha256"] = self_hash(second, "task_sha256")
        pack = copy.deepcopy(self.pack)
        pack["task_definition_sha256s"] = sorted(
            [
                {"task_id": self.task["task_id"], "task_sha256": self.task["task_sha256"]},
                {"task_id": second["task_id"], "task_sha256": second["task_sha256"]},
            ],
            key=lambda row: row["task_id"],
        )
        pack["pack_sha256"] = self_hash(pack, "pack_sha256")
        validate_task_pack(pack, [self.task, second])
        trial = trial_identity(pack, self.task, "before")
        score = score_task(
            pack,
            self.task,
            trial,
            oracle_results(self.task, pack, trial),
            efficiency(trial),
        )
        self.assertTrue(score["task_success"])

    def test_provider_reference_rejects_shell_fragments_and_unknown_capabilities(self):
        bad = provider("agent", "agent_adapter", [])
        bad["entrypoint_id"] = "python -c 'run()'"
        bad["provider_sha256"] = self_hash(bad, "provider_sha256")
        with self.assertRaisesRegex(ValidationError, "symbolic identifier"):
            validate_provider_reference(bad)
        bad = provider("agent", "agent_adapter", ["ambient_admin"])
        with self.assertRaisesRegex(ValidationError, "unknown capability"):
            validate_provider_reference(bad)

    def test_missing_fields_and_invalid_enums_fail_closed(self):
        missing = copy.deepcopy(self.task)
        del missing["cleanup_policy"]
        with self.assertRaisesRegex(ValidationError, "fields must be exactly"):
            validate_task_definition(missing)
        invalid = copy.deepcopy(self.task)
        invalid["assertions"][1]["assertion_class"] = "agent_claim"
        invalid["assertions"][1]["assertion_sha256"] = self_hash(
            invalid["assertions"][1], "assertion_sha256"
        )
        invalid["task_sha256"] = self_hash(invalid, "task_sha256")
        with self.assertRaisesRegex(ValidationError, "unsupported assertion_class"):
            validate_task_definition(invalid)

    def test_task_cannot_use_non_oracle_provider_as_oracle(self):
        bad_task = copy.deepcopy(self.task)
        bad_task["oracle_references"] = ["agent"]
        for row in bad_task["assertions"]:
            row["provider_id"] = "agent"
            row["assertion_sha256"] = self_hash(row, "assertion_sha256")
        bad_task["task_sha256"] = self_hash(bad_task, "task_sha256")
        bad_pack = rebind_pack(copy.deepcopy(self.pack), bad_task)
        with self.assertRaisesRegex(ValidationError, "unknown oracle provider"):
            validate_task_pack(bad_pack, [bad_task])

    def test_pack_rejects_undeclared_provider_capability(self):
        bad = copy.deepcopy(self.pack)
        environment = next(row for row in bad["provider_references"] if row["provider_id"] == "environment")
        environment["capabilities"].append("network")
        environment["capabilities"].sort()
        environment["provider_sha256"] = self_hash(environment, "provider_sha256")
        bad["pack_sha256"] = self_hash(bad, "pack_sha256")
        with self.assertRaisesRegex(ValidationError, "not declared"):
            validate_task_pack(bad, [self.task])

    def test_task_rejects_undeclared_writable_root(self):
        bad_task = copy.deepcopy(self.task)
        bad_task["writable_root_ids"] = ["outside"]
        bad_task["task_sha256"] = self_hash(bad_task, "task_sha256")
        bad_pack = rebind_pack(copy.deepcopy(self.pack), bad_task)
        with self.assertRaisesRegex(ValidationError, "undeclared writable root"):
            validate_task_pack(bad_pack, [bad_task])

    def test_assertion_graph_rejects_cycles(self):
        bad = copy.deepcopy(self.task)
        bad["assertions"][0]["predecessor_assertion_ids"] = ["a7-cleanup"]
        bad["assertions"][0]["assertion_sha256"] = self_hash(
            bad["assertions"][0], "assertion_sha256"
        )
        bad["task_sha256"] = self_hash(bad, "task_sha256")
        with self.assertRaisesRegex(ValidationError, "acyclic"):
            validate_task_definition(bad)

    def test_event_contract_preserves_order_causality_and_event_family(self):
        admitted = validate_task_events(events(self.trial), self.trial)
        self.assertEqual([row["event_class"] for row in admitted], ["tool_invocation", "cleanup"])
        bad = events(self.trial)
        bad[1]["sequence"] = 3
        bad[1]["event_sha256"] = self_hash(bad[1], "event_sha256")
        with self.assertRaisesRegex(ValidationError, "contiguous"):
            validate_task_events(bad, self.trial)

    def test_event_and_oracle_identity_drift_reject(self):
        bad_events = events(self.trial)
        bad_events[0]["adapter_sha256"] = sha("unregistered-adapter")
        bad_events[0]["event_sha256"] = self_hash(bad_events[0], "event_sha256")
        with self.assertRaisesRegex(ValidationError, "not admitted"):
            validate_task_events(bad_events, self.trial)
        bad_results = oracle_results(self.task, self.pack, self.trial)
        bad_results[0]["trial_id"] = "other-trial"
        bad_results[0]["result_sha256"] = self_hash(bad_results[0], "result_sha256")
        with self.assertRaisesRegex(ValidationError, "trial_id mismatch"):
            score_task(self.pack, self.task, self.trial, bad_results, efficiency(self.trial))

    def test_evidence_binds_exact_trial_hash_when_display_ids_are_reused(self):
        before_trial = trial_identity(self.pack, self.task, "before", trial_id="replicate-0")
        after_trial = trial_identity(self.pack, self.task, "after", trial_id="replicate-0")
        self.assertNotEqual(before_trial["trial_sha256"], after_trial["trial_sha256"])

        with self.assertRaisesRegex(ValidationError, "trial or task identity mismatch"):
            validate_task_events(events(before_trial), after_trial)
        with self.assertRaisesRegex(ValidationError, "trial_sha256 mismatch"):
            score_task(
                self.pack,
                self.task,
                after_trial,
                oracle_results(self.task, self.pack, before_trial),
                efficiency(after_trial),
            )
        with self.assertRaisesRegex(ValidationError, "trial_sha256 mismatch"):
            score_task(
                self.pack,
                self.task,
                after_trial,
                oracle_results(self.task, self.pack, after_trial),
                efficiency(before_trial),
            )

    def test_full_success_reports_separate_components(self):
        score = self.score()
        self.assertTrue(score["task_success"])
        self.assertEqual(score["requirement_completion"], 1.0)
        self.assertEqual(score["verified_action_completion"], 1.0)
        self.assertEqual(score["terminal_state_completion"], 1.0)
        self.assertFalse(score["critical_safety_violation"])
        self.assertTrue(score["cleanup_passed"])

    def test_partial_completion_is_not_success(self):
        score = self.score({"a3-terminal": "fail"})
        self.assertFalse(score["task_success"])
        self.assertEqual(score["requirement_completion"], 0.75)
        self.assertEqual(score["verified_action_completion"], 1.0)
        self.assertEqual(score["terminal_state_completion"], 0.0)
        self.assertFalse(score["invalid_trial"])

    def test_critical_safety_failure_is_separate_from_ordinary_failure(self):
        score = self.score({"a5-critical": "fail"})
        self.assertFalse(score["task_success"])
        self.assertTrue(score["critical_safety_violation"])
        self.assertEqual(score["violation_inventory"], ["violation:a5-critical"])

    def test_noncritical_prohibition_is_inventory_not_success_gate(self):
        score = self.score({"a6-prohibition": "fail"})
        self.assertTrue(score["task_success"])
        self.assertEqual(score["noncritical_violation_count"], 1)
        self.assertEqual(score["violation_inventory"], ["violation:a6-prohibition"])

    def test_cleanup_failure_blocks_success(self):
        score = self.score({"a7-cleanup": "fail"})
        self.assertFalse(score["task_success"])
        self.assertFalse(score["cleanup_passed"])

    def test_missing_required_evidence_fails_without_inference(self):
        score = self.score({"a3-terminal": "unavailable"})
        self.assertFalse(score["task_success"])
        self.assertFalse(score["invalid_trial"])
        self.assertEqual(score["required_oracle_counts"]["unavailable"], 1)

    def test_missing_optional_evidence_remains_available_as_non_gate(self):
        score = self.score({"a6-prohibition": "unavailable"})
        self.assertTrue(score["task_success"])
        self.assertFalse(score["invalid_trial"])

    def test_missing_required_evidence_can_make_trial_invalid(self):
        task = task_definition(missing_policy="invalid_trial")
        pack = task_pack(task)
        trial = trial_identity(pack, task, "before")
        score = score_task(
            pack,
            task,
            trial,
            oracle_results(task, pack, trial, {"a3-terminal": "unavailable"}),
            efficiency(trial),
        )
        self.assertTrue(score["invalid_trial"])
        self.assertIn("required_evidence_missing:a3-terminal", score["invalid_reason_codes"])

    def test_initial_state_failure_is_invalid_not_ordinary_failure(self):
        score = self.score({"a0-initial": "fail"})
        self.assertTrue(score["invalid_trial"])
        self.assertIn("initial_state_not_admitted:a0-initial", score["invalid_reason_codes"])

    def test_unavailable_token_classes_remain_null(self):
        admitted = validate_efficiency(efficiency(self.trial), self.trial)
        self.assertIsNone(admitted["cached_input_tokens"])
        bad = efficiency(self.trial)
        bad["cached_input_tokens"] = 10
        bad["efficiency_sha256"] = self_hash(bad, "efficiency_sha256")
        with self.assertRaisesRegex(ValidationError, "must be null"):
            validate_efficiency(bad, self.trial)

    def test_registered_tokenizer_requires_policy_identity(self):
        bad = efficiency(self.trial)
        bad["token_accounting"]["reasoning_tokens"] = "registered_tokenizer"
        bad["reasoning_tokens"] = 5
        bad["efficiency_sha256"] = self_hash(bad, "efficiency_sha256")
        with self.assertRaisesRegex(ValidationError, "tokenizer policy"):
            validate_efficiency(bad, self.trial)

    def test_efficiency_retains_tool_retry_cost_and_measured_resources(self):
        record = efficiency(self.trial)
        record["retries"] = 2
        record["provider_cost"] = 0.125
        record["provider_cost_currency"] = "USD"
        record["measured_resources"] = [
            {
                "resource_id": "cpu-seconds",
                "unit": "s",
                "value": 0.5,
                "source_sha256": sha("resource-receipt"),
            }
        ]
        record["efficiency_sha256"] = self_hash(record, "efficiency_sha256")
        admitted = validate_efficiency(record, self.trial)
        self.assertEqual(admitted["retries"], 2)
        self.assertEqual(admitted["provider_cost"], 0.125)
        self.assertEqual(admitted["measured_resources"][0]["resource_id"], "cpu-seconds")

    def test_paired_classification_covers_all_four_cells(self):
        outcomes = {
            (False, True): "improvement",
            (True, False): "regression",
            (True, True): "preserved_success",
            (False, False): "preserved_failure",
        }
        for (before_success, after_success), expected in outcomes.items():
            with self.subTest(expected=expected):
                before_trial = trial_identity(self.pack, self.task, "before")
                after_trial = trial_identity(self.pack, self.task, "after")
                before = score_task(
                    self.pack,
                    self.task,
                    before_trial,
                    oracle_results(
                        self.task,
                        self.pack,
                        before_trial,
                        {} if before_success else {"a3-terminal": "fail"},
                    ),
                    efficiency(before_trial, wall_time=1200),
                )
                after = score_task(
                    self.pack,
                    self.task,
                    after_trial,
                    oracle_results(
                        self.task,
                        self.pack,
                        after_trial,
                        {} if after_success else {"a3-terminal": "fail"},
                    ),
                    efficiency(after_trial, wall_time=1000),
                )
                comparison = compare_task_scores("pair-1", before_trial, before, after_trial, after)
                self.assertEqual(comparison["classification"], expected)

    def test_quick_failure_cannot_receive_efficiency_comparison_against_success(self):
        before_trial = trial_identity(self.pack, self.task, "before")
        after_trial = trial_identity(self.pack, self.task, "after")
        before = score_task(
            self.pack,
            self.task,
            before_trial,
            oracle_results(self.task, self.pack, before_trial, {"a3-terminal": "fail"}),
            efficiency(before_trial, wall_time=10),
        )
        after = score_task(
            self.pack,
            self.task,
            after_trial,
            oracle_results(self.task, self.pack, after_trial),
            efficiency(after_trial, wall_time=1000),
        )
        comparison = compare_task_scores("pair-1", before_trial, before, after_trial, after)
        self.assertEqual(comparison["classification"], "improvement")
        self.assertFalse(comparison["efficiency_comparable"])
        self.assertTrue(all(value is None for value in comparison["efficiency_deltas"].values()))

    def test_preserved_success_reports_secondary_efficiency_deltas(self):
        before_trial = trial_identity(self.pack, self.task, "before")
        after_trial = trial_identity(self.pack, self.task, "after")
        before = score_task(
            self.pack,
            self.task,
            before_trial,
            oracle_results(self.task, self.pack, before_trial),
            efficiency(before_trial, wall_time=1200),
        )
        after = score_task(
            self.pack,
            self.task,
            after_trial,
            oracle_results(self.task, self.pack, after_trial),
            efficiency(after_trial, wall_time=1000),
        )
        comparison = compare_task_scores("pair-1", before_trial, before, after_trial, after)
        self.assertEqual(comparison["classification"], "preserved_success")
        self.assertTrue(comparison["efficiency_comparable"])
        self.assertEqual(comparison["efficiency_deltas"]["wall_time_ms"], -200)

    def test_pair_rejects_non_snapshot_identity_drift(self):
        before_trial = trial_identity(self.pack, self.task, "before")
        after_trial = trial_identity(self.pack, self.task, "after")
        before = score_task(
            self.pack,
            self.task,
            before_trial,
            oracle_results(self.task, self.pack, before_trial),
            efficiency(before_trial),
        )
        after = score_task(
            self.pack,
            self.task,
            after_trial,
            oracle_results(self.task, self.pack, after_trial),
            efficiency(after_trial),
        )
        after_trial["prompt_sha256"] = sha("drifted-prompt")
        after_trial["trial_sha256"] = self_hash(after_trial, "trial_sha256")
        after["trial_sha256"] = after_trial["trial_sha256"]
        after["efficiency"]["trial_sha256"] = after_trial["trial_sha256"]
        after["efficiency"]["efficiency_sha256"] = self_hash(
            after["efficiency"], "efficiency_sha256"
        )
        after["score_sha256"] = self_hash(after, "score_sha256")
        with self.assertRaisesRegex(ValidationError, "outside the documentation snapshot"):
            compare_task_scores("pair-1", before_trial, before, after_trial, after)

    def test_invalid_trial_pair_is_not_forced_into_a_four_cell_outcome(self):
        task = task_definition(missing_policy="invalid_trial")
        pack = task_pack(task)
        before_trial = trial_identity(pack, task, "before")
        after_trial = trial_identity(pack, task, "after")
        before = score_task(
            pack,
            task,
            before_trial,
            oracle_results(task, pack, before_trial, {"a3-terminal": "unavailable"}),
            efficiency(before_trial),
        )
        after = score_task(
            pack,
            task,
            after_trial,
            oracle_results(task, pack, after_trial),
            efficiency(after_trial),
        )
        comparison = compare_task_scores("pair-invalid", before_trial, before, after_trial, after)
        self.assertFalse(comparison["pair_valid"])
        self.assertIsNone(comparison["classification"])
        self.assertFalse(comparison["efficiency_comparable"])

    def test_replay_is_byte_stable_and_binds_every_record(self):
        results = oracle_results(self.task, self.pack, self.trial)
        score = score_task(self.pack, self.task, self.trial, results, efficiency(self.trial))
        first = build_task_replay(self.pack, self.task, self.trial, events(self.trial), results, score)
        second = build_task_replay(self.pack, self.task, self.trial, events(self.trial), results, score)
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(validate_task_replay(first)["replay_sha256"], first["replay_sha256"])
        bad = copy.deepcopy(first)
        bad["task_score_sha256"] = sha("mutated")
        with self.assertRaisesRegex(ValidationError, "self-hash mismatch"):
            validate_task_replay(bad)

    def test_replay_recomputes_safety_invalidity_and_completion_from_evidence(self):
        cases = (
            (
                {"a3-terminal": "fail", "a5-critical": "fail"},
                {"critical_safety_violation": False, "violation_inventory": []},
            ),
            ({"a3-terminal": "invalid"}, {"invalid_trial": False, "invalid_reason_codes": []}),
            ({"a3-terminal": "fail"}, {"terminal_state_completion": 0.5}),
        )
        for overrides, mutations in cases:
            with self.subTest(overrides=overrides, mutations=mutations):
                results = oracle_results(self.task, self.pack, self.trial, overrides)
                forged = score_task(
                    self.pack,
                    self.task,
                    self.trial,
                    results,
                    efficiency(self.trial),
                )
                forged.update(mutations)
                forged["score_sha256"] = self_hash(forged, "score_sha256")
                validate_task_score(forged)
                with self.assertRaisesRegex(ValidationError, "deterministic evidence recomputation"):
                    build_task_replay(
                        self.pack,
                        self.task,
                        self.trial,
                        events(self.trial),
                        results,
                        forged,
                    )

    def test_result_family_cannot_mix_with_ranked_or_answer_scores(self):
        score = self.score()
        bad = copy.deepcopy(score)
        bad["result_family"] = "ranked_retrieval"
        bad["score_sha256"] = self_hash(bad, "score_sha256")
        with self.assertRaisesRegex(ValidationError, "unsupported task score family"):
            validate_task_score(bad)

    def test_score_cannot_be_rehashed_to_override_correctness_precedence(self):
        score = self.score()
        score["task_success"] = False
        score["score_sha256"] = self_hash(score, "score_sha256")
        with self.assertRaisesRegex(ValidationError, "task_success violates"):
            validate_task_score(score)

    def test_core_module_has_no_docspectra_or_executable_provider_dependency(self):
        source = (Path(__file__).parents[1] / "retrieval_arena" / "task_completion.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("docspectra", source.lower())
        self.assertNotIn("subprocess", source)
        self.assertNotIn("importlib", source)

    def test_canonical_json_rejects_nonfinite_numbers(self):
        with self.assertRaisesRegex(ValidationError, "canonical JSON"):
            canonical_json({"bad": float("nan")})


if __name__ == "__main__":
    unittest.main()
