from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from retrieval_arena.errors import ValidationError
from retrieval_arena.cli import main
from retrieval_arena.dual_profile import validate_destination_qrel_inventory
from retrieval_arena.profile_evaluation import evaluate_profile_files
from retrieval_arena.replay_compare import compare_profile_replays
from retrieval_arena.review_packets import build_review_packet
from unit_tests.test_dual_profile import lineage_artifact, lineage_record, profile_policy, sha
from unit_tests.test_ranked_scoring import fixture as ranked_fixture
from unit_tests.test_review_packets import candidate, completed_judgments, review_policy


def self_hash(value, field):
    from retrieval_arena.dual_profile import self_hash as calculate
    value[field] = calculate(value, field)
    return value


class ProfileEvaluationTests(unittest.TestCase):
    def write_json(self, path: Path, value) -> None:
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    def write_jsonl(self, path: Path, rows) -> None:
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8", newline="\n")

    def build_inputs(
        self,
        root: Path,
        *,
        calibration=False,
        missing_bertscore=False,
        source_qrels_scope="pooled",
    ) -> Path:
        dataset, qrels, predictions, ranked_policy = ranked_fixture(source_qrels_scope)
        dataset["corpus_snapshot_id"] = "after"
        requirements = {
            "schema_version": "retrieval_arena.evidence_requirements.v1",
            "dataset_id": "d",
            "query_set_id": "qs",
            "ordered_query_ids": ["q1", "q2"],
            "requirements": [
                {"query_id": "q1", "evidence_requirement_id": "r1", "requirement_text": "one", "requirement_provenance_sha256": sha("r1")},
                {"query_id": "q2", "evidence_requirement_id": "r2", "requirement_text": "two", "requirement_provenance_sha256": sha("r2")},
            ],
            "requirements_sha256": "0" * 64,
        }
        self_hash(requirements, "requirements_sha256")
        bindings = {
            "schema_version": "retrieval_arena.requirement_bindings.v1",
            "dataset_id": "d",
            "query_set_id": "qs",
            "ordered_snapshot_ids": ["before", "after"],
            "bindings": [
                {"query_id": "q1", "evidence_requirement_id": "r1", "corpus_snapshot_id": "after", "support_target_id": "a", "evidence_unit_id": "u1", "normalized_evidence_sha256": sha("u1"), "bounded_context_sha256": sha("c1"), "structural_owner": "s1", "repository_provenance_sha256": sha("repo"), "document_provenance_sha256": sha("a")},
                {"query_id": "q2", "evidence_requirement_id": "r2", "corpus_snapshot_id": "after", "support_target_id": "c", "evidence_unit_id": "u2", "normalized_evidence_sha256": sha("u2"), "bounded_context_sha256": sha("c2"), "structural_owner": "s2", "repository_provenance_sha256": sha("repo"), "document_provenance_sha256": sha("c")},
            ],
            "bindings_sha256": "0" * 64,
        }
        self_hash(bindings, "bindings_sha256")
        lineage = lineage_artifact([lineage_record(), lineage_record("novel_support_unjudged", "novel-source", "novel-target")])
        lineage["records"][1]["source_evidence_unit_id"] = "novel-u1"
        lineage["records"][1]["destination_evidence_unit_id"] = "novel-u2"
        self_hash(lineage["records"][1], "record_sha256")
        self_hash(lineage, "lineage_sha256")
        requirement_predictions = [
            {"schema_version": "retrieval_arena.requirement_prediction.v1", "query_id": "q1", "corpus_snapshot_id": "after", "context_item_budget_k": 2, "ranked_support_target_ids": ["a", "x"]},
            {"schema_version": "retrieval_arena.requirement_prediction.v1", "query_id": "q2", "corpus_snapshot_id": "after", "context_item_budget_k": 2, "ranked_support_target_ids": ["c", "y"]},
        ]
        evaluation_policy = profile_policy("scientific_calibration" if calibration else "operational_automatic")
        evaluation_policy["requirements_sha256"] = requirements["requirements_sha256"]
        evaluation_policy["bindings_sha256"] = bindings["bindings_sha256"]
        evaluation_policy["lineage_policy_id"] = lineage["policy_id"]
        evaluation_policy["lineage_sha256"] = lineage["lineage_sha256"]
        self_hash(evaluation_policy, "policy_sha256")
        files = {
            "profile_policy.json": evaluation_policy,
            "dataset.json": dataset,
            "ranked_policy.json": ranked_policy,
            "requirements.json": requirements,
            "bindings.json": bindings,
            "lineage.json": lineage,
        }
        for name, value in files.items():
            self.write_json(root / name, value)
        self.write_jsonl(root / "qrels.jsonl", qrels)
        dataset["qrels_sha256"] = hashlib.sha256((root / "qrels.jsonl").read_bytes()).hexdigest()
        self.write_json(root / "dataset.json", dataset)
        self.write_jsonl(root / "predictions.jsonl", predictions)
        self.write_jsonl(root / "requirement_predictions.jsonl", requirement_predictions)
        review_paths = {"review_policy_path": None, "review_packet_path": None, "judgment_artifact_path": None}
        if calibration:
            rp = review_policy(ceiling=4)
            rp["dataset_id"] = "d"; rp["query_set_id"] = "qs"; self_hash(rp, "policy_sha256")
            candidates = []
            for index, (qid, target, snapshot) in enumerate((("q1", "a", "after"), ("q1", "x", "after"), ("q2", "c", "after"), ("q2", "y", "after"))):
                row = candidate(index, snapshot=snapshot)
                row["query_id"] = qid; row["support_target_id"] = target
                row["candidate_provenance_sha256"] = sha(f"{qid}-{target}")
                candidates.append(row)
            packet = build_review_packet(candidates, rp)
            judgments = completed_judgments(packet, rp)
            self.write_json(root / "review_policy.json", rp)
            self.write_json(root / "review_packet.json", packet)
            self.write_json(root / "judgments.json", judgments)
            review_paths = {"review_policy_path": "review_policy.json", "review_packet_path": "review_packet.json", "judgment_artifact_path": "judgments.json"}
        config = {
            "schema_version": "retrieval_arena.dual_profile_config.v1",
            "automatic_qrels_snapshot_id": "before",
            "profile_policy_path": "profile_policy.json",
            "ranked_dataset_path": "dataset.json",
            "automatic_qrels_path": "qrels.jsonl",
            "ranked_predictions_path": "predictions.jsonl",
            "ranked_metric_policy_path": "ranked_policy.json",
            "requirements_path": "requirements.json",
            "bindings_path": "bindings.json",
            "lineage_path": "lineage.json",
            "requirement_predictions_path": "requirement_predictions.jsonl",
            "answer_pairs_path": None,
            "bertscore_provider_path": "missing-provider.json" if missing_bertscore else None,
            "bertscore_scores_path": "missing-scores.json" if missing_bertscore else None,
            **review_paths,
        }
        self.write_json(root / "config.json", config)
        return root / "config.json"

    def test_zero_human_automatic_missing_calibration_and_unavailable_bertscore(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root, missing_bertscore=True)
            evaluated = evaluate_profile_files(config, root / "out")
            result = evaluated["result"]
            self.assertEqual(result["evaluation_profile"], "operational_automatic")
            self.assertEqual(result["calibration"]["availability"], "absent_valid")
            self.assertEqual(result["metric_families"]["automatic_answer_diagnostics"]["bertscore"]["unavailable_reason"], "registered_provider_artifact_missing")
            self.assertEqual(result["metric_families"]["project_specific_diagnostics"]["classification"], "project_specific_not_standard_recall")
            self.assertEqual(len(evaluated["written_artifacts"]), 5)

    def test_zero_relevant_snapshot_query_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root)
            qrels = [json.loads(line) for line in (root / "qrels.jsonl").read_text(encoding="utf-8").splitlines()]
            for row in qrels:
                if row["query_id"] == "q2":
                    row["relevance"] = 0
            self.write_jsonl(root / "qrels.jsonl", qrels)
            dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
            dataset["qrels_sha256"] = hashlib.sha256((root / "qrels.jsonl").read_bytes()).hexdigest()
            self.write_json(root / "dataset.json", dataset)
            result = evaluate_profile_files(config, root / "out")["result"]
            standard = result["metric_families"]["standard_ranked_retrieval"]
            self.assertEqual(standard["unavailable_queries"][0]["query_id"], "q2")
            self.assertEqual(standard["unavailable_queries"][0]["unavailable_reason"], "zero_relevant_items")
            self.assertEqual(standard["aggregate"]["profile_aggregate_scope"], "available_queries_only")

    def test_destination_qrels_use_only_admitted_relocation_without_stale_source_leakage(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root)
            records = [
                lineage_record("exact_target_relocation", "a", "relocated-a"),
                lineage_record("support_removed", "b", "removed-b"),
                lineage_record("identity_unchanged", "x", "x"),
                lineage_record("novel_support_unjudged", "novel-source", "novel-x"),
                lineage_record("semantic_rewrite_unresolved", "c", "rewritten-c"),
            ]
            for index, row in enumerate(records):
                row["source_evidence_unit_id"] = f"source-u{index}"
                row["destination_evidence_unit_id"] = f"destination-u{index}"
                if index == 4:
                    row["query_id"] = "q2"
                    row["evidence_requirement_id"] = "r2"
                self_hash(row, "record_sha256")
            lineage = lineage_artifact(records)
            self.write_json(root / "lineage.json", lineage)
            policy = json.loads((root / "profile_policy.json").read_text(encoding="utf-8"))
            policy["lineage_sha256"] = lineage["lineage_sha256"]
            self_hash(policy, "policy_sha256")
            self.write_json(root / "profile_policy.json", policy)
            predictions = [json.loads(line) for line in (root / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
            predictions[0]["items"][0]["support_target_id"] = "relocated-a"
            predictions[0]["items"][0]["source_item_id"] = "chunk-relocated-a"
            predictions[0]["items"][0]["source_provenance_sha256"] = sha("source-relocated-a")
            self.write_jsonl(root / "predictions.jsonl", predictions)
            result = evaluate_profile_files(config, root / "out")["result"]
            diagnostics = result["metric_families"]["project_specific_diagnostics"]
            inventory = diagnostics["destination_qrel_inventory"]
            self.assertEqual(inventory["source_snapshot_id"], "before")
            self.assertEqual(inventory["destination_snapshot_id"], "after")
            self.assertEqual([(row["query_id"], row["support_target_id"]) for row in inventory["qrels"]], [("q1", "relocated-a")])
            self.assertNotIn("a", {row["support_target_id"] for row in inventory["qrels"]})
            standard = result["metric_families"]["standard_ranked_retrieval"]
            self.assertEqual(standard["per_query"][0]["evidence_recall"], 1.0)
            self.assertEqual([row["query_id"] for row in standard["unavailable_queries"]], ["q2"])
            self.assertEqual(result["inventory"]["judged_top_k_count"], 1)
            self.assertEqual(result["inventory"]["unjudged_top_k_count"], 3)

    def test_complete_source_derives_pooled_destination_with_disposition_audit(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root, source_qrels_scope="complete")
            records = [
                lineage_record("exact_target_relocation", "a", "relocated-a"),
                lineage_record("support_removed", "b", "removed-b"),
                lineage_record("identity_unchanged", "x", "x"),
                lineage_record("semantic_rewrite_unresolved", "c", "rewritten-c"),
                lineage_record("novel_support_unjudged", "novel-source", "novel-y"),
            ]
            for index, row in enumerate(records):
                row["source_evidence_unit_id"] = f"source-u{index}"
                row["destination_evidence_unit_id"] = f"destination-u{index}"
                if index >= 3:
                    row["query_id"] = "q2"
                    row["evidence_requirement_id"] = "r2"
                self_hash(row, "record_sha256")
            lineage = lineage_artifact(records)
            self.write_json(root / "lineage.json", lineage)
            policy = json.loads((root / "profile_policy.json").read_text(encoding="utf-8"))
            policy["lineage_sha256"] = lineage["lineage_sha256"]
            self_hash(policy, "policy_sha256")
            self.write_json(root / "profile_policy.json", policy)
            predictions = [
                json.loads(line)
                for line in (root / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            for item, target in zip(
                [*predictions[0]["items"], *predictions[1]["items"]],
                ["relocated-a", "x", "rewritten-c", "novel-y"],
            ):
                item["support_target_id"] = target
                item["source_item_id"] = f"chunk-{target}"
                item["source_provenance_sha256"] = sha(f"source-{target}")
            self.write_jsonl(root / "predictions.jsonl", predictions)

            evaluated = evaluate_profile_files(config, root / "out")
            result = evaluated["result"]
            diagnostics = result["metric_families"]["project_specific_diagnostics"]
            inventory = diagnostics["destination_qrel_inventory"]
            self.assertEqual(result["source_qrels_scope"], "complete")
            self.assertEqual(result["qrels_scope"], "pooled")
            self.assertEqual(inventory["schema_version"], "retrieval_arena.destination_qrel_inventory.v2")
            self.assertEqual(inventory["inventory_sha256"], self_hash(copy.deepcopy(inventory), "inventory_sha256")["inventory_sha256"])
            self.assertEqual(
                {(row["query_id"], row["source_support_target_id"]) for row in inventory["source_qrel_dispositions"]},
                {("q1", "a"), ("q1", "b"), ("q1", "x"), ("q2", "c"), ("q2", "y")},
            )
            source_reasons = {row["reason"] for row in inventory["source_qrel_dispositions"]}
            self.assertTrue(
                {"exact_positive_transfer", "source_nonpositive_judgment", "semantic_rewrite_unresolved", "support_removed"}
                <= source_reasons
            )
            top_reasons = {row["reason"] for row in inventory["destination_top_k_judgments"]}
            self.assertEqual(
                top_reasons,
                {"exact_positive_transfer", "source_nonpositive_judgment", "semantic_rewrite_unresolved", "novel_support_unjudged"},
            )
            self.assertEqual(inventory["judged_top_k_count"], 1)
            self.assertEqual(inventory["unjudged_top_k_count"], 3)
            self.assertEqual(result["qrel_audit_hashes"]["source_qrel_dispositions_sha256"], inventory["source_qrel_dispositions_sha256"])
            audit = json.loads((root / "out" / "automatic_audit.v1.json").read_text(encoding="utf-8"))
            evidence = json.loads((root / "out" / "automatic_evidence.v1.json").read_text(encoding="utf-8"))
            report = (root / "out" / "automatic_report.v1.md").read_text(encoding="utf-8")
            self.assertEqual(audit["qrel_audit_hashes"], result["qrel_audit_hashes"])
            self.assertEqual(evidence["qrel_audit_hashes"], result["qrel_audit_hashes"])
            for value in result["qrel_audit_hashes"].values():
                self.assertIn(value, report)

            mutated = copy.deepcopy(inventory)
            mutated["source_qrel_dispositions"][0]["reason"] = "silently_dropped"
            qrels = [
                json.loads(line)
                for line in (root / "qrels.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            dataset = json.loads((root / "dataset.json").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValidationError, "exact recomposition mismatch"):
                validate_destination_qrel_inventory(
                    mutated,
                    qrels,
                    lineage,
                    "before",
                    "after",
                    dataset["ordered_query_ids"],
                    dataset["relevance_scale"],
                    source_qrels_scope=dataset["qrels_scope"],
                    ranked_predictions=predictions,
                    cutoff=2,
                    ranked_dataset_sha256=hashlib.sha256((root / "dataset.json").read_bytes()).hexdigest(),
                    ranked_policy_sha256=hashlib.sha256((root / "ranked_policy.json").read_bytes()).hexdigest(),
                )

    def test_fully_judged_destination_retains_complete_scope(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root, source_qrels_scope="complete")
            records = []
            for index, (qid, rid, target) in enumerate(
                (("q1", "r1", "a"), ("q1", "r1", "b"), ("q2", "r2", "c"))
            ):
                row = lineage_record("identity_unchanged", target, target)
                row["query_id"] = qid
                row["evidence_requirement_id"] = rid
                row["source_evidence_unit_id"] = f"source-complete-{index}"
                row["destination_evidence_unit_id"] = f"destination-complete-{index}"
                self_hash(row, "record_sha256")
                records.append(row)
            lineage = lineage_artifact(records)
            self.write_json(root / "lineage.json", lineage)
            policy = json.loads((root / "profile_policy.json").read_text(encoding="utf-8"))
            policy["lineage_sha256"] = lineage["lineage_sha256"]
            self_hash(policy, "policy_sha256")
            self.write_json(root / "profile_policy.json", policy)
            predictions = [
                json.loads(line)
                for line in (root / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            predictions[0]["items"] = predictions[0]["items"][:2]
            predictions[0]["items"][1]["support_target_id"] = "b"
            predictions[0]["items"][1]["source_item_id"] = "chunk-b"
            predictions[0]["items"][1]["source_provenance_sha256"] = sha("source-b")
            predictions[1]["items"] = predictions[1]["items"][:1]
            predictions[1]["max_rank"] = 1
            predictions[1]["retrieval_exhausted"] = True
            self.write_jsonl(root / "predictions.jsonl", predictions)
            result = evaluate_profile_files(config, root / "out")["result"]
            self.assertEqual(result["source_qrels_scope"], "complete")
            self.assertEqual(result["qrels_scope"], "complete")
            self.assertEqual(result["inventory"]["unjudged_top_k_count"], 0)

    def test_no_admitted_positive_destination_makes_all_standard_metrics_unavailable(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root)
            lineage = lineage_artifact([lineage_record("support_removed", "a", "removed-a")])
            self.write_json(root / "lineage.json", lineage)
            policy = json.loads((root / "profile_policy.json").read_text(encoding="utf-8"))
            policy["lineage_sha256"] = lineage["lineage_sha256"]
            self_hash(policy, "policy_sha256")
            self.write_json(root / "profile_policy.json", policy)
            result = evaluate_profile_files(config, root / "out")["result"]
            standard = result["metric_families"]["standard_ranked_retrieval"]
            self.assertEqual(standard["per_query"], [])
            self.assertEqual(len(standard["unavailable_queries"]), 2)
            self.assertEqual(standard["aggregate"]["profile_aggregate_scope"], "unavailable_no_admitted_positive_queries")
            self.assertIsNone(standard["aggregate"]["macro_evidence_recall"])
            self.assertEqual(result["inventory"]["judged_top_k_count"], 0)
            self.assertEqual(result["inventory"]["unjudged_top_k_count"], 4)

    def test_source_snapshot_binding_and_scores_without_provider_fail_closed(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config_path = self.build_inputs(root)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["automatic_qrels_snapshot_id"] = "wrong-source"
            self.write_json(config_path, config)
            with self.assertRaisesRegex(ValidationError, "source snapshot"):
                evaluate_profile_files(config_path, root / "out")
            config["automatic_qrels_snapshot_id"] = "before"
            config["bertscore_scores_path"] = "scores.json"
            self.write_json(root / "scores.json", {})
            self.write_json(config_path, config)
            with self.assertRaisesRegex(ValidationError, "requires a registered provider"):
                evaluate_profile_files(config_path, root / "out2")

    def test_missing_registered_bertscore_model_does_not_fail_automatic_family(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config_path = self.build_inputs(root)
            self.write_jsonl(root / "answers.jsonl", [{"query_id": "q1", "generated_answer": "a", "reference_answer": "a"}, {"query_id": "q2", "generated_answer": "b", "reference_answer": "b"}])
            provider = {
                "schema_version": "retrieval_arena.bertscore_provider.v1", "provider_id": "offline", "package_name": "bert-score", "package_version": "0.3.13",
                "package_artifact_path": "missing-wheel", "package_artifact_sha256": sha("wheel"), "checkpoint_id": "checkpoint", "checkpoint_revision": "revision",
                "model_artifact_path": "missing-model", "model_artifact_sha256": sha("model"), "tokenizer_id": "tokenizer", "tokenizer_revision": "revision",
                "tokenizer_artifact_path": "missing-tokenizer", "tokenizer_artifact_sha256": sha("tokenizer"), "baseline_policy": "none",
                "baseline_artifact_path": None, "baseline_artifact_sha256": None, "rescaling_policy": "none", "language": "en", "device": "cpu",
                "dtype": "float32", "runtime_download": False, "provider_sha256": "0" * 64,
            }
            self_hash(provider, "provider_sha256")
            self.write_json(root / "bertscore_provider.json", provider)
            self.write_json(root / "bertscore_scores.json", {})
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["answer_pairs_path"] = "answers.jsonl"
            config["bertscore_provider_path"] = "bertscore_provider.json"
            config["bertscore_scores_path"] = "bertscore_scores.json"
            self.write_json(config_path, config)
            result = evaluate_profile_files(config_path, root / "out")["result"]
            diagnostic = result["metric_families"]["automatic_answer_diagnostics"]["bertscore"]
            self.assertEqual(diagnostic["availability"], "unavailable")
            self.assertEqual(diagnostic["unavailable_reason"], "package_artifact_missing")

    def test_automatic_replay_is_byte_identical(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root)
            one = evaluate_profile_files(config, root / "one")
            two = evaluate_profile_files(config, root / "two")
            self.assertEqual(one, two)
            hashes_one = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / "one").iterdir()}
            hashes_two = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / "two").iterdir()}
            self.assertEqual(hashes_one, hashes_two)
            self.assertTrue(compare_profile_replays(one["replay"], two["replay"])["matched_exactly"])

    def test_real_cli_writes_isolated_automatic_family(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root)
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(["evaluate-profile", "--config", str(config), "--out-dir", str(root / "out")])
            self.assertEqual(code, 0)
            self.assertIn('"evaluation_profile": "operational_automatic"', stdout.getvalue())
            self.assertTrue((root / "out" / "automatic_replay.v1.json").is_file())

    def test_profile_replay_family_mixing_rejects(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            automatic = evaluate_profile_files(self.build_inputs(root), root / "automatic")
            calibration_root = root / "calibration-inputs"; calibration_root.mkdir()
            calibration = evaluate_profile_files(self.build_inputs(calibration_root, calibration=True), root / "calibration")
            with self.assertRaisesRegex(ValidationError, "family mismatch"):
                compare_profile_replays(automatic["replay"], calibration["replay"])

    def test_scientific_calibration_is_separate_and_artifact_only(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config = self.build_inputs(root, calibration=True)
            evaluated = evaluate_profile_files(config, root / "out")
            result = evaluated["result"]
            self.assertEqual(result["evaluation_profile"], "scientific_calibration")
            self.assertEqual(result["result_family"], "calibration")
            self.assertTrue(result["automatic_family_reference"]["not_combined"])
            self.assertIsNone(result["composite_metric"])
            self.assertIn("bpref_aggregate", result["metric_families"]["calibrated_relevance"])
            self.assertEqual(set(evaluated["written_artifacts"]), {"calibration_result.v1.json", "calibration_replay.v1.json", "calibration_audit.v1.json", "calibration_evidence.v1.json", "calibration_report.v1.md"})

    def test_profile_mixing_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config_path = self.build_inputs(root, calibration=True)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.write_json(root / "profile_policy.json", profile_policy())
            with self.assertRaisesRegex(ValidationError, "profile mixing"):
                evaluate_profile_files(config_path, root / "out")

    def test_qrels_hash_mismatch_fails_before_scoring(self):
        with tempfile.TemporaryDirectory(dir=".tmp_unit_tests") as root_text:
            root = Path(root_text)
            config_path = self.build_inputs(root)
            with (root / "qrels.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(ValidationError, "qrels_path hash mismatch"):
                evaluate_profile_files(config_path, root / "out")


if __name__ == "__main__":
    unittest.main()
