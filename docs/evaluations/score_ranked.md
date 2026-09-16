# `score_ranked`

Ranked-retrieval metrics v2: scores a provider's ranked predictions against frozen qrels under a metric policy, delegating the arithmetic to the pinned `ir-measures==0.4.3` engine (the wheel sha256 is checked) and recording a replay artifact. The primary metric is macro evidence recall at the policy's `context_item_budget_k`.

**Input:** `retrieval_arena.ranked_dataset.v2` (`--dataset`), qrels JSONL (`--qrels`), `retrieval_arena.ranked_prediction.v2` predictions (`--predictions`), `retrieval_arena.ranked_metric_policy.v2` (`--policy`: `primary_metric: evidence_recall`, `secondary_metrics`, `context_item_budget_k`, `gain: identity`, `discount: log2_rank_plus_one`, `aggregation: query_macro`, `tie_policy`, `duplicate_policy: reject`).

**Output:** `ranked_item_scores.v2.jsonl` (one `retrieval_arena.ranked_item_score.v2` row per query), `ranked_aggregate.v2.json` (`retrieval_arena.ranked_aggregate.v2`), and the `retrieval_arena.ranked_replay.v2` record with the engine identity and artifact hashes.

## Metric ids

Per query: `evidence_recall` (`Recall@k`), `complete_support` (all targets in the top-k), `ndcg` (`nDCG@k`; `None` with unjudged top-k items under a pooled policy), `reciprocal_rank` (`RR@k`), plus `available_metrics`/`unavailable_metrics`. Aggregate: `macro_evidence_recall`, `macro_complete_support`, `macro_ndcg`, `mrr`. Guide: [../ranked_retrieval_metrics_v2_plan.md#metric-guide](../ranked_retrieval_metrics_v2_plan.md#metric-guide).

**Invocation:** `python -m retrieval_arena.cli score-ranked --dataset <dataset.json> --qrels <qrels.jsonl> --predictions <predictions.jsonl> --policy <policy.json> --out-dir <dir>`; inside the verification image through `evaluate-profile` (`retrieval_arena/profile_evaluation.py`).

**Tests:** `unit_tests/test_ranked_scoring.py`; `unit_tests/test_profile_evaluation.py` and `test_dual_profile.py` in the `focused` suite.

**Source:** `retrieval_arena/ranked_scoring.py` (`score_ranked`, `score_ranked_files`).
