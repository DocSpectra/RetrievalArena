# BigData Pilot Baseline: pilot_express_docs_2025_09_20_to_2026_03_30

This calibration store contains the current selected BigData pilot baseline.
Raw source trees, copied corpus content, and copied query/answer text are excluded by policy.
No efficiency score, overall scale score, or composite systems metric is included.

## S016 Family Matrix

This baseline wires six CIKM-derived retrieval surfaces as BigData observed
systems over the Express 2025-09-20 to 2026-03-30 snapshot pair:

- `rag_lexical_topk`
- `rag_vector`
- `rag_graph_rerank`
- `oracle_graph_support`
- `rag_graph_multihop_rerank`
- `rag_iterative_search`

Each comparison includes drift, audit, systems measurements, replay manifests,
scores, and metadata for the before and after runs. The full CIKM family is
mapped in `git/context/cikm_family_observability_matrix.md`; semantic MiniLM and
provider-style variants are deferred until their model/provider prerequisites
are promoted into the BigData pilot contract.
