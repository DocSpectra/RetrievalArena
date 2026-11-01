# Fixed-view local magnetic objectives v1

`qualify-local-objective` is an offline immutable verification command. Its
request binds a portable input inventory, stage (`fixtures` or `development`),
`policy.json`, and development `cases.json`. Reserved assignments are refused.
No model, corpus acquisition or edit is performed by this operator. Numerical
algorithms live in `graph/local_objective.py`; the consumer freezes corpus,
regions, mechanical proposals, numerical policy and interpretation.

Use binary A, W=(A+Aᵀ)/2 and θ=2πq(A−Aᵀ). The descriptor is the smallest
eigenvalue of D⁺¹ᐟ²(D−W∘exp(iθ))D⁺¹ᐟ², including zero rows for isolates.
Save the complete lowest cluster (absolute width 1e-8 by default), its uniform
projector diagonal, effective support, degree vector, residual and gap. All
fixed nodes/components stay. No lowest-component selection is implemented.

The unit-phase energy counts each unordered pair once:
E=Σᵢ<ⱼ Wᵢⱼ|exp(iφᵢ)−exp(iθᵢⱼ)exp(iφⱼ)|².
This equals z*(D−H)z, with ||z||²=n. A positive lower bound is obtained from
n times the unnormalized minimum eigenvalue minus a numerical allowance;
nonnegativity always supplies zero. Feasible phase solutions supply upper
bounds, not global minima. Isolates have arbitrary phases and zero energy;
one lexicographic anchor fixes each component's global gauge.

The bounded deterministic schedule uses eight graph-independent starts;
paired comparisons add two identical cross-starts from their two scout bests.
Every graph receives 18 minimizations including its eight scouts. Save every
start, initial/final best phases, energy, convergence flag, gradient infinity
norm, iteration count and message. Stationarity defaults to 1e-7; a solver's
success flag alone does not pass this gate. No eigenvector phase extraction,
semantic interpretation of phase signs, uniqueness or global optimality is used.
Restart spread is a warning and an operational rank gate, not a rigorous bound
on distance from an unknown global minimum. Caller policy is frozen before data.

`compare` refuses existing/duplicate/reciprocal/self/outside/component-crossing
additions. Thus existing weights/phases remain fixed, and added terms are
nonnegative. The true minimum total energy cannot decrease. A computed negative
change beyond tolerance is a numerical comparison failure. Report total change,
old-edge energy at the edited solution, added-edge energy, fixed-baseline-phase
added cost, and normalizations by baseline W and added W separately. Decreasing
an average through a growing denominator is not an inconsistency repair.
An objective's narrow proposed use is equal-budget marginal inconsistency
ranking among independently supported edits; documentation utility is untested.

All additions change W and degree normalization. The normalized eigenvalue
remains descriptive unless a consuming study independently admits an objective.
Ordinary q=0 spectra, graph connectivity, degree, reciprocity, reachability and
finite directed distance accompany comparisons. Forestation, gauge/reversal
invariance, cycle aliasing, localization, no-op and boundary cases are mandatory
consumer qualification records. Semantic shortcuts require independent support
and host placement admission: numerical algorithms do not detect misleading prose.

Fresh outputs contain every case/charge/start and a hashed output manifest,
runtime, peak Linux RSS, source/image/dependency identities and zero-call record.
Launchers keep network none, read-only inputs/source, two CPUs and 2 GiB. The
Python numerical library has no corpus or operating-system assumptions; the
existing immutable image remains linux/amd64 under RA-D09. No dependency change.
