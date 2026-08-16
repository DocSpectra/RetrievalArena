# Ranked-retrieval metrics v2 specification

Status: accepted design; PRE-S019q-32B implementation authorized

Decision date: 2026-08-15. Reference status: `zotero_unchecked`. No E-EXP1
result or held-out evidence was inspected.

## Decision

Retrieval Arena remains the DocSpectra evaluation/evidence shell, but no longer
owns standard ranked-metric arithmetic. V2 delegates that arithmetic to pinned
`ir_measures==0.4.3` and records its provider, distribution hash, measure
declarations, qrels/run hashes, and per-query output. A frozen tiny corpus is
also scored by an independently pinned `trec_eval`-compatible provider.

The primary endpoint is macro **evidence Recall@k**, where `k` is the
preregistered number of unique support items admitted to the answer model.
Secondary endpoints are binary nDCG@k, complete-support@k, and MRR@k. Lexical
answer F1 remains legacy-only and is not retrieval effectiveness.

Generic scoring belongs to accepted IR engines. Retrieval Arena owns dataset
and run admission, DocSpectra support semantics, before/after pairing, action
traces, replay identity, availability, audit, reporting, and evidence export.

## Metric guide

In this specification, an **endpoint** is an experimental outcome measure. It
is not an HTTP endpoint, API service, or network connection. Ranked scoring is
local and deterministic once the qrels, predictions, policy, and pinned metric
engine are present.

All four v2 metrics examine the same first `k` unique support targets, but they
answer different questions:

### Evidence Recall@k

**Question:** How much of the required evidence did retrieval place inside the
model's usable context?

For each query, it divides the number of required support targets retrieved in
the first `k` positions by the total number of required support targets. A
score of `1.0` means all required evidence was available; `0.5` means half was
available. It gives partial credit and directly matches DocSpectra's main claim
about improving evidence availability. The reported primary endpoint is the
macro-average across queries, so each query has equal weight.

### Complete-support@k

**Question:** Did retrieval obtain every required support target?

This project-specific diagnostic is binary per query: `1` only when the first
`k` items contain the complete required-support set, otherwise `0`. It is more
strict than Recall@k and is useful when an answer may be unsafe or incomplete
after omitting even one required source. It does not show whether a failed
query missed one target or nearly all of them.

### nDCG@k

**Question:** Were relevant support targets placed near the top in a good
order?

Discounted cumulative gain rewards relevant items more when they appear at
earlier ranks. Normalization divides by the best possible ordering for that
query, producing a value between `0` and `1`. nDCG is especially informative
with graded relevance. E-EXP1 currently uses binary judgments, so nDCG mainly
adds rank sensitivity rather than richer importance information.

### MRR@k

**Question:** How quickly did retrieval produce its first relevant support
target?

Reciprocal rank is `1/r`, where `r` is the first relevant rank at or before
`k`; it is zero when no relevant item appears. Mean reciprocal rank averages
that value across queries. MRR is intuitive for first-hit tasks, but it ignores
all relevant evidence after the first hit and therefore cannot measure
multi-support completeness.

### Legacy lexical F1

**Question:** How much token overlap exists between generated and reference
answer text?

Lexical F1 is retained for v1 compatibility and answer-overlap diagnostics.
It does not measure ranked retrieval: an answer can share many words with a
reference while relying on missing evidence, or use different wording despite
retrieving the correct evidence. It is not a v2 retrieval endpoint.

### Shared example

Suppose a query requires support targets `{A, B, C}`, the context budget is
`k=3`, and retrieval returns `[A, X, B]`.

- Evidence Recall@3 is `2/3`, because `A` and `B` were retrieved but `C` was
  missed.
- Complete-support@3 is `0`, because the required set is incomplete.
- nDCG@3 receives credit for `A` at rank 1 and less credit for `B` at rank 3;
  it is below the ideal score of `1.0`.
- Reciprocal rank is `1.0`, because the first retrieved item is relevant. This
  illustrates why MRR alone would hide the missing `C`.
- Lexical F1 cannot be inferred from the ranking at all; it requires generated
  and reference answer text.

## Metric comparison

| Metric | Primary question | Partial credit | Rank-sensitive | Multi-support coverage | Main blind spot | Role in E-EXP1 |
| --- | --- | --- | --- | --- | --- | --- |
| Evidence Recall@k | How much required evidence is in context? | Yes | No, within top `k` | Yes | Does not distinguish rank 1 from rank `k` | Primary |
| Complete-support@k | Is every required target in context? | No | No | Yes, strict all-or-nothing | Hides how close an incomplete result was | Secondary |
| nDCG@k | Is relevant evidence ordered near the top? | Yes | Yes | Yes | Binary labels limit graded usefulness | Secondary |
| MRR@k | How early is the first relevant target? | Yes | Yes | No, only first hit | Ignores later required evidence | Secondary |
| Lexical F1 | Does answer wording overlap the reference? | Yes | Not applicable | No | Is not retrieval effectiveness | Legacy diagnostic |

No single metric should be treated as a composite quality score. The primary
endpoint answers the preregistered evidence-availability question; secondary
metrics explain completeness and ranking behavior without replacing it.

## Scientific unit

The endpoint asks whether a documentation revision makes the evidence required
for a registered query retrievable inside the exact model context-item budget.
The ranked item is a canonical `support_target_id`: the evaluator's smallest
independently judgeable documentation unit, normally a page section. It is not
an arbitrary retrieval chunk. An adapter may map chunks to targets, but must
retain chunk provenance and collapse repeated targets at their earliest rank
before canonical-run admission.

The query is the scoring unit. Connected support groups are the independence
unit for paired uncertainty because queries sharing support targets are not
independent. The primary summary macro-averages queries; micro averaging is
never primary.

## Relevance and qrels

V2 admits integer relevance `0..3`; E-EXP1 uses binary qrels: required support
target `1`, explicitly judged non-support `0`. Higher grades are unavailable
unless a later registration identifies the judgment protocol, assessor class,
adjudication, and provenance hash. Model confidence, answer text, and retrieval
scores cannot create relevance grades.

Qrels declare `complete` or `pooled`. Primary Recall@k is available under
pooling because its denominator is the complete registered required-support
set. nDCG@k is unavailable if a pooled, unjudged item appears in top `k`; it is
not silently nonrelevant. With complete qrels, an unknown top-k item is invalid.

## Frozen metrics

For query `q`, let `G_q` be its non-empty required-support set, `R_q(k)` the
unique canonical targets at ranks `1..k`, and `rel_q(i)` registered relevance.

- Primary: `Recall@k(q) = |G_q intersect R_q(k)| / |G_q|`.
- Secondary: `CompleteSupport@k(q) = 1` iff `G_q subset R_q(k)`, else zero.
- Secondary: `DCG@k = sum(rel_i/log2(i+1))`; nDCG divides by ideal DCG.
- Secondary: reciprocal rank is `1/r` for the first positive rank `r <= k`,
  else zero; MRR is its query macro-average.

MRR is secondary because DocSpectra questions may require multiple targets.
nDCG is secondary because current judgments are binary. Token precision,
recall, F1, match rate, and lexical overlap retain their v1 names only.

Paired uncertainty resamples connected support groups with all member queries.
The registration must state confidence level, seed, and resample count; the
scorer invents no inferential default.

## Cutoff binding

Configuration has positive `context_item_budget_k`. The same value binds
retriever truncation, model context admission, every `@k` metric, replay, and
comparison. Disagreement invalidates the run. Token budgets are separate
observability and never substitute for item `k`.

## Edge cases

| Case | Required behavior |
| --- | --- |
| No positive qrels | unavailable `zero_relevant_items`, never zero |
| Missing query run | invalid before scoring |
| Unknown query/item | invalid unless admitted by dataset |
| Duplicate target | invalid canonical run; pre-admission chunk collapse requires an audit record |
| Equal source scores | descending score then stable target ID; retain original rank and policy |
| Missing/non-finite score | invalid |
| Fewer than `k` | valid only with `retrieval_exhausted=true` |
| More than `k` | retain through `max_rank`; measures consume top `k` |
| Unjudged top-k, complete qrels | invalid qrels/run relation |
| Unjudged top-k, pooled qrels | Recall available; nDCG unavailable `unjudged_top_k` |
| Missing support group/qrels | invalid dataset |
| Engine/provider failure | failed/unavailable; no local fallback arithmetic |

## Strict v2 schemas

All objects reject unknown fields. IDs are non-empty; hashes are lowercase
SHA-256; timestamps are fixed ISO-8601 UTC.

### `retrieval_arena.ranked_dataset.v2`

Fields: `schema_version`, `dataset_id`, `corpus_id`, `query_set_id`,
`support_surface_id`, `corpus_snapshot_id`, `graph_snapshot_id`,
`ranked_item_granularity` (exactly `support_target`), `qrels_scope`,
`relevance_scale`, `judgment_policy_id`, `judgment_provenance_sha256`,
`ordered_query_ids`, `support_group_by_query`, `qrels_sha256`, and
`dataset_manifest_sha256`.

Qrels JSONL records contain `query_id`, `support_target_id`, `relevance`,
`judgment_id`, and `judgment_provenance_sha256`. Pairs are unique and every
query has a positive qrel.

### `retrieval_arena.ranked_prediction.v2`

Fields: `schema_version`, `query_id`, `context_item_budget_k`, `max_rank`,
`retrieval_exhausted`, ordered `items`, `duplicate_collapse_count`, and nullable
`duplicate_collapse_sha256`. Each item has `support_target_id`, contiguous
`rank`, finite `score`, `source_rank`, `source_item_id`, and
`source_provenance_sha256`. Targets are unique.

### `retrieval_arena.ranked_metric_policy.v2`

Fields: `schema_version`, `policy_id`, primary `evidence_recall`, ordered
secondary metrics `[complete_support, ndcg, reciprocal_rank]`, one cutoff bound
to `context_item_budget_k`, `gain=identity`, `discount=log2_rank_plus_one`,
`aggregation=query_macro`, `uncertainty_unit=connected_support_group`, qrels,
unjudged, tie, and duplicate policies, engine/provider names and versions,
engine artifact hash, parity oracle/version/corpus hash, optional registered
uncertainty fields, and `policy_sha256`.

### Score, aggregate, and replay

`retrieval_arena.ranked_item_score.v2` contains query/group IDs, cutoff, the
four metric values, available/unavailable maps, positive-qrel/retrieved/judged
counts, and engine-record hash.

`retrieval_arena.ranked_aggregate.v2` contains dataset/run/policy IDs, cutoff,
query/group counts, four macro values, availability counts/reasons, optional
registered intervals, per-query hash, and aggregate hash.

`retrieval_arena.ranked_replay.v2` binds SHA-256 values for dataset, qrels,
predictions, policy, engine distribution/provider, parity corpus, per-query,
aggregate, action traces, and reports, plus repository commit/tree/status,
exact command/config/environment identity, timestamp, context budget, and
ordered query/group identities.

## Tool decision

| Tool | Role/fit | Disposition |
| --- | --- | --- |
| NIST `trec_eval` | TREC reference scorer; weak project orchestration | independent parity oracle |
| `pytrec_eval` | SIGIR 2018 binding; native packaging friction | acceptable oracle provider, not primary dependency |
| `ir_measures` 0.4.3 | ECIR 2022 typed multi-provider per-query API | standard metric owner |
| `ranx` | ECIR evaluation/statistics and CIKM fusion | deferred exploratory comparison |
| BEIR | heterogeneous benchmark, not just a scorer | optional external sensitivity benchmark |
| RAGAS | EACL model-assisted RAG diagnosis | deferred nondeterministic secondary diagnostic |
| RAGChecker | fine-grained retriever/generator diagnosis | deferred secondary diagnostic |
| Retrieval Arena | before/after, traces, replay, audit, reports | retain shell, delegate standard arithmetic |

`ir_measures==0.4.3` has a platform-independent Apache-2.0 wheel with SHA-256
`3d8b3b5283e4a77df85f9f6c8a1c2c99a7421b78fa014ef97f2ddab3037d3dcf`.
32B must capture its complete offline dependency closure; evaluation performs
no network install. 32C must prove the capsule has those exact artifacts.

Replacement is rejected: it would require a large rewrite of replay, trace,
revision, governance, and evidence-export ownership without improving standard
metric authority. Delegation is two medium changes: isolated RA v2, then the
Capstone adapter/capsule boundary.

## Literature matrix

| Authority | Venue/year | Adopted constraint |
| --- | --- | --- |
| Järvelin & Kekäläinen, `10.1145/582415.582418` | TOIS 2002 | explicit gain, discount, ideal ordering |
| TREC judgment guidance | NIST | qrels scope and explicit unjudged handling |
| TREC 2025 RAG retrieval-only | TREC 2025 | metric roles, not its task-specific cutoffs |
| Attributed IR, `10.1145/3627673.3679172` | CIKM 2024 | separate ranked evidence from answer overlap |
| `pytrec_eval`, `10.1145/3209978.3210065` | SIGIR 2018 | accepted parity lineage |
| `ir_measures`, `10.1007/978-3-030-99739-7_38` | ECIR 2022 | delegated typed standard scoring |
| `ranx`, `10.1007/978-3-030-99739-7_30` | ECIR 2022 | later statistical analysis candidate |
| ranx fusion, `10.1145/3511808.3557207` | CIKM 2022 | keep optimization outside evaluator |
| BEIR resources, `10.1145/3626772.3657862` | SIGIR 2024 | optional external benchmark |
| RAGAS, `10.18653/v1/2024.eacl-demo.16` | EACL 2024 | not deterministic primary |
| RAGChecker | NeurIPS Datasets/Benchmarks 2024 | supplementary component diagnosis |

The targeted CIKM/WSDM/SDM review found relevant attributed retrieval and
ranking work, but no newer venue paper supersedes official TREC contracts and
the foundational nDCG definition for standard metric semantics.

## Rejected alternatives

- Retain RA unchanged: v1 lacks qrels, ranked identity, edge semantics, and an
  independent standard-metric authority.
- Replace RA with BEIR: it is a benchmark ecosystem, not DocSpectra's evidence
  and replay owner.
- Replace RA with RAGAS/RAGChecker: model-assisted judgments cannot be the
  deterministic primary endpoint.
- MRR primary: first hit does not measure multi-support completeness.
- nDCG primary: current labels are binary and the claim is evidence coverage.
- Recall@100: 100 belongs to another task; `k` must match DocSpectra context.
- Local formulas or a composite: rejected except tiny independent test oracles.

## Compatibility

V1 artifacts remain readable only through existing legacy paths. They are
never implicitly upgraded, assigned synthetic qrels, or compared numerically
with v2. A later explicit converter must have stable item mappings and qrels
and retain source/conversion hashes.

## 32B acceptance

32B must add strict schemas without weakening v1; delegate Recall, nDCG, and
RR to pinned `ir_measures`; implement complete-support as the explicitly
project-specific set diagnostic; cover every edge case at public ingress;
pass a frozen tiny corpus through two accepted providers; emit byte-stable
score/aggregate/replay artifacts; and perform no model, held-out, E-EXP1,
network, or outcome-dependent action.

## Sources

- https://doi.org/10.1145/582415.582418
- https://trec.nist.gov/data/reljudge_eng.html
- https://trec.nist.gov/pubs/trec34/appendices/trec2025-rag-retrieval.html
- https://doi.org/10.1145/3627673.3679172
- https://doi.org/10.1145/3209978.3210065
- https://doi.org/10.1007/978-3-030-99739-7_38
- https://ir-measur.es/en/latest/getting-started.html
- https://pypi.org/project/ir-measures/
- https://doi.org/10.1007/978-3-030-99739-7_30
- https://doi.org/10.1145/3511808.3557207
- https://doi.org/10.1145/3626772.3657862
- https://doi.org/10.18653/v1/2024.eacl-demo.16
- https://github.com/amazon-science/RAGChecker
