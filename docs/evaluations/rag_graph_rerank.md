# `rag_graph_rerank`

BM25 seeds expanded one hop along `graph_edges.csv` (undirected), then the seed-plus-neighbour set is reranked by BM25 and cut to `final_top_k`. A BigData pilot family.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; items `source: graph_rerank`, `selection_role` (`seed` | `expanded_neighbor`), `rank`; metadata records `seed_ranker: bm25`, `final_ranker: bm25` and the knobs.

## Knobs

| Key | Default |
| --- | --- |
| `seed_top_k` | `5` |
| `final_top_k` | `top_k` (`5`) |
| `max_context_chars_per_doc` | `1200` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); a family of `configs/bigdata2026/` and the `docs_pilot_*.yaml` pilots.

**Tests:** no runner test; `unit_tests/test_bigdata_pilot.py` runs it as a selected pilot test.

**Source:** `tests/rag_graph_rerank/runner.py`.
