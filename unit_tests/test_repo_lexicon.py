"""S055: the repository-grounded lexicon (repo_lexicon_v1) and the Counter 1 token classes on a
synthetic repository and one-line fixtures; the mention rule against that lexicon."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from retrieval_arena.docstyle_measurement import identifier_metrics, identifier_tokens, identifier_view
from retrieval_arena.errors import ValidationError
from retrieval_arena.repo_lexicon import (
    CATALOG_DIR, STOP_CATALOGS, build_lexicon, classify_identifier, load_lexicon, load_stop_catalogs, mention_index,
    stopped,
)

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIERS = ROOT / "unit_tests" / "fixtures" / "identifiers"
REPO = ROOT / "unit_tests" / "fixtures" / "lexicon_repo"
CANDIDATE = {"commit": "a" * 40, "tree": "b" * 40}

# One fixture per class: (file, identifier_view_word_count, identifier_count, unique, class counts).
CLASS_EXPECTED = {
    "class_a_span.md": (7, 1, 1, {"a_code_span": 1}),
    "class_b_flag.md": (9, 2, 2, {"b_flag": 2}),
    "class_c_dotted.md": (11, 1, 1, {"c_dotted": 1}),
    "class_d_path.md": (10, 1, 1, {"d_path": 1}),
    "class_e_snake.md": (6, 1, 1, {"e_snake": 1}),
    "class_f_camel.md": (10, 1, 1, {"f_camel": 1}),
    "class_g_version.md": (8, 1, 1, {"g_version": 1}),
    "class_h_caps.md": (14, 1, 1, {"h_caps": 1}),
    "exclusions.md": (26, 0, 0, {}),
    "fenced.md": (7, 0, 0, {}),
    "bold_span.md": (8, 2, 1, {"a_code_span": 1, "f_camel": 1}),
    "long_span.md": (11, 0, 0, {}),  # S056: a span of > 6 tokens is a view word, never an identifier
}
CLASS_CASES = {
    "--server=foo": "b_flag", "--insecure": "b_flag", "e.g": None, "i.e": None, "helm.sh": "c_dotted",
    "argocd.argoproj.io/hook-delete-policy": "d_path", "/metadata/name": None, "apps/v1": None,
    "GITHUB_TOKEN": "e_snake", "AZURE_CLIENT_ID": "e_snake", "ServiceAccount": "f_camel", "GitHub": "f_camel",
    "Argo": None, "API": None, "1.5": None, "v1.3": "g_version", "2.14.3": "g_version", "10.0.0.1": "g_version",
    "HS256": "h_caps", "UTF-8": "h_caps", "SHA-256": "h_caps", "CD": None, "TLS": None, "2024": None,
    "8080": None, "---": None, "well-known": None, "pre-sync": None, "ArgoCD's": None, "hook-succeeded": None,
    "": None,
}
LEXICON_ENTRIES = ["AnnotationHookDeletePolicy", "DefaultRevision", "PreSync", "ServiceAccount", "SyncPolicy",
                   "argocd.argoproj.io/hook", "argocd.argoproj.io/hook-delete-policy", "syncOptions", "syncPolicy"]


class IdentifierClassTests(unittest.TestCase):
    def test_every_class_case_lands_where_the_prompt_says(self):
        for token, expected in CLASS_CASES.items():
            self.assertEqual(classify_identifier(token), expected, token)

    def test_each_fixture_counts_its_class_once_and_exclusions_zero(self):
        for name, (words, count, unique, classes) in CLASS_EXPECTED.items():
            values = identifier_metrics((IDENTIFIERS / name).read_text(encoding="utf-8"), None)
            self.assertEqual((values["identifier_view_word_count"], values["identifier_count"],
                              values["unique_identifier_count"], values["identifier_class_counts"]),
                             (words, count, unique, classes), name)
            self.assertAlmostEqual(values["identifier_density"], count / words * 100 if words else 0.0, places=9)
            self.assertEqual(sum(classes.values()), count)
            for column in ("lexicon_mention_count", "unique_lexicon_mention_count", "lexicon_mention_density",
                           "identifier_resolution_ratio"):
                self.assertIsNone(values[column])

    def test_identifier_view_keeps_inline_code_and_drops_fences_links_images_and_tags(self):
        view = identifier_view((IDENTIFIERS / "exclusions.md").read_text(encoding="utf-8"))
        self.assertNotIn("https://x.y", view.replace("(see https://x.y/a.md)", ""))
        self.assertNotIn("docs/guide.md", view)
        self.assertNotIn("<div", view)
        self.assertNotIn("img/x.png", view)
        self.assertIn("`this inline span", view)
        fenced = identifier_view((IDENTIFIERS / "fenced.md").read_text(encoding="utf-8"))
        self.assertNotIn("argocd app sync", fenced)
        tokens = identifier_tokens(view)
        self.assertTrue(all(kind == "long_code_span" for token, kind in tokens if token == "seven"))


class RepoLexiconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stops = load_stop_catalogs()
        cls.lexicon = build_lexicon(REPO, REPO / "docs", CANDIDATE, cls.stops)

    def test_stop_catalogs_are_tracked_pinned_and_casefolded(self):
        for key, name in STOP_CATALOGS.items():
            payload = json.loads((CATALOG_DIR / name).read_bytes())
            self.assertEqual(payload["metadata"]["printed_entry_count"], len(payload["entries"]), name)
            self.assertEqual(self.stops[key]["entry_count"], len(set(item.casefold() for item in payload["entries"])))
        self.assertEqual(self.stops["english"]["entry_count"], 1000)
        self.assertTrue(stopped("Application", self.stops))
        self.assertTrue(stopped("Description", self.stops))
        self.assertTrue(stopped("template", self.stops))
        self.assertFalse(stopped("SyncPolicy", self.stops))

    def test_harvest_is_the_expected_entry_set_with_rule_counts(self):
        self.assertEqual([row["token"] for row in self.lexicon["entries"]], LEXICON_ENTRIES)
        self.assertEqual(self.lexicon["rule_counts"],
                         {"r1_identifier_class": 11, "r2_go_declaration": 3, "r3_string_literal": 3,
                          "r4_invalid_json_skipped": 1, "r4_mapping_key": 2})
        self.assertEqual((self.lexicon["file_count"], self.lexicon["skipped_count"]), (4, 0))  # S056: invalid JSON skips r4 only
        self.assertTrue(all(isinstance(row["file_count"], int) and isinstance(row["occurrences"], int)
                            for row in self.lexicon["entries"]))
        tokens = {row["token"] for row in self.lexicon["entries"]}
        self.assertNotIn("VendoredThing", tokens)        # vendor/ excluded
        self.assertNotIn("DocsOnlyIdentifier", tokens)   # the corpus root excluded
        self.assertNotIn("app", tokens)                  # cobra Use: "app" carries no qualifying character
        self.assertNotIn("Application", tokens)          # english stop (casefolded)
        self.assertNotIn("Name", tokens)                 # schema stop (casefolded)
        self.assertNotIn("helper", tokens)               # unexported
        self.assertNotIn("Foo", tokens)                  # a function-body assignment
        self.assertNotIn("description", tokens)          # schema stop
        self.assertIn("syncOptions", tokens)             # a JSON key
        self.assertEqual(self.lexicon["commit"], CANDIDATE["commit"])

    def test_lexicon_is_stable_and_round_trips(self):
        again = build_lexicon(REPO, REPO / "docs", CANDIDATE, self.stops)
        self.assertEqual(again["lexicon_sha256"], self.lexicon["lexicon_sha256"])
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests") as temp:
            path = Path(temp) / "repo_lexicon.v1.json"
            path.write_text(json.dumps(self.lexicon), encoding="utf-8")
            self.assertEqual(load_lexicon(path)["entry_count"], len(LEXICON_ENTRIES))
            broken = dict(self.lexicon, entries=[])
            path.write_text(json.dumps(broken), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_lexicon(path)

    def test_mentions_match_exact_or_casefolded_over_six_and_never_stop_words(self):
        index = mention_index(self.lexicon, self.stops)
        values = identifier_metrics((IDENTIFIERS / "mentions.md").read_text(encoding="utf-8"), index)
        # SyncPolicy (exact), syncpolicy (casefolded, >= 6), the annotation key inside backticks (exact);
        # Application / description / Template are stop words; hook-delete-policy is prose, not an entry.
        self.assertEqual((values["lexicon_mention_count"], values["unique_lexicon_mention_count"]), (3, 2))
        self.assertAlmostEqual(values["lexicon_mention_density"], 3 / values["identifier_view_word_count"] * 100)
        self.assertAlmostEqual(values["identifier_resolution_ratio"], 2 / 3)
        self.assertIsNone(identifier_metrics("no identifiers here\n", index)["identifier_resolution_ratio"])


if __name__ == "__main__":
    unittest.main()
