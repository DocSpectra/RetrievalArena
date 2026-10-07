# Structural target: connectivity constraint, anchored reachability, link guard

Standalone corpus-graph diagnostic in `retrieval_arena/graph/structural_target.py`. It is not a retrieval or answer score, not an admitted verification operator, and not a corpus edit. It ranks no documents and claims no answer utility. NumPy is imported only for λ₂. Tests are in `unit_tests/test_structural_target.py`.

The consuming workspace freezes anchors, depth, tolerances and validity thresholds before computing values, and owns any admission decision.

## Graph policy

- Nodes are document IDs; duplicate node IDs raise `ValueError('duplicate-node')`.
- Edges are binary, simple and directed. Duplicates, self-loops and edges with an unknown endpoint are dropped. This is the same policy as `magnetic_phase.canonical_edges`.
- The main component M is the largest weak component. Ties go to the component whose smallest sorted member ID is smallest.
- Every output is invariant to node order, edge order and duplicate listings.

## `anchored_forward_reachability.v1`

`anchored_reachability(nodes, edges, anchors, depth=3, *, config)` runs a multi-source forward BFS from the anchors that exist in `nodes`, over the whole edge set.

`value = |{v ∈ M : dist(A, v) ≤ depth}| / |M|`. Anchors inside M count at distance 0.

The result also carries:

- `reached_count` and `main_component_size`;
- `reached_count_all_pages`, the count over all nodes rather than M;
- a reach `profile` at each `config.profile_depths` and at `inf`;
- `mean_forward_distance` over reached pages in M;
- anchor presence lists;
- `graph_sha256` and `config_sha256`.

Properties:

- **Direction.** T is directional on forests: anchors-first and leaves-first orientations differ. Reversing every edge changes T unless the anchor set is symmetric under the reversal (for example a directed cycle with one anchor, or an all-reciprocal graph).
- **Gameability.** T is gameable by linking. One added link can bring the target's whole (depth − 1)-hop forward ball into reach, so the gain per link is not bounded by 1/|M|. Use it only together with the link guard, and only with an external decision about gameability.

## `structural_connectivity_constraint.v1`

`baseline_summary(nodes, edges, anchors, *, config)` freezes the reference: node list, M, edge count, maximum out-degree, anchors, the λ₂ of M and the graph hash.

`connectivity_admission(nodes, edges, baseline, *, mapping=None, config)` returns `passed` plus reasons. `mapping` sends merged-away baseline IDs to their survivors.

| Reason | Rule |
| --- | --- |
| `C0-anchor-missing` | a baseline anchor's image is not a node |
| `C1-baseline-page-left-main-component` | a baseline-M page's image is not in the variant's M |
| `C1-new-page-outside-main-component` | a new page (not a baseline ID) is not in M |
| `C1b-new-page-without-in-link` | a new page has no in-link from another page |
| `C2-lambda2-below-tolerance` | normalized undirected λ₂ of the evaluation set is below (1 − `lambda2_tolerance`) × baseline |

- **Normalized λ₂:** the second eigenvalue of `D^-1/2 (D − A) D^-1/2` on the binary symmetric adjacency, using dense `eigvalsh`. It is 0 for a disconnected graph or fewer than two nodes.
- **C2 evaluation set:** the subgraph induced by the images of baseline-M pages plus new pages. Baseline pages from outside M that join M are excluded, so connecting them is never penalized.
- **Disabling C2:** set `use_lambda2=False`.

## `structural_link_guard.v1`

`link_guard(edges, baseline, *, nodes=None, config)` checks two limits:

- **G1:** the canonical directed edge count is at most `floor(edge_ratio_max × baseline edges)`.
- **G2:** every out-degree is at most `max(baseline max out-degree, outdegree_floor)`.

Defaults are 1.20 and 30.

## Configuration

`StructuralConfig` defaults: `depth=3`, `profile_depths=(1..6)`, `lambda2_tolerance=.10`, `use_lambda2=True`, `edge_ratio_max=1.20`, `outdegree_floor=30`. `config_digest` is the SHA-256 of the canonical JSON form.

The `depth` argument of `anchored_reachability` is explicit, and receipts record both it and the config hash.

## Not claimed

These functions do not establish that T predicts answer quality or evidence exposure. Correlation with a one-hop out-neighbour retriever is partly mechanical. Numerical behavior and gameability must be qualified on each frozen study population.
