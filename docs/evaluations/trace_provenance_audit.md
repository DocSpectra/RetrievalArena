# Trace provenance audit v1

`retrieval_arena/trace_provenance.py`, version `trace_provenance_audit.v1`;
operator `-Command audit-trace-provenance`. It is a retrospective, offline
consumer of saved agent traces. It runs no model, tokenizer or retriever,
and it changes no input. Every output is an observation about the saved
record. None is a retrieval or answer score, a measure of reliance, or
evidence that a link was necessary.

## Inputs

The config (`retrieval_arena.trace_provenance_config.v1`) declares:
- `dialect`: the trace dialect. The only one is
  `docspectra_complex_task_agent_trace_v2`, the bounded
  search/read/follow/note/answer loop with the `working_evidence_v1`
  history policy (trace schema `docspectra.icsc.complex_task_agent_trace.v2`).
- `system_message`: the exact system message text of the run (the
  consuming workspace supplies it; RA names no prompt).
- `variants`: per variant, the agent's `corpus/` and `graph_edges.csv`.
  Optional inputs:
  - `edge_classes`: JSONL `{source, target, class: mapped_baseline | added,
    new_page_incident}`. It comes from the consuming study's own edge
    definition. An arc absent from the table is `unknown`; a row outside the
    variant graph is counted and never admitted.
  - `labels`: this repository's pooled-support outputs (`pool`,
    `page_decisions`, `judgments`, optional `qrels`).
  - `views`: graph bundles (`retrieval_arena.graph_bundle.v1`) with a
    declared `normalized_magnetic_spectrum` view (`full`, `lwcc`, …).
- `cells`: one per planned trace. Each has `tier`, `variant_id`,
  `question_id`, the expected `question_sha256`, `condition`, `replicate`,
  `follow_links`, the trace and event-log paths, the expected
  `trace_sha256`, and `labels` (whether page labels apply to it).

The operator refuses unless the input root holds exactly `config.json`,
the named files and the declared corpora.

## Identity

A trace whose bytes differ from `trace_sha256` is quarantined (one row
`quarantined: trace-bytes-mismatch`, no actions). Otherwise the trace row
records these checks:
- the trace schema and history policy;
- the question id;
- the question hash (declared, recorded and recomputed);
- the graph file SHA-256 against the variant's `graph_edges.csv` bytes;
- the corpus inventory SHA-256, the canonical JSON of `{stem: file sha256}`;
- the follow condition.

The whole-file graph hash and a bundle's canonical graph hash
(`normalized_magnetic_spectrum.graph_sha256`) are different namespaces.
`graph_identity` therefore compares node and arc sets and reports both
hashes.

## Actions

Each step is classified from its action **and** its result:
- `accepted`: the tool ran, and the loop's state changed.
- `executed_tool_error`: a search that returned an error result, which the
  loop still counts as a search.
- `rejected`: a validation or tool error. The loop state is unchanged.
- `indeterminate`: no action, for example after a transport failure.

For read and follow:
- `listed_by_prior_search`, `snippet_lines_prior`, `lines_returned_prior`.
- After success: returned ranges and a text hash, file and line identity
  against the variant corpus, `first_returned_lines`,
  `first_page_text_exposure` (no earlier returned lines and no earlier
  snippet line), `new_lines` and `new_lines_beyond_snippets`, and agreement
  with the trace's own `newly_observed_lines`.

For follow, additionally:
- `source_opened_before` and `edge_in_variant_graph`.
- `edge_class`: `mapped_baseline`, `added`, `unknown`, or `not_an_edge`
  (a rejected arc).
- `new_page_incident`.
- The menu the loop offered at that decision: every outgoing arc of every
  opened page. It is reconstructed only on a hash-verified call. The fields
  are `menu_edges`, `menu_added_edges`, `menu_unknown_edges`,
  `menu_sources` and `edge_in_menu`. Eligibility is that menu; it is not
  the anchor text visible in a returned window. Without the menu
  denominator, an added-edge share describes use, not preference.
- Per declared view: `edge_in_view`, `source_in_view` and
  `target_in_view`.

## Labels and spans

A page judgment is joined on (query id, doc id) **and** page bytes:
1. The pool row's `page_sha256` must equal the returned `file_sha256`.
2. The decision must carry the pool's page hash.

The outcomes are `supported`, `not_supported`, `undetermined`,
`never_judged` (not pooled, or pooled without a decision) and
`identity_mismatch`. A missing row is never negative. `legacy_qrels_positive`
reproduces the qrels view, in which an unjudged page counts as a miss.

A supporting span is a judgment row of the joined decision with verdict
`answers`, status `ok` and a non-empty `supporting_span`. The span is located
inside its window lines as an exact substring of the page text (lines joined
by newline). Failing that, it is matched after collapsing whitespace. The
first occurrence is used, and a span that is not found is unmappable. Span
exposure against a set of lines is:
- `full`: some located span lies inside the set;
- `partial`: some located span overlaps it;
- `none`;
- `unmappable_only`;
- `no_valid_span`.

Overlap is not a fresh semantic judgment.

## History replay

For every call, the messages are rebuilt from these parts:
- the system message;
- the task template;
- the observation: allowed actions, budgets, search-hit pages, read pages,
  read ranges, follow menu and notes (JSON `sort_keys`, ASCII);
- the assistant action and the tool result (JSON `sort_keys`, non-ASCII
  kept).

A recorded `history_compaction` is rebuilt as the working-evidence snapshot
for the retained-read count whose snapshot SHA-256 equals the recorded one.
A call is `verified` only when the SHA-256 of the canonical message list
equals both the recorded `call_start` hash and the step's `history_sha256`.
From the first unmatched call onward, context is `unknown`. Hidden model
reasoning is never inspected.

On a verified answer call:
- `final_context` per answer citation: `full`, `partial` or `none`;
- `returned_lines_in_final_context` per accepted follow: `all`, `some` or
  `none`;
- `span_exposure_final_context`.

These describe the original line text present in the request. Notes travel
in the observation as claims, so their count is reported separately.
Original lines that are absent do not show that no information from the page
was present.

## Citations

There is one row per final-answer citation and one per note citation.
- `first_exposure` says which route first returned the cited lines:
  `follow_only`, `read_only`, `mixed` or `none`.
- Page routes are reported separately. A page reached by both follow and
  read is not attributable to following.
- `first_exposing_follow_edge_classes` names the classes of the arcs whose
  follows first returned cited lines.

## Outputs

The operator writes:
- `trace_provenance.{traces,actions,citations}.jsonl`;
- `trace_provenance.variants.json`: graph and edge-class coverage, label file
  hashes, view selections and graph identity;
- `trace_provenance.summary.json`: categorical counts per (tier, variant,
  condition);
- the manifest, with row counts and hashes;
- the receipt.

Repeated observations within a question stay as separate rows. The summary
is descriptive and has no sampling unit.

## Tests

`unit_tests/test_trace_provenance.py` (focused suite) covers these cases on
forward-built fixtures:
- mismatched question and page bytes;
- a failed follow;
- repeated and overlapping reads;
- search listing versus text exposure;
- a page-positive label with an unseen span;
- unknown judgments;
- a citation after a follow plus a later read;
- compaction eviction;
- notes;
- an unavailable history, from a tampered result or an unreproducible
  snapshot.

The fixtures are forward-built by the test's own loop. Replay fidelity to a
real run is shown only by its recorded hashes.
