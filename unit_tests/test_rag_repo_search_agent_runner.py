"""Unit tests for tests/rag_repo_search_agent/runner.py and scripts/search_navigation_metrics.py (S046, RSA-1..6)."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = ROOT / "tests" / "rag_repo_search_agent" / "runner.py"
METRICS_PATH = ROOT / "scripts" / "search_navigation_metrics.py"
NAVIGATOR_RUNNER_PATH = ROOT / "tests" / "rag_llm_guided_search" / "runner.py"
DIGEST = "sha256:" + "b" * 64
POLICY = {"backend": "ollama", "model": "qwen3:14b", "digest": DIGEST, "temperature": 0.2, "top_p": 0.9,
          "seed": 391960843, "context_window_tokens": 8192, "max_output_tokens": 1024, "timeout_seconds": 300,
          "max_attempts": 1, "endpoint": "http://host.docker.internal:11434", "thinking": False}
CONFIG = {"top_k": 5, "max_context_chars_per_doc": 1500, "answer_policy": "agent_answer_v1",
          "search_budget": 3, "search_hit_files": 10, "search_hit_lines_per_file": 3,
          "page_visit_budget": 8, "model_call_budget": 12, "page_char_limit": 40, "follow_links": True,
          "model_policy": POLICY}
# The six-page fixture corpus (RSA-1): two pages carry both "sync" and "windows".
PAGES = {
    "index__b8d322cc": "# Home\nWelcome. See the guide and the faq.\n",
    "sync_windows__00000001": "# Sync windows\nSync windows block syncs outside a window.\nA sync outside the windows is denied.\nSee also hooks.\n",
    "faq__00000002": "# FAQ\nWhy does my sync fail? Sync windows may block it.\n",
    "hooks__00000003": "# Hooks\nHooks run per wave. No windows here.\n",
    "waves__00000004": "# Waves\nSync waves order resources.\n",
    "orphan__00000005": "# Orphan\nNothing links here; grep finds it by the word windows.\n",
}
EDGES = ("source,target\n"
         "index__b8d322cc,faq__00000002\nindex__b8d322cc,waves__00000004\n"
         "sync_windows__00000001,hooks__00000003\nfaq__00000002,sync_windows__00000001\n"
         "waves__00000004,missing__deadbeef\n")


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem + "_" + path.parent.name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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
        assert schema["properties"]["action"]["enum"] == observation["allowed_actions"]
        self.calls.append(observation)
        script = self.scripts[observation["question_id"]]
        action = script.pop(0)
        if action.get("action") == "__error__":
            raise self.error("provider_request_failed:POST /api/chat:timed out")
        return action, {"input_tokens": 10, "output_tokens": 5, "done_reason": "stop", "latency_ms": 1.0}


class RepoSearchAgentTests(unittest.TestCase):
    def setUp(self):
        self.runner = load(RUNNER_PATH)
        FakeClient.error = self.runner.NavigatorError
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.input = Path(self.temp.name) / "input"
        self.output = Path(self.temp.name) / "output"
        corpus = self.input / "corpus"
        corpus.mkdir(parents=True)
        for doc_id, text in PAGES.items():
            (corpus / f"{doc_id}.md").write_text(text, encoding="utf-8")
        (self.input / "graph_edges.csv").write_text(EDGES, encoding="utf-8")
        (self.input / "config.yaml").write_text(json.dumps(CONFIG), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def questions(self, **texts: str) -> None:
        (self.input / "questions.jsonl").write_text(
            "".join(json.dumps({"question_id": q, "question": t}) + "\n" for q, t in texts.items()), encoding="utf-8")

    def run_with(self, scripts: dict[str, list[dict]], **config) -> tuple[int, FakeClient]:
        if config:
            (self.input / "config.yaml").write_text(json.dumps({**CONFIG, **config}), encoding="utf-8")
        client = FakeClient(scripts)
        code = self.runner.run(self.input, self.output, client_factory=lambda policy: client)
        return code, client

    def read_trace(self, query_id: str) -> list[dict]:
        return [json.loads(line) for line in (self.output / "traces" / f"{query_id}.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_relay_client_and_snippet_are_the_navigators(self):
        navigator = load(NAVIGATOR_RUNNER_PATH)
        text = "# Title\n\nFirst line here.\n## Sub\nSecond line.\n"
        self.assertEqual(self.runner.answer_text(text, max_chars=12), navigator.answer_text(text, max_chars=12))
        self.assertEqual(self.runner.ALLOWED_REQUESTS, navigator.ALLOWED_REQUESTS)
        with self.assertRaisesRegex(self.runner.NavigatorError, "upstream_address_prohibited"):
            self.runner.resolve_private("127.0.0.1", 11434)
        policy = self.runner.parse_model_policy(POLICY)
        client = self.runner.ModelClient.__new__(self.runner.ModelClient)
        client.policy = policy; client.base = "http://10.0.0.1:11434"
        with self.assertRaisesRegex(self.runner.NavigatorError, "route_not_allowlisted"):
            client._request("POST", "/api/generate", {})

    def test_rsa1_landing_search_orders_files_and_offers_hits_then_links(self):
        self.questions(q1="sync windows block")
        code, client = self.run_with({"q1": [
            {"action": "open", "doc_id": "sync_windows__00000001"},
            {"action": "open", "doc_id": "hooks__00000003"},
            {"action": "back"},
            {"action": "answer", "cited_pages": ["sync_windows__00000001", "hooks__00000003", "sync_windows__00000001"],
             "text": "A sync outside the windows is denied."},
        ]})
        self.assertEqual(code, 0)
        rows = self.read_trace("q1")
        header, terminal = rows[0], rows[-1]
        self.assertEqual(header["schema_version"], "e007c.action_trace.v1")
        self.assertEqual(header["policy"], "bounded_repo_search_controller_v1")
        self.assertEqual((header["follow_links"], header["landing_query"], header["start_doc_id"]),
                         (True, "sync windows block", None))
        self.assertEqual([r["action"] for r in rows[1:-1]], ["search", "open", "open", "back", "answer", "stop"])
        landing = rows[1]["details"]
        # Only the lines carrying every word ("sync", "windows", "block") match: one line in each of two
        # pages, so the tie on match count is broken by document id.
        self.assertEqual((landing["landing"], landing["search_index"], landing["query"]), (True, 1, "sync windows block"))
        self.assertEqual(landing["doc_ids"], ["faq__00000002", "sync_windows__00000001"])
        self.assertEqual(terminal["visits"], ["sync_windows__00000001", "hooks__00000003"])
        self.assertEqual((terminal["opens"], terminal["searches"], terminal["model_calls"], terminal["dead_ends"]), (2, 1, 4, 1))
        self.assertEqual(terminal["answer"]["cited_pages"], ["sync_windows__00000001", "hooks__00000003"])
        first, second, third = client.calls[0], client.calls[1], client.calls[2]
        self.assertIsNone(first["current_page"])
        self.assertEqual([(h["doc_id"], h["line_number"]) for h in first["searches"][0]["hits"]],
                         [("faq__00000002", 2), ("sync_windows__00000001", 2)])
        # Before any open: hits are openable, no links, no back.
        self.assertEqual(first["allowed_actions"], ["open", "search", "answer"])
        self.assertEqual(first["budget"], {"searches_remaining": 2, "opens_remaining": 8, "calls_remaining": 12})
        # After the first open the page's admissible links join the openable set; after the
        # second (a link route) back appears; the dead-end flag marks hooks (no outlinks).
        self.assertEqual(second["links"], ["hooks__00000003"])
        self.assertEqual(second["allowed_actions"], ["open", "search", "answer"])
        self.assertEqual(third["links"], [])
        self.assertEqual(third["allowed_actions"], ["open", "search", "answer", "back"])
        self.assertEqual([r["details"]["route"] for r in rows[1:-1] if r["action"] == "open"], ["landing", "link"])
        # A search with no hits, a landing that hits nothing: open is not offered before any open.
        self.questions(q2="how do sync windows block")
        import shutil; shutil.rmtree(self.output)
        code, client = self.run_with({"q2": [{"action": "answer", "cited_pages": [], "text": "none"}]})
        self.assertEqual(code, 0)
        self.assertEqual(client.calls[0]["allowed_actions"], ["search", "answer"])
        self.assertEqual(self.read_trace("q2")[1]["details"]["doc_ids"], [])

    def test_rsa1_two_word_query_groups_lines_by_file_in_match_count_order(self):
        self.questions(q1="sync windows")
        code, client = self.run_with({"q1": [
            {"action": "open", "doc_id": "sync_windows__00000001"},
            {"action": "answer", "cited_pages": ["sync_windows__00000001"], "text": "Denied outside a window."},
        ]}, search_hit_lines_per_file=1)
        self.assertEqual(code, 0)
        first, second = client.calls[0], client.calls[1]
        hits = first["searches"][0]["hits"]
        self.assertEqual([(h["doc_id"], h["path"], h["line_number"], h["matching_lines"]) for h in hits],
                         [("sync_windows__00000001", "sync_windows__00000001.md", 1, 3), ("faq__00000002", "faq__00000002.md", 2, 1)])
        self.assertEqual(hits[0]["text"], "# Sync windows")
        self.assertEqual(first["openable_pages"], ["faq__00000002", "sync_windows__00000001"])
        self.assertEqual(first["allowed_actions"], ["open", "search", "answer"])
        self.assertEqual(first["links"], [])
        # After an open, the page's admissible links are offered and back is not (depth 1).
        self.assertEqual(second["current_page"]["doc_id"], "sync_windows__00000001")
        self.assertTrue(second["current_page"]["truncated"])
        self.assertEqual(second["links"], ["hooks__00000003"])
        self.assertEqual(second["openable_pages"], ["faq__00000002", "hooks__00000003", "sync_windows__00000001"])
        self.assertEqual(second["allowed_actions"], ["open", "search", "answer"])
        rows = self.read_trace("q1")
        self.assertEqual(rows[2]["details"]["route"], "landing")
        prediction = json.loads((self.output / "predictions.jsonl").read_text(encoding="utf-8"))
        self.assertEqual([(i["doc_id"], i["score"], i["rank"], i["source"]) for i in prediction["retrieved_context"]],
                         [("sync_windows__00000001", 1.0, 1, "repo_search_navigation")])
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual((metadata["name"], metadata["follow_links"], metadata["search_hit_lines_per_file"]),
                         ("rag_repo_search_agent", True, 1))

    def test_rsa2_nolinks_variant_hides_links_and_refuses_a_link_id(self):
        self.questions(q1="sync windows")
        code, client = self.run_with({"q1": [
            {"action": "open", "doc_id": "sync_windows__00000001"},
            {"action": "open", "doc_id": "hooks__00000003"},
        ]}, follow_links=False)
        self.assertEqual(code, 0)
        second = client.calls[1]
        self.assertNotIn("links", second)
        self.assertEqual(second["openable_pages"], ["faq__00000002", "sync_windows__00000001"])
        rows = self.read_trace("q1")
        self.assertEqual(rows[0]["follow_links"], False)
        self.assertEqual(rows[-1]["terminal"], {"reason": "invalid_action", "validation_error": "page_not_offered",
                                                 "requested": {"action": "open", "doc_id": "hooks__00000003"}})
        metadata = json.loads((self.output / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["name"], "rag_repo_search_agent_nolinks")
        summary = json.loads((self.output / "navigation.json").read_text(encoding="utf-8"))
        self.assertEqual((summary["provider"], summary["follow_links"], summary["terminals"]["invalid_action"]),
                         ("rag_repo_search_agent_nolinks", False, 1))

    def test_rsa3_query_is_literal_text_never_a_path_or_regex(self):
        corpus = {doc_id: text for doc_id, text in PAGES.items()}
        search = lambda q: self.runner.search_corpus(corpus, q, hit_files=10, lines_per_file=3)
        self.assertEqual(search("../support.json"), [])
        self.assertEqual(search("docs/index.md"), [])
        self.assertEqual(search("/etc/passwd"), [])
        self.assertEqual(search(".*"), [])
        self.assertEqual(search("w[i]ndows"), [])
        self.assertEqual(search("   "), [])
        # A regex metacharacter present literally in a page matches literally.
        corpus["regex__00000006"] = "# Regex\nUse .* to match anything.\n"
        self.assertEqual([(h["doc_id"], h["line_number"]) for h in search(".*")], [("regex__00000006", 2)])
        self.assertEqual([h["doc_id"] for h in search("WINDOWS")][:1], ["sync_windows__00000001"])
        # A zero-hit search still costs a search (the second, after the landing).
        self.questions(q1="sync windows")
        code, client = self.run_with({"q1": [
            {"action": "search", "query": "../support.json"},
            {"action": "answer", "cited_pages": [], "text": "unknown"},
        ]})
        self.assertEqual(code, 0)
        rows = self.read_trace("q1")
        self.assertEqual([r["details"]["doc_ids"] for r in rows[1:-1] if r["action"] == "search"][1], [])
        self.assertEqual((rows[-1]["searches"], rows[-1]["zero_hit_searches"]), (2, 1))
        self.assertEqual(client.calls[1]["budget"]["searches_remaining"], 1)
        self.assertEqual(client.calls[1]["searches"][0], {"search_index": 1, "query": "sync windows",
                                                          "doc_ids": ["sync_windows__00000001", "faq__00000002"]})

    def test_rsa4_budgets_end_the_trace(self):
        self.questions(q1="sync windows", q2="sync windows", q3="sync windows")
        code, client = self.run_with({
            "q1": [{"action": "search", "query": "hooks"}, {"action": "search", "query": "waves"},
                   {"action": "answer", "cited_pages": [], "text": "spent"}],
            "q2": [{"action": "open", "doc_id": "sync_windows__00000001"}, {"action": "back"},
                   {"action": "open", "doc_id": "faq__00000002"}, {"action": "answer", "cited_pages": [], "text": "x"}],
            "q3": [{"action": "open", "doc_id": "sync_windows__00000001"}] * 2 + [{"action": "search", "query": "hooks"}] * 2
                  + [{"action": "back"}],
        }, search_budget=3, page_visit_budget=2, model_call_budget=5)
        self.assertEqual(code, 0)
        # q1: the third search is the last allowed; the fourth call's enum no longer offers search.
        q1_calls = [c for c in client.calls if c["question_id"] == "q1"]
        self.assertEqual(q1_calls[2]["allowed_actions"], ["open", "answer"])
        self.assertEqual(q1_calls[2]["budget"]["searches_remaining"], 0)
        # q2: back at the first page is not offered (depth 1): invalid_action.
        self.assertEqual(self.read_trace("q2")[-1]["terminal"]["validation_error"], "back_at_first_page")
        # q3: two opens spend the visit budget (open drops from the enum), then the call budget ends it.
        rows = self.read_trace("q3")
        q3_calls = [c for c in client.calls if c["question_id"] == "q3"]
        self.assertEqual(q3_calls[2]["allowed_actions"], ["search", "answer", "back"])
        self.assertEqual(rows[-1]["terminal"], {"reason": "budget_exhausted", "exhausted": "model_calls"})
        self.assertEqual((rows[-1]["opens"], rows[-1]["searches"], rows[-1]["model_calls"]), (2, 3, 5))

    def test_rsa5_link_route_and_link_new_against_a_baseline_graph(self):
        metrics = load(METRICS_PATH)
        # The orphan page is reachable by a link the refined graph adds (faq -> orphan) and no search offers it.
        (self.input / "graph_edges.csv").write_text(EDGES + "faq__00000002,orphan__00000005\n", encoding="utf-8")
        self.questions(q1="sync windows")
        code, client = self.run_with({"q1": [
            {"action": "open", "doc_id": "faq__00000002"},
            {"action": "open", "doc_id": "orphan__00000005"},
            {"action": "answer", "cited_pages": ["orphan__00000005"], "text": "found"},
        ]})
        self.assertEqual(code, 0)
        rows = self.read_trace("q1")
        opens = [r["details"] for r in rows[1:-1] if r["action"] == "open"]
        self.assertEqual([(o["doc_id"], o["route"], o["from_doc_id"]) for o in opens],
                         [("faq__00000002", "landing", None), ("orphan__00000005", "link", "faq__00000002")])
        support = {"q1": {"orphan__00000005"}}
        baseline = metrics.read_graph(self.input / "graph_edges.csv")
        baseline["faq__00000002"].discard("orphan__00000005")
        result = metrics.compute(self.output / "traces", support, baseline)
        self.assertEqual(result["per_query"][0]["success_route"], "link")
        self.assertEqual(result["per_query"][0]["link_new"], True)
        self.assertEqual((result["per_query"][0]["hops_to_first_support"], result["per_query"][0]["cost_to_first_support"]), (1, 2))
        self.assertEqual(result["slot"]["successes_by_route"], {"landing": 0, "search": 0, "link": 1})
        self.assertEqual(result["slot"]["successes_via_new_link"], 1)
        # With the edge in the baseline graph the same trace is a link route that is not new.
        refined = metrics.read_graph(self.input / "graph_edges.csv")
        self.assertEqual(metrics.compute(self.output / "traces", support, refined)["per_query"][0]["link_new"], False)
        # On the baseline graph itself the open is impossible: the id is never offered.
        (self.input / "graph_edges.csv").write_text(EDGES, encoding="utf-8")
        import shutil; shutil.rmtree(self.output)
        code, client = self.run_with({"q1": [
            {"action": "open", "doc_id": "faq__00000002"},
            {"action": "open", "doc_id": "orphan__00000005"},
        ]})
        self.assertEqual(self.read_trace("q1")[-1]["terminal"]["validation_error"], "page_not_offered")

    def test_rsa6_route_attribution_sums_to_success_count(self):
        metrics = load(METRICS_PATH)
        self.questions(q1="sync windows", q2="sync windows", q3="sync windows", q4="sync windows")
        code, client = self.run_with({
            # landing: the first opened support page was offered by the landing search
            "q1": [{"action": "open", "doc_id": "sync_windows__00000001"}, {"action": "answer", "cited_pages": [], "text": ""}],
            # search: offered by a later search only
            "q2": [{"action": "search", "query": "wave"}, {"action": "open", "doc_id": "waves__00000004"},
                   {"action": "answer", "cited_pages": [], "text": ""}],
            # link: offered by the current page's links and by no search
            "q3": [{"action": "open", "doc_id": "sync_windows__00000001"}, {"action": "open", "doc_id": "hooks__00000003"},
                   {"action": "answer", "cited_pages": [], "text": ""}],
            # none: no support page opened; a zero-hit search on the way
            "q4": [{"action": "search", "query": "nothing matches this"}, {"action": "open", "doc_id": "faq__00000002"},
                   {"action": "answer", "cited_pages": [], "text": ""}],
        })
        self.assertEqual(code, 0)
        support = {"q1": {"sync_windows__00000001"}, "q2": {"waves__00000004"}, "q3": {"hooks__00000003"}, "q4": {"waves__00000004"}}
        result = metrics.compute(self.output / "traces", support)
        by_id = {row["query_id"]: row for row in result["per_query"]}
        self.assertEqual([by_id[q]["success_route"] for q in ("q1", "q2", "q3", "q4")], ["landing", "search", "link", "none"])
        self.assertEqual([by_id[q]["landing_hit"] for q in ("q1", "q2", "q3", "q4")], [True, False, False, False])
        self.assertEqual([by_id[q]["cost_to_first_support"] for q in ("q1", "q2", "q3", "q4")], [1, 2, 2, None])
        self.assertEqual(by_id["q3"]["link_new"], None)
        slot = result["slot"]
        self.assertEqual(slot["successes_by_route"], {"landing": 1, "search": 1, "link": 1})
        self.assertEqual(sum(slot["successes_by_route"].values()), slot["success_count"])
        self.assertEqual((slot["landing_hit_rate"], slot["queries_with_zero_hit_search"], slot["searches_mean"]), (0.25, 1, 1.5))
        self.assertEqual(slot["cost_to_first_support_by_route"], {"landing": 1.0, "search": 2.0, "link": 2.0})

    def test_provider_error_ends_the_query_and_the_slot_continues(self):
        self.questions(q1="sync windows", q2="sync windows")
        code, client = self.run_with({"q1": [{"action": "__error__"}],
                                      "q2": [{"action": "answer", "cited_pages": [], "text": "fine"}]})
        self.assertEqual(code, 0)
        self.assertEqual(self.read_trace("q1")[-1]["terminal"]["reason"], "provider_error")
        self.assertEqual(self.read_trace("q2")[-1]["terminal"]["reason"], "answer")
        summary = json.loads((self.output / "navigation.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["terminals"], {"answer": 1, "budget_exhausted": 0, "invalid_action": 0, "provider_error": 1})
        self.assertEqual(summary["searches"], 2)

    def test_config_requires_follow_links_and_the_agent_answer_policy(self):
        with self.assertRaisesRegex(self.runner.NavigatorError, "follow_links_missing"):
            self.runner.parse_config({k: v for k, v in CONFIG.items() if k != "follow_links"})
        with self.assertRaisesRegex(self.runner.NavigatorError, "answer_policy_unsupported"):
            self.runner.parse_config({**CONFIG, "answer_policy": "best_passage_v1"})


if __name__ == "__main__":
    unittest.main()
