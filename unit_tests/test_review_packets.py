from __future__ import annotations

import copy
import hashlib
import math
import unittest

from retrieval_arena.dual_profile import self_hash, sha256_json
from retrieval_arena.errors import ValidationError
from retrieval_arena.review_packets import (
    build_review_packet,
    import_judgments,
    validate_review_packet,
)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def review_policy(*, ceiling=240, revealed=False):
    value = {
        "schema_version": "retrieval_arena.review_policy.v1",
        "packet_id": "packet-1",
        "dataset_id": "tiny",
        "query_set_id": "queries",
        "ordered_snapshot_ids": ["before", "after"],
        "seed": 1729,
        "primary_judgment_ceiling": ceiling,
        "duplicate_rate": 0.10,
        "sampling_method": "stratified_simple_random_without_replacement",
        "stratification_fields": ["snapshot_id", "candidate_class", "rank_band"],
        "allocation_method": "proportional_largest_remainder",
        "minimum_one_when_feasible": True,
        "stopping_rule": "frame_exhausted_or_fixed_quotas_complete",
        "adjudication_policy": "blind_all_duplicate_disagreements_s24_precedent",
        "assessor_classes": ["domain_expert"],
        "allowed_assessor_ids": ["a1", "a2", "adjudicator"],
        "guidelines_sha256": sha("guidelines"),
        "outcome_revealed": revealed,
        "policy_sha256": "0" * 64,
    }
    value["policy_sha256"] = self_hash(value, "policy_sha256")
    return value


def candidate(index, *, condition="baseline", snapshot=None, candidate_class=None, rank_band=None):
    snapshot = snapshot or ("before" if index % 2 == 0 else "after")
    candidate_class = candidate_class or ("top_k" if index % 3 else "unresolved")
    rank_band = rank_band or ("1_2" if index % 2 else "3_5")
    return {
        "schema_version": "retrieval_arena.review_candidate.v1",
        "query_id": f"q{index}",
        "support_target_id": f"t{index}",
        "snapshot_id": snapshot,
        "candidate_class": candidate_class,
        "rank_band": rank_band,
        "query_text": f"question {index}",
        "requirement_texts": [f"requirement {index}"],
        "candidate_evidence": f"evidence {index}",
        "bounded_context": f"context {index}",
        "condition_label": condition,
        "system_label": "system-a",
        "outcome_label": "unknown",
        "rank": index + 1,
        "delta": float(index),
        "lineage_class": "novel_support_unjudged",
        "candidate_provenance_sha256": sha(f"candidate-{index}-{condition}"),
    }


def completed_judgments(packet, policy, *, classification=None):
    records = []
    for index, assessment in enumerate(packet["assessor_view_records"]):
        label = "relevant" if index % 2 == 0 else "nonrelevant"
        value = {
            "assessment_id": assessment["assessment_id"],
            "pair_id": assessment["pair_id"],
            "assessment_kind": assessment["assessment_kind"],
            "assessor_id": "a1" if assessment["assessment_kind"] == "primary" else "a2",
            "label": label,
            "relevance": 1 if label == "relevant" else 0,
            "answer_bearing_relevance": 1.0 if label == "relevant" else 0.0,
            "semantic_answer_quality": 0.75,
            "attribution_correctness": 0.5,
            "paraphrase_robustness": 0.8,
            "adjudicates_assessment_ids": [],
            "record_sha256": "0" * 64,
        }
        value["record_sha256"] = self_hash(value, "record_sha256")
        records.append(value)
    by_pair = {}
    for row in records:
        by_pair.setdefault(row["pair_id"], {})[row["assessment_kind"]] = row
    for pair_id in packet["duplicate_pair_ids"]:
        pair = by_pair[pair_id]
        if pair["primary"]["label"] == pair["duplicate"]["label"]:
            continue
        value = {
            "assessment_id": sha(f"adjudication-{pair_id}"),
            "pair_id": pair_id,
            "assessment_kind": "adjudication",
            "assessor_id": "adjudicator",
            "label": pair["primary"]["label"],
            "relevance": pair["primary"]["relevance"],
            "answer_bearing_relevance": pair["primary"]["answer_bearing_relevance"],
            "semantic_answer_quality": pair["primary"]["semantic_answer_quality"],
            "attribution_correctness": pair["primary"]["attribution_correctness"],
            "paraphrase_robustness": pair["primary"]["paraphrase_robustness"],
            "adjudicates_assessment_ids": sorted([pair["primary"]["assessment_id"], pair["duplicate"]["assessment_id"]]),
            "record_sha256": "0" * 64,
        }
        value["record_sha256"] = self_hash(value, "record_sha256")
        records.append(value)
    assessor_ids = ["a1", "a2"] + (["adjudicator"] if any(row["assessment_kind"] == "adjudication" for row in records) else [])
    artifact = {
        "schema_version": "retrieval_arena.judgment_artifact.v1",
        "artifact_id": "judgments-1",
        "packet_id": packet["packet_id"],
        "packet_sha256": packet["packet_sha256"],
        "candidate_pool_sha256": packet["candidate_pool_sha256"],
        "review_policy_sha256": policy["policy_sha256"],
        "dataset_id": packet["dataset_id"],
        "query_set_id": packet["query_set_id"],
        "ordered_snapshot_ids": packet["ordered_snapshot_ids"],
        "classification": classification or packet["classification"],
        "assessor_classes": policy["assessor_classes"],
        "assessor_ids": assessor_ids,
        "outcome_revealed_at_import": policy["outcome_revealed"],
        "records": records,
        "artifact_sha256": "0" * 64,
    }
    artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
    return artifact


class ReviewPacketTests(unittest.TestCase):
    def test_ordering_deduplication_blinding_and_self_hash(self):
        rows = [candidate(index) for index in range(8)]
        duplicate = copy.deepcopy(rows[0])
        duplicate["condition_label"] = "treatment"
        duplicate["system_label"] = "system-b"
        duplicate["candidate_provenance_sha256"] = sha("duplicate")
        packet = build_review_packet(list(reversed(rows + [duplicate])), review_policy(ceiling=8))
        self.assertEqual(packet["frame_count"], 8)
        self.assertEqual(packet["primary_count"], 8)
        self.assertEqual(packet["duplicate_count"], 1)
        self.assertEqual(packet["maximum_action_count"], 10)
        validate_review_packet(packet)
        forbidden = {"condition_labels", "system_labels", "outcome_labels", "ranks", "deltas", "lineage_classes"}
        self.assertFalse(any(set(row) & forbidden for row in packet["assessor_view_records"]))
        self.assertFalse(any(set(row) & forbidden for row in packet["frame_inventory"]))

    def test_packet_frame_allocation_and_inclusion_probability_closed_world_mutations(self):
        policy = review_policy(ceiling=6)
        packet = build_review_packet([candidate(index) for index in range(12)], policy)

        def rehash(value, *, inventory_changed=False):
            if inventory_changed:
                value["frame_inventory_sha256"] = sha256_json(value["frame_inventory"])
                selected = set(value["selected_pair_ids"])
                value["selected_frame_sha256"] = sha256_json(
                    [row for row in value["frame_inventory"] if row["pair_id"] in selected]
                )
            value["packet_sha256"] = self_hash(value, "packet_sha256")
            return value

        cases = []
        bad = copy.deepcopy(packet); bad["frame_count"] = "12"; cases.append((rehash(bad), "frame_count"))
        bad = copy.deepcopy(packet); bad["frame_count"] = 0; cases.append((rehash(bad), "frame_count"))
        bad = copy.deepcopy(packet); bad["frame_count"] -= 1; cases.append((rehash(bad), "frame_count"))
        bad = copy.deepcopy(packet); del bad["inclusion_probabilities"][next(iter(bad["inclusion_probabilities"]))]; cases.append((rehash(bad), "exactly cover"))
        bad = copy.deepcopy(packet); bad["inclusion_probabilities"][next(iter(bad["inclusion_probabilities"]))] = 0.123; cases.append((rehash(bad), "mismatches its frozen stratum"))
        bad = copy.deepcopy(packet); bad["inclusion_probabilities"][sha("extra-pair")] = 0.5; cases.append((rehash(bad), "exactly cover"))
        bad = copy.deepcopy(packet); bad["selected_pair_ids"][0] = sha("outside-frame"); bad["selected_pair_ids"].sort(); cases.append((rehash(bad), "drawn from the frozen frame"))
        bad = copy.deepcopy(packet); removable = next(item for item in bad["selected_pair_ids"] if item not in bad["duplicate_pair_ids"]); bad["selected_pair_ids"].remove(removable); cases.append((rehash(bad), "primary sample count"))
        bad = copy.deepcopy(packet); bad["duplicate_pair_ids"].append(bad["selected_pair_ids"][0]); bad["duplicate_pair_ids"].sort(); cases.append((rehash(bad), "duplicate sample count"))
        bad = copy.deepcopy(packet); bad["maximum_action_count"] += 1; cases.append((rehash(bad), "action arithmetic"))
        bad = copy.deepcopy(packet); bad["stratum_allocations"][0]["frame_count"] += 1; bad["stratum_allocations"][0]["inclusion_probability"] = bad["stratum_allocations"][0]["sample_count"] / bad["stratum_allocations"][0]["frame_count"]; cases.append((rehash(bad), "stratum frame_count"))
        bad = copy.deepcopy(packet); bad["frame_inventory"][0]["candidate_class"] = "changed-stratum"; cases.append((rehash(bad, inventory_changed=True), "allocations do not exactly cover"))
        for mutated, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValidationError, message):
                validate_review_packet(mutated)

    def test_byte_stable_packet_from_identical_inputs(self):
        rows = [candidate(index) for index in range(30)]
        one = build_review_packet(rows, review_policy(ceiling=12))
        two = build_review_packet(copy.deepcopy(rows), copy.deepcopy(review_policy(ceiling=12)))
        self.assertEqual(one, two)

    def test_largest_remainder_stratification_minimum_one_and_fixed_stop(self):
        rows = [candidate(index) for index in range(40)]
        packet = build_review_packet(rows, review_policy(ceiling=12))
        self.assertEqual(sum(row["sample_count"] for row in packet["stratum_allocations"]), 12)
        self.assertTrue(all(row["sample_count"] >= 1 for row in packet["stratum_allocations"]))
        self.assertTrue(all(0 < row["inclusion_probability"] <= 1 for row in packet["stratum_allocations"]))

    def test_240_ceiling_10_percent_and_288_arithmetic(self):
        rows = [candidate(index) for index in range(300)]
        packet = build_review_packet(rows, review_policy())
        self.assertEqual(packet["primary_count"], 240)
        self.assertEqual(packet["duplicate_count"], 24)
        self.assertEqual(packet["maximum_adjudication_count"], 24)
        self.assertEqual(packet["maximum_action_count"], 288)
        self.assertEqual(packet["governance_maximum_action_count_at_ceiling"], 288)
        bad = review_policy(); bad["primary_judgment_ceiling"] = 241; bad["policy_sha256"] = self_hash(bad, "policy_sha256")
        with self.assertRaisesRegex(ValidationError, "maximum 240"):
            build_review_packet(rows, bad)

    def test_confirmatory_and_post_reveal_exploratory_classification(self):
        self.assertEqual(build_review_packet([candidate(0)], review_policy(ceiling=1))["classification"], "confirmatory")
        self.assertEqual(build_review_packet([candidate(0)], review_policy(ceiling=1, revealed=True))["classification"], "exploratory")

    def test_judgment_import_agreement_and_separate_families(self):
        policy = review_policy(ceiling=10)
        packet = build_review_packet([candidate(index) for index in range(10)], policy)
        imported = import_judgments(completed_judgments(packet, policy), packet, policy)
        self.assertEqual(imported["primary_count"], 10)
        self.assertEqual(imported["duplicate_count"], 1)
        self.assertEqual(imported["agreement"]["agreement_pair_count"], 1)
        self.assertEqual(imported["agreement"]["interpretation"], "assessor_agreement_not_retrieval_effectiveness")
        self.assertIn("semantic_answer_quality", imported["calibrated_family_means"])

    def test_wrong_packet_pool_policy_snapshot_and_schema_reject(self):
        policy = review_policy(ceiling=3)
        packet = build_review_packet([candidate(index) for index in range(3)], policy)
        base = completed_judgments(packet, policy)
        cases = (
            ("packet_sha256", sha("wrong"), "packet_sha256"),
            ("candidate_pool_sha256", sha("wrong"), "candidate_pool_sha256"),
            ("review_policy_sha256", sha("wrong"), "review_policy_sha256"),
            ("ordered_snapshot_ids", ["wrong"], "ordered_snapshot_ids"),
            ("schema_version", "retrieval_arena.judgment_artifact.v0", "schema"),
        )
        for name, value, message in cases:
            artifact = copy.deepcopy(base)
            artifact[name] = value
            artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError, message):
                import_judgments(artifact, packet, policy)

    def test_wrong_assessor_and_incomplete_judgments_reject(self):
        policy = review_policy(ceiling=3)
        packet = build_review_packet([candidate(index) for index in range(3)], policy)
        artifact = completed_judgments(packet, policy)
        artifact["assessor_ids"] = ["unknown"]
        artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
        with self.assertRaisesRegex(ValidationError, "unknown assessor"):
            import_judgments(artifact, packet, policy)
        artifact = completed_judgments(packet, policy)
        artifact["records"] = artifact["records"][:-1]
        artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
        with self.assertRaisesRegex(ValidationError, "incomplete|adjudication|inventory"):
            import_judgments(artifact, packet, policy)

    def test_every_duplicate_disagreement_requires_blind_adjudication(self):
        policy = review_policy(ceiling=10)
        packet = build_review_packet([candidate(index) for index in range(10)], policy)
        artifact = completed_judgments(packet, policy)
        artifact["records"] = [row for row in artifact["records"] if row["assessment_kind"] != "adjudication"]
        pair_id = packet["duplicate_pair_ids"][0]
        duplicate = next(row for row in artifact["records"] if row["pair_id"] == pair_id and row["assessment_kind"] == "duplicate")
        primary = next(row for row in artifact["records"] if row["pair_id"] == pair_id and row["assessment_kind"] == "primary")
        duplicate["label"] = "nonrelevant" if primary["label"] == "relevant" else "relevant"
        duplicate["relevance"] = 0 if duplicate["label"] == "nonrelevant" else 1
        duplicate["record_sha256"] = self_hash(duplicate, "record_sha256")
        artifact["assessor_ids"] = ["a1", "a2"]
        artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
        with self.assertRaisesRegex(ValidationError, "requires exactly one blind adjudication"):
            import_judgments(artifact, packet, policy)

    def test_judgment_artifact_and_record_hash_mismatch_reject(self):
        policy = review_policy(ceiling=2)
        packet = build_review_packet([candidate(index) for index in range(2)], policy)
        artifact = completed_judgments(packet, policy)
        artifact["artifact_sha256"] = sha("wrong")
        with self.assertRaisesRegex(ValidationError, "artifact self-hash"):
            import_judgments(artifact, packet, policy)
        artifact = completed_judgments(packet, policy)
        artifact["records"][0]["record_sha256"] = sha("wrong")
        artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
        with self.assertRaisesRegex(ValidationError, "record self-hash"):
            import_judgments(artifact, packet, policy)

    def test_condition_label_leakage_reject(self):
        policy = review_policy(ceiling=1)
        packet = build_review_packet([candidate(0)], policy)
        packet["assessor_view_records"][0]["condition_labels"] = ["baseline"]
        packet["assessor_view_sha256"] = hashlib.sha256(b"irrelevant").hexdigest()
        packet["packet_sha256"] = self_hash(packet, "packet_sha256")
        with self.assertRaisesRegex(ValidationError, "condition-label leakage"):
            validate_review_packet(packet)

    def test_wrong_pair_and_post_reveal_confirmatory_reject(self):
        policy = review_policy(ceiling=2)
        packet = build_review_packet([candidate(index) for index in range(2)], policy)
        artifact = completed_judgments(packet, policy)
        artifact["records"][0]["pair_id"] = sha("wrong")
        artifact["records"][0]["record_sha256"] = self_hash(artifact["records"][0], "record_sha256")
        artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
        with self.assertRaisesRegex(ValidationError, "identity mismatch"):
            import_judgments(artifact, packet, policy)
        policy = review_policy(ceiling=2, revealed=True)
        packet = build_review_packet([candidate(index) for index in range(2)], policy)
        artifact = completed_judgments(packet, policy, classification="confirmatory")
        with self.assertRaisesRegex(ValidationError, "post-reveal"):
            import_judgments(artifact, packet, policy)

    def test_label_relevance_equivalence_for_primary_duplicate_and_adjudication(self):
        policy = review_policy(ceiling=10)
        packet = build_review_packet([candidate(index) for index in range(10)], policy)
        artifact = completed_judgments(packet, policy)
        by_kind = {row["assessment_kind"]: row for row in artifact["records"]}
        self.assertEqual(set(by_kind), {"primary", "duplicate", "adjudication"})
        for kind in ("primary", "duplicate", "adjudication"):
            for label, relevance in (("nonrelevant", 1), ("relevant", 0)):
                mutated = copy.deepcopy(artifact)
                row = next(item for item in mutated["records"] if item["assessment_kind"] == kind)
                row["label"] = label
                row["relevance"] = relevance
                row["record_sha256"] = self_hash(row, "record_sha256")
                mutated["artifact_sha256"] = self_hash(mutated, "artifact_sha256")
                with self.subTest(kind=kind, label=label), self.assertRaisesRegex(ValidationError, "label/relevance contradiction"):
                    import_judgments(mutated, packet, policy)

    def test_outcome_revealed_at_import_requires_boolean(self):
        policy = review_policy(ceiling=2)
        packet = build_review_packet([candidate(index) for index in range(2)], policy)
        artifact = completed_judgments(packet, policy)
        artifact["outcome_revealed_at_import"] = 0
        artifact["artifact_sha256"] = self_hash(artifact, "artifact_sha256")
        with self.assertRaisesRegex(ValidationError, "must be boolean"):
            import_judgments(artifact, packet, policy)


if __name__ == "__main__":
    unittest.main()
