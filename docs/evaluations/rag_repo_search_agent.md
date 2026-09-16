# `rag_repo_search_agent`

Bounded repo-search agent reader over one candidate corpus (S046, EXP-1e). Lands by an automatic literal, case-insensitive line grep of the question over the corpus Markdown (`search_hit_files` files by match count then id, `search_hit_lines_per_file` lines each; a query is text, never a path or regex); actions `search(query)`, `open(doc_id)` (a hit of any search so far or, with `follow_links`, an admissible outlink of the current page), `back`, `answer(cited_pages, text)`. The navigator's model client and relay rules.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`; network: `model_policy.endpoint` only.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` plus `answer_policy: agent_answer_v1` and `cited_pages` (items `source: repo_search_navigation`); `traces/<question_id>.jsonl` (`e007c.action_trace.v1`, policy `bounded_repo_search_controller_v1`: search lines with queries and returned ids, opens with a `route` — `landing`/`search`/`link`); `navigation.json`; metadata records `follow_links` and `landing: search_question_text`.

## Knobs

| Key | Default |
| --- | --- |
| `answer_policy` | `agent_answer_v1` (only) |
| `follow_links` | required bool (`true` for this name) |
| `top_k` | `5` |
| `max_context_chars_per_doc` | `1500` |
| `search_budget` | `3` (the landing counts) |
| `search_hit_files` | `10` |
| `search_hit_lines_per_file` | `3` |
| `page_visit_budget` | `8` |
| `model_call_budget` | `12` |
| `page_char_limit` | `12000` |
| `model_policy` | required, as `rag_llm_guided_search` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context` (the cited pages); search-navigation metrics (`scripts/search_navigation_metrics.py`) from the traces; BERTScore over the agent's answers.

**Admission:** admitted at `candidate-retrieve` (image `retrieval-arena/rag-repo-search-agent@sha256:…`, `requires_graph` true, network `host_model_alias`, `follow_links` true fixed by the profile; provider config keys `model_policy`, `search_budget`, `search_hit_files`, `search_hit_lines_per_file`, `page_visit_budget`, `model_call_budget`, `page_char_limit`, `follow_links`).

**Tests:** `unit_tests/test_rag_repo_search_agent_runner.py` (10 cases RSA-1…RSA-6 and more: landing order, hits then links, literal queries, budgets end the trace, link route and `link_new`, route attribution sums, provider error ends the query, config requires `follow_links`); `unit_tests/test_candidate_retrieval.py::test_repo_search_agent_declared_in_two_variants_fixed_by_name`.

**Source:** `tests/rag_repo_search_agent/runner.py` (one image, two profile names).
