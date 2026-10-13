# Corpus import and dataset preparation

For adding a source or transform, read the [dataset extension workflow](extension-datasets.md).
Preparation currently copies documents without cleaning or chunking; question
sourcing and independent judgment are separate inputs.

How a documentation corpus becomes a dataset a provider can read. The
**experiment lane** (`retrieval_arena/corpus/*.py`, `retrieval_arena/support/*.py`)
imports an upstream revision and prepares a dataset with questions, answers,
a link graph and a support surface; the **candidate lane** (`candidate-retrieve`,
`retrieval_arena/candidate_retrieval.py`) stages a candidate repository's
corpus directly for one provider under declared document-id and exclude
policies. Remote acquisition and manifests: [remote_acquisition.md](remote_acquisition.md),
[manifest_lineage.md](manifest_lineage.md).

## The experiment lane

The chain, in call order (`retrieval_arena/corpus/comparison.py::run_corpus_snapshot_comparison`
runs it per snapshot of a comparison plan):

1. `corpus/importers.py::import_corpus_source` — copies the files a
   `CorpusSourceDescriptor` selects (`source_type` `local` or `git` at an
   explicit `requested_ref`, `include` globs) into an import workspace with
   `retrieval_arena.corpus_import_manifest.v1`; `python -m retrieval_arena.cli
   corpus acquire --descriptor <json> --transport local-only|host-network`
   acquires a remote Git snapshot the same way (`corpus/acquisition.py`).
2. `corpus/dataset_export.py::prepare_dataset` — writes the dataset directory
   from the import manifest, the questions and the answers, with
   `dataset_preparation_manifest.json`.
3. `dataset_export.py::build_graph_transformation` — extracts the Markdown
   link graph to `graph_edges.csv` (`source,target`) with `graph_metrics.json`
   and its manifest.
4. `dataset_export.py::build_support_surface` — labels each question's
   support documents from the answers (`support/surface.py`, method
   `idf_weighted_answer_support_v1`, thresholds 0.6 / 0.25) into
   `faq_support_audit.jsonl` and `support_construction_manifest.json`.
5. `dataset_export.py::build_reachability_surface` — the S036 support
   reachability surface (`support/reachability.py`), also standalone as
   `python -m retrieval_arena.cli corpus reachability --dataset <dir>`.

There is no `corpus import` or `corpus prepare` verb: steps 1–5 run inside
`corpus compare-snapshots --plan <plan.json>` (and the BigData pilot orchestrator);
`corpus acquire` and `corpus reachability` stand alone. `unit_tests/test_corpus_import.py` exercises the chain.

## The dataset layout a provider consumes

```text
<dataset>/corpus/<doc>.md      questions.jsonl   answers.jsonl
          graph_edges.csv      graph_metrics.json
          faq_support_audit.jsonl   support_reachability.v1.json   *_manifest.json
```

The harness (`retrieval_arena/harness.py`) stages `corpus/`, `questions.jsonl`,
`answers.jsonl`, `graph_edges.csv`, `graph_metrics.json` and
`faq_support_audit.jsonl` (each when present) into the provider's `/input`,
plus `config.yaml` holding the test's `config` block as JSON. The document id
in this lane is the corpus-relative path without suffix.

## The candidate lane: ids and exclusion

`candidate-retrieve` reads the candidate repository at an exact commit and
stages only `corpus/<stem>__<sha1[:8]>.md`, `questions.jsonl`, `config.yaml`
and, for a `requires_graph` provider, a freshly extracted `graph_edges.csv`
(never answers or support); the operator side is [operator.md](operator.md),
the shapes `configs/candidate_retrieval*.example.json`. The request's
`corpus` block declares:

- `root` — the corpus subdirectory of the candidate (for example `docs`);
  `extensions` must be `[".md"]`;
- `import_policy` — the document-id rule (`imported_document_name`):
  `cikm_flat_path_sha1_v1` hashes the corpus-relative path as written,
  `cikm_flat_path_sha1_lower_v1` (S044) hashes it lowercased — the rule of
  the CIKM dataset importer that produced the support ids, so candidate
  and support ids join under one rule. A corpus whose paths differ only by
  case is refused (`candidate-retrieval-document-id-collision`), never merged;
- `exclude_policy` — `cikm_faq_pattern_v1` (S047, `excluded_by_policy`):
  a page whose lowercased path matches `*faq*` or carries `faq` in any
  path part is neither copied into the provider input nor a node of the
  extracted graph; the launch record and the operator receipt carry
  `corpus_exclude_policy`, `excluded_document_count` and
  `excluded_document_ids`. A request whose `experiment_id` starts with
  `E-EXP` is refused without it (`candidate-retrieval-exclude-policy-required`).
  The rule of record is the DocSpectra Capstone exporter config
  (`configs/pinned_cikm_exporter.yaml` there); the rule reads paths, never content;
- `exclude_policy` `cikm_faq_pattern_v2` with `faq_patterns` (S023): the CIKM
  exporter's whole page rule with the corpus's own FAQ pattern list declared
  on the request (the `faq_patterns` of its CIKM corpus config, for example
  `["*faq*", "*frequently*"]` for `elasticsearch_docs`). A page is excluded
  when its lowercased corpus-relative path or its lowercased file name matches
  any declared pattern, when `faq` occurs in any path part, or when any
  directory on its path starts with `_` other than `_sources` (the exporter's
  `discover_markdown_pages` skip rule: `_snippets` fragments are not pages).
  `faq_patterns` is required under v2 and refused under v1; the launch record
  and the receipt carry it as `corpus_faq_patterns`.
