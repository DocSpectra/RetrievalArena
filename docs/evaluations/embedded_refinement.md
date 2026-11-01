# Authorized embedded-link refinement integration

The opt-in [source-bound interface and combined execution](embedded_interface.md)
adds host-resolved IDs, persistent provider circuits and one complete approval
path. The byte-coordinate contract below remains available for historical replay.

This extends the [v2 offline selector](embedded_selection_v2.md) without changing
its objective, q or semantic rubric. Corpus sources, groups, access, prompts,
schemas, models/prices and caps remain consumer-owned, frozen inputs. Historical
v1 and the authored-fixture runner remain available.

## Model stages

`scripts/embedded-api.py model` (also through the PowerShell/POSIX shims) executes
one frozen role inventory using `model_providers`. Its separate SDK image is
built from a local lock-matching RAGAS image with network disabled and no new
dependency. Runtime receipts bind clean source, tree, image and SDK lock. Model
mode uses external_api and passes the single provider credential by environment
name only. The provider SDK reads it; redirect/proxy/host restrictions remain.
An explicit consumer authorization must bind the study manifest and bounded
refinement scope. Complete generation token admission is mandatory before the
first client is opened; failed authentication cannot become an empty real pool.

Role jobs bind prompt/schema, source/access identities and exact provider count
receipts. Generation jobs must match the whole frozen inventory. Review/final
requests use the exact structured body, including transformed Anthropic schema,
with the provider's count endpoint before inference. Over-cap review inputs are
unavailable, never inferred cheap scores. Provider Standard tiers can be pinned
explicitly in settings; old transports omit this field when not configured.

Both providers now accept an optional audit callback. Existing callers are
unchanged. The callback retains exact requests, reservation IDs, request IDs and
canonical SDK response JSON before validating completion/stop status. These are
SDK model_dump serializations, not HTTP wire bytes. Failure records retain
redacted diagnostics; authentication response payloads are suppressed to avoid
credential echoes. Usage/spend remain in the existing CostLedger, including
failed or unreported reservations. Maximum per-role caps sum to at most the
consumer's aggregate hard cap, bounded here by USD 20; no transfer is performed.

At most two counts/completions run simultaneously. Every count/completion has a
120-second timeout. Role execution respects the remaining study clock; monotonic
and persisted wall clocks prevent a normal restart from resetting time. Attempts
are written before calls. A completed cached result is revalidated against its
raw response, payload, schema and identity; it cannot make a second call. Unknown
interrupted attempts consume capacity. One technical retry is permitted, while
a schema-valid substantive rejection is final. Authentication failures halt the
batch after calls already in flight; provider usage beyond declared caps also
halts rather than retrying. An empty role constructs no provider client.

## Offline stages

Both verification adapters expose `refine-embedded-v2`, always network none,
two CPUs, 2 GiB and one numerical thread. Its inventory-bound `config.json`
selects a stage:

| Stage | Required evidence | Durable output |
|---|---|---|
| `prepare-generation` | Frozen packets, token-input bodies, complete count manifest and policy | Exact admitted bodies/jobs, retained target subsets, count receipts, omissions, token-admitted manifest |
| `proposals` | Exact source bytes/masks/groups, generation jobs and retained raw SDK responses | Typed proposals, structural/access rejections, full-pool reciprocal/conflict audit, blinded eligibility jobs |
| `select` | Common structural pool, independently checked eligibility records | Whole-tier S/Z/M traces, matched copies, patches, graph deltas, ledger tips, cards and deduplicated blinded final jobs |
| `assemble` | Blinded final jobs/raw responses and group records | Mapped AI-reference assessments with shared dependence retained |

Token admission can omit only whole target passages/targets. It cannot change
source paragraphs or introduce another target. Exact counts bind the actual
structured request and raw count record. Authentication failure refuses the
stage before any corpus pool is materialized. Model/source/hash/quote facts are
host-validated; model output never supplies successful-application facts.
Proposer insufficient-evidence/unsupported assertions are retained rejections,
not rescued by a subsequent reviewer. Every declared proposal remains in the
records, including ones that cannot form a valid typed proposal; those retain
their raw response/index/reason rather than invented host identities.

Eligibility uses the consumer's independent 0–2 dimensions: all at least one,
sum at least five, no uncertainty/contradiction/insufficient relation. The
proposer's relation/intent and review verdict remain distinct. Source/target
quotes must lie within exact disclosed byte passages. A valid generated JSON
item failing placement or source constraints is a structural rejection; no
additional source or semantic regeneration is introduced.

Selection uses the existing exact completion-preserving common budget and full
highest semantic tier. Every original node/component remains. Final S/Z/M phase
costs use identical schedules and shared caches; final normalized complete-ground-
cluster and ordinary reachability diagnostics are descriptive only. Spawned
process deadlines cover numerical solves and descriptors, at most 600 seconds
per group and 2400 total. A 50-MiB detailed-solve sublimit reserves headroom for
traces/descriptors within the 100-MiB group output ceiling. Unavailable numerical
alternatives force explicit semantic fallback; unavailable final costs cannot
be compared as matched numerical outcomes. Copied corpora retain equal feasible
budgets even when magnetic guidance is unavailable.

All source files are mechanically checked, including untouched bytes, semantic
text and old navigation syntax. Each changed source has a before/after paragraph
card and exact unified full-file diff. Raw HTML is escaped, outgoing requests are
blocked by CSP and previews do not execute the source site. Frozen baseline
target byte evidence remains explicit when that target also receives link syntax;
the entire final target's semantic text is separately verified. Original/content
pair deltas reuse the frozen mask, with no deletion or hidden mask reselection.

The intent log uses v2 real-provider provenance. Per-arm records repeat the same
common declarations, not independent model judgments. Trusted tips sit outside
the log and are anchored by output manifests. V2 replay now validates the projected
legacy state machine once after the full chain, preserving semantics while
avoiding repeated quadratic prefix validation inside each replay.

Final assessment inputs omit arm, selection/semantic scores, prior verdicts,
proposer relation and rationale. Exact identical occurrences are deduplicated;
one independent AI assessment maps to every affected arm. No human accuracy,
original author intent, reader benefit or link-level independence follows.

Python libraries and host shim remain corpus-independent. Only inherited
linux/amd64 Docker runtimes have been exercised (RA-D09/D10); SDK admission's
Docker requirement and untested arm64/HPC deployment are unchanged. No dependency
or upstream history is rewritten. Tests cover fake SDK calls, count/auth gates,
retry/spend/cache/raw-response identity, and a four-group provider-to-patch fixture.
