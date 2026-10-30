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
