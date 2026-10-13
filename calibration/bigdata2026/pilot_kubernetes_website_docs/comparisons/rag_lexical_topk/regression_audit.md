# Regression Audit

- Queries audited: 24
- Queries with labels: 24
- Overall drift score: not computed

## Cause Labels

- `action_trace_changed`: 9
- `answer_lexical_score_regressed`: 5
- `distance_to_support_increased`: 1
- `evidence_unavailable`: 24
- `rank_changed`: 6
- `replay_manifest_changed`: 24
- `retrieval_config_changed`: 24
- `retrieval_score_changed`: 24
- `retrieved_candidate_changed`: 9

## Evidence Availability

- `action_trace`: 24 available / 0 unavailable
- `distance_to_support`: 24 available / 0 unavailable
- `evidence_coverage`: 24 available / 0 unavailable
- `graph`: 0 available / 24 unavailable
- `lexical_answer_score`: 24 available / 0 unavailable
- `rank`: 24 available / 0 unavailable
- `replay_manifest`: 24 available / 0 unavailable
- `retrieval_candidates`: 24 available / 0 unavailable
- `retrieval_config`: 24 available / 0 unavailable
- `retrieval_score`: 24 available / 0 unavailable
- `scoring_config`: 24 available / 0 unavailable
- `snapshot_diff`: 0 available / 24 unavailable
- `support_exposure`: 24 available / 0 unavailable
- `support_recall`: 24 available / 0 unavailable
- `support_surface`: 0 available / 24 unavailable

## Query Candidates

- `kubernetes_website_docs_faq_0001`: `distance_to_support_increased`, `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0002`: `evidence_unavailable`, `rank_changed`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0003`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0004`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0005`: `action_trace_changed`, `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0006`: `action_trace_changed`, `answer_lexical_score_regressed`, `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0007`: `action_trace_changed`, `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0008`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0009`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0010`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0011`: `evidence_unavailable`, `rank_changed`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0012`: `action_trace_changed`, `answer_lexical_score_regressed`, `evidence_unavailable`, `rank_changed`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0013`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0014`: `action_trace_changed`, `evidence_unavailable`, `rank_changed`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0015`: `action_trace_changed`, `answer_lexical_score_regressed`, `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0016`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0017`: `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0018`: `action_trace_changed`, `answer_lexical_score_regressed`, `evidence_unavailable`, `rank_changed`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
- `kubernetes_website_docs_faq_0019`: `evidence_unavailable`, `rank_changed`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`
- `kubernetes_website_docs_faq_0020`: `action_trace_changed`, `answer_lexical_score_regressed`, `evidence_unavailable`, `replay_manifest_changed`, `retrieval_config_changed`, `retrieval_score_changed`, `retrieved_candidate_changed`
