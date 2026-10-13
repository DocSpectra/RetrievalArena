# `rag_graph_expand`

Minimal graph example: the best token-overlap document plus its first (sorted) neighbour in `graph_edges.csv`, treated as undirected. A two-item context; not a scientific system.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv` (optional).

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; items `source: graph_expand`, `score` 1.0 for the seed and 0.0 for the neighbour; metadata `{name: rag_graph_expand, deterministic: true}`.

## Knobs

None: the runner does not read `config.yaml`.

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); `unit_tests/test_candidate_retrieval.py` uses its name as the undeclared-provider fixture.

**Tests:** no runner test.

**Source:** `tests/rag_graph_expand/runner.py`.
