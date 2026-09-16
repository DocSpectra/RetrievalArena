# `rag_random_seed_graph_traversal`

Randomised-seed traversal baseline (E010, policy `random_seed_graph_traversal_v1`): over `num_trials` trials seeded from `rng_seed`, sample seed documents uniformly, traverse up to `max_hops` under `neighbor_budget`/`candidate_budget`, rank by BM25 minus a distance penalty, and keep the best trial. A stochastic diagnostic of graph navigability, deterministic for a fixed seed; no model.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with a `random_seed_graph_traversal` sub-object per row (rng config, sampled seeds, per-trial records, best trial); items `source: random_seed_graph_traversal`, `selection_role` (`random_seed` | `traversed_candidate`), `bm25_score`, `graph_distance`, `rank`; metadata `stochastic_diagnostic: true`, `seed_ranker: random_uniform`.

## Knobs

| Key | Default |
| --- | --- |
| `num_trials` | `8` |
| `rng_seed` | `20260504` |
| `max_hops` | `2` |
| `neighbor_budget` | `12` |
| `candidate_budget` | `60` |
| `final_top_k` | `top_k` (`5`) |
| `distance_penalty` | `0.10` |
| `max_context_chars_per_doc` | `1200` |
| `directed` | `false` |
| `experiment_id` | `E010` |
| `diagnostic_role` | `random_seed_graph_navigability` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only).

**Tests:** `unit_tests/test_rag_random_seed_graph_traversal.py` (4 cases: determinism under one seed, seed change alters sampled seeds, rng config and no-LLM metadata, hop/candidate/final budgets respected).

**Source:** `tests/rag_random_seed_graph_traversal/runner.py`.
