# Adding experiments and providers

Start at [Extending RA](extending.md). Detailed contracts: [retrieval evaluation](retrieval_evaluation.md), [provider catalog](evaluations/README.md), [operator](operator.md).

## Existing providers: choose the execution path first

| Path | Entry point | What it actually does |
| --- | --- | --- |
| Experiment YAML | `python -m retrieval_arena.cli run --config configs/<experiment>.yaml` | Runs each dataset/provider pair through `harness.py`; validates legacy predictions and computes lexical answer-overlap scores |
| Candidate operator | `scripts/run-verification.ps1 -Command candidate-retrieve` with named input/output/candidate/receipt parameters | Admits one declared profile, stages a pinned candidate corpus without answers/support, validates output and publishes strict ranked-v2 predictions |

These are interfaces, not a ready-made experiment command: first resolve inputs, images, output directories and the applicable runtime policy. Only five candidate profiles are present at the audited baseline: lexical top-k, graph ranked expansion, link navigator, repo-search agent and its no-links variant. E002c embedding top-k and E004 graph reranking exist in the YAML path but require implementation work before candidate-operator use.

For YAML experiments:

1. Copy the shape of [experiment.example.yaml](../configs/experiment.example.yaml), select existing providers and give each condition a distinct name. Dataset, build-context, volume and output paths resolve relative to the config file. The loader is a small YAML subset, not a general YAML engine (`config.py::_load_simple_yaml`).
2. Declare dataset/query identities, knobs, budgets, model assets and an isolated output root. `harness.py::run_experiment` replaces existing directories for the same dataset/test run identity. Use a new output location when retaining previous runs.
3. Resolve image handling: with `build_context`, `docker.py::build_image` executes `docker build -t <image>`. For a prebuilt digest-pinned image, omit `build_context`; a digest is not a build tag. Set `network_disabled: true` explicitly for offline providers; YAML defaults to false. Read-only model volumes and offline model locks are separate requirements.
4. Inspect `harness.py::prepare_input`: it mounts answers and support audits when present, as well as questions/corpus/graph. This supports oracle runs but does not enforce judgment isolation for ordinary retrieval. A scientific experiment requiring that isolation must address the staging boundary before running; merely asking the provider to ignore gold files is not isolation.
5. The loader accepts only `scoring.method: lexical_baseline`; the harness calls `scoring.py::score_predictions`. Adding `ndcg` to that field does not enable ranked scoring. Use the [evaluation workflow](extension-evaluations.md) for strict scoring and artifact conversion.
6. Verify provider contract, a small real smoke run, retained identities and output completeness before the full matrix. Generated outputs stay untracked.

## New retrieval provider

Create `tests/<name>/runner.py` and its container build context. Follow [the provider contract](retrieval_evaluation.md): questions/corpus/config in, one prediction per question and metadata out. Metadata requires at least `name` and boolean `deterministic`; prediction validation is in `retrieval_arena/schemas.py`. Record resolved knobs and model identity. Reference answers, judgments and support targets must not enter ordinary retrieval or answer generation; oracle access is a separately labeled diagnostic.

Add a YAML condition and focused tests under `unit_tests/` for ranking behavior, empty/no-hit cases, budgets, determinism when claimed, and emitted IDs/metadata. Add a provider page and row in [the catalog](evaluations/README.md). A new provider is not automatically an LLM synthesizer: inspect what its `generated_answer` field contains.

If candidate execution is needed, also extend `candidate_retrieval.py::PROVIDERS`, profile config validation, metadata validation and output projection. Trace-based providers need compatible trace validation. Follow an existing admitted profile for image repository, config keys, graph requirements and network policy. Add admission/refusal tests as well as successful predictions. Bind an appropriate verification image/receipt for operator execution; adding a directory or YAML row alone is insufficient.

The existing fixed-model answer synthesis described in [AGENTS.md](../AGENTS.md) belongs to Capstone. Reuse requires checking that caller's contracts; it is not implicitly invoked by RA retrieval.
