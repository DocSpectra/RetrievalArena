# `rag_semantic_graph_rerank`

Embedding seeds expanded along the graph (policy `semantic_seeded_graph_rerank_v1`): the `seed_top_k` documents by embedding similarity (hashing TF-IDF or an offline sentence-transformers snapshot, as `rag_embedding_topk`), expanded `graph_hops` hops under `neighbor_budget`/`candidate_budget`, reranked by similarity minus a `distance_penalty`, with an optional PageRank prior. A pilot-era family.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`; the model lock and snapshot in the sentence-transformers mode.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with a `semantic_graph_rerank` sub-object per row (seed ids, expanded candidates, graph distances, `pagerank_prior_enabled`, `variant_id`); items add `semantic_score`, `graph_distance`, `pagerank`; metadata records the model provenance fields and `offline_required`.

## Knobs

| Key | Default |
| --- | --- |
| `seed_top_k` | `top_k` (`5`) |
| `graph_hops` | `hop_budget` (`1`) |
| `neighbor_budget` | `12` |
| `candidate_budget` | `60` |
| `final_top_k` | `top_k` (`5`) |
| `max_context_chars_per_doc` | `1200` |
| `distance_penalty` | `0.02` |
| `directed` | `false` |
| `use_pagerank_prior` | `false` |
| `pagerank_weight` | `0.0` |
| `pagerank_iterations` | `30` |
| `pagerank_damping` | `0.85` |
| `embedding_dimensions` | `512` |
| `model_id` | `local-hashing-tfidf-v1` |
| `embedding_backend` | `local_hashing_tfidf` |
| `model_lock_path` | `/model_provenance/embedding_models.lock.json` |
| `model_cache_path` | `/models/minilm` |
| `batch_size` | `32` |
| `variant_id` | `""` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only); `configs/minilm_smoke.yaml`.

**Tests:** `unit_tests/test_rag_semantic_graph_rerank.py` (5 cases: predictions and metadata, seeds expanded before rerank, neighbours can enter the final context, determinism, provenance fields).

**Source:** `tests/rag_semantic_graph_rerank/runner.py`.
