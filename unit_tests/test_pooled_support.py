"""Pooled, LLM-verified support: windows, generators, pool, verifier statuses, qrels, review, incremental. Offline."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from retrieval_arena.errors import ValidationError
from retrieval_arena.model_providers import SpendStop
from retrieval_arena.ranked_scoring import validate_ranked_inputs
from retrieval_arena.support import pooling, review, verification
from unit_tests.test_ranked_scoring import fixture as ranked_fixture

VOCAB = ["alpha", "beta", "gamma", "delta", "answer", "token", "widget", "config", "network", "volume"]


def encoder(texts):
    """Deterministic bag-of-words embedding over a small vocabulary, L2-normalized."""
    rows = []
    for t in texts:
        words = t.lower().split()
        v = np.array([words.count(w) for w in VOCAB], dtype=float) + 1e-3
        rows.append(v / np.linalg.norm(v))
    return np.array(rows)


def verifier_config(**over):
    config = {
        "schema_version": verification.SCHEMA_VERIFIER_CONFIG, "prompt_version": verification.PROMPT_VERSION,
        "provider": "anthropic", "model": "claude-haiku-5-5", "revision": "claude-haiku-5-5",
        "temperature": None, "seed": None, "thinking": False,
        "generation": {"thinking": "disabled", "effort": "low"},
        "pricing": {"currency": "USD", "input_per_mtok": 0.1, "output_per_mtok": 0.5, "long_context": None, "source": "test"},
        "spend_cap_usd": 1.0, "max_input_tokens": 20000, "max_output_tokens": 512,
        "retries": 1, "concurrency": 3, "timeout_seconds": 30,
    }
    config.update(over)
    return config


class FakeTransport:
    """Says `answers` for a passage line with ANSWER and the reference answer's first word, quoting it; else `not_answer`."""

    def __init__(self, stop_after=None, malformed=()):
        self.calls, self.stop_after, self.malformed = 0, stop_after, set(malformed)

    async def __call__(self, prompt, schema, config):
        if self.stop_after is not None and self.calls >= self.stop_after:
            raise SpendStop("spend-cap-reached")
        self.calls += 1
        passage = prompt.split("<passage>\n", 1)[1].rsplit("\n</passage>", 1)[0]
        if any(m in passage for m in self.malformed):
            return {"text": "not json", "model": config["model"], "revision": config["revision"], "usage": {}, "cost": 0.0}
        first = prompt.split("<reference_answer>", 1)[1].split()[0]
        hit = next((line for line in passage.splitlines() if "ANSWER" in line and first in line), None)
        body = {"verdict": "answers", "supporting_span": hit.strip(), "reason": "states it"} if hit else \
               {"verdict": "not_answer", "supporting_span": "", "reason": "off topic"}
        await asyncio.sleep(0)
        return {"text": json.dumps(body), "model": config["model"], "revision": config["revision"], "usage": {"input_tokens": 10}, "cost": 1e-6}


def corpus_files(root: Path) -> None:
    pages = {
        "guide/alpha": ["# Alpha", "alpha widget config", "The ANSWER is alpha beta gamma.", "more alpha"] + [f"line {i}" for i in range(20)],
        "guide/beta": ["# Beta", "beta network volume", "nothing here"] + [f"beta line {i}" for i in range(9)],
        "notes/long": ["release notes"] + [f"token widget {i}" for i in range(40)] + ["ANSWER for gamma delta here"],
        "notes/other": ["unrelated", "", "  ", "widget only"],
    }
    for doc, lines in pages.items():
        path = root / "corpus" / (doc + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    queries = [
        {"question_id": "q1", "question": "What is alpha?", "answer": "alpha beta gamma widget"},
        {"question_id": "q2", "question": "What about gamma?", "answer": "gamma delta token"},
        {"question_id": "q3", "question": "Volumes?", "answer": "network volume beta"},
    ]
    pooling.write_jsonl(root / "queries.jsonl", queries)
    runs = root / "runs"
    runs.mkdir()
    pooling.write_jsonl(runs / "lexical.jsonl", [
        {"question_id": "q1", "retrieved_context": [{"doc_id": "notes/other", "rank": 1}, {"doc_id": "missing/page", "rank": 2}]},
        {"question_id": "q2", "retrieved_context": [{"doc_id": "guide/beta", "rank": 1}]},
        {"question_id": "q3", "retrieved_context": []},
    ])
    config = {"schema_version": pooling.SCHEMA_POOL_CONFIG, "pool_id": "t", "corpus_id": "fixture", "generator_depth": 2,
              "run_depth": 10, "window_lines": 12, "stride_lines": 6, "windows_per_generator": 2, "query_field": "answer",
              "runs": [{"run_id": "lexical", "path": "lexical.jsonl", "format": "legacy_predictions"}],
              "embedding_model_id": "fake-bow"}
    (root / "pool_config.json").write_text(json.dumps(config), encoding="utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        corpus_files(self.root)
        self.queries = pooling.read_jsonl(self.root / "queries.jsonl")
        self.pool_dir = self.root / "pool"
        self.manifest = pooling.pool_files(self.root / "pool_config.json", self.root / "corpus", self.root / "queries.jsonl",
                                           self.root / "runs", None, self.pool_dir, encoder=encoder)

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, out, transport=None, config=None, prior=None, query_ids=None, limit=None):
        run = verification.VerifyRun(self.pool_dir, self.queries, config or verifier_config(), self.root / out,
                                     transport or FakeTransport(), prior_dir=prior, query_ids=query_ids)
        stats = asyncio.run(run.run(limit=limit))
        return run, stats, verification.write_qrels(run)


class WindowTests(unittest.TestCase):
    def test_window_boundaries(self):
        docs = {"a": "\n".join(f"l{i}" for i in range(1, 31)), "short": "x\ny", "blank": "\n\n  \n"}
        wins = pooling.make_windows(docs, 12, 6)
        a = [(w["start_line"], w["end_line"]) for w in wins if w["doc_id"] == "a"]
        self.assertEqual(a, [(1, 12), (7, 18), (13, 24), (19, 30)])
        self.assertEqual([(w["start_line"], w["end_line"]) for w in wins if w["doc_id"] == "short"], [(1, 2)])
        self.assertFalse([w for w in wins if w["doc_id"] == "blank"])
        self.assertEqual(wins[0]["window_id"], "a#L1-12")
        with self.assertRaises(ValidationError):
            pooling.make_windows(docs, 6, 12)

    def test_bm25_determinism_and_ties(self):
        docs = {"z": "kubernetes volume", "a": "kubernetes volume", "m": "other text"}
        wins = pooling.make_windows(docs, 12, 6)
        index = pooling.BM25Index(wins)
        first = pooling.rank_pages(index.scores("volume kubernetes"), wins, 5)
        again = pooling.rank_pages(pooling.BM25Index(wins).scores("kubernetes volume"), wins, 5)
        self.assertEqual(first, again)
        self.assertEqual([d for d, _ in first], ["a", "z"])  # equal scores: doc_id ascending; zero scores dropped

    def test_embedding_ranking_deterministic(self):
        docs = {"p": "alpha alpha", "q": "beta", "r": "alpha beta"}
        wins = pooling.make_windows(docs, 12, 6)
        s1 = pooling.embedding_scores(wins, ["alpha"], encoder)
        s2 = pooling.embedding_scores(wins, ["alpha"], encoder)
        self.assertEqual(s1, s2)
        self.assertEqual(pooling.rank_pages(s1[0], wins, 3)[0][0], "p")


class PoolTests(Base):
    def test_pool_union_and_provenance(self):
        rows = pooling.read_jsonl(self.pool_dir / "pool.jsonl")
        by = {(r["query_id"], r["doc_id"]): r for r in rows}
        # run-only page enters with its run rank and still gets windows from both generators
        other = by[("q1", "notes/other")]
        self.assertEqual(other["sources"].get("run:lexical"), 1)
        self.assertTrue(other["window_ids"])
        # unknown run page is counted, not pooled
        self.assertEqual(self.manifest["summary"]["unknown_run_pages"], {"lexical": 1})
        self.assertNotIn(("q1", "missing/page"), by)
        for r in rows:
            self.assertTrue(set(r["sources"]) <= {"bm25_passage", "embedding_passage", "run:lexical"})
            self.assertLessEqual(len(r["window_ids"]), 4)
            self.assertEqual(len(r["window_ids"]), len(set(r["window_ids"])))
        for gen in ("bm25_passage", "embedding_passage"):
            for q in ("q1", "q2", "q3"):
                ranks = sorted(r["sources"][gen] for r in rows if r["query_id"] == q and gen in r["sources"])
                self.assertEqual(ranks, list(range(1, len(ranks) + 1)))
                self.assertLessEqual(len(ranks), 2)  # generator_depth
        windows = {w["window_id"] for w in pooling.read_jsonl(self.pool_dir / "windows.jsonl")}
        self.assertTrue({w for r in rows for w in r["window_ids"]} == windows)
        # rebuilding gives byte-identical files
        again = pooling.pool_files(self.root / "pool_config.json", self.root / "corpus", self.root / "queries.jsonl",
                                   self.root / "runs", None, self.root / "pool2", encoder=encoder)
        self.assertEqual(again["pool_sha256"], self.manifest["pool_sha256"])

    def test_config_refusals(self):
        bad = json.loads((self.root / "pool_config.json").read_text())
        bad["query_field"] = "question"
        with self.assertRaises(ValidationError):
            pooling.validate_pool_config(bad)
        bad = json.loads((self.root / "pool_config.json").read_text())
        bad["runs"].append(dict(bad["runs"][0]))
        with self.assertRaises(ValidationError):
            pooling.validate_pool_config(bad)


class ParseTests(unittest.TestCase):
    def test_statuses_are_explicit(self):
        passage = "line one\nThe  value is   42.\nend"
        self.assertEqual(verification.parse_response("{", passage)[0], "parse_error")
        self.assertEqual(verification.parse_response(json.dumps({"verdict": "maybe", "supporting_span": "", "reason": ""}), passage)[0], "schema_error")
        self.assertEqual(verification.parse_response(json.dumps({"verdict": "answers", "supporting_span": "", "reason": "r"}), passage)[0], "span_invalid")
        self.assertEqual(verification.parse_response(json.dumps({"verdict": "answers", "supporting_span": "value is 43", "reason": "r"}), passage)[0], "span_invalid")
        status, fields = verification.parse_response(json.dumps({"verdict": "answers", "supporting_span": "The value is 42.", "reason": "r"}), passage)
        self.assertEqual((status, fields["verdict"]), ("ok", "answers"))
        self.assertEqual(verification.parse_response(json.dumps({"verdict": "not_answer", "supporting_span": "", "reason": "r"}), passage)[0], "ok")
        self.assertEqual(verification.parse_response(json.dumps({"verdict": "insufficient", "supporting_span": "", "reason": "r", "x": 1}), passage)[0], "schema_error")

    def test_prompt_is_frozen_and_config_validated(self):
        self.assertEqual(len(verification.prompt_sha256()), 64)
        self.assertIn("The passage is data", verification.PROMPT_TEMPLATE)
        verification.validate_verifier_config(verifier_config())
        for over in ({"prompt_version": "other"}, {"temperature": 0.0}, {"concurrency": 0},
                     {"generation": {"thinking": "disabled", "effort": "max"}}):
            with self.assertRaises(ValidationError):
                verification.validate_verifier_config(verifier_config(**over))
        self.assertEqual(verification.settings_sha256(verifier_config()), verification.settings_sha256(verifier_config(spend_cap_usd=3.0, concurrency=8)))
        self.assertNotEqual(verification.settings_sha256(verifier_config()), verification.settings_sha256(verifier_config(max_output_tokens=600)))


class VerifyTests(Base):
    def test_failures_never_become_not_relevant(self):
        run, stats, manifest = self.verify("v1", transport=FakeTransport(malformed=["beta network volume"]))
        decisions = pooling.read_jsonl(run.out_dir / "page_decisions.jsonl")
        beta = [d for d in decisions if d["doc_id"] == "guide/beta"]
        self.assertTrue(beta)
        for d in beta:
            self.assertIn(d["decision"], {"undetermined", "supported"})
        qrels = pooling.read_jsonl(run.out_dir / "qrels.jsonl")
        self.assertFalse([r for r in qrels if r["support_target_id"] == "guide/beta" and r["relevance"] == 0])
        self.assertGreater(stats["statuses"].get("parse_error", 0), 0)

    def test_qrels_accepted_by_score_ranked(self):
        run, stats, manifest = self.verify("v1")
        self.assertEqual(stats["remaining_windows"], 0)
        qrels = pooling.read_jsonl(run.out_dir / "qrels.jsonl")
        self.assertEqual(manifest["ordered_query_ids"], ["q1", "q2"])  # q3 has no verified page: dropped and reported
        self.assertEqual(manifest["counts"]["queries_dropped_no_verified_page"], ["q3"])
        dataset, _, _, policy = ranked_fixture("pooled")
        dataset.update(verification.ranked_dataset_fields(manifest))
        self.assertEqual(manifest["qrels_sha256"], hashlib.sha256((run.out_dir / "qrels.jsonl").read_bytes()).hexdigest())

        def prediction(qid, targets):
            return {"schema_version": "retrieval_arena.ranked_prediction.v2", "query_id": qid, "context_item_budget_k": 2,
                    "max_rank": len(targets), "retrieval_exhausted": True, "duplicate_collapse_count": 0,
                    "duplicate_collapse_sha256": None,
                    "items": [{"support_target_id": t, "rank": i, "score": float(10 - i), "source_rank": i,
                               "source_item_id": t, "source_provenance_sha256": "0" * 64} for i, t in enumerate(targets, 1)]}
        preds = [prediction("q1", ["guide/alpha", "never/pooled"]), prediction("q2", ["notes/long"])]
        by_query, _ = validate_ranked_inputs(dataset, qrels, preds, policy)
        self.assertEqual(by_query["q1"]["guide/alpha"], 1)
        self.assertNotIn("never/pooled", by_query["q1"])  # unjudged, not zero
        self.assertTrue(all(set(r) == {"query_id", "support_target_id", "relevance", "judgment_id", "judgment_provenance_sha256"} for r in qrels))

    def test_resume_after_spend_stop(self):
        run, stats, _ = self.verify("v1", transport=FakeTransport(stop_after=2))
        self.assertTrue(stats["spend_stopped"])
        self.assertGreater(stats["remaining_windows"], 0)
        decisions = {d["decision"] for d in pooling.read_jsonl(run.out_dir / "page_decisions.jsonl")}
        self.assertIn("unjudged", decisions)
        run2, stats2, manifest2 = self.verify("v1")  # same out_dir: resume
        self.assertEqual(stats2["remaining_windows"], 0)
        self.assertEqual(stats2["attempted_windows"], stats["remaining_windows"])
        self.assertNotIn("unjudged", manifest2["counts"]["decisions"])

    def test_resume_refuses_a_different_run(self):
        self.verify("v1", query_ids=["q1"])
        with self.assertRaises(ValidationError):
            self.verify("v1", query_ids=["q2"])

    def test_incremental_skips_judged_and_keeps_bytes(self):
        run1, _, m1 = self.verify("v1", query_ids=["q1"])
        prior_files = {p.name: p.read_bytes() for p in run1.out_dir.iterdir()}
        counter = FakeTransport()
        run2, stats2, m2 = self.verify("v2", transport=counter, prior=run1.out_dir)
        judged_q1 = {(r["query_id"], r["doc_id"]) for r in pooling.read_jsonl(run1.out_dir / "judgments.jsonl")}
        new_rows = pooling.read_jsonl(run2.out_dir / "judgments.jsonl")[len(pooling.read_jsonl(run1.out_dir / "judgments.jsonl")):]
        self.assertFalse({(r["query_id"], r["doc_id"]) for r in new_rows} & judged_q1)
        self.assertEqual(counter.calls, len(new_rows))
        self.assertTrue((run2.out_dir / "judgments.jsonl").read_bytes().startswith(prior_files["judgments.jsonl"]))
        self.assertEqual({p.name: p.read_bytes() for p in run1.out_dir.iterdir()}, prior_files)  # prior untouched
        self.assertEqual(m2["qrels_version"], 2)
        old = {(d["query_id"], d["doc_id"]): d for d in pooling.read_jsonl(run1.out_dir / "page_decisions.jsonl")}
        new = {(d["query_id"], d["doc_id"]): d for d in pooling.read_jsonl(run2.out_dir / "page_decisions.jsonl")}
        self.assertTrue(all(new[k] == v for k, v in old.items()))
        with self.assertRaises(ValidationError):  # changed settings are refused
            self.verify("v3", config=verifier_config(max_output_tokens=600), prior=run2.out_dir)
        prompt = verification.PROMPT_TEMPLATE
        try:
            verification.PROMPT_TEMPLATE = prompt + " "
            with self.assertRaises(ValidationError):  # changed prompt is refused
                self.verify("v4", prior=run2.out_dir)
        finally:
            verification.PROMPT_TEMPLATE = prompt

    def test_changed_page_content_is_unjudged(self):
        run1, _, _ = self.verify("v1")
        page = self.root / "corpus" / "guide" / "alpha.md"
        page.write_text(page.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
        self.pool_dir = self.root / "pool-edited"
        pooling.pool_files(self.root / "pool_config.json", self.root / "corpus", self.root / "queries.jsonl",
                           self.root / "runs", None, self.pool_dir, encoder=encoder)
        counter = FakeTransport()
        run2, _, m2 = self.verify("v2", transport=counter, prior=run1.out_dir)
        new_docs = {r["doc_id"] for r in pooling.read_jsonl(run2.out_dir / "judgments.jsonl")[len(pooling.read_jsonl(run1.out_dir / "judgments.jsonl")):]}
        self.assertEqual(new_docs, {"guide/alpha"})
        qrels = pooling.read_jsonl(run2.out_dir / "qrels.jsonl")
        self.assertEqual(sum(1 for r in qrels if r["query_id"] == "q1" and r["support_target_id"] == "guide/alpha"), 1)


class ReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.run, _, self.qm = self.verify("v1")

    def packet(self, n=1, seed="s1"):
        return review.build_packet(self.run.out_dir, self.pool_dir, self.queries, n, seed, "pk")

    def test_stratified_seeded_and_blind(self):
        p1, k1 = self.packet()
        p2, k2 = self.packet()
        self.assertEqual(p1, p2)
        strata = [k1["items"][i["item_id"]]["stratum"] for i in p1["items"]]
        self.assertEqual(sorted(strata), ["verifier_no", "verifier_yes"])
        blob = json.dumps(p1)
        self.assertNotIn("verifier_yes", blob)
        self.assertNotIn("supported", blob)
        for item in p1["items"]:
            self.assertEqual(set(item), {"item_id", "query_id", "question", "reference_answer", "doc_id", "window_id", "passage"})
        with self.assertRaises(ValidationError):
            self.packet(n=100)

    def test_round_trip_and_rates(self):
        packet, key = self.packet()
        yes_item = next(i for i, v in key["items"].items() if v["stratum"] == "verifier_yes")
        no_item = next(i for i, v in key["items"].items() if v["stratum"] == "verifier_no")
        responses = {"schema_version": review.SCHEMA_RESPONSES, "packet_id": "pk", "reviewer": "author",
                     "responses": [{"item_id": yes_item, "response": "yes"}, {"item_id": no_item, "response": "yes"}]}
        result = review.import_responses(packet, key, responses)
        self.assertEqual(result["precision"]["estimate"], 1.0)
        self.assertEqual(result["miss_rate"]["estimate"], 1.0)
        self.assertLess(result["precision"]["low"], 1.0)
        self.assertEqual(len(result["disagreements"]), 1)
        bad = copy.deepcopy(responses)
        bad["responses"].append({"item_id": yes_item, "response": "no"})
        with self.assertRaises(ValidationError):
            review.import_responses(packet, key, bad)
        bad = copy.deepcopy(responses)
        bad["responses"][0]["item_id"] = "unknown"
        with self.assertRaises(ValidationError):
            review.import_responses(packet, key, bad)
        unsure = copy.deepcopy(responses)
        unsure["responses"][0]["response"] = "unsure"
        result = review.import_responses(packet, key, unsure)
        self.assertIsNone(result["precision"]["estimate"])
        self.assertEqual(result["unsure"]["verifier_yes"], 1)

    def test_wilson(self):
        w = review.wilson(35, 35)
        self.assertEqual(w["estimate"], 1.0)
        self.assertAlmostEqual(w["low"], 0.9011, places=3)
        self.assertIsNone(review.wilson(0, 0)["estimate"])


if __name__ == "__main__":
    unittest.main()
