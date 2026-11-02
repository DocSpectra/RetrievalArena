# Blinded full-page support review

`retrieval_arena.embedded_blinded_support.v1` is an opt-in all-candidate,
two-reviewer consumer of the existing shared model executor. It introduces no
transport, schema, semantic threshold or numerical rule. Consumer configuration
owns identities, rubric, fixtures, request limits, tariffs and corpus choices.

`refine-embedded-v2` stages `blinded-support-prepare`,
`blinded-support-select` and `blinded-support-verify` run offline. Preparation
verifies immutable source bytes and full-page packets, creates one isolated job
per admitted candidate per role, and retains opaque job/private candidate bindings.
The outbound projection has closed packet/page fields. Its exact pages, anchor
and sentence match curator ingress, while no curator label or declared relation
enters prompts. Literal UTF-8 maps permit unique exact, role-bound quotes from
all supplied text, including code. No missing numerical estimate filters review.

The SDK image's offline `serialize_requests(input, output)` constructs no clients.
It uses the real SDK schema projection and shared transport request builders to
export every final body, checks the byte ceiling, revalidates the public payload
and records prompt/body/evidence hashes. Freeze its hashes in the live manifest.
The executor refuses a final serialization differing from that inventory.

The existing portable `scripts/embedded-api.py study` routes a manifest with
`blinded_support_version` to this runner. Exact input/manifest authorization,
official identity/tariff/context verification, disjoint role/lane reservations,
two-hour original clock, concurrency two, bounded retries, persisted provider
circuits and cached responses precede dependent stages. Synthetic positive and
negative jobs precede real jobs. Both models review all admitted candidates even
when the first model rejects one. Four fixed synthetic jobs plus the real jobs
are the complete inventory; no generation, replacement or substantive retry.
Only a mock manifest and injected fake transport may bypass official verification
for network-none tests; the live CLI does not expose transport injection.

The full inventory distinguishes both-pass, first-only, second-only,
both-negative and technical-unavailable. Both-pass pairs enter the common pool;
only first-review scores rank them. The runner stops for consumer publication of
the complete inventory/pool in Git before selecting. A publication receipt binds
the support hash and commit. Consumer tooling verifies the committed bytes.

Historical-score loading independently verifies all frozen source/graph/policy,
numerical keys, seed/start identities, energies, gradients and comparisons.
New semantic scores/pool bindings are separate: k=1 costs may transfer only after
this dependency check. S/Z and the unchanged minimum-set rule use the new common
pool's highest first-review tier. Unavailable alternatives retain veto precedence.
No historical winner or curator score determines new eligibility/ranking.

The existing editor materializes original/S/Z/M copies and anchored ledgers.
A curator-origin proposal may receive a standard reviewer-authority judgment;
that never converts its proposal provenance to model-generated. Review events
bind both response hashes. Complete destination source previews supplement the
source/diff cards. Independent verification regenerates the complete artifacts,
checks external ledger anchors and freshly replays every changed highest tier.
Raw responses/copies stay in consumer output; source, contracts and concise
provenance stay in Git. Source previews do not execute a documentation website.

`unit_tests.test_embedded_blinded_support` is in the official focused suite.
Tests exercise full-page blindness, score changes, disagreement, both negatives,
technical exhaustion, invalid schema/quotes/roles, response mapping, cached
restart, frozen request mismatch, changed selections, actual edited copies and
external ledger anchors. Consumer exact-inventory fake runs complement these
authored tests; fake judgments establish plumbing only.

The implementation is Python and uses existing verification and SDK locks.
Existing SDK image amd64/launcher portability limitations remain RA-D10/RA-D11;
the verification image retains RA-D09. No new dependencies or platform assumption.
