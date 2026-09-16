# `oracle_partial`

Answer oracle for calibrating an answer scorer: `generated_answer` is the first three words of the gold answer, `retrieved_context` is empty. A partial-credit reference point between the ceiling and the floor. Declared in `configs/experiment.example.yaml` and `configs/e001_llm_only_minidocs.yaml`.

**Input:** `/input/questions.jsonl`, `/input/answers.jsonl` (the gold answers).

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; `retrieved_context: []`; metadata `{name: oracle_partial, deterministic: true}`.

## Knobs

None: the runner does not read `config.yaml`.

**Metrics it feeds:** answer scorers only: legacy v1 `lexical_baseline` (`precision`, `recall`, `f1`) in the experiment YAMLs, BERTScore when pairs are composed; ranked v2 has nothing to rank.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only).

**Tests:** no unit test.

**Source:** `tests/oracle_partial/runner.py`.
