from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from retrieval_arena.candidate_retrieval import canonical, prepare_provider_input, project_ranked_predictions, regular_inventory, sha256_bytes, validate_config
from retrieval_arena.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "retrieval-arena/rag-lexical-topk@sha256:" + "a" * 64


class CandidateRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.root = Path(self.temp.name)
        self.input = self.root / "input"; self.input.mkdir()
        (self.input / "questions.jsonl").write_text(json.dumps({"question_id": "q1", "question": "sync waves"}) + "\n", encoding="utf-8")
        self.repo = self.root / "candidate"; self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Fixture"], check=True)
        docs = self.repo / "docs"; docs.mkdir()
        (docs / "guide.md").write_text("# Guide\nSync waves order resources.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "docs/guide.md"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.tree = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()

    def tearDown(self): self.temp.cleanup()

    def config(self):
        inventory = regular_inventory(self.input)
        return {"schema_version": "retrieval_arena.candidate_retrieval_config.v1", "experiment_id": "exp", "execution_id": "slot", "output_identity": "output", "candidate": {"commit": self.commit, "tree": self.tree}, "corpus": {"root": "docs", "extensions": [".md"], "import_policy": "cikm_flat_path_sha1_v1"}, "queries_path": "questions.jsonl", "provider": {"name": "rag_lexical_topk", "image": IMAGE, "platform": "linux/amd64", "network": "none", "top_k": 1, "max_context_chars_per_doc": 1500}, "ranked": {"schema_version": "retrieval_arena.ranked_prediction.v2", "context_item_budget_k": 1}, "input_inventory": inventory, "input_inventory_sha256": sha256_bytes(canonical(inventory))}

    def test_closed_config_and_provider_selection(self):
        value = self.config(); validate_config(value); value["unknown"] = True
        with self.assertRaisesRegex(ValidationError, "config-fields"): validate_config(value)
        value = self.config(); value["provider"]["name"] = "substitute"
        with self.assertRaisesRegex(ValidationError, "provider-policy"): validate_config(value)

    def test_exact_candidate_and_inventory_prepare(self):
        destination = self.root / "provider-input"
        result = prepare_provider_input(self.config(), self.input, self.repo, destination)
        self.assertEqual(result["candidate_commit"], self.commit)
        self.assertTrue((destination / "corpus" / "guide__bbbdfa65.md").is_file())
        bad = self.config(); bad["candidate"]["tree"] = "0" * 40
        with self.assertRaisesRegex(ValidationError, "candidate-admission"): prepare_provider_input(bad, self.input, self.repo, self.root / "bad")

    def provider_output(self):
        output = self.root / "provider-output"; output.mkdir()
        (output / "metadata.json").write_text(json.dumps({"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": 1, "max_context_chars_per_doc": 1500}), encoding="utf-8")
        item = {"doc_id": "guide__bbbdfa65", "text": "Sync waves", "score": 1.0, "source": "lexical_topk", "rank": 1}
        row = {"question_id": "q1", "question": "sync waves", "generated_answer": "Sync waves", "retrieved_context": [item]}
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        return output

    def test_deterministic_ranked_projection_and_partial_rejection(self):
        output = self.provider_output()
        documents = self.root / "documents.json"
        documents.write_text(json.dumps([{"doc_id": "guide__bbbdfa65", "expected_text": "Sync waves"}]), encoding="utf-8")
        first, answers = project_ranked_predictions(self.config(), output, self.input, documents)
        self.assertEqual((first, answers), project_ranked_predictions(self.config(), output, self.input, documents))
        self.assertEqual(first[0]["items"][0]["support_target_id"], "guide__bbbdfa65")
        self.assertEqual(answers, [{"query_id": "q1", "generated_answer": "Sync waves"}])
        (output / "unexpected").write_text("partial", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "output-shape"): project_ranked_predictions(self.config(), output, self.input, documents)

    def test_missing_query_and_fabricated_content_fail_closed(self):
        output = self.provider_output(); documents = self.root / "documents.json"
        documents.write_text(json.dumps([{"doc_id": "guide__bbbdfa65", "expected_text": "different"}]), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "provider-item"):
            project_ranked_predictions(self.config(), output, self.input, documents)
        (output / "predictions.jsonl").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "query-completeness"):
            project_ranked_predictions(self.config(), output, self.input, documents)

    def test_operator_parity_and_no_scoring(self):
        for relative in ("scripts/run-verification.ps1", "scripts/run-verification.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in ("candidate-retrieve", "--network", "none", "rag-lexical-topk@sha256"): self.assertIn(token, text)
        module = (ROOT / "retrieval_arena" / "candidate_retrieval.py").read_text(encoding="utf-8")
        self.assertNotIn("score_ranked", module); self.assertNotIn("qrels", module)


if __name__ == "__main__": unittest.main()
