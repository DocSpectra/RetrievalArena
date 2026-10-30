# Magnetic page representations v1

`retrieval_arena.graph.page_representations`, `magnetic_page_representations.v1`;
offline operator `export-page-representations`. Corpus-specific choices and
interpretations belong to the consuming study. No role labels are inferred.

## Operator and identity

Use the normalized operator from [normalized magnetic spectrum](normalized_magnetic_spectrum.md)
unchanged. For declared finite q and positive t, compute the complete dense
complex128 eigensystem and H=V exp(-t Lambda) V*. The row feature vector is
`concatenate(H.real, H.imag, axis=1)`: 2N columns, including identically zero
imaginary columns at q=0. It is transductive within one graph: test nodes and
edges remain present; row-coordinate coefficients cannot transfer to another
corpus by matching a few IDs. No truncated eigenspace, phase unwrap or arbitrary
eigenvector basis is used.

Qualification checks L Hermitian to 1e-12, residual / max(1,||L||F), orthogonality,
spectral bounds [0,2], H Hermitian, and disconnected coupling to the declared
tolerance (default 1e-10). H has positive eigenvalues exp(-t lambda), at most
one within tolerance. Positive definite does not mean entrywise nonnegative.
q=0 is entrywise nonnegative within numerical error, using the same W.
Disconnected coupling is set exactly zero after checking the roundoff bound.
An isolate has H_ii=1 and no off-diagonal coupling. This is missing structure,
not a quality judgment.

Input `retrieval_arena.page_representations_request.v1` contains command and
an exact canonical regular inventory/hash. Config
`retrieval_arena.page_representations_config.v1` declares graph bundles, q/t
pairs, tolerance, map anchors, card count and byte/runtime ceilings. Corpus
files join every sorted node ID, with exact raw-byte hashes in pages.json;
graphs must use the same complete node universe. Extra files, targets and
undeclared assets are refused. Page metadata are used only for card strata;
no supervised evaluation runs in this operator. No model or network is used.
The standard immutable sh/PowerShell launchers provide four CPUs and 8 GiB.
Runtime/output caps are at most four hours/6 GiB, checked between complete jobs;
they are not an asynchronous interruption of a single LAPACK call.

Every bundle saves kernel.npy (complex128), features.npy (float64), and a
manifest with node order, graph/content identities, operator conventions,
q/t, qualification checks and array hashes. `load_bundle` rejects failed status,
content/node identity mismatch, changed arrays or incompatible shapes/values.
The top operator manifest pins source/image/lock, inputs, code and all outputs.
Arrays remain external generated artifacts.

## Simple features and downstream operations

Per page: in/out degree, reciprocal degree, weak-component size, largest-component
indicator, isolate flag, component-anchored generalized trophic level and missing
flag. The component identifier is grouping metadata, never a categorical predictor.
Trophic levels reuse the qualified grounded solver and minimum-zero anchoring;
edgeless components have null level. This is a linear hierarchy candidate,
not a validated prerequisite order.

`pair_affinity(H, seeds, candidates)` computes mean |H[s,c]|. Global reversal
conjugates H and leaves this affinity unchanged. It can bridge multi-hop weak
connectivity but is neither directed reachability, a transition probability
nor a route length. Full complex data retain phase convention; retaining them
alone does not validate a direction-sensitive downstream policy.
`masked_heat_kernel` removes specified edges before rebuilding the full operator.
Consumers must also rebuild simple features and any other graph-derived inputs
after masking; cached unmasked features cannot enter held-out-edge evaluation.

## Descriptive outputs and label limits

Anchors are fixed by SHA256 node-ID order, shared across q/t and graph companions.
Maps retain real/imag H[i,anchor] plus nearest-anchor groups under Euclidean
complex row distance. Display coordinates are two differences of distances
to anchor pairs; common symmetric limits and color settings are used.
Maps include self-mass concentration, effective row support and low off-diagonal
anchor coupling (<=1e-10). Arbitrary nearest-anchor ties, overlap, self-dominance
and anchors in small components limit interpretation. Groups have numeric names;
no workflow/role claim follows. Companion group agreement is descriptive,
including ties; it is not a semantic clustering score.

Review cards use round-robin source-family strata with SHA256 ID order, at
most 40 pages, excerpts and blank role/ambiguity/missing-coverage fields. No
spectral features, graph clusters or suggested labels enter the cards. Raw
editorial text can disclose its own role; source provenance must remain explicit.
`clean_text` removes frontmatter, headings, markup/comments/shortcodes/code,
link destinations/images and URLs. `training_boilerplate` identifies exact
frequent lines on training pages only. No global vocabulary or IDF is fitted.
Independent labels, grouped splits, fitting and bootstrap evaluation remain
separate downstream work when labels support that endpoint.

Focused fixtures: `unit_tests.test_page_representations`, including full expm
agreement, repeated-eigenspace rotations, matched q=0, reversal, permutation
and refitted ridge predictions, localization, isolate/component coupling,
masking, bundle tampering and a small operator end to end. No platform beyond
the actual recorded container run is certified.
