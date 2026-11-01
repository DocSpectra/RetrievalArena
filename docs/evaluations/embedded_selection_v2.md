# Embedded-link selection and source demonstration v2

`demonstrate-embedded-v2` is an offline immutable verification command on both
PowerShell and POSIX adapters. The inventory-bound request declares `fixtures`
or `access`. It reads exact UTF-8 sources and consumer-owned config/manifest,
writes only a fresh output root, and makes zero model calls. CPU/RAM/thread
limits reuse the two-CPU, 2-GiB verification operator. The pure Python libraries
and multiprocessing spawn watchdog introduce no host-specific dependency; the
image still has RA-D09's tested linux/amd64 limitation. No dependencies change.

`embedded_selection_v2.select_embedded_v2(pool, group_graph, policy, score_cache,
arm=...)` preserves historical v1. Exact conflict-free sets determine the common
budget before numerical scoring. Same-source alternatives cannot both be used.
All completion-preserving alternatives in the highest current semantic tier
are scored, using the entire tentative prefix against the original graph.
Two schedules, q=.25, fixed 1e-6 raw rank floor and unchanged numerical policy
are enforced. S selects semantic score then caller-declared tie ID (default
proposal digest). Z has analytic q=0 zero energy and equals S. M uses a unique
separated schedule range, or records explicit semantic fallback. Distinct
statuses cover all-candidate ties, partial minimum ties, overlapping ranges,
single options, unavailable candidates and exhausted numerical runtime. No
failed alternative is deleted. A failure on a single option remains a recorded
guidance failure. Raw tiny negative costs are retained and treated as zero only
for tie handling. Schedule ranges are sensitivity diagnostics, not error bars.

Score-cache keys bind the complete pool, original graph, policy, version and
prefix. Solve-cache keys additionally bind canonical support, charge, seed and
the full numerical policy. The scorer saves per-schedule changed/canceled block
keys, denominator and complete solves. M costs include rejected alternatives.
`BoundedBlockScorer` runs numerical work in a spawned process, terminates it at
the group deadline and preserves attempted requests plus completed cache state.
The serialized numerical cache has a declared ceiling. A killed in-progress
native solve cannot provide final phases; it is explicitly unavailable. Access
construction has a separate process-level 600-second watchdog.

`embedded_ledger_v2` records authored sources with a closed `authored_fixture`
provenance schema (manifest hash/candidate key/time) and `fixture_author`
authority, never provider/model/response fields. Real provenance uses a separate
`model_response` branch. Host patch validation and transition semantics reuse
v1 through a private checked validation projection; that projection is never
written as evidence. Public v2 hashes/trusted tips bind actual v2 records.
Unknown fields, incorrect authority, fabricated fixture-provider fields, stale
quotes, application duplication and tampering fail. Historical v1 replay and
schemas remain unchanged. Fixture semantic labels are authored assertions;
they do not become human/model judgments through the ledger.

The fixture stage materializes original/S/Z/M sources, one card/diff per changed
source, exact spans, full original/content/navigation graph deltas, complete
pool/selection/application events and anchored replay tips. It verifies all
untouched bytes, text equality and navigation, independently checks a cycle
formula and records q=.10 diagnostic reversal. Cards are CommonMark source
previews, not whole-site rendering. Final matched costs use identical schedules;
directed reachability/distance counts are descriptive only.

The access stage accepts only four explicitly development-assigned regions;
reserved records are excluded from inputs. It reuses baseline component IDs and
original parsed pairs in both orientations. Source pages and up to three legal
paragraphs are hash-ordered with the consumer's frozen seed. Targets use maximum
paragraph token-set Jaccard, unchanged lowercase Unicode apostrophe/hyphen word
runs and target-ID ties. Up to five targets expose two matched full paragraphs.
Every character has a raw-byte map. Unsupported, unmappable and overlong blocks
remain in the omission audit. No sentence is truncated; no scores influence
access; no weak-opportunity resampling occurs. These packets still require
provider-specific token counting and manifest approval before inference.

There is no live model execution in this operator. Reusable external_api
transport/accounting remain in model_providers and need an authorized caller
before real pool construction. Authored assertions and future AI references
must not be pooled. No efficacy, global minimum or recovered intent is claimed.
