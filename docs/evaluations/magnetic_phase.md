# Magnetic edge-relative flow v2

Standalone corpus diagnostic in `retrieval_arena/graph/magnetic_phase.py`.
It is not a retrieval/answer score or an admitted verification operator.
NumPy is needed only for this optional diagnostic; no dependency lock or image
has been changed. ICSC selects and audits it separately.

For binary simple directed adjacency A, W=(A+Aᵀ)/2 and
L=diag(W1)−W⊙exp(2πiq(A−Aᵀ)), unnormalized complex128. Duplicate edges,
self loops and unknown endpoints are ignored, consistent with historical
matrix construction. Duplicate node IDs are rejected.

Select indices 1 through min(K,n−1), K=10 by default, of the ordered full
Hermitian eigensystem. Index 0 is excluded for historical comparability; it
is **not** assumed to be a trivial magnetic mode. Every selected eigenvalue
must be separated from both neighbors by more than `gap_relative` times
max(1, ||L||∞). This includes the lower and upper selection boundaries.
Repeated/numerically unresolved clusters produce unavailable, never an
arbitrary basis statistic. No claim of invariance to basis rotation within
a repeated eigenspace is made for a scalar; such graphs get unavailable.

For each isolated unit mode v, eligible directed edge (s,t) has both node
amplitudes greater than `amplitude_relative` times max_u |v_u|. Compute
δ=arg(v_t conjugate(v_s)), directly, without anchoring node phases. This
cancels independent global eigenvector rotations. Neutral means |δ|≤τ or
π−|δ|≤τ, where τ=`phase_absolute`. Undefined amplitudes are excluded.
Let F,B,N be eligible positive, negative and neutral counts. Define
I_v=|F−B|/(F+B+N), and final I=mean_v I_v, in [0,1]. All directed binary
edges have equal weight; modes have equal weight. Neutral edges remain in
the denominator. Each mode must meet the declared minimum coverage (.95).
Empty/edgeless graphs or failed spectral/coverage checks return value=null.

This measures average within-mode edge orientation imbalance, not the sign
of an averaged node phase and not a conserved flow. Cancellation occurs
within each mode, not across modes. It is a new aggregation, not numerical
replay of historical `flow_imbalance` or `phase_var`. Directed-degree
imbalance remains a separately named control. Magnetic transport phase is
not added: this construct measures spectral phase progression rather than
connection-corrected energy. No invariance to arbitrary node-local gauge
rotations is claimed.

Changing q need not be monotonic. At q=0 isolated real modes have neutral
0/π differences and value zero if covered; degeneracy may make it
unavailable. q→−q conjugates eigenvectors, swapping F/B and preserving I.
Reversing every edge also conjugates L and swaps endpoints; I stays equal
when validity holds. This scalar has no absolute navigation direction.

Default tolerances: eigen residual relative Frobenius 1e−10,
orthogonality Frobenius 1e−10, eigen gaps 1e−8·max(1,||L||∞), amplitude
1e−8·max|v|, phase 1e−7 radians. These deliberately exceed double precision
roundoff; empirical tolerance sweeps and permutation checks are required
on each frozen study population. Passing a single receipt is not study
qualification. Optimization admission remains false in this primitive;
only an external qualified freeze can admit a target. Scientific utility
remains unvalidated even after numerical qualification.

Receipts carry version, graph/config/code hashes, solver/dtype/runtime,
selected modes, adjacent gaps, residual/orthogonality, per-mode amplitude
thresholds and edge coverage, neutral/excluded counts and failure reasons.

## Magnetic subspace edge polarization v1

`measure_subspace` implements the separately named
`magnetic_subspace_edge_polarization.v1`. The isolated-mode v2 candidate
remains diagnostic after the ICSC tolerance sweep failed on two Docker
snapshots. This replacement is a continuous amplitude-weighted subspace
correlation, not a forward/backward edge-count imbalance.

Use the same unnormalized L, q and binary graph policy. Select the lowest
K=10 eigenvectors **including index 0**. Expand the upper boundary to
include every adjacent eigenvalue whose gap is ≤gap_relative·max(1,||L||∞).
Do not split repeated or unresolved clusters. Reject an inclusion decision
within max(residual_relative,100·machine_epsilon)·max(1,||L||∞) of this
threshold. K, actual dimension, boundary gap and expansion are reported.
When the complete spectrum is selected, P=I, and the descriptor is zero;
report that fact rather than claiming a useful low-frequency signal.

Let U contain the selected orthonormal columns and P=UU*. For directed
edge (s,t), z_st=P_ts=Σ_j U_tj conjugate(U_sj) and
w_st=√(P_ss P_tt). Define

    S = |Σ_(s,t)∈E Im(z_st)| / Σ_(s,t)∈E w_st.

The denominator must exceed residual_relative (default 1e−10); otherwise
return unavailable. Cauchy–Schwarz gives |Im(z_st)|≤w_st, so S∈[0,1].
Equivalent interpretation: each edge contributes the normalized imaginary
correlation Im(z)/w, weighted by w; zero-weight edges contribute zero.
Opposing contributions cancel before the final absolute value. This
captures signed quadrature polarization of low-frequency spectral edge
correlations. It does not measure literal navigation flow, procedural order,
directed-degree imbalance, or answerability. q itself encodes direction,
so a large value can reflect the operator convention; utility requires
independent controls and task evidence.

All graph edges are included (coverage=1, exclusions=0). Weak node
amplitudes are downweighted, never used as divisors for individual phases.
Neutral edges are counted for diagnostics when |Im(z)|≤residual_relative·w;
this label does not change the scalar. No discontinuous sign test or wrapped
angle aggregation occurs. amplitude_relative, phase_absolute and
minimum_coverage are unused by this subspace formula; the common config
retains them for reproducibility but changing them cannot change its value.

For any unitary R, (UR)(UR)*=UU*: independent mode phase rotations and
valid rotations within repeated eigenspaces leave the result invariant.
Permutation gives P′=ΠPΠᵀ, so corresponding edges preserve z,w.
q→−q conjugates P, swapping the signed numerator; S stays equal.
Reversing all edges conjugates P and exchanges endpoints; S stays equal.
q=0 gives a real projector and S=0. No monotonic response to q is claimed.
Magnetic transport phases are not inserted: this descriptor measures
spectral correlation in the canonical adjacency gauge, not transported
energy or a quantity invariant to arbitrary node-local gauge transforms.

The same Hermitian/residual/orthogonality checks apply. Optimization
admission stays false in every primitive receipt. A numerically qualified
frozen corpus may be nominated for a later target freeze, with its precise
name and semantics; no scientific quality objective is established here.
