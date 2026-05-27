# RetrievalArena

RetrievalArena is a replayable observability harness for retrieval over
evolving documentation ecosystems. It imports explicit source revisions,
prepares dataset contracts, runs selected retrieval families, writes provenance
and replay manifests, compares drift, audits regressions, measures artifact
footprint, and emits user-facing reports.

The BigData 2026 pipeline is implemented on branch
`IEEEBigData2026_metrics`.

## Quick Start

```powershell
pip install -e .
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --dry-run
python -m retrieval_arena.cli report html --bundle calibration\bigdata2026\pilot_express_docs
```

Open the generated report at:

```text
calibration/bigdata2026/pilot_express_docs/observability_report.html
```

## BigData User Docs

- [Run BigData pilots](docs/bigdata_pilots.md)
- [Use the HTML observability report](docs/html_observability_report.md)
- [Export paper evidence](docs/paper_evidence_export.md)
- [Refresh an existing baseline](docs/incremental_baseline_refresh.md)
- [Inspect manifest lineage](docs/manifest_lineage.md)
- [Architecture diagram](docs/architecture.mmd)

## Selected Calibration Bundles

- `calibration/bigdata2026/pilot_express_docs/`
- `calibration/bigdata2026/pilot_docker_docs/`
- `calibration/bigdata2026/pilot_kubernetes_website_docs/`

These bundles are reviewable evidence packages. They include manifests,
snapshot diffs, retrieval drift reports, regression audits, systems
measurements, replay references, and generated reports. They intentionally
exclude raw source trees, copied corpora, copied question/answer files,
predictions, and item scores unless explicitly approved.

## Core Commands

```powershell
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --dry-run
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --refresh-baseline
python -m retrieval_arena.cli report html --bundle calibration\bigdata2026\pilot_express_docs
python -m retrieval_arena.cli report evidence --bundle calibration\bigdata2026\pilot_express_docs --out-dir ..\IEEEBigData2026_paper\evidence\bigdata2026\express_docs
python -m unittest discover -s unit_tests
```

Use `python -m retrieval_arena.cli validate --config <config>` for the
deterministic output-contract gate on standard RetrievalArena experiments.
