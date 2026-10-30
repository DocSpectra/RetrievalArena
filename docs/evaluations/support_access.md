# Cached support-access comparison

`compare-support-access` is an offline immutable verification operator using
`support_access_comparison.v1`. It compares cached semantic top one/top four
with saved one-hop graph predictions, inside the identical eligible page view.
The primary budget is a maximum page count, not computation or token cost.
The secondary semantic top-m baseline uses the graph prediction's actual count.

`retrieval_arena/graph/support_access.py` owns ranking validation, outcome-blind
prediction replay, separate positive-set evaluation, unrestricted reachability,
first-failure attribution, aggregation and joint query-text-group bootstrap.
It reuses `phase_navigation.traverse_once`, historical `score_trial` semantics
and the support-reachability BFS in `diagnostics`; no new traversal policy or
zero-imputation rule is introduced. Historical operators remain unchanged.

## Input and stage contract

The request schema is `retrieval_arena.support_access_operator_request.v1`:
command, config path (`config.json`), exact regular input inventory and its
canonical SHA-256. Config schema `retrieval_arena.support_access_config.v1`
declares queries/trials/targets/reference-scores/corpus paths, jobs (job ID,
family, full_scope or fixed_lwcc kind, bundle, node IDs), primary job,
traversal knobs, descriptive semantic depths, bootstrap draws/seed and output
byte/runtime ceilings. Authoritative validation is in
`containers/verification/support_access.py`; undeclared files and escaping
paths are refused. Every query must have a trial and reference score in every
view. Duplicate keys, duplicate cache IDs and missing/nonfinite eligible
scores are refused. Ties use exact descending cached score then document ID.

Queries carry query_id, question and cached score map. Trials carry job_id,
query_id, identical question and the complete saved semantic trial. Graph
bundles provide nodes/edges and jobs define induced eligible views. Labels
carry query_id and distinct known_target_ids. Full-scope targets must exist;
component views project targets and explicitly retain ineligible query IDs.
Reference scores are saved phase-navigation score rows; every historical
semantic hit, seed/candidate/nonseed hit, selected/candidate count and dead-end
value is reproduced before aggregation.

First, `freeze_prediction` receives only scores, graph and saved predictions;
it checks every saved field by replay, and writes `frozen_predictions.jsonl`
(`retrieval_arena.support_access_prediction.v1`). Only then does the evaluator
open targets. Functions do not pass labels to ranking, traversal or selection.
This combined operator mounts evaluation inputs, so this is an explicit code
and stage separation, not separate-container access isolation. It records and
rechecks the frozen prediction hash. Scoring emits
`retrieval_arena.support_access_evaluation.v1` in `per_query_comparison.jsonl`.

## Estimands and diagnostics

Hit is intersection with view-projected known positives. The complete paired
table is both_hit, graph_only, semantic_only and neither_hit. The query-macro
G4-B4 difference equals (graph_only - semantic_only)/N. B4 misses bound
improvement in this binary saved-label metric. Recovery and loss fractions
have explicit denominators; zero denominators are unavailable. Support IDs,
overlap, exclusives and positive-set coverage reveal complementarity obscured
by any-hit. They neither establish necessary evidence nor complete answers.
Unjudged pages are never relabeled negative. The historical nonseed-positive
`expansion_added_hit` is preserved under a clearly qualified output name;
seed-miss/graph-hit and net G4-B1 are distinct quantities.

Failure priority is final_selection, traversal_admission, beyond_hop_limit,
directed_unreachable. Uncapped directed and undirected BFS start at the actual
seed. A deterministic shortest route is illustrative, never successful access.
Admission diagnosis records neighbor positions, offers and cap exclusions;
unreachability distinguishes wrong direction within a weak component from
different components. Stage/cell cross-tabs, semantic ranks, selected counts,
dead ends, candidate access and declared semantic-only depths are descriptive.

The bootstrap samples exact query-text groups once per draw and carries all
views and conditions together. All original rows retain their query weights;
the estimand is query-macro, not a mean of equally weighted group means.
Percentile intervals are conditional on the frozen graphs and saved labels.
No orientation or random start is an independent query sample.

Outputs include aggregate JSON, up to two deterministic primary-view example
cards per paired cell, a source/image/input/output-pinned manifest and a
self-hashed receipt. Corpus excerpts illustrate local text, not sufficiency.
Caller-owned provenance is joined by unchanged query ID. The operator enforces
at most 4 GiB output and 7,200 seconds; the standard sh/PowerShell adapters
bound execution to four CPUs and 8 GiB. No new dependencies or platform
assumptions are added; current verification-image portability limits remain.

Focused fixtures: `unit_tests.test_support_access`, including a hand-computable
end-to-end operator bundle, all paired cells, failure stages, incomplete labels,
ties/missing scores, actual-count comparison and duplicate-text bootstrap.

## Corpus-aware generation and evaluation (v2)

`support_access_corpora.v2` is a separate contract; v1 replay stays unchanged.
`retrieval_arena.graph.support_access_rank` generates complete scores and new
predictions inside a dedicated offline image built with
`containers/verification/Dockerfile.support-access-rank` from a provisioned
embedding runtime. Only questions, full corpora/graphs, historical parity
scores/predictions and the local model/lock are mounted. Labels are refused by
an exact input allowlist. Source commit/tree are image labels. The launcher
must pin the image ID, mount inputs/model read-only, disable networking, and
bound execution to four CPUs/8 GiB. No model download or fallback is allowed.

Generation config `retrieval_arena.support_access_generation.v2` declares
corpus IDs, the fixed traversal, encoder policy/model/revision/batch and frozen
parity tolerances. Full UTF-8 Markdown uses Python universal newline decoding,
the local model's recorded truncation/tokenizer/pooling, normalized float32
embeddings converted to Python floats, and the historical Python dot sum.
Content and the full model/encoding/runtime policy key each cached vector;
unchanged IDs cannot reuse rewritten text. Distinct encodes, reused occurrences,
encode invocations/batches, model files and input/output inventories are recorded.
Scores must cover exactly every final page and remain finite. No chunking.
Encoding preserves repeated text occurrences and ordered batching. An explicit
label-free `encoding_reference` can supply the historical complete encoding
panel/corpus for the first corpus parity check; evaluation still uses only the
declared queries and eligible pages. Every retained text must match its reference.
Batch-sequence identity and position enter vector keys. Deduplication is not
assumed numerically equivalent, and no thread override is introduced.

`compare-support-access-corpora` uses evaluation config/request v2 in the normal
immutable verification image. Input inventory is exact. Corpora, predictions,
scores, surfaces and targets join by `(corpus_id, query_id)` or
`(surface_id, query_id)`. Generation consistency assertions are explicitly
separate from genuine historical replay using supplied saved evaluations.
Direct-credit surfaces reject derived credit and targets outside their corpus.
Empty positives retain `no_known_positive_in_arm` and unavailable failure stage.

The caller freezes a common positive-eligible mask by labels/scope alone. The
operator verifies this mask, reports its paired contrasts plus all-panel
saved-label access (zero only for this named estimate), and reports each arm's
eligible population descriptively. It returns selector components, graph
increment interactions, complete per-query transitions and label-only surface
contrasts. Label surfaces never modify predictions. A positive interaction may
arise from a weaker semantic baseline; components must accompany interpretation.

One joint exact-question-text bootstrap resamples all original groups and keeps
all member IDs/arms/surfaces together, then applies fixed masks inside each draw.
The statistic is query-macro. Empty eligible draws are counted, not imputed.
Intervals condition on fixed corpora/pooled labels. The operator preserves all
v1 diagnostics, semantic depth curves and deterministic matched examples. It
does not establish complete answers, isolated topology effects or causality.

`unit_tests.test_support_access_corpora` is in focused verification and supplies
hand-computable generation/evaluation, corpus/label namespaces, content-cache
identity, complete-score/new-page/tie rules, masks, empty targets, direct-credit
boundaries, actual-count comparison, exact interaction arithmetic, joint duplicate
bootstrap and parity tests. v1 supplies unchanged failure-stage/replay fixtures.
