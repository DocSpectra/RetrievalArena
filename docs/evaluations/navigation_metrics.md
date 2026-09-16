# `navigation_metrics`

Navigation metrics from S026 action traces (EXP-1c, exploratory): reads one `traces/<query_id>.jsonl` per query (`e007b.action_trace.v1`, policy `bounded_provider_action_controller_v1`) and the evaluator's support targets, and recomputes from the traces alone the behavioural measures the report shows — whether the walk cited a support page, how many opens it took, and how it ended.

**Input:** `--traces <dir>` (the navigator's published `traces/`), `--support <support.json>` (`{"records": [{"query_id", "target_ids"}]}` — the judge's support file), `--per-query`.

**Output:** `retrieval_arena.navigation_metrics.v1` JSON: slot totals and, with `--per-query`, one record per query (`success`, `hops_to_first_support`, `opens`, `model_calls`, `dead_ends`, `terminal`, `cited_support`, `cited_pages`).

## Metric ids

`query_count`, `success_count`, `success_at_n`, `hops_to_first_support`, `pages_per_success`, `dead_ends`, `opens`, `model_calls`, `terminals` (`answer`, `budget_exhausted`, `invalid_action`, `provider_error`).

**Invocation:** `python scripts/navigation_metrics.py --traces <dir> --support <support.json> [--per-query]`; applies to `rag_llm_guided_search` traces.

**Tests:** `unit_tests/test_rag_llm_guided_search_runner.py::test_navigation_metrics_recompute_from_traces`.

**Source:** `scripts/navigation_metrics.py`.
