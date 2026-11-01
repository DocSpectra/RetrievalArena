# Embedded links, content views and intent ledger v1

`retrieval_arena.embedded_links.v1` is an offline source/constraint contract.
`audit-embedded-links` runs in the immutable verification operator with corpus
input read-only, a fresh output directory, network disabled, and no model client.
The consumer supplies corpus/snapshot identities, exact page/path/hash mapping,
historical graphs, source-family depth, duplicate threshold and resource limits.
The Python implementation has no corpus names or platform-specific assumptions.
The current verification image/launchers retain RA-D09's amd64 limitation; only
linux/amd64 has been exercised. No new dependency is needed: CommonMark parsing
uses the existing locked markdown-it-py 4.2.0.

## Source and graph accounting

The importer reads exact UTF-8 source bytes; coordinates are zero-based half-open
UTF-8 byte offsets, including original CRLF bytes. Stable namespaces distinguish
proposal, occurrence, binary edge, region and baseline-component identities.
Page IDs are caller-supplied and unique, rather than guessed from basenames.
Occurrence IDs bind source ID, raw-byte hash, byte interval and syntax.
Edge IDs bind ordered source/target IDs, regardless of occurrence count.

CommonMark inline and used reference links are parsed as occurrences. Unused
reference definitions and links in fenced/indented code are not prose edges.
HTML anchors are inventoried but conservatively ambiguous, except explicit
`nav`/`menu` blocks classified navigation. Images, code blocks, reference
definitions and template markers have separate coverage counts. Shortcodes,
includes, generated menus, Hugo rendering, template substitutions, redirects and
dynamic aliases are not executed. The source resolver accepts relative paths,
`.md`/`index.md`/`_index.md`, HTML-to-Markdown suffix aliases and root paths with
an optional `content/` prefix. Multiple matches are unresolved ambiguity, never
an arbitrary choice. External URLs, queries, traversal above root and template
destinations are refused. A page edge and a fragment are separate facts: simple
unique heading slugs or explicit HTML IDs qualify fragments; other fragments are
unverified and move substantive occurrences to the ambiguity sensitivity view.

Classification is per occurrence and source context. A normal paragraph or
sentence-bearing list item is substantive. Link-only blocks are navigation.
Short list entries lacking clear sentence punctuation, template/HTML/quote
contexts, unmappable inline source and unverified fragments are ambiguous.
Heading links are `other_supported` and excluded from both content views.
These are reproducible heuristics, without human or model semantic validation.
They can misclassify editorial prose and navigation; the source-only hash packet
documents the limits. Degree, page basename and magnetic values never enter the
mask. In particular, inline prose links on index/release-note pages survive.

`original` projects all parsed classes, `content` projects substantive only,
`sensitivity` projects substantive plus ambiguous. Each includes every source
node, including isolates. Multiple occurrences collapse to one directed binary
edge; a substantive occurrence keeps an edge even if another is navigation.
The historical importer and incident-edge mask are separate comparisons and are
never overwritten. Their regex/source-resolution behavior can differ from this
declared CommonMark view. Weak/strong components, isolates, coverage and undirected
cycle rank are reported. A forest/isolate has no fixed-skeleton orientation
information for the applicable magnetic endpoint; cycle presence alone does not
qualify a useful objective.

## Fixed regions

Families use the configured number of source parent-path components. Before hash
ordering, reuse `page_role_annotation.leakage_groups` for source families,
normalized-body identity, literal includes and exhaustive five-word-shingle
Jaccard comparisons (minimum 30 tokens, caller-declared threshold). Additionally
merge families sharing literal include/partial references and their uniquely
identified source pages. Dynamic/unresolved includes remain explicit limitations;
this is not proof of independent semantics or exhaustive template detection.

Order groups by SHA256 of `[seed, region_id]`; select at most eight groups with
an available missing nonreciprocal ordered page pair inside one baseline local
weak component. First four are development, next four reserved evaluation.
Keep all nodes/components of a core, including isolates. v1 uses zero halo;
external incident edges are recorded as omitted. Region IDs, component IDs,
node lists, assignments and equal region weights are frozen before outcomes.
No favorable component reselection or evaluation optimization occurs in the
audit. A source-only forest core remains accounted, with eligibility unqualified.

## Safe edit and interface contract

`apply_edit` accepts an exact proposal, target bytes, frozen region, baseline
edges and caller-declared lexical limits. It wraps a literal contiguous prose
span in an inline internal link. It retains old links and every other byte.
It rejects headings, all list placements (a conservative subset of the graph
mask), quoted blocks, code/emphasis/escaping in the edited block, HTML/templates,
unsafe targets, invalid fragments, stale hashes/spans, overlap/nesting, existing
directed pairs, reciprocal partners and different baseline components. One new
link is allowed per lexical sentence, defined by `. ! ?` followed by whitespace
or line boundaries. Word counting uses Unicode word runs with internal
apostrophes/hyphens. Generation limits must be frozen by the consumer; audit
fixture limits do not authorize model generation.

The validator checks exact target quotation spans, unchanged source anchor and
sentence evidence, source-only link-syntax insertion, parser token text/block
identity, and exactly one added binary content edge. It returns bytes and host
facts; it never writes an original corpus. `replay_patch` verifies patch digests,
before/after bytes and stale sources and is idempotent. After one edit, later
proposals using old source hashes are stale: callers must either declare same-file
conflicts or prepare a separately recorded current-source proposal. No silent
offset rebasing is implemented.

`navigation_view` exposes destination, unchanged anchor, href, source syntax,
surrounding inline block and occurrence ID. Existing repo-search `page_view`
returns raw Markdown, and its search can index link destinations. A new link
therefore need not preserve existing ranking inputs. `semantic_text` is an explicit
CommonMark text projection excluding link destinations; it retains code and
literal HTML, and does not execute the site. The audit freezes this view for every
page and verifies it unchanged in the fixture. Future controlled ranking must
consume this exact view, not assume old raw-source retrieval is equivalent.

## Intent, events and replay

Machine-readable schemas are in `schemas/embedded-link-{proposal,event}.v1.json`,
also emitted by the audit. The host validator implements their closed structural
subset and tagged event payloads. Proposals include corpus/snapshot, source/target
IDs/hashes, target/fragment, exact anchor/span/sentence, target evidence, relation,
concise stated intent/reading use/basis, uncalibrated optional confidence,
provider/model/prompt/schema/attempt/time/response hash/source access and retry
lineage. Relation definitions are `RELATIONS` in the implementation. A source
requires target assertion navigates source-to-target but orders target-before-source.
Labels remain editorial assertions, not validated intent, edge weights or charge.

The append-only single-writer API records `proposed`, `schema_validated`,
`structurally_validated`, `reviewed`, `eligible`, `selected`, `applied`, `verified`,
`rejected`, `failed`, `reverted`. Model, reviewer and host authorities are distinct.
Only the host can assert actual edits and graph facts. Eligibility requires both
structural and semantic decisions. Selection records rank, reason, objective
input/output hashes, requested/achieved budget and conflicts/skips. Application
records occurrence/edge IDs, patch/source hashes and a facts digest; verification
binds that digest, rendered identity and before/after graph hashes.

Every event binds proposal/run/arm/region, previous canonical-event digest and its
own canonical-body digest. Exact duplicate delivery is idempotent; duplicate
application, illegal transitions, stale edits, identity drift and partial-chain
tampering fail. Pre-application corrections append a same-state event superseding
the latest event; actual edit correction requires reversion and a new proposal.
Store the trusted final tip outside the log: an unanchored hash chain cannot detect
complete rewriting/truncation by a party that controls all artifacts. Provider
accounting is separate. Original response bytes must be retained and joined by
hash, not treated as trusted application facts. The audit's responses/reviews are
explicit `mock-model` records; they are not semantic judgments or provider calls.

`blind_review_input` uses an allowlist of source sentence/anchor, target quotations
and byte hashes. It excludes arm, scores, earlier verdicts and proposer rationale.
Human/model assessments and downstream access/performance remain separate layers.

Tests in `unit_tests.test_embedded_links` are in the focused suite. The corpus-copy
smoke imports all pages after one labeled mechanical fixture edit, checks exactly
the expected content-edge addition, exact preservation of all other files, raw
response/event linkage, rejected/failed examples, and idempotent patch replay.
