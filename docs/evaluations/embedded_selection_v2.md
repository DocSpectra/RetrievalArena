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

The `requests` stage projects frozen access into exact provider request bodies,
with full raw excerpts/byte intervals and semantic text, excluding graph scores,
rankings and group membership. The large character maps remain in local evidence.
It records request hashes/bytes but refuses to claim provider token counts.
The deterministic over-cap helper removes the last-ranked whole target paragraph,
then last target, preserving one target/passage; a final over-cap packet is omitted.
No source sentence is truncated. A provider token-counting request is an outbound
disclosure requiring consumer authorization; none is made by this offline stage.

`scripts/embedded-api.py` (with `.ps1`/`.sh` thin shims) builds a separate
immutable token-counting image from an already local RAGAS SDK image matching
the existing SDK lock. Build has network none and adds no dependency. Receipt
checks bind clean source commit/tree, dependency lock and image labels. `smoke`
imports the pinned SDK count surface locally without a client/key/network.
`count` requires a separate explicit authorization record binding the exact
input inventory, provider/model and `provider_token_count_only` scope before
any client is opened. It passes the environment credential by name only; the
SDK reads it. The RA https host guard, no redirects/proxies, no SDK retry and
existing external_api container admission apply. It calls only OpenAI's input
token-count endpoint, not text generation. Full request/schema bodies are counted.

The caller permits two simultaneous counts, 120 seconds each, two hours total,
at most ten deterministic whole-target disclosure levels and one technical
retry per level. Every count/failure and modified request hash is retained.
The final admitted request bodies and token receipts are inventoried separately;
they do not themselves authorize inference. No live count has been qualified
without author authorization. Only linux/amd64 is exercised by the inherited
SDK image; this does not resolve RA-D10. Provider token counting for Haiku/Sol and the generation/review/assessment executor are implemented by the separate model mode; they remain gated on actual token admission and explicit bounded-study authorization.

The complete real-document integration and approval-bound model mode are now in [embedded_refinement](embedded_refinement.md). Token-count and inference scopes remain separate; no network is added to verification commands.
