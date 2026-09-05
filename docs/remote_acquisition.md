# Remote Git acquisition

`corpus acquire` is a host operation, separate from evaluation and comparison.
It publishes a clean detached repository, copied selected documents, descriptor,
inventory, acquisition receipt, and corpus import manifest in one directory.
This feature is implemented for independent RA qualification; DocSpectra
Capstone acquisition-receipt admission and deployment activation are deferred.

## Command and descriptor

```text
python -m retrieval_arena.cli corpus acquire --descriptor source.json --transport local-only
```

The default transport accepts only local `file:///` URLs. An explicitly
deployed host invocation can select `--transport host-network` for anonymous
HTTPS. `RA_NETWORK_DISABLED=1` refuses that capability, and registered
verification always sets it and runs with Docker network `none`. The ordinary
corpus import API and comparison path have acquisition disabled by default.
No evaluation command invokes the acquisition capability.

```json
{
  "corpus_id": "example",
  "snapshot_id": "baseline",
  "source_type": "git",
  "source_url": "file:///absolute/path/to/controlled.git",
  "requested_ref": "refs/heads/documentation",
  "docs_root": "docs",
  "include": ["*.md", "**/*.md"],
  "exclude": [],
  "destination_workspace": "generated"
}
```

Use portable corpus/snapshot IDs containing letters, digits, underscores and
hyphens, starting with a letter or digit. Documentation roots are relative
POSIX paths without traversal. Selection uses `PurePosixPath.match` on paths
relative to that root; declare `*.md` as well as `**/*.md` for root-level files.
An empty selected inventory is rejected. Local directory and local Git imports
retain their existing behavior and v1 manifest contract.

## Ref and transport policy

Supported refs are full `refs/heads/...`, full `refs/tags/...` (including
annotated tags), lowercase 40-character commit IDs, and unambiguous short names.
Names use ASCII letters, digits, underscore, slash, period and hyphen, begin
with a letter, digit or underscore, and reject empty components, `..`, `.lock`
suffixes and trailing periods. `HEAD`, other ref namespaces, abbreviated
hexadecimal IDs, revision expressions and option-like strings are rejected.
A short name matching both branch and tag is an error; use the full ref.

Symbolic resolution captures one advertisement and peels the selected tag.
All fetch, checkout, object and inventory operations then use the captured
commit. A final advertisement checks that the requested symbolic binding has
not moved; it never substitutes a newly observed commit. A later acquisition
of a moved branch produces a different publication root and preserves the old
revision. Exact commit requests fetch that object directly; an unavailable or
unreachable object fails. There is no default-branch fallback, shallow fetch,
sparse checkout, partial clone, alternates or active checkout modification.

HTTPS URLs allow an ASCII hostname and conservative path characters. Embedded
user information, queries, fragments, non-default ports, redirects, SSH,
remote helpers and traversal are rejected. File URLs must have no authority
and an existing canonical absolute path without percent encoding. Version 1
admits no authentication mechanism: it disables Git credential helpers,
interactive prompts, inherited Git configuration and environment overrides.
Future authenticated transport needs an explicit runtime policy extension.
Git diagnostics are never retained or echoed; failures use fixed reason codes.

## Receipt and lineage

The receipt schema is `retrieval_arena.remote_git_acquisition.v1`, policy
`exact-git-anonymous-v1`. Its closed top-level fields are `schema_version`,
`policy_version`, `created_at`, `identity`, `identity_hash`, `operational`,
`phase`, `status`, `failure_class`, and `manifest_hash`.

`identity` binds the canonical descriptor and descriptor hash, requested-ref
interpretation, resolved commit, full tree, optional docs tree, clean/detached/
independent-object observations, selected file inventory, content hash,
strategy, reuse policy, implementation commit/tree, and fixed artifact names.
The descriptor excludes the destination path. `identity_hash` is RA's
`sha256_json(identity)`; `content_hash` is `sha256_json({"files": inventory})`.
The inventory uses the existing import path/doc ID/size/SHA-256 entry shape.
Git objects and materialized bytes are compared for every tracked ordinary
file, including files outside the selected documentation scope. Symlinks,
submodules, case collisions and transformed checkout bytes are rejected.

`operational` records publication root and Git executable/version.
`created_at` and operational paths are outside `identity_hash`. The receipt
self-hash uses RA's existing `finalize_manifest`: canonical JSON SHA-256 after
removing `manifest_hash`. Canonical files are compact sorted UTF-8 JSON plus
one newline. A terminal receipt has phase `complete`, status `success`, and
null `failure_class`. Failure emits a fixed exception/CLI error and no receipt.

The remote import manifest is `retrieval_arena.corpus_import_manifest.v2`.
It references the exact receipt path/self-hash and materialized source path;
its snapshot identity is the receipt's `identity_hash`. Existing dataset
preparation consumes v2 only after offline acquisition revalidation. V1 local
imports are unchanged. No Capstone package schema or runtime is imported.

## Publication, verification and reuse

Each publication is under `destination/corpus/snapshot/<descriptor+commit hash>`.
Work occurs in a unique marker-owned `.acquiring-*` sibling. Complete Git,
inventory, copied-document, descriptor, manifest and receipt validation occurs
before one directory rename publishes success. Handled failure or interruption
removes only that exact owned temporary root; unsafe ownership changes refuse
cleanup. A hard process kill can leave a hidden temporary root, but it can never
be reused as an admitted publication. Do not manually promote partial roots.

`verify_acquisition` is offline and accepts an expected descriptor, exact commit
and optional externally retained receipt hash. It checks strict receipt fields,
self-hashes, artifact references, exact manifest recomposition, independent
Git objects, detached HEAD, origin, clean status and copied content. Reuse runs
the same checks and returns the original receipt bytes. No refresh or overwrite
is supported, including when the legacy import API's `overwrite` is true.
Markerless, partial, changed or malformed existing destinations are errors.
Hashes detect mutation; authenticity requires the caller to retain and supply
the expected receipt hash or use an independently authenticated record.

Output storage must be exclusively controlled by the acquisition operator.
Directory rechecks reduce accidental races; this is not a sandbox against
another process with concurrent write access to the publication parent.
Generated repositories, documents, receipts and inventories remain untracked.

## Qualification

The registered `focused` inventory includes `unit_tests.test_remote_acquisition`
and local-import regressions. CLI help and synthetic file-remote acquisition
run inside those tests. Runtime witness expectations include acquisition
capability, credential-URL and invalid-ref rejection, alongside the exact test
case inventory. Use the existing build/run verification scripts and immutable
image receipt. Neither tests nor evaluator profiles need external network.

Build networking is disabled. When dependencies are already provisioned in a
local RA verification image, pass its exact image ID as `-ProvisionedImage
sha256:...` to `scripts/build-verification.ps1` (fourth positional argument to
the POSIX script). The adapter verifies Linux/amd64 and the dependency-lock
label before assigning a content-named local build tag. The Dockerfile probes
installed versions and imports without installing packages. A cold build
without a cached dependency layer fails offline; it does not authorize downloads.

## SDM mechanical reuse attribution

Command-local `core.longpaths=true` and Windows readonly cleanup mechanics
were adapted from `docspectra-importer/docspectra_importer/corpora_importer.py`
at DocSpectraSDM2026 commit `00f0148d32ddaa498d744e249ff741837d8dca45`.
The source header credits Copyright (c) 2026 Rocker D'Antonio; its license is:

MIT License

Copyright (c) 2025 Rocker D'Antonio

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
