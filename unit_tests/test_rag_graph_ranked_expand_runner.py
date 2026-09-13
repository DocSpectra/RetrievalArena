"""Unit tests for tests/rag_graph_ranked_expand/runner.py (S025 provider generalization)."""

from __future__ import annotations

import ast
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "tests" / "rag_graph_ranked_expand" / "runner.py"
LEXICAL_RUNNER_PATH = ROOT / "tests" / "rag_lexical_topk" / "runner.py"
SHARED_FUNCTIONS = ("Passage", "read_jsonl", "read_config", "tokens", "truncate", "answer_text",
                    "split_passages", "head_of_document_answer", "best_passage_answer")


def load_runner(path: Path = RUNNER_PATH):
    spec = importlib.util.spec_from_file_location(path.parent.name + "_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def definitions(path: Path) -> dict[str, str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return {node.name: ast.get_source_segment(source, node)
            for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}


class GraphRankedExpandRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.input = Path(self.temp.name) / "input"
        self.output = Path(self.temp.name) / "output"
        corpus = self.input / "corpus"
        corpus.mkdir(parents=True)
        (corpus / "a__00000001.md").write_text("# Alpha\nsync waves order resources.\n\nHooks run per wave.\n", encoding="utf-8")
        (corpus / "b__00000002.md").write_text("# Beta\nsync waves again and again.\n", encoding="utf-8")
        (corpus / "c__00000003.md").write_text("# Gamma\nnothing relevant here.\n\nwaves mentioned once.\n", encoding="utf-8")
        (corpus / "d__00000004.md").write_text("# Delta\nunrelated page about hooks and nothing else.\n", encoding="utf-8")
        (corpus / "e__00000005.md").write_text("# Epsilon\nisolated page.\n", encoding="utf-8")
        (self.input / "graph_edges.csv").write_text(
            "source,target\na__00000001,c__00000003\nd__00000004,b__00000002\n", encoding="utf-8")
        (self.input / "questions.jsonl").write_text(json.dumps({"question_id": "q1", "question": "sync waves"}) + "\n",
                                                   encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def config(self, **overrides):
        value = {"top_k": 3, "seed_top_k": 2, "neighbor_budget": 1, "graph_expansion_hops": 1,
                 "max_context_chars_per_doc": 1500, "answer_policy": "best_passage_v1", **overrides}
        (self.input / "config.yaml").write_text(json.dumps(value), encoding="utf-8")
        return value

    def run_runner(self):
        load_runner().main(self.input, self.output)
        rows = [json.loads(line) for line in (self.output / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        return rows, json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))

    def test_seeds_then_ranked_one_hop_neighbours_within_the_top_k_budget(self):
        self.config()
        rows, metadata = self.run_runner()
        context = rows[0]["retrieved_context"]
        self.assertEqual([(item["doc_id"], item["selection_role"], item["rank"]) for item in context],
                         [("b__00000002", "seed", 1), ("a__00000001", "seed", 2), ("c__00000003", "ranked_neighbor", 3)])
        self.assertTrue(all(item["source"] == "graph_ranked_expand" for item in context))
        self.assertEqual(metadata, {"name": "rag_graph_ranked_expand", "deterministic": True, "seed_ranker": "bm25",
                                    "neighbor_ranker": "bm25", "graph_expansion_hops": 1, "top_k": 3, "seed_top_k": 2,
                                    "neighbor_budget": 1, "max_context_chars_per_doc": 1500,
                                    "answer_policy": "best_passage_v1"})
        self.assertEqual(rows[0]["generated_answer"],
                         "sync waves again and again. sync waves order resources. waves mentioned once.")
        self.assertEqual([(p["doc_id"], p["passage_index"], p["section_heading"]) for p in rows[0]["answer_provenance"]],
                         [("b__00000002", 0, "Beta"), ("a__00000001", 0, "Alpha"), ("c__00000003", 1, "Gamma")])
        self.assertEqual(set(rows[0]), {"question_id", "question", "generated_answer", "retrieved_context", "answer_provenance"})

    def test_head_of_document_policy_and_no_graph(self):
        self.config(answer_policy="head_of_document_v1")
        (self.input / "graph_edges.csv").unlink()
        rows, metadata = self.run_runner()
        self.assertEqual([item["doc_id"] for item in rows[0]["retrieved_context"]], ["b__00000002", "a__00000001"])
        self.assertEqual(rows[0]["generated_answer"], "sync waves again and again. sync waves order resources. Hooks run per wave.")
        self.assertNotIn("answer_provenance", rows[0])
        self.assertEqual(metadata["answer_policy"], "head_of_document_v1")

    def test_fixed_knobs_are_enforced(self):
        runner = load_runner()
        self.assertEqual(runner.resolve_budgets({"top_k": 5}), (3, 2, 5))
        self.assertEqual(runner.resolve_budgets({"seed_top_k": 3, "neighbor_budget": 2}), (3, 2, 5))
        self.assertEqual(runner.resolve_budgets({}), (3, 2, 5))
        with self.assertRaisesRegex(ValueError, "graph_expansion_hops"):
            runner.resolve_budgets({"top_k": 5, "graph_expansion_hops": 2})
        with self.assertRaisesRegex(ValueError, "top_k"):
            runner.resolve_budgets({"top_k": 5, "seed_top_k": 5, "neighbor_budget": 2})
        self.config(answer_policy="whole_document_v9")
        with self.assertRaisesRegex(ValueError, "answer_policy"):
            runner.main(self.input, self.output)

    def test_passage_functions_are_byte_identical_to_the_lexical_runner(self):
        graph, lexical = definitions(RUNNER_PATH), definitions(LEXICAL_RUNNER_PATH)
        for name in SHARED_FUNCTIONS:
            self.assertEqual(graph[name], lexical[name], name)
        for name in ("TOKEN_RE", "HEADING_RE", "ANSWER_POLICIES", "DEFAULT_ANSWER_POLICY", "BEST_PASSAGE_COUNT"):
            self.assertEqual(getattr(load_runner(), name), getattr(load_runner(LEXICAL_RUNNER_PATH), name), name)


if __name__ == "__main__":
    unittest.main()
