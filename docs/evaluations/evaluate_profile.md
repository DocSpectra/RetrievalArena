# `evaluate_profile`

Dual-profile ranked evaluation: `operational_automatic` (zero-human, from frozen qrels, ranked predictions, evidence requirements, snapshot bindings and exact lineage) and the optional artifact-only `scientific_calibration` (from a frozen review packet and immutable imported judgments). Requirement coverage and support lineage are project-specific diagnostics; BPref and human judgments are calibration-only and never enter an automatic result. No composite score exists.

**Input:** An `evaluation_profile_policy.v1` config naming the dataset, qrels, predictions, `evidence_requirements.v1`, `requirement_bindings.v1`, `requirement_prediction.v1` and `destination_qrel_inventory.v2` artifacts; through the operator a `retrieval_arena.profile_evaluation_request.v1` input root with an exact inventory.

**Output:** `automatic_result.v1` and, when calibration inputs exist, `calibration_result.v1`, each with a replay record (`automatic_replay.v1`, `calibration_replay.v1`); the operator adds `retrieval_arena.profile_evaluation_operator_receipt.v1`.

## Metric ids

Families: `standard_metric_family` (`evidence_recall`, `ndcg`, `reciprocal_rank`), `project_diagnostic_family` (`complete_support`, `requirement_coverage`, `all_requirements`, `support_lineage`), `automatic_answer_family` (`lexical`, `bertscore_*`), calibrated families (`bpref`, `answer_bearing_relevance`, `semantic_answer_quality`, `paraphrase_robustness`, `attribution_correctness`). Contract: [../dual_profile_evaluation.md](../dual_profile_evaluation.md).

**Invocation:** `python -m retrieval_arena.cli evaluate-profile --config <dual-profile.json> --out-dir <dir>` (`configs/dual_profile.example.json`); `scripts/run-verification.ps1 -Command evaluate-profile -InputRoot … -OutputRoot … -Receipt …`.

**Tests:** `unit_tests/test_dual_profile.py`, `test_profile_evaluation.py`, `test_profile_operator_admission.py`, `test_review_packets.py`, `test_review_packet_operator.py` — the `focused` suite.

**Source:** `retrieval_arena/dual_profile.py`, `retrieval_arena/profile_evaluation.py`, `retrieval_arena/review_packets.py`; `containers/verification/evaluate_profile.py`.
