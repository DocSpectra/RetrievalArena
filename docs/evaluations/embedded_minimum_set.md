# Separated finite-estimate minimum-set selection

`embedded_minimum_set.select_minimum_set` explicitly opts into
`retrieval_arena.embedded_selection.minimum_set.v1`, restricted to maximum=1.
No v2 default changes. Admission, feasibility, semantic tiers/order, numerical
policy, cache keys and score provenance reuse `embedded_selection_v2`.

Failures take precedence (resource limit, then any unavailable candidate), then
singleton, full-tier tie and unique strictly separated minimum retain v2 behavior.
For two-schedule clipped comparison extrema L/U and epsilon=1e-6, form
C={i:L_i<=min U+epsilon}. Accept a tied set only for at least two members with
max_C U-min_C L<=epsilon and every outsider L_j>max_C U+epsilon. Select the
frozen semantic-first member. Other overlaps fall back over the whole tier.
Ranges are sensitivity diagnostics; the set is not certified globally optimal.
Tiny negative raw costs remain recorded. Trace records member IDs, all ranges,
envelope spread, outsider gap, selected tie ID and fallback reason.

`replay_scores` checks v2 graph/pool/policy bindings, exact seed/start/solve keys,
recomputes every energy/full gradient and reconstructs every candidate comparison
without allowing a missing cache entry to invoke an optimizer. Old v2 replay must
match. Selection binding separately names the new version. Unavailable candidates
remain unavailable. S=Z uses the unchanged analytic q=0 control.

Offline `minimum-set-select` / `minimum-set-verify` stages under the existing
`refine-embedded-v2` immutable command accept a consumer's inventory-bound
historical output bundle and source freeze. They verify historical inventories,
replay all scores/controls, seal primary outcomes, materialize isolated actual
copies through the existing editor/ledger and export changed blocks. A separate
verification checks all copies, externally anchored tips, recomputed saved fits,
selection/structure parity and fresh identical-policy solves for changed groups.
Historical outputs remain read-only. No provider code/client is used.

The structural export includes before/after arcs, actual w/theta terms, every
schedule/start phase and exact directed isomorphism maps preserving before-edge
membership and the distinguished added edge. Simple-cycle equal-residual energy
is explanatory, not a global-optimum certificate. Corpus interpretation belongs
to the consumer. Same 2 CPU/2 GiB, 600-second/100-MiB group watchdog and one native
thread; no dependencies added. Pure Python remains portable; only existing
linux/amd64 verification is exercised (RA-D09 remains open).
