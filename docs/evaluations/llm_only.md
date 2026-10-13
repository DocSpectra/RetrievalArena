# `llm_only`

No-retrieval answer baseline with a dummy model: every question gets the fixed text "This dummy model does not use retrieval." and an empty context (E001, `configs/e001_llm_only_minidocs.yaml`). It fixes the shape of a no-retrieval arm; not a scientific system.

**Input:** `/input/questions.jsonl`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; `retrieved_context: []`; metadata `{name: llm_only, deterministic: true}`.

## Knobs

None: the runner does not read `config.yaml`.

**Metrics it feeds:** legacy v1 `lexical_baseline` (`precision`, `recall`, `f1`) as declared in the E001 YAML; ranked v2 has nothing to rank.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only).

**Tests:** no unit test.

**Source:** `tests/llm_only/runner.py`.
