# Portable task execution, receipts, and replay

Status: PRE-S019q-34B second corrective implementation; pending independent
acceptance

## Boundary

`retrieval_arena.task_execution` is the RA-owned adapter boundary above the
pure contracts in `task_completion.py`. Providers are exact Python source
artifacts, not caller-supplied callables. A `ProviderImplementation` contains
only an artifact path. RA resolves a regular non-symlink file, reads its bytes,
derives SHA-256 itself, compares that digest with the task-pack reference and
registry, retains those admitted bytes against path drift, and executes only
the registered entrypoint from those bytes.

The core passes opaque admitted `provider_parameters` to the agent and
environment providers and retains opaque environment-owned `oracle_state` for
the oracle. Only the built-in synthetic provider knows that its fixture is a
web-service restart. External projects can supply a different exact source
artifact and task pack without modifying RA core.

## Provider runtime and capabilities

Admitted source executes in a fresh namespace with a restricted builtins table:
there is no import primitive, dynamic loader, evaluator, or ambient caller
callable. The harness activates a Python audit policy around both source
execution and entrypoint invocation. It observes filesystem, process, socket,
and native-library events. An operation not declared by the provider fails
before the operation completes. Filesystem use additionally requires an
absolute path inside a task-pack writable root; merely declaring filesystem
capability does not grant ambient host access. Runtime network remains
forbidden by the 34B request and task-pack policy.

Every invocation receipt records the capabilities actually attempted. A
capability violation remains visible in a failed receipt even though the
attempted capability was undeclared. A passed receipt may contain only a subset
of the provider's admitted capabilities.

A passed receipt also requires the exact registered operation-result contract.
The agent, environment-execution, environment-cleanup, normalizer, and oracle
operations each have a closed top-level schema plus typed nested inventory.
Missing fields, extra fields, wrong field types, incomplete oracle verdict
inventories, and invalid event descriptors fail inside `_invoke_provider`.
Operation validation therefore occurs before receipt status is chosen; a
JSON-object shape alone is never sufficient for a passed receipt.

The built-in qualification provider lives in
`retrieval_arena/synthetic_task_providers.py`. It has no imports and performs
no filesystem, process, socket, model, real-service, or dynamic-loading work.

## Observed lifecycle and failures

`build_execution_plan` is non-consuming and write-free. Execution owns admit,
initialize, execute, observe, finalize, score, publish, replay, and cleanup.
Lifecycle dispositions are a projection of an explicit set of completed and
failed phase observations, not an inference from the failed phase's position in
that display order. A cleanup-provider failure after execution therefore marks
cleanup failed and observe, finalize, score, and replay `not_reached`. Cleanup
that completed before a later normalizer or oracle failure remains completed.
Replay is pending until its admitted oracle receipt exists.

Each provider call creates a terminal native receipt whether it passes, raises,
returns malformed data, emits a forbidden secret-like field, or attempts an
undeclared/out-of-root capability. Environment cleanup is invoked from the
harness's `finally` path even after agent or environment failure. Cleanup is
also retained before downstream normalization or oracle failure. A replay call
has its own native receipt.

If execution cannot reach a valid score, RA returns a standalone
`retrieval_arena.task_execution_failure.v1` record. It embeds the admitted pack,
task, trial, registry, request, and plan; every receipt; efficiency/resource
evidence; the failed phase; observed lifecycle dispositions; cleanup outcome;
retention policy; and a self-hash. The same publisher writes this record,
native receipts, efficiency, lifecycle, Markdown report, and compact summary to
a new harness-owned directory. Provider failure is therefore not converted to
an exception that discards evidence.

## Versioned records

| Schema | Purpose | Self-hash |
| --- | --- | --- |
| `retrieval_arena.task_execution_provider.v2` | Provider manifest bound to exact RA-derived source bytes | `provider_manifest_sha256` |
| `retrieval_arena.task_execution_registry.v2` | Exact admitted provider inventory | `registry_sha256` |
| `retrieval_arena.task_execution_request.v2` | Trial, opaque parameters, policy, retention, and registry binding | `request_sha256` |
| `retrieval_arena.task_execution_plan.v2` | Non-consuming lifecycle and event inventory | `plan_sha256` |
| `retrieval_arena.task_native_receipt.v2` | Passed or failed provider request/observation/capability receipt | `receipt_sha256` |
| `retrieval_arena.task_execution_bundle.v2` | Complete scored result plus observed replay receipt and lifecycle | `bundle_sha256` |
| `retrieval_arena.task_execution_failure.v1` | Self-contained provider/lifecycle failure with cleanup and resources | `failure_sha256` |
| `retrieval_arena.task_execution_summary.v2` | Compact success-or-failure publication inventory | `summary_sha256` |
| `retrieval_arena.task_replay_verification.v2` | Oracle-only replay result with its native receipt | `verification_sha256` |

Native receipts carry exact `trial_sha256`, provider and operation identity,
monotonic sequence and deterministic harness timestamps, canonical request and
observation hashes, attempted capabilities, status, exit status, typed failure
metadata, redaction, sensitivity, and a self-hash. Normalized task events retain
parent sequence, causal correlation, semantic argument and observation hashes,
input/output identities, and the source native receipt hash.

## Oracle and replay boundary

The deterministic oracle consumes retained normalized events and retained
environment state. It checks initial state, action occurrence, temporal order,
HTTP/config/test terminal predicates, causal linkage, prohibited mock
substitution, and cleanup. The agent stub's success claim never enters oracle
or scoring input.

Bundle validation reconstructs the plan, validates every native and replay
receipt, recomputes the pure score from oracle and efficiency evidence, and
rebuilds the pure replay manifest. It also reconstructs the exact replay
request from retained task/events/environment state and requires the replay
observation to equal the retained original oracle observation and results.
Rehashing a changed replay payload cannot make it admissible.

Replay re-admits current provider source bytes, invokes only the oracle from a
fresh restricted namespace, records that invocation, and requires canonical
equality for oracle observation, oracle results, score, and replay. Replay
verification is validated with its source bundle, so its bundle, provider,
score, replay, request, and observation claims are evidence-bound rather than
standalone assertions. It reports zero environment invocations and zero
service executions.

## CLI and publication

The command family remains:

```text
retrieval-arena task validate-pack
retrieval-arena task plan
retrieval-arena task execute-synthetic
retrieval-arena task score
retrieval-arena task compare
retrieval-arena task report
retrieval-arena task replay
```

All inputs are explicit JSON paths. `plan` writes nothing. Execution and report
directories must not already exist. `execute-synthetic` publishes either a
completed v2 bundle or a standalone terminal-failure record; provider failure
is a retained machine-readable result rather than lost stderr.

## Qualification and non-goals

The hermetic fixture proves success, two equivalent native commands, skipped
restart, stale response, early request, mock substitution, task cleanup
failure, cleanup-provider failure, and provider failures in agent, environment,
normalizer, and oracle phases. Adversarial exact artifacts prove that
filesystem, process, and network attempts fail with receipts; declared
filesystem capability remains confined to pack roots; source substitution and
path drift reject; every provider operation terminalizes a malformed object;
missing, extra, and wrong-type operation fields reject; failure lifecycle
dispositions match observed reachability; and rehashed receipt, lifecycle,
cleanup, oracle, score, and replay request/observation mutations fail closed.

34B does not authorize a live LLM, external network, real service, Docker,
DocSpectra task pack, held-out artifact, E-EXP1, or scientific interpretation.
PRE-S019q-34C remains blocked until independent acceptance of the corrected
signed implementation and context closeout identities.
