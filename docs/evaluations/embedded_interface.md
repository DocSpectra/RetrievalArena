# Source-bound refinement interface

`retrieval_arena.embedded_interface.v1` is an opt-in model-facing projection over
the existing [v2 editor/selector](embedded_refinement.md). Historical proposals,
ledgers, counts and responses retain their original meanings. It changes neither
the magnetic method nor the consumer's semantic rubric or candidate population.

The consumer supplies immutable source files and the previously selected
paragraphs/targets. `prepare-interface` assigns passage IDs bound to document ID,
source SHA-256, role, span, exact text, the existing CommonMark raw/display map and
the resolution-policy version. Host sentence IDs additionally bind the editor's
fixed lexical segmentation. A parent-to-child manifest proves that paragraph and
target arrays remain identical. Unmapped sentences are reported without replacing
the passage or packet. Models see exact text, IDs and source hashes; byte maps and
coordinate computation stay on the host.

Generation returns `source_sentence_id`, exact anchor text, target ID and exact
quotes with target passage IDs, plus the unchanged typed relation/intent fields.
The host supplies the unchanged full sentence and resolves UTF-8 spans. Matching
is exact and case-sensitive. Repeated matches within the cited sentence/passage
are refused; sentence IDs distinguish occurrences in different sentences. There
is no fuzzy match, whitespace folding, punctuation repair or first-match rule.
The tested CommonMark paragraph map permits exact display evidence to project
across Markdown syntax and CRLF, retaining the returned quote, raw quote, map,
source hash and resolution policy. An anchor must also equal contiguous raw bytes.
All existing placement, navigation, graph, conflict and application guards still
run after resolution. Exact location is not semantic support.

Eligibility and final schemas cite source and target passage IDs separately.
Validation searches only the actual disclosed review packet and rejects wrong
role/document IDs even for identical text. Review results retain the original
declaration and the separate host-resolved projection. Valid negative judgments
complete without retry. Rejected responses remain in the SDK/attempt artifacts.
The interface journal distinguishes model declarations, host resolutions and
rejections, reviewer declarations and review resolutions, with an externally
anchored hash tip. The v2 ledger separately records structural validation,
eligibility, selection, actual patches and verification. `mock_response`
provenance is explicitly tagged; it cannot be confused with `model_response` or
`authored_fixture`. Existing v1/v2 replay is preserved.

## Combined execution

`scripts/embedded-api.py study` admits one complete, immutable consumer inventory
and `bounded_embedded_refinement_v3` authorization **before constructing clients**.
It uses `model_providers.transport_for`, the existing SDK transports, CostLedger,
exact provider counts and `embedded_model_run.execute`. Both provider credentials
are passed by environment name only. No new HTTP client or provider stack exists.
The SDK image already contains the parser and numerical dependencies; inherited
linux/amd64 deployment is the only exercised target (RA-D09/D10 remain open).

The consumer freezes a small authored preflight and real lane, exact initial
requests, role settings/schemas/prompts, whole-passage omission policy, retry and
canary rules, image/source/lock, wall and spending limits. The preflight must reach
actual edited S/Z/M copies and independent final output. It is a technical check,
not evidence about the corpus. Fresh real counts precede generation; old counts
never admit a changed request. Exact admitted bodies and hashes are written before
completion. First-two original source identities form the canary; count omissions
never cause replacement cases. Schema/transport/ID/review technical failures stop
the remainder. Empty lists and substantive/placement rejections are legitimate.

The common independent pool is frozen before selection, then the unchanged v2
selector applies matched arms and deduplicates blinded final assessments. Final
packets omit arm, scores, prior verdicts, relation and rationale. Source copies,
exact graph/text/navigation checks, v2 patch replay, interface tips, HTML paragraph
previews and full-file diffs remain outputs. No utility or human accuracy follows.

Persistent attempts are written before calls. Completed counts/responses are
reused; interrupted unknown attempts consume capacity. A persisted first 401 or
403 opens a distinct authentication/permission circuit, allowing only at most
two already-in-flight requests. Unsupported model/request errors halt separately.
Only declared transient failures and response validation permit one retry, with
bounded backoff; SDK retries remain zero. Shared transports keep credentials out
of evidence. Persisted wall/monotonic anchors prevent resetting the study clock.
An interrupted offline publication is preserved for inspection rather than
silently overwritten. Authentication recovery requires an explicit bounded
continuation decision; no automatic credential rotation or capacity reset occurs.

Every completion reserves through CostLedger. The combined contract also reserves
each count at a consumer-declared serialized-request byte ceiling plus framing
and one output token, using the model input tariff as a conservative contingency
when count billing is unreported. These are reservations, not invoices or inferred
token counts. Over-byte-cap requests are unavailable; only actual provider counts
admit completion. Per-partition caps sum to the frozen parent limits, without
score-dependent transfer. All accounting includes preflight, canary and retries.

`unit_tests.test_embedded_interface` covers Unicode/CRLF, Markdown evidence,
unique/repeated anchors, stale and cross-document IDs, invented quotes, faithful
saved-response regressions, negative reviews, and the new schema through actual
matched edits/final assessment. `unit_tests.test_embedded_study` covers combined
admission, persistent circuits, count omission/restart/unknown attempts and the
complete fake-provider pipeline with cached restart. Both are in `focused`.

The pinned Anthropic SDK requires `type` on an enum schema even when JSON Schema
does not. The shared transport now adds `type: string` only to nonempty all-string
enums lacking type before the existing SDK transformation. This is a lossless
transport projection: host schemas, accepted values, prompts and corpus disclosure
remain unchanged. SDK smoke exercises both actual interface schemas without a
client. A separately inventoried `semantics_preserving_runtime_correction` may
bind a corrected immutable runtime to the same approved manifest, input inventory
and authorization, preserving all prompts/host schemas/access/caps, original
clock, cached jobs and used capacity. The original receipt and failed checkpoint
remain historical; the manifest is never rewritten as if it used the correction.
