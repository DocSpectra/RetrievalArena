from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from retrieval_arena import candidate_retrieval
from retrieval_arena.candidate_retrieval import EXCLUDE_POLICY_CIKM_V2, EXCLUDE_POLICY_FAQ, IMPORT_POLICY_LOWER, canonical, excluded_by_policy, expected_provider_metadata, imported_document_name, prepare_provider_input, project_ranked_predictions, regular_inventory, sha256_bytes, validate_config
from retrieval_arena.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "retrieval-arena/rag-lexical-topk@sha256:" + "a" * 64
GRAPH_IMAGE = "retrieval-arena/rag-graph-ranked-expand@sha256:" + "b" * 64


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

    def test_import_policy_lowercase_matches_the_dataset_importer_rule(self):
        """S044 (ID-1/ID-2): the lowercase policy reproduces the S019 support ids on an uppercase path."""
        as_written = imported_document_name("operator-manual/applicationset/Generators-Git.md")
        lowered = imported_document_name("operator-manual/applicationset/Generators-Git.md", IMPORT_POLICY_LOWER)
        self.assertEqual(as_written[1], "operator_manual_applicationset_generators_git__ab35aa5e")
        self.assertEqual(lowered[1], "operator_manual_applicationset_generators_git__523ee31f")
        self.assertEqual(imported_document_name("docs/guide.md", IMPORT_POLICY_LOWER), imported_document_name("docs/guide.md"))
        self.assertEqual(imported_document_name("Foo.md", IMPORT_POLICY_LOWER), imported_document_name("foo.md", IMPORT_POLICY_LOWER))
        with self.assertRaisesRegex(ValidationError, "import-policy"): imported_document_name("guide.md", "cikm_flat_path_md5_v1")
        value = self.config(); value["corpus"]["import_policy"] = "cikm_flat_path_md5_v1"
        with self.assertRaisesRegex(ValidationError, "import-policy"): validate_config(value)
        docs = self.repo / "docs"; (docs / "Sync-Waves.md").write_text("# Waves" + chr(10) + "Ordered." + chr(10), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "docs/Sync-Waves.md"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "uppercase"], check=True)
        value = self.config(); value["corpus"]["import_policy"] = IMPORT_POLICY_LOWER
        value["candidate"] = {"commit": subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip(),
                              "tree": subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()}
        destination = self.root / "provider-input-lower"
        launch = prepare_provider_input(value, self.input, self.repo, destination)
        self.assertEqual(launch["import_policy"], IMPORT_POLICY_LOWER)
        self.assertTrue((destination / "corpus" / "guide__bbbdfa65.md").is_file())
        self.assertTrue((destination / "corpus" / ("sync_waves__" + hashlib.sha1(b"sync-waves.md").hexdigest()[:8] + ".md")).is_file())
        documents = json.loads((destination.parent / "documents.json").read_bytes())
        self.assertEqual({row["path"]: row["doc_id"] for row in documents if row["path"] == "Sync-Waves.md"},
                         {"Sync-Waves.md": "sync_waves__" + hashlib.sha1(b"sync-waves.md").hexdigest()[:8]})

    def test_case_only_path_pair_is_refused_not_merged(self):
        """S044: two documents whose paths differ only by case must never share one id silently."""
        docs = self.repo / "docs"
        (docs / "Guide.md").write_text("# Other guide" + chr(10) + "Different content." + chr(10), encoding="utf-8")
        listed = subprocess.check_output(["git", "-C", str(self.repo), "ls-files", "--", "docs"], text=True).split()
        try:
            subprocess.run(["git", "-C", str(self.repo), "add", "docs/Guide.md"], check=True)
            subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "case pair"], check=True)
        except subprocess.CalledProcessError:
            self.skipTest("filesystem folds case: the pair cannot coexist")
        if sorted(subprocess.check_output(["git", "-C", str(self.repo), "ls-files", "--", "docs"], text=True).split()) == sorted(listed):
            self.skipTest("filesystem folds case: the pair cannot coexist")
        value = self.config(); value["corpus"]["import_policy"] = IMPORT_POLICY_LOWER
        value["candidate"] = {"commit": subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip(),
                              "tree": subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()}
        with self.assertRaisesRegex(ValidationError, "document-id-collision"):
            prepare_provider_input(value, self.input, self.repo, self.root / "collision")

    def test_closed_config_and_provider_selection(self):
        value = self.config(); validate_config(value); value["unknown"] = True
        with self.assertRaisesRegex(ValidationError, "config-fields"): validate_config(value)
        value = self.config(); value["provider"]["name"] = "substitute"
        with self.assertRaisesRegex(ValidationError, "provider-undeclared"): validate_config(value)

    def test_exact_candidate_and_inventory_prepare(self):
        destination = self.root / "provider-input"
        result = prepare_provider_input(self.config(), self.input, self.repo, destination)
        self.assertEqual(result["candidate_commit"], self.commit)
        self.assertTrue((destination / "corpus" / "guide__bbbdfa65.md").is_file())
        bad = self.config(); bad["candidate"]["tree"] = "0" * 40
        with self.assertRaisesRegex(ValidationError, "candidate-admission"): prepare_provider_input(bad, self.input, self.repo, self.root / "bad")

    def provider_output(self):
        output = self.root / "provider-output"; output.mkdir()
        (output / "metadata.json").write_text(json.dumps({"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": 1, "max_context_chars_per_doc": 1500, "answer_policy": "head_of_document_v1"}), encoding="utf-8")
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

    def provenance_output(self, provenance=None, metadata_policy="best_passage_v1"):
        output = self.root / "provider-output"; output.mkdir()
        (output / "metadata.json").write_text(json.dumps({"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": 1, "max_context_chars_per_doc": 1500, "answer_policy": metadata_policy}), encoding="utf-8")
        item = {"doc_id": "guide__bbbdfa65", "text": "Sync waves order resources.", "score": 1.0, "source": "lexical_topk", "rank": 1}
        if provenance is None:
            provenance = [{"doc_id": "guide__bbbdfa65", "section_heading": "Guide", "passage_index": 0, "char_start": 8, "char_end": 35}]
        row = {"question_id": "q1", "question": "sync waves", "generated_answer": "Sync waves order resources.", "retrieved_context": [item], "answer_provenance": provenance}
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        return output

    def test_answer_policy_config_default_and_rejection(self):
        legacy = validate_config(self.config())
        self.assertEqual(legacy["provider"]["answer_policy"], "head_of_document_v1")
        self.assertNotIn("answer_policy", self.config()["provider"])
        value = self.config(); value["provider"]["answer_policy"] = "best_passage_v1"
        self.assertEqual(validate_config(value)["provider"]["answer_policy"], "best_passage_v1")
        value = self.config(); value["provider"]["answer_policy"] = "whole_document_v9"
        with self.assertRaisesRegex(ValidationError, "provider-policy"): validate_config(value)
        value = self.config(); value["provider"]["answer_policy"] = None
        with self.assertRaisesRegex(ValidationError, "provider-policy"): validate_config(value)
        destination = self.root / "provider-input"
        prepare_provider_input(self.config(), self.input, self.repo, destination)
        self.assertEqual(json.loads((destination / "config.yaml").read_text(encoding="utf-8")), {"answer_policy": "head_of_document_v1", "max_context_chars_per_doc": 1500, "top_k": 1})

    def test_legacy_policy_projection_rejects_metadata_and_row_drift(self):
        documents = self.root / "documents.json"
        documents.write_text(json.dumps([{"doc_id": "guide__bbbdfa65", "expected_text": "Sync waves"}]), encoding="utf-8")
        output = self.provider_output()
        (output / "metadata.json").write_text(json.dumps({"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": 1, "max_context_chars_per_doc": 1500}), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "provider-metadata-fields"):
            project_ranked_predictions(self.config(), output, self.input, documents)
        (output / "metadata.json").write_text(json.dumps({"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": 1, "max_context_chars_per_doc": 1500, "answer_policy": "head_of_document_v1"}), encoding="utf-8")
        ranked, answers = project_ranked_predictions(self.config(), output, self.input, documents)
        self.assertEqual(answers, [{"query_id": "q1", "generated_answer": "Sync waves"}])
        self.assertEqual(set(ranked[0]["items"][0]), {"support_target_id", "rank", "score", "source_rank", "source_item_id", "source_provenance_sha256"})
        row = json.loads((output / "predictions.jsonl").read_text(encoding="utf-8")); row["answer_provenance"] = []
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "provider-row-fields"):
            project_ranked_predictions(self.config(), output, self.input, documents)
        (output / "metadata.json").write_text(json.dumps({"name": "rag_lexical_topk", "deterministic": True, "ranker": "bm25", "top_k": 1, "max_context_chars_per_doc": 1500, "answer_policy": "best_passage_v1"}), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "provider-metadata"):
            project_ranked_predictions(self.config(), output, self.input, documents)

    def test_best_passage_projection_carries_provenance(self):
        config = self.config(); config["provider"]["answer_policy"] = "best_passage_v1"
        provider_input = self.root / "provider-input"
        prepare_provider_input(config, self.input, self.repo, provider_input)
        documents = self.root / "documents.json"
        rows = [{"doc_id": "guide__bbbdfa65", "imported_path": "guide__bbbdfa65.md", "expected_text": "Sync waves order resources."}]
        documents.write_text(json.dumps(rows), encoding="utf-8")
        output = self.provenance_output()
        with self.assertRaisesRegex(ValidationError, "provider-metadata"):
            project_ranked_predictions(self.config(), output, self.input, documents)
        ranked, answers = project_ranked_predictions(config, output, self.input, documents)
        self.assertEqual(answers, [{"query_id": "q1", "generated_answer": "Sync waves order resources.", "answer_policy": "best_passage_v1", "answer_provenance": [{"doc_id": "guide__bbbdfa65", "section_heading": "Guide", "passage_index": 0, "char_start": 8, "char_end": 35}]}])
        self.assertEqual((ranked, answers), project_ranked_predictions(config, output, self.input, documents, provider_input))
        self.assertEqual(ranked[0]["items"][0]["support_target_id"], "guide__bbbdfa65")
        self.assertEqual(set(ranked[0]), {"schema_version", "query_id", "context_item_budget_k", "max_rank", "retrieval_exhausted", "items", "duplicate_collapse_count", "duplicate_collapse_sha256"})

    def test_best_passage_provenance_validation_fails_closed(self):
        import shutil
        config = self.config(); config["provider"]["answer_policy"] = "best_passage_v1"
        provider_input = self.root / "provider-input"
        prepare_provider_input(config, self.input, self.repo, provider_input)
        documents = self.root / "documents.json"
        documents.write_text(json.dumps([{"doc_id": "guide__bbbdfa65", "imported_path": "guide__bbbdfa65.md", "expected_text": "Sync waves order resources."}]), encoding="utf-8")
        good = {"doc_id": "guide__bbbdfa65", "section_heading": None, "passage_index": 0, "char_start": 8, "char_end": 35}
        bad_entries = [
            {**good, "doc_id": "other__00000000"}, {**good, "passage_index": -1}, {**good, "passage_index": True},
            {**good, "char_start": 35}, {**good, "char_start": -1}, {**good, "section_heading": 3},
            {k: v for k, v in good.items() if k != "char_end"}, {**good, "extra": 1}, "not-a-dict",
        ]
        for entry in bad_entries:
            output = self.provenance_output([entry])
            with self.assertRaisesRegex(ValidationError, "provider-provenance", msg=repr(entry)):
                project_ranked_predictions(config, output, self.input, documents, provider_input)
            shutil.rmtree(output)
        output = self.provenance_output({"doc_id": "guide__bbbdfa65"})
        with self.assertRaisesRegex(ValidationError, "provider-provenance"):
            project_ranked_predictions(config, output, self.input, documents)
        shutil.rmtree(output)
        output = self.provenance_output([{**good, "char_end": 37}])
        project_ranked_predictions(config, output, self.input, documents)
        with self.assertRaisesRegex(ValidationError, "provider-provenance"):
            project_ranked_predictions(config, output, self.input, documents, provider_input)
        shutil.rmtree(output)
        output = self.provenance_output()
        row = json.loads((output / "predictions.jsonl").read_text(encoding="utf-8")); del row["answer_provenance"]
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "provider-row-fields"):
            project_ranked_predictions(config, output, self.input, documents, provider_input)

    def test_operator_parity_and_no_scoring(self):
        for relative in ("scripts/run-verification.ps1", "scripts/run-verification.sh"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in ("candidate-retrieve", "--network", "none", "retrieval-arena/[a-z0-9-]+@sha256:[0-9a-f]{64}", "--user 65532:65532"): self.assertIn(token, text)
        self.assertIn("PositionalBinding = $false", (ROOT / "scripts" / "run-verification.ps1").read_text(encoding="utf-8"))
        module = (ROOT / "retrieval_arena" / "candidate_retrieval.py").read_text(encoding="utf-8")
        self.assertNotIn("score_ranked", module); self.assertNotIn("qrels", module)


class GraphProviderTests(CandidateRetrievalTests):
    """S025: a second config-declared provider with per-candidate graph extraction."""

    def setUp(self):
        super().setUp()
        docs = self.repo / "docs"
        (docs / "guide").mkdir()
        (docs / "guide" / "waves.md").write_text("# Waves\nSee [the guide](../guide.md) and [hooks](hooks.md).\n\nPhase ordering.\n", encoding="utf-8")
        (docs / "guide" / "hooks.md").write_text("# Hooks\nResource hooks run in [waves](waves.md). ![img](../img.png) [img](../img.png)\n", encoding="utf-8")
        (docs / "img.png").write_bytes(b"png")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "graph fixture"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.tree = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()

    def graph_config(self, **overrides):
        value = self.config()
        value["provider"] = {"name": "rag_graph_ranked_expand", "image": GRAPH_IMAGE, "platform": "linux/amd64",
                             "network": "none", "top_k": 3, "max_context_chars_per_doc": 1500,
                             "answer_policy": "best_passage_v1", "graph_expansion_hops": 1,
                             "seed_top_k": 2, "neighbor_budget": 1, **overrides}
        value["ranked"]["context_item_budget_k"] = value["provider"]["top_k"]
        return value

    def test_v1_declared_pinned_graph_provider_admitted_and_drift_rejected(self):
        config = validate_config(self.graph_config())
        self.assertEqual(config["provider"]["name"], "rag_graph_ranked_expand")
        cases = {
            "provider-policy": [{"image": "retrieval-arena/rag-graph-ranked-expand:latest"},
                                {"image": IMAGE}],
            "provider-undeclared": [{"name": "rag_graph_expand"}],
            "provider-graph-policy": [{"graph_expansion_hops": 2}, {"seed_top_k": 3}, {"neighbor_budget": -1},
                                      {"seed_top_k": True}],
        }
        for code, overrides_list in cases.items():
            for overrides in overrides_list:
                with self.assertRaisesRegex(ValidationError, code, msg=repr(overrides)):
                    validate_config(self.graph_config(**overrides))
        value = self.graph_config(); del value["provider"]["neighbor_budget"]
        with self.assertRaisesRegex(ValidationError, "provider-fields"): validate_config(value)
        value = self.config(); value["provider"]["seed_top_k"] = 2
        with self.assertRaisesRegex(ValidationError, "provider-fields"): validate_config(value)

    def test_v2_prepare_extracts_directed_flat_id_graph_and_binds_its_hash(self):
        destination = self.root / "provider-input"
        launch = prepare_provider_input(self.graph_config(), self.input, self.repo, destination)
        edges = (destination / "graph_edges.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(edges, ["source,target", "guide_hooks__b1ec6f5b,guide_waves__3440d413",
                                 "guide_waves__3440d413,guide__bbbdfa65", "guide_waves__3440d413,guide_hooks__b1ec6f5b"])
        self.assertEqual(launch["provider_name"], "rag_graph_ranked_expand")
        self.assertEqual(launch["graph_edges_sha256"], hashlib.sha256((destination / "graph_edges.csv").read_bytes()).hexdigest())
        self.assertIn({"path": "graph_edges.csv", "sha256": launch["graph_edges_sha256"],
                       "size_bytes": (destination / "graph_edges.csv").stat().st_size}, launch["provider_input_inventory"])
        self.assertEqual(json.loads((destination / "config.yaml").read_text(encoding="utf-8")),
                         {"answer_policy": "best_passage_v1", "graph_expansion_hops": 1, "max_context_chars_per_doc": 1500,
                          "neighbor_budget": 1, "seed_top_k": 2, "top_k": 3})
        flat = prepare_provider_input(self.config(), self.input, self.repo, self.root / "flat-input")
        self.assertIsNone(flat["graph_edges_sha256"])
        self.assertFalse((self.root / "flat-input" / "graph_edges.csv").exists())

    def faq_fixture(self):
        """S047 FX-1: three FAQ-named pages under three spellings, all linked from guide.md."""
        docs = self.repo / "docs"
        (docs / "notes").mkdir()
        (docs / "faq.md").write_text("# FAQ" + chr(10) + "## How do sync waves work?" + chr(10) + "The gold answer." + chr(10), encoding="utf-8")
        (docs / "guide" / "FAQ.md").write_text("# Guide FAQ" + chr(10) + "Entries." + chr(10), encoding="utf-8")
        (docs / "notes" / "faqs-old.md").write_text("# Old FAQs" + chr(10) + "See [waves](../guide/waves.md)." + chr(10), encoding="utf-8")
        (docs / "guide.md").write_text("# Guide" + chr(10) + "Sync waves order resources. See [faq](faq.md), [guide faq](guide/FAQ.md), "
                                       "[old](notes/faqs-old.md) and [waves](guide/waves.md)." + chr(10), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "faq fixture"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.tree = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()

    def test_s047_faq_exclusion_rule_reads_paths_only(self):
        """S047: the rule is the CIKM exporter's -- ``*faq*`` on the lowercased path or ``faq`` in any part."""
        for path in ("faq.md", "developer-guide/faq.md", "guide/FAQ.md", "notes/faqs-old.md", "FAQ/index.md", "x/myfaqs/y.md"):
            self.assertTrue(excluded_by_policy(path, EXCLUDE_POLICY_FAQ), path)
        for path in ("guide.md", "guide/waves.md", "operator-manual/applicationset/Generators-Git.md"):
            self.assertFalse(excluded_by_policy(path, EXCLUDE_POLICY_FAQ), path)
        self.assertFalse(excluded_by_policy("faq.md", None))
        with self.assertRaisesRegex(ValidationError, "exclude-policy"): excluded_by_policy("faq.md", "faq_by_content_v1")

    def test_s047_exclusion_is_declared_closed_and_required_for_direct_lane_requests(self):
        value = self.graph_config(); value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_FAQ
        self.assertEqual(validate_config(value)["corpus"]["exclude_policy"], EXCLUDE_POLICY_FAQ)
        value = self.graph_config(); value["corpus"]["exclude_policy"] = "faq_by_content_v1"
        with self.assertRaisesRegex(ValidationError, "exclude-policy"): validate_config(value)
        value = self.graph_config(); value["corpus"]["exclude"] = ["*faq*"]
        with self.assertRaisesRegex(ValidationError, "corpus-fields"): validate_config(value)
        # a request of any direct-lane campaign (E-EXP1, E-EXP1b, E-EXP1o, E-EXP3, E-EXP4 ...) must declare it
        for experiment_id in ("E-EXP1-argo-historical-v1", "E-EXP1o-argo-nofaq-v1", "E-EXP3-argo-historical-v1", "E-EXP4-argo-historical-v1"):
            value = self.graph_config(); value["experiment_id"] = experiment_id
            with self.assertRaisesRegex(ValidationError, "exclude-policy-required", msg=experiment_id): validate_config(value)
            value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_FAQ
            validate_config(value)
        # a request outside the direct lane may still omit it (its corpus then carries every page)
        validate_config(self.graph_config())

    def test_s047_fx1_prepare_skips_faq_pages_and_the_graph_has_no_edge_to_them(self):
        """FX-1: the three FAQ pages are neither imported nor graph nodes; the receipt fields name them."""
        self.faq_fixture()
        value = self.graph_config(); value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_FAQ
        value["corpus"]["import_policy"] = IMPORT_POLICY_LOWER
        destination = self.root / "provider-input-faq"
        launch = prepare_provider_input(value, self.input, self.repo, destination)
        imported = sorted(path.name for path in (destination / "corpus").iterdir())
        self.assertEqual(imported, ["guide__bbbdfa65.md", "guide_hooks__b1ec6f5b.md", "guide_waves__3440d413.md"])
        excluded = {imported_document_name(path, IMPORT_POLICY_LOWER)[1] for path in ("faq.md", "guide/FAQ.md", "notes/faqs-old.md")}
        self.assertEqual(launch["corpus_exclude_policy"], EXCLUDE_POLICY_FAQ)
        self.assertEqual(launch["excluded_document_count"], 3)
        self.assertEqual(launch["excluded_document_ids"], sorted(excluded))
        edges = (destination / "graph_edges.csv").read_text(encoding="utf-8").splitlines()[1:]
        nodes = {node for edge in edges for node in edge.split(",")}
        self.assertFalse(nodes & excluded, edges)
        self.assertIn("guide__bbbdfa65,guide_waves__3440d413", edges)
        documents = json.loads((destination.parent / "documents.json").read_bytes())
        self.assertEqual({row["doc_id"] for row in documents if row.get("excluded")}, excluded)
        self.assertEqual({row["doc_id"] for row in documents if not row.get("excluded")}, set(nodes) | {"guide_hooks__b1ec6f5b"} - excluded)
        # the same fixture without the policy: every page imported, the FAQ pages are nodes
        plain = prepare_provider_input(self.graph_config(), self.input, self.repo, self.root / "provider-input-plain")
        self.assertEqual(plain["excluded_document_count"], 0)
        self.assertIsNone(plain["corpus_exclude_policy"])
        self.assertEqual(len(list((self.root / "provider-input-plain" / "corpus").iterdir())), 6)
        plain_edges = (self.root / "provider-input-plain" / "graph_edges.csv").read_text(encoding="utf-8").splitlines()[1:]
        self.assertTrue({node for edge in plain_edges for node in edge.split(",")} & excluded)
        # and an E-EXP1 request without the policy is refused before anything is written
        refused = self.graph_config(); refused["experiment_id"] = "E-EXP1-argo-historical-v1"
        with self.assertRaisesRegex(ValidationError, "exclude-policy-required"):
            prepare_provider_input(refused, self.input, self.repo, self.root / "refused")
        self.assertFalse((self.root / "refused").exists())

    def test_s023_cikm_pattern_v2_reads_the_declared_patterns_and_the_underscore_rule(self):
        """S023: v2 excludes the request's FAQ patterns (path or file name), ``faq`` in any part, and ``_`` directories."""
        patterns = ["*faq*", "*frequently*"]
        for path in ("faq.md", "guide/FAQ.md", "x/myfaqs/y.md", "troubleshoot/frequently-asked-questions.md",
                     "solutions/kspm-Frequently-Asked-Questions.md", "manage/_snippets/agentless.md", "_snippets/x/y.md"):
            self.assertTrue(excluded_by_policy(path, EXCLUDE_POLICY_CIKM_V2, patterns), path)
        for path in ("guide.md", "guide/waves.md", "_sources/kept.md", "under_score/kept.md", "a/b_c/kept.md"):
            self.assertFalse(excluded_by_policy(path, EXCLUDE_POLICY_CIKM_V2, patterns), path)
        # v2 with the Argo pattern list alone is v1 plus the underscore rule
        self.assertFalse(excluded_by_policy("troubleshoot/frequently-asked-questions.md", EXCLUDE_POLICY_CIKM_V2, ["*faq*"]))
        self.assertFalse(excluded_by_policy("manage/_snippets/agentless.md", EXCLUDE_POLICY_FAQ))
        # the pattern list is required under v2 and refused under v1
        with self.assertRaisesRegex(ValidationError, "faq-patterns"): excluded_by_policy("faq.md", EXCLUDE_POLICY_CIKM_V2)
        with self.assertRaisesRegex(ValidationError, "faq-patterns"): excluded_by_policy("faq.md", EXCLUDE_POLICY_CIKM_V2, [])
        with self.assertRaisesRegex(ValidationError, "faq-patterns"): excluded_by_policy("faq.md", EXCLUDE_POLICY_FAQ, ["*faq*"])
        value = self.graph_config(); value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_CIKM_V2
        with self.assertRaisesRegex(ValidationError, "faq-patterns"): validate_config(value)
        value["corpus"]["faq_patterns"] = patterns
        self.assertEqual(validate_config(value)["corpus"]["faq_patterns"], patterns)
        value["corpus"]["faq_patterns"] = ["*faq*", "*faq*"]
        with self.assertRaisesRegex(ValidationError, "faq-patterns"): validate_config(value)
        value = self.graph_config(); value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_FAQ; value["corpus"]["faq_patterns"] = patterns
        with self.assertRaisesRegex(ValidationError, "faq-patterns"): validate_config(value)

    def test_s023_v2_prepare_skips_pattern_pages_and_snippet_directories_and_the_receipt_names_the_patterns(self):
        self.faq_fixture()
        docs = self.repo / "docs"
        (docs / "guide" / "_snippets").mkdir()
        (docs / "guide" / "_snippets" / "fragment.md").write_text("# Fragment" + chr(10) + "Sync waves." + chr(10), encoding="utf-8")
        (docs / "frequently-asked.md").write_text("# Frequently asked" + chr(10) + "See [waves](guide/waves.md)." + chr(10), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "v2 fixture"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True).strip()
        self.tree = subprocess.check_output(["git", "-C", str(self.repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
        value = self.graph_config(); value["corpus"]["import_policy"] = IMPORT_POLICY_LOWER
        value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_CIKM_V2; value["corpus"]["faq_patterns"] = ["*faq*", "*frequently*"]
        destination = self.root / "provider-input-v2"
        launch = prepare_provider_input(value, self.input, self.repo, destination)
        imported = sorted(path.name for path in (destination / "corpus").iterdir())
        self.assertEqual(imported, ["guide__bbbdfa65.md", "guide_hooks__b1ec6f5b.md", "guide_waves__3440d413.md"])
        excluded = {imported_document_name(path, IMPORT_POLICY_LOWER)[1]
                    for path in ("faq.md", "guide/FAQ.md", "notes/faqs-old.md", "guide/_snippets/fragment.md", "frequently-asked.md")}
        self.assertEqual(launch["corpus_exclude_policy"], EXCLUDE_POLICY_CIKM_V2)
        self.assertEqual(launch["corpus_faq_patterns"], ["*faq*", "*frequently*"])
        self.assertEqual(launch["excluded_document_count"], 5)
        self.assertEqual(launch["excluded_document_ids"], sorted(excluded))
        edges = (destination / "graph_edges.csv").read_text(encoding="utf-8").splitlines()[1:]
        self.assertFalse({node for edge in edges for node in edge.split(",")} & excluded, edges)
        # under v1 on the same tree the two new pages are imported (the pattern and the directory are not FAQ under v1)
        v1 = self.graph_config(); v1["corpus"]["import_policy"] = IMPORT_POLICY_LOWER; v1["corpus"]["exclude_policy"] = EXCLUDE_POLICY_FAQ
        plain = prepare_provider_input(v1, self.input, self.repo, self.root / "provider-input-v1")
        self.assertEqual(plain["excluded_document_count"], 3)
        self.assertIsNone(plain["corpus_faq_patterns"])
        self.assertEqual(len(list((self.root / "provider-input-v1" / "corpus").iterdir())), 5)

    def test_s047_excluded_page_is_never_an_admitted_context_item(self):
        """An excluded page's id is in the inventory (excluded: true) but no provider row may cite it."""
        self.faq_fixture()
        value = self.graph_config(); value["corpus"]["exclude_policy"] = EXCLUDE_POLICY_FAQ
        destination = self.root / "provider-input-faq"
        prepare_provider_input(value, self.input, self.repo, destination)
        faq_id = imported_document_name("faq.md")[1]
        context = [{"doc_id": faq_id, "text": "FAQ How do sync waves work? The gold answer.", "score": 2.0,
                    "source": "graph_ranked_expand", "rank": 1, "selection_role": "seed"}]
        output = self.graph_output(context)
        with self.assertRaisesRegex(ValidationError, "provider-item"):
            project_ranked_predictions(value, output, self.input, destination.parent / "documents.json", destination)

    def graph_output(self, context, metadata_overrides=None):
        output = self.root / "provider-output"; output.mkdir()
        metadata = {"name": "rag_graph_ranked_expand", "deterministic": True, "seed_ranker": "bm25", "neighbor_ranker": "bm25",
                    "graph_expansion_hops": 1, "top_k": 3, "seed_top_k": 2, "neighbor_budget": 1,
                    "max_context_chars_per_doc": 1500, "answer_policy": "best_passage_v1", **(metadata_overrides or {})}
        (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        row = {"question_id": "q1", "question": "sync waves", "generated_answer": "Sync waves order resources.",
               "retrieved_context": context,
               "answer_provenance": [{"doc_id": "guide__bbbdfa65", "section_heading": "Guide", "passage_index": 0, "char_start": 8, "char_end": 35}]}
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        return output

    def test_graph_projection_keeps_row_shape_and_fails_closed_on_order_and_adjacency(self):
        import shutil
        config = self.graph_config()
        provider_input = self.root / "provider-input"
        prepare_provider_input(config, self.input, self.repo, provider_input)
        documents = self.root / "documents.json"
        documents.write_text(json.dumps([
            {"doc_id": "guide__bbbdfa65", "imported_path": "guide__bbbdfa65.md", "expected_text": "Sync waves order resources."},
            {"doc_id": "guide_waves__3440d413", "imported_path": "guide_waves__3440d413.md", "expected_text": "W"},
            {"doc_id": "guide_hooks__b1ec6f5b", "imported_path": "guide_hooks__b1ec6f5b.md", "expected_text": "H"}]), encoding="utf-8")

        def item(doc_id, text, score, role, rank):
            return {"doc_id": doc_id, "text": text, "score": score, "source": "graph_ranked_expand", "selection_role": role, "rank": rank}
        good = [item("guide__bbbdfa65", "Sync waves order resources.", 2.0, "seed", 1), item("guide_waves__3440d413", "W", 1.0, "seed", 2),
                item("guide_hooks__b1ec6f5b", "H", 0.5, "ranked_neighbor", 3)]
        output = self.graph_output(good)
        ranked, answers = project_ranked_predictions(config, output, self.input, documents, provider_input)
        self.assertEqual(set(ranked[0]), {"schema_version", "query_id", "context_item_budget_k", "max_rank", "retrieval_exhausted", "items", "duplicate_collapse_count", "duplicate_collapse_sha256"})
        self.assertEqual([i["support_target_id"] for i in ranked[0]["items"]], ["guide__bbbdfa65", "guide_waves__3440d413", "guide_hooks__b1ec6f5b"])
        self.assertEqual(set(ranked[0]["items"][0]), {"support_target_id", "rank", "score", "source_rank", "source_item_id", "source_provenance_sha256"})
        self.assertEqual(answers[0]["answer_policy"], "best_passage_v1"); self.assertIn("answer_provenance", answers[0])
        shutil.rmtree(output)
        def reranked(*items):
            return [{**item, "rank": rank} for rank, item in enumerate(items, 1)]
        bad = {
            "provider-order": [reranked(good[1], good[0], good[2]), reranked(good[0], good[2], good[1]),
                               [item("guide__bbbdfa65", "Sync waves order resources.", 2.0, "seed", 1), item("guide_waves__3440d413", "W", 1.0, "seed", 2), item("guide_hooks__b1ec6f5b", "H", 0.5, "seed", 3)]],
            "provider-neighbor": [[good[0], item("guide_hooks__b1ec6f5b", "H", 0.5, "ranked_neighbor", 2)]],
            "provider-item-fields": [[{k: v for k, v in good[0].items() if k != "selection_role"}]],
            "provider-item": [[{**good[0], "source": "lexical_topk"}]],
        }
        for code, contexts in bad.items():
            for context in contexts:
                output = self.graph_output(context)
                with self.assertRaisesRegex(ValidationError, code, msg=json.dumps(context)):
                    project_ranked_predictions(config, output, self.input, documents, provider_input)
                shutil.rmtree(output)
        output = self.graph_output(good, {"neighbor_budget": 2})
        with self.assertRaisesRegex(ValidationError, "provider-metadata"):
            project_ranked_predictions(config, output, self.input, documents, provider_input)
        shutil.rmtree(output)
        output = self.graph_output(good); (output / "metadata.json").write_text(json.dumps({"name": "rag_graph_ranked_expand", "deterministic": True, "ranker": "bm25", "top_k": 3, "max_context_chars_per_doc": 1500, "answer_policy": "best_passage_v1"}), encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "provider-metadata-fields"):
            project_ranked_predictions(config, output, self.input, documents, provider_input)


NAVIGATOR_IMAGE = "retrieval-arena/rag-llm-guided-search@sha256:" + "c" * 64
MODEL_POLICY = {"backend": "ollama", "model": "qwen3:14b", "digest": "sha256:" + "d" * 64, "temperature": 0.2, "top_p": 0.9,
                "seed": 391960843, "context_window_tokens": 8192, "max_output_tokens": 1024, "timeout_seconds": 300,
                "max_attempts": 1, "endpoint": "http://host.docker.internal:11434", "thinking": False}


class NavigatorProviderTests(GraphProviderTests):
    """S026: the bounded navigator; host model alias network, agent answers, published traces."""

    def navigator_config(self, **overrides):
        value = self.config()
        value["provider"] = {"name": "rag_llm_guided_search", "image": NAVIGATOR_IMAGE, "platform": "linux/amd64",
                             "network": "host_model_alias", "top_k": 3, "max_context_chars_per_doc": 1500,
                             "answer_policy": "agent_answer_v1", "model_policy": dict(MODEL_POLICY),
                             "page_visit_budget": 8, "model_call_budget": 12, "page_char_limit": 12000, **overrides}
        value["ranked"]["context_item_budget_k"] = value["provider"]["top_k"]
        return value

    def test_navigator_declared_with_model_policy_and_network_exception(self):
        config = validate_config(self.navigator_config())
        self.assertEqual(config["provider"]["network"], "host_model_alias")
        cases = {
            "provider-policy": [{"network": "none"}, {"answer_policy": "best_passage_v1"}, {"image": GRAPH_IMAGE}],
            "provider-model-policy": [{"page_visit_budget": 0}, {"model_call_budget": True},
                                      {"model_policy": {**MODEL_POLICY, "max_attempts": 2}},
                                      {"model_policy": {**MODEL_POLICY, "endpoint": "http://127.0.0.1:11434"}},
                                      {"model_policy": {**MODEL_POLICY, "digest": "d" * 64}},
                                      {"model_policy": {**MODEL_POLICY, "backend": "openai"}},
                                      {"model_policy": {k: v for k, v in MODEL_POLICY.items() if k != "thinking"}}],
        }
        for code, overrides_list in cases.items():
            for overrides in overrides_list:
                with self.assertRaisesRegex(ValidationError, code, msg=repr(overrides)):
                    validate_config(self.navigator_config(**overrides))
        # Every retriever keeps network none; the alias is the navigator's alone.
        with self.assertRaisesRegex(ValidationError, "provider-policy"):
            validate_config(self.graph_config(network="host_model_alias"))
        value = self.navigator_config(); del value["provider"]["page_char_limit"]
        with self.assertRaisesRegex(ValidationError, "provider-fields"): validate_config(value)

    def test_navigator_prepare_carries_graph_and_launch_network(self):
        destination = self.root / "provider-input"
        launch = prepare_provider_input(self.navigator_config(), self.input, self.repo, destination)
        self.assertEqual((launch["provider_name"], launch["network"]), ("rag_llm_guided_search", "host_model_alias"))
        self.assertIsNotNone(launch["graph_edges_sha256"])
        provider_config = json.loads((destination / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(provider_config["model_policy"], MODEL_POLICY)
        self.assertEqual((provider_config["page_visit_budget"], provider_config["model_call_budget"], provider_config["page_char_limit"]), (8, 12, 12000))
        flat = prepare_provider_input(self.config(), self.input, self.repo, self.root / "flat-input")
        self.assertEqual(flat["network"], "none")

    def navigator_output(self, cited, *, text="Waves order resources.", trace_overrides=None, terminal="answer", metadata_overrides=None):
        output = self.root / "provider-output"; output.mkdir()
        metadata = {"name": "rag_llm_guided_search", "deterministic": False, "top_k": 3, "max_context_chars_per_doc": 1500,
                    "answer_policy": "agent_answer_v1", "page_visit_budget": 8, "model_call_budget": 12, "page_char_limit": 12000,
                    "model_policy": MODEL_POLICY, "trace_schema_version": "e007b.action_trace.v1",
                    "provider_policy": "bounded_provider_action_controller_v1", "prompt_version": "e007c_navigator_prompt_v1",
                    "start_document": "index.md", **(metadata_overrides or {})}
        (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        expected = {"guide__bbbdfa65": "Sync waves order resources.", "guide_waves__3440d413": "W", "guide_hooks__b1ec6f5b": "H"}
        context = [{"doc_id": d, "text": expected[d], "score": float(len(cited[:3]) - i), "source": "llm_guided_navigation", "rank": i + 1}
                   for i, d in enumerate(cited[:3])]
        row = {"question_id": "q1", "question": "sync waves", "generated_answer": text, "retrieved_context": context,
               "answer_policy": "agent_answer_v1", "cited_pages": cited}
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        (output / "navigation.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        traces = output / "traces"; traces.mkdir()
        header = {"record": "header", "question_id": "q1", "schema_version": "e007b.action_trace.v1",
                  "compatible_action_schema_version": "e007a.action_trace.v1", "policy": "bounded_provider_action_controller_v1",
                  "controller": {}, "start_doc_id": "guide__bbbdfa65"}
        visits = ["guide__bbbdfa65", "guide_waves__3440d413", "guide_hooks__b1ec6f5b"]
        actions = [{"record": "action", "step": 1, "action": "open", "budget_remaining": 11, "details": {"from_doc_id": visits[0], "doc_id": visits[1], "dead_end": False}},
                   {"record": "action", "step": 2, "action": "open", "budget_remaining": 10, "details": {"from_doc_id": visits[1], "doc_id": visits[2], "dead_end": False}}]
        if terminal == "answer":
            actions.append({"record": "action", "step": 3, "action": "answer", "budget_remaining": 9, "details": {"cited_pages": cited, "text": text}})
        actions.append({"record": "action", "step": len(actions) + 1, "action": "stop", "budget_remaining": 9, "details": {}})
        last = {"record": "terminal", "terminal": {"reason": terminal}, "visits": visits, "opens": 2, "model_calls": 3, "dead_ends": 0,
                "answer": {"cited_pages": cited, "text": text}, "final_context_doc_ids": cited[:3], "provider_calls": [],
                "paper_facing_evidence": False, **(trace_overrides or {})}
        (traces / "q1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [header, *actions, last]), encoding="utf-8")
        return output

    def test_navigator_projection_publishes_cited_pages_and_validates_traces(self):
        import shutil
        config = self.navigator_config()
        provider_input = self.root / "provider-input"
        prepare_provider_input(config, self.input, self.repo, provider_input)
        documents = self.root / "documents.json"
        documents.write_text(json.dumps([
            {"doc_id": "guide__bbbdfa65", "imported_path": "guide__bbbdfa65.md", "expected_text": "Sync waves order resources."},
            {"doc_id": "guide_waves__3440d413", "imported_path": "guide_waves__3440d413.md", "expected_text": "W"},
            {"doc_id": "guide_hooks__b1ec6f5b", "imported_path": "guide_hooks__b1ec6f5b.md", "expected_text": "H"}]), encoding="utf-8")
        output = self.navigator_output(["guide_hooks__b1ec6f5b", "guide_waves__3440d413"])
        ranked, answers = project_ranked_predictions(config, output, self.input, documents, provider_input)
        self.assertEqual([i["support_target_id"] for i in ranked[0]["items"]], ["guide_hooks__b1ec6f5b", "guide_waves__3440d413"])
        self.assertEqual(ranked[0]["retrieval_exhausted"], True)
        self.assertEqual(answers[0], {"query_id": "q1", "generated_answer": "Waves order resources.", "answer_policy": "agent_answer_v1",
                                      "cited_pages": ["guide_hooks__b1ec6f5b", "guide_waves__3440d413"]})
        shutil.rmtree(output)
        # An empty answer (budget exhausted) projects an empty context and an empty answer.
        output = self.navigator_output([], text="", terminal="budget_exhausted")
        ranked, answers = project_ranked_predictions(config, output, self.input, documents, provider_input)
        self.assertEqual((ranked[0]["items"], answers[0]["generated_answer"]), ([], ""))
        shutil.rmtree(output)
        bad = {
            "provider-navigation-row": [dict(cited=["guide_hooks__b1ec6f5b"], trace_overrides={"answer": {"cited_pages": [], "text": "Waves order resources."}}),
                                        dict(cited=["guide_hooks__b1ec6f5b"], trace_overrides={"final_context_doc_ids": []})],
            "provider-item": [dict(cited=["guide_hooks__b1ec6f5b", "guide_hooks__b1ec6f5b"])],
            "provider-trace-budget": [dict(cited=["guide_hooks__b1ec6f5b"], trace_overrides={"opens": 3}),
                                      dict(cited=["guide_hooks__b1ec6f5b"], trace_overrides={"model_calls": 13}),
                                      dict(cited=["guide_hooks__b1ec6f5b"], trace_overrides={"visits": ["guide__bbbdfa65", "guide_waves__3440d413", "nope__00000000"]}),
                                      dict(cited=["guide_hooks__b1ec6f5b"], trace_overrides={"terminal": {"reason": "answer"}}, terminal="invalid_action")],
            "provider-output-shape": [dict(cited=[], text="", terminal="budget_exhausted", remove="navigation.json")],
            "provider-traces": [dict(cited=[], text="", terminal="budget_exhausted", remove="traces/q1.jsonl")],
        }
        for code, cases in bad.items():
            for case in cases:
                remove = case.pop("remove", None)
                output = self.navigator_output(**case)
                if remove:
                    (output / remove).unlink()
                with self.assertRaisesRegex(ValidationError, code, msg=json.dumps(case)):
                    project_ranked_predictions(config, output, self.input, documents, provider_input)
                shutil.rmtree(output)
        output = self.navigator_output([], text="", terminal="budget_exhausted", metadata_overrides={"page_visit_budget": 9})
        with self.assertRaisesRegex(ValidationError, "provider-metadata"):
            project_ranked_predictions(config, output, self.input, documents, provider_input)

    # --- S046: the repo-search agent reader ---------------------------------------

    SEARCH_IMAGE = "retrieval-arena/rag-repo-search-agent@sha256:" + "c" * 64

    def search_config(self, name="rag_repo_search_agent", **overrides):
        value = self.config()
        value["provider"] = {"name": name, "image": self.SEARCH_IMAGE, "platform": "linux/amd64",
                             "network": "host_model_alias", "top_k": 3, "max_context_chars_per_doc": 1500,
                             "answer_policy": "agent_answer_v1", "model_policy": dict(MODEL_POLICY),
                             "search_budget": 3, "search_hit_files": 10, "search_hit_lines_per_file": 3,
                             "page_visit_budget": 8, "model_call_budget": 12, "page_char_limit": 12000,
                             "follow_links": name == "rag_repo_search_agent", "landing_policy": "search_question_text", **overrides}
        value["ranked"]["context_item_budget_k"] = value["provider"]["top_k"]
        return value

    def test_repo_search_agent_declared_in_two_variants_fixed_by_name(self):
        for name, follow in (("rag_repo_search_agent", True), ("rag_repo_search_agent_nolinks", False)):
            config = validate_config(self.search_config(name))
            self.assertEqual((config["provider"]["network"], config["provider"]["follow_links"]), ("host_model_alias", follow))
            metadata = expected_provider_metadata(config["provider"])
            self.assertEqual((metadata["name"], metadata["follow_links"], metadata["trace_schema_version"], metadata["provider_policy"]),
                             (name, follow, "e007c.action_trace.v1", "bounded_repo_search_controller_v1"))
        cases = {
            "provider-policy": [{"network": "none"}, {"answer_policy": "best_passage_v1"}, {"image": NAVIGATOR_IMAGE}],
            "provider-model-policy": [{"page_visit_budget": 0}, {"model_policy": {**MODEL_POLICY, "endpoint": "http://127.0.0.1:11434"}}],
            # RSA-2: the variant is the name; a config that contradicts it is refused.
            "provider-search-policy": [{"follow_links": False}, {"follow_links": "yes"}, {"search_budget": 0},
                                       {"search_hit_files": True}, {"search_hit_lines_per_file": 0}],
        }
        for code, overrides_list in cases.items():
            for overrides in overrides_list:
                with self.assertRaisesRegex(ValidationError, code, msg=repr(overrides)):
                    validate_config(self.search_config(**overrides))
        with self.assertRaisesRegex(ValidationError, "provider-search-policy"):
            validate_config(self.search_config("rag_repo_search_agent_nolinks", follow_links=True))
        value = self.search_config(); del value["provider"]["follow_links"]
        with self.assertRaisesRegex(ValidationError, "provider-fields"): validate_config(value)
        # S046b (RL-4): the landing policy is required and closed; the tokenised policy's metadata names the
        # v2 prompt and echoes the stop-word list, the literal policy's metadata is the S046 one.
        value = self.search_config(); del value["provider"]["landing_policy"]
        with self.assertRaisesRegex(ValidationError, "provider-landing-policy"): validate_config(value)
        with self.assertRaisesRegex(ValidationError, "provider-landing-policy"):
            validate_config(self.search_config(landing_policy="bm25_v1"))
        tokenised = validate_config(self.search_config(landing_policy="tokenised_content_words_v1"))
        metadata = expected_provider_metadata(tokenised["provider"])
        self.assertEqual((metadata["landing"], metadata["prompt_version"], metadata["stop_word_list"], len(metadata["stop_words"])),
                         ("tokenised_content_words_v1", "e007d_repo_search_prompt_v2", "STOP_WORDS_EN_V1", 110))
        literal = expected_provider_metadata(validate_config(self.search_config())["provider"])
        self.assertEqual((literal["landing"], literal["prompt_version"]), ("search_question_text", "e007d_repo_search_prompt_v1"))
        self.assertNotIn("stop_words", literal)
        launch_t = prepare_provider_input(self.search_config(landing_policy="tokenised_content_words_v1"), self.input, self.repo, self.root / "search-input-t")
        self.assertEqual(launch_t["landing_policy"], "tokenised_content_words_v1")
        self.assertEqual(json.loads((self.root / "search-input-t" / "config.yaml").read_text(encoding="utf-8"))["landing_policy"], "tokenised_content_words_v1")
        launch = prepare_provider_input(self.search_config("rag_repo_search_agent_nolinks"), self.input, self.repo, self.root / "search-input")
        self.assertEqual((launch["provider_name"], launch["network"], launch["landing_policy"]),
                         ("rag_repo_search_agent_nolinks", "host_model_alias", "search_question_text"))
        self.assertIsNotNone(launch["graph_edges_sha256"])
        provider_config = json.loads((self.root / "search-input" / "config.yaml").read_text(encoding="utf-8"))
        self.assertEqual((provider_config["follow_links"], provider_config["search_budget"], provider_config["search_hit_files"]), (False, 3, 10))
        # The provider input carries the corpus, the questions, the graph and the config only (V2).
        self.assertEqual(sorted(p.name for p in (self.root / "search-input").iterdir()), ["config.yaml", "corpus", "graph_edges.csv", "questions.jsonl"])

    def search_output(self, cited, *, text="Waves order resources.", trace_overrides=None, terminal="answer",
                      metadata_overrides=None, follow_links=True, actions=None, header_overrides=None):
        output = self.root / "provider-output"; output.mkdir()
        name = "rag_repo_search_agent" if follow_links else "rag_repo_search_agent_nolinks"
        metadata = {"name": name, "deterministic": False, "top_k": 3, "max_context_chars_per_doc": 1500,
                    "answer_policy": "agent_answer_v1", "search_budget": 3, "search_hit_files": 10, "search_hit_lines_per_file": 3,
                    "page_visit_budget": 8, "model_call_budget": 12, "page_char_limit": 12000, "follow_links": follow_links,
                    "model_policy": MODEL_POLICY, "trace_schema_version": "e007c.action_trace.v1",
                    "provider_policy": "bounded_repo_search_controller_v1", "prompt_version": "e007d_repo_search_prompt_v1",
                    "landing": "search_question_text", **(metadata_overrides or {})}
        (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        expected = {"guide__bbbdfa65": "Sync waves order resources.", "guide_waves__3440d413": "W", "guide_hooks__b1ec6f5b": "H"}
        context = [{"doc_id": d, "text": expected[d], "score": float(len(cited[:3]) - i), "source": "repo_search_navigation", "rank": i + 1}
                   for i, d in enumerate(cited[:3])]
        row = {"question_id": "q1", "question": "sync waves", "generated_answer": text, "retrieved_context": context,
               "answer_policy": "agent_answer_v1", "cited_pages": cited}
        (output / "predictions.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        (output / "navigation.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        traces = output / "traces"; traces.mkdir()
        header = {"record": "header", "question_id": "q1", "schema_version": "e007c.action_trace.v1",
                  "compatible_action_schema_version": "e007b.action_trace.v1", "policy": "bounded_repo_search_controller_v1",
                  "controller": {}, "follow_links": follow_links, "landing_query": "sync waves", "start_doc_id": None,
                  **(header_overrides or {})}
        visits = ["guide_waves__3440d413", "guide_hooks__b1ec6f5b"]
        if actions is None:
            actions = [{"action": "search", "details": {"search_index": 1, "query": "sync waves", "landing": True, "doc_ids": ["guide_waves__3440d413"], "hit_count": 1}},
                       {"action": "open", "details": {"from_doc_id": None, "doc_id": visits[0], "route": "landing", "search_index": 1, "dead_end": False, "searches_before": 1, "opens_before": 0}},
                       {"action": "open", "details": {"from_doc_id": visits[0], "doc_id": visits[1], "route": "link", "search_index": None, "dead_end": True, "searches_before": 1, "opens_before": 1}}]
        actions = [dict(a) for a in actions]
        if terminal == "answer":
            actions.append({"action": "answer", "details": {"cited_pages": cited, "text": text}})
        actions.append({"action": "stop", "details": {}})
        rows = [{"record": "action", "step": i + 1, "budget_remaining": 9, **a} for i, a in enumerate(actions)]
        opens = [a["details"]["doc_id"] for a in actions if a["action"] == "open"]
        last = {"record": "terminal", "terminal": {"reason": terminal}, "visits": opens, "opens": len(opens),
                "searches": sum(1 for a in actions if a["action"] == "search"), "zero_hit_searches": 0, "model_calls": 3, "dead_ends": 1,
                "answer": {"cited_pages": cited, "text": text}, "final_context_doc_ids": cited[:3], "provider_calls": [],
                "paper_facing_evidence": False, **(trace_overrides or {})}
        (traces / "q1.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [header, *rows, last]), encoding="utf-8")
        return output

    def test_repo_search_projection_validates_e007c_traces_at_finalize(self):
        import shutil
        config = self.search_config()
        provider_input = self.root / "provider-input"
        prepare_provider_input(config, self.input, self.repo, provider_input)
        documents = self.root / "documents.json"
        documents.write_text(json.dumps([
            {"doc_id": "guide__bbbdfa65", "imported_path": "guide__bbbdfa65.md", "expected_text": "Sync waves order resources."},
            {"doc_id": "guide_waves__3440d413", "imported_path": "guide_waves__3440d413.md", "expected_text": "W"},
            {"doc_id": "guide_hooks__b1ec6f5b", "imported_path": "guide_hooks__b1ec6f5b.md", "expected_text": "H"}]), encoding="utf-8")
        output = self.search_output(["guide_hooks__b1ec6f5b", "guide_waves__3440d413"])
        ranked, answers = project_ranked_predictions(config, output, self.input, documents, provider_input)
        self.assertEqual([i["support_target_id"] for i in ranked[0]["items"]], ["guide_hooks__b1ec6f5b", "guide_waves__3440d413"])
        self.assertEqual(answers[0]["cited_pages"], ["guide_hooks__b1ec6f5b", "guide_waves__3440d413"])
        shutil.rmtree(output)
        output = self.search_output([], text="", terminal="budget_exhausted")
        ranked, answers = project_ranked_predictions(config, output, self.input, documents, provider_input)
        self.assertEqual((ranked[0]["items"], answers[0]["generated_answer"]), ([], ""))
        shutil.rmtree(output)
        landing = {"action": "search", "details": {"search_index": 1, "query": "sync waves", "landing": True, "doc_ids": ["guide_waves__3440d413"], "hit_count": 1}}
        open1 = {"action": "open", "details": {"from_doc_id": None, "doc_id": "guide_waves__3440d413", "route": "landing", "search_index": 1, "dead_end": False, "searches_before": 1, "opens_before": 0}}
        later = lambda i, ids: {"action": "search", "details": {"search_index": i, "query": "q", "landing": False, "doc_ids": ids, "hit_count": len(ids)}}
        bad = {
            # RSA-4: a fourth search, a ninth open, a thirteenth call, a citation of an unvisited page.
            "provider-trace-budget": [
                dict(cited=[], text="", terminal="budget_exhausted", actions=[landing, later(2, []), later(3, []), later(4, [])]),
                dict(cited=[], text="", terminal="budget_exhausted", actions=[landing] + [open1] * 9),
                dict(cited=[], text="", terminal="budget_exhausted", trace_overrides={"model_calls": 13}),
                dict(cited=["guide__bbbdfa65"]),
                dict(cited=[], text="", terminal="budget_exhausted", trace_overrides={"visits": ["guide_waves__3440d413"]}),
                dict(cited=[], text="", terminal="budget_exhausted", trace_overrides={"searches": 2}),
                # the landing must be the first action and carry the header's query; a later search is not a landing
                dict(cited=[], text="", terminal="budget_exhausted", actions=[open1, landing]),
                dict(cited=[], text="", terminal="budget_exhausted", actions=[landing, later(2, ["nope__00000000"])]),
                dict(cited=[], text="", terminal="budget_exhausted", actions=[{**landing, "details": {**landing["details"], "query": "other"}}]),
                # RSA-2: a link route in the nolinks variant
                dict(cited=[], text="", terminal="budget_exhausted", follow_links=False,
                     actions=[landing, open1, {"action": "open", "details": {"from_doc_id": "guide_waves__3440d413", "doc_id": "guide_hooks__b1ec6f5b", "route": "link", "search_index": None, "dead_end": True, "searches_before": 1, "opens_before": 1}}]),
                dict(cited=[], text="", terminal="budget_exhausted", actions=[landing, {**open1, "details": {**open1["details"], "route": "teleport"}}]),
            ],
            "provider-trace-shape": [dict(cited=[], text="", terminal="budget_exhausted", header_overrides={"follow_links": False}),
                                     dict(cited=[], text="", terminal="budget_exhausted", header_overrides={"schema_version": "e007b.action_trace.v1"})],
        }
        for code, cases in bad.items():
            for case in cases:
                follow = case.get("follow_links", True)
                case_config = config if follow else self.search_config("rag_repo_search_agent_nolinks")
                if not follow:
                    case["metadata_overrides"] = {"name": "rag_repo_search_agent_nolinks", "follow_links": False}
                output = self.search_output(**case)
                with self.assertRaisesRegex(ValidationError, code, msg=json.dumps(case, default=str)):
                    project_ranked_predictions(case_config, output, self.input, documents, provider_input)
                shutil.rmtree(output)
        output = self.search_output([], text="", terminal="budget_exhausted", metadata_overrides={"search_budget": 4})
        with self.assertRaisesRegex(ValidationError, "provider-metadata"):
            project_ranked_predictions(config, output, self.input, documents, provider_input)
        shutil.rmtree(output)
        # S046b (RL-4): under the literal policy a v1-shaped trace validates (above) and a header naming the
        # tokenised policy is refused; under the tokenised policy the header must name it and carry the
        # terms the rule yields for landing_query ("sync waves" -> ["sync", "waves"]), the landing line's
        # terms (when present) likewise, and the metadata names prompt v2 and the stop-word list.
        for header in ({"landing": "tokenised_content_words_v1"}, {"landing_terms": ["sync", "waves"]}):
            output = self.search_output([], text="", terminal="budget_exhausted", header_overrides=header)
            with self.assertRaisesRegex(ValidationError, "provider-landing-policy", msg=repr(header)):
                project_ranked_predictions(config, output, self.input, documents, provider_input)
            shutil.rmtree(output)
        tokenised = validate_config(self.search_config(landing_policy="tokenised_content_words_v1"))
        t_input = self.root / "provider-input-t"
        prepare_provider_input(tokenised, self.input, self.repo, t_input)
        stop_words = list(candidate_retrieval.STOP_WORDS_EN_V1)
        t_metadata = {"landing": "tokenised_content_words_v1", "prompt_version": "e007d_repo_search_prompt_v2",
                      "stop_words": stop_words, "stop_word_list": "STOP_WORDS_EN_V1"}
        t_header = {"landing": "tokenised_content_words_v1", "landing_terms": ["sync", "waves"]}
        t_landing = {**landing, "details": {**landing["details"], "landing_terms": ["sync", "waves"]}}
        output = self.search_output([], text="", terminal="budget_exhausted", metadata_overrides=t_metadata,
                                    header_overrides=t_header, actions=[t_landing, open1])
        ranked, answers = project_ranked_predictions(tokenised, output, self.input, documents, t_input)
        self.assertEqual(ranked[0]["items"], [])
        shutil.rmtree(output)
        for header, action in (({}, t_landing), ({**t_header, "landing_terms": ["sync"]}, t_landing),
                               ({**t_header, "landing": "search_question_text"}, t_landing),
                               (t_header, {**landing, "details": {**landing["details"], "landing_terms": ["waves"]}})):
            output = self.search_output([], text="", terminal="budget_exhausted", metadata_overrides=t_metadata,
                                        header_overrides=header, actions=[action, open1])
            with self.assertRaisesRegex(ValidationError, "provider-landing-policy", msg=repr((header, action))):
                project_ranked_predictions(tokenised, output, self.input, documents, t_input)
            shutil.rmtree(output)
        output = self.search_output([], text="", terminal="budget_exhausted", header_overrides=t_header, actions=[t_landing, open1],
                                    metadata_overrides={**t_metadata, "stop_words": stop_words[:-1]})
        with self.assertRaisesRegex(ValidationError, "provider-metadata"):
            project_ranked_predictions(tokenised, output, self.input, documents, t_input)


if __name__ == "__main__": unittest.main()
