# `answer_element_comparison.v1`

An offline, retrospective decomposition of **saved** rubric judgments of **saved** answers into element-level
differences between arms. Source: `retrieval_arena/answer_element_comparison.py`; operator command
`compare-answer-elements` (`containers/verification/answer_element_comparison.py`); tests
`unit_tests/test_answer_element_comparison.py` (focused suite).

It never calls a model and never re-grades. It explains how recorded verdicts add up to recorded scores; reusing
the same verdicts is not independent validation of answer completeness. A matching quotation shows that the text
exists in the answer, not that it is correct or meets the requirement.

## Inputs

Operator request `retrieval_arena.answer_element_operator_request.v1` (`config.json` plus the input inventory; the
input root must hold exactly the files the config names). Config `retrieval_arena.answer_element_comparison_config.v1`:

| Key | Content |
| --- | --- |
| `arms`, `conditions`, `replicates` | the schedule's labels; every answer must sit in exactly one slot `arm × task × condition × replicate` |
| `contrasts` | ordered pairs `[a, b]`; differences are `a − b`, labelled `a-b` |
| `tasks` | `task_id`, `question` (its SHA-256 must equal `question_sha256`), `elements` (`element_id`, `requirement`, `acceptance`, in frozen order), optional `references` (`label`, `path`) shown to a reviewer |
| `answers` | `answer_id`, `task_id`, `arm`, `condition`, `replicate`, `terminal`, `answer_path` (UTF-8 text, or `null` when no answer was produced), `answer_sha256`, `question_sha256` |
| `judgments` | `judgment_id`, `answer_ids` (one or more: aliases are kept, never inferred), `record_path` |
| `scores` (optional) | `{measure: {answer_id: [grade, …]}}`; several grades of one answer (scoring passes) are averaged before aggregation, missing grades dropped |
| `selection` (optional) | `salt`, `focal` arm, `contrasts` (each `[focal, other]`, a subset of `contrasts`), `max_pairs` (at most four per contrast), `forbidden_strings` |

A judgment record (`retrieval_arena.element_judgment_record.v1`) carries the judged `question`, `answer` and
`elements` exactly as the judge saw them, `status` (`valid` or another label), `selected_attempt`, the selected
attempt's `raw_verdicts` and `strict_verdicts` (each `element_id`, `verdict`, `answer_span`, `reason`) and
`sources` (hashes of the files the workspace adapter read). Adapting a legacy layout into records is the
consuming workspace's job; its adapter should be pinned and tested.

## Joins and outcomes

A judgment joins an answer only when the record's answer text equals the answer file, the record's question hash
equals the answer's and the task's, and the record's element list equals the task's (ids, requirement and
acceptance text, order). Otherwise the answer is `identity_mismatch` with reasons (`answer_text`, `question`,
`element_ids`, `element_text`, `element_order`, `several_judgments`). Raw and strict verdict ids are checked for
missing, duplicate and foreign elements; a missing or duplicated element is not observed. Answer statuses:
`judged`, `no_answer`, `no_judgment`, `no_valid_completion`, `identity_mismatch`.

Each scheduled answer × element is one row with the raw verdict, reason and span, the strict verdict, relaxed
eligibility and rule, and the quote location (`verbatim` with offsets, `located_<rule>`, `not_located`,
`no_span`). Outcomes:

| Outcome | Meaning | Counted |
| --- | --- | --- |
| `relaxed_satisfied` | raw `satisfied` and the span passes `quote_match_relaxed_v1` | yes |
| `quote_rejected` | raw `satisfied`, span fails the rule | no |
| `judged_not_satisfied` | any other raw verdict | no |
| `not_observed` | no answer, no or invalid judgment, identity mismatch, missing or duplicate verdict | — |

`quote_match_relaxed_v1` is reproduced exactly: NFKC; inline citation tags (`[… line(s) …]`, `(… line(s) …)`)
dropped; `*`, backticks and straight/curly quotes stripped; whitespace collapsed. Rules in order: exact substring;
ellipsis pieces in order (an ellipsis span uses only this rule); normalized substring; at least 90% of span words
in order (LCS over the whole answer). Per-answer score = counted / elements.

## Aggregation (`replicate_condition_task_v1`)

Per arm × task × condition the mean over replicates with a value; per arm × task the mean over conditions with a
value; a contrast is the mean over tasks with both values of the per-task difference, tasks weighted equally. The
same order applied to an element's counted indicator gives element rates whose contributions
(`difference / elements / tasks`) add up to the overall relaxed difference. Elements, conditions, replicates and
scoring passes are not independent samples; no interval is computed here.

## Outputs

`answer_element.rows.jsonl`, `.answers.jsonl`, `.issues.jsonl`, `.cells.jsonl` (task × condition × element × arm:
counted, observed, scheduled, planned, rate, per-replicate outcome), `.contrasts.jsonl` (per contrast × task ×
condition × element: rates, difference, complete flag, numbered-replicate directions, `persistence` — `persists`,
`reverses`, `one_sided`, `unchanged`, `incomplete` — and `shared_omission`/`shared_satisfied`),
`.elements.jsonl` (over conditions: mean difference in the historical order and over complete cells only,
contribution, direction and persistence counts), `.tasks.jsonl`, `.pairs.jsonl` (per contrast × task × condition ×
numbered replicate: gains = elements counted for `a` only, losses = for `b` only, stratum), `answer_element.summary.json`
(coverage, the reconstruction of per-measure contrasts, per-condition and per-replicate breakdowns), the manifest
and a self-hashed receipt. Identically numbered replicates are display pairs, not paired draws.

## Bounded blinded review packet

With `selection`, candidates are complete numbered-replicate pairs (both answers judged with every element
observed). Strata: `net_gain` (gains > losses), `net_loss`, `tradeoff` (equal, nonzero), `label_agreement` (none).
Within each stratum the pair with the lowest `sha256(salt|contrast|task_id|condition|replicate)` is selected; an
empty stratum stays empty. The focal arm's side alternates A/B in the order of a separate key
(`salt|position|…`), so sides balance within one per contrast; packet order uses `salt|order|…`. Outputs: the
selection receipt (candidate counts and keys), the packet (`retrieval_arena.blinded_answer_review_packet.v1`: task,
elements, references and answers A/B only), a blank response form (`…review_response.v1`: per element per answer
`satisfied`/`partly satisfied`/`not satisfied`/`unsure` with quote, reason and contradiction/unsupported note; an
overall A/B/tie/unsure after the element review; reviewer identity and role, date, reviewed pairs, unblinding) and
a separate key. The packet is refused when it carries extra keys or any configured forbidden string, answer id,
judgment id or judge reason. Blinding metadata cannot hide what answer content reveals. A selected,
outcome-stratified packet illustrates and checks judgments; it does not estimate prevalence.
