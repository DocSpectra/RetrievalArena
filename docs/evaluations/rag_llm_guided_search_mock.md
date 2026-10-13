# `rag_llm_guided_search_mock`

The navigator scaffold (E007b): the bounded action controller and the `e007b.action_trace.v1` trace shape (`search`, `inspect_page`, `follow_link`, `add_context`, `stop`) driven by a mock fixture controller, a fake response sequence, or a loopback-only OpenAI-compatible provider. It exists to fix the trace and controller contract that `rag_llm_guided_search` promoted; not a scientific system.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`; optional loopback HTTP under an explicit local-execution flag.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with an `action_trace` per row (items `source: iterative_context`-style roles); `action_traces.jsonl` (`e007b.action_trace.v1`, policy `mock_llm_guided_fixture_controller_v1`); metadata records `controller`, `provider`, `dry_run`, `paper_facing_evidence: false`.

## Knobs

| Key | Default |
| --- | --- |
| `controller` | `mock` (or `provider`) |
| `search_top_k` | `top_k` (`5`) |
| `inspect_budget` | `8` |
| `follow_budget_per_page` | `2` |
| `max_actions` | `30` |
| `final_top_k` | `top_k` (`5`) |
| `max_context_chars_per_doc` | `1200` |
| `directed` | `false` |
| `provider` | dict: `type` (`mock`/`fake_sequence`/`openai_compatible`), `model_id`, `base_url`, `local_only`, `allow_network`, `allow_local_provider_execution`, `responses`, … |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`; its traces are `action_traces.jsonl`, not the `traces/` directory the navigation script reads.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); `configs/e007b_mock_minidocs.yaml`.

**Tests:** `unit_tests/test_rag_llm_guided_search_mock.py` (14 cases: deterministic mock trace, fake sequence accepted/refused, remote URL and network flags refused before HTTP, loopback request construction, inspection-count rules).

**Source:** `tests/rag_llm_guided_search_mock/runner.py`.
