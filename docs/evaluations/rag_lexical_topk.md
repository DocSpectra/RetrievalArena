# `rag_lexical_topk`

BM25 over whole documents, top-k, with two deterministic answer policies: `head_of_document_v1` (default) answers with the head snippet of each retrieved document, `best_passage_v1` keeps the ranking and answers with the three best BM25 passages of the top-k documents in document order, with `answer_provenance` (document id, nearest preceding heading, passage index, character span). No model.

**Input:** `/input/questions.jsonl`, `/input/corpus/**/*.md` (document id = corpus-relative path without suffix), `/input/config.yaml`. No graph.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json`; items carry `source: lexical_topk` and `rank`; `answer_provenance` under `best_passage_v1`; metadata records `ranker: bm25`, `top_k`, `max_context_chars_per_doc`, `answer_policy`.

## Knobs

| Key | Default |
| --- | --- |
| `top_k` | `5` |
| `max_context_chars_per_doc` | `1500` |
| `answer_policy` | `head_of_document_v1` (or `best_passage_v1`; anything else is rejected) |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context`; BERTScore over `generated_answer` when the caller composes pairs.

**Admission:** admitted at `candidate-retrieve` (`ProviderProfile` in `retrieval_arena/candidate_retrieval.py`, image `retrieval-arena/rag-lexical-topk@sha256:…`, `requires_graph` false, network `none`); the EXP-1 flat retriever.

**Tests:** `unit_tests/test_rag_lexical_topk_runner.py` (8 cases: passage splitting and spans, best-passage selection, byte-identical runs, legacy-row reproduction, unknown policy rejected); `unit_tests/test_candidate_retrieval.py` projections; `unit_tests/test_bigdata_pilot.py`, `test_html_report.py` use it as the pilot family.

**Source:** `tests/rag_lexical_topk/runner.py`; `rag_graph_ranked_expand` and the navigators copy its snippet and passage functions byte for byte (asserted by their tests).
