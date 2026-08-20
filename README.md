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

- [Ranked-retrieval metrics v2 plan](docs/ranked_retrieval_metrics_v2_plan.md)
- [Dual-profile ranked evaluation](docs/dual_profile_evaluation.md)
- [Portable deterministic task-completion evaluation](docs/task_completion_evaluation_plan.md)
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

Canonical qualification runs use the repository-owned Linux verification
image, not host Python. Build once, then run either registered tier:

```powershell
.\scripts\build-verification.ps1
.\scripts\run-verification.ps1 focused
.\scripts\run-verification.ps1 all
```

Linux and macOS hosts use the equivalent `scripts/build-verification.sh` and
`scripts/run-verification.sh` adapters. Both adapters run the same immutable
image with the repository mounted read-only, runtime networking disabled, a
read-only container root, and only declared tmpfs test scratch writable. The
CPU image targets `linux/amd64`; later Spark or NVIDIA profiles should derive
from this verification contract without changing the baseline test semantics.

Ranked-retrieval v2 uses strict qrels, run, and policy artifacts and delegates
standard arithmetic to pinned `ir-measures==0.4.3`:

```powershell
python -m retrieval_arena.cli score-ranked --dataset <dataset.json> --qrels <qrels.jsonl> --predictions <predictions.jsonl> --policy <policy.json> --out-dir <output>
python -m retrieval_arena.cli evaluate-profile --config <dual-profile.json> --out-dir <output>
python -m retrieval_arena.cli score-bertscore --provider <provider.json> --answer-pairs <answers.jsonl> --out <scores.json>
python -m retrieval_arena.cli export-review-packet --candidates <candidates.jsonl> --policy <review-policy.json> --out <packet.json>
python -m retrieval_arena.cli import-judgments --judgments <judgments.json> --packet <packet.json> --policy <review-policy.json> --out <import.json>
```

The primary result is macro evidence Recall@k at the registered model
context-item budget. See the specification's
[plain-language metric guide and comparison](docs/ranked_retrieval_metrics_v2_plan.md#metric-guide)
before the formal contracts and formulas.

Dual-profile evaluation preserves that ranked-v2 scorer and adds a zero-human
`operational_automatic` family plus an optional artifact-only
`scientific_calibration` family. Requirement coverage and support lineage are
explicitly project-specific diagnostics. Provider-pinned offline BERTScore is
an optional answer diagnostic; calibration-only BPref and human answer or
attribution judgments never enter an automatic result or a composite. See the
[operator contract](docs/dual_profile_evaluation.md).

Use `python -m retrieval_arena.cli validate --config <config>` for the
deterministic output-contract gate on standard RetrievalArena experiments.
