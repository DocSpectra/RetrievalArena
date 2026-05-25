# RetrievalArena

RetrievalArena is a dependency-light observability harness for documentation
retrieval experiments. It can import explicit documentation source revisions,
prepare RetrievalArena dataset contracts, run Dockerized retrieval/generation
tests, validate outputs, compute deterministic scores, and compare snapshot or
replay artifacts through manifest-backed reports.

## Quick start

```cmd
pip install -e .
retrieval-arena run --config configs\experiment.example.yaml
retrieval-arena validate --config configs\experiment.example.yaml
```

The example run writes:

```text
results/toy_faq_reconstruction/summary.csv
results/toy_faq_reconstruction/summary.md
```

See the parent workspace specification at `project_specifications/RetrievalArenaSpec.md` for the v1 contract and validation plan.

## Source-to-snapshot comparison

Committed corpus source descriptors live under:

```text
configs/corpus_sources/
```

Each descriptor names a corpus, source type, source path or URL, requested Git
ref, docs root, include/exclude patterns, snapshot ID, and ignored destination
workspace. Generated source imports are written under `raw/`, which is ignored
by Git.

Committed snapshot comparison plans live under:

```text
configs/corpus_comparisons/
```

A comparison plan names two source descriptors and an ignored report directory.
The command below imports both snapshots with reuse enabled, prepares
corpus-only datasets, writes corpus snapshot manifests, and runs snapshot diff:

```cmd
python -m retrieval_arena.cli corpus compare-snapshots --plan configs\corpus_comparisons\express_docs_2025_09_20_to_2026_03_30.json
```

The generated comparison report is written under `reports/`, which is also
ignored by Git. The Express example compares:

- `express_docs` at `74e27ec71d391c89829076063f4f5c29e43c6c9c`
- `express_docs` at `2d4df3f05fbd5d5f211f5938395ae965e8714d60`

Use `overwrite_imports: false` in comparison plans to reuse existing imports
when the import manifest, selected documents, requested ref, and clean Git
source still match.

## Architecture

The high-level BigData observability flow is documented as a Mermaid diagram in
[docs/architecture.mmd](docs/architecture.mmd).

## Output contract validation

`retrieval-arena validate` runs the deterministic Phase 1 gate and negative
contract checks before results should be trusted for analysis. Container outputs
fail validation on invalid JSONL, duplicate predictions, unknown question IDs,
missing required prediction fields, mismatched question text for a claimed
`question_id`, missing predictions, or incomplete `metadata.json`.
