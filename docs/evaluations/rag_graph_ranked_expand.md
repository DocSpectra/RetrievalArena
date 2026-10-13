# `rag_graph_ranked_expand`

BM25 seeds plus a ranked one-hop neighbour budget (S025, the EXP-1 graph retriever): the `seed_top_k` best BM25 documents, then the `neighbor_budget` best BM25 documents among their one-hop link neighbours (`graph_edges.csv`, both directions) that are not seeds. `top_k = seed_top_k + neighbor_budget` (`top_k: 5` splits 3 + 2); `graph_expansion_hops` is fixed at 1 and refused otherwise. Answer policies as `rag_lexical_topk`.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md` (stem ids), `/input/graph_edges.csv`, `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; items carry `source: graph_ranked_expand`, `selection_role` (`seed` | `ranked_neighbor`), `rank`; `answer_provenance` under `best_passage_v1`; metadata records `seed_ranker`, `neighbor_ranker`, `graph_expansion_hops: 1`, the budgets and `answer_policy`.

## Knobs

| Key | Default |
| --- | --- |
| `graph_expansion_hops` | `1` (must be 1) |
| `seed_top_k` | `3` |
| `neighbor_budget` | `2` |
| `top_k` | `seed_top_k + neighbor_budget` (given alone: `neighbor_budget = min(2, top_k - 1)`) |
| `max_context_chars_per_doc` | `1200` |
| `answer_policy` | `head_of_document_v1` (or `best_passage_v1`) |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`; BERTScore over `generated_answer` when the caller composes pairs.

**Admission:** admitted at `candidate-retrieve` (image `retrieval-arena/rag-graph-ranked-expand@sha256:…`, `requires_graph` true — prepare extracts the candidate's own link graph and binds `graph_edges_sha256`; network `none`; provider config keys `graph_expansion_hops`, `seed_top_k`, `neighbor_budget`).

**Tests:** `unit_tests/test_rag_graph_ranked_expand_runner.py` (4 cases: seeds then ranked neighbours within the budget, head-of-document without a graph, fixed knobs enforced, passage functions byte-identical to the lexical runner); `unit_tests/test_candidate_retrieval.py` (graph provider admission and graph extraction).

**Source:** `tests/rag_graph_ranked_expand/runner.py`.
