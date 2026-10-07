# Docker variant integration boundary

The ICSC Sprint 08 implementation is owned by the consuming workspace at
`git/context/scripts/docker_corpus_variants.py`. It uses the pinned historical
CIKM exporter and graph implementation, rather than changing RA's general
`prepare_dataset` or candidate-retrieval contract. This is an integration
record, not an RA operator command.

The variant editor tree contains only discovered FAQ-excluded source pages.
The trusted exporter makes a provider-only dataset with flat path-hash document
IDs, `corpus/*.md`, and `graph_edges.csv`. It withholds questions, answers,
support targets, judgments and scores. `complex_task_agent_loop_v12.py` in the
consuming workspace reads that dataset. Its one/six-search and follow-off/on
budgets are unchanged; the caller freezes task and prompt identities separately.

The variant tool verifies the recovered source tree and the external CIKM
exporter/metric file hashes before copying or analyzing. Its full graph view
uses locked FAQ pages only inside the trusted process. The editor must not
receive a mount of the locked source, recovered dataset archive, or private
evaluation manifests. A local CLI script alone is not an access-control
boundary if the editor can browse the entire host. A later Codex session needs
an isolated filesystem/container and a narrow read-only tool endpoint that
returns only the analysis receipt.

The historical CIKM `lambda_2_norm` is unnormalized Laplacian λ₂ divided by
that graph's maximum eigenvalue. It is retained under that explicit name.
The new true undirected normalized Laplacian uses
`D^-1/2 (D-A) D^-1/2`, with zero diagonal for isolates. The pinned magnetic
operator remains unnormalized with `q=0.25`. The phase-derived flow statistic
has recorded node-order sensitivity and is diagnostic only; directed degree
imbalance is a simple order-invariant control. No metric-targeted edit or answer
effect was run in Sprint 08.

Any later RA candidate-operator admission would require a separate source,
image and receipt pin. This local export does not make an edited corpus an
admitted RA candidate, and the offline preflight does not qualify a model run.

Sprint 09 adds the optional standalone [magnetic phase diagnostics](evaluations/magnetic_phase.md).
The consuming workspace's new manifest may explicitly pin
`magnetic_subspace_edge_polarization.v1`, its source hash and complete config.
The analyzer adds a separately named receipt; old manifests omit this field
and retain historical replay. Historical phase counts remain blocked. The
isolated-mode edge-relative v2 candidate is also blocked after tolerance
sensitivity. The subspace statistic requires a frozen corpus audit and
does not establish utility or admit any editor run. Full receipts remain
private; filtered session tools belong to the separately planned Sprint 10.
