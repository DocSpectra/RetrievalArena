from __future__ import annotations

import copy
import hashlib
import json
import math
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from retrieval_arena.errors import ValidationError
from retrieval_arena.cli import main
from retrieval_arena.ranked_scoring import ENGINE_VERSION, score_ranked


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fixture(scope: str = "complete"):
    qrels = [
        {"query_id": "q1", "support_target_id": "a", "relevance": 1, "judgment_id": "j1", "judgment_provenance_sha256": sha("j1")},
        {"query_id": "q1", "support_target_id": "b", "relevance": 1, "judgment_id": "j2", "judgment_provenance_sha256": sha("j2")},
        {"query_id": "q1", "support_target_id": "x", "relevance": 0, "judgment_id": "j3", "judgment_provenance_sha256": sha("j3")},
        {"query_id": "q2", "support_target_id": "c", "relevance": 1, "judgment_id": "j4", "judgment_provenance_sha256": sha("j4")},
        {"query_id": "q2", "support_target_id": "y", "relevance": 0, "judgment_id": "j5", "judgment_provenance_sha256": sha("j5")},
    ]
    dataset = {
        "schema_version": "retrieval_arena.ranked_dataset.v2", "dataset_id": "d", "corpus_id": "c",
        "query_set_id": "qs", "support_surface_id": "ss", "corpus_snapshot_id": "cs",
        "graph_snapshot_id": "gs", "ranked_item_granularity": "support_target", "qrels_scope": scope,
        "relevance_scale": "binary", "judgment_policy_id": "jp", "judgment_provenance_sha256": sha("jp"),
        "ordered_query_ids": ["q1", "q2"], "support_group_by_query": {"q1": "g1", "q2": "g2"},
        "qrels_sha256": sha("qrels"), "dataset_manifest_sha256": sha("dataset"),
    }
    def prediction(qid, targets, exhausted=False):
        return {
            "schema_version": "retrieval_arena.ranked_prediction.v2", "query_id": qid,
            "context_item_budget_k": 2, "max_rank": len(targets), "retrieval_exhausted": exhausted,
            "items": [
                {"support_target_id": target, "rank": rank, "score": float(10-rank), "source_rank": rank,
                 "source_item_id": f"chunk-{target}", "source_provenance_sha256": sha(f"source-{target}")}
                for rank, target in enumerate(targets, 1)
            ],
            "duplicate_collapse_count": 0, "duplicate_collapse_sha256": None,
        }
    predictions = [prediction("q1", ["a", "x"]), prediction("q2", ["c", "y"])]
    policy = {
        "schema_version": "retrieval_arena.ranked_metric_policy.v2", "policy_id": "p",
        "primary_metric": "evidence_recall", "secondary_metrics": ["complete_support", "ndcg", "reciprocal_rank"],
        "context_item_budget_k": 2, "gain": "identity", "discount": "log2_rank_plus_one",
        "aggregation": "query_macro", "uncertainty_unit": "connected_support_group", "qrels_scope": scope,
        "unjudged_policy": "unavailable_ndcg", "tie_policy": "score_desc_target_id_asc", "duplicate_policy": "reject",
        "engine_name": "ir_measures", "engine_version": ENGINE_VERSION,
        "engine_artifact_sha256": "3d8b3b5283e4a77df85f9f6c8a1c2c99a7421b78fa014ef97f2ddab3037d3dcf",
        "provider_name": "pytrec_eval", "provider_version": "0.5.10",
        "parity_oracle_name": "trec_eval", "parity_oracle_version": "9.0.8",
        "parity_corpus_sha256": sha("parity"), "policy_sha256": sha("policy"),
    }
    return dataset, qrels, predictions, policy


class RankedScoringTests(unittest.TestCase):
    def test_partial_and_perfect_multi_support_scores(self):
        dataset, qrels, predictions, policy = fixture()
        scores, aggregate = score_ranked(dataset, qrels, predictions, policy)
        self.assertEqual(scores[0]["evidence_recall"], 0.5)
        self.assertEqual(scores[0]["complete_support"], 0.0)
        self.assertEqual(scores[1]["evidence_recall"], 1.0)
        self.assertEqual(scores[1]["complete_support"], 1.0)
        self.assertEqual(aggregate["macro_evidence_recall"], 0.75)
        self.assertEqual(aggregate["macro_complete_support"], 0.5)

    def test_independent_formula_oracles_for_ndcg_and_rr(self):
        dataset, qrels, predictions, policy = fixture()
        scores, _ = score_ranked(dataset, qrels, predictions, policy)
        expected_q1_ndcg = 1.0 / (1.0 + 1.0 / math.log2(3))
        self.assertAlmostEqual(scores[0]["ndcg"], expected_q1_ndcg)
        self.assertEqual(scores[0]["reciprocal_rank"], 1.0)

    def test_pooled_unjudged_retains_recall_but_disables_ndcg(self):
        dataset, qrels, predictions, policy = fixture("pooled")
        predictions[0]["items"][1]["support_target_id"] = "unjudged"
        scores, aggregate = score_ranked(dataset, qrels, predictions, policy)
        self.assertEqual(scores[0]["evidence_recall"], 0.5)
        self.assertIsNone(scores[0]["ndcg"])
        self.assertEqual(scores[0]["unavailable_metrics"], {"ndcg": "unjudged_top_k"})
        self.assertEqual(aggregate["available_counts"]["ndcg"], 1)

    def test_zero_relevant_missing_duplicate_tie_truncation_and_nan_reject(self):
        mutations = []
        dataset, qrels, predictions, policy = fixture()
        no_positive = copy.deepcopy(qrels)
        for row in no_positive:
            if row["query_id"] == "q1": row["relevance"] = 0
        mutations.append((dataset, no_positive, predictions, policy, "zero_relevant_items"))
        mutations.append((dataset, qrels, predictions[:-1], policy, "predictions must exactly"))
        duplicate = copy.deepcopy(predictions); duplicate[0]["items"][1]["support_target_id"] = "a"
        mutations.append((dataset, qrels, duplicate, policy, "duplicate canonical"))
        reversed_run = copy.deepcopy(predictions); reversed_run[0]["items"][0]["score"] = 1.0; reversed_run[0]["items"][1]["score"] = 2.0
        mutations.append((dataset, qrels, reversed_run, policy, "tie/order"))
        short = copy.deepcopy(predictions); short[0]["items"] = short[0]["items"][:1]; short[0]["max_rank"] = 1
        mutations.append((dataset, qrels, short, policy, "truncated_run"))
        nan_run = copy.deepcopy(predictions); nan_run[0]["items"][0]["score"] = float("nan")
        mutations.append((dataset, qrels, nan_run, policy, "rank or score"))
        for values in mutations:
            with self.subTest(message=values[4]), self.assertRaisesRegex(ValidationError, values[4]):
                score_ranked(*values[:4])

    def test_exhausted_short_run_is_valid(self):
        dataset, qrels, predictions, policy = fixture()
        predictions[0]["items"] = predictions[0]["items"][:1]
        predictions[0]["max_rank"] = 1
        predictions[0]["retrieval_exhausted"] = True
        scores, _ = score_ranked(dataset, qrels, predictions, policy)
        self.assertEqual(scores[0]["retrieved_count"], 1)

    def test_file_ingress_is_byte_stable(self):
        dataset, qrels, predictions, policy = fixture()
        scratch = Path(".tmp_unit_tests")
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as root:
            root = Path(root)
            for name, rows in (("qrels.jsonl", qrels), ("predictions.jsonl", predictions)):
                (root / name).write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
            dataset["qrels_sha256"] = hashlib.sha256((root / "qrels.jsonl").read_bytes()).hexdigest()
            for name, value in (("dataset.json", dataset), ("policy.json", policy)):
                (root / name).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
            hashes = []
            for output in (root / "one", root / "two"):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    exit_code = main([
                        "score-ranked", "--dataset", str(root / "dataset.json"), "--qrels", str(root / "qrels.jsonl"),
                        "--predictions", str(root / "predictions.jsonl"), "--policy", str(root / "policy.json"),
                        "--out-dir", str(output),
                    ])
                self.assertEqual(exit_code, 0)
                self.assertIn('"macro_evidence_recall": 0.75', stdout.getvalue())
                hashes.append({path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()})
            self.assertEqual(hashes[0], hashes[1])


if __name__ == "__main__":
    unittest.main()
