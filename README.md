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
- [Task-completion contracts and pure scoring](docs/task_completion_contracts.md)
- [Run BigData pilots](docs/bigdata_pilots.md)
- [Use the HTML observability report](docs/html_observability_report.md)
- [Export paper evidence](docs/paper_evidence_export.md)
- [Refresh an existing baseline](docs/incremental_baseline_refresh.md)
- [Inspect manifest lineage](docs/manifest_lineage.md)
- [Acquire immutable remote Git sources](docs/remote_acquisition.md)
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

The same registered operator family also exposes the cross-repository
dual-profile ingress. `input-root` is an immutable bundle containing an
`operator-request.json` and its exactly hashed files; `output-root` is a fresh
directory containing only `.retrieval-arena-output-owner` with the literal
`retrieval_arena.output_root.v1` marker. The command validates the self-hashed
image receipt and Docker labels before execution, mounts source/input
read-only, disables networking, and writes only result artifacts plus a
self-hashed terminal receipt to the output root:

```powershell
.\scripts\run-verification.ps1 evaluate-profile <input-root> <output-root> <image-receipt>
```

The same immutable entrypoint exposes a generic review-packet boundary:

```powershell
.\scripts\run-verification.ps1 review-packet <input-root> <output-root> <image-receipt>
```

Its `operator-request.json` is closed-world
`retrieval_arena.review_packet_operator_request.v1` with exactly
`schema_version`, `command` (`review-packet`), `candidates_path`,
`policy_path`, `judgment_artifact_path` (nullable), `input_inventory`, and
`input_inventory_sha256`. `candidates_path` is UTF-8 JSONL whose records use
the existing `retrieval_arena.review_candidate.v1` schema with exactly
`schema_version`, `query_id`, `support_target_id`, `snapshot_id`,
`candidate_class`, `rank_band`, `query_text`, `requirement_texts`,
`candidate_evidence`, `bounded_context`, `condition_label`, `system_label`,
`outcome_label`, `rank`, `delta`, `lineage_class`, and
`candidate_provenance_sha256`; `policy_path` is a strict
`retrieval_arena.review_policy.v1` document. The input inventory lists
every input other than the request. A fresh owned output receives
`review_packet.v1.json` and, when a judgment artifact is supplied,
`judgment_import.v1.json`, plus the self-hashed terminal
`retrieval_arena.review_packet_operator_receipt.v1`. The receipt binds the
request, input and output inventories, RA commit/tree/image/lock identity,
isolation, and disposition. Export and optional import are staged together:
an import failure leaves no packet/import artifact (only its terminal receipt).
Project-specific candidate fixtures and expected packet bytes remain owned by
the calling project. `review_packet.v1.json` is canonical compact UTF-8 JSON
plus one trailing LF, so the caller must compare its raw bytes directly to the
tracked fixture rather than compare parsed objects.

On a green `focused` invocation the final stdout line is the canonical,
self-hashed `retrieval_arena.focused_verification_witness.v1`. It binds the
mounted commit/tree/image/lock identity, `focused_suite_success`, and the
exact `focused_tests_run` count. It separates RA runtime observations from
Capstone-owned negative-case observations; consumers must not parse unittest
prose as verification evidence.

POSIX hosts use `scripts/run-verification.sh evaluate-profile <input-root>
<output-root> <image-receipt>`. The request schema is
`retrieval_arena.profile_evaluation_request.v1`; it has closed fields
`schema_version`, `command`, `config_path`, `input_inventory`, and
`input_inventory_sha256`. The inventory excludes the request itself and must
exactly enumerate every other regular input file. The terminal
`operator_receipt.v1.json` is operational evidence, not evaluator result
truth.

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

Portable task completion is a fourth, separate result family. The 34A library
surface admits closed-world task packs, task definitions, trial identities,
normalized events, oracle results, efficiency evidence, scores, paired
comparisons, and replay manifests. `score_task` is pure: it consumes admitted
records and never launches an agent, provider, service, model, or plugin.
Correctness and critical safety precede efficiency, and failed or invalid
trials retain their resource evidence without being allowed to outrank safe
successes. See the [pure scoring contract](docs/task_completion_contracts.md).

The corrected 34B execution layer derives provider identity from exact admitted
source bytes, executes registered entrypoints under capability and writable-root
enforcement, and creates harness-owned passed or failed native receipts. A
`finally`-owned cleanup attempt and standalone terminal-failure record preserve
failure resources. Passed receipts require exact operation-result schemas;
lifecycle dispositions come from observed phase states; and replay request and
observation payloads are reconstructed against their source bundle. Replay
invokes only the oracle and has its own receipt. The layer has no live model,
runtime network, real service, ambient caller callable, or corpus dependency.
Use `python -m
retrieval_arena.cli task --help` for pack validation, non-consuming planning,
synthetic execution, scoring, comparison, reporting, and replay. See the
[execution contract](docs/task_execution_contracts.md).

Use `python -m retrieval_arena.cli validate --config <config>` for the
deterministic output-contract gate on standard RetrievalArena experiments.
