# Manifest Lineage

RetrievalArena treats source import and dataset preparation as explicit
reproducibility boundaries. Prepared folders are derived artifacts, not the
root provenance source.

## Source Import

`corpus_import_manifest.json` records the source descriptor, descriptor hash,
source type, source location, requested ref, resolved Git commit when
available, import timestamp, selected files, ignored files, import
configuration hash, parser version, content hash, and
`snapshot_identity_hash`.

Remote Git clone support is still deliberately scaffolded. Real-corpus pilots
use local Git source paths plus explicit refs, so imports can be reproduced
offline without mutating the active checkout.

## Dataset Preparation

`dataset_preparation_manifest.json` records the input
`snapshot_identity_hash`, normalization configuration, chunking configuration,
generated document IDs, document inventory, excluded optional artifacts,
query/answer hashes when present, software lineage, and
`output_dataset_identity_hash`.

The current preparation contract copies imported documents one file per
document. No chunking transform is hidden inside retrieval execution.

## Optional Graph And Support Stages

Graph and support stages run only when enabled by the comparison or pilot
configuration.

`graph_transformation_manifest.json` and `graph_snapshot_manifest.json` record
the source dataset identity, graph extraction configuration, resolver
configuration, graph hash, node count, edge count, and graph metrics when
available.

`support_construction_manifest.json` and `support_surface_manifest.json` record
the source dataset identity, support construction configuration, query set,
support targets, support label counts, and support artifact hashes.

## Replay And Audit

Replay comparison reports separate behavior equality, provenance equality, and
byte-level artifact equality in the report summary. This allows a rerun to be
reported as behaviorally identical while still exposing timestamp, config path,
Git lineage, manifest schema, or aggregate artifact movement.

Regression audit reports include `cause_label_schema_version` and a stable
cause-label schema in `regression_audit_summary.json`, so manuscript tables do
not rely on manual interpretation of labels.
