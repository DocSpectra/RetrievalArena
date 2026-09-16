# `local_no_context_llm_e001b`

No-context local-model answer baseline (E001b, policy `e001b_local_no_context_llm_v1`): answers each question from the prompt alone through a loopback-only OpenAI-compatible gate, or with a mock answer by default. Live execution needs `local_only`, `allow_network: false` and an explicit `allow_local_provider_execution`; remote URLs are refused before any HTTP. Not paper-facing evidence.

**Input:** `/input/questions.jsonl`, `/input/config.yaml`; live mode: `POST {base_url}/chat/completions` on loopback.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with a `metadata` sub-object per row (`experiment_id: E001b`, `retrieval_context: none`, `provider_provenance`); `retrieved_context: []`; metadata records `provider`, `local_only`, `allow_network`, `prompt_version`, `response_schema_version`.

## Knobs

| Key | Default |
| --- | --- |
| `provider_mode` | `mock` |
| `model` | `mock/docspectra-e001b-no-context-v1` |
| `base_url` | `""` |
| `temperature` | `0.0` |
| `max_tokens` | `256` |
| `timeout_seconds` | `60.0` |
| `max_retries` | `0` |
| `local_only` | `true` |
| `allow_network` | `false` |
| `allow_local_provider_execution` | `false` |

**Metrics it feeds:** answer scorers only: legacy v1 `lexical_baseline` in `configs/e001b_local_no_context_minidocs.yaml`, BERTScore when pairs are composed; ranked v2 has nothing to rank.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only).

**Tests:** `unit_tests/test_local_no_context_llm_e001b.py` (mock output and policy metadata; live mode refused without the execution gate; remote or network policy refused); `unit_tests/test_config.py::test_loads_e001b_local_no_context_config`.

**Source:** `tests/local_no_context_llm_e001b/runner.py`.
