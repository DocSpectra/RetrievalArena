# Extending Retrieval Arena

Source audit: September 24, 2026, baseline `8424d9b979b1623631e3b6f0a9ed1399af9110b6`. This guide describes implemented boundaries and extension steps; it does not certify a runnable experiment. Recheck the linked source when changing those boundaries.

## Load only the relevant path

Use the terminology and follow-up IDs in [architecture conventions](architecture-conventions.md) when describing execution paths or proposing changes. Differences are recorded there; a broad architecture refactor is deferred.

Read the workspace guide and [RA agent guide](../AGENTS.md), then choose a path below. Existing reference pages remain the source of detailed contracts; this page explains where a change belongs.

| Intended change | Read next | Implementation owners |
| --- | --- | --- |
| New experiment using existing retrieval methods | [Experiment workflow](extension-experiments.md) | `config.py`, `harness.py`, `docker.py`, `configs/` |
| New retrieval method | [Experiment workflow](extension-experiments.md), then its nearest [provider reference](evaluations/README.md) | `tests/<provider>/`; optionally `candidate_retrieval.py` |
| New metric, diagnostic or judging procedure | [Evaluation workflow](extension-evaluations.md) | scorer/validator, profile integration and result consumers |
| New model provider, or a model call from a judge/scorer | [Model providers](model_providers.md) | `model_providers/` (transport, pricing, `external_api` admission), its caller's versioned config |
| New corpus, preprocessing or question set | [Dataset workflow](extension-datasets.md) | `corpus/`, `support/`, `graph/`, dataset contracts |
| Run through the immutable operator | [Operator contract](operator.md) | `scripts/run-verification.*`, `containers/verification/` |

Paths in the owner column are relative to `retrieval_arena/` unless explicitly rooted in `tests/`, `configs/`, `scripts/` or `containers/`. Providers are called `tests` in experiment YAML; correctness tests live in `unit_tests/`.

## Boundaries to preserve

- An experiment selects data and methods; a provider emits predictions; an evaluation scores them. Adding a YAML row does not add a new scorer or candidate-operator profile.
- Legacy YAML execution and strict operator execution have different input isolation and output contracts. Do not treat their prediction files as interchangeable.
- Acquisition, document preparation, question sourcing, support construction and independent judgments are separate stages. Automatically inferred support is not independently judged relevance.
- Dataset identity, image/model identity, context budgets and scoring policy belong in the frozen experiment record. Repository HEAD alone does not identify a completed experiment.
- Keep reusable implementation knowledge here; keep a paper's question, sample, claim/evidence decisions and optional upstream dependencies in its context repository.

For implementation changes, add focused behavioral tests for the changed contract and follow [operator validation](operator.md). Check `containers/verification/verify.py::COMMANDS` when selecting suites: a test file's existence does not imply inclusion in `focused`. Documentation-only changes need source/link checks, not an experiment launch or image rebuild merely to preview prose. A later operator run still needs a checkout matching its receipt, including after documentation commits.

For full normalized magnetic heat-row exports, pair affinity and edge-masked recomputation, see [page representations](evaluations/page_representations.md).

For frozen seed-preserving affinity selection with held-separate labels, see [complementary context](evaluations/complementary_context.md).

For source-only content masking, fixed regions, safe inline edits and append-only intent events, see [embedded links](embedded-links.md).

For model-facing sentence/passage IDs, exact host resolution and a combined
preflight/count/canary/refinement lifecycle, see [source-bound refinement](evaluations/embedded_interface.md).

For fixed-node additive phase-energy qualification and bounded solver diagnostics, see [local objectives](evaluations/local_objective.md).

For exact component/block cancellation and finite phase comparisons, see [block phase](evaluations/block_phase.md).
