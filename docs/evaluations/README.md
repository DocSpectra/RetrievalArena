# Evaluations

To add a metric or judging surface, follow the [evaluation extension workflow](../extension-evaluations.md).

Every evaluation Retrieval Arena can apply, one row each. Name is the
provider's `tests/<name>/` directory (or the scoring surface's id) and links
its page; "(admitted)" marks the five profiles `candidate-retrieve` admits
(`retrieval_arena/candidate_retrieval.py`). Metric names the ids the row
feeds; Implementation says what it does and where. The ids are defined in
[../retrieval_evaluation.md](../retrieval_evaluation.md).

## Retrieval providers

"ranked v2" is `evidence_recall`, `complete_support`, `ndcg`,
`reciprocal_rank` over the row's `retrieved_context` items (the answer
oracles and no-retrieval baselines write none); BERTScore applies wherever
the caller composes answer pairs from `generated_answer`.

| Name | Metric | Implementation |
| --- | --- | --- |
| [`rag_lexical_topk`](rag_lexical_topk.md) (admitted) | ranked v2; BERTScore over answers | BM25 over whole documents, top-k; `answer_policy` `head_of_document_v1` (default) or `best_passage_v1` (three best passages with `answer_provenance`); `tests/rag_lexical_topk/runner.py` |
| [`rag_graph_ranked_expand`](rag_graph_ranked_expand.md) (admitted) | ranked v2; BERTScore over answers | BM25 seeds (`seed_top_k`) plus a ranked one-hop neighbour budget over `graph_edges.csv` (`neighbor_budget`); the lexical runner's answer policies; `tests/rag_graph_ranked_expand/runner.py` |
| [`rag_llm_guided_search`](rag_llm_guided_search.md) (admitted) | ranked v2; navigation (`success_at_n`, `hops_to_first_support`, `pages_per_success`, `dead_ends`); BERTScore over `agent_answer_v1` answers | bounded link-following navigator from `index.md` (`open`/`back`/`answer`) driven by a digest-pinned local Ollama model over the host relay; `e007b.action_trace.v1` traces; `tests/rag_llm_guided_search/runner.py` |
| [`rag_repo_search_agent`](rag_repo_search_agent.md) (admitted) | ranked v2; search navigation (`landing_hit_rate`, `successes_by_route`, `successes_via_new_link`, `cost_to_first_support`); BERTScore over answers | a landing over the corpus under the request's `landing_policy` (literal whole-question line grep, or the question's content words matched as a set with files ranked by matched-word count — S046b), then `search`/`open`/`back`/`answer` under search, visit and call budgets, links followed; `e007c.action_trace.v1` traces; `tests/rag_repo_search_agent/runner.py` |
| [`rag_repo_search_agent_nolinks`](rag_repo_search_agent_nolinks.md) (admitted) | as `rag_repo_search_agent` | the same image with `follow_links: false` — a grep-only reader; fixed by the provider name |
| [`rag_llm_guided_search_mock`](rag_llm_guided_search_mock.md) | ranked v2 | the navigator scaffold: bounded action controller and `e007b.action_trace.v1` trace shape with a mock or loopback controller, written to `action_traces.jsonl`; `tests/rag_llm_guided_search_mock/runner.py` |
| [`rag_embedding_topk`](rag_embedding_topk.md) | ranked v2 | cosine top-k over embeddings from a local hashing TF-IDF backend or a lock-pinned offline sentence-transformers snapshot; `tests/rag_embedding_topk/runner.py` |
| [`rag_vector`](rag_vector.md) | ranked v2 | minimal example: the single best document by token overlap; `tests/rag_vector/runner.py` |
| [`rag_graph_expand`](rag_graph_expand.md) | ranked v2 | minimal example: the best token-overlap document plus its first neighbour in `graph_edges.csv`; `tests/rag_graph_expand/runner.py` |
| [`rag_graph_rerank`](rag_graph_rerank.md) | ranked v2 | BM25 seeds expanded one hop, BM25 rerank to `final_top_k`; `tests/rag_graph_rerank/runner.py` |
| [`rag_graph_multihop_rerank`](rag_graph_multihop_rerank.md) | ranked v2 | multi-hop frontier search with a distance penalty and reserved deep slots; `tests/rag_graph_multihop_rerank/runner.py` |
| [`rag_directed_spectral_rerank`](rag_directed_spectral_rerank.md) | ranked v2 | BM25 seeds with directed link bonuses plus PageRank and HITS authority/hub priors; `tests/rag_directed_spectral_rerank/runner.py` |
| [`rag_semantic_graph_rerank`](rag_semantic_graph_rerank.md) | ranked v2 | embedding seeds expanded along the graph, distance penalty, optional PageRank prior; `tests/rag_semantic_graph_rerank/runner.py` |
| [`rag_random_seed_graph_traversal`](rag_random_seed_graph_traversal.md) | ranked v2 | randomized-seed traversal baseline over `num_trials` trials from `rng_seed`; `tests/rag_random_seed_graph_traversal/runner.py` |
| [`rag_iterative_search`](rag_iterative_search.md) | ranked v2 | deterministic BM25 search → inspect → follow-link loop under an action budget, `e007a.action_trace.v1` traces; `tests/rag_iterative_search/runner.py` |
| [`oracle_perfect`](oracle_perfect.md) | BERTScore / legacy v1 over answers | the gold answer, no context — the answer ceiling; `tests/oracle_perfect/runner.py` |
| [`oracle_partial`](oracle_partial.md) | BERTScore / legacy v1 over answers | the first three words of the gold answer, no context; `tests/oracle_partial/runner.py` |
| [`oracle_empty`](oracle_empty.md) | BERTScore / legacy v1 over answers | an empty answer, no context — the floor; `tests/oracle_empty/runner.py` |
| [`oracle_graph_support`](oracle_graph_support.md) | ranked v2 | support targets from `faq_support_audit.jsonl` reached within `hop_budget` of BM25 seeds — the graph-support bound; `tests/oracle_graph_support/runner.py` |
| [`llm_only`](llm_only.md) | legacy v1 (`lexical_baseline`) in `configs/e001_llm_only_minidocs.yaml` | no-retrieval answer baseline with a dummy fixed answer; `tests/llm_only/runner.py` |
| [`local_no_context_llm_e001b`](local_no_context_llm_e001b.md) | legacy v1 / BERTScore over answers | no-context answer baseline through a loopback-only OpenAI-compatible gate (mock by default); `tests/local_no_context_llm_e001b/runner.py` |

## Scoring and diagnostics

| Name | Metric | Implementation |
| --- | --- | --- |
| [`score_ranked`](score_ranked.md) | `evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`; aggregates `macro_evidence_recall`, `macro_complete_support`, `macro_ndcg`, `mrr` | `retrieval_arena/ranked_scoring.py`: strict qrels/run/policy artifacts, arithmetic delegated to `ir-measures==0.4.3`; `python -m retrieval_arena.cli score-ranked`, or `evaluate-profile` through the operator |
| [`pooled_support`](pooled_support.md) | pooled, LLM-verified qrels (`qrels_scope: pooled`, binary) for `score_ranked`; spot-check precision and miss rate with Wilson intervals | `retrieval_arena/support/{pooling,verification,review}.py`: passage BM25 and passage embeddings against the reference answer plus the top pages of evaluated runs, one hosted-LLM verifier call per window under a frozen prompt, resumable and incrementally extendable; `support pool|verify|review-packet|import-review`, operator `containers/support/support_operator.py` via `scripts/run_support.py` (C2) |
| [`score_ragas`](score_ragas.md) | Custom fixed-element completeness, contradiction counts; optional built-in faithfulness, separately | Optional pinned Ragas Docker judge; local Ollama or hosted Anthropic/OpenAI judge via the [model provider layer](../model_providers.md) (config v4, `external_api`); versioned corpus/rubric joins, per-element spans, cached scoring and offline replay; `scripts/run-ragas.ps1` |
| [`score_bertscore`](score_bertscore.md) | `precision`, `recall`, `f1` per query (`bertscore_precision`, `bertscore_recall`, `bertscore_f1` as profile families) | provider-pinned offline BERTScore over answer pairs; `retrieval_arena/dual_profile.py::run_bertscore_offline`, operator `-Command score-bertscore` |
| [`measure_docstyle`](measure_docstyle.md) | the DocEng 2026 Appendix B per-document metrics (35 columns: file, Markdown structure, text volume, vagueness / constraint density, spaCy basic NLP, NER, POS) plus per-corpus summaries, the four-signal Spearman matrix and support-page percentile ranks; S055 adds the identifier view, the rule-based identifier counter and the repository-grounded lexicon mention counter (`repo_lexicon_v1`, harvested from the candidate on request) | deterministic measurement over a candidate repository's `.md` documents with `en_core_web_sm` 3.8.0 inside the image; `retrieval_arena/docstyle_measurement.py`, operator `-Command measure-docstyle` (S052) |
| [`evaluate_profile`](evaluate_profile.md) | the families `operational_automatic` and `scientific_calibration`; diagnostics `requirement_coverage`, `support_lineage` | `retrieval_arena/dual_profile.py` with `retrieval_arena/profile_evaluation.py`; no composite score |
| [`navigation_metrics`](navigation_metrics.md) | `success_at_n`, `hops_to_first_support`, `pages_per_success`, `dead_ends`, `opens`, `model_calls` | `scripts/navigation_metrics.py` over `traces/*.jsonl` (`e007b.action_trace.v1`) plus the judge's support file |
| [`search_navigation_metrics`](search_navigation_metrics.md) | the navigation set plus `landing_hit_rate`, `successes_by_route`, `successes_via_new_link`, `cost_to_first_support` | `scripts/search_navigation_metrics.py` over `e007c.action_trace.v1` traces, optional baseline graph |
| [`corpus_reachability`](corpus_reachability.md) | `fraction_within_{1,2,3,8}_hops`, `median_hops`, `orphan_fraction`, `questions_with_target_within_3_hops_fraction`, `support_class_mix` | `retrieval_arena/support/reachability.py`, at import (`build_reachability_surface`) or `corpus reachability --dataset` |
| [`structural_connectivity_constraint_v2`](structural_connectivity_constraint_v2.md) | admission verdict `passed` with reasons `C0`, `C1`, `C1b`, `C2` (C v1 plus two config-declared relaxations: C2 joined pages, C1b templated pages); not a retrieval score | `retrieval_arena/graph/structural_target_v2.py`, a standalone function over a variant link graph and a frozen `structural_target.baseline_summary`; C v1 (`structural_target.py`) unchanged |
| [`lexical_baseline`](lexical_baseline.md) | `precision`, `recall`, `f1` (legacy v1) | `retrieval_arena/scoring.py`, the `scoring.method` of the experiment YAMLs |
