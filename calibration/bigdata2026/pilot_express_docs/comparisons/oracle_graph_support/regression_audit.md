# Regression Audit

- Queries audited: 8
- Queries with labels: 8
- Overall drift score: not computed

## Cause Labels

- `corpus_page_changed`: 3
- `graph_edge_removed`: 3
- `replay_manifest_changed`: 8
- `retrieval_config_changed`: 8
- `support_target_added`: 3
- `support_target_changed`: 3
- `support_target_removed`: 3

## Evidence Availability

- `action_trace`: 8 available / 0 unavailable
- `corpus`: 8 available / 0 unavailable
- `distance_to_support`: 8 available / 0 unavailable
- `evidence_coverage`: 8 available / 0 unavailable
- `graph`: 8 available / 0 unavailable
- `lexical_answer_score`: 8 available / 0 unavailable
- `rank`: 8 available / 0 unavailable
- `replay_manifest`: 8 available / 0 unavailable
- `retrieval_candidates`: 8 available / 0 unavailable
- `retrieval_config`: 8 available / 0 unavailable
- `retrieval_score`: 8 available / 0 unavailable
- `scoring_config`: 8 available / 0 unavailable
- `snapshot_diff`: 8 available / 0 unavailable
- `support_exposure`: 8 available / 0 unavailable
- `support_recall`: 8 available / 0 unavailable
- `support_surface`: 8 available / 0 unavailable

## Query Candidates

- `express_docs_faq_0001`: `replay_manifest_changed`, `retrieval_config_changed`
- `express_docs_faq_0002`: `replay_manifest_changed`, `retrieval_config_changed`
- `express_docs_faq_0003`: `replay_manifest_changed`, `retrieval_config_changed`
- `express_docs_faq_0004`: `corpus_page_changed`, `graph_edge_removed`, `replay_manifest_changed`, `retrieval_config_changed`, `support_target_added`, `support_target_changed`, `support_target_removed`
- `express_docs_faq_0005`: `corpus_page_changed`, `graph_edge_removed`, `replay_manifest_changed`, `retrieval_config_changed`, `support_target_added`, `support_target_changed`, `support_target_removed`
- `express_docs_faq_0006`: `replay_manifest_changed`, `retrieval_config_changed`
- `express_docs_faq_0007`: `corpus_page_changed`, `graph_edge_removed`, `replay_manifest_changed`, `retrieval_config_changed`, `support_target_added`, `support_target_changed`, `support_target_removed`
- `express_docs_faq_0008`: `replay_manifest_changed`, `retrieval_config_changed`
