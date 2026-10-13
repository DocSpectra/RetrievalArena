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
| `invalid_action_recovery` | optional, `0` \| `2`; absent ≡ `0` (S057) |

## Observation and recovery (S057)

The model sees one JSON observation per call (`build_messages`, keys sorted): the question, the current page, the searches so far, the offered links (with `follow_links`), the openable pages, the allowed actions and the budgets. The observation is not written to the trace; each `provider_calls` entry records `request_index`, `current_page`, `links_offered` and `hits_offered` (counts), `requested_action`, `usage`, `model_digest` and `prompt_version`.

An `open` of the current page or of a page that is neither a search hit nor (with `follow_links`) an admissible outlink is refused `page_not_offered`. Without `invalid_action_recovery`, or with `0`, the refusal ends the query (`terminal.reason: invalid_action`), as since S046. With `2`, the first two refusals of a query do not end it: the refused call's `provider_calls` entry gains `recovered: true` and `validation_error: page_not_offered` beside its `requested_action`, no action row is written, the call stays counted against `model_call_budget`, and the next observation carries one extra key, `last_action_feedback`, with the fixed text `RECOVERY_FEEDBACK` ("That page is already open or was not offered. Answer from the open page, search, open a page listed as offered, or go back."). The key is absent from every other observation, so a query that is never refused sends the same bytes as under `0`. The third refusal ends the query `invalid_action`. When the field is declared, the trace header's `controller` carries `invalid_action_recovery`, the terminal line carries `invalid_actions_recovered: {count, steps}` (the refused calls' request indices), `navigation.json` carries the campaign total, and `metadata.json` carries the setting; when it is absent none of these keys appears. At finalize `validate_search_traces` refuses a terminal whose recovery record is present without the request field, absent with it, inconsistent, or above the bound (`candidate-retrieval-provider-trace-recovery`).

## Link-syntax profiles (S057)

The provider's `graph_edges.csv` is extracted at prepare. A request may declare `corpus.link_profiles` (closed list; absent ≡ `["markdown_relative_v1"]`, today's relative-`.md` resolver byte for byte) and `corpus.link_profile_params`. `hugo_site_url_v1` (`retrieval_arena/graph/hugo.py`, parameters `product_url_prefix`, `site_prefixes`, `content_root` relative to `corpus.root`) resolves Hugo `ref:` aliases through the page's front-matter `refs` (the first entry whose `pattern` is a prefix of the product URL prefix), absolute site URLs under the literal `site_prefixes`, and directory-style relative URLs under Hugo page-URL semantics, to `<path>.md`, `<path>/_index.md` or `<path>/index.md`, else through the front-matter `aliases` index (relative aliases against the page's parent section URL; sorted path first on a shared key). Several profiles give the union of their edges. The launch record and the operator receipt carry `link_profiles` and `link_resolution` (per profile and kind: found / resolved / external / unresolved).

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context` (the cited pages); search-navigation metrics (`scripts/search_navigation_metrics.py`) from the traces; BERTScore over the agent's answers.

**Admission:** admitted at `candidate-retrieve` (image `retrieval-arena/rag-repo-search-agent@sha256:…`, `requires_graph` true, network `host_model_alias`, `follow_links` true fixed by the profile; provider config keys `model_policy`, `search_budget`, `search_hit_files`, `search_hit_lines_per_file`, `page_visit_budget`, `model_call_budget`, `page_char_limit`, `follow_links`, `landing_policy` — absent or unknown is refused, `candidate-retrieval-provider-landing-policy`; at finalize the trace header's policy must be the request's and, under the tokenised policy, its `landing_terms` must equal the rule applied to `landing_query`; the launch record and the operator receipt name `landing_policy`).

**Tests:** `unit_tests/test_s057_reader_v2.py` (S057 cases (a)–(e): recovery absent/0/2, the feedback key, the Hugo fixture, the endpoint fields); `unit_tests/test_rag_repo_search_agent_runner.py` (13 cases: RSA-1…RSA-6 and more — landing order, hits then links, literal queries, budgets end the trace, link route and `link_new`, route attribution sums, provider error ends the query, config requires `follow_links` and a closed `landing_policy`; S046b RL-1…RL-3 — landing terms and the zero-hit landing, the file and line ranking, tokens never paths, the model's search unchanged, the endpoint's list and rule equal the runner's); `unit_tests/test_candidate_retrieval.py::test_repo_search_agent_declared_in_two_variants_fixed_by_name` and `::test_repo_search_projection_validates_e007c_traces_at_finalize` (RL-4 at both boundaries).

**Source:** `tests/rag_repo_search_agent/runner.py` (one image, two profile names).
