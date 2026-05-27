# BigData Pilots

BigData pilot plans live in `configs/bigdata2026/`.

Selected milestone plans:

- `configs/bigdata2026/pilot_express_docs.json`
- `configs/bigdata2026/pilot_docker_docs.json`
- `configs/bigdata2026/pilot_kubernetes_website_docs.json`

Dry-run a plan before producing artifacts:

```powershell
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --dry-run
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_docker_docs.json --dry-run
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_kubernetes_website_docs.json --dry-run
```

Run or refresh a baseline:

```powershell
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --refresh-baseline
```

The milestone pilots resolve the configured source-to-observability stages and
6 run comparisons:

- prerequisites
- source import, dataset preparation, and snapshot manifests through the
  snapshot comparison stage
- snapshot comparison
- retrieval before
- retrieval after
- drift/audit/measurements
- HTML report

The six selected retrieval families are lexical top-k, lightweight vector,
graph rerank, oracle graph support, multihop graph rerank, and deterministic
iterative search.

The Kubernetes website pilot uses non-Git local snapshot descriptors. The
materialized source folders are generated under ignored `raw/` from explicit
official upstream revisions, while the committed descriptors retain the
official source URL, requested revision, local snapshot path, descriptor hash,
local source hash, and snapshot identity hash.

Calibration bundles live under `calibration/bigdata2026/`. They are meant for
review and paper evidence. Generated source trees and bulky retrieval outputs
remain ignored by default.

See [manifest lineage](manifest_lineage.md) for the source descriptor,
dataset identity, graph/support lineage, replay comparison, and audit-label
schema fields that pilots produce.
