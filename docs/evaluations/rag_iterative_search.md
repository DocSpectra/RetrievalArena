# `rag_iterative_search`

Deterministic BM25 search loop (E007a, policy `deterministic_bm25_link_walk_v1`): search the corpus, inspect the top hits, follow up to `follow_budget_per_page` links from each inspected page, and stop at `max_actions`; the pages inspected form the context, cut to `final_top_k`. The search-loop ancestor of `rag_repo_search_agent`; no model.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with an `action_trace` per row; `action_traces.jsonl` (one `e007a.action_trace.v1` trace per question: budgets, actions, `final_context_doc_ids`); items `source: iterative_search`, `selection_role: iterative_context`, `rank`.

## Knobs

| Key | Default |
| --- | --- |
| `search_top_k` | `top_k` (`5`) |
| `inspect_budget` | `8` |
| `follow_budget_per_page` | `3` |
| `max_actions` | `30` (min 3) |
| `final_top_k` | `top_k` (`5`) |
| `max_context_chars_per_doc` | `1200` |
| `directed` | `false` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`; its `e007a` traces predate the navigation scripts.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); a family of the BigData pilot plans.

**Tests:** `unit_tests/test_rag_iterative_search.py` (3 cases: valid trace and prediction, determinism, action budget honoured).

**Source:** `tests/rag_iterative_search/runner.py`.
