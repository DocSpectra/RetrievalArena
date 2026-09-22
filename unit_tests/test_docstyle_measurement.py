"""S052: the DocEng Appendix B measurement -- hand-computed structure / volume / marker values
on a thirteen-file fixture, shape checks for the spaCy columns, the summary conventions, the
request admission, and the operator entry end to end (twice, byte-identical).
S055: the lexicon config fields, and the operator building then consuming a lexicon."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from retrieval_arena.candidate_retrieval import canonical, imported_document_name, regular_inventory, sha256_bytes
from retrieval_arena.docstyle_measurement import (
    ALGORITHM_CHOICES, CATALOG_DIR, CONFIG_SCHEMA, EXPORT_METRIC_COLUMNS, EXTRA_COLUMNS, IDENTIFIER_COLUMNS,
    NUMERIC_COLUMNS, REQUEST_SCHEMA, ROW_COLUMNS, SUMMARY_SCHEMA, TABLE_SCHEMA, column_summary, count_markers, load_catalogs, load_spacy, load_support_ids,
    marker_metrics, measure_rows, nlp_metrics, order_statistic, percentile_rank, spearman, structure_metrics,
    summarize, text_view, validate_config,
)
from retrieval_arena.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "unit_tests" / "fixtures" / "docstyle"
CAPSTONE_CATALOGS = ROOT.parent.parent / "git" / "DocSpectraCapstone" / "src" / "docspectra_capstone" / "metrics" / "catalogs"

# Hand-computed from the fixture text (see the file contents): every File, Markdown Structure,
# Text Volume, vagueness and constraint column. Densities are count / word_count * 1000.
# Under reading revision R1 (S052 calibration) word_count, sentence_count_basic, paragraph_count and
# the marker densities read the text view (fenced blocks and inline code removed); paragraphs are
# every blank-line-separated block of that view.
ZERO = {"header_count": 0, "code_fence_count": 0, "code_block_lines": 0, "bullet_list_count": 0,
        "numbered_list_count": 0, "checklist_count": 0, "list_item_count": 0, "paragraph_count": 0,
        "word_count": 0, "sentence_count_basic": 0, "vagueness": 0, "constraint": 0,
        **{f"header_h{level}_count": 0 for level in range(1, 7)}}
EXPECTED = {
    "empty.md": {**ZERO, "lines": 0},
    "whitespace.md": {**ZERO, "lines": 3},
    "plain.md": {**ZERO, "lines": 2, "paragraph_count": 1, "word_count": 17, "sentence_count_basic": 3,
                 "vagueness": 1, "constraint": 1},
    "headers.md": {**ZERO, "lines": 9, "header_count": 6, "header_h1_count": 1, "header_h2_count": 1,
                   "header_h3_count": 1, "header_h4_count": 1, "header_h5_count": 1, "header_h6_count": 1,
                   "paragraph_count": 1, "word_count": 22, "sentence_count_basic": 1},
    "lists.md": {**ZERO, "lines": 13, "bullet_list_count": 8, "numbered_list_count": 3, "checklist_count": 3,
                 "list_item_count": 11, "paragraph_count": 1, "word_count": 30, "sentence_count_basic": 2},
    "fences.md": {**ZERO, "lines": 16, "code_fence_count": 3, "code_block_lines": 7, "paragraph_count": 1,
                  "word_count": 2, "sentence_count_basic": 1},
    "frontmatter.md": {**ZERO, "lines": 6, "paragraph_count": 2, "word_count": 10, "sentence_count_basic": 1},
    "liquid.md": {**ZERO, "lines": 3, "header_count": 1, "header_h1_count": 1, "paragraph_count": 2,
                  "word_count": 19, "sentence_count_basic": 2, "vagueness": 2, "constraint": 2},
    "markers.md": {**ZERO, "lines": 2, "paragraph_count": 1, "word_count": 24, "sentence_count_basic": 8,
                   "vagueness": 5, "constraint": 10},
    "nested_numbered.md": {**ZERO, "lines": 9, "bullet_list_count": 1, "numbered_list_count": 3,
                           "list_item_count": 4, "paragraph_count": 3, "word_count": 18, "sentence_count_basic": 6},
    "code_inline.md": {**ZERO, "lines": 7, "header_count": 1, "header_h2_count": 1, "code_fence_count": 1,
                       "code_block_lines": 1, "paragraph_count": 2, "word_count": 7, "sentence_count_basic": 1},
    "table.md": {**ZERO, "lines": 5, "paragraph_count": 2, "word_count": 12, "sentence_count_basic": 1},
    "entities.md": {**ZERO, "lines": 1, "paragraph_count": 1, "word_count": 20, "sentence_count_basic": 2},
}
RATIO_COLUMNS = ("entity_density", "imperative_verb_ratio", "type_token_ratio", "moving_avg_type_token_ratio",
                 "entity_mention_ratio")
COUNT_COLUMNS = ("token_count", "sentence_count", "entity_count", "unique_entity_count", "pronoun_count")


def _git(*arguments: str) -> None:
    subprocess.run(["git", *arguments], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DocstyleStructureTests(unittest.TestCase):
    def setUp(self):
        self.catalogs = load_catalogs()

    def test_fixture_has_thirteen_files_and_every_expected_value_is_reproduced(self):
        names = sorted(path.name for path in FIXTURE.glob("*.md"))
        self.assertEqual(names, sorted(EXPECTED))
        self.assertEqual(len(names), 13)
        for name, expected in EXPECTED.items():
            raw = (FIXTURE / name).read_bytes()
            text = raw.decode("utf-8")
            values = structure_metrics(text, raw)
            values.update(marker_metrics(text_view(text), values["word_count"], self.catalogs))
            with self.subTest(name=name):
                self.assertEqual(values["bytes"], len(raw))
                self.assertEqual(values["chars"], len(text))
                for column in ("lines", "header_count", "code_fence_count", "code_block_lines",
                               "bullet_list_count", "numbered_list_count", "checklist_count", "list_item_count",
                               "paragraph_count", "word_count", "sentence_count_basic",
                               *(f"header_h{level}_count" for level in range(1, 7))):
                    self.assertEqual(values[column], expected[column], column)
                words = expected["word_count"]
                self.assertEqual(values["vagueness_density"], expected["vagueness"] / words * 1000 if words else 0.0)
                self.assertEqual(values["constraint_density"], expected["constraint"] / words * 1000 if words else 0.0)

    def test_text_view_drops_fenced_blocks_and_inline_code_only(self):
        self.assertEqual(text_view("a `b` c\n```\nx\n```\nd `e\nf` g\n"), "a  c\nd  g")
        self.assertEqual(text_view("---\ntitle: T\n---\n{% data x %}"), "---\ntitle: T\n---\n{% data x %}")
        self.assertEqual(text_view("~~~\nunclosed\n- code"), "")

    def test_typographic_apostrophes_count_as_bytes_not_chars(self):
        raw = (FIXTURE / "markers.md").read_bytes()
        values = structure_metrics(raw.decode("utf-8"), raw)
        self.assertEqual(values["bytes"], values["chars"] + 4)

    def test_marker_rule_is_longest_match_non_overlapping_and_bounded(self):
        pattern = self.catalogs["constraint"]["pattern"]
        self.assertEqual(count_markers("must not", pattern), 1)
        self.assertEqual(count_markers("must-not", pattern), 1)
        self.assertEqual(count_markers("if and only if", pattern), 1)
        self.assertEqual(count_markers("Mustard cannot", pattern), 1)
        self.assertEqual(count_markers("don’t don't", pattern), 2)
        vague = self.catalogs["vagueness"]["pattern"]
        self.assertEqual(count_markers("can cannot can't", vague), 1)

    def test_catalogs_are_the_capstone_copies(self):
        for name in ("docstyle_vagueness_v1.json", "docstyle_constraint_v1.json"):
            if not (CAPSTONE_CATALOGS / name).is_file():
                self.skipTest("Capstone checkout not beside this tree")
            self.assertEqual((CATALOG_DIR / name).read_bytes(), (CAPSTONE_CATALOGS / name).read_bytes())
        self.assertEqual(self.catalogs["vagueness"]["printed_entry_count"], 30)
        self.assertEqual(self.catalogs["constraint"]["printed_entry_count"], 32)
        self.assertEqual(self.catalogs["constraint"]["paper_stated_entry_count"], 28)


class DocstyleSpacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nlp, cls.identity = load_spacy()
        cls.catalogs = load_catalogs()

    def test_model_identity_is_the_pinned_one(self):
        self.assertEqual(self.identity["meta"], {"lang": "en", "name": "core_web_sm", "version": "3.8.0",
                                                  "spacy_version": ">=3.8.0,<3.9.0"})
        self.assertTrue(self.identity["spacy_version"].startswith("3.8."))
        self.assertIn("ner", self.identity["pipeline"])
        self.assertRegex(self.identity["model_directory_inventory_sha256"], r"^[0-9a-f]{64}$")

    def test_every_fixture_row_is_typed_bounded_and_zero_safe(self):
        rows = [{"path": path.name, "doc_id": path.stem, "source": path} for path in sorted(FIXTURE.glob("*.md"))]
        table = measure_rows(rows, "fixture", "0" * 40, self.nlp, self.catalogs)
        self.assertEqual(len(table), 13)
        for row in table:
            with self.subTest(path=row["doc_path"]):
                self.assertEqual(list(row), list(ROW_COLUMNS))
                self.assertIsNone(row["error"])
                for column in COUNT_COLUMNS:
                    self.assertIsInstance(row[column], int)
                    self.assertGreaterEqual(row[column], 0)
                for column in RATIO_COLUMNS:
                    self.assertIsInstance(row[column], float)
                    self.assertGreaterEqual(row[column], 0.0)
                    self.assertLessEqual(row[column], 1.0)
                self.assertIsInstance(row["entity_type_counts"], dict)
                self.assertEqual(list(row["entity_type_counts"]), sorted(row["entity_type_counts"]))
                self.assertEqual(sum(row["entity_type_counts"].values()), row["entity_count"])
                self.assertLessEqual(len(row["top_entities"]), 10)
                if row["entity_count"] == 0:
                    self.assertEqual(row["entity_mention_ratio"], 0.0)
                if row["token_count"] <= 50:
                    self.assertEqual(row["moving_avg_type_token_ratio"], row["type_token_ratio"])
        empty = next(row for row in table if row["doc_path"] == "empty.md")
        for column in NUMERIC_COLUMNS:
            if "lexicon_mention" in column or column == "identifier_resolution_ratio":
                self.assertIsNone(empty[column], column)  # S055: null without a lexicon / without identifiers
            else:
                self.assertEqual(empty[column], 0, column)
        self.assertEqual(empty["entity_type_counts"], {})
        self.assertEqual(empty["identifier_class_counts"], {})
        self.assertEqual(empty["top_entities"], [])
        self.assertEqual((empty["doc_id"], empty["corpus_id"], empty["commit"]), ("empty", "fixture", "0" * 40))

    def test_entities_and_imperatives_are_found_where_the_text_has_them(self):
        values = nlp_metrics(self.nlp((FIXTURE / "entities.md").read_text(encoding="utf-8")))
        self.assertGreater(values["entity_count"], 0)
        self.assertGreaterEqual(values["unique_entity_count"], 1)
        self.assertLessEqual(values["unique_entity_count"], values["entity_count"])
        self.assertEqual(values["top_entities"], sorted(values["top_entities"], key=lambda item: (-item[1], item[0])))
        plain = nlp_metrics(self.nlp((FIXTURE / "plain.md").read_text(encoding="utf-8")))
        self.assertEqual(plain["sentence_count"], 3)
        # "Restart the service" is a subjectless VB root; "It may take", "You must not skip" carry subjects.
        self.assertGreater(plain["imperative_verb_ratio"], 0.0)
        self.assertLess(plain["imperative_verb_ratio"], 1.0)
        self.assertGreater(plain["pronoun_count"], 0)

    def test_failure_policy_keeps_the_row_with_error_and_null_metrics(self):
        rows = [{"path": "bad.md", "doc_id": "bad", "source": FIXTURE / "does-not-exist.md"}]
        table = measure_rows(rows, "fixture", "0" * 40, self.nlp, self.catalogs)
        self.assertTrue(table[0]["error"].startswith("FileNotFoundError"))
        self.assertTrue(all(table[0][column] is None for column in EXPORT_METRIC_COLUMNS + EXTRA_COLUMNS))
        summary = summarize(table, None)
        self.assertEqual((summary["row_count"], summary["measured_row_count"], summary["error_row_count"]), (1, 0, 1))


class DocstyleSummaryTests(unittest.TestCase):
    def test_quantiles_are_order_statistics_and_stdev_is_sample(self):
        values = [5.0, 1.0, 3.0, 1.0, 2.0]
        self.assertEqual(order_statistic(values, 0.25), 1.0)
        self.assertEqual(order_statistic(values, 0.75), 3.0)
        summary = column_summary(values)
        self.assertEqual(summary["count"], 5)
        self.assertEqual(summary["median"], 2.0)
        self.assertAlmostEqual(summary["stdev"], 1.6733200530681511)
        self.assertEqual(column_summary([2.0])["stdev"], None)
        self.assertEqual(column_summary([])["mean"], None)

    def test_percentile_rank_uses_half_the_ties(self):
        values = [1.0, 1.0, 2.0, 3.0, 5.0]
        self.assertEqual(percentile_rank(values, 1.0), 0.2)
        self.assertEqual(percentile_rank(values, 2.0), 0.5)
        self.assertEqual(percentile_rank(values, 5.0), 0.9)

    def test_spearman_is_mid_rank_and_null_with_a_reason(self):
        self.assertEqual(spearman([1, 2, 3, 4], [2, 4, 6, 8])["rho"], 1.0)
        self.assertAlmostEqual(spearman([1, 2, 2, 3], [1, 3, 2, 4])["rho"], 3 / 10 ** 0.5)
        self.assertEqual(spearman([1, 2, 3], [1, 1, 1]), {"rho": None, "n": 3, "reason": "constant_column"})
        self.assertEqual(spearman([1, 2], [1, 2]), {"rho": None, "n": 2, "reason": "insufficient_rows"})

    def test_summary_covers_numeric_columns_only_drops_zero_token_rows_and_joins_support(self):
        def row(doc_id, token_count, value):
            base = {column: value for column in NUMERIC_COLUMNS}
            base.update({"doc_id": doc_id, "doc_path": doc_id + ".md", "corpus_id": "c", "commit": "0" * 40,
                         "error": None, "entity_type_counts": {}, "top_entities": [], "token_count": token_count})
            return base
        table = [row("a", 10, 1.0), row("b", 20, 2.0), row("c", 30, 2.0), row("d", 0, 9.0)]
        summary = summarize(table, ["b", "zzz"])
        self.assertEqual(set(summary["columns"]), set(NUMERIC_COLUMNS))
        self.assertNotIn("entity_type_counts", summary["columns"])
        self.assertEqual(summary["spearman_rho"]["rows"], 3)
        self.assertEqual(summary["spearman_rho"]["zero_token_rows_dropped"], 1)
        self.assertEqual(summary["support"]["matched_count"], 1)
        self.assertEqual(summary["support"]["unmatched_ids"], ["zzz"])
        self.assertEqual(summary["support"]["rows"]["b"]["columns"]["vagueness_density"]["percentile_rank"], 0.5)
        self.assertEqual(summary["support"]["rows"]["b"]["columns"]["vagueness_density"]["value"], 2.0)
        self.assertEqual(summary["columns"]["vagueness_density"]["count"], 4)


class DocstyleRequestTests(unittest.TestCase):
    def config(self, **corpus):
        return {"schema_version": CONFIG_SCHEMA, "corpus_id": "argo", "candidate": {"commit": "a" * 40, "tree": "b" * 40},
                "corpus": {"root": "docs", "extensions": [".md"], "import_policy": "cikm_flat_path_sha1_lower_v1", **corpus}}

    def test_root_dot_is_admitted_and_traversal_is_refused(self):
        validate_config({**self.config(), "corpus": {**self.config()["corpus"], "root": "."}})
        for root in ("../x", "docs/..", "/abs", "docs\\x", ""):
            with self.assertRaises(ValidationError):
                validate_config({**self.config(), "corpus": {**self.config()["corpus"], "root": root}})

    def test_policies_and_patterns_are_closed(self):
        validate_config(self.config(exclude_policy="cikm_faq_pattern_v1"))
        validate_config(self.config(exclude_policy="cikm_faq_pattern_v2", faq_patterns=["*faq*"]))
        validate_config(self.config(exclude_policy=None))
        with self.assertRaises(ValidationError):
            validate_config(self.config(exclude_policy="cikm_faq_pattern_v3"))
        with self.assertRaises(ValidationError):
            validate_config(self.config(exclude_policy="cikm_faq_pattern_v2"))
        with self.assertRaises(ValidationError):
            validate_config(self.config(exclude_policy="cikm_faq_pattern_v1", faq_patterns=["*faq*"]))
        with self.assertRaises(ValidationError):
            validate_config({**self.config(), "corpus": {**self.config()["corpus"], "extensions": [".rst"]}})
        with self.assertRaises(ValidationError):
            validate_config({**self.config(), "support_path": "../support.json"})
        with self.assertRaises(ValidationError):
            validate_config({**self.config(), "schema_version": "other"})

    def test_lexicon_fields_are_exclusive_relative_and_closed(self):
        block = {"policy": "repo_lexicon_v1", "source_root": ".", "exclude_root": "docs"}
        validate_config({**self.config(), "lexicon": block})
        validate_config({**self.config(), "lexicon": {**block, "exclude_root": None}})
        validate_config({**self.config(), "lexicon_path": "repo_lexicon.v1.json"})
        validate_config({**self.config(), "lexicon_path": "repo_lexicon.v1.json", "lexicon_commit_policy": "frozen_snapshot"})
        for bad in ({"lexicon": block, "lexicon_path": "x.json"},
                    {"lexicon": {**block, "policy": "other"}},
                    {"lexicon": {**block, "exclude_root": "."}},
                    {"lexicon": {**block, "source_root": "../x"}},
                    {"lexicon_path": "."},
                    {"lexicon_path": "../x.json"},
                    {"lexicon_commit_policy": "frozen_snapshot"},
                    {"lexicon_path": "x.json", "lexicon_commit_policy": "other"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                validate_config({**self.config(), **bad})

    def test_support_file_is_read_as_it_is(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests") as temp:
            path = Path(temp) / "support.json"
            path.write_text(json.dumps({"records": [{"query_id": "q1", "target_ids": ["b", "a"]}],
                                        "targets": [{"corpus_id": "c", "target_id": "b"}, {"corpus_id": "c", "target_id": "a"},
                                                    {"corpus_id": "c", "target_id": "b"}]}), encoding="utf-8")
            ids, digest = load_support_ids(path)
            self.assertEqual(ids, ["a", "b"])
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            path.write_text(json.dumps({"targets": []}), encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_support_ids(path)


class DocstyleOperatorTests(unittest.TestCase):
    """The operator entry end to end over a git candidate built from the fixture (container venue)."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None or not Path("/usr/bin/git").exists():
            raise unittest.SkipTest("the operator's git admission needs /usr/bin/git (container venue)")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "docstyle_measurement_operator", ROOT / "containers" / "verification" / "docstyle_measurement.py")
        cls.operator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.operator)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp_unit_tests")
        base = Path(self.temp.name)
        self.candidate = base / "candidate"
        (self.candidate / "docs" / "faq").mkdir(parents=True)
        for path in FIXTURE.glob("*.md"):
            shutil.copy(path, self.candidate / "docs" / path.name)
        shutil.copy(FIXTURE / "plain.md", self.candidate / "docs" / "faq" / "questions.md")
        _git("init", "-q", str(self.candidate))
        _git("-C", str(self.candidate), "config", "user.email", "fixture@example.invalid")
        _git("-C", str(self.candidate), "config", "user.name", "Fixture")
        _git("-C", str(self.candidate), "add", "-A")
        _git("-C", str(self.candidate), "commit", "-qm", "fixture")
        self.commit = subprocess.run(["git", "-C", str(self.candidate), "rev-parse", "HEAD"], check=True,
                                     capture_output=True, text=True).stdout.strip()
        self.tree = subprocess.run(["git", "-C", str(self.candidate), "rev-parse", "HEAD^{tree}"], check=True,
                                   capture_output=True, text=True).stdout.strip()
        self.input = base / "input"
        self.input.mkdir()
        self.outputs = []

    def tearDown(self):
        self.temp.cleanup()

    def write_input(self, config, support=None, lexicon=None):
        for optional in ("support.json", "repo_lexicon.v1.json"):
            (self.input / optional).unlink(missing_ok=True)
        (self.input / "config.json").write_bytes(canonical(config) + b"\n")
        if support is not None:
            (self.input / "support.json").write_bytes(canonical(support) + b"\n")
        if lexicon is not None:
            (self.input / "repo_lexicon.v1.json").write_bytes(lexicon)
        inventory = [row for row in regular_inventory(self.input) if row["path"] != "operator-request.json"]
        request = {"schema_version": REQUEST_SCHEMA, "command": "measure-docstyle", "config_path": "config.json",
                   "input_inventory": inventory, "input_inventory_sha256": sha256_bytes(canonical(inventory))}
        (self.input / "operator-request.json").write_bytes(canonical(request) + b"\n")

    def run_operator(self):
        output = Path(self.temp.name) / f"output-{len(self.outputs)}"
        output.mkdir()
        (output / ".retrieval-arena-output-owner").write_text("retrieval_arena.output_root.v1\n", encoding="utf-8")
        self.outputs.append(output)
        with mock.patch.object(self.operator, "INPUT", self.input), \
                mock.patch.object(self.operator, "CANDIDATE", self.candidate), \
                mock.patch.object(self.operator, "OUTPUT", output), \
                mock.patch.dict(os.environ, {"RA_SOURCE_COMMIT": "c" * 40, "RA_IMAGE_REFERENCE": "sha256:" + "d" * 64}):
            code = self.operator.main()
        return code, output

    def config(self, **extra):
        return {"schema_version": CONFIG_SCHEMA, "corpus_id": "fixture",
                "candidate": {"commit": self.commit, "tree": self.tree},
                "corpus": {"root": "docs", "extensions": [".md"], "import_policy": "cikm_flat_path_sha1_lower_v1",
                           "exclude_policy": "cikm_faq_pattern_v1"}, **extra}

    def test_measures_twice_byte_identically_with_receipt_and_support(self):
        plain_id = imported_document_name("plain.md", "cikm_flat_path_sha1_lower_v1")[1]
        faq_id = imported_document_name("faq/questions.md", "cikm_flat_path_sha1_lower_v1")[1]
        support = {"records": [{"query_id": "q", "target_ids": [plain_id]}],
                   "targets": [{"corpus_id": "fixture", "target_id": plain_id},
                               {"corpus_id": "fixture", "target_id": "missing__00000000"}]}
        self.write_input(self.config(support_path="support.json"), support)
        code, first = self.run_operator()
        self.assertEqual(code, 0, (first / "operator_receipt.v1.json").read_text(encoding="utf-8"))
        receipt = json.loads((first / "operator_receipt.v1.json").read_bytes())
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["row_count"], 13)
        self.assertEqual(receipt["excluded_document_count"], 1)
        self.assertEqual(receipt["excluded_document_ids"], [faq_id])
        self.assertEqual(receipt["candidate"], {"commit": self.commit, "tree": self.tree})
        self.assertEqual(receipt["identity"]["source_commit"], "c" * 40)
        self.assertEqual(receipt["error_count"], 0)
        manifest = json.loads((first / "docstyle_metrics_manifest.json").read_bytes())
        self.assertEqual(manifest["row_count"], 13)
        self.assertEqual(manifest["algorithm_choices"], ALGORITHM_CHOICES)
        self.assertEqual(manifest["spacy"]["meta"]["version"], "3.8.0")
        self.assertRegex(manifest["python_version"], r"^3\.\d+\.\d+$")
        self.assertEqual(manifest["support"]["id_count"], 2)
        self.assertEqual(manifest["table_sha256"], receipt["outputs"]["docstyle_metrics.v1.jsonl"])
        summary = json.loads((first / "docstyle_summary.v1.json").read_bytes())
        self.assertEqual(summary["support"]["matched_count"], 1)
        self.assertEqual(summary["support"]["unmatched_ids"], ["missing__00000000"])
        self.assertEqual(summary["spearman_rho"]["signals"],
                         ["imperative_verb_ratio", "constraint_density", "vagueness_density", "entity_density"])
        rows = [json.loads(line) for line in (first / "docstyle_metrics.v1.jsonl").read_bytes().splitlines()]
        self.assertEqual([row["doc_path"] for row in rows], sorted(row["doc_path"] for row in rows))
        self.assertTrue(all(row["commit"] == self.commit and row["corpus_id"] == "fixture" for row in rows))
        for line in (first / "docstyle_metrics.v1.jsonl").read_bytes().splitlines():
            self.assertEqual(line, canonical(json.loads(line)))
        code, second = self.run_operator()
        self.assertEqual(code, 0)
        for name in ("docstyle_metrics.v1.jsonl", "docstyle_summary.v1.json", "docstyle_metrics_manifest.json"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes(), name)

    def test_lexicon_is_built_then_consumed_and_absent_gives_nulls(self):
        # absent: the identifier columns are present, the lexicon columns null, the manifest says absent
        self.write_input(self.config())
        code, absent = self.run_operator()
        self.assertEqual(code, 0, (absent / "operator_receipt.v1.json").read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in (absent / "docstyle_metrics.v1.jsonl").read_bytes().splitlines()]
        self.assertEqual(set(rows[0]), set(ROW_COLUMNS))  # canonical rows carry sorted keys
        self.assertTrue(all(row["lexicon_mention_count"] is None and row["identifier_count"] is not None
                            for row in rows if row["error"] is None))
        manifest = json.loads((absent / "docstyle_metrics_manifest.json").read_bytes())
        self.assertEqual(manifest["schema_version"], TABLE_SCHEMA)
        self.assertEqual(manifest["lexicon"], {"policy": None, "origin": "absent"})
        summary = json.loads((absent / "docstyle_summary.v1.json").read_bytes())
        self.assertEqual(summary["schema_version"], SUMMARY_SCHEMA)
        self.assertEqual(summary["columns"]["lexicon_mention_count"]["count"], 0)
        self.assertGreater(summary["columns"]["identifier_count"]["count"], 0)
        self.assertIsNone(json.loads((absent / "operator_receipt.v1.json").read_bytes())["outputs"]["repo_lexicon.v1.json"])
        # built: a source file outside the corpus root is harvested and the lexicon is written and hashed
        (self.candidate / "pkg").mkdir()
        (self.candidate / "pkg" / "app.go").write_text(
            'package pkg\n\ntype SyncPolicy struct{}\n\nconst Key = "argocd.argoproj.io/hook-delete-policy"\n', encoding="utf-8")
        _git("-C", str(self.candidate), "add", "-A")
        _git("-C", str(self.candidate), "commit", "-qm", "source")
        self.commit = subprocess.run(["git", "-C", str(self.candidate), "rev-parse", "HEAD"], check=True,
                                     capture_output=True, text=True).stdout.strip()
        self.tree = subprocess.run(["git", "-C", str(self.candidate), "rev-parse", "HEAD^{tree}"], check=True,
                                   capture_output=True, text=True).stdout.strip()
        self.write_input(self.config(lexicon={"policy": "repo_lexicon_v1", "source_root": ".", "exclude_root": "docs"}))
        code, built = self.run_operator()
        self.assertEqual(code, 0, (built / "operator_receipt.v1.json").read_text(encoding="utf-8"))
        lexicon = json.loads((built / "repo_lexicon.v1.json").read_bytes())
        self.assertEqual([row["token"] for row in lexicon["entries"]], ["SyncPolicy", "argocd.argoproj.io/hook-delete-policy"])
        self.assertEqual(lexicon["commit"], self.commit)
        receipt = json.loads((built / "operator_receipt.v1.json").read_bytes())
        self.assertEqual(receipt["outputs"]["repo_lexicon.v1.json"], sha256_bytes((built / "repo_lexicon.v1.json").read_bytes()))
        manifest = json.loads((built / "docstyle_metrics_manifest.json").read_bytes())
        self.assertEqual((manifest["lexicon"]["origin"], manifest["lexicon"]["entry_count"]), ("built", 2))
        rows = [json.loads(line) for line in (built / "docstyle_metrics.v1.jsonl").read_bytes().splitlines()]
        self.assertTrue(all(row["lexicon_mention_count"] is not None for row in rows if row["error"] is None))
        # consumed: the same lexicon through lexicon_path gives the same lexicon columns; a foreign commit is refused
        # unless frozen_snapshot
        self.write_input(self.config(lexicon_path="repo_lexicon.v1.json"), lexicon=(built / "repo_lexicon.v1.json").read_bytes())
        code, consumed = self.run_operator()
        self.assertEqual(code, 0, (consumed / "operator_receipt.v1.json").read_text(encoding="utf-8"))
        self.assertEqual((consumed / "docstyle_metrics.v1.jsonl").read_bytes(), (built / "docstyle_metrics.v1.jsonl").read_bytes())
        self.assertFalse((consumed / "repo_lexicon.v1.json").exists())
        foreign = dict(lexicon, commit="f" * 40)
        self.write_input(self.config(lexicon_path="repo_lexicon.v1.json"), lexicon=canonical(foreign) + b"\n")
        code, refused = self.run_operator()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((refused / "operator_receipt.v1.json").read_bytes())["error_code"],
                         "docstyle-lexicon-commit-mismatch")
        self.write_input(self.config(lexicon_path="repo_lexicon.v1.json", lexicon_commit_policy="frozen_snapshot"),
                         lexicon=canonical(foreign) + b"\n")
        code, frozen = self.run_operator()
        self.assertEqual(code, 0, (frozen / "operator_receipt.v1.json").read_text(encoding="utf-8"))
        self.assertEqual(json.loads((frozen / "docstyle_metrics_manifest.json").read_bytes())["lexicon"]["commit_policy"],
                         "frozen_snapshot")

    def test_root_dot_measures_the_repository_and_v2_needs_patterns(self):
        config = self.config()
        config["corpus"] = {"root": ".", "extensions": [".md"], "import_policy": "cikm_flat_path_sha1_lower_v1"}
        self.write_input(config)
        code, output = self.run_operator()
        self.assertEqual(code, 0)
        receipt = json.loads((output / "operator_receipt.v1.json").read_bytes())
        self.assertEqual(receipt["row_count"], 14)
        self.assertEqual(receipt["excluded_document_count"], 0)
        rows = [json.loads(line) for line in (output / "docstyle_metrics.v1.jsonl").read_bytes().splitlines()]
        self.assertIn("docs/faq/questions.md", [row["doc_path"] for row in rows])

    def test_dirty_tree_wrong_identity_and_foreign_input_are_refused(self):
        self.write_input(self.config())
        (self.candidate / "docs" / "extra.md").write_text("dirty\n", encoding="utf-8")
        code, output = self.run_operator()
        self.assertEqual(code, 1)
        receipt = json.loads((output / "operator_receipt.v1.json").read_bytes())
        self.assertEqual(receipt["error_code"], "candidate-retrieval-candidate-admission")
        self.assertFalse((output / "docstyle_metrics.v1.jsonl").exists())
        (self.candidate / "docs" / "extra.md").unlink()
        config = self.config()
        config["candidate"]["tree"] = "0" * 40
        self.write_input(config)
        code, output = self.run_operator()
        self.assertEqual(code, 1)
        (self.input / "stray.txt").write_text("x", encoding="utf-8")
        self.write_input(self.config())
        code, output = self.run_operator()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((output / "operator_receipt.v1.json").read_bytes())["error_code"],
                         "docstyle-input-root-contents")


if __name__ == "__main__":
    unittest.main()
