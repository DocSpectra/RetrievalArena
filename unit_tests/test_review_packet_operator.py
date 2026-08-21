from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from unit_tests.test_review_packets import candidate, completed_judgments, review_policy


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verification_review_packet_operator",
    ROOT / "containers" / "verification" / "review_packet.py",
)
assert SPEC and SPEC.loader
OPERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPERATOR)


class ReviewPacketOperatorTests(unittest.TestCase):
    def _request(self, input_root: Path, output: Path, *, judgment=None):
        candidates = [candidate(index) for index in range(5)]
        (input_root / "candidates.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in candidates),
            encoding="utf-8", newline="\n",
        )
        policy = review_policy(ceiling=5)
        (input_root / "policy.json").write_text(json.dumps(policy, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        judgment_path = None
        if judgment is not None:
            packet = OPERATOR.build_review_packet(candidates, policy)
            value = completed_judgments(packet, policy) if judgment == "valid" else {"not": "a judgment artifact"}
            (input_root / "judgments.json").write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
            judgment_path = "judgments.json"
        inventory = OPERATOR._inventory(input_root)
        request = {
            "schema_version": OPERATOR.SCHEMA,
            "command": "review-packet",
            "candidates_path": "candidates.jsonl",
            "policy_path": "policy.json",
            "judgment_artifact_path": judgment_path,
            "input_inventory": inventory,
            "input_inventory_sha256": hashlib.sha256(OPERATOR._canonical(inventory)).hexdigest(),
        }
        (input_root / OPERATOR.REQUEST).write_text(json.dumps(request, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        (output / OPERATOR.OWNER).write_text("retrieval_arena.output_root.v1\n", encoding="utf-8", newline="\n")
        return policy

    def _run(self, input_root: Path, output: Path) -> tuple[int, str, str]:
        old_input, old_output = OPERATOR.INPUT, OPERATOR.OUTPUT
        OPERATOR.INPUT, OPERATOR.OUTPUT = input_root, output
        try:
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = OPERATOR.main()
            return code, stdout.getvalue(), stderr.getvalue()
        finally:
            OPERATOR.INPUT, OPERATOR.OUTPUT = old_input, old_output

    def test_generic_export_is_inventory_bound_and_byte_stable(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests") as root_text:
            root = Path(root_text); input_root = root / "input"; output = root / "output"
            input_root.mkdir(); output.mkdir()
            policy = self._request(input_root, output)
            code, stdout, stderr = self._run(input_root, output)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(len(stdout.splitlines()), 1)
            self.assertEqual(json.loads(stdout)["frame_count"], 5)
            packet = json.loads((output / "review_packet.v1.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["frame_count"], 5)
            self.assertEqual(packet["review_policy_sha256"], policy["policy_sha256"])
            packet_bytes = (output / "review_packet.v1.json").read_bytes()
            self.assertEqual(packet_bytes, OPERATOR._canonical(packet) + b"\n")
            receipt = json.loads((output / OPERATOR.RECEIPT).read_text(encoding="utf-8"))
            self.assertEqual(receipt["exit_code"], 0)
            self.assertEqual(receipt["output_inventory"], OPERATOR._inventory(output, exclude={OPERATOR.OWNER, OPERATOR.RECEIPT}))
            unsigned = dict(receipt); unsigned["receipt_sha256"] = "0" * 64
            self.assertEqual(receipt["receipt_sha256"], hashlib.sha256(OPERATOR._canonical(unsigned)).hexdigest())
            second_input, second_output = root / "input-two", root / "output-two"
            second_input.mkdir(); second_output.mkdir()
            self._request(second_input, second_output)
            second_code, second_stdout, second_stderr = self._run(second_input, second_output)
            self.assertEqual(second_code, 0, second_stderr)
            self.assertEqual(len(second_stdout.splitlines()), 1)
            self.assertEqual((second_output / "review_packet.v1.json").read_bytes(), packet_bytes)

    def test_invalid_import_rolls_back_packet_and_writes_terminal_receipt(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests") as root_text:
            root = Path(root_text); input_root = root / "input"; output = root / "output"; input_root.mkdir(); output.mkdir()
            self._request(input_root, output, judgment="invalid")
            code, stdout, stderr = self._run(input_root, output)
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertTrue(stderr)
            self.assertFalse((output / "review_packet.v1.json").exists())
            self.assertFalse((output / "judgment_import.v1.json").exists())
            receipt = json.loads((output / OPERATOR.RECEIPT).read_text(encoding="utf-8"))
            self.assertEqual(receipt["exit_code"], 1)
            self.assertEqual(receipt["output_inventory"], [])
            self.assertEqual(sorted(path.name for path in output.iterdir()), [OPERATOR.OWNER, OPERATOR.RECEIPT])

    def test_valid_import_publishes_exact_two_artifact_inventory(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests") as root_text:
            root = Path(root_text); input_root = root / "input"; output = root / "output"; input_root.mkdir(); output.mkdir()
            self._request(input_root, output, judgment="valid")
            code, stdout, stderr = self._run(input_root, output)
            self.assertEqual(code, 0, stderr)
            self.assertTrue(json.loads(stdout)["imported"])
            packet = json.loads((output / "review_packet.v1.json").read_text(encoding="utf-8"))
            imported = json.loads((output / "judgment_import.v1.json").read_text(encoding="utf-8"))
            self.assertEqual((output / "review_packet.v1.json").read_bytes(), OPERATOR._canonical(packet) + b"\n")
            self.assertEqual((output / "judgment_import.v1.json").read_bytes(), OPERATOR._canonical(imported) + b"\n")
            receipt = json.loads((output / OPERATOR.RECEIPT).read_text(encoding="utf-8"))
            self.assertEqual([row["path"] for row in receipt["output_inventory"]], ["judgment_import.v1.json", "review_packet.v1.json"])

    def test_receipt_write_fault_rolls_back_every_publication_and_success_stdout(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests") as root_text:
            root = Path(root_text); input_root = root / "input"; output = root / "output"; input_root.mkdir(); output.mkdir()
            self._request(input_root, output)
            original = OPERATOR._write
            def receipt_fault(path, value):
                if path.name == OPERATOR.RECEIPT:
                    raise OSError("injected receipt fault")
                return original(path, value)
            OPERATOR._write = receipt_fault
            try:
                code, stdout, stderr = self._run(input_root, output)
            finally:
                OPERATOR._write = original
            self.assertEqual(code, 1)
            self.assertEqual(stdout, "")
            self.assertIn("review-packet-receipt-write-failed", stderr)
            self.assertEqual([path.name for path in output.iterdir()], [OPERATOR.OWNER])


if __name__ == "__main__":
    unittest.main()
