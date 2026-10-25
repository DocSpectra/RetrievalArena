# Normalized magnetic spectrum v1

`retrieval_arena/graph/magnetic_spectrum.py`, version
`normalized_magnetic_spectrum.v1`. It is a graph descriptor, not a retrieval or
answer score and never an optimization target (`optimization_admitted: false`
in every receipt). The legacy unnormalized constructor in
`magnetic_phase.py` and its receipts are unchanged. Old fields keep their old
meaning. Use this module for normalized values.

## Operator

For binary directed adjacency A:

```
W = (A + A.T) / 2                      # 1 reciprocal, 1/2 one-way
d = W.sum(axis=1)
Hq = W * exp(2j * pi * q * (A - A.T))
r[i] = 1/sqrt(d[i]) if d[i] > 0 else 0
L_q = diag(d > 0) - r[:, None] * Hq * r[None, :]
```

complex128. The phase sign is fixed: an arc s→t carries +2πq at (s, t). The
conjugate convention has the same spectrum, but consumers must not mix them.
Isolated nodes stay in the view: their row and column are zero, so each adds
an eigenvalue 0. `I - H_norm` is never substituted. The q = 0 operator uses
the **same W**, so `weighted_normalized_lambda2_q0` is the weighted ordinary
normalized Laplacian. The historical binary-skeleton value is reported
separately as `legacy_binary_skeleton_normalized_lambda2`. Dividing
unnormalized eigenvalues by a degree statistic is not matrix normalization.
The unit tests show that it gives different spectra.

`normalized_operator_from_weights(w, theta)` is the general form. A uniform
rescaling of `w` at fixed `theta` leaves the operator unchanged.
`unnormalized_magnetic_matrix` is the explicit `D - H` comparator. It equals
the legacy constructor entrywise (parity test).

## Input contract

A bundle is `retrieval_arena.graph_bundle.v1`:
- `graph_id`;
- sorted unique string `nodes`, including isolates;
- sorted unique `edges`;
- optional named `node_sets`;
- `canonical_graph_sha256 = sha256(canonical JSON {nodes, edges})`;
- free provenance fields.

Admission refuses duplicate or malformed nodes, malformed edges, unknown
endpoints and a hash mismatch. A bundle must already be canonical. The
general `admit_graph` removes and counts duplicate arcs and self loops.

Views:
- `full`;
- `lwcc`: the largest weak component, ties broken by the smallest node id;
- `induced` on a named node set;
- `induced_lwcc`: the LWCC of that induced graph.

Each receipt records the view rule, LWCC tie count, node and edge counts, and
the view, selected-node and selected-edge hashes.

## Spectrum

`spectrum(nodes, edges, q, config)` returns the lowest `modes` (default 3)
eigenvalues in ascending order, raw. Nothing is clipped, no absolute value is
taken and the first mode is never dropped. A magnetic ground state may be
zero or positive. "Second nontrivial eigenvalue" is not used.

A view with fewer nodes than a mode index returns null for that mode with a
reason. An empty view is `unavailable`. Each value has a `near_zero` flag
(|λ| ≤ `near_zero_absolute`).

Solvers are selected explicitly:
- **`dense`:** `numpy.linalg.eigh`, full spectrum. It reports the spectrum
  minimum and maximum, the full [0, 2] bound check and `numerical_zero_count`.
- **`sparse`:** `scipy.sparse.linalg.eigsh` on `2I - L` (largest algebraic),
  with a seeded complex start vector, `tol` and `maxiter`. A QR plus
  Rayleigh–Ritz step on the returned subspace restores orthonormality inside
  repeated eigenvalues. It never asserts total nullity. Non-convergence is
  recorded as a failure, never accepted.

Qualification uses frozen tolerances, all in `SpectrumConfig`:

| Check | Tolerance |
|---|---|
| Relative residual ‖LV − VΛ‖_F / max(1, ‖L‖_F) | 1e-10 |
| Orthogonality max \|VᴴV − I\| | 1e-10 |
| Hermitian max \|L − Lᴴ\| | 1e-12 |
| Computed modes inside [0, 2] | 1e-10 |
| Near-zero flag | 1e-9 |

A failed check sets `status: failed` and keeps the raw values for audit.
Consumers must not use failed values.

Dense and sparse results are compared by eigenvalues and by projector
distance on separated subspaces. Arbitrary eigenvector phases are never
compared.

## Structure and hierarchy comparator

`structural_descriptors` covers the view's:
- nodes and arcs;
- weak components;
- isolates and isolate fraction;
- LWCC coverage;
- largest strongly connected component fraction (`scipy.sparse.csgraph`);
- reciprocal-arc fraction (denominator: arcs);
- sources (out > 0, in = 0) and sinks, with fractions over view nodes;
- out-degree Gini over all view nodes;
- W-degree summaries.

`trophic_incoherence` is the generalized trophic incoherence F0 of MacKay,
Johnson and Sansom (2020, *R. Soc. Open Sci.*, doi:10.1098/rsos.201138):
- Levels solve `diag(in + out) h - (A + Aᵀ) h = in - out`, grounded once per
  weak component (minimum level 0).
- F0 = Σ A_ij (h_j − h_i − 1)² / Σ A_ij over components with edges.
- Edgeless components are counted and excluded. An edgeless view is
  unavailable.
- Path F0 = 0. A directed cycle and a reciprocal pair give F0 = 1.

It is a descriptive linear-hierarchy comparator, not a documentation-quality
label. A single scalar does not separate every tree orientation.

## Known limits (consequences of the construction)

- Reversing every arc conjugates L: the spectrum is identical.
- At fixed weights, reorienting a forest's arcs is a diagonal gauge
  transform: the spectrum is unchanged. Normalization commutes with the gauge.
- Cycle flux aliases: a directed four-cycle at q = 0.25 has flux 2π and is
  balanced (λ1 = 0).
- A fully reciprocal graph is q-independent.
- The q response need not be monotone. A directed n-cycle has eigenvalues
  1 − cos((2πk + 2πnq)/n).

These are limits, not numerical failures. Low frustration does not mean good
navigation or good documentation.

## Invocation and receipts

Run the operator command with an input root holding `operator-request.json`
(`retrieval_arena.graph_spectra_operator_request.v1`), `config.json`
(`retrieval_arena.graph_spectra_config.v1`) and the bundles the config names:

```powershell
.\scripts\run-verification.ps1 -Command measure-graph-spectra -InputRoot <in> -OutputRoot <out> -Receipt <receipt>
```

The config holds `spectrum_config` and `jobs`. Each job has `job_id`,
`bundle`, `view`, `normalized_q`, `unnormalized_q` and `legacy`. The command
runs BLAS single-threaded with 4 GB container memory. It writes:
- `graph_spectra.v1.jsonl`: one receipt per job;
- `graph_spectra_manifest.json`: per-job receipt hashes, metric code hash,
  config hash, runtime;
- `operator_receipt.v1.json`: the image, commit, tree, lock and status.

Each receipt carries:
- the version and the normalization, phase, weight, isolate and edge
  policies;
- graph and view hashes;
- the config and its hash;
- the module sha256, dtype and solver;
- every eigenvalue and its diagnostics.

`python -m retrieval_arena.cli graph measure-spectra --input-root <dir> --out <jsonl>`
is a host development shim over the same `run_jobs`. A consumer's cache must
match the graph hash, view hash, `code_sha256` and `config_sha256`. A matching
file name alone is not enough.

The tests are `unit_tests/test_magnetic_spectrum.py`, in the `focused` suite.
Only linux/amd64 has been run so far. No arm64 or macOS execution is claimed.
