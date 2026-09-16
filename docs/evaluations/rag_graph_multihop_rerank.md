# `rag_graph_multihop_rerank`

Multi-hop frontier search: BM25 seeds, a bounded frontier expanded up to `max_hops` along the graph (`frontier_budget_per_node`, `candidate_budget`), reranked by BM25 minus a `distance_penalty` per hop, with `reserved_deep_slots` of the final list held for candidates at least `reserved_min_graph_distance` hops out. A BigData pilot family.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with a `graph_multihop` sub-object per row (seeds, candidate count, hops, reserved slots); items `source: graph_multihop_rerank`, `selection_role` (`seed` | `multihop_neighbor`), `graph_distance`, `rank`.

## Knobs

| Key | Default |
| --- | --- |
| `seed_top_k` | `5` |
| `max_hops` | `2` |
| `frontier_budget_per_node` | `12` |
| `candidate_budget` | `60` |
| `final_top_k` | `5` |
| `distance_penalty` | `0.15` |
| `reserved_deep_slots` | `0` |
| `reserved_min_graph_distance` | `2` |
| `max_context_chars_per_doc` | `1200` |
| `directed` | `false` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); a family of the BigData pilot plans.

**Tests:** no runner test; `unit_tests/test_config.py::test_loads_docs_pilot_configs_with_oracle_graph_support` loads its config.

**Source:** `tests/rag_graph_multihop_rerank/runner.py`.
