"""S057: reader v2 -- the reopen recovery (runner and endpoint) and the link-syntax profiles (graph/links.py,
graph/hugo.py, the request fields). Cases (a)-(e) of the S057 prompt."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from retrieval_arena.candidate_retrieval import (
    canonical, expected_provider_metadata, prepare_provider_input, regular_inventory, sha256_bytes, validate_config,
)
from retrieval_arena.errors import ValidationError
from retrieval_arena.graph.hugo import HugoResolver, page_url, parent_section_url
from retrieval_arena.graph.links import extract_markdown_link_edges, extract_profile_link_edges

from unit_tests.test_rag_repo_search_agent_runner import CONFIG, EDGES, PAGES, RUNNER_PATH, FakeClient, load

ROOT = Path(__file__).resolve().parents[1]
MODEL_POLICY = {"backend": "ollama", "model": "qwen3:14b", "digest": "sha256:" + "d" * 64, "temperature": 0.2, "top_p": 0.9,
                "seed": 391960843, "context_window_tokens": 8192, "max_output_tokens": 1024, "timeout_seconds": 300,
                "max_attempts": 1, "endpoint": "http://host.docker.internal:11434", "thinking": False}
SEARCH_IMAGE = "retrieval-arena/rag-repo-search-agent@sha256:" + "c" * 64
HUGO = {"product_url_prefix": "/docs/grafana/",
        "site_prefixes": ["/docs/grafana/<GRAFANA_VERSION>/", "/docs/grafana/latest/", "/docs/grafana/next/"],
        "content_root": "sources"}
# A grafana-shaped tree: a section with _index.md, a leaf, a moved page reachable only by its alias.
HUGO_PAGES = {
    "sources/alerting/_index.md": "---\ntitle: Alerting\n---\n# Alerting\nSee [slack](configure-slack/).\n",
    "sources/alerting/configure-slack.md": (
        "---\ntitle: Slack\nrefs:\n  notification-templates:\n    - pattern: /docs/grafana-cloud/\n"
        "      destination: /docs/grafana-cloud/alerting/templates/\n    - pattern: /docs/\n"
        "      destination: /docs/grafana/<GRAFANA_VERSION>/alerting/templates/\n  leaf:\n    - pattern: /docs/grafana/\n"
        "      destination: /docs/grafana/<GRAFANA_VERSION>/alerting/leaf/\n---\n"
        "# Slack\nUse [templates](ref:notification-templates), [leaf](ref:leaf), [old](/docs/grafana/latest/panels/old-name/),\n"
        "[sibling](../templates/), [external](https://api.slack.com/start), [v9](/docs/grafana/v9.0/alerting/),\n"
        "[missing](ref:nope), [cloud](/docs/grafana-cloud/alerting/), [anchor](#settings).\n"),
    "sources/alerting/leaf.md": "---\ntitle: Leaf\n---\n# Leaf\nBack to [alerting](../).\n",
    "sources/alerting/templates/index.md": "---\ntitle: Templates\n---\n# Templates\n",
    "sources/visualizations/new-name.md": "---\ntitle: New\naliases:\n  - ../../panels/old-name/\n---\n# New\n",
}


class ReaderV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    # --- the runner --------------------------------------------------------------------------------

    def _runner(self, script: list[dict], **config):
        runner = load(RUNNER_PATH)
        FakeClient.error = runner.NavigatorError
        inp, out = self.root / "input", self.root / "output"
        (inp / "corpus").mkdir(parents=True)
        for doc_id, text in PAGES.items():
            (inp / "corpus" / f"{doc_id}.md").write_text(text, encoding="utf-8")
        (inp / "graph_edges.csv").write_text(EDGES, encoding="utf-8")
        (inp / "config.yaml").write_text(json.dumps({**CONFIG, **config}), encoding="utf-8")
        (inp / "questions.jsonl").write_text(json.dumps({"question_id": "q1", "question": "sync windows"}) + "\n", encoding="utf-8")
        client = FakeClient({"q1": list(script)})
        self.assertEqual(runner.run(inp, out, client_factory=lambda policy: client), 0)
        rows = [json.loads(line) for line in (out / "traces" / "q1.jsonl").read_text(encoding="utf-8").splitlines()]
        return runner, client, rows, out

    def test_a_absent_or_zero_recovery_ends_the_run_at_the_first_reopen(self):
        reopen = [{"action": "open", "doc_id": "sync_windows__00000001"}, {"action": "open", "doc_id": "sync_windows__00000001"}]
        _, client, rows, out = self._runner(reopen)
        self.assertEqual(rows[-1]["terminal"]["reason"], "invalid_action")
        self.assertNotIn("invalid_actions_recovered", rows[-1])
        self.assertNotIn("invalid_action_recovery", rows[0]["controller"])
        self.assertNotIn("invalid_action_recovery", json.loads((out / "metadata.json").read_text(encoding="utf-8")))
        self.assertNotIn("invalid_actions_recovered", json.loads((out / "navigation.json").read_text(encoding="utf-8")))
        self.assertTrue(all("last_action_feedback" not in call for call in client.calls))
        self.temp.cleanup(); self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests"); self.root = Path(self.temp.name)
        _, _, rows0, out0 = self._runner(reopen, invalid_action_recovery=0)
        self.assertEqual(rows0[-1]["terminal"]["reason"], "invalid_action")
        self.assertEqual(rows0[-1]["invalid_actions_recovered"], {"count": 0, "steps": []})
        self.assertEqual(json.loads((out0 / "metadata.json").read_text(encoding="utf-8"))["invalid_action_recovery"], 0)

    def test_b_two_recoveries_then_the_third_refusal_ends_the_run(self):
        runner, client, rows, out = self._runner([{"action": "open", "doc_id": "sync_windows__00000001"}]
                                                 + [{"action": "open", "doc_id": "sync_windows__00000001"}] * 3,
                                                 invalid_action_recovery=2)
        self.assertEqual(rows[-1]["terminal"]["reason"], "invalid_action")
        self.assertEqual(rows[-1]["invalid_actions_recovered"], {"count": 2, "steps": [2, 3]})
        self.assertEqual([c.get("recovered") for c in rows[-1]["provider_calls"]], [None, True, True, None])
        self.assertEqual(rows[-1]["model_calls"], 4)                     # the refused calls already counted
        self.assertEqual([r["action"] for r in rows[1:-1]], ["search", "open", "stop"])   # no new action rows
        self.assertEqual(json.loads((out / "navigation.json").read_text(encoding="utf-8"))["invalid_actions_recovered"], 2)
        self.assertEqual(rows[0]["controller"]["prompt_version"], "e007d_repo_search_prompt_v1")

    def test_c_the_feedback_appears_only_in_the_observation_after_a_refusal(self):
        runner, client, rows, _ = self._runner([
            {"action": "open", "doc_id": "sync_windows__00000001"},
            {"action": "open", "doc_id": "sync_windows__00000001"},
            {"action": "answer", "cited_pages": ["sync_windows__00000001"], "text": "Windows block syncs."},
        ], invalid_action_recovery=2)
        self.assertEqual(rows[-1]["terminal"]["reason"], "answer")
        self.assertEqual(["last_action_feedback" in call for call in client.calls], [False, False, True])
        self.assertEqual(client.calls[2]["last_action_feedback"], runner.RECOVERY_FEEDBACK)
        without = dict(client.calls[2]); without.pop("last_action_feedback")
        self.assertEqual(without["current_page"], client.calls[1]["current_page"])
        with self.assertRaises(runner.NavigatorError):
            runner.parse_config({**CONFIG, "invalid_action_recovery": 1})

    # --- the link profiles -------------------------------------------------------------------------

    def _hugo_tree(self) -> Path:
        docs = self.root / "docs"
        for relative, text in HUGO_PAGES.items():
            (docs / relative).parent.mkdir(parents=True, exist_ok=True)
            (docs / relative).write_text(text, encoding="utf-8")
        return docs

    def test_d_hugo_profile_resolves_refs_site_urls_relative_urls_and_aliases_and_counts_the_rest(self):
        self.assertEqual((page_url("a/b.md"), page_url("a/_index.md"), page_url("a/index.md"), page_url("_index.md")),
                         ("/a/b/", "/a/", "/a/", "/"))
        self.assertEqual((parent_section_url("a/b/c.md"), parent_section_url("a/b/_index.md")), ("/a/b/", "/a/"))
        docs = self._hugo_tree()
        content = {k[len("sources/"):]: v for k, v in HUGO_PAGES.items()}
        resolver = HugoResolver(content, HUGO)
        slack = "alerting/configure-slack.md"
        cases = {
            "ref:notification-templates": ("ref", "resolved", "alerting/templates/index.md"),    # first pattern that is a prefix
            "ref:leaf": ("ref", "resolved", "alerting/leaf.md"),
            "ref:nope": ("ref", "unresolved", None),
            "/docs/grafana/latest/panels/old-name/": ("site_absolute", "resolved", "visualizations/new-name.md"),  # alias
            "/docs/grafana/v9.0/alerting/": ("site_absolute", "external", None),                  # another version
            "/docs/grafana-cloud/alerting/": ("site_absolute", "external", None),                 # another product
            "https://api.slack.com/start": ("site_absolute", "external", None),
            "../templates/": ("site_relative", "resolved", "alerting/templates/index.md"),       # page-URL semantics
            "#settings": ("site_relative", "skip", None),
        }
        for href, expected in cases.items():
            self.assertEqual(resolver.resolve(href, slack), expected, href)
        self.assertEqual(resolver.resolve("configure-slack/", "alerting/_index.md")[2], "alerting/configure-slack.md")
        self.assertEqual(resolver.resolve("../", "alerting/leaf.md")[2], "alerting/_index.md")
        ids = lambda relative: relative.replace("/", "_")  # noqa: E731
        edges, stats = extract_profile_link_edges(docs, profiles=["markdown_relative_v1", "hugo_site_url_v1"],
                                                  params={"hugo_site_url_v1": HUGO}, doc_id_for=ids, extensions=[".md"])
        self.assertIn(("sources_alerting_configure-slack.md", "sources_visualizations_new-name.md"), edges)
        self.assertEqual(stats["hugo_site_url_v1"]["ref"], {"found": 3, "resolved": 2, "external": 0, "unresolved": 1})
        self.assertEqual(stats["hugo_site_url_v1"]["site_absolute"]["external"], 3)
        markdown_only, _ = extract_profile_link_edges(docs, profiles=["markdown_relative_v1"], doc_id_for=ids, extensions=[".md"])
        self.assertEqual(markdown_only, extract_markdown_link_edges(docs, doc_id_for=ids, extensions=[".md"]))

    # --- the endpoint ------------------------------------------------------------------------------

    def _search_config(self, **provider):
        (self.root / "input").mkdir(exist_ok=True)
        (self.root / "input" / "questions.jsonl").write_text(json.dumps({"question_id": "q1", "question": "slack"}) + "\n", encoding="utf-8")
        repo = self.root / "candidate"
        if not (repo / ".git").exists():
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "f@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "F"], check=True)
            for relative, text in HUGO_PAGES.items():
                (repo / "docs" / relative).parent.mkdir(parents=True, exist_ok=True)
                (repo / "docs" / relative).write_text(text, encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
        inventory = regular_inventory(self.root / "input")
        return {"schema_version": "retrieval_arena.candidate_retrieval_config.v1", "experiment_id": "exp", "execution_id": "slot",
                "output_identity": "output", "candidate": {"commit": commit, "tree": tree},
                "corpus": {"root": "docs", "extensions": [".md"], "import_policy": "cikm_flat_path_sha1_lower_v1"},
                "queries_path": "questions.jsonl",
                "provider": {"name": "rag_repo_search_agent", "image": SEARCH_IMAGE, "platform": "linux/amd64",
                             "network": "host_model_alias", "top_k": 3, "max_context_chars_per_doc": 1500,
                             "answer_policy": "agent_answer_v1", "model_policy": dict(MODEL_POLICY), "search_budget": 3,
                             "search_hit_files": 10, "search_hit_lines_per_file": 3, "page_visit_budget": 8,
                             "model_call_budget": 12, "page_char_limit": 12000, "follow_links": True,
                             "landing_policy": "search_question_text", **provider},
                "ranked": {"schema_version": "retrieval_arena.ranked_prediction.v2", "context_item_budget_k": 3},
                "input_inventory": inventory, "input_inventory_sha256": sha256_bytes(canonical(inventory))}, repo

    def test_e_the_endpoint_keeps_old_requests_and_admits_the_new_fields_closed(self):
        plain, repo = self._search_config()
        validated = validate_config(json.loads(json.dumps(plain)))
        self.assertNotIn("invalid_action_recovery", expected_provider_metadata(validated["provider"]))
        launch_plain = prepare_provider_input(plain, self.root / "input", repo, self.root / "plain")
        self.assertNotIn("link_profiles", launch_plain)
        self.assertNotIn("invalid_action_recovery", json.loads((self.root / "plain" / "config.yaml").read_text(encoding="utf-8")))
        self.assertEqual(expected_provider_metadata(validate_config(self._search_config(invalid_action_recovery=2)[0])["provider"])
                         ["invalid_action_recovery"], 2)
        for bad in (1, True, "2"):
            with self.assertRaisesRegex(ValidationError, "invalid-action-recovery"):
                validate_config(self._search_config(invalid_action_recovery=bad)[0])
        lexical, _ = self._search_config()
        lexical["provider"] = {"name": "rag_lexical_topk", "image": "retrieval-arena/rag-lexical-topk@sha256:" + "a" * 64,
                               "platform": "linux/amd64", "network": "none", "top_k": 3, "max_context_chars_per_doc": 1500,
                               "invalid_action_recovery": 2}
        with self.assertRaisesRegex(ValidationError, "provider-fields"):
            validate_config(lexical)
        hugo, _ = self._search_config(invalid_action_recovery=2)
        hugo["corpus"].update({"link_profiles": ["markdown_relative_v1", "hugo_site_url_v1"],
                               "link_profile_params": {"hugo_site_url_v1": HUGO}})
        launch = prepare_provider_input(hugo, self.root / "input", repo, self.root / "hugo")
        self.assertEqual(launch["link_profiles"], ["markdown_relative_v1", "hugo_site_url_v1"])
        self.assertGreater(launch["link_resolution"]["hugo_site_url_v1"]["ref"]["resolved"], 0)
        self.assertGreater(len((self.root / "hugo" / "graph_edges.csv").read_text(encoding="utf-8").splitlines()),
                           len((self.root / "plain" / "graph_edges.csv").read_text(encoding="utf-8").splitlines()))
        for corpus, code in (({"link_profiles": ["hugo_site_url_v1"]}, "link-profile-params"),
                             ({"link_profiles": ["sphinx_rst_v2"]}, "link-profiles"),
                             ({"link_profiles": [["markdown_relative_v1"]]}, "link-profiles"),
                             ({"link_profile_params": {"hugo_site_url_v1": HUGO}}, "link-profiles"),
                             ({"link_profiles": ["hugo_site_url_v1"], "link_profile_params": {"hugo_site_url_v1": {**HUGO, "extra": 1}}},
                              "link-profile-params")):
            bad, _ = self._search_config()
            bad["corpus"].update(corpus)
            with self.assertRaisesRegex(ValidationError, code, msg=repr(corpus)):
                validate_config(bad)


if __name__ == "__main__":
    unittest.main()
