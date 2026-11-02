# Competitive embedded-link selection

`retrieval_arena.embedded_competitive.v1` is an opt-in extension of the
[source-bound interface](embedded_interface.md). It preserves the historical
combined runner and v2 numerical selector. Corpus choices, models, prompts,
access, thresholds and prices remain consumer-owned.

Generation uses `disposition_schema`: exactly one supported proposal or explicit
negative per disclosed target. The request binds source and target IDs; missing,
duplicate or inconsistent dispositions fail validation inside the existing
bounded executor, permitting only its declared technical retry. Exact all-target
requests never omit a passage/target to pass token admission; over-cap packets
are unavailable with no replacement. Every negative disposition is retained.

The normal eligibility stage reviews every structural proposal. The new
`support_confirmation` role sees the same blinded source/target projection for
each eligibility-passing proposal, before selection. Both source-bound raw
responses and host resolutions are checked again. Confirmation is a support gate;
only the first review total ranks candidates. Unavailable/negative confirmations
are retained and never overridden. Every admitted pair requires both gates.

The common pool freezes accepted/rejected records, conflicts, distinct pairs,
top tiers, group budgets and semantic winners before scoring. Ties use the
consumer-declared fixed v1 hash rule SHA256(canonical([3801,source,target])), then
proposal ID. Duplicate directed pairs are refused. Optional consumer publication
stops before real scoring until a receipt binds this complete pool to its context
commit. Resume preserves the original clock and all counts/responses/capacity.

`competitive-select` uses k=1 per group and the unchanged v2 q=.25 two-schedule
selector. Z remains analytic q=0 and equals S. All top-tier alternatives remain
in the comparison, including failures. Noncompetitive groups use recorded
semantic fallback without running a solver. The primary seal records all groups
before separate q=.10 diagnostics on exactly the same tier. Each stage retains
100 MiB/group and 600 seconds/group, two CPUs/2 GiB/one native thread. Final
primary costs receive matched cached effort across arms; ranking time is separate.

Five exclusive group outcomes separate empty, insufficient top-tier competition,
competitive unresolved, resolved agreement and a qualified changed pair set.
Evaluability requires qualified finite values for every tier member. True/partial
ties, overlap and numerical/resource failure remain distinct. Reports include
pair intersections, symmetric difference and replacements, with separate group
denominators; no utility or population-inference endpoint is implied.

Copies/cards, host facts, graph deltas and externally anchored v2 ledgers use the
existing editor. A composite reviewed event binds both response/input digests;
full separate verdicts/resolutions remain in the frozen pool and card JSON.
There is no post-selection judging round. The opt-in aggregate ceiling is USD 30;
legacy model/combined modes retain USD 20. One inventory-bound combined consumer
authorization still precedes clients. Existing shared SDKs, CostLedger, persisted
circuits, timeout/retry rules and one-thread smoke are reused.

`unit_tests.test_embedded_competitive` is in `focused`: changed/unchanged winners,
true/partial ties, overlap, unavailable alternatives, lower-tier singleton,
empty/negative pools, confirmation disagreement, disposition coverage, stable
hash order, full fake-provider copies/ledgers, deterministic replay and restart.
Authored/mock outcomes establish plumbing, never real semantic validity.

Opt-in [constraint/context v2](embedded_constraints.md) retains reciprocal alternatives, counts duplicate occurrences once and validates selected assignments before materialization and replay. Historical defaults remain unchanged.

Consumers whose source projection can omit packets freeze exactly two original
`canary_source_ids` in the lane manifest, within `allowed_source_ids`. These are
validated before counting. Missing sources stay unavailable in the canary receipt;
later surviving packets never replace them. Omission of this field retains the
historical first-two-packets behavior. The diagnostic scorer receives the same
constraint version as primary selection and materialization.
