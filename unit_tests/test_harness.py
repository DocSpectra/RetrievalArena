from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from retrieval_arena.config import TestConfig
from retrieval_arena.harness import prepare_input


class HarnessTests(unittest.TestCase):
    def test_prepare_input_copies_optional_faq_support_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dataset = root / "dataset"
            (dataset / "corpus").mkdir(parents=True)
            (dataset / "corpus" / "doc.md").write_text("# Doc\n", encoding="utf-8")
            (dataset / "questions.jsonl").write_text('{"question_id":"q1","question":"Q?"}\n', encoding="utf-8")
            (dataset / "answers.jsonl").write_text('{"question_id":"q1","answer":"A"}\n', encoding="utf-8")
            (dataset / "faq_support_audit.jsonl").write_text('{"question_id":"q1","top_docs":[]}\n', encoding="utf-8")
            input_dir = root / "input"

            prepare_input(dataset, TestConfig(name="oracle_graph_support", image="test", config={"experiment_id": "E005"}), input_dir)

            self.assertTrue((input_dir / "faq_support_audit.jsonl").exists())
            self.assertIn("E005", (input_dir / "config.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
