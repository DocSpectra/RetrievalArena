# Dual-profile ranked evaluation

Retrieval Arena exposes two strict, versioned evaluation profiles:

- `operational_automatic`: zero-human evaluation from frozen qrels, ranked
  predictions, evidence requirements, snapshot bindings, and exact lineage;
- `scientific_calibration`: optional artifact-only evaluation from a frozen
  review packet and immutable imported judgments.

The profiles are separate result and replay families. They share registered
input identities, but no report, aggregate, replay, or evidence export combines
their values. There is no composite score.

## Commands

```powershell
python -m retrieval_arena.cli evaluate-profile --config <dual-profile.json> --out-dir <output>
python -m retrieval_arena.cli score-bertscore --provider <provider.json> --answer-pairs <answers.jsonl> --out <scores.json>
python -m retrieval_arena.cli export-review-packet --candidates <candidates.jsonl> --policy <review-policy.json> --out <packet.json>
python -m retrieval_arena.cli import-judgments --judgments <judgments.json> --packet <packet.json> --policy <review-policy.json> --out <import.json>
```

All commands are noninteractive. They do not read stdin, open an editor or
browser, invoke a human workflow, use the network, or download a model.

## Configuration

`retrieval_arena.dual_profile_config.v1` rejects unknown fields and contains:

- `profile_policy_path`;
- `ranked_dataset_path`, `automatic_qrels_path`, and the non-empty
  `automatic_qrels_snapshot_id` that binds those source judgments,
  `ranked_predictions_path`, and `ranked_metric_policy_path`, all using the
  accepted ranked-v2 contracts;
- `requirements_path`, `bindings_path`, `lineage_path`, and
  `requirement_predictions_path`;
- optional `answer_pairs_path`, `bertscore_provider_path`, and
  `bertscore_scores_path`;
- `review_policy_path`, `review_packet_path`, and `judgment_artifact_path`,
  which must all be absent in `operational_automatic` and all be present in
  `scientific_calibration`.

The paths are resolved relative to the JSON configuration. The evaluator is
corpus-neutral: no path convention, task-pack layout, experiment name, or
DocSpectra module is assumed.

## Profile policy

`retrieval_arena.evaluation_profile_policy.v1` binds the profile,
`confirmatory` or `exploratory` classification, context-item cutoff, exact
ordered metric-family declarations, evidence-requirement, binding, and lineage
policy/artifact hashes, calibration requirement, network and interaction
prohibitions, and a canonical self-hash.

The family declarations are closed:

| Family | Measures or evidence | Classification |
| --- | --- | --- |
| Standard ranked retrieval | evidence Recall@k, nDCG@k, reciprocal rank@k | provider-owned standard IR |
| Project diagnostics | complete-support@k, RequirementCoverage@k, AllRequirements@k, exact lineage counts | project-specific, never named standard Recall |
| Automatic answer diagnostics | lexical precision/recall/F1 and optional BERTScore precision/recall/F1 | diagnostic only |
| Calibrated relevance | BPref and answer-bearing relevance | imported-human calibration only |
| Calibrated answer quality | semantic answer quality and paraphrase robustness | imported-human calibration only |
| Calibrated attribution | attribution correctness | imported-human calibration only |

V1 commands and artifacts remain on their existing paths. Ranked-v2 artifacts
remain `retrieval_arena.ranked_*.v2` and are passed to the unchanged
ranked-v2 scorer. Neither is implicitly upgraded, reinterpreted, or compared
to a dual-profile artifact. The new ingress accepts ranked-v2 artifacts
explicitly; it does not manufacture qrels for v1 data.

## Evidence requirements and snapshot bindings

`retrieval_arena.evidence_requirements.v1` defines a non-empty ordered set of
stable `evidence_requirement_id` records for every query.

`retrieval_arena.requirement_bindings.v1` binds each requirement to zero or
more physical `support_target_id` values in each registered snapshot. Every
binding identifies its own evidence unit, normalized evidence hash, bounded
context hash, structural owner, and repository/document provenance.

`retrieval_arena.requirement_prediction.v1` binds one query's ordered unique
targets to the same corpus snapshot and context-item cutoff as ranked-v2.

For a query and snapshot, RequirementCoverage@k is the fraction of frozen
requirements that have at least one explicitly bound target in the first `k`
unique targets. AllRequirements@k is one only when all frozen requirements are
covered. Physical bindings are OR alternatives inside one requirement;
requirements are AND obligations across the query. Empty bindings remain in
the denominator. A consolidated target satisfies multiple requirements only
through separate evidence-unit bindings.

## Exact support lineage

`retrieval_arena.support_lineage.v1` accepts automatic transfer only for:

- `identity_unchanged`;
- `exact_target_relocation` with exact repository/document lineage;
- `exact_evidence_unit_preserved`.

All three require exact normalized evidence, bounded context, and structural
owner identity, complete provenance, and no ambiguity, contradiction, or
conflict. `semantic_rewrite_unresolved`, `novel_support_unjudged`, and
`support_removed` are inventoried and never promoted.

Duplicate and cyclic edges are rejected. A many-to-many history is admissible
only as unique, direct, acyclic, non-conflicting
`many_to_many_decomposed` evidence-unit edges with the complete exact
predicate. Lexical overlap, embeddings, model confidence, treatment authorship,
and transitive history cannot create relevance.

The destination-qrel composer requires the declared automatic-qrel source
snapshot to equal the lineage source and the ranked dataset snapshot to equal
the lineage destination. It copies only positive source qrels over admitted
edges, deduplicates equal destination judgments, rejects conflicting grades,
and records the exact lineage-record hash as judgment provenance. The original
source target inventory is never scored directly. Source negatives,
nontransfer classes, and source targets without an admitted edge remain
unjudged at the destination; no negative transfer is invented. Standard
ranked scoring and judged/unjudged top-k counts consume this same canonical
destination inventory.

When a snapshot leaves one query with no admitted positive target, its
standard ranked measures are explicitly unavailable with
`zero_relevant_items`; stable requirements remain in the separate coverage
denominator. Any macro over the remaining provider-scored queries is labeled
`available_queries_only` rather than silently treating the missing query as
zero. If no query has an admitted positive destination target, every standard
query and the standard aggregate are explicitly unavailable; the provider is
not invoked.

## Offline BERTScore

`retrieval_arena.bertscore_provider.v1` pins:

- `bert-score` package version and local distribution path/hash;
- checkpoint identifier and revision;
- tokenizer identifier and revision;
- local model, tokenizer, and optional baseline artifact paths and hashes;
- baseline and rescaling policies;
- language, device, and dtype;
- `runtime_download: false`;
- a canonical provider self-hash.

The artifact-producing `score-bertscore` ingress requires
`package_artifact_path` to be the exact hashed local distribution tree that it
imports. It validates the tree's sole `bert_score` package and `.dist-info`
name/version, removes any ambient `bert_score` module, loads only from the
registered tree, verifies every loaded provider module path, disables bytecode
writes, sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, and revalidates
all artifact hashes after execution. A wheel/archive may document an external
provider but is not executable by this ingress. No same-version ambient
installation, injected callable, runtime installation, or fallback is
trusted. A provider-owned
`retrieval_arena.bertscore_scores.v1` artifact must bind the exact provider and
answer-pair hashes. Missing registered provider material makes only BERTScore
explicitly unavailable. A hash mismatch or provider substitution fails
admission. Supplying a scores artifact without its provider is rejected.

BERTScore precision, recall, and F1 remain model-bound paraphrase diagnostics.
They do not create qrels, transfer support, establish factuality, detect every
contradiction, prove attribution, or replace semantic human judgments.

## Review packets

`retrieval_arena.review_policy.v1` freezes the packet and candidate-pool
identity, snapshots, guidelines, assessor classes, allowed assessor IDs, seed,
budget, sampling, blinding, stopping, and reveal state.

The deterministic exporter:

1. deduplicates the cross-condition frame by snapshot/query/target;
2. verifies identical assessor-visible evidence for duplicates;
3. stratifies by snapshot, candidate class, and top-k rank band;
4. uses simple random sampling without replacement with proportional
   largest-remainder allocation and minimum one per non-empty stratum when the
   ceiling permits;
5. freezes the seed and primary ceiling plus a non-assessor frame inventory of
   pair IDs and sampling strata, verifies its count/hash, and deterministically
   rederives allocations, selected/duplicate IDs, and inclusion probabilities
   against that exact inventory;
6. freezes inclusion probabilities and a fixed, non-outcome-driven stop;
7. selects `ceil(0.10 * primary_count)` independent duplicate assessments;
8. randomizes assessor order;
9. removes condition, system, outcome, rank, delta, and lineage-class labels
   from every assessor record;
10. self-hashes the frame, frozen inventory, selected sample, assessor
    projection, and packet.

The primary ceiling is at most 240. The adopted 10% duplicate rate is a
project use of the domain-specific S24 precedent, not a universal constant.
At the ceiling the arithmetic maximum is 240 primary + 24 duplicate + at most
24 adjudications = 288 actions.

## Immutable judgment import

`retrieval_arena.judgment_artifact.v1` binds the exact packet, pool, policy,
dataset, query set, snapshots, assessor classes/IDs, reveal state, records, and
self-hash. Unknown fields are rejected, including condition labels. Every
frozen primary and duplicate assessment must be present. Adjudication records
bind exactly two assessments. Post-reveal material is always exploratory.
`outcome_revealed_at_import` must be Boolean. Every primary, duplicate, and
adjudication record enforces `nonrelevant` exactly when relevance is zero and
`relevant` exactly when relevance is positive before agreement or BPref input
is formed.

Import reports raw label-pair counts, exact agreement, and Cohen's kappa.
Agreement is assessor evidence, not retrieval effectiveness. Answer-bearing
relevance, semantic answer quality, attribution correctness, and paraphrase
robustness remain separate calibrated families.

## Calibration-only BPref

BPref is delegated to the registered `ir_measures==0.4.3` /
`pytrec_eval` provider and is available only in
`scientific_calibration`. Unjudged items are ignored. A query with no judged
positive or no judged negative has an explicit unavailable reason instead of
zero; the query-macro value is unavailable unless every scored query is
available. BPref is not truncated or renamed as an `@k` metric, does not replace
Recall@k, and does not imply a full-frame estimate for a sampled artifact.

## Outputs and replay

Automatic execution writes only:

- `automatic_result.v1.json`;
- `automatic_replay.v1.json`;
- `automatic_audit.v1.json`;
- `automatic_evidence.v1.json`;
- `automatic_report.v1.md`.

Calibration writes the parallel `calibration_*` family. Result and replay
schemas, hashes, family names, classification, judgment source, qrels scope,
lineage inventory, judged/unjudged counts, provider identities,
available/unavailable states, and every input hash remain explicit. Identical
inputs produce byte-identical artifacts.

## Security and scope

- Human work remains external and artifact-driven.
- Runtime network/model download is forbidden.
- The evaluator does not inspect or own an authoring pipeline.
- No result family may grade itself or mutate its qrels, packet, or policy.
- No AP/MAP, R-precision, alpha-nDCG, ERR-IA, D#, inferred-AP, or composite
  endpoint is exposed by this implementation.
