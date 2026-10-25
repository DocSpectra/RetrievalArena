# Magnetic attribution v1

`retrieval_arena/graph/magnetic_attribution.py`, version
`magnetic_attribution.v1`. It is a diagnostic over
[`normalized_magnetic_spectrum.v1`](normalized_magnetic_spectrum.md). That
module supplies the operator, the dense solver and the numerical
qualification. It is imported unchanged, and its receipts keep their meaning.
This module adds:
- deterministic cycle selection from topology alone;
- declared arc-reversal controls with exact per-pair assertions;
- ground-cluster mass and edge energy;
- the finite-change account below.

Every receipt says `optimization_admitted: false` and
`scientific_utility: unvalidated`. Nothing here is a retrieval or answer
score.

## Cycle selection (topology only)

`nonreciprocal_cycles(nodes, edges, lengths, max_cycles, budget)` enumerates
simple directed cycles that use only **nonreciprocal** arcs (s→t present,
t→s absent). The rules:
- Lengths are searched in the declared order.
- Start nodes are taken in ascending id order. A cycle is rooted at its
  smallest id and every other node must be larger. This is the canonical
  rotation, so each cycle is met once.
- Successors are visited in ascending order, depth first.
- One expansion is one partial path created, the one-node start path
  included.
- The search stops at `max_cycles` or at `budget` expansions.

The result records the cycles, their arcs, the count found per length, the
expansions and a status: `max_cycles_reached`, `budget_exhausted` or
`search_complete`. A budget stop is not evidence that no further cycles
exist. The function reads only ids and arcs. A test runs it with the
eigensolvers patched to raise.

`validate_cycle` refuses a cycle that is not simple, has an absent arc or uses
a reciprocal arc (`reciprocal-cycle-arc`). `reverse_arcs` refuses absent,
duplicated or reciprocal arcs.

## Controls

`control_checks(nodes_a, edges_a, nodes_b, edges_b, control, changed_arcs)`
asserts exact per-pair equalities. They are not marginal correlations.

| Control | Asserted |
|---|---|
| `matched_cycle_reversal` | B is A with the declared directed cycle reversed. The cycle's arcs are nonreciprocal in A. Equal: ordered nodes, node and arc counts, W, every in-, out- and weighted degree, reciprocal-arc count, source and sink counts, out-degree Gini, weak and strong partitions (canonical node sets), q = 0 normalized matrix. Trophic incoherence agrees within `trophic_absolute` (1e-9). |
| `global_reversal` | B is every arc reversed. W, the q = 0 matrix and both partitions are equal; in- and out-degrees are swapped. |
| `forest_reorientation` | B is A with the declared arcs reversed. The undirected graph is a forest. W and the q = 0 matrix are equal. |

**Invariant.** A directed-cycle reversal leaves W and `in − out` unchanged.
The trophic level system `diag(in + out) h − (A + Aᵀ) h = in − out` is
therefore unchanged. So is its minimized objective, because
Σ_arcs (h_t − h_s − 1)² depends only on W and `in − out`.

Any structural descriptor that changes is listed in
`descriptors_that_change`. Its status is reported, not called controlled. A
failed check sets `quarantined: true`. The pair is still measured, but it
cannot support a matched-control claim. Global-reversal and forest pairs are
negative spectral controls, not degree-matched positive cases
(`degree_matched` says which).

## Ground cluster, mass and energy

Each graph view at each q is solved by `magnetic_spectrum.spectrum` with
`modes = n`. This gives the full dense, qualified spectrum: complex128 and the
spectrum module's residual, orthogonality, Hermitian and bound checks.

**Ground cluster.** The cluster holds every eigenvalue within
`cluster_absolute` (1e-8) of λ1. The cluster records:
- rank k and width;
- the next gap;
- the mean eigenvalue;
- the absolute residual r = max over the cluster of ‖L u − λ u‖₂ for unit u.

The boundary is qualified when the cluster is the whole space, or when the
next gap exceeds max(`cluster_absolute`, `resolution_factor` · r). Otherwise
attribution is `unavailable` (`cluster_boundary_unresolved`).

For the orthonormal cluster U (n × k):

```
mass[i]        = sum_r |U[i,r]|^2 / k
f[i,r]         = U[i,r] / sqrt(d[i])            (0 on an isolate)
energy[{i,j}]  = W[i,j] * sum_r |f[i,r] - exp(1j*theta[i,j]) * f[j,r]|^2 / k
theta[i,j]     = 2*pi*q*(A[i,j] - A[j,i])       (the spectrum module's phase sign)
```

Each unordered edge is summed once (i < j in view order). The masses sum to
1. The energies sum to the **cluster mean eigenvalue**: λ1 when k = 1,
labelled separately when k > 1. `energy_sum_residual` reports the identity
error. Both fields are invariant under a global phase and under any unitary
rotation U → UQ inside the eigenspace. A single vector from a clustered
ground mode is never used, and vectors are never matched across graphs.

**Reported per side:**
- effective support 1/Σ mass², and its fraction of the view;
- the total-variation distance to the q = 0 degree mass d/vol (high-degree
  concentration is not automatically a structural effect);
- isolate mass;
- the top nodes and edges.

For a pair, each side also reports:
- mass on the changed cycle's nodes beside their node share and degree-volume
  share;
- energy on the changed edges beside their W share;
- the highest-energy edge outside the changed edges.

Energy fractions are `null` when the total energy is not above the
resolution max(`resolution_floor`, `resolution_factor` · r). They are never
divided by a near-zero denominator.

Edge energy says **where a mode's energy resides**. It does not say what
caused a finite eigenvalue change.

## Pair difference and finite-change account

`delta_lambda1.raw = λ1_B − λ1_A`, and it is resolved iff
|Δ| > max(1e-8, 10 · (r_A + r_B)). The receipt keeps the raw delta, the
tolerance and the baseline λ1. Numerical resolution is not practical
importance. λ2 and its delta are descriptive.

When both ground modes are simple and qualified:

```
delta_lambda1 = u_A* (L_B - L_A) u_A  +  (lambda1_B - u_A* L_B u_A)
                fixed-mode term          relaxation term (<= 0 by Rayleigh)
```

W and d are fixed, so only the reversed edges enter the fixed-mode term. The
receipt gives:
- the term per changed edge, as an energy difference under the fixed u_A;
- their sum, checked against the matrix form;
- the largest unchanged-edge contribution, which is exactly 0;
- the account residual against the measured delta.

This is a Rayleigh-energy decomposition of the declared intervention, not an
independent causal effect per edge. With a clustered ground mode on either
side the account is `unavailable`, and the invariant cluster fields remain.

`permutation_check` re-solves a side on a seeded node permutation. It compares
the full spectrum, the cluster projector distance
(`magnetic_spectrum.subspace_distance`), mass and per-edge energy mapped back
by id.

## Jobs and operator

The config is `retrieval_arena.magnetic_attribution_config.v1`. It holds
`spectrum_config` (dense only), `attribution_config` (`AttributionConfig`
fields) and `jobs`. Job kinds:

| Kind | Keys |
|---|---|
| `cycle_selection` | `bundle`, `view`, `lengths`, `max_cycles`, `budget` |
| `pair` | `bundle_a`, `bundle_b`, `view` (applied to both), `q`, `control`, `changed_arcs`, optional `selection_ref` {`job_id`, `index`} (the reversed arcs must equal that earlier selection's cycle on the same view hash), optional `permutation_seed` |
| `single` | `bundle`, `views`, `q` (e.g. a degeneracy control: full view with an isolate against its LWCC) |

Bundles are `retrieval_arena.graph_bundle.v1` (see the spectrum contract).
Solves are cached per (view hash, q) within a run.

```powershell
.\scripts\run-verification.ps1 -Command measure-magnetic-attribution -InputRoot <in> -OutputRoot <out> -Receipt <receipt>
```

The input root holds `operator-request.json`
(`retrieval_arena.magnetic_attribution_operator_request.v1`), `config.json`
and exactly the named bundles. BLAS runs single-threaded, with 4 GB of
container memory and network none. Outputs:
- `magnetic_attribution.v1.jsonl`: one record per job, in config order. Each
  carries both module hashes, the config, the policies, and the graph and
  view hashes.
- `magnetic_attribution_manifest.json`: record hashes and the artifact
  inventory.
- `artifacts/`: per view hash and q, `tables/<slug>.nodes.csv` (degree, mass,
  degree mass), `tables/<slug>.edges.csv` (W, θ, energy for every edge) and
  `modes/<slug>.npy` (the cluster U, complex128, in view node order).
- `operator_receipt.v1.json`: image, commit, tree, lock and status,
  self-hashed.

The tests are `unit_tests/test_magnetic_attribution.py`, in the `focused`
suite. Only linux/amd64 has been run.
