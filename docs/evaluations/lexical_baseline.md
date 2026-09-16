# `lexical_baseline`

Legacy v1 answer scoring: token-overlap precision, recall and F1 between `generated_answer` and the gold answer, with a match threshold on F1. The `scoring.method` every experiment YAML declares (the only value `retrieval_arena/config.py` accepts) and the score the BigData pilots and the E001 baselines report. Superseded for retrieval by ranked v2; kept for the YAML lane and the pilot bundles.

**Input:** The experiment's `predictions.jsonl` rows and the dataset's `answers.jsonl`; `scoring.match_threshold` from the YAML (`0.5` in the examples).

**Output:** Per-question `score_pair` records and the run's aggregate written by the harness into the run directory.

## Metric ids

Per pair: `precision`, `recall`, `f1`, `lexical_overlap`, `match` (`f1 >= match_threshold`). Aggregate: `match_percent`, `mean_f1`, `mean_precision`, `mean_recall`, `mean_lexical_overlap`, `num_questions`.

**Invocation:** `scoring: {method: lexical_baseline, match_threshold: 0.5}` in `configs/<experiment>.yaml`, applied by `python -m retrieval_arena.cli run --config <yaml>`.

**Tests:** `unit_tests/test_scoring.py`; `unit_tests/test_config.py` (method validation).

**Source:** `retrieval_arena/scoring.py` (`score_pair`, `score_predictions`).
