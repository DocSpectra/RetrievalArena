# `rag_repo_search_agent`

Bounded repo-search agent reader over one candidate corpus (S046, EXP-1e). Lands by an automatic search of the question over the corpus Markdown under the request's `landing_policy` (S046b): `search_question_text` — a literal, case-insensitive line grep of the whole question (`search_hit_files` files by match count then id, `search_hit_lines_per_file` lines each; a query is text, never a path or regex) — or `tokenised_content_words_v1` — the question's first 200 characters lower-cased and split into `[a-z0-9]+` runs, tokens shorter than two characters and the fixed stop words `STOP_WORDS_EN_V1` dropped, duplicates removed (`landing_terms`); every corpus file tokenised by the same rule; a file's matched set is the landing terms it carries as whole tokens (no stemming, substrings, synonyms or embeddings); files ranked by matched-set size, then the most landing terms on one line, then document id; at most `search_hit_files` files with at most `search_hit_lines_per_file` lines each (terms on the line, then line number); each hit `{doc_id, path, line_number, text, matched_words, matched_word_count}`; no terms or no matching file is a zero-hit landing. The model's own `search(query)` is the literal all-words line grep under either policy; actions `search(query)`, `open(doc_id)` (a hit of any search so far or, with `follow_links`, an admissible outlink of the current page), `back`, `answer(cited_pages, text)`. The navigator's model client and relay rules.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`; network: `model_policy.endpoint` only.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` plus `answer_policy: agent_answer_v1` and `cited_pages` (items `source: repo_search_navigation`); `traces/<question_id>.jsonl` (`e007c.action_trace.v1`, policy `bounded_repo_search_controller_v1`: search lines with queries and returned ids, opens with a `route` — `landing`/`search`/`link`); `navigation.json`; metadata records `follow_links`, `landing` (the policy id), `prompt_version` (`e007d_repo_search_prompt_v1` under the literal landing; `_v2` — the v1 text plus one sentence naming the landing terms — under the tokenised one) and, under the tokenised policy, `stop_words` (the list) and `stop_word_list: STOP_WORDS_EN_V1`; the trace header carries `landing`, `landing_query` and `landing_terms` (null under the literal policy), the landing search line carries `landing_terms` under the tokenised policy, and every `open` line records the page view's `truncated` and `char_count`.

## Knobs

| Key | Default |
| --- | --- |
| `answer_policy` | `agent_answer_v1` (only) |
| `follow_links` | required bool (`true` for this name) |
| `landing_policy` | required, `search_question_text` \| `tokenised_content_words_v1` |
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

**Admission:** admitted at `candidate-retrieve` (image `retrieval-arena/rag-repo-search-agent@sha256:…`, `requires_graph` true, network `host_model_alias`, `follow_links` true fixed by the profile; provider config keys `model_policy`, `search_budget`, `search_hit_files`, `search_hit_lines_per_file`, `page_visit_budget`, `model_call_budget`, `page_char_limit`, `follow_links`, `landing_policy` — absent or unknown is refused, `candidate-retrieval-provider-landing-policy`; at finalize the trace header's policy must be the request's and, under the tokenised policy, its `landing_terms` must equal the rule applied to `landing_query`; the launch record and the operator receipt name `landing_policy`).

**Tests:** `unit_tests/test_rag_repo_search_agent_runner.py` (13 cases: RSA-1…RSA-6 and more — landing order, hits then links, literal queries, budgets end the trace, link route and `link_new`, route attribution sums, provider error ends the query, config requires `follow_links` and a closed `landing_policy`; S046b RL-1…RL-3 — landing terms and the zero-hit landing, the file and line ranking, tokens never paths, the model's search unchanged, the endpoint's list and rule equal the runner's); `unit_tests/test_candidate_retrieval.py::test_repo_search_agent_declared_in_two_variants_fixed_by_name` and `::test_repo_search_projection_validates_e007c_traces_at_finalize` (RL-4 at both boundaries).

**Source:** `tests/rag_repo_search_agent/runner.py` (one image, two profile names).
