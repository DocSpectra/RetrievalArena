# Ranked-retrieval metrics v2 specification

Status: accepted implemented ranked-v2 design and implemented dual-profile
PRE-S019q-33A contracts; independent 33A review pending

Decision dates: 2026-08-15 (v2) and 2026-08-18 (refinement and BERTScore
diagnostic addenda). Reference status: S01--S27 form the original verified
refinement audit; S28 is the separately verified BERTScore extension. No
E-EXP1 result, held-out evidence, model, scorer, or experiment was inspected or
run for either decision.

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

Deterministic agent task completion is a separate RA evaluation family defined
in [Portable deterministic task-completion evaluation](task_completion_evaluation_plan.md).
It does not alter, extend, or combine with the ranked-v2 metric family.

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

### Automatic BERTScore F1 diagnostic

**Question:** How closely does generated answer wording align with a reference
under contextual token embeddings rather than exact token identity?

BERTScore F1 is accepted as an optional `operational_automatic` answer
diagnostic (S28). It aligns candidate and reference tokens using a pinned
contextual-embedding provider, computes semantic precision and recall from
those alignments, and reports their harmonic mean. It is more sensitive to
paraphrase than lexical F1, but it is not ranked-retrieval effectiveness,
factuality, evidence attribution, contradiction detection, or a primary human
judgment. It never creates qrels, transfers relevance, enters a composite, or
replaces the human-calibrated semantic-answer family.

Reproducible execution requires the exact package, model/checkpoint revision,
tokenizer, baseline/rescaling policy, language setting, device/dtype policy,
and artifact hashes to be admitted and recorded. Runtime network download is
forbidden. When the registered model artifact is absent, BERTScore is explicitly
unavailable rather than silently substituted or reported as zero.

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
- BERTScore F1 likewise uses generated and reference answer text; it can reward
  paraphrase, but cannot establish that the answer is supported or factually
  correct.

## Metric comparison

| Metric | Primary question | Partial credit | Rank-sensitive | Multi-support coverage | Main blind spot | Role in E-EXP1 |
| --- | --- | --- | --- | --- | --- | --- |
| Evidence Recall@k | How much required evidence is in context? | Yes | No, within top `k` | Yes | Does not distinguish rank 1 from rank `k` | Primary |
| Complete-support@k | Is every required target in context? | No | No | Yes, strict all-or-nothing | Hides how close an incomplete result was | Secondary |
| nDCG@k | Is relevant evidence ordered near the top? | Yes | Yes | Yes | Binary labels limit graded usefulness | Secondary |
| MRR@k | How early is the first relevant target? | Yes | Yes | No, only first hit | Ignores later required evidence | Secondary |
| Lexical F1 | Does answer wording overlap the reference? | Yes | Not applicable | No | Is not retrieval effectiveness | Legacy diagnostic |
| BERTScore F1 | Does answer wording align under contextual embeddings? | Yes | Not applicable | No | Does not prove factuality, support, or contradiction absence | Automatic diagnostic |

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

## PRE-S019q-33L refinement-aware literature addendum

### Search and verification protocol

The addendum searched primary and authoritative sources through 2026-08-18.
Queries combined metric, incomplete-judgment, diversity/aspect, dynamic-corpus,
passage-answerability, attribution, and assessment terms with SIGIR, CIKM,
WSDM, ECIR, SIAM SDM, TREC/NIST, ACL, and CLEF/LongEval sources. Inclusion
required a metric or procedure definition, a directly applicable assumption,
or a primary precedent for evolving collections, support attribution, or
bounded assessment. Package documentation and citation counts were not
scientific authority.

Every one of the 27 retained sources was independently audited against its
claim-bearing surface. Public status is `verified` for S01--S27. Access status
is 22 `full_text_read`, three `official_protocol_full_text`, two
`abstract_only`, zero `metadata_only`, and zero `inaccessible`. S05 and S10
are the abstract-only records; their claims are narrowed below. S20, S26, and
S27 are the official-protocol full-text records.

A 2026-08-19 read-only Zotero collection audit found every S01--S27 item in
`DocSpectra - IR` (`929HWPB4`), so all are separately `zotero_present`. A
batched child-item audit found no attachments for any of the 27 items. Zotero
ownership and attachment state do not establish public or full-text access.
No Zotero item was added or changed. No Crossref-backed repository checker was
available. The public audit corrected the author metadata for S13, the title
and author metadata for S14, and added the verified DOI
`10.1145/1390334.1390437` for S23. S08 full text was read from an
author-uploaded ResearchGate reproduction and corroborated against DOI and
Google Research metadata; no stable author/institutional full-text copy was
verified, so that third-party-host limitation is explicit. S27 is protocol
evidence only; no TREC RAG 2026 result was available at the cutoff.

| Evidence family | Primary/authoritative records | Evaluation unit and judgment assumption | Supported conclusion | Limitation carried into this design |
| --- | --- | --- | --- | --- |
| AP, R-precision, bpref | S01--S05 | ranked documents; complete relevant set for AP/R-precision, judged positives and negatives for bpref | definitions, macro aggregation, pooling, and abstract-supported assessor-variation/comparative-stability caution from S05 | S05 supplies no adjudication procedure; none binds whole-ranking or query-specific `R` to DocSpectra's fixed model-context `k` |
| aspect/diversity | S06--S10 | documents by subtopic/intent, with binary or graded intent labels and sometimes intent probabilities | alpha-nDCG, intent-aware measures, ERR, D#, and subtopic recall are standard diversity semantics; S10 supports only intent-probability/per-intent-grade semantics from its abstract | S10 supplies no retained exact cutoff or aggregation claim; alternative user intents are not necessarily mandatory evidence components |
| evolving collections | S11--S14, S26 | time-indexed collections and collection-bound qrels | corpus version, judgment scope, and renewed maintenance must be explicit | no source validates similarity-only relevance transfer across changed content |
| answer-bearing support | S15--S16 | passages/targets with answerability or fixed-snapshot provenance judgments | ranked support and downstream answer/provenance evaluation can be separate | passage-to-document max inference can hide missing multi-part evidence |
| attribution and answer quality | S17--S21 | claims/sentences, cited sources, nuggets, and generated answers | answer correctness, coverage, citation completeness, and citation support are separate layers | semantic support commonly needs humans or models and is not ranked `@k` relevance |
| bounded assessment | S22--S25 | pooled or probability-sampled query-item pairs, duplicate assessment, adjudication | literature supplies comparison-specific stopping/sampling precedents and S24's domain-specific duplicate/adjudication procedure | DocSpectra's ceiling, strata, allocation, minimum-one, and fixed stop are project choices; adaptive known-system ranking and reusable endpoint estimation are different objectives |
| current protocol boundary | S27 | 2026 retrieval and RAG tasks over a new corpus | retrieval and generation remain distinct tasks with explicit corpus identity | protocol only; no outcome claim |
| automatic paraphrase sensitivity | S28 | generated and reference answer tokens aligned by contextual embeddings | BERTScore precision, recall, and F1 provide a deterministic model-bound paraphrase diagnostic | embedding similarity does not establish factuality, provenance, contradiction absence, or retrieval effectiveness |

The complete per-source metadata, access level, full-text surface, inspected
sections/pages, claim scope, public-verification state, Zotero item key, and
attachment state are retained in the context-owned PRE-S019q-33L evidence
matrix. The table above records only the implications owned by Retrieval
Arena.

### Registered dispositions

The review treated 32A as the accepted prospective baseline, not an immutable
answer: a demonstrated refinement-specific question or validity defect could
have added, demoted, replaced, or reclassified an endpoint without seeing
outcomes. None of the audited families met that bar. The addendum therefore
selects the smallest orthogonal family. `adopt_confirmatory` and
`adopt_secondary` are intentionally unused: the accepted Recall@k primary and
nDCG@k, complete-support@k, and MRR@k secondary family remains unchanged for
the evidence-backed overlap, cutoff, judgment-cost, and failure-mode reasons
below, not because change was prohibited.

| Candidate family | Disposition | Scientific question and authority | Required judgments and cost | Failure modes and overlap | Retrieval Arena consequence |
| --- | --- | --- | --- | --- | --- |
| AP / MAP | `reject` | Whole-ranking precision-recall ordering (S01, S03) | complete relevant-set denominator; ordinarily treats unjudged as nonrelevant | not inherently at the registered `k`, sensitive to missing positives, and overlaps Recall@k plus nDCG@k | no AP/MAP endpoint or AP@k variant |
| R-precision | `reject` | Precision at query-specific relevant count `R` (S01) | known `R` and judgments through rank `R` | cutoff can exceed model context, changes under split/consolidation, and at a common cutoff carries the same relevant-count information as recall/precision | no R-precision endpoint |
| bpref | `calibration_only` | Judged-positive versus judged-negative ordering under incomplete judgments (S01, S02) | an imported artifact with both positive and negative human judgments; zero automatic-profile cost | unavailable from positive-only support mappings; pool/selection bias and small `R` or `N`; not requirement coverage | optional provider-owned BPref only in `scientific_calibration`, never primary or composite |
| probability-sampled AP/nDCG estimators | `future_extension` | Design-based estimation and intervals under recorded sampling (S23), distinct from adaptive known-system ranking (S22) | inclusion probabilities, registered estimator/provider, and a probability sample | implementation/provider complexity; a convenience or delta-selected pool is invalid | 33A records sampling provenance but adds no inferred-AP scorer |
| alpha-nDCG, ERR-IA, intent-aware, and D# measures | `reject` | Diversity across alternative or probabilistic intents (S06--S10) | intent/subtopic labels, optional probabilities/grades, and metric-specific parameters | mandatory evidence requirements are not alternative user intents; adoption by name would misstate semantics | no diversity-named endpoint for requirement coverage |
| mandatory evidence-requirement coverage | `diagnostic_only` | Whether each registered semantic component has at least one physical realization in top `k`; subtopic recall is only an analogy (S09) | frozen requirement IDs and explicit snapshot bindings; zero human cost when bindings/lineage are deterministic | decomposition quality, empty bindings, and many-to-one concealment if evidence-unit bindings are omitted | add exact project-specific coverage and all-requirements diagnostics, separately labeled |
| evolving-corpus transfer and lineage | `diagnostic_only` | Whether an assessed evidence unit is demonstrably identical across snapshots (S11--S14, S26) | content/provenance identity for automatic transfer; otherwise an external judgment | copy-context contradiction, rewrite, novelty, removal, ambiguity, cycles, and many-to-many promotion | narrow fail-closed integrity policy and class inventories; not a standard effectiveness metric |
| answer-bearing passage relevance | `calibration_only` | Whether a candidate unit actually answers the query (S15) | imported passage/target human judgments with unit and grade frozen | target aggregation can conceal missing requirements; automatic inference is forbidden | calibration qrels may add answerability, but ranked target identity and requirement diagnostics stay distinct |
| BERTScore precision/recall/F1 | `diagnostic_only` | Whether generated and reference answer tokens align contextually (S28) | no human judgment at runtime; exact frozen reference answers and a locally available pinned model artifact | model/checkpoint sensitivity, negation and factuality blind spots, reference dependence, and no evidence-grounding guarantee | optional automatic answer diagnostic with full provider provenance; never an endpoint, qrel source, lineage rule, or composite |
| semantic answer quality | `calibration_only` | Whether the reconstructed answer is correct and complete (S16, S18--S21) | human claim/nugget judgments in the imported artifact | can vary independently of retrieval; paraphrase breaks lexical proxies; model judges are not primary authority | separate calibration result family; legacy lexical answer metrics remain diagnostics |
| attribution / citation correctness | `calibration_only` | Whether required claims are cited and cited evidence supports them (S17--S21) | claim/sentence-source human judgments and a citation-needed policy | multi-source claims, granularity, paraphrase, and assessor cost | separate calibration family, never folded into retrieval or answer score |
| paraphrase robustness | `calibration_only` | Whether semantically rewritten support or answers preserve meaning (S17--S21) | blind human semantic judgments for sampled rewrite candidates | lexical disagreement, contextual contradiction, and model-judge dependence | automatic lineage remains unresolved; only imported judgments may resolve sampled cases |
| bounded human calibration methodology | `calibration_only` | Pooling/reliability (S04), abstract-supported assessor variation/comparative stability (S05), sampling/stopping precedents (S22--S23), S24 duplicate assessment and blinded adjudication, and model-assisted allocation limits (S25) | one frozen review packet, assessor work, duplicates, and adjudication | leakage, convenience sampling, outcome-aware stopping, and incomplete artifacts | implement export/import and provenance only; automatic execution never waits for calibration |

### Accepted project-specific requirement formulas

For query `q` and snapshot `s`, let `E_q` be the non-empty frozen set of
mandatory `evidence_requirement_id` values, `B_qs(e)` the possibly empty set of
physical `support_target_id` values explicitly bound to requirement `e`, and
`R_qs(k)` the first `k` unique admitted targets. Define:

```text
RequirementCoverage@k(q,s)
  = (1 / |E_q|) * sum over e in E_q of 1[B_qs(e) intersect R_qs(k) is non-empty]

AllRequirements@k(q,s)
  = 1 iff every e in E_q has B_qs(e) intersect R_qs(k) non-empty; else 0
```

OR applies only among physical realizations of the same requirement. AND
applies across requirements. An empty treatment binding therefore remains an
uncovered requirement rather than disappearing from the denominator. A target
may satisfy multiple requirements only when each requirement has a separate,
explicit, independently auditable evidence-unit binding inside that target.
Target relevance to one requirement never promotes relevance to another.

These are exploratory diagnostic conventions, not standard Recall or subtopic
recall. Per-query values and query-macro summaries are permitted; connected
support groups remain the uncertainty unit when intervals were preregistered.
They never join a composite.

### Calibration-only BPref semantics

For a calibrated query with `R > 0` judged relevant and `N > 0` judged
nonrelevant items, let `N_r` be the count of judged nonrelevant items ranked
above relevant item `r`. The provider-owned TREC semantics are:

```text
BPref(q) = (1/R) * sum over judged relevant r
           [1 - min(R, N_r) / min(R, N)]
```

Unjudged items are ignored. BPref is unavailable with missing calibrated
judgments, `R = 0`, or `N = 0`; it is query-macro aggregated only when the
registered calibration policy declares it. It is not truncated to `k`, does
not replace Recall@k, and cannot appear in `operational_automatic`. A sampled
artifact yields artifact-bound descriptive BPref; it does not imply a
full-frame estimate or confidence interval. Any design-based uncertainty
analysis requires a prospectively registered estimator and inclusion
probabilities and remains outside 33A.

### Exact lineage admission procedure

Dynamic-test-collection evidence supports snapshot-bound evaluation and
explicit collection maintenance, not a universal transfer theorem. Retrieval
Arena therefore treats the following as a DocSpectra-specific integrity and
lineage policy:

1. Every transfer binds query, requirement, source/destination snapshots,
   source/destination targets, normalized evidence, bounded local context,
   structural owner, repository/document provenance, policy identity, and
   self-hash.
2. `identity_unchanged`, `exact_target_relocation`, and
   `exact_evidence_unit_preserved` transfer only after all registered identity
   fields and provenance predicates pass. A structural rename is admissible
   only through explicit repository/document lineage.
3. A contextual copy transfers only if the registered evidence, local context,
   and owner lineage remain policy-exact and the destination contains no
   contradiction. Mere substring equality is insufficient.
4. Semantic rewrites, paraphrases, novel support, contradictory destinations,
   removed support, ambiguous mappings, and unsupported many-to-many shapes do
   not transfer. They enter explicit unresolved, unjudged, removed, or invalid
   inventories.
5. Many-to-many history is admissible automatically only after decomposition
   into unique, acyclic, non-conflicting evidence-unit edges that each satisfy
   the exact predicate. No transitive, duplicate, lexical, embedding, model,
   or treatment-authored promotion is permitted.

Standard target metrics remain snapshot-specific. A query with no admitted
positive target in a snapshot makes standard ranked metrics unavailable; its
stable mandatory requirements remain visible as uncovered in the separate
project diagnostic.

### Bounded calibration procedure

Calibration is optional, external, immutable, and artifact-only. A
registration may declare a smaller design, but the default ceiling for one
cross-corpus deep dive is:

- a frozen deduplicated frame built from the union of registered top-`k`
  query-target pairs, registered support bindings, and unresolved/novel
  candidates across all conditions and snapshots;
- at most 240 primary human judgments total, not per run or corpus; 240 is a
  DocSpectra governance ceiling, not a literature-derived constant;
- if the frame exceeds 240, stratified simple random sampling without
  replacement over snapshot, candidate class, and top-`k` rank band, with a
  preregistered seed, proportional largest-remainder allocation, at least one
  item per non-empty stratum when the ceiling permits, and recorded inclusion
  probabilities;
- an independently and randomly selected 10% duplicate-assessment sample
  (`ceil(0.10 * primary_count)`) and blind adjudication of every duplicate
  disagreement; 10% is a project adoption of S24's domain-specific precedent,
  not a universal rate;
- at most 288 rating/adjudication actions at the 240-item ceiling; 288 is the
  arithmetic bound 240 primary + 24 duplicate + at most 24 adjudications, not
  a source-reported constant;
- assessor views randomized and stripped of condition, system, outcome, rank,
  delta, and lineage-class labels; views include only the query/requirement,
  candidate evidence, and registered bounded context needed to judge support;
- exact agreement and Cohen's kappa for categorical labels, with raw counts;
  agreement is assessor evidence, not retrieval effectiveness;
- a fixed stopping rule: stop when the frozen frame is exhausted or all fixed
  quotas are complete. Labels, system deltas, or preliminary scores cannot
  expand, shrink, or redirect the sample. The chosen
  snapshot/candidate-class/rank-band strata, proportional largest-remainder
  allocation, minimum-one rule, and fixed quota stop are preregistered
  DocSpectra choices rather than literature constants.

The packet, frame, sample, seed, guidelines, assessor classes, blinding,
budget, metric declarations, and adjudication policy must be frozen, and the
completed judgment artifact imported, before registered outcome reveal for a
`confirmatory` calibration result. Any post-reveal judgment, procedure change,
or outcome-informed sample is `exploratory`. Missing or incomplete calibration
is valid absence for `operational_automatic` and invalidates only the requested
calibration family.

The 240-item governance ceiling is a bounded DocSpectra design choice anchored
only by TREC-COVID's approximate one-minute-per-assessment cost context. The
10% duplicate rate is a project adoption of the TREC Legal precedent, whose
domain and Topic Authority workflow are not universal. The 288 maximum is
arithmetic, and the exact sampling/allocation/stopping policy is
preregistered project governance.

### Rule-class ownership

| Accepted rule | Classification |
| --- | --- |
| Recall@k, nDCG@k, MRR@k, and calibration-only BPref arithmetic | standard IR metric semantics |
| pooling/frame freeze, probability sampling, duplicate assessment, blinding, agreement, and adjudication | standard judgment methodology |
| stable requirements, snapshot bindings, exact transfer predicates, unresolved/removed inventories, and no relevance self-promotion | DocSpectra-specific integrity or lineage policy |
| 240 governance ceiling, project-adopted 10% duplicate rate, strata, largest-remainder allocation, minimum-one rule, fixed quota stop, and 288 arithmetic bound | exploratory diagnostic convention implemented as preregistered project calibration governance |
| RequirementCoverage@k, AllRequirements@k, lineage counts, lexical F1, BERTScore F1, answer/provenance diagnostics, and any post-reveal calibration | exploratory diagnostic convention |

### Ownership and successor implications

PRE-S019q-33A must implement the two profiles, exact requirement formulas,
lineage admission and inventories, review-packet sampling/provenance fields,
judgment import, calibration-only provider BPref availability, and separated
answer/attribution calibration families. It must also add optional
provider-pinned BERTScore precision/recall/F1 as an automatic answer diagnostic
with explicit unavailability and no runtime download. It must not add AP/MAP,
R-precision, diversity-named metrics, inferred-AP scoring, a composite, an
interactive assessor, or any model-created primary relevance. This addendum
changes 33A's exact obligations and therefore must be reflected in its prompt,
but does not start implementation.

No additional standard confirmatory or secondary endpoint is accepted. The
32A primary/secondary family and fixed context-budget binding remain frozen.

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

The PRE-S019q-33A implementation preserves the same boundary. Its explicit
dual-profile ingress consumes ranked-v2 artifacts and emits distinct
`automatic_*` or `calibration_*` v1 result/replay families. It does not change
v1 parsing or ranked-v2 schemas/arithmetic, manufacture qrels, or reinterpret
either predecessor family. The complete operator and schema contract is
[Dual-profile ranked evaluation](dual_profile_evaluation.md).

## PRE-S019q-33A implementation

The dual-profile core is corpus-neutral and lives entirely in Retrieval Arena.
It adds strict profile, requirement, binding, lineage, BERTScore-provider,
review-packet, judgment-import, result, evidence, audit, report, and replay
contracts. Exact automatic lineage, RequirementCoverage@k and
AllRequirements@k, optional offline provider-pinned BERTScore, deterministic
blinded packet export, immutable judgment import, agreement, and
calibration-only provider BPref implement the accepted 33L decisions without
adding another standard endpoint or a composite.

The independent 33A review corrective makes the destination inventory
explicit: source qrels are bound to the lineage source snapshot, only admitted
positive exact edges produce destination qrels, and ranked scoring plus top-k
judged counts consume only that destination inventory. Stale source targets,
negative source judgments, and removed, novel, or unresolved classes remain
unjudged. It also binds executable BERTScore code to the hashed registered
distribution tree, closes the review-frame allocation/probability inventory,
and enforces label/relevance equivalence before calibration scoring.

Human assessment remains an external activity. Retrieval Arena only exports
and imports immutable artifacts. Automatic execution remains terminal when
calibration is absent. No implementation path assumes a DocSpectra source
layout, task pack, prompt, held-out root, or pipeline module.

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

### Refinement-addendum sources

- S01: NIST, *Common Evaluation Measures*:
  https://trec.nist.gov/pubs/trec15/appendices/CE.MEASURES06.pdf
- S02: Buckley and Voorhees, *Retrieval Evaluation with Incomplete
  Information*: https://doi.org/10.1145/1008992.1009000
- S03: Voorhees, *Overview of TREC 2003*:
  https://trec.nist.gov/pubs/trec12/papers/OVERVIEW.12.pdf
- S04: Zobel, *How Reliable Are the Results of Large-Scale Information
  Retrieval Experiments?*: https://doi.org/10.1145/290941.291014
- S05: Voorhees, *Variations in Relevance Judgments and the Measurement of
  Retrieval Effectiveness*: https://doi.org/10.1016/S0306-4573(00)00010-8
- S06: Clarke et al., *Novelty and Diversity in Information Retrieval
  Evaluation*: https://doi.org/10.1145/1390334.1390446
- S07: Agrawal et al., *Diversifying Search Results*:
  https://doi.org/10.1145/1498759.1498766
- S08: Chapelle et al., *Expected Reciprocal Rank for Graded Relevance*:
  https://doi.org/10.1145/1645953.1646033; institutional metadata:
  https://research.google/pubs/expected-reciprocal-rank-for-graded-relevance/;
  inspected full text was an author-uploaded third-party reproduction:
  https://www.researchgate.net/publication/220269787_Expected_reciprocal_rank_for_graded_relevance
- S09: Clarke, Craswell, and Soboroff, *Overview of the TREC 2009 Web Track*:
  https://trec.nist.gov/pubs/trec18/papers/WEB09.OVERVIEW.pdf
- S10: Sakai and Song, *Evaluating Diversified Search Results Using
  Per-Intent Graded Relevance*: https://doi.org/10.1145/2009916.2010055
- S11: Soboroff, *Dynamic Test Collections: Measuring Search Effectiveness on
  the Live Web*: https://doi.org/10.1145/1148170.1148220
- S12: Galuščáková et al., *LongEval-Retrieval: French-English Dynamic Test
  Collection for Continuous Web Search Evaluation*:
  https://doi.org/10.1145/3539618.3591921
- S13: Roberts et al., *TREC-COVID: Rationale and Structure of an Information
  Retrieval Shared Task for COVID-19*: https://doi.org/10.1093/jamia/ocaa091
- S14: Roberts et al., *Searching for Scientific Evidence in a Pandemic: An
  Overview of TREC-COVID*: https://doi.org/10.1016/j.jbi.2021.103865
- S15: Craswell et al., *Overview of the TREC 2022 Deep Learning Track*:
  https://trec.nist.gov/pubs/trec31/papers/Overview_deep.pdf
- S16: Petroni et al., *KILT: a Benchmark for Knowledge Intensive Language
  Tasks*: https://doi.org/10.18653/v1/2021.naacl-main.200
- S17: Rashkin et al., *Measuring Attribution in Natural Language Generation
  Models*: https://doi.org/10.1162/coli_a_00486
- S18: Gao et al., *Enabling Large Language Models to Generate Text with
  Citations*: https://doi.org/10.18653/v1/2023.emnlp-main.398
- S19: Djeddal et al., *An Evaluation Framework for Attributed Information
  Retrieval using Large Language Models*: https://doi.org/10.1145/3627673.3679172
- S20: TREC 2024 RAG evaluation procedure:
  https://trec-rag.github.io/annoucements/evaluation/
- S21: Upadhyay et al., *Overview of the TREC 2025 Retrieval Augmented
  Generation Track*: https://trec.nist.gov/pubs/trec34/papers/Overview_rag.pdf
- S22: Carterette, Allan, and Sitaraman, *Minimal Test Collections for
  Retrieval Evaluation*: https://doi.org/10.1145/1148170.1148219
- S23: Yilmaz, Kanoulas, and Aslam, *A Simple and Efficient Sampling Method for
  Estimating AP and NDCG*: https://doi.org/10.1145/1390334.1390437
- S24: Cormack et al., *Overview of the TREC 2010 Legal Track*:
  https://trec.nist.gov/pubs/trec19/papers/LEGAL10.OVERVIEW.pdf
- S25: Takehi et al., *LLM-Assisted Relevance Assessments: When Should We Ask
  LLMs for Help?*: https://doi.org/10.1145/3726302.3729916
- S26: NIST relevance-judgment and TREC methodology guidance:
  https://trec.nist.gov/data/reljudge_eng.html and https://trec.nist.gov/howto.html
- S27: TREC RAG 2026 protocol: https://trec-rag.github.io/
- S28: Zhang et al., *BERTScore: Evaluating Text Generation with BERT*
  (ICLR 2020): https://arxiv.org/abs/1904.09675 and
  https://openreview.net/forum?id=SkeHuCVFDr
