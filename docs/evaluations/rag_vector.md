# `rag_vector`

Minimal example provider: the single document with the highest token overlap with the question, returned as the whole context. It is the smallest runner in the tree and the template a new provider starts from; not a scientific system.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; one item `source: vector` with the overlap count as `score`; metadata `{name: rag_vector, deterministic: true}`.

## Knobs

None: the runner does not read `config.yaml`.

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context` (a one-item list).

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); the BigData pilot plans list it as a family.

**Tests:** no unit test.

**Source:** `tests/rag_vector/runner.py`.
