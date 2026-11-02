# Individual embedded links and selected assignments v2

`retrieval_arena.embedded_constraints.v2` is opt in through the study manifest's
`constraint_version`. Unknown versions fail closed. Omission preserves historical
pool-wide reciprocal rejection and the existing-link sentence guard. V1 evidence,
schemas, ledgers and entry points keep their meanings.

The host editor retains exact byte/hash/identity/target/component/fragment checks,
literal paragraph syntax, unique source-bound quote resolution, text/navigation
preservation and one expected new substantive directed edge. A reverse edge in
the frozen original graph remains forbidden. A proposed reverse alternative
does not change individual validity; its coexistence in the pool is a joint
conflict. V2 permits an existing different link in the same sentence, with nesting
and overlapping old spans still forbidden. The maximum of one selected source
file implies at most one new link per sentence; no offset rebasing is introduced.

Exact feasible sets and completion-preserving S/Z/M prefixes forbid jointly
selected reciprocals, duplicates, same-source edits and declared anchor conflicts.
The numerical kernel independently checks additive new-pair validity. Final
materialization validates every selected assignment against the **original
parsed graph**, before any patch. It emits `selected-set-proof.json`; independent
`replay_assignment` revalidates the assignment and exact idempotent patches. Patch
facts bind the constraint version. Support admission, pool freeze, prefix scoring,
application and replay use the same version. No numerical policy changes.

Duplicate occurrences and all their reviews remain in frozen records. V2's
competitive pool counts a directed pair once, retaining the highest eligible
semantic score and then canonical proposal ID as its representative. Aliases do
not create competition or a second edit slot. Reverse directions are distinct
pairs that may compete at k=1. Historical competitive freeze still refuses
duplicate pairs. Same-source caps subsume same-sentence new-link conflicts;
identical sentence text at two byte locations is not treated as occurrence proof.

## Bounded evidence context

`retrieval_arena.embedded_packet_context.v1` and `prepare-packet-context` under
`refine-embedded-v2` offer one deterministic source-only projection. The consumer
supplies fixed source/target pages, development groups, exact bytes and prompts.
First and last substantive editable paragraphs plus one hash-ordered paragraph
are selected, at most three. Short meaningful sentences qualify; version/date
fragments and dependent output lead-ins do not. Lists/code/emphasis remain
unsupported editable anchors. These heuristics are not a semantic classifier.
Targets use two complete mapped prose paragraphs ranked by token-set overlap.
Page identities and target order remain fixed; source/evidence shortages are
recorded, not resampled.

Each selected paragraph receives a raw evidence window of complete top-level
CommonMark blocks, including lists and code. A window stops at a heading or a
4,096-byte cap; the page sum is at most 6,144 bytes. Over-cap atomic blocks/windows
are recorded, never sliced. Exact source title lines and enclosing headings
provide context. Public readonly passages have no source sentence IDs. Generation
can cite target evidence context; support reviewers can cite either role's
readonly passages. Raw maps, role/document/hash-bound IDs and quote uniqueness
checks remain exact. Only `source_paragraphs[].sentences[]` are editable.

The operator writes old/new hashes, omissions, policy, full host maps, request
bodies/hashes and byte counts. A changed context requires fresh provider counts
and fresh support labels. Bytes do not estimate tokens. Exact all-target runs
refuse any counted disclosure drift; over-byte-cap packets remain unavailable.
An evidence-empty target can still receive an explicit negative in this version.
No client or network enters preparation.

Consumer review wording must distinguish a supported navigational relation from
using a hyperlink as a citation for every source-sentence clause. This library
does not assign semantic labels, weaken support thresholds or overrule historical
judgments. Exact quote location supplies provenance only.

`unit_tests.test_embedded_constraints` is in `focused`: reciprocal alternatives,
k=1 and k>1, baseline reverses, ordering invariance, exact completion, same-source
caps, duplicate occurrences, old/new sentence behavior, full fake-provider patches
and ledgers, independent assignment replay, short prose, source shortages,
atomic list/code context, readonly IDs, role/quote failures and operator projection.
All model records in these tests are explicitly fake. Only Linux amd64 is
qualified by the inherited local images; Python source adds no platform-specific
dependency and existing RA-D09/D10 deployment limits remain.
