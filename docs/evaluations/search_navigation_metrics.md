# `search_navigation_metrics`

Search-navigation metrics from S046 action traces (EXP-1e, exploratory): reads one `traces/<query_id>.jsonl` per query (`e007c.action_trace.v1`, policy `bounded_repo_search_controller_v1`) and the support targets, attributes every success to the route that produced it (`landing`, `search`, `link`), and, given a baseline link graph, tells a link that did not exist in the baseline (`link_new`) from one that did.

**Input:** `--traces <dir>`, `--support <support.json>`, `--baseline-graph <graph_edges.csv>` (optional; enables `link_new`), `--per-query`.

**Output:** `retrieval_arena.search_navigation_metrics.v1` JSON: slot totals and, with `--per-query`, per-query records adding `landing_hit`, `success_route`, `link_new`, `cost_to_first_support`, `searches`, `zero_hit_search`.

## Metric ids

The navigation set (`success_at_n`, `hops_to_first_support`, `pages_per_success`, `dead_ends`, `opens`, `model_calls`, `terminals`) plus `landing_hit_rate`, `successes_by_route` (`landing`/`search`/`link`), `successes_via_new_link`, `cost_to_first_support`, `cost_to_first_support_by_route`, `searches_mean`, `queries_with_zero_hit_search`.

**Invocation:** `python scripts/search_navigation_metrics.py --traces <dir> --support <support.json> [--baseline-graph <graph_edges.csv>] [--per-query]`; applies to `rag_repo_search_agent` and `_nolinks` traces.

**Tests:** `unit_tests/test_rag_repo_search_agent_runner.py` (`test_rsa5_link_route_and_link_new_against_a_baseline_graph`, `test_rsa6_route_attribution_sums_to_success_count`).

**Source:** `scripts/search_navigation_metrics.py`.
