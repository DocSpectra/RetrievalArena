from __future__ import annotations

import hashlib
import importlib.util
import json
import copy
import unittest
from pathlib import Path

from retrieval_arena.verification_witness import (
    NEGATIVE_CASES,
    SCHEMA,
    build_focused_witness,
    collect_focused_witness,
)
from retrieval_arena.review_packets import build_review_packet, import_judgments
from retrieval_arena.errors import ValidationError


ROOT = Path(__file__).resolve().parents[1]
VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verification_runner_for_witness", ROOT / "containers" / "verification" / "verify.py",
)
assert VERIFY_SPEC and VERIFY_SPEC.loader
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
VERIFY_SPEC.loader.exec_module(VERIFY)


class FocusedVerificationWitnessTests(unittest.TestCase):
    def test_base_judgment_fixture_is_valid_before_targeted_hash_mutation(self):
        from retrieval_arena import verification_witness as witness
        policy = witness._policy()
        packet = build_review_packet([witness._candidate(index) for index in range(3)], policy)
        artifact = witness._judgments(packet, policy)
        import_judgments(artifact, packet, policy)
        corrupted = copy.deepcopy(artifact); corrupted["artifact_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValidationError, "judgment artifact self-hash mismatch"):
            import_judgments(corrupted, packet, policy)

    def test_focused_case_inventory_is_ordered_unique_and_contains_registered_cases(self):
        suite = VERIFY._suite("focused")
        case_ids = VERIFY._case_ids(suite)
        self.assertEqual(len(case_ids), suite.countTestCases())
        self.assertEqual(len(case_ids), len(set(case_ids)))
        for expected in (
            "unit_tests.test_dual_profile.DualProfileTests.test_bertscore_provider_identity_scores_and_blind_spot_fixture",
            "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_packet_pool_policy_snapshot_and_schema_reject",
            "unit_tests.test_profile_operator_admission.ProfileOperatorAdmissionTests.test_unowned_output_never_receives_a_receipt",
            "unit_tests.test_review_packet_operator.ReviewPacketOperatorTests.test_invalid_import_rolls_back_packet_and_writes_terminal_receipt",
        ):
            self.assertIn(expected, case_ids)

    def test_runtime_collector_observes_every_ra_mutation_and_external_split(self):
        runtime = collect_focused_witness()
        expected = {row["case_id"]: row for row in NEGATIVE_CASES if row["owner"] == "retrieval_arena"}
        observed = {row["case_id"]: row for row in runtime["observed_negative_cases"]}
        self.assertEqual(set(observed), set(expected))
        for case_id, contract in expected.items():
            self.assertEqual(observed[case_id]["observed_class"], contract["expected_class"])
            self.assertEqual(observed[case_id]["observed_reason_code"], contract["expected_reason_code"])
        self.assertEqual(
            [row["case_id"] for row in runtime["external_required_negative_cases"]],
            ["post_receipt_mutation", "authority_hash_splice"],
        )

    def test_bertscore_observation_is_live_offline_and_relation_bound(self):
        observed = collect_focused_witness()["bertscore"]
        scores = {row["case_id"]: row["f1"] for row in observed["cases"]}
        self.assertGreater(scores["exact_match"], scores["contradiction"])
        self.assertGreater(scores["contradiction"], scores["paraphrase"])
        self.assertGreater(scores["paraphrase"], scores["unrelated"])
        self.assertTrue(observed["no_runtime_download"])
        self.assertTrue(observed["no_substitution"])
        self.assertTrue(observed["byte_stable_replay"])
        self.assertEqual(
            {row["state"] for row in observed["unavailable"]},
            {"registered_provider_artifact_missing", "runtime_download_rejected", "same_version_substitution_rejected"},
        )

    def test_canonical_self_hash_binds_runtime_observations(self):
        runtime = collect_focused_witness()
        witness = build_focused_witness(
            source_commit="a" * 40,
            source_tree="b" * 40,
            image_reference="sha256:" + "c" * 64,
            dependency_lock_sha256="d" * 64,
            focused_tests_run=27,
            focused_test_case_ids=[f"unit_tests.test_witness.Case.test_{index}" for index in range(27)],
            runtime=runtime,
        )
        self.assertEqual(witness["schema_version"], SCHEMA)
        self.assertTrue(witness["focused_suite_success"])
        self.assertEqual(witness["focused_tests_run"], 27)
        self.assertEqual(witness["focused_test_case_ids"], [f"unit_tests.test_witness.Case.test_{index}" for index in range(27)])
        self.assertEqual(
            witness["witness_sha256"],
            hashlib.sha256(
                json.dumps({key: value for key, value in witness.items() if key != "witness_sha256"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
