# `score_bertscore`

Offline BERTScore over answer pairs with a provider-pinned model: the caller composes `(query_id, candidate answer, reference answer)` pairs and a provider registration that pins the `bert-score` package version, checkpoint id and revision, model artifact path and sha256, tokenizer, baseline and rescaling policy, with `runtime_download: false`. An automatic answer diagnostic, never a composite.

**Input:** `retrieval_arena.bertscore_provider.v2` (`--provider`), answer pairs JSONL (`--answer-pairs`); through the operator an input root with `retrieval_arena.bertscore_scoring_request.v1` and its `retrieval_arena.bertscore_scoring_config.v1`.

**Output:** `retrieval_arena.bertscore_scores.v1` (`provider_id`, `provider_sha256`, `answer_pairs_sha256`, `records[]`, `scores_sha256`); the operator writes `bertscore_scores.v1.json` and a `retrieval_arena.bertscore_scoring_operator_receipt.v1`.

## Metric ids

`precision`, `recall`, `f1` per record; as profile families `bertscore_precision`, `bertscore_recall`, `bertscore_f1` (the `automatic_answer_family` beside `lexical`).

**Invocation:** `python -m retrieval_arena.cli score-bertscore --provider <provider.json> --answer-pairs <answers.jsonl> --out <scores.json>`; `scripts/run-verification.ps1 -Command score-bertscore -InputRoot … -OutputRoot … -Receipt …` (memory raised to 8 GB). DocSpectra Capstone composes the pairs (`scripts/compose_bertscore_input.py`) and reads the scores.

**Tests:** `unit_tests/test_dual_profile.py`, `unit_tests/test_profile_evaluation.py`, `unit_tests/test_verification_witness.py` (the witness's live offline BERTScore observation) — all in the `focused` suite.

**Source:** `retrieval_arena/dual_profile.py` (`run_bertscore_offline`, `validate_bertscore_provider`, `validate_bertscore_scores`); `containers/verification/evaluate_profile.py` (`_score_bertscore_files`).
