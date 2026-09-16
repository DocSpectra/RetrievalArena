# `rag_repo_search_agent_nolinks`

The `rag_repo_search_agent` image with `follow_links: false`: a grep-only reader that never sees or opens a page's outlinks — `open(doc_id)` admits search hits only and a link id is refused. Everything else (landing grep, budgets, model policy, traces) is the links variant's; the variant is fixed by the provider name, not by the request.

**Input:** As `rag_repo_search_agent`.

**Output:** As `rag_repo_search_agent`; the trace header records `follow_links: false` and no `link` route occurs; metadata `name` is `rag_repo_search_agent_nolinks`.

## Knobs

| Key | Default |
| --- | --- |
| `follow_links` | required bool (`false` for this name) |
| `all others` | as [`rag_repo_search_agent`](rag_repo_search_agent.md) |

**Metrics it feeds:** As `rag_repo_search_agent`: ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`; search-navigation metrics; BERTScore over answers.

**Admission:** admitted at `candidate-retrieve` as its own `ProviderProfile` row (same image repository, `follow_links` false).

**Tests:** `unit_tests/test_rag_repo_search_agent_runner.py::test_rsa2_nolinks_variant_hides_links_and_refuses_a_link_id`; `unit_tests/test_candidate_retrieval.py::test_repo_search_agent_declared_in_two_variants_fixed_by_name`.

**Source:** `tests/rag_repo_search_agent/runner.py` (`PROVIDER_NAME_NOLINKS`).
