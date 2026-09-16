# `oracle_empty`

Answer oracle for calibrating an answer scorer: `generated_answer` is an empty string, `retrieved_context` is empty. The floor: an answer scorer must give it its minimum. Declared in `configs/experiment.example.yaml` and `configs/e001_llm_only_minidocs.yaml`.

**Input:** `/input/questions.jsonl`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; `retrieved_context: []`; metadata `{name: oracle_empty, deterministic: true}`.

## Knobs

None: the runner does not read `config.yaml`.

**Metrics it feeds:** answer scorers only: legacy v1 `lexical_baseline` (`precision`, `recall`, `f1`) in the experiment YAMLs, BERTScore when pairs are composed; ranked v2 has nothing to rank.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only).

**Tests:** no unit test.

**Source:** `tests/oracle_empty/runner.py`.
