from __future__ import annotations

import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from retrieval_arena.dual_profile import (
    PROFILE_AUTOMATIC,
    PROFILE_CALIBRATION,
    run_bertscore_offline,
    score_bpref,
    score_requirements,
    self_hash,
    sha256_file,
    sha256_json,
    validate_bertscore_provider,
    validate_bertscore_scores,
    validate_bindings,
    validate_lineage,
    validate_profile_policy,
    validate_requirements,
    transfer_qrels,
)
from retrieval_arena.errors import ValidationError


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def hashed(value, field):
    value[field] = self_hash(value, field)
    return value


def profile_policy(profile=PROFILE_AUTOMATIC, classification="confirmatory"):
    value = {
        "schema_version": "retrieval_arena.evaluation_profile_policy.v1",
        "policy_id": f"{profile}-v1",
        "evaluation_profile": profile,
        "classification": classification,
        "context_item_budget_k": 2,
        "requirements_sha256": sha("requirements"),
        "bindings_sha256": sha("bindings"),
        "lineage_policy_id": "exact-v1",
        "lineage_sha256": sha("lineage"),
        "calibration_requirement": "optional" if profile == PROFILE_AUTOMATIC else "required",
        "standard_metric_family": ["evidence_recall", "ndcg", "reciprocal_rank"],
        "project_diagnostic_family": ["complete_support", "requirement_coverage", "all_requirements", "support_lineage"],
        "automatic_answer_family": ["lexical", "bertscore_precision", "bertscore_recall", "bertscore_f1"],
        "calibrated_relevance_family": ["bpref", "answer_bearing_relevance"],
        "calibrated_answer_family": ["semantic_answer_quality", "paraphrase_robustness"],
        "calibrated_attribution_family": ["attribution_correctness"],
        "composite_metric": None,
        "runtime_network": "forbidden",
        "interactive_human_work": "forbidden",
        "policy_sha256": "0" * 64,
    }
    return hashed(value, "policy_sha256")


def requirements_fixture():
    value = {
        "schema_version": "retrieval_arena.evidence_requirements.v1",
        "dataset_id": "tiny",
        "query_set_id": "queries",
        "ordered_query_ids": ["q1"],
        "requirements": [
            {"query_id": "q1", "evidence_requirement_id": "r1", "requirement_text": "first", "requirement_provenance_sha256": sha("r1")},
            {"query_id": "q1", "evidence_requirement_id": "r2", "requirement_text": "second", "requirement_provenance_sha256": sha("r2")},
        ],
        "requirements_sha256": "0" * 64,
    }
    return hashed(value, "requirements_sha256")


def bindings_fixture():
    rows = []
    for rid, target, unit in (("r1", "a", "u1"), ("r1", "b", "u2"), ("r2", "a", "u3")):
        rows.append(
            {
                "query_id": "q1",
                "evidence_requirement_id": rid,
                "corpus_snapshot_id": "after",
                "support_target_id": target,
                "evidence_unit_id": unit,
                "normalized_evidence_sha256": sha(unit),
                "bounded_context_sha256": sha("context-" + unit),
                "structural_owner": "section:install",
                "repository_provenance_sha256": sha("repo"),
                "document_provenance_sha256": sha(target),
            }
        )
    value = {
        "schema_version": "retrieval_arena.requirement_bindings.v1",
        "dataset_id": "tiny",
        "query_set_id": "queries",
        "ordered_snapshot_ids": ["before", "after"],
        "bindings": rows,
        "bindings_sha256": "0" * 64,
    }
    return hashed(value, "bindings_sha256")


def requirement_prediction(targets):
    return {
        "schema_version": "retrieval_arena.requirement_prediction.v1",
        "query_id": "q1",
        "corpus_snapshot_id": "after",
        "context_item_budget_k": 2,
        "ranked_support_target_ids": targets,
    }


def lineage_record(transfer_class="identity_unchanged", source="a", destination="a"):
    value = {
        "query_id": "q1",
        "evidence_requirement_id": "r1",
        "source_snapshot_id": "before",
        "destination_snapshot_id": "after",
        "source_support_target_id": source,
        "destination_support_target_id": destination,
        "source_evidence_unit_id": "u1",
        "destination_evidence_unit_id": "u1",
        "source_normalized_evidence_sha256": sha("evidence"),
        "destination_normalized_evidence_sha256": sha("evidence"),
        "source_bounded_context_sha256": sha("context"),
        "destination_bounded_context_sha256": sha("context"),
        "source_structural_owner": "section:install",
        "destination_structural_owner": "section:install",
        "repository_provenance_sha256": sha("repo"),
        "source_document_provenance_sha256": sha("source-doc"),
        "destination_document_provenance_sha256": sha("source-doc") if transfer_class == "identity_unchanged" else sha("destination-doc"),
        "repository_document_lineage_exact": True,
        "transfer_class": transfer_class,
        "mapping_shape": "one_to_one",
        "decomposed_exact_edge": False,
        "ambiguous": False,
        "contradictory_destination": False,
        "conflicting": False,
        "record_sha256": "0" * 64,
    }
    return hashed(value, "record_sha256")


def lineage_artifact(records):
    value = {
        "schema_version": "retrieval_arena.support_lineage.v1",
        "policy_id": "exact-v1",
        "source_snapshot_id": "before",
        "destination_snapshot_id": "after",
        "records": records,
        "lineage_sha256": "0" * 64,
    }
    return hashed(value, "lineage_sha256")


class DualProfileTests(unittest.TestCase):
    def test_profile_contracts_and_no_composite(self):
        self.assertEqual(validate_profile_policy(profile_policy())["evaluation_profile"], PROFILE_AUTOMATIC)
        self.assertEqual(validate_profile_policy(profile_policy(PROFILE_CALIBRATION))["evaluation_profile"], PROFILE_CALIBRATION)
        bad = profile_policy()
        bad["composite_metric"] = "forbidden"
        bad["policy_sha256"] = self_hash(bad, "policy_sha256")
        with self.assertRaisesRegex(ValidationError, "composite_metric"):
            validate_profile_policy(bad)

    def test_profile_mixing_and_unknown_fields_reject(self):
        bad = profile_policy()
        bad["calibration_requirement"] = "required"
        bad["policy_sha256"] = self_hash(bad, "policy_sha256")
        with self.assertRaisesRegex(ValidationError, "cannot require calibration"):
            validate_profile_policy(bad)
        bad = profile_policy()
        bad["silent_upgrade"] = True
        with self.assertRaisesRegex(ValidationError, "fields must be exactly"):
            validate_profile_policy(bad)

    def test_requirement_or_within_and_across_and_many_to_one(self):
        requirements = validate_requirements(requirements_fixture())
        bindings = validate_bindings(bindings_fixture(), requirements)
        rows, aggregate = score_requirements(
            requirements,
            bindings,
            [requirement_prediction(["a", "x"])],
            "after",
            2,
        )
        self.assertEqual(rows[0]["requirement_coverage"], 1.0)
        self.assertEqual(rows[0]["all_requirements"], 1.0)
        self.assertEqual(rows[0]["matched_evidence_unit_ids_by_requirement"], {"r1": ["u1"], "r2": ["u3"]})
        self.assertEqual(aggregate["macro_all_requirements"], 1.0)

    def test_requirement_empty_binding_stays_uncovered(self):
        requirements = validate_requirements(requirements_fixture())
        artifact = bindings_fixture()
        artifact["bindings"] = [row for row in artifact["bindings"] if row["evidence_requirement_id"] == "r1"]
        artifact["bindings_sha256"] = self_hash(artifact, "bindings_sha256")
        bindings = validate_bindings(artifact, requirements)
        rows, _ = score_requirements(requirements, bindings, [requirement_prediction(["a"])], "after", 2)
        self.assertEqual(rows[0]["requirement_coverage"], 0.5)
        self.assertEqual(rows[0]["uncovered_requirement_ids"], ["r2"])

    def test_exact_identity_relocation_and_contextual_preservation(self):
        records = [
            lineage_record(),
            lineage_record("exact_target_relocation", "b", "c"),
            lineage_record("exact_evidence_unit_preserved", "d", "e"),
        ]
        records[1]["source_evidence_unit_id"] = "u2"; records[1]["destination_evidence_unit_id"] = "u2"; records[1]["record_sha256"] = self_hash(records[1], "record_sha256")
        records[2]["source_evidence_unit_id"] = "u3"; records[2]["destination_evidence_unit_id"] = "u3"; records[2]["record_sha256"] = self_hash(records[2], "record_sha256")
        admitted, counts = validate_lineage(lineage_artifact(records))
        self.assertEqual(len(admitted), 3)
        self.assertEqual(counts["exact_target_relocation"], 1)

    def test_unresolved_novel_and_removed_never_transfer(self):
        records = []
        for index, name in enumerate(("semantic_rewrite_unresolved", "novel_support_unjudged", "support_removed")):
            row = lineage_record(name, f"s{index}", f"d{index}")
            row["source_evidence_unit_id"] = f"us{index}"; row["destination_evidence_unit_id"] = f"ud{index}"
            row["record_sha256"] = self_hash(row, "record_sha256")
            records.append(row)
        admitted, counts = validate_lineage(lineage_artifact(records))
        self.assertEqual(admitted, [])
        self.assertEqual(sum(counts.values()), 3)

    def test_qrels_transfer_only_over_exact_authorized_classes(self):
        records = [
            lineage_record(),
            lineage_record("exact_target_relocation", "b", "c"),
            lineage_record("exact_evidence_unit_preserved", "d", "e"),
            lineage_record("semantic_rewrite_unresolved", "f", "g"),
            lineage_record("novel_support_unjudged", "h", "i"),
            lineage_record("support_removed", "j", "k"),
        ]
        for index, row in enumerate(records):
            row["source_evidence_unit_id"] = f"source-u{index}"; row["destination_evidence_unit_id"] = f"destination-u{index}"
            row["record_sha256"] = self_hash(row, "record_sha256")
        qrels = [
            {"query_id": "q1", "support_target_id": target, "relevance": 1, "judgment_id": f"j-{target}", "judgment_provenance_sha256": sha(f"j-{target}")}
            for target in ("a", "b", "d", "f", "h", "j")
        ]
        transferred = transfer_qrels(qrels, lineage_artifact(records))
        self.assertEqual([row["support_target_id"] for row in transferred], ["a", "c", "e"])
        self.assertTrue(all(row["judgment_id"].startswith("exact-lineage:") for row in transferred))

    def test_ambiguous_contradictory_context_and_semantic_rewrite_reject_transfer(self):
        for mutation, message in (
            (("ambiguous", True), "predicate failed"),
            (("contradictory_destination", True), "predicate failed"),
            (("destination_bounded_context_sha256", sha("changed")), "predicate failed"),
            (("destination_normalized_evidence_sha256", sha("paraphrase")), "predicate failed"),
        ):
            row = lineage_record("exact_evidence_unit_preserved", "a", "b")
            row[mutation[0]] = mutation[1]
            row["record_sha256"] = self_hash(row, "record_sha256")
            with self.subTest(mutation=mutation[0]), self.assertRaisesRegex(ValidationError, message):
                validate_lineage(lineage_artifact([row]))

    def test_duplicate_and_cyclic_lineage_reject(self):
        row = lineage_record()
        with self.assertRaisesRegex(ValidationError, "duplicate lineage"):
            validate_lineage(lineage_artifact([row, copy.deepcopy(row)]))
        left = lineage_record("exact_target_relocation", "a", "b")
        right = lineage_record("exact_target_relocation", "b", "a")
        right["source_evidence_unit_id"] = "u2"; right["destination_evidence_unit_id"] = "u2"; right["record_sha256"] = self_hash(right, "record_sha256")
        with self.assertRaisesRegex(ValidationError, "cyclic"):
            validate_lineage(lineage_artifact([left, right]))

    def test_many_to_many_requires_unique_decomposed_edges(self):
        row = lineage_record("exact_evidence_unit_preserved", "a", "b")
        row["mapping_shape"] = "many_to_many_decomposed"
        row["record_sha256"] = self_hash(row, "record_sha256")
        with self.assertRaisesRegex(ValidationError, "decomposed exact"):
            validate_lineage(lineage_artifact([row]))
        row["decomposed_exact_edge"] = True
        row["record_sha256"] = self_hash(row, "record_sha256")
        self.assertEqual(len(validate_lineage(lineage_artifact([row]))[0]), 1)
        second = lineage_record("exact_evidence_unit_preserved", "a", "c")
        second["source_evidence_unit_id"] = "u2"; second["destination_evidence_unit_id"] = "u2"
        second["record_sha256"] = self_hash(second, "record_sha256")
        first = lineage_record("exact_evidence_unit_preserved", "a", "b")
        with self.assertRaisesRegex(ValidationError, "unsupported many-to-many"):
            validate_lineage(lineage_artifact([first, second]))
        for item in (first, second):
            item["mapping_shape"] = "many_to_many_decomposed"; item["decomposed_exact_edge"] = True
            item["record_sha256"] = self_hash(item, "record_sha256")
        self.assertEqual(len(validate_lineage(lineage_artifact([first, second]))[0]), 2)

    def test_missing_lineage_field_fails_closed(self):
        row = lineage_record()
        del row["destination_document_provenance_sha256"]
        with self.assertRaisesRegex(ValidationError, "fields must be exactly"):
            validate_lineage(lineage_artifact([row]))

    def test_bpref_unjudged_and_availability(self):
        rows, aggregate = score_bpref(
            ["q1", "q2", "q3"],
            {"q1": ["p", "u", "n"], "q2": ["n"], "q3": ["p"]},
            {"q1": {"p": 1, "n": 0}, "q2": {"n": 0}, "q3": {"p": 1}},
            "pytrec_eval",
            "0.5.10",
        )
        self.assertEqual(rows[0]["bpref"], 1.0)
        self.assertEqual(rows[0]["unjudged_count"], 1)
        self.assertEqual(rows[1]["unavailable_reason"], "zero_positive_judgments")
        self.assertEqual(rows[2]["unavailable_reason"], "zero_negative_judgments")
        self.assertEqual(aggregate["metric_classification"], "standard_ir_calibration_only")

    def test_bertscore_provider_identity_scores_and_blind_spot_fixture(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            package = root / "bert_score.whl"; package.write_bytes(b"wheel")
            model = root / "model.bin"; model.write_bytes(b"model")
            tokenizer = root / "tokenizer.json"; tokenizer.write_bytes(b"tokenizer")
            provider = {
                "schema_version": "retrieval_arena.bertscore_provider.v1",
                "provider_id": "bert-tiny-offline",
                "package_name": "bert-score",
                "package_version": "0.3.13",
                "package_artifact_path": "bert_score.whl",
                "package_artifact_sha256": sha256_file(package),
                "checkpoint_id": "frozen-checkpoint",
                "checkpoint_revision": "revision-1",
                "model_artifact_path": "model.bin",
                "model_artifact_sha256": sha256_file(model),
                "tokenizer_id": "frozen-tokenizer",
                "tokenizer_revision": "revision-1",
                "tokenizer_artifact_path": "tokenizer.json",
                "tokenizer_artifact_sha256": sha256_file(tokenizer),
                "baseline_policy": "none",
                "baseline_artifact_path": None,
                "baseline_artifact_sha256": None,
                "rescaling_policy": "none",
                "language": "en",
                "device": "cpu",
                "dtype": "float32",
                "runtime_download": False,
                "provider_sha256": "0" * 64,
            }
            provider["provider_sha256"] = self_hash(provider, "provider_sha256")
            validate_bertscore_provider(provider, root)
            pairs = [
                {"query_id": "exact", "generated_answer": "restart service", "reference_answer": "restart service"},
                {"query_id": "paraphrase", "generated_answer": "reboot daemon", "reference_answer": "restart service"},
                {"query_id": "unrelated", "generated_answer": "paint wall", "reference_answer": "restart service"},
                {"query_id": "negation", "generated_answer": "do not restart service", "reference_answer": "restart service"},
            ]
            scores = {"exact": (1.0, 1.0, 1.0), "paraphrase": (.8, .8, .8), "unrelated": (.1, .1, .1), "negation": (.92, .92, .92)}
            artifact = {
                "schema_version": "retrieval_arena.bertscore_scores.v1",
                "provider_id": provider["provider_id"],
                "provider_sha256": provider["provider_sha256"],
                "answer_pairs_sha256": sha256_json(pairs),
                "records": [{"query_id": qid, "precision": p, "recall": r, "f1": f} for qid, (p, r, f) in scores.items()],
                "scores_sha256": "0" * 64,
            }
            artifact["scores_sha256"] = self_hash(artifact, "scores_sha256")
            validated = validate_bertscore_scores(artifact, provider, sha256_json(pairs))
            self.assertGreater(validated["paraphrase"]["f1"], validated["unrelated"]["f1"])
            self.assertGreater(validated["negation"]["f1"], validated["paraphrase"]["f1"], "fixture proves the declared factuality blind spot")
            artifact["provider_id"] = "substitute"
            artifact["scores_sha256"] = self_hash(artifact, "scores_sha256")
            with self.assertRaisesRegex(ValidationError, "substitution"):
                validate_bertscore_scores(artifact, provider, sha256_json(pairs))

    def test_bertscore_missing_artifact_and_runtime_download_reject(self):
        provider = {
            "schema_version": "retrieval_arena.bertscore_provider.v1", "provider_id": "p", "package_name": "bert-score", "package_version": "x",
            "package_artifact_path": "missing-wheel", "package_artifact_sha256": sha("wheel"), "checkpoint_id": "c", "checkpoint_revision": "r", "model_artifact_path": "missing",
            "model_artifact_sha256": sha("m"), "tokenizer_id": "t", "tokenizer_revision": "r", "tokenizer_artifact_path": "missing2",
            "tokenizer_artifact_sha256": sha("t"), "baseline_policy": "none", "baseline_artifact_path": None, "baseline_artifact_sha256": None,
            "rescaling_policy": "none", "language": "en", "device": "cpu", "dtype": "float32", "runtime_download": False,
            "provider_sha256": "0" * 64,
        }
        provider["provider_sha256"] = self_hash(provider, "provider_sha256")
        with self.assertRaisesRegex(ValidationError, "artifact missing"):
            validate_bertscore_provider(provider, Path("."))
        provider["runtime_download"] = True
        provider["provider_sha256"] = self_hash(provider, "provider_sha256")
        with self.assertRaisesRegex(ValidationError, "runtime download"):
            validate_bertscore_provider(provider, Path("."))

    def test_offline_bertscore_runner_sets_download_guards(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            (root / "wheel").write_bytes(b"w"); (root / "model").write_bytes(b"m"); (root / "tokenizer").write_bytes(b"t")
            provider = {
                "schema_version": "retrieval_arena.bertscore_provider.v1", "provider_id": "p", "package_name": "bert-score", "package_version": "x",
                "package_artifact_path": "wheel", "package_artifact_sha256": sha256_file(root / "wheel"), "checkpoint_id": "c", "checkpoint_revision": "r", "model_artifact_path": "model",
                "model_artifact_sha256": sha256_file(root / "model"), "tokenizer_id": "t", "tokenizer_revision": "r", "tokenizer_artifact_path": "tokenizer",
                "tokenizer_artifact_sha256": sha256_file(root / "tokenizer"), "baseline_policy": "none", "baseline_artifact_path": None,
                "baseline_artifact_sha256": None, "rescaling_policy": "none", "language": "en", "device": "cpu", "dtype": "float32",
                "runtime_download": False, "provider_sha256": "0" * 64,
            }
            provider["provider_sha256"] = self_hash(provider, "provider_sha256")
            observed = {}
            def scorer(candidates, references, **kwargs):
                import os
                observed["offline"] = (os.environ["HF_HUB_OFFLINE"], os.environ["TRANSFORMERS_OFFLINE"])
                return [1.0], [1.0], [1.0]
            scorer.provider_id = provider["provider_id"]
            scorer.provider_sha256 = provider["provider_sha256"]
            result = run_bertscore_offline(provider, root, [{"query_id": "q", "generated_answer": "a", "reference_answer": "a"}], scorer)
            self.assertEqual(observed["offline"], ("1", "1"))
            self.assertEqual(result["records"][0]["f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
