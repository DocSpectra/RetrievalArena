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


if __name__ == "__main__":
    unittest.main()
