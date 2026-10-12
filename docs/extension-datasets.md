# Importing datasets and extending preprocessing

Start at [Extending RA](extending.md). Details: [corpus import](corpus_import.md), [remote acquisition](remote_acquisition.md), [manifest lineage](manifest_lineage.md).

## Existing import path

1. Declare a source using `corpus/sources.py::CorpusSourceDescriptor`: corpus/snapshot IDs, `source_type` (`local` or `git`), source path/URL, requested Git ref, `docs_root`, include/exclude patterns and destination workspace. Resolve to an exact revision for a frozen study. Examples live in [configs/corpus_sources](../configs/corpus_sources).
2. Acquire/import with `corpus/acquisition.py` or `corpus/importers.py::import_corpus_source`. `corpus acquire --descriptor <json> --transport local-only|host-network` is the acquisition CLI; network acquisition has its own descriptor and receipt validation. It is not a web-question crawler.
3. Supply questions and reference answers separately. `dataset_export.py::prepare_dataset` copies imported documents and optional question/answer files. It records `copy_imported_documents`, `one_file_per_document`, and `chunking: none`. There is no general cleaning, HTML conversion, FAQ extraction, question generation or chunker hidden in this step.
4. Build graph and support stages as required. `build_graph_transformation` extracts Markdown links or consumes supplied edges. `build_support_surface` consumes supplied records or derives answer-overlap support; `build_reachability_surface` measures reachability. Check whether stages are enabled: they are not unconditional in comparison plans. Generated overlap labels are annotation candidates, not independent gold judgments.
5. Validate IDs, retained/excluded files, questions/answers, graph endpoints and support joins, then retain manifests and source/config hashes. `schemas.py::validate_dataset` requires corpus, questions and answers, checks matching unique question IDs and graph headers; it does not prove answerability or complete graph/support referential integrity.

The implemented orchestration CLI is `corpus compare-snapshots --plan <json>`; see [the comparison example](../configs/corpus_comparisons/express_docs_2025_09_20_to_2026_03_30.json). Questions and answers must be supplied together there. `corpus acquire` and `corpus reachability` also stand alone. There is no `corpus import` or `corpus prepare` CLI verb. Single-snapshot preparation is available as Python functions; add an explicit interface only if the intended experiment needs one, rather than inventing a command or disguising a single import as a scientific comparison.

Use a fresh generated destination: `prepare_dataset` removes an existing output directory before copying documents. Do not target manuscript, source, original corpus or retained evidence directories.

## Candidate import is a different contract

The [candidate lane](corpus_import.md) stages a pinned repository's Markdown under a declared corpus root, document-ID rule and exclusion policy. It withholds answers/support from the provider and extracts a graph when required. Extend its validation and staging in `candidate_retrieval.py` if that path needs a new import policy; changing `prepare_dataset` does not change candidate import.

Experiment preparation records corpus-relative paths without suffix as IDs; candidate import flattens paths into stem/hash IDs under a named case-sensitive or lowercase hashing policy. Providers also have their own corpus loaders: verify nested-path handling against the chosen runner. Do not assume CIKM IDs, imported path IDs, candidate IDs and external question citations join without an explicit checked mapping. Path exclusions must also apply consistently to graph nodes and support references.

## New preprocessing or external questions

Historical CIKM exports are not produced by RA's `prepare_dataset` copy stage.
They were made by the separate CIKM `docspectra_importer/retrieval_arena_export.py`,
which discovers additional page formats, normalizes RST/HTML, extracts FAQ pairs,
excludes FAQ pages, and assigns flattened path/hash IDs. When recovering one,
pin that exporter and its source root as well as the source revision. A later
RA descriptor for the same Git revision can select a different subtree and
retain FAQ pages. Match question/answer identities and retained provider inputs;
equal question counts alone do not establish equal corpora or exclusions.

For a new document format, cleaning transform or chunker, choose an explicit deterministic stage between acquired source and evaluated corpus. Record its version, settings, source-to-output ID mapping and content hashes. Update preparation manifests, graph extraction and support joins to match the resulting granularity. Test small fixtures for stable IDs, exclusions, links, collisions and replay; existing anchors are `unit_tests/test_corpus_import.py`, `test_remote_acquisition.py` and `test_candidate_retrieval.py`. Do not silently change `copy-v1` semantics.

For externally sourced questions, retain acquisition provenance separately, map accepted items to stable `question_id`/`question` records, and supply references and independently checked support on the judging side. Check duplicates, corpus-version answerability, held-out source exclusions and split rules before freezing a sample. RA's import functions consume these files; they do not validate an upstream crawler's question quality. Pipeline availability and the decision to adopt its output belong in the consuming project's context.
