# `pooled_support`

Added in C2 (October 12, 2026). A TREC-style qrels construction stage: pool candidate pages from several sources, verify each pooled page with a hosted LLM under a frozen prompt, and write qrels that [`score_ranked`](score_ranked.md) accepts with `qrels_scope: pooled`. The labels are **LLM-verified support** (support construction followed by automatic verification, in the terms of [architecture conventions](../architecture-conventions.md)), not independently judged relevance. Report them with the measured spot-check error rate.

## Stages

| Verb | What it does | Runs in | Network |
| --- | --- | --- | --- |
| `support pool` | windows, two passage generators, evaluated runs, pool union | an image with `sentence-transformers` (the pinned embedding image) | none |
| `support verify` | one verifier call per (query, window); checkpoint, resume, incremental extension; page decisions; qrels | an image with the `anthropic` SDK (the RAGAS image) | `external_api` |
| `support escalate` | one verifier call per `undetermined` page of a prior version, on the whole page; new version (operator only) | as `support verify` | `external_api` |
| `support review-packet` | stratified, seeded, blinded spot-check packet plus a separate key | any RA image | none |
| `support import-review` | validate reviewer responses; precision and miss rate with Wilson 95% intervals | any RA image | none |

Each verb is a function in `retrieval_arena/support/` (`pooling.py`, `verification.py`, `review.py`) and a CLI verb (`python -m retrieval_arena.cli support <verb>`). The containerized form is the closed operator `containers/support/support_operator.py`, launched by the thin host shim `scripts/run_support.py` (docker or podman). The shim writes `operator-request.json` with the input inventory, mounts the RA source read-only at `/opt/ra-src`, picks the network, and passes `ANTHROPIC_API_KEY` by name for `verify` and `escalate` only. It refuses a dirty `retrieval_arena/` or `containers/support/` unless `--allow-dirty` is passed, and the receipt records the source commit, tree and dirty flag. Every operator writes `receipt.json` (`retrieval_arena.pooled_support_operator_receipt.v1`) with the input inventory hash, the args and an output inventory.

## Pool rule (`pooled_support_pool_v1`)

Config `retrieval_arena.pooled_support_pool_config.v1`: `generator_depth`, `run_depth`, `window_lines`, `stride_lines`, `windows_per_generator`, `query_field` (only `answer`), `runs` (`run_id`, `path`, `format`: `legacy_predictions` or `ranked_prediction_v2`) and `embedding_model_id`.

- **Windows.** Each page is split into `window_lines`-line windows at `stride_lines`; the last may be shorter; blank windows are dropped. Window id `<doc_id>#L<start>-<end>`. Doc ids follow RA's `corpus_doc_id` rule (relative path without suffix).
- **Generators**, both matched against the query's **reference answer**; a page scores its best window; ties break by doc id ascending; zero scores are dropped.
  - `bm25_passage`: Okapi BM25 over windows (k1 1.2, b 0.75), content terms from the support tokenizer.
  - `embedding_passage`: cosine over L2-normalized embeddings (a local sentence-transformers model, offline).
- **Pool** per query: the union of each generator's top `generator_depth` pages and every run's top `run_depth` pages. Run pages not in the corpus are counted (`unknown_run_pages`), not pooled.
- **Windows judged** per pooled page: its top `windows_per_generator` windows under each generator, deduplicated. Both generators score every window, so a run-only page still gets windows.
- **Outputs:** `pool.jsonl` (`retrieval_arena.pooled_support_pool_row.v1`: query, page, `page_sha256`, `sources` with the rank in each source, `window_ids`, best window scores), `windows.jsonl`, `corpus_inventory.json` (page sha256 by id) and `pool_manifest.json` (config, corpus inventory hash, run hashes, summary).

Depths and rules are fixed before any verification and are never tuned on outcomes.

## Verifier (`pooled_support_verifier.v1`)

Config `retrieval_arena.pooled_support_verifier_config.v1`: the provider fields of the [model provider layer](../model_providers.md) (`provider`, `model`, `revision`, `temperature`/`seed` null for hosted models, `thinking`, `generation`, `pricing`, `spend_cap_usd`), plus `max_input_tokens` (a UTF-8 byte ceiling per prompt), `max_output_tokens`, `retries`, `concurrency`, `timeout_seconds` and `prompt_version`.

- **Prompt.** Question, reference answer and one passage. The question asked: does the passage state the reference answer, or a necessary part of it? The structured reply is `{verdict: answers | not_answer | insufficient, supporting_span, reason}`. The passage is declared data, and instructions inside it are to be ignored (keyword-stuffing and injection risk). The template and schema are hashed (`prompt_sha256`).
- **Identity.** `settings_sha256` hashes the config without its operational fields (`spend_cap_usd`, `retries`, `concurrency`, `timeout_seconds`). Each judgment row records both hashes and the provider's request id and usage.
- **Statuses, never silent "no":** `ok`; `parse_error`; `schema_error`; `span_invalid` (verdict `answers` with a span that is not in the passage after whitespace normalization); `provider_error` (after `retries`); `input_too_long`. A spend-cap stop ends the run cleanly; unjudged windows stay unjudged.
- **Page decision** (`retrieval_arena.pooled_support_page_decision.v1`): `supported` if any window is `ok` with `answers`; `not_supported` only if every window is `ok` with `not_answer`; `undetermined` otherwise (an `insufficient` or failed window); `unjudged` if a window has no row.
- **Qrels.** `qrels.jsonl` has the exact `score_ranked` row shape: `relevance` 1 for supported, 0 for not supported, `judgment_id` `pooled_support_llm_verified_v1:v<version>:<query>:<page>`, and `judgment_provenance_sha256` (the hash of that page's judgment rows). Undetermined and unjudged pages are absent, so `score_ranked` treats them as unjudged. A query with no supported page is left out (score_ranked rejects zero-relevant queries) and listed in `qrels_manifest.json` as `queries_dropped_no_verified_page`. The manifest records the counts, the hashes, the pool rule and `ranked_dataset_fields` input.
- **Ledger.** The provider layer's `CostLedger` writes `cost-ledger.jsonl`; the reservation per call uses that prompt's byte count.

## Resume and incremental versions

- **Resume.** Judgments are appended as they finish. A rerun into the same output, with the same pool, prompt, settings, prior and query selection (`verify_state.json`), judges only windows without a terminal row (`ok`, `span_invalid`, `input_too_long`). The operator's `resume` arg stages a stopped run's state, judgments and ledger into a fresh output.
- **Incremental (`prior`).** A new version starts from the prior version's `judgments.jsonl` bytes, unchanged, and judges only (query, page, page content) triples the prior never judged. A different prompt or settings hash is refused. The prior's page decisions carry over unchanged. The qrels cover the current pool's corpus: a decision counts only if its `page_sha256` equals the corpus page's. So an edited page that kept its id is unjudged on the new corpus until it is verified.

## Whole-page escalation (`whole_page_escalation_v1`)

Added October 24, 2026 (ICSC D-P5). Line windows can split an answer: each window holds part of it, comes back `insufficient`, and the page stays `undetermined`, so a query whose only answering page is split can be dropped. `escalate` (`EscalateRun`) takes a prior version (`prior`), its pool and the pool's corpus (`corpus`, checked against `corpus_inventory.json`), and judges every `undetermined` decision on the current corpus (of the selected `query_ids`, default all the prior's queries) once more, with the **same prompt**, on the whole page (stripped, as one window `<doc_id>#page`).

- **Settings.** The prompt hash must equal the prior's; the settings may differ (normally a larger `max_input_tokens`, so long pages fit). The manifest records both settings hashes (`escalation.prior_settings_sha256`). A page that still does not fit is `input_too_long` and stays undetermined.
- **Decisions.** `ok` `answers` makes the page `supported` (answering window `<doc_id>#page`); `ok` `not_answer` makes it `not_supported`; anything else leaves it `undetermined`. Each target's decision gets an `escalation` block (window, judgment key, status, verdict, prior decision). Every other decision is copied unchanged. Qrels follow the usual rule, so a dropped query whose page is now supported is kept again.
- **Version.** A new qrels version (`prior + 1`); judgments start from the prior bytes; resume as for `verify`. The manifest's `escalation` block gives the target count and outcome counts.
- **Reading.** A whole page is a larger passage than a window, so the verifier sees more context: report escalated labels as such (counts from the manifest), and apply escalation to every pool compared, never to one arm only.

## Spot-check

`review-packet` samples `per_stratum` verifier-supported and `per_stratum` verifier-not-supported pages (seeded, without replacement, current corpus only) and shuffles them. Each item shows question, reference answer, page id and one passage: the accepted window for a supported page, the top-ranked window otherwise. It never shows the verdict. `review_key.json` maps items to strata and holds the stratum sizes; the review page never receives it.

`import-review` takes `retrieval_arena.pooled_support_review_responses.v1` (`packet_id`, `reviewer`, `responses: [{item_id, response: yes | no | unsure}]`). It refuses unknown or duplicate items and returns:
- `precision`: reviewer-yes among decided items in the verifier-yes stratum;
- `miss_rate`: reviewer-yes among decided items in the verifier-no stratum;
- Wilson 95% intervals, `unsure` counts, the disagreements and these limits:
  - misses outside the pool are not measured;
  - one window, not the whole page, is shown;
  - a pooled error rate needs the stratum sizes as weights;
  - one reviewer.

## Tests

`unit_tests/test_pooled_support.py` (in `focused`), offline with a fake transport and a bag-of-words encoder: window boundaries; BM25 and embedding determinism and ties; pool union, per-source provenance and run-only windows; parse failures as explicit statuses; qrels accepted by `validate_ranked_inputs` (pooled, unjudged stays unjudged); spend-stop and resume; incremental verify (judged pages skipped, prompt or settings change refused, prior files byte-identical, judgments a byte prefix); changed page content becomes unjudged; escalation (a split answer becomes supported and its dropped query returns, other decisions unchanged, `not_answer` / `insufficient` / `input_too_long` outcomes, resume, corpus and prompt refusals); review packet determinism, blinding, stratum checks, round trip and Wilson bounds.

## Portability

The modules are pure Python plus numpy; `sentence-transformers` and `anthropic` are imported lazily. The launcher is Python (docker or podman), not PowerShell. The two images it currently runs in are `linux/amd64` only: the embedding image and the RAGAS image (RA-D10). Recorded as RA-D12.

## Literature

The design follows pooling (TREC; Buckley et al. 2007 on pool bias) and LLM relevance judging (Thomas et al. 2024; UMBRELA) with a human spot-check, as in FreshStack (NeurIPS 2025). The consuming project keeps the verified reference list.
