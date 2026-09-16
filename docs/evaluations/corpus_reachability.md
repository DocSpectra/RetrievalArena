# `corpus_reachability`

Support reachability surface (S036): how far the support set is from the root. Computed at import from the corpus, the directed link graph, the question set and the support audit alone — no answer text, no model, no retrieval run — so a dataset states, before any provider runs, whether its support pages can be reached by following links from `index`.

**Input:** A prepared dataset directory: `corpus/`, `graph_edges.csv`, `questions.jsonl`, `faq_support_audit.jsonl` (target ids and `support_label`; never `answers.jsonl`). A missing input gives `status: unavailable` with the input named.

**Output:** `support_reachability.v1.json` (`retrieval_arena.support_reachability.v1`: `root_doc_id`, `graph`, per-target degrees, size and hops from the root, per-question minimum hops, and the aggregate) plus `support_reachability_manifest.json` (`retrieval_arena.support_reachability_manifest.v1`, method `support_reachability_v1`, file hash).

## Metric ids

Aggregate: `fraction_within_1_hops`, `_2_`, `_3_`, `_8_` (and `_undirected` twins), `median_hops`, `median_hops_undirected`, `targets_reachable`/`targets_unreachable`, `orphan_fraction`, `questions_with_target_within_3_hops_fraction`, `questions_all_targets_orphan_fraction`, `support_class_mix`, `support_fraction_of_corpus`.

**Invocation:** At import: `retrieval_arena/corpus/dataset_export.py::build_reachability_surface` after `build_support_surface`; standalone: `python -m retrieval_arena.cli corpus reachability --dataset <dir> [--query-set-id <id>]`.

**Tests:** `unit_tests/test_reachability_surface.py` (in the `focused` suite).

**Source:** `retrieval_arena/support/reachability.py`; `retrieval_arena/corpus/dataset_export.py`.
