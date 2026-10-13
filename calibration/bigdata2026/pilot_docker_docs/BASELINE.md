# BigData Pilot Baseline: pilot_docker_docs_2026_05_01_to_2026_05_26

This calibration store contains the current selected BigData pilot baseline.
Raw source trees, copied corpus content, and copied query/answer text are excluded by policy.
No efficiency score, overall scale score, or composite systems metric is included.

## S017 Second Corpus Baseline

This baseline adds Docker docs as the second real documentation ecosystem for
the BigData observability pipeline. It compares Docker docs commit
`274681c49355bd951ae60c9208e854ff70f12474` from 2026-05-01 with
`76547a84dfd051bf751fa1366e2346451c2c8915` from 2026-05-26.

The snapshot pair produced 1,238 before pages and 1,242 after pages, with 5
added files, 1 removed file, 185 changed files, 46 added graph edges, 3 removed
graph edges, and 11 changed support questions.

## Family Matrix

The baseline runs the same six shared retrieval families used by the
Express family-matrix baseline:

- `rag_lexical_topk`
- `rag_vector`
- `rag_graph_rerank`
- `oracle_graph_support`
- `rag_graph_multihop_rerank`
- `rag_iterative_search`

Each comparison covers 144 Docker pilot queries and includes replay manifests,
scores, metadata, retrieval drift, regression audit, and systems measurement
artifacts. The lexical top-k family shows retrieval drift over the Docker
snapshot pair, while the graph and deterministic family runs provide
cross-family observability controls over the same corpus changes.
