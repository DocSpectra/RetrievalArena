# `rag_directed_spectral_rerank`

BM25 seeds reranked with directed link structure (E008, policy `bm25_plus_directed_pagerank_hits_v1`): forward and backward link bonuses from the seeds, a PageRank prior and HITS authority/hub priors computed over the directed graph, combined with the normalised BM25 score and cut to `final_top_k`. A pilot-era family.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv` (directed), `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with a `directed_spectral` sub-object per row; items `source: directed_spectral_rerank`, `selection_role` (`seed` | `directed_or_spectral_candidate`), `bm25_score_norm`, `pagerank`, `authority`, `hub`, `spectral_prior_score`, forward/backward neighbour flags, `rank`.

## Knobs

| Key | Default |
| --- | --- |
| `seed_top_k` | `5` |
| `final_top_k` | `top_k` (`5`) |
| `directed_neighbor_budget` | `8` |
| `global_prior_top_k` | `10` |
| `max_context_chars_per_doc` | `1200` |
| `pagerank_weight` | `0.18` |
| `authority_weight` | `0.22` |
| `hub_weight` | `-0.04` |
| `forward_link_bonus` | `0.08` |
| `backward_link_bonus` | `0.03` |
| `spectral_iterations` | `40` |
| `pagerank_damping` | `0.85` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only).

**Tests:** `unit_tests/test_rag_directed_spectral_rerank.py::test_runner_uses_directed_spectral_components`.

**Source:** `tests/rag_directed_spectral_rerank/runner.py`.
