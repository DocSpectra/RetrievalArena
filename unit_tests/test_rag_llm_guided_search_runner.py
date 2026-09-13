"""Unit tests for tests/rag_llm_guided_search/runner.py and scripts/navigation_metrics.py (S026)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "tests" / "rag_llm_guided_search" / "runner.py"
METRICS_PATH = ROOT / "scripts" / "navigation_metrics.py"
LEXICAL_RUNNER_PATH = ROOT / "tests" / "rag_lexical_topk" / "runner.py"
DIGEST = "sha256:" + "b" * 64
POLICY = {"backend": "ollama", "model": "qwen3:14b", "digest": DIGEST, "temperature": 0.2, "top_p": 0.9,
          "seed": 391960843, "context_window_tokens": 8192, "max_output_tokens": 1024, "timeout_seconds": 300,
          "max_attempts": 1, "endpoint": "http://host.docker.internal:11434", "thinking": False}
CONFIG = {"top_k": 5, "max_context_chars_per_doc": 1500, "answer_policy": "agent_answer_v1",
          "page_visit_budget": 3, "model_call_budget": 5, "page_char_limit": 40, "model_policy": POLICY}


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem + "_" + path.parent.name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


class FakeClient:
    """Scripted model: one action per call, per question id."""

    def __init__(self, scripts: dict[str, list[dict]]):
        self.scripts = scripts
        self.calls: list[dict] = []
        self.upstream_addresses = ("172.17.0.1",)
        self.identity_calls = 0

    def observe_identity(self):
        self.identity_calls += 1
        return DIGEST.removeprefix("sha256:")

    def chat(self, messages, schema):
        observation = json.loads(messages[1]["content"].split("\n", 1)[1])
        self.calls.append(observation)
        script = self.scripts[observation["question_id"]]
        action = script.pop(0)
        if action.get("action") == "__error__":
            raise self.error("provider_request_failed:POST /api/chat:timed out")
        return action, {"input_tokens": 10, "output_tokens": 5, "done_reason": "stop", "latency_ms": 1.0}


class NavigatorRunnerTests(unittest.TestCase):
    def setUp(self):
        self.runner = load(RUNNER_PATH)
        FakeClient.error = self.runner.NavigatorError
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.input = Path(self.temp.name) / "input"
        self.output = Path(self.temp.name) / "output"
        corpus = self.input / "corpus"
        corpus.mkdir(parents=True)
        self.start = self.runner.start_doc_id()
        self.assertEqual(self.start, "index__b8d322cc")
        (corpus / f"{self.start}.md").write_text("# Home\nWelcome. See the guide and the faq.\n", encoding="utf-8")
        (corpus / "guide__00000001.md").write_text("# Guide\nSync waves order resources by wave.\n" + "x" * 100, encoding="utf-8")
        (corpus / "faq__00000002.md").write_text("# FAQ\nNo links here.\n", encoding="utf-8")
        (corpus / "hooks__00000003.md").write_text("# Hooks\nHooks run per wave.\n", encoding="utf-8")
        (self.input / "graph_edges.csv").write_text(
            "source,target\n"
            f"{self.start},guide__00000001\n{self.start},faq__00000002\nguide__00000001,hooks__00000003\n"
            "guide__00000001,missing__deadbeef\n", encoding="utf-8")
        (self.input / "config.yaml").write_text(json.dumps(CONFIG), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def questions(self, *ids: str) -> None:
        (self.input / "questions.jsonl").write_text(
            "".join(json.dumps({"question_id": q, "question": f"what about {q}?"}) + "\n" for q in ids), encoding="utf-8")

    def run_with(self, scripts: dict[str, list[dict]]) -> tuple[int, FakeClient]:
        client = FakeClient(scripts)
        code = self.runner.run(self.input, self.output, client_factory=lambda policy: client)
        return code, client

    def read_trace(self, query_id: str) -> list[dict]:
        return [json.loads(line) for line in (self.output / "traces" / f"{query_id}.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_snippet_matches_lexical_runner(self):
        lexical = load(LEXICAL_RUNNER_PATH)
        text = "# Title\n\nFirst line here.\n## Sub\nSecond line.\n"
        self.assertEqual(self.runner.answer_text(text, max_chars=1500), lexical.answer_text(text, max_chars=1500))
        self.assertEqual(self.runner.answer_text(text, max_chars=12), lexical.answer_text(text, max_chars=12))

    def test_open_back_answer_trace_and_prediction_row(self):
        self.questions("q1")
        code, client = self.run_with({"q1": [
            {"action": "open", "link": "guide__00000001", "reason": "guide"},
            {"action": "open", "link": "hooks__00000003"},
            {"action": "back"},
            {"action": "answer", "cited_pages": ["guide__00000001", "hooks__00000003", "guide__00000001"], "text": "Waves order resources."},
        ]})
        self.assertEqual(code, 0)
        rows = self.read_trace("q1")
        self.assertEqual(rows[0]["record"], "header")
        self.assertEqual(rows[0]["schema_version"], "e007b.action_trace.v1")
        self.assertEqual(rows[0]["policy"], "bounded_provider_action_controller_v1")
        self.assertEqual(rows[0]["start_doc_id"], self.start)
        self.assertEqual([r["action"] for r in rows[1:-1]], ["open", "open", "back", "answer", "stop"])
        terminal = rows[-1]
        self.assertEqual(terminal["terminal"], {"reason": "answer"})
        self.assertEqual(terminal["visits"], [self.start, "guide__00000001", "hooks__00000003"])
        self.assertEqual((terminal["opens"], terminal["model_calls"], terminal["dead_ends"]), (2, 4, 1))
        self.assertEqual(terminal["answer"]["cited_pages"], ["guide__00000001", "hooks__00000003"])
        self.assertEqual(len(terminal["provider_calls"]), 4)
        self.assertEqual(terminal["provider_calls"][0]["model_digest"], "b" * 64)
        # Observation: the page is truncated to page_char_limit, links come from the directed graph
        # restricted to the corpus, and back is offered only below the root.
        first, second, third = client.calls[0], client.calls[1], client.calls[2]
        self.assertEqual(first["current_page"]["doc_id"], self.start)
        self.assertEqual(first["links"], ["faq__00000002", "guide__00000001"])
        self.assertEqual(first["allowed_actions"], ["open", "answer"])
        self.assertEqual(second["links"], ["hooks__00000003"])
        self.assertTrue(second["current_page"]["truncated"])
        self.assertEqual(len(second["current_page"]["text"]), 40)
        self.assertEqual(second["allowed_actions"], ["open", "answer", "back"])
        self.assertEqual(third["links"], [])
        self.assertEqual(third["allowed_actions"], ["answer", "back"])
        self.assertEqual(third["budget"], {"opens_remaining": 1, "calls_remaining": 3})
        prediction = json.loads((self.output / "predictions.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(prediction["generated_answer"], "Waves order resources.")
        self.assertEqual(prediction["answer_policy"], "agent_answer_v1")
        self.assertEqual(prediction["cited_pages"], ["guide__00000001", "hooks__00000003"])
        self.assertEqual([(i["doc_id"], i["score"], i["rank"], i["source"]) for i in prediction["retrieved_context"]],
                         [("guide__00000001", 2.0, 1, "llm_guided_navigation"), ("hooks__00000003", 1.0, 2, "llm_guided_navigation")])
        self.assertEqual(prediction["retrieved_context"][1]["text"], "Hooks run per wave.")
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], "rag_llm_guided_search")
        self.assertFalse(metadata["deterministic"])
        self.assertEqual(metadata["model_policy"], POLICY)
        self.assertEqual((metadata["page_visit_budget"], metadata["model_call_budget"], metadata["page_char_limit"]), (3, 5, 40))
        summary = json.loads((self.output / "navigation.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["terminals"]["answer"], 1)
        self.assertEqual(summary["model_digest_before"], "b" * 64)
        self.assertEqual(client.identity_calls, 6)  # before, after each of 4 calls, after

    def test_invalid_actions_and_budgets_terminate(self):
        self.questions("bad_link", "back_root", "cite_unvisited", "opens", "calls", "error")
        code, _ = self.run_with({
            "bad_link": [{"action": "open", "link": "hooks__00000003"}],
            "back_root": [{"action": "back"}],
            "cite_unvisited": [{"action": "answer", "cited_pages": ["guide__00000001"], "text": "x"}],
            "opens": [{"action": "open", "link": "guide__00000001"}, {"action": "back"},
                      {"action": "open", "link": "guide__00000001"}, {"action": "back"},
                      {"action": "open", "link": "faq__00000002"}, {"action": "answer", "cited_pages": [], "text": "done"}],
            "calls": [{"action": "open", "link": "guide__00000001"}, {"action": "back"}] * 3,
            "error": [{"action": "open", "link": "guide__00000001"}, {"action": "__error__"}],
        })
        self.assertEqual(code, 0)
        terminal = lambda q: self.read_trace(q)[-1]["terminal"]
        self.assertEqual(terminal("bad_link")["reason"], "invalid_action")
        self.assertEqual(terminal("bad_link")["validation_error"], "link_not_on_page")
        self.assertEqual(terminal("back_root")["validation_error"], "back_at_root")
        self.assertEqual(terminal("cite_unvisited")["validation_error"], "answer_cites_unvisited_page")
        # Three opens fill the visit budget; the answer still lands within the five calls.
        self.assertEqual(terminal("opens"), {"reason": "answer"})
        self.assertEqual(self.read_trace("opens")[-1]["opens"], 3)
        # Five calls without an answer exhaust the call budget.
        self.assertEqual(terminal("calls"), {"reason": "budget_exhausted", "exhausted": "model_calls"})
        self.assertEqual(self.read_trace("calls")[-1]["model_calls"], 5)
        self.assertEqual(terminal("error")["reason"], "provider_error")
        predictions = [json.loads(line) for line in (self.output / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["generated_answer"] for row in predictions], ["", "", "", "done", "", ""])
        self.assertTrue(all(row["retrieved_context"] == [] for row in predictions))
        summary = json.loads((self.output / "navigation.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["terminals"], {"answer": 1, "budget_exhausted": 1, "invalid_action": 3, "provider_error": 1})

    def test_open_beyond_visit_budget_is_budget_exhausted(self):
        self.questions("q")
        self.run_with({"q": [{"action": "open", "link": "guide__00000001"}, {"action": "back"}] * 3 + [{"action": "open", "link": "guide__00000001"}]})
        rows = self.read_trace("q")
        self.assertEqual(rows[-1]["terminal"], {"reason": "budget_exhausted", "exhausted": "page_visits",
                                                "requested": {"action": "open", "link": "guide__00000001"}})
        self.assertEqual(rows[-1]["opens"], 3)
        self.assertEqual(rows[-1]["model_calls"], 5)

    def test_missing_start_page_is_unavailable(self):
        (self.input / "corpus" / f"{self.start}.md").unlink()
        self.questions("q")
        code, client = self.run_with({"q": []})
        self.assertEqual(code, 4)
        self.assertEqual(client.calls, [])
        summary = json.loads((self.output / "navigation.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["status"], summary["reason"]), ("unavailable", "start_page_missing"))
        self.assertFalse((self.output / "predictions.jsonl").exists())

    def test_model_policy_is_closed(self):
        for field, value in (("backend", "openai"), ("digest", "abc"), ("max_attempts", 2), ("endpoint", "https://host.docker.internal:11434")):
            with self.assertRaises(self.runner.NavigatorError):
                self.runner.parse_model_policy({**POLICY, field: value})
        with self.assertRaises(self.runner.NavigatorError):
            self.runner.parse_config({**CONFIG, "answer_policy": "best_passage_v1"})
        client = self.runner.ModelClient.__new__(self.runner.ModelClient)
        with self.assertRaisesRegex(self.runner.NavigatorError, "route_not_allowlisted"):
            client.policy = self.runner.parse_model_policy(POLICY)
            client._request("POST", "/api/generate", {})
        with self.assertRaisesRegex(self.runner.NavigatorError, "upstream_address_prohibited"):
            self.runner.resolve_private("localhost", 11434)

    def test_navigation_metrics_recompute_from_traces(self):
        self.questions("hit", "miss", "start_hit")
        self.run_with({
            "hit": [{"action": "open", "link": "faq__00000002"}, {"action": "back"}, {"action": "open", "link": "guide__00000001"},
                    {"action": "answer", "cited_pages": ["guide__00000001"], "text": "waves"}],
            "miss": [{"action": "open", "link": "faq__00000002"}, {"action": "answer", "cited_pages": ["faq__00000002"], "text": "no"}],
            "start_hit": [{"action": "answer", "cited_pages": [], "text": "home"}],
        })
        metrics = load(METRICS_PATH)
        support = {"hit": {"guide__00000001"}, "miss": {"hooks__00000003"}, "start_hit": {self.start}}
        result = metrics.compute(self.output / "traces", support)
        slot = result["slot"]
        self.assertEqual((slot["query_count"], slot["success_count"]), (3, 2))
        self.assertAlmostEqual(slot["success_at_n"], 2 / 3)
        self.assertEqual(slot["hops_to_first_support"], 1.0)  # (2 + 0) / 2
        self.assertEqual(slot["pages_per_success"], 1.5)  # 3 opens / 2 successes
        self.assertAlmostEqual(slot["dead_ends"], 2 / 3)  # faq is a dead end, opened twice
        self.assertEqual(slot["terminals"]["answer"], 3)
        by_query = {row["query_id"]: row for row in result["per_query"]}
        self.assertEqual(by_query["hit"]["hops_to_first_support"], 2)
        self.assertIsNone(by_query["miss"]["hops_to_first_support"])
        self.assertEqual(by_query["start_hit"]["hops_to_first_support"], 0)
        self.assertEqual(by_query["hit"]["cited_support"], 1)
        with self.assertRaisesRegex(ValueError, "different queries"):
            metrics.compute(self.output / "traces", {"hit": set()})


if __name__ == "__main__":
    unittest.main()
