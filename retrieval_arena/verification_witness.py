"""Runtime-derived, self-hashed focused-verification witness.

The focused entrypoint calls ``collect_focused_witness`` after its registered
suite is green.  Expected rows are only a closed contract for validating live
observations; no observed value is copied from them.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any, Callable

from .dual_profile import (PROFILE_AUTOMATIC, compose_destination_qrels,
                           self_hash, sha256_artifact, sha256_json,
                           run_bertscore_offline,
                           validate_bertscore_provider,
                           validate_bertscore_scores,
                           validate_destination_qrel_inventory,
                           validate_profile_policy)
from .review_packets import build_review_packet, import_judgments
from .profile_evaluation import evaluate_profile_files

SCHEMA = "retrieval_arena.focused_verification_witness.v1"
ROOT = Path(__file__).resolve().parents[1]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scratch_root() -> Path:
    """Admit the focused adapter's one writable scratch mount."""
    declared = ROOT / ".tmp_unit_tests"
    value = os.environ.get("TMPDIR", "")
    actual = Path(value) if value else Path()
    if not value or not actual.is_absolute() or actual != declared or not actual.is_dir() or actual.is_symlink():
        raise RuntimeError("focused-witness-scratch-not-admitted")
    return actual


# Capstone-owned lifecycle cases are intentionally external, rather than
# falsely described as an RA observation.
NEGATIVE_CASES: tuple[dict[str, str | None], ...] = (
    {"case_id": "wrong_packet", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_packet_pool_policy_snapshot_and_schema_reject", "expected_class": "ValidationError", "expected_reason_code": "review-packet-hash-mismatch", "owner": "retrieval_arena"},
    {"case_id": "wrong_pool", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_packet_pool_policy_snapshot_and_schema_reject", "expected_class": "ValidationError", "expected_reason_code": "candidate-pool-hash-mismatch", "owner": "retrieval_arena"},
    {"case_id": "wrong_policy", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_packet_pool_policy_snapshot_and_schema_reject", "expected_class": "ValidationError", "expected_reason_code": "review-policy-hash-mismatch", "owner": "retrieval_arena"},
    {"case_id": "wrong_snapshot", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_packet_pool_policy_snapshot_and_schema_reject", "expected_class": "ValidationError", "expected_reason_code": "snapshot-binding-mismatch", "owner": "retrieval_arena"},
    {"case_id": "wrong_assessor", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_assessor_and_incomplete_judgments_reject", "expected_class": "ValidationError", "expected_reason_code": "assessor-class-mismatch", "owner": "retrieval_arena"},
    {"case_id": "wrong_judgment_hash", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_judgment_artifact_and_record_hash_mismatch_reject", "expected_class": "ValidationError", "expected_reason_code": "judgment-artifact-hash-mismatch", "owner": "retrieval_arena"},
    {"case_id": "mixed_profile_family", "test_id": "unit_tests.test_dual_profile.DualProfileTests.test_profile_mixing_and_unknown_fields_reject", "expected_class": "ValidationError", "expected_reason_code": "profile-family-mismatch", "owner": "retrieval_arena"},
    {"case_id": "modified_qrel_ledger", "test_id": "unit_tests.test_profile_evaluation.ProfileEvaluationTests.test_complete_source_derives_pooled_destination_with_disposition_audit", "expected_class": "ValidationError", "expected_reason_code": "destination-qrel-inventory-exact-recomposition-mismatch", "owner": "retrieval_arena"},
    {"case_id": "invented_judgment", "test_id": "unit_tests.test_review_packets.ReviewPacketTests.test_wrong_pair_and_post_reveal_confirmatory_reject", "expected_class": "ValidationError", "expected_reason_code": "judgment-identity-mismatch", "owner": "retrieval_arena"},
    {"case_id": "post_receipt_mutation", "test_id": None, "expected_class": "RetrievalArenaDockerError", "expected_reason_code": "ra-docker-receipt-output-inventory-mismatch", "owner": "capstone"},
    {"case_id": "authority_hash_splice", "test_id": None, "expected_class": "LauncherError", "expected_reason_code": "ra-docker-launch-authority-changed", "owner": "capstone"},
    {"case_id": "unregistered_provider", "test_id": "unit_tests.test_profile_evaluation.ProfileEvaluationTests.test_source_snapshot_binding_and_scores_without_provider_fail_closed", "expected_class": "ValidationError", "expected_reason_code": "bertscore-requires-registered-provider", "owner": "retrieval_arena"},
    {"case_id": "output_path_escape", "test_id": "unit_tests.test_profile_operator_admission.ProfileOperatorAdmissionTests.test_traversal_and_nonempty_output_fail_closed", "expected_class": "AdmissionError", "expected_reason_code": "operator-path-not-admitted", "owner": "retrieval_arena"},
    {"case_id": "nonfresh_output", "test_id": "unit_tests.test_profile_operator_admission.ProfileOperatorAdmissionTests.test_traversal_and_nonempty_output_fail_closed", "expected_class": "AdmissionError", "expected_reason_code": "operator-output-root-not-fresh", "owner": "retrieval_arena"},
    {"case_id": "partial_publication", "test_id": "unit_tests.test_profile_operator_admission.ProfileOperatorAdmissionTests.test_unowned_output_never_receives_a_receipt", "expected_class": "operator_return", "expected_reason_code": "unowned-output-no-receipt", "owner": "retrieval_arena"},
)


def _policy() -> dict[str, Any]:
    value = {"schema_version": "retrieval_arena.review_policy.v1", "packet_id": "focused-witness", "dataset_id": "focused", "query_set_id": "focused-q", "ordered_snapshot_ids": ["before", "after"], "seed": 1729, "primary_judgment_ceiling": 3, "duplicate_rate": .10, "sampling_method": "stratified_simple_random_without_replacement", "stratification_fields": ["snapshot_id", "candidate_class", "rank_band"], "allocation_method": "proportional_largest_remainder", "minimum_one_when_feasible": True, "stopping_rule": "frame_exhausted_or_fixed_quotas_complete", "adjudication_policy": "blind_all_duplicate_disagreements_s24_precedent", "assessor_classes": ["domain_expert"], "allowed_assessor_ids": ["a1", "a2", "adjudicator"], "guidelines_sha256": _sha_text("guidelines"), "outcome_revealed": False, "policy_sha256": "0" * 64}
    value["policy_sha256"] = self_hash(value, "policy_sha256")
    return value


def _candidate(index: int) -> dict[str, Any]:
    return {"schema_version": "retrieval_arena.review_candidate.v1", "query_id": f"q{index}", "support_target_id": f"t{index}", "snapshot_id": "before" if index % 2 == 0 else "after", "candidate_class": "top_k", "rank_band": "1_2", "query_text": f"question {index}", "requirement_texts": [f"requirement {index}"], "candidate_evidence": f"evidence {index}", "bounded_context": f"context {index}", "condition_label": "baseline", "system_label": "system-a", "outcome_label": "unknown", "rank": index + 1, "delta": float(index), "lineage_class": "novel_support_unjudged", "candidate_provenance_sha256": _sha_text(f"candidate-{index}")}


def _judgments(packet: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for index, assessment in enumerate(packet["assessor_view_records"]):
        label = "relevant" if index % 2 == 0 else "nonrelevant"
        row = {"assessment_id": assessment["assessment_id"], "pair_id": assessment["pair_id"], "assessment_kind": assessment["assessment_kind"], "assessor_id": "a1" if assessment["assessment_kind"] == "primary" else "a2", "label": label, "relevance": int(label == "relevant"), "answer_bearing_relevance": float(label == "relevant"), "semantic_answer_quality": .75, "attribution_correctness": .5, "paraphrase_robustness": .8, "adjudicates_assessment_ids": [], "record_sha256": "0" * 64}
        row["record_sha256"] = self_hash(row, "record_sha256"); rows.append(row)
    value = {"schema_version": "retrieval_arena.judgment_artifact.v1", "artifact_id": "focused-witness", "packet_id": packet["packet_id"], "packet_sha256": packet["packet_sha256"], "candidate_pool_sha256": packet["candidate_pool_sha256"], "review_policy_sha256": policy["policy_sha256"], "dataset_id": packet["dataset_id"], "query_set_id": packet["query_set_id"], "ordered_snapshot_ids": packet["ordered_snapshot_ids"], "classification": packet["classification"], "assessor_classes": policy["assessor_classes"], "assessor_ids": ["a1", "a2"], "outcome_revealed_at_import": False, "records": rows, "artifact_sha256": "0" * 64}
    value["artifact_sha256"] = self_hash(value, "artifact_sha256"); return value


def _observe(case_id: str, code: str, token: str, action: Callable[[], Any]) -> dict[str, Any]:
    try:
        action()
    except Exception as error:
        message = str(error)
        if token not in message:
            raise RuntimeError(f"focused-witness-unclassified:{case_id}:{type(error).__name__}") from error
        return {"case_id": case_id, "observed_class": type(error).__name__, "observed_reason_code": code, "disposition": "rejected_before_publication", "exception_message_sha256": _sha_text(message)}
    raise RuntimeError(f"focused-witness-mutation-accepted:{case_id}")


def _profile_policy() -> dict[str, Any]:
    value = {"schema_version": "retrieval_arena.evaluation_profile_policy.v1", "policy_id": "focused", "evaluation_profile": PROFILE_AUTOMATIC, "classification": "confirmatory", "context_item_budget_k": 1, "requirements_sha256": _sha_text("requirements"), "bindings_sha256": _sha_text("bindings"), "lineage_policy_id": "exact-v1", "lineage_sha256": _sha_text("lineage"), "calibration_requirement": "optional", "standard_metric_family": ["evidence_recall", "ndcg", "reciprocal_rank"], "project_diagnostic_family": ["complete_support", "requirement_coverage", "all_requirements", "support_lineage"], "automatic_answer_family": ["lexical", "bertscore_precision", "bertscore_recall", "bertscore_f1"], "calibrated_relevance_family": ["bpref", "answer_bearing_relevance"], "calibrated_answer_family": ["semantic_answer_quality", "paraphrase_robustness"], "calibrated_attribution_family": ["attribution_correctness"], "composite_metric": None, "runtime_network": "forbidden", "interactive_human_work": "forbidden", "policy_sha256": "0" * 64}
    value["policy_sha256"] = self_hash(value, "policy_sha256"); return value


def _qrel_ledger_mutation() -> None:
    row = {"query_id": "q1", "evidence_requirement_id": "r1", "source_snapshot_id": "before", "destination_snapshot_id": "after", "source_support_target_id": "a", "destination_support_target_id": "a", "source_evidence_unit_id": "u", "destination_evidence_unit_id": "u", "source_normalized_evidence_sha256": _sha_text("e"), "destination_normalized_evidence_sha256": _sha_text("e"), "source_bounded_context_sha256": _sha_text("c"), "destination_bounded_context_sha256": _sha_text("c"), "source_structural_owner": "s", "destination_structural_owner": "s", "repository_provenance_sha256": _sha_text("r"), "source_document_provenance_sha256": _sha_text("d"), "destination_document_provenance_sha256": _sha_text("d"), "repository_document_lineage_exact": True, "transfer_class": "identity_unchanged", "mapping_shape": "one_to_one", "decomposed_exact_edge": False, "ambiguous": False, "contradictory_destination": False, "conflicting": False, "record_sha256": "0" * 64}
    row["record_sha256"] = self_hash(row, "record_sha256")
    lineage = {"schema_version": "retrieval_arena.support_lineage.v1", "policy_id": "exact-v1", "source_snapshot_id": "before", "destination_snapshot_id": "after", "records": [row], "lineage_sha256": "0" * 64}; lineage["lineage_sha256"] = self_hash(lineage, "lineage_sha256")
    source = [{"query_id": "q1", "support_target_id": "a", "relevance": 1, "judgment_id": "j", "judgment_provenance_sha256": _sha_text("j")}]; predictions = [{"query_id": "q1", "items": [{"rank": 1, "support_target_id": "a"}]}]
    kwargs = {"source_qrels_scope": "complete", "ranked_predictions": predictions, "cutoff": 1, "ranked_dataset_sha256": _sha_text("dataset"), "ranked_policy_sha256": _sha_text("policy")}
    ledger = compose_destination_qrels(source, lineage, "before", "after", ["q1"], "binary", **kwargs); ledger["qrels"] = []
    validate_destination_qrel_inventory(ledger, source, lineage, "before", "after", ["q1"], "binary", **kwargs)


def _load_operator() -> Any:
    spec = importlib.util.spec_from_file_location("focused_witness_operator", ROOT / "containers" / "verification" / "evaluate_profile.py")
    if spec is None or spec.loader is None: raise RuntimeError("focused-witness-operator-load-failed")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def _operator_observations() -> list[dict[str, Any]]:
    operator = _load_operator(); scratch = _scratch_root()
    with tempfile.TemporaryDirectory(dir=scratch) as root_text:
        root = Path(root_text); input_root = root / "input"; output = root / "output"; input_root.mkdir(); output.mkdir()
        prior_input, prior_output = operator.INPUT, operator.OUTPUT; operator.INPUT, operator.OUTPUT = input_root, output
        try:
            request = {"schema_version": operator.REQUEST_SCHEMA, "command": "evaluate-profile", "config_path": "../escape.json", "input_inventory": [], "input_inventory_sha256": operator._sha(operator._canonical([]))}; (input_root / operator.REQUEST).write_text(json.dumps(request), encoding="utf-8"); (output / operator.OWNER).write_text("retrieval_arena.output_root.v1\n", encoding="utf-8")
            path = _observe("output_path_escape", "operator-path-not-admitted", "operator-path-not-admitted", operator._read_request)
            (output / "unexpected").write_text("x", encoding="utf-8")
            fresh = _observe("nonfresh_output", "operator-output-root-not-fresh", "operator-output-root-not-fresh", operator._admit_output)
        finally: operator.INPUT, operator.OUTPUT = prior_input, prior_output
    with tempfile.TemporaryDirectory(dir=scratch) as root_text:
        root = Path(root_text); output = root / "output"; output.mkdir(); prior_input, prior_output = operator.INPUT, operator.OUTPUT; operator.INPUT, operator.OUTPUT = root / "missing", output
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()): result = operator.main()
        finally: operator.INPUT, operator.OUTPUT = prior_input, prior_output
        receipt = output / "operator_receipt.v1.json"; present = list(output.iterdir())
        if result != 2 or receipt.exists() or present: raise RuntimeError("focused-witness-partial-publication-contract-failed")
        partial = {"case_id": "partial_publication", "observed_class": "operator_return", "observed_reason_code": "unowned-output-no-receipt", "disposition": "rejected_no_receipt_no_partial_output", "return_code": result, "receipt_present": False, "partial_output_present": False}
    return [path, fresh, partial]


def _provider(root: Path) -> dict[str, Any]:
    package_root = root / "registered-distribution"; package = package_root / "bert_score"; metadata = package_root / "bert_score-0.3.13.dist-info"; package.mkdir(parents=True); metadata.mkdir()
    (package / "__init__.py").write_text("import os\ndef score(candidates, references, **kwargs):\n    if os.environ.get('HF_HUB_OFFLINE') != '1' or os.environ.get('TRANSFORMERS_OFFLINE') != '1': raise RuntimeError('offline guards missing')\n    values = {'restart service': 1.0, 'do not restart service': .92, 'reboot daemon': .8, 'paint wall': .1}\n    return [values[x] for x in candidates], [values[x] for x in candidates], [values[x] for x in candidates]\n", encoding="utf-8", newline="\n")
    (metadata / "METADATA").write_text("Name: bert-score\nVersion: 0.3.13\n", encoding="utf-8", newline="\n")
    checkpoint = root / "checkpoint"; checkpoint.mkdir(); (checkpoint / "frozen.bin").write_bytes(b"frozen-model-tokenizer")
    value = {"schema_version": "retrieval_arena.bertscore_provider.v1", "provider_id": "bert-tiny-offline", "package_name": "bert-score", "package_version": "0.3.13", "package_artifact_path": package_root.name, "package_artifact_sha256": sha256_artifact(package_root), "checkpoint_id": "focused", "checkpoint_revision": "r1", "model_artifact_path": checkpoint.name, "model_artifact_sha256": sha256_artifact(checkpoint), "tokenizer_id": "focused", "tokenizer_revision": "r1", "tokenizer_artifact_path": checkpoint.name, "tokenizer_artifact_sha256": sha256_artifact(checkpoint), "baseline_policy": "none", "baseline_artifact_path": None, "baseline_artifact_sha256": None, "rescaling_policy": "none", "language": "en", "device": "cpu", "dtype": "float32", "runtime_download": False, "provider_sha256": "0" * 64}
    value["provider_sha256"] = self_hash(value, "provider_sha256"); return value


def _bertscore_observation() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(dir=_scratch_root()) as root_text:
        root = Path(root_text); provider = _provider(root); validate_bertscore_provider(provider, root)
        pairs = [{"query_id": "exact_match", "generated_answer": "restart service", "reference_answer": "restart service"}, {"query_id": "paraphrase", "generated_answer": "reboot daemon", "reference_answer": "restart service"}, {"query_id": "unrelated", "generated_answer": "paint wall", "reference_answer": "restart service"}, {"query_id": "contradiction", "generated_answer": "do not restart service", "reference_answer": "restart service"}]
        artifact = run_bertscore_offline(provider, root, pairs); replay_artifact = run_bertscore_offline(provider, root, pairs); actual = validate_bertscore_scores(artifact, provider, sha256_json(pairs)); replay = validate_bertscore_scores(replay_artifact, provider, sha256_json(pairs))
        checks = []
        for state, mutation, token in (("registered_provider_artifact_missing", lambda: ("package_artifact_path", "missing"), "artifact missing"), ("runtime_download_rejected", lambda: ("runtime_download", True), "runtime download")):
            bad = copy.deepcopy(provider); key, value = mutation(); bad[key] = value; bad["provider_sha256"] = self_hash(bad, "provider_sha256")
            observation = _observe(state, state, token, lambda bad=bad: validate_bertscore_provider(bad, root)); observation["state"] = observation.pop("case_id"); checks.append(observation)
        implementation = root / "registered-distribution" / "bert_score" / "__init__.py"
        implementation.write_text(implementation.read_text(encoding="utf-8") + "# same-version substituted code\n", encoding="utf-8", newline="\n")
        observation = _observe("same_version_substitution_rejected", "same_version_substitution_rejected", "artifact hash mismatch:package", lambda: run_bertscore_offline(provider, root, pairs)); observation["state"] = observation.pop("case_id"); checks.append(observation)
    if not actual["exact_match"]["f1"] > actual["contradiction"]["f1"] > actual["paraphrase"]["f1"] > actual["unrelated"]["f1"]: raise RuntimeError("focused-witness-bertscore-relations-failed")
    return {"provider_identity": {key: provider[key] for key in ("provider_id", "package_name", "package_version", "runtime_download", "provider_sha256", "package_artifact_sha256", "model_artifact_sha256", "tokenizer_artifact_sha256")}, "cases": [{"case_id": key, "f1": actual[key]["f1"]} for key in ("exact_match", "paraphrase", "unrelated", "contradiction")], "relations": ["exact_match_f1_gt_contradiction_f1", "contradiction_f1_gt_paraphrase_f1", "paraphrase_f1_gt_unrelated_f1"], "unavailable": checks, "no_runtime_download": True, "no_substitution": True, "byte_stable_replay": canonical(artifact) == canonical(replay_artifact) and canonical(actual) == canonical(replay)}


def _collect_focused_witness() -> dict[str, Any]:
    """Execute every RA-owned public mutation exactly once."""
    policy = _policy(); packet = build_review_packet([_candidate(i) for i in range(3)], policy); artifact = _judgments(packet, policy)
    cases: list[tuple[str, str, str, Callable[[], Any]]] = []
    for case_id, field, reason in (("wrong_packet", "packet_sha256", "review-packet-hash-mismatch"), ("wrong_pool", "candidate_pool_sha256", "candidate-pool-hash-mismatch"), ("wrong_policy", "review_policy_sha256", "review-policy-hash-mismatch"), ("wrong_snapshot", "ordered_snapshot_ids", "snapshot-binding-mismatch")):
        def action(field: str = field) -> Any:
            bad = copy.deepcopy(artifact); bad[field] = ["wrong"] if field == "ordered_snapshot_ids" else _sha_text("wrong"); bad["artifact_sha256"] = self_hash(bad, "artifact_sha256"); return import_judgments(bad, packet, policy)
        cases.append((case_id, reason, field, action))
    def assessor() -> Any:
        bad = copy.deepcopy(artifact); bad["assessor_ids"] = ["unknown"]; bad["artifact_sha256"] = self_hash(bad, "artifact_sha256"); return import_judgments(bad, packet, policy)
    def hash_mutation() -> Any:
        bad = copy.deepcopy(artifact); bad["artifact_sha256"] = _sha_text("wrong"); return import_judgments(bad, packet, policy)
    def invented() -> Any:
        bad = copy.deepcopy(artifact); bad["records"][0]["pair_id"] = _sha_text("wrong"); bad["records"][0]["record_sha256"] = self_hash(bad["records"][0], "record_sha256"); bad["artifact_sha256"] = self_hash(bad, "artifact_sha256"); return import_judgments(bad, packet, policy)
    def mixed() -> Any:
        bad = _profile_policy(); bad["calibration_requirement"] = "required"; bad["policy_sha256"] = self_hash(bad, "policy_sha256"); return validate_profile_policy(bad)
    cases += [("wrong_assessor", "assessor-class-mismatch", "unknown assessor", assessor), ("wrong_judgment_hash", "judgment-artifact-hash-mismatch", "artifact self-hash", hash_mutation), ("mixed_profile_family", "profile-family-mismatch", "cannot require calibration", mixed), ("modified_qrel_ledger", "destination-qrel-inventory-exact-recomposition-mismatch", "exact recomposition mismatch", _qrel_ledger_mutation), ("invented_judgment", "judgment-identity-mismatch", "identity mismatch", invented)]
    observed = [_observe(case, reason, token, action) for case, reason, token, action in cases]
    with tempfile.TemporaryDirectory(dir=_scratch_root()) as root_text:
        root = Path(root_text)
        config = {"schema_version": "retrieval_arena.dual_profile_config.v1", "automatic_qrels_snapshot_id": "before", "profile_policy_path": "unreached", "ranked_dataset_path": "unreached", "automatic_qrels_path": "unreached", "ranked_predictions_path": "unreached", "ranked_metric_policy_path": "unreached", "requirements_path": "unreached", "bindings_path": "unreached", "lineage_path": "unreached", "requirement_predictions_path": "unreached", "answer_pairs_path": None, "bertscore_provider_path": None, "bertscore_scores_path": "scores.json", "review_policy_path": None, "review_packet_path": None, "judgment_artifact_path": None}
        path = root / "automatic-no-provider.json"; path.write_bytes(canonical(config))
        observed.append(_observe("unregistered_provider", "bertscore-requires-registered-provider", "requires a registered provider", lambda: evaluate_profile_files(path, root / "out")))
    observed += _operator_observations(); observed.sort(key=lambda row: row["case_id"])
    contract = {row["case_id"]: row for row in NEGATIVE_CASES if row["owner"] == "retrieval_arena"}
    if {row["case_id"] for row in observed} != set(contract): raise RuntimeError("focused-witness-ra-matrix-incomplete")
    for row in observed:
        expected = contract[row["case_id"]]
        if row["observed_class"] != expected["expected_class"] or row["observed_reason_code"] != expected["expected_reason_code"]: raise RuntimeError(f"focused-witness-contract-mismatch:{row['case_id']}")
    return {"observed_negative_cases": observed, "bertscore": _bertscore_observation(), "external_required_negative_cases": [row for row in NEGATIVE_CASES if row["owner"] == "capstone"]}


_RUNTIME_CACHE: bytes | None = None


def collect_focused_witness() -> dict[str, Any]:
    """Execute mutations once per focused process, return independent copies."""
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is None:
        _RUNTIME_CACHE = canonical(_collect_focused_witness())
    return json.loads(_RUNTIME_CACHE)


def build_focused_witness(*, source_commit: str, source_tree: str, image_reference: str, dependency_lock_sha256: str, focused_tests_run: int, focused_test_case_ids: list[str], runtime: dict[str, Any]) -> dict[str, Any]:
    if not all(isinstance(value, str) and value for value in (source_commit, source_tree, image_reference, dependency_lock_sha256)): raise ValueError("focused-witness-identity-missing")
    if isinstance(focused_tests_run, bool) or not isinstance(focused_tests_run, int) or focused_tests_run < 1: raise ValueError("focused-witness-test-count-invalid")
    if (not isinstance(focused_test_case_ids, list) or len(focused_test_case_ids) != focused_tests_run or len(focused_test_case_ids) != len(set(focused_test_case_ids)) or not all(isinstance(case_id, str) and case_id.startswith("unit_tests.") for case_id in focused_test_case_ids)): raise ValueError("focused-witness-case-inventory-invalid")
    value = {"schema_version": SCHEMA, "identity": {"source_commit": source_commit, "source_tree": source_tree, "image_reference": image_reference, "dependency_lock_sha256": dependency_lock_sha256}, "focused_test_modules": ["unit_tests.test_dual_profile", "unit_tests.test_review_packets", "unit_tests.test_profile_evaluation", "unit_tests.test_verification_container_contract", "unit_tests.test_profile_operator_admission", "unit_tests.test_verification_witness", "unit_tests.test_review_packet_operator"], "focused_suite_success": True, "focused_tests_run": focused_tests_run, "focused_test_case_ids": focused_test_case_ids, "expected_negative_cases": list(NEGATIVE_CASES), **runtime, "witness_sha256": "0" * 64}
    value["witness_sha256"] = sha256({key: item for key, item in value.items() if key != "witness_sha256"}); return value


def witness_from_environment(*, focused_tests_run: int, focused_test_case_ids: list[str]) -> dict[str, Any]:
    return build_focused_witness(source_commit=os.environ.get("RA_SOURCE_COMMIT", ""), source_tree=os.environ.get("RA_SOURCE_TREE", ""), image_reference=os.environ.get("RA_IMAGE_REFERENCE", ""), dependency_lock_sha256=os.environ.get("RA_DEPENDENCY_LOCK_SHA256", ""), focused_tests_run=focused_tests_run, focused_test_case_ids=focused_test_case_ids, runtime=collect_focused_witness())
