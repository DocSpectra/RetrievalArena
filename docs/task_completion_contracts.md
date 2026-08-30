# Task-completion contracts and pure scoring

Status: PRE-S019q-34A implementation contract

## Boundary

The task-completion layer evaluates retained, harness-observed evidence. It
does not execute an agent or trust an agent's final message. It is independent
of ranked retrieval, automatic answer diagnostics, and imported human
calibration; every output uses the explicit
`deterministic_task_completion` result family.

The implementation is `retrieval_arena.task_completion`. It has no provider
loader, subprocess owner, network client, service manager, corpus import, or
DocSpectra dependency. PRE-S019q-34B will add provider admission, execution
receipts, CLI, reporting, and replay commands without changing the 34A scoring
semantics.

## Versioned records

| Schema | Purpose | Self-hash field |
| --- | --- | --- |
| `retrieval_arena.provider_reference.v1` | Symbolic content-addressed provider identity and declared capabilities | `provider_sha256` |
| `retrieval_arena.task_definition.v1` | Goal, fixture, allowed roots/capabilities, assertions, policies | `task_sha256` |
| `retrieval_arena.task_pack.v1` | Closed pack inventory, providers, fixtures, budgets, replication and cleanup | `pack_sha256` |
| `retrieval_arena.task_trial_identity.v1` | Frozen trial identity; only the documentation snapshot may vary within a pair | `trial_sha256` |
| `retrieval_arena.task_event.v2` | Harness-observed normalized event with exact-trial and native-receipt provenance | `event_sha256` |
| `retrieval_arena.task_oracle_result.v2` | Exact-trial-bound provider result for exactly one declared assertion | `result_sha256` |
| `retrieval_arena.task_efficiency.v2` | Exact-trial-bound time, tokens, context, tool, retry, cost, and measured-resource evidence | `efficiency_sha256` |
| `retrieval_arena.task_score.v2` | Separate correctness, partial completion, safety, cleanup, invalidity, and efficiency | `score_sha256` |
| `retrieval_arena.task_paired_comparison.v2` | Four-cell before/after outcome plus conditional efficiency deltas | `comparison_sha256` |
| `retrieval_arena.task_replay.v2` | Ordered content identities needed to replay one retained score | `replay_sha256` |

The six v2 records correct the unaccepted 34A v1 draft: events, oracle results,
and efficiency evidence now carry `trial_sha256` as the authoritative trial
foreign key, and the dependent score, comparison, and replay families advance
with that binding. `trial_id` remains a display/operational label and is not
sufficient evidence identity.

All records are closed-world. Unknown or missing fields fail admission. Use
`load_contract_json` when decoding JSON so duplicate object fields fail before
the JSON parser can collapse them. Canonical hashes use UTF-8 JSON with sorted
keys, compact separators, no ASCII coercion, and no non-finite numbers.

## Task-pack admission

A pack binds an exact sorted task-definition inventory, immutable fixture
hashes, and content-addressed provider references. It requires exactly one
agent adapter, environment adapter, and event normalizer, plus at least one
oracle provider. Entrypoints are symbolic identifiers; inline Python, shell
fragments, and arbitrary plugin loading are not representable.

Capabilities are a closed map over network, filesystem read/write, process,
container, secrets, and privileges. Each is explicitly `declared` or
`forbidden`. Provider and task requirements must be subsets of the declared
capabilities. Writable roots are absolute, traversal-free, uniquely named, and
cleanup-required. A task cannot reference an undeclared root.

Task assertions preserve these classes independently:

- initial-state admission;
- semantic action;
- temporal order;
- terminal state;
- causal relation;
- prohibition;
- critical safety;
- cleanup.

Assertions form a unique acyclic partial-order graph and reference admitted
oracle providers only. A task requires at least one mandatory semantic action,
terminal state, and cleanup assertion. Parameters remain separately hashed;
the task configuration cannot carry executable provider code.

## Pure scoring

Call `score_task(pack, task, trial, oracle_results, efficiency)`. The function
validates the selected task against its pack binding and returns a self-hashed
`task_score.v2` record.

`task_success` is true only when:

1. the trial is valid;
2. every mandatory oracle passes;
3. every critical safety oracle passes; and
4. mandatory cleanup passes.

The score retains separate requirement, verified-action, and terminal-state
completion fractions. Initial-state failure and explicit invalid oracle
evidence produce `invalid_trial`, not an ordinary task failure. Missing
required evidence follows the task's registered `failure` or `invalid_trial`
policy and is never inferred from prose. Critical and noncritical violation
inventories remain explicit.

Efficiency is retained inside successful, failed, and invalid scores. Token
classes are individually marked `provider_reported`, `registered_tokenizer`,
or `unavailable`; unavailable values must be null, and tokenizer-derived
values require a registered tokenizer-policy hash. There is no weighted
correctness/time/token composite.

## Paired comparison

`compare_task_scores` requires equality of task, fixture, providers, model and
runtime, prompt and decoding, tools, context, budgets, scoring, replicate, and
seed. Only the documentation snapshot identity may differ.

Valid pairs receive exactly one classification:

| Before | After | Classification |
| --- | --- | --- |
| fail | pass | `improvement` |
| pass | fail | `regression` |
| pass | pass | `preserved_success` |
| fail | fail | `preserved_failure` |

An invalid trial makes the pair invalid rather than forcing it into a success
cell. Efficiency deltas are available only for two successful, safe trials.
A fast failure therefore cannot outrank a slower success.

## Replay

`build_task_replay` binds the exact pack, task, trial, ordered event hashes,
ordered oracle-result hashes, efficiency record, and score. It reruns the pure
scorer over the retained oracle and efficiency records and requires canonical
byte equality with the supplied score before constructing the replay manifest.
Repeated construction from identical admitted inputs produces byte-identical
canonical JSON. Any identity, ordering, safety, invalidity, violation, cleanup,
or completion mutation fails upstream evidence recomputation or the replay
self-hash.

## 34A non-goals

This layer does not provide an execution adapter, event collector, oracle
runner, CLI command, report renderer, task pack for a specific corpus, or
scientific experiment. It does not authorize E-EXP1, held-out reveal, model
execution, or human annotation. Those boundaries remain separately governed.
