# Portable task execution, receipts, and replay

Status: PRE-S019q-34B implementation contract; pending independent acceptance

## Boundary

`retrieval_arena.task_execution` is the Retrieval Arena-owned adapter boundary
above the pure contracts in `task_completion.py`. Provider implementations are
caller-supplied callables whose symbolic entrypoint and implementation digest
must match a closed task-pack registry. They return observations only. RA owns
admission, native receipts, semantic events, oracle-result construction,
scoring, publication, and replay verification.

The core passes an opaque, admitted `provider_parameters` object to the agent
and environment adapters and retains an opaque `oracle_state` object returned
by the environment for its bound oracle. RA does not interpret corpus or
service fields inside either object. Only the built-in synthetic providers
interpret `scenario`, `native_variant`, and web-service generation fields.

The 34B implementation is deliberately hermetic and synthetic. It has no
dynamic loader, subprocess owner, network client, model invocation, real
service manager, corpus dependency, or DocSpectra import. Its built-in restart
fixture is qualification evidence for the interface, not a scientific task
pack. An external project can construct the same JSON contracts and provide
separately admitted callables without changing RA core.

## Admission and lifecycle

The registry binds exactly one synthetic `agent_adapter`,
`environment_adapter`, `event_normalizer`, and `oracle_provider`. Every
provider binds the corresponding task-pack reference, implementation artifact,
symbolic entrypoint, declared capabilities, supported operations, interface
version, and a self-hashed manifest. Runtime networking and dynamic loading
must both be forbidden. The callable inventory must exactly match the registry
and implementation digests are checked before the first invocation.

`build_execution_plan` is write-free and non-consuming. It declares the one
RA-owned lifecycle in order: admit, initialize, execute, observe, finalize,
score, publish, replay, cleanup. `execute_task` admits the complete input set,
invokes subordinate providers, constructs the evidence bundle, recomputes its
score and replay manifest, and verifies deterministic oracle replay before
returning. `publish_task_execution` writes that admitted bundle only to a new
harness-owned directory. The immutable pre-publication bundle marks `publish`
as `pending_externalization`; the content-addressed summary is the publication
receipt, avoiding a false claim that a bundle had been written before it was.

## Versioned execution records

| Schema | Purpose | Self-hash |
| --- | --- | --- |
| `retrieval_arena.task_execution_provider.v1` | Executable provider manifest bound to a task-pack reference | `provider_manifest_sha256` |
| `retrieval_arena.task_execution_registry.v1` | Exact admitted provider inventory | `registry_sha256` |
| `retrieval_arena.task_execution_request.v1` | Trial, opaque provider parameters, policy, retention, and registry binding | `request_sha256` |
| `retrieval_arena.task_execution_plan.v1` | Non-consuming lifecycle and event inventory | `plan_sha256` |
| `retrieval_arena.task_native_receipt.v1` | Exact-trial-bound provider request and observation receipt | `receipt_sha256` |
| `retrieval_arena.task_execution_bundle.v1` | Complete retained inputs, receipts, evidence, result, and lifecycle | `bundle_sha256` |
| `retrieval_arena.task_execution_summary.v1` | Compact publication result plus content-addressed artifact inventory | `summary_sha256` |
| `retrieval_arena.task_replay_verification.v1` | Proof that replay invoked only the deterministic oracle | `verification_sha256` |

Native receipts carry exact `trial_sha256`, monotonic harness sequence and
synthetic timestamps, provider and operation identity, canonical request and
observation hashes, exit status, redaction, and sensitivity. Normalized v2
events add parent action sequence, causal correlation, semantic argument and
observation hashes, input/output artifact hashes, and the native receipt hash.
The supported synthetic inventory includes tool invocation, command result,
process and service lifecycle, filesystem mutation, HTTP request and response,
test execution, artifact publication, and cleanup.

## Oracle and replay boundary

The deterministic oracle consumes retained normalized events and retained
environment observations. It checks initial state, action occurrence,
temporal order, HTTP/config/test terminal predicates, causal linkage,
prohibition of mock substitution, and cleanup. The agent stub's success claim
is retained only inside its native observation and is never passed to the
oracle or scorer.

Bundle validation reconstructs the plan, validates every native receipt and
event binding, recomputes the pure score from oracle and efficiency evidence,
and rebuilds the pure replay manifest. `replay_task_execution` then rebuilds
the oracle input from retained evidence, invokes only the admitted oracle
callable, and requires canonical equality for the oracle observation, oracle
results, score, and replay manifest. It reports zero environment invocations
and zero service executions. Rehashed receipt, score, or oracle mutations
therefore fail closed.

## CLI

The command family is:

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
directories must not already exist. Publications contain a compact summary,
the full content-addressed bundle, native receipts, normalized events, oracle
results, score, replay manifest, and a Markdown report. Synthetic receipts are
public and accept no secret-like provider fields; the request policy forbids
secret payloads while retaining native receipts and failed-run resources.

## Qualification and non-goals

The built-in fixture proves a successful restart and rejects skipped restart,
stale response, request-before-restart, mock substitution, and cleanup failure.
Two native command forms produce different native receipt identities but the
same semantic event projection. Efficiency evidence retains unavailable
optional token classes as null and never creates a composite metric.

34B does not authorize a live LLM, external network, real service, Docker,
DocSpectra task pack, held-out artifact, E-EXP1, or scientific interpretation.
PRE-S019q-34C remains blocked until independent acceptance of the signed 34B
implementation and closeout identities.
