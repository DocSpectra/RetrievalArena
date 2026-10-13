"""Unit tests for tests/rag_lexical_topk/runner.py answer policies (S024)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "tests" / "rag_lexical_topk" / "runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("rag_lexical_topk_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Frozen legacy reference (git show c535801:tests/rag_lexical_topk/runner.py) ---
LEGACY_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def legacy_tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in LEGACY_TOKEN_RE.finditer(text)]


def legacy_answer_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip("# ").strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    snippet = " ".join(lines)
    if max_chars > 0 and len(snippet) > max_chars:
        return snippet[:max_chars].rsplit(" ", 1)[0].strip()
    return snippet


def legacy_bm25_scores(query_tokens: list[str], corpus_tokens: dict[str, list[str]]) -> list[tuple[float, str]]:
    num_docs = len(corpus_tokens)
    avg_len = sum(len(doc_tokens) for doc_tokens in corpus_tokens.values()) / num_docs if num_docs else 0.0
    doc_freq: Counter[str] = Counter()
    for doc_tokens in corpus_tokens.values():
        doc_freq.update(set(doc_tokens))
    k1 = 1.5
    b = 0.75
    query = Counter(query_tokens)
    scored: list[tuple[float, str]] = []
    for doc_id, doc_tokens in corpus_tokens.items():
        tf = Counter(doc_tokens)
        doc_len = len(doc_tokens)
        score = 0.0
        for token, query_count in query.items():
            if tf[token] == 0:
                continue
            idf = math.log(1.0 + (num_docs - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
            denom = tf[token] + k1 * (1.0 - b + b * (doc_len / avg_len if avg_len else 0.0))
            score += query_count * idf * ((tf[token] * (k1 + 1.0)) / denom)
        scored.append((score, doc_id))
    return sorted(scored, key=lambda item: (-item[0], item[1]))


def legacy_predictions(input_dir: Path) -> tuple[str, dict]:
    config = json.loads((input_dir / "config.yaml").read_text(encoding="utf-8"))
    top_k = max(1, int(config.get("top_k", 5)))
    max_context_chars = max(0, int(config.get("max_context_chars_per_doc", 1500)))
    questions = [json.loads(line) for line in (input_dir / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    corpus_root = input_dir / "corpus"
    corpus = {path.relative_to(corpus_root).with_suffix("").as_posix(): path.read_text(encoding="utf-8")
              for path in corpus_root.rglob("*.md")}
    corpus_tokens = {doc_id: legacy_tokens(text) for doc_id, text in corpus.items()}
    lines = []
    for question in questions:
        ranked = legacy_bm25_scores(legacy_tokens(question["question"]), corpus_tokens)[:top_k]
        context = [{"doc_id": doc_id, "text": legacy_answer_text(corpus[doc_id], max_chars=max_context_chars),
                    "score": float(score), "source": "lexical_topk", "rank": rank}
                   for rank, (score, doc_id) in enumerate(ranked, start=1)]
        generated = " ".join(item["text"] for item in context if item["text"])
        lines.append(json.dumps({"question_id": question["question_id"], "question": question["question"],
                                 "generated_answer": generated, "retrieved_context": context}, sort_keys=True) + "\n")
    metadata = {"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": top_k,
                "max_context_chars_per_doc": max_context_chars}
    return "".join(lines), metadata
# --- end frozen legacy reference ---


INTRO = " ".join(f"filler sentence number {index} about nothing in particular." for index in range(12))
DEEP_DOC = (
    "# Deployment Guide\n"
    "\n"
    f"{INTRO}\n"
    "Second line of the intro paragraph, still without the answer.\n"
    "\n"
    "## Sync Waves\n"
    "\n"
    "Sync waves order resources during an Argo CD sync;\r\n"
    "lower wave numbers apply first.\n"
    "\n"
    "#### Deeper heading kept inside a paragraph block\n"
    "A trailing paragraph mentioning hooks only.\n"
)
OTHER_DOC = "# Other\n\nA note about hooks and phases, never about waves.\n"


def write_input(root: Path, config: dict, questions: list[dict], corpus: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    (root / "corpus").mkdir()
    (root / "config.yaml").write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
    (root / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in questions), encoding="utf-8")
    for name, text in corpus.items():
        (root / "corpus" / f"{name}.md").write_bytes(text.encode("utf-8"))
    return root


class SplitPassagesTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_runner()

    def test_split_passages_headings_blank_lines_and_spans(self):
        text = "# Title\nfirst para line one\nline two\n\n## Sub\n\npara two\n#### deep\nstill para two\n\n\n#"
        passages = self.runner.split_passages(text)
        self.assertEqual([p.text for p in passages], ["first para line one line two", "para two still para two"])
        self.assertEqual([p.section_heading for p in passages], ["Title", "Sub"])
        self.assertEqual([p.passage_index for p in passages], [0, 1])
        for passage in passages:
            raw = text[passage.char_start:passage.char_end]
            self.assertFalse(raw.startswith("\n") or raw.endswith("\n"))
        self.assertEqual(text[passages[0].char_start:passages[0].char_end], "first para line one\nline two")
        self.assertEqual(text[passages[1].char_start:passages[1].char_end], "para two\n#### deep\nstill para two")
        self.assertEqual(self.runner.split_passages("no headings\n\nsecond"), [
            self.runner.Passage("no headings", None, 0, 0, 11), self.runner.Passage("second", None, 1, 13, 19)])
        self.assertEqual(self.runner.split_passages(""), [])
        self.assertEqual(self.runner.split_passages("# only headings\n\n### and more\n"), [])


class BestPassageRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = load_runner()
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_policy(self, name: str, config: dict, questions=None, corpus=None) -> tuple[Path, list[dict], dict]:
        questions = questions or [{"question_id": "q1", "question": "sync waves order"}]
        corpus = corpus or {"deep": DEEP_DOC, "other": OTHER_DOC}
        input_dir = write_input(self.root / f"{name}-input", config, questions, corpus)
        output_dir = self.root / f"{name}-output"
        self.runner.main(input_dir, output_dir)
        rows = [json.loads(line) for line in (output_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        metadata = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
        return output_dir, rows, metadata

    def test_best_passage_selects_passage_outside_document_head(self):
        config = {"answer_policy": "best_passage_v1", "max_context_chars_per_doc": 1500, "top_k": 5}
        output_dir, rows, metadata = self.run_policy("deep", config)
        self.assertEqual(metadata["answer_policy"], "best_passage_v1")
        self.assertEqual(set(metadata), {"name", "deterministic", "ranker", "top_k", "max_context_chars_per_doc", "answer_policy"})
        row = rows[0]
        self.assertEqual(set(row), {"question_id", "question", "generated_answer", "retrieved_context", "answer_provenance"})
        self.assertEqual(row["retrieved_context"][0]["doc_id"], "deep")
        # Context text stays the head snippet (ranking projection unchanged).
        self.assertEqual(row["retrieved_context"][0]["text"], self.runner.answer_text(DEEP_DOC, max_chars=1500))
        best = "Sync waves order resources during an Argo CD sync; lower wave numbers apply first."
        self.assertIn(best, row["generated_answer"])
        self.assertTrue(row["generated_answer"].startswith(best))
        self.assertNotIn("filler sentence", row["generated_answer"])
        provenance = row["answer_provenance"]
        self.assertEqual(provenance[0]["doc_id"], "deep")
        self.assertEqual(provenance[0]["section_heading"], "Sync Waves")
        self.assertEqual(provenance[0]["passage_index"], 1)
        # Offsets index the text as the runner reads it (universal newlines).
        normalized = (self.root / "deep-input" / "corpus" / "deep.md").read_text(encoding="utf-8")
        raw = normalized[provenance[0]["char_start"]:provenance[0]["char_end"]]
        self.assertEqual(raw, "Sync waves order resources during an Argo CD sync;\nlower wave numbers apply first.")
        for entry in provenance:
            self.assertEqual(set(entry), {"doc_id", "section_heading", "passage_index", "char_start", "char_end"})
            self.assertLess(entry["char_start"], entry["char_end"])
            self.assertLessEqual(entry["char_end"], len(normalized) if entry["doc_id"] == "deep" else len(OTHER_DOC))
        # Zero-score passages (the intro, the hooks paragraph) are dropped once any passage scores > 0;
        # the "waves" paragraph of the rank-2 document follows in document order.
        self.assertEqual(row["generated_answer"], best + " A note about hooks and phases, never about waves.")
        self.assertEqual([(p["doc_id"], p["passage_index"], p["section_heading"]) for p in provenance],
                         [("deep", 1, "Sync Waves"), ("other", 0, "Other")])

    def test_zero_score_passages_kept_only_when_nothing_matches(self):
        question_tokens = self.runner.tokens("unrelated query terms")
        corpus = {"a": "# A\n\nfirst para\n\nsecond para\n\nthird para\n\nfourth para\n", "b": "b para\n"}
        answer, provenance = self.runner.best_passage_answer(question_tokens, ["a", "b"], corpus, 1500)
        self.assertEqual(answer, "first para second para third para")
        self.assertEqual([(p["doc_id"], p["passage_index"]) for p in provenance], [("a", 0), ("a", 1), ("a", 2)])
        self.assertEqual(self.runner.best_passage_answer(question_tokens, [], corpus, 1500), ("", []))
        self.assertEqual(self.runner.best_passage_answer(question_tokens, ["c"], {"c": "# heading only\n"}, 1500), ("", []))

    def test_document_order_concatenation_and_truncation(self):
        long_para = " ".join(f"word{index}" for index in range(400))  # > 1500 chars, no query terms
        corpus = {
            "first": f"# First\n\n{long_para}\n\ntarget alpha here.\n",
            "second": "# Second\n\ntarget beta here.\n\ntarget gamma target here.\n",
        }
        question_tokens = self.runner.tokens("target")
        answer, provenance = self.runner.best_passage_answer(question_tokens, ["second", "first"], corpus, 1500)
        # Best BM25 passage (two hits) is in doc rank 1; document order puts rank-1 passages first.
        self.assertEqual(answer, "target beta here. target gamma target here. target alpha here.")
        self.assertEqual([(p["doc_id"], p["passage_index"]) for p in provenance], [("second", 0), ("second", 1), ("first", 1)])
        # Five unique long-paragraph terms outrank the two-hit "target" passage; the long paragraph
        # (doc rank 1, index 0) leads the document-order concatenation and forces truncation.
        question_tokens = self.runner.tokens("word1 word2 word3 word4 word5 target")
        answer, provenance = self.runner.best_passage_answer(question_tokens, ["first", "second"], corpus, 1500)
        self.assertLessEqual(len(answer), 1500)
        self.assertEqual(answer, self.runner.truncate(long_para + " target alpha here. target gamma target here.", max_chars=1500))
        self.assertEqual(answer, (long_para + " target alpha here. target gamma target here.")[:1500].rsplit(" ", 1)[0].strip())
        self.assertEqual([(p["doc_id"], p["passage_index"]) for p in provenance], [("first", 0), ("first", 1), ("second", 1)])
        untruncated, _ = self.runner.best_passage_answer(question_tokens, ["first", "second"], corpus, 0)
        self.assertGreater(len(untruncated), 1500)

    def test_best_passage_runs_are_byte_identical(self):
        config = {"answer_policy": "best_passage_v1", "max_context_chars_per_doc": 1500, "top_k": 5}
        questions = [{"question_id": "q1", "question": "sync waves order"}, {"question_id": "q2", "question": "hooks phases"},
                     {"question_id": "q3", "question": "nothing matches xyzzy"}]
        first, _, _ = self.run_policy("run1", config, questions)
        second, _, _ = self.run_policy("run2", config, questions)
        first_bytes = (first / "predictions.jsonl").read_bytes()
        self.assertEqual(hashlib.sha256(first_bytes).hexdigest(), hashlib.sha256((second / "predictions.jsonl").read_bytes()).hexdigest())
        self.assertEqual((first / "metadata.json").read_bytes(), (second / "metadata.json").read_bytes())
        self.assertEqual(first_bytes.count(b"\n"), 3)
        self.assertNotIn(b"\r", first_bytes)

    def test_head_of_document_policy_reproduces_legacy_rows(self):
        questions = [{"question_id": "q1", "question": "sync waves order"}, {"question_id": "q2", "question": "hooks phases"},
                     {"question_id": "q3", "question": "nothing matches xyzzy"}]
        corpus = {"deep": DEEP_DOC, "other": OTHER_DOC, "long": "# Long\n\n" + " ".join(f"tok{i} waves" for i in range(300)) + "\n"}
        for name, config in (("explicit", {"answer_policy": "head_of_document_v1", "max_context_chars_per_doc": 1500, "top_k": 5}),
                             ("absent", {"max_context_chars_per_doc": 1500, "top_k": 5}),
                             ("empty", {})):
            output_dir, rows, metadata = self.run_policy(name, config, questions, corpus)
            expected_text, expected_metadata = legacy_predictions(self.root / f"{name}-input")
            self.assertEqual((output_dir / "predictions.jsonl").read_text(encoding="utf-8"), expected_text, name)
            self.assertEqual(metadata, {**expected_metadata, "answer_policy": "head_of_document_v1"}, name)
            for row in rows:
                self.assertEqual(set(row), {"question_id", "question", "generated_answer", "retrieved_context"})

    def test_context_items_identical_across_policies(self):
        legacy_dir, legacy_rows, _ = self.run_policy("legacy", {"max_context_chars_per_doc": 1500, "top_k": 5})
        best_dir, best_rows, _ = self.run_policy("best", {"answer_policy": "best_passage_v1", "max_context_chars_per_doc": 1500, "top_k": 5})
        self.assertEqual([row["retrieved_context"] for row in legacy_rows], [row["retrieved_context"] for row in best_rows])
        self.assertNotEqual(legacy_rows[0]["generated_answer"], best_rows[0]["generated_answer"])

    def test_unknown_answer_policy_is_rejected(self):
        input_dir = write_input(self.root / "bad-input", {"answer_policy": "whole_document_v9"},
                                [{"question_id": "q1", "question": "sync"}], {"deep": DEEP_DOC})
        with self.assertRaisesRegex(ValueError, "answer_policy"):
            self.runner.main(input_dir, self.root / "bad-output")


if __name__ == "__main__":
    unittest.main()
