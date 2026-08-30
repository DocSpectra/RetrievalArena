# Portable deterministic task-completion evaluation

Status: PRE-S019q-34A pure-scoring and PRE-S019q-34B synthetic execution layers
implemented; external corpus task packs remain separately governed

Decision date: 2026-08-19

## Purpose

Retrieval Arena (RA) needs an outcome layer that answers a stronger question
than ranked retrieval or answer similarity:

> Given the same task, agent, tools, budget, and environment, does the refined
> documentation help the agent complete and verify the task?

This layer belongs in RA's replayable experiment and provenance shell, but it
does not become part of the ranked-retrieval v2 metric family. Corpus-specific
task packs supply tasks and deterministic oracles. RA supplies portable schemas,
admission, execution receipts, paired comparison, scoring, reporting, and
replay.

RA must remain usable independently of DocSpectraCapstone. A completed RA may
be lent to a related interim experiment and later brought back with operational
feedback. No RA core module may import DocSpectraCapstone or assume a particular
documentation corpus, agent, service manager, container runtime, or experiment
layout.

## PRE-S019q-34A and 34B as-built boundary

`retrieval_arena/task_completion.py` implements the corpus-neutral contract
and scoring layer. It admits self-hashed closed-world records for task packs,
provider references, task definitions, trials, normalized events, oracle
results, efficiency observations, scores, paired comparisons, and replay.
Duplicate JSON object fields are rejected before decoding can erase them.

The scorer consumes only admitted records. It does not import a corpus project,
execute an adapter, load a plugin, run a service, invoke a model, access the
network, or infer evidence from agent text. PRE-S019q-34B adds the separately
bounded `retrieval_arena/task_execution.py` synthetic execution layer:
content-addressed provider admission, harness-owned receipts and normalization,
deterministic oracle invocation, CLI, report publication, and oracle-only
replay. It deliberately has no provider loader, subprocess, network client,
real service, model, or corpus dependency. The exact schemas and calls are in
[`task_completion_contracts.md`](task_completion_contracts.md) and
[`task_execution_contracts.md`](task_execution_contracts.md).

## Evaluation families

RA keeps four explicitly separate result families:

1. `ranked_retrieval`: standard Recall@k, binary nDCG@k, complete-support@k,
   and MRR@k under the ranked-v2 contract;
2. `automatic_answer_diagnostics`: lexical overlap and optional provider-pinned
   BERTScore precision, recall, and F1;
3. `scientific_calibration`: separately imported human relevance, answer,
   attribution, and paraphrase judgments;
4. `deterministic_task_completion`: observed task actions, terminal outcomes,
   safety checks, and efficiency measurements.

No report, aggregate, or replay may silently combine these families. BERTScore
does not establish task completion. Human calibration is not required for an
automatic task-completion run.

## Portable ownership boundary

RA core owns:

- closed-world task-pack, task, trial, event, oracle, score, comparison, and
  replay schemas;
- content and provider identity admission;
- before/after pairing and trial identity;
- generic execution-adapter and oracle-provider interfaces;
- tamper-evident event and observation receipts;
- deterministic scoring and aggregation;
- structured reports and byte-stable replay.

A task pack owns:

- corpus-specific task statements and initial fixtures;
- allowed tools and declared capabilities;
- required semantic actions and their ordering constraints;
- independently observable terminal-state requirements;
- prohibited actions and safety invariants;
- references to pinned execution adapters and oracle providers;
- task-specific budgets and optional efficiency ceilings.

An external agent adapter owns model invocation and tool mediation. A task pack
may not embed unrestricted executable code. Executable adapters and oracles are
separately versioned, signed or content-addressed, capability-declared providers.

## Task-pack contract

A `retrieval_arena.task_pack.v1` bundle contains:

- pack ID, version, publisher, license, and manifest hash;
- supported RA contract version;
- task records and immutable fixture identities;
- agent-adapter, environment-adapter, event-normalizer, and oracle-provider
  identities;
- network, filesystem, process, container, secret, and privilege capabilities;
- declared writable roots and cleanup policy;
- before/after documentation binding policy;
- token, time, tool-call, retry, and context budgets;
- aggregation and replication policy.

Each `retrieval_arena.task_definition.v1` contains:

- stable task ID and natural-language goal;
- initial-state fixture and admissibility assertions;
- context inputs available to the agent;
- required semantic events and partial-order constraints;
- terminal-state assertions;
- causal-link assertions between actions and observations;
- prohibited events and critical safety assertions;
- oracle references and typed parameters;
- success and cleanup policies.

Exact command strings are not generally required. The task should accept
different valid procedures that produce the same admitted semantic events and
verified outcome.

## Observed execution, not self-report

The evaluated object is the complete observed trial, not the agent's final
message. RA accepts evidence only from admitted harness receipts. Agent claims
such as "I restarted the server" or "the page works" are untrusted text.

`retrieval_arena.task_event.v2` records include:

- exact `trial_sha256`, display `trial_id`, task, attempt, and monotonic
  sequence identity;
- harness-observed timestamps;
- event class, target, normalized arguments, and adapter identity;
- input/output artifact hashes;
- exit status or typed observation;
- parent action and causal-correlation IDs;
- redaction and sensitivity classification.

The event model must cover at least tool invocation, process/service lifecycle,
filesystem mutation, command result, HTTP request/response, test execution,
artifact publication, and cleanup. Adapter-native details remain available in
hashed receipts while generic semantic events make task packs portable.

## Deterministic oracle model

Oracles evaluate immutable receipts and declared artifacts. They never ask an
LLM whether the task succeeded. Oracle classes include:

- initial-state admission;
- semantic action occurrence and ordered-action constraints;
- command/process/service state;
- HTTP status, headers, and bounded content assertions;
- file, schema, configuration, and semantic-configuration assertions;
- focused test or health-check outcomes;
- causal correlation, such as a request served by the restarted process
  generation;
- prohibited-event and critical-safety checks;
- cleanup and state-restoration checks.

Every oracle result identifies the exact trial hash, its provider, version,
parameters, input hashes, expected predicate, observed value, verdict, and
canonical record hash. A missing required receipt is a failure or explicit
invalid trial according to the preregistered policy; it is never inferred from
agent prose.

### Example: restart and verify a web service

A valid task can require:

1. an admitted `service_restart` event for `web`;
2. an observed process-generation change;
3. an `http_request` to `/` after the restart;
4. an HTTP 200 response containing a fixture-specific marker;
5. proof that the new process generation served that request;
6. no mock substitution, disabled health check, oracle modification, or
   unrelated service mutation;
7. terminal cleanup according to the task policy.

A pre-existing healthy page does not satisfy the restart or ordering oracles.
A narrated claim without harness receipts does not satisfy any oracle.

## Scoring hierarchy

RA reports components and never hides them in an unregistered weighted
composite.

### Correctness and safety

- `task_success`: one iff every required oracle passes, every critical safety
  oracle passes, and cleanup is green; otherwise zero;
- `requirement_completion`: passed required oracles divided by all applicable
  required oracles;
- `verified_action_completion`: passed required semantic-action oracles divided
  by all applicable required semantic-action oracles;
- `terminal_state_completion`: passed terminal-state oracles divided by all
  applicable terminal-state oracles;
- `critical_safety_violation`: one if any critical prohibition fails;
- `noncritical_violation_count` and complete violation inventory;
- `invalid_trial`: explicit admission, harness, receipt, or oracle-integrity
  failure, reported separately from ordinary task failure.

### Speed and resource efficiency

Harness clocks and provider receipts, not agent estimates, supply:

- wall-clock time from admitted task start to verified terminal completion;
- active execution time where the adapter can distinguish it;
- time to first valid task action;
- time to first successful verification action;
- input, cached-input, output, and reasoning token counts when the provider
  exposes them;
- total and successful tool calls;
- retry and failed-action counts;
- context items and context tokens supplied;
- optional provider cost and energy/resource observations when independently
  measured.

Unknown token classes remain explicitly unavailable. They are never estimated
from text unless a separately registered tokenizer policy authorizes that
diagnostic.

Efficiency is interpreted only after correctness and safety:

1. compare task success and critical safety first;
2. compare time, tokens, tool calls, and retries among successful safe trials;
3. report resources consumed by failed or invalid trials separately;
4. do not let a fast failure outrank a slower success;
5. do not create a weighted speed/token/quality composite without a distinct
   preregistration.

Useful aggregates include success rate, macro requirement completion, safety
violation rate, median and tail completion time among successes, median input
and output tokens per successful task, successful tasks per million tokens,
and tool calls per successful task.

## Before/after experimental design

Each pair freezes the task, fixture, agent/model identity, prompt, tool set,
budgets, environment, retrieval/context policy, adapters, oracles, and scoring
policy. Only the admitted documentation snapshot changes.

Per-task paired outcomes are:

| Before | After | Meaning |
| --- | --- | --- |
| fail | pass | improvement |
| pass | fail | regression |
| pass | pass | preserved success; compare efficiency secondarily |
| fail | fail | no demonstrated completion improvement |

Retrieval metrics explain whether required evidence entered context. Answer
diagnostics describe the generated answer. Task completion is the practical
outcome. These layers may be related analytically but are not collapsed.

## Determinism and stochastic agents

Scoring and replay are deterministic for fixed receipts and artifacts. Agent
execution may remain stochastic. Every trial therefore records model and
runtime identity, decoding policy, seed where supported, prompt, context,
budget, adapter, and replicate index.

Single-run deterministic claims require a provider that actually supports the
registered determinism contract. Otherwise the experiment uses a frozen number
of paired replicates and reports both task-level paired outcomes and aggregate
uncertainty. Replication counts cannot be increased after viewing outcomes.

## Security and integrity

- Task fixtures are isolated from oracle sources and expected predicates.
- Agents cannot write task definitions, oracle code, receipts, or evaluator
  policy.
- Secrets and external network access are denied unless explicitly declared by
  a task capability and supplied by an authorized adapter.
- Receipt append and finalization are harness-owned.
- Every writable root is declared and cleanup-verified.
- Task packs cannot introduce inline shell, Python, or arbitrary plugin loading
  through configuration.
- Provider admission fails closed on identity, schema, capability, or version
  mismatch.
- Exact expected outputs may be used when scientifically required, but semantic
  predicates are preferred when multiple correct solutions exist.

## Human calibration relationship

Human review remains a separate imported artifact family. It may assess whether
task definitions reflect realistic maintainer intent, whether deterministic
oracles omit important quality dimensions, or whether successful outputs are
professionally useful. It never silently changes automatic task verdicts.

The operational task profile requires no interactive assessor. Periodic human
calibration may lead to a new versioned task pack or oracle provider; it does
not rewrite previous results.

## Portable interim-experiment use

RA core and generic task contracts must be releasable independently. A related
interim experiment may pin an RA commit, provide its own task pack and adapters,
and return only issue reports, contract proposals, and non-project-specific
fixtures. Feedback is reviewed and deliberately incorporated through new RA
versions. DocSpectra-specific corpus data, held-out mappings, task outcomes,
and pipeline modules do not enter RA core.

## Required implementation sequence

1. complete the PRE-S019q-33 dual-profile BERTScore and human-artifact core;
2. add strict task-pack, event, oracle, score, comparison, and replay schemas
   plus a pure scorer — implemented by PRE-S019q-34A;
3. add provider admission, execution adapter, event normalization, CLI, report,
   and byte-stable replay using synthetic task packs — implemented by
   PRE-S019q-34B;
4. extract candidate DocSpectra tasks without opening evaluation outcomes;
5. implement a separately owned DocSpectra task pack and adapters;
6. independently verify portability with at least one RA-native synthetic pack
   and one external corpus pack.

Implementation does not authorize E-EXP1, held-out reveal, or a scientific
task-completion experiment. A later preregistration must select tasks,
replicates, endpoints, budgets, and analysis.
