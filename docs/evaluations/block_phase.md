# Component and block phase comparison v1

`compare-block-phase` runs an inventory-bound offline comparison in the immutable
verification image. `graph/block_phase.py` owns reusable partition, caching,
phase and rank logic; `block_phase_audit.py` owns the portable audit. Consumer
`policy.json` supplies a finite matrix and `cases.json` supplies authored fixtures
or explicitly development-assigned graphs. Reserved assignments are refused.
No corpus acquisition, language model, semantic review or corpus edit is performed.

The energy and positive phase convention are exactly [local objective v1](local_objective.md).
For unrestricted unit phases the global minimum is additive over weak components
and over biconnected edge blocks, including bridges. Each edge belongs to one
block; shared articulation phases can be matched by a rigid phase rotation of
each child block in the block-cut forest. Thus independent block minima are
simultaneously attainable. This reasoning does not apply to arbitrary overlapping
neighborhoods, normalized eigenvalue minima or externally pinned boundary phases.

Compare old and edited partitions by exact node/arc digests and cancel identical
partitions before any solve. Multiple additions can merge blocks and must be
evaluated jointly. Isolates remain explicit partitions. Original source groups,
external boundaries and all population accounting belong to the consumer and do
not change. The reporting denominator is the full original core's unordered W
sum, frozen across candidates and prefixes. A difference of normalized averages
using changing denominators is not this quantity.

The implementation estimates each changed partition using the existing eight-start
solver independently under each consumer seed. It retains every initial/final
phase, objective, residual, termination and iteration count. The lowest-energy
stationary start supplies each feasible upper bound; lack of one refuses the
comparison. An unsuccessful or worse start is diagnostic, not a veto on another
stationary solution. This changes the finite algorithm and admission policy,
not the old result. Identical partition/q/policy/seed keys reuse cached solves.
Every serialized energy is recomputed. Differences of feasible upper bounds are
not bounds on the true marginal optimum. Negative differences beyond the numerical
floor are refused. No global optimality or documentation benefit is asserted.

`ordering` compares a candidate's finite schedule range to others, with a declared
absolute floor. Overlap can force abstention. Ranges are sensitivity diagnostics,
not confidence intervals. A genuine all-candidate tie uses input order; the
consumer must pre-order by its semantic/ID policy. A single candidate cannot
demonstrate magnetic guidance. This routine does not implement a full corpus
selector: production selection and source conflict handling remain the next
implementation increment.

The third comparison is added-edge cost at phases integrated along a sorted-ID
BFS spanning forest. Reverse-ID BFS is a fixed basis-dependence diagnostic.
These costs are repeatable but not optimized; on cyclic graphs the chosen tree
ignores non-tree constraints. They never replace an unavailable optimized score.

Inputs and outputs carry inventory hashes, all solve records, runtime, Linux peak
RSS, source/image/dependency identity and zero model calls. The existing platform
limitation RA-D09 remains: the reusable library is Python/NumPy/SciPy/NetworkX,
but only linux/amd64 is exercised by this image. No dependency is added. Resource
ceilings are consumer policy; launchers retain two CPUs, 2 GiB, network none and
single-thread numerical libraries. Historical v1 APIs and receipts remain intact.
