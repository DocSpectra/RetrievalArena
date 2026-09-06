from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OPERATOR_PATH = ROOT / "containers" / "verification" / "evaluate_profile.py"
SPEC = importlib.util.spec_from_file_location("verification_profile_operator", OPERATOR_PATH)
assert SPEC and SPEC.loader
OPERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPERATOR)


class ProfileOperatorAdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.root = Path(self.temp.name)
        self.input = self.root / "input"; self.input.mkdir()
        self.output = self.root / "output"; self.output.mkdir()
        self.original_input, self.original_output = OPERATOR.INPUT, OPERATOR.OUTPUT
        OPERATOR.INPUT, OPERATOR.OUTPUT = self.input, self.output

    def tearDown(self) -> None:
        OPERATOR.INPUT, OPERATOR.OUTPUT = self.original_input, self.original_output
        self.temp.cleanup()

    def request(self, *, extra: bool = False, inventory=None, config_path: str = "config.json") -> None:
        (self.input / "config.json").write_text("{}\n", encoding="utf-8", newline="\n")
        if extra:
            (self.input / "extra.json").write_text("{}\n", encoding="utf-8", newline="\n")
        rows = OPERATOR._regular_inventory(self.input)
        value = rows if inventory is None else inventory
        request = {
            "schema_version": OPERATOR.REQUEST_SCHEMA,
            "command": "evaluate-profile",
            "config_path": config_path,
            "input_inventory": value,
            "input_inventory_sha256": OPERATOR._sha(OPERATOR._canonical(value)),
        }
        (self.input / OPERATOR.REQUEST).write_text(json.dumps(request, sort_keys=True), encoding="utf-8", newline="\n")

    def owner(self) -> None:
        (self.output / OPERATOR.OWNER).write_text("retrieval_arena.output_root.v1\n", encoding="utf-8", newline="\n")

    def test_exact_request_and_fresh_output_admit(self) -> None:
        self.request(); self.owner()
        request, config, inventory = OPERATOR._read_request()
        OPERATOR._admit_output()
        self.assertEqual(request["config_path"], "config.json")
        self.assertEqual(config, self.input / "config.json")
        self.assertEqual(inventory, [{"path": "config.json", "sha256": OPERATOR._file_sha(config)}])

    def test_missing_extra_and_modified_inventory_fail_closed(self) -> None:
        self.request(inventory=[])
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "operator-input-inventory-mismatch"):
            OPERATOR._read_request()
        (self.input / OPERATOR.REQUEST).unlink()
        self.request(extra=True)
        (self.input / "extra.json").write_text('{"changed":true}\n', encoding="utf-8", newline="\n")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "operator-input-inventory-mismatch"):
            OPERATOR._read_request()

    def test_traversal_and_nonempty_output_fail_closed(self) -> None:
        self.request(config_path="../config.json"); self.owner()
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "operator-path-not-admitted"):
            OPERATOR._read_request()
        (self.output / "unexpected").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "operator-output-root-not-fresh"):
            OPERATOR._admit_output()

    def test_symlink_is_rejected_when_supported(self) -> None:
        target = self.input / "config.json"; target.write_text("{}\n", encoding="utf-8")
        link = self.input / "linked.json"
        try:
            os.symlink(target, link)
        except (NotImplementedError, OSError):
            self.skipTest("symlink creation unavailable on this host")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "operator-symlink-rejected"):
            OPERATOR._regular_inventory(self.input)

    def test_unowned_output_never_receives_a_receipt(self) -> None:
        # An invalid marker means the output root is not owned by this operator.
        self.assertEqual(OPERATOR.main(), 2)
        self.assertFalse((self.output / "operator_receipt.v1.json").exists())


    def scoring_request(self):
        from unit_tests.test_dual_profile import executable_bertscore_provider
        from retrieval_arena.dual_profile import self_hash
        provider, _ = executable_bertscore_provider(self.input)
        provider.update(schema_version="retrieval_arena.bertscore_provider.v2", num_layers=17)
        provider["provider_sha256"] = self_hash(provider, "provider_sha256")
        (self.input / "provider.json").write_text(json.dumps(provider), encoding="utf-8")
        (self.input / "pairs.jsonl").write_text(
            json.dumps({"query_id": "q", "generated_answer": "a", "reference_answer": "a"}) + "\n",
            encoding="utf-8",
        )
        config = {"schema_version": "retrieval_arena.bertscore_scoring_config.v1",
                  "provider_path": "provider.json", "answer_pairs_path": "pairs.jsonl"}
        (self.input / "config.json").write_text(json.dumps(config), encoding="utf-8")
        inventory = OPERATOR._regular_inventory(self.input)
        request = {"schema_version": "retrieval_arena.bertscore_scoring_request.v1",
                   "command": "score-bertscore", "config_path": "config.json",
                   "input_inventory": inventory,
                   "input_inventory_sha256": OPERATOR._sha(OPERATOR._canonical(inventory))}
        (self.input / OPERATOR.REQUEST).write_text(json.dumps(request), encoding="utf-8")
        return provider

    def test_scoring_operator_produces_bound_scores_from_admitted_fixture(self):
        provider = self.scoring_request()
        request, config, _ = OPERATOR._read_request("score-bertscore")
        staging = self.root / "staging"
        staging.mkdir()
        result = OPERATOR._score_bertscore_files(config, staging)
        scores = json.loads((staging / "bertscore_scores.v1.json").read_bytes())
        self.assertEqual(scores["provider_sha256"], provider["provider_sha256"])
        self.assertEqual(result["result"]["result_sha256"], scores["scores_sha256"])
        self.assertEqual(request["command"], "score-bertscore")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "command-invalid"):
            OPERATOR._read_request()

    def test_scoring_operator_requires_v2_and_local_provider_assets(self):
        provider = self.scoring_request()
        staging = self.root / "staging"
        staging.mkdir()
        provider["schema_version"] = "retrieval_arena.bertscore_provider.v1"
        (self.input / "provider.json").write_text(json.dumps(provider), encoding="utf-8")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "v2-provider-required"):
            OPERATOR._score_bertscore_files(self.input / "config.json", staging)
        provider["schema_version"] = "retrieval_arena.bertscore_provider.v2"
        provider["model_artifact_path"] = "../outside"
        (self.input / "provider.json").write_text(json.dumps(provider), encoding="utf-8")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "path-not-admitted"):
            OPERATOR._score_bertscore_files(self.input / "config.json", staging)
        self.assertEqual(list(staging.iterdir()), [])

    def test_scoring_operator_rejects_changed_inventory_and_config_escape(self):
        self.scoring_request()
        (self.input / "checkpoint" / "frozen.bin").write_bytes(b"changed")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "inventory-mismatch"):
            OPERATOR._read_request("score-bertscore")
        config = {"schema_version": "retrieval_arena.bertscore_scoring_config.v1",
                  "provider_path": "../provider.json", "answer_pairs_path": "pairs.jsonl"}
        (self.input / "config.json").write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(OPERATOR.AdmissionError, "path-not-admitted"):
            OPERATOR._score_bertscore_files(self.input / "config.json", self.root)

if __name__ == "__main__":
    unittest.main()
