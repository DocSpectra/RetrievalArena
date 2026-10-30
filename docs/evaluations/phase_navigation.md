# Normalized phase and matched-entry navigation

Two offline operator stages implement `phase_navigation.v1`. The measurement
stage receives graph bundles, page text, query IDs/text and cached cosine scores only;
it refuses undeclared files and never receives answers, judgments or support
targets. It measures normalized phase descriptors and executes a shared
outgoing-link traversal for semantic and uniformly sampled entry pages. The
separate scoring stage joins saved compact trials to known-positive target
sets and fixed view-node lists. Unjudged pages are never treated as negatives.

## Graph descriptors

`normalized_phase_dispersion.v1` is

`1 - abs(sum_i |v_i| v_i) / sum_i |v_i|^2`.

It is computed separately for the first and second eigenvectors. Global phase
rotation and node permutation leave it unchanged; zero amplitudes contribute
zero. A mode whose adjacent spectral gap is unresolved is unavailable. This
is circular dispersion, not arithmetic variance of wrapped angles.

`normalized_magnetic_subspace_edge_polarization.v2` selects the lowest ten
modes, including the ground mode, and expands through a complete unresolved
boundary cluster. For `P=UU*`, it is the absolute directed-edge sum of
`imag(P[t,s])` divided by the sum of `sqrt(P[s,s] P[t,t])`. It is invariant
to basis changes within the selected complete subspace. Full-spectrum zero
is reported as degeneracy, not useful signal. It is a spectral correlation,
not literal flow, procedural order, or a quality objective.

Both descriptors use `normalized_magnetic_spectrum.v1`'s symmetric normalized
operator and its residual, orthogonality, Hermitian, and eigenvalue-bound
checks. Their tolerances and operator convention are recorded with each
receipt. The historical unnormalized `magnetic_phase.py` remains unchanged.

## Navigation and scoring

Both entry conditions use one traversal implementation: outgoing neighbors in
ID order, one hop, neighbor budget 20, candidate budget 40 including the seed,
final context at most four, cached cosine minus `0.02 * shortest_distance`,
then distance and ID. Semantic entry chooses the highest cached cosine (ID
breaks ties). Random entry is uniform without replacement, separately keyed
by master seed, family, view and query ID. Every start is one trial; all trials
are retained. No global fallback or support-informed pruning is implemented.

The evaluator reports known-positive intersections at the reached candidate
set and at the final selected-page set. Per-query `H` is one if any selected
page intersects the view-projected known-positive target set. Random starts
are averaged within query before query macro aggregation. `C_semantic`,
`C_random`, and `D=C_semantic-C_random` are page-level known-support access
measures. A zero means no recorded known-positive page was selected; it does
not mean other pages are irrelevant or that an answer failed. Candidate,
target-distance, seed-only and dead-end counts are diagnostics.

## Operator

The offline commands are `measure-phase-navigation` and
`score-phase-navigation`; both use the standard immutable verification image
and exact input inventory. The first writes graph features, fixed-view node
lists, and compact per-query trial records. The second consumes those records
with target lists and view-node lists, producing trial-level summaries. Input
hashes, output hashes, source commit/tree, dependency lock, image digest and
runtime are recorded. The code is portable Python; operator execution has
only been qualified on the recorded platform for a particular study.

Focused tests: `unit_tests.test_phase_navigation`.

For view-matched semantic top-k comparisons, support identity overlap and uncapped reachability diagnostics on saved semantic trials, see [support-access comparison](support_access.md). Historical nonseed-positive fields keep their original meaning.
