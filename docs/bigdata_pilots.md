# BigData Pilots

BigData pilot plans live in `configs/bigdata2026/`.

Selected milestone plans:

- `configs/bigdata2026/pilot_express_docs.json`
- `configs/bigdata2026/pilot_docker_docs.json`

Dry-run a plan before producing artifacts:

```powershell
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --dry-run
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_docker_docs.json --dry-run
```

Run or refresh a baseline:

```powershell
python -m retrieval_arena.cli study bigdata pilot --plan configs\bigdata2026\pilot_express_docs.json --refresh-baseline
```

The milestone pilots resolve 9 stages and 6 run comparisons:

- prerequisites
- source import
- dataset preparation
- snapshot manifests
- snapshot comparison
- retrieval before
- retrieval after
- drift/audit/measurements
- HTML report

The six selected retrieval families are lexical top-k, lightweight vector,
graph rerank, oracle graph support, multihop graph rerank, and deterministic
iterative search.

Calibration bundles live under `calibration/bigdata2026/`. They are meant for
review and paper evidence. Generated source trees and bulky retrieval outputs
remain ignored by default.
