# `rag_llm_guided_search`

Bounded link-following navigator over one candidate corpus (S026, EXP-1c). Starts at the corpus root `index.md` with the question; actions `open(link)` (a link the current page carries, resolved through `graph_edges.csv`), `back`, `answer(cited_pages, text)`; a real local model behind Ollama's chat API reached only through the host relay; one trace per question. The answer is the agent's text (`agent_answer_v1`) and the cited pages are the context items.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`; network: `model_policy.endpoint` only.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` plus `answer_policy: agent_answer_v1` and `cited_pages` per row (items `source: llm_guided_navigation`); `traces/<question_id>.jsonl` (`e007b.action_trace.v1`, policy `bounded_provider_action_controller_v1`); `navigation.json` (`retrieval_arena.navigation_run.v1`: model digests, terminals, opens, calls; `status: unavailable` with exit 4 when `index.md` is missing).

## Knobs

| Key | Default |
| --- | --- |
| `answer_policy` | `agent_answer_v1` (only) |
| `top_k` | `5` |
| `max_context_chars_per_doc` | `1500` |
| `page_visit_budget` | `8` |
| `model_call_budget` | `12` |
| `page_char_limit` | `12000` |
| `model_policy` | required — see below |

`model_policy` is a required dict: `backend: ollama`, `model`, `digest` (sha256, observed before the run and after every call), `temperature`, `top_p`, `seed`, `context_window_tokens`, `max_output_tokens`, `timeout_seconds`, `max_attempts` (1), `endpoint` (private host, `GET /api/tags` and `POST /api/chat` only), `thinking` (`false`).

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context` (the cited pages); navigation metrics (`scripts/navigation_metrics.py`) from the traces; BERTScore over the agent's answers.

**Admission:** admitted at `candidate-retrieve` (image `retrieval-arena/rag-llm-guided-search@sha256:…`, `requires_graph` true, network `host_model_alias` — the operator runs it on the default bridge with `host.docker.internal` added; `publishes_traces`).

**Tests:** `unit_tests/test_rag_llm_guided_search_runner.py` (7 cases: snippet matches the lexical runner, open/back/answer trace and row, invalid actions and budgets terminate, visit budget, missing start page, closed model policy, navigation metrics recompute); `unit_tests/test_candidate_retrieval.py::test_navigator_prepare_carries_graph_and_launch_network`.

**Source:** `tests/rag_llm_guided_search/runner.py`.
