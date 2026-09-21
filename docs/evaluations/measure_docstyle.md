# `measure-docstyle` — the DocEng 2026 per-document metrics

S052 (2026-09-21). An operator command that computes, inside the
verification image with no network and no generative model, the 35
per-document measurements of the DocEng '26 paper (D'Antonio, Xie, Guo,
*Operational Runbooks as Living Documents*, DOI 10.1145/3820755.3821485,
Appendix B) over every `.md` document a corpus request admits. It reads a
corpus and answers no question, so it is an operator command beside
`score-bertscore`, not a retrieval provider. Implementation:
`retrieval_arena/docstyle_measurement.py` (the measurement),
`containers/verification/docstyle_measurement.py` (the operator entry),
`unit_tests/test_docstyle_measurement.py` (in the `focused` suite; a
thirteen-file fixture under `unit_tests/fixtures/docstyle/` with
hand-computed expectations for every model-free column).

## Invocation

```powershell
scripts/run-verification.ps1 -Command measure-docstyle -InputRoot <input> -OutputRoot <fresh output> -CandidateRoot <candidate repository> -Receipt <image receipt>
```

`.sh`: `run-verification.sh measure-docstyle <input> <output> <candidate> [receipt]`
or the named form. One phase: the operator container runs with the
repository, the input root and the candidate mounted read-only
(`/workspace/source`, `/workspace/input`, `/workspace/candidate`), the
output root writable, `--network none --read-only`, 2 GB. There is no
`retrieval_arena.cli` subcommand.

The input root holds exactly `operator-request.json`
(`retrieval_arena.docstyle_measurement_request.v1`: `command`
`measure-docstyle`, `config_path` `config.json`, `input_inventory` and its
sha256 over every other file — the `candidate-retrieve` request shape),
`config.json`, and — only when the config names it — the support file.

`config.json` (`retrieval_arena.docstyle_measurement_config.v1`):

| Field | Value |
| --- | --- |
| `corpus_id` | a short id (`[a-z0-9][a-z0-9_-]*`) written into every row |
| `candidate` | `{commit, tree}`; the candidate root must be a clean checkout at exactly these (the `candidate-retrieve` admission, `candidate_retrieval.admit_candidate`) |
| `corpus.root` | the corpus directory relative to the candidate root; `"."` is admitted for this command (a whole repository, as the paper's GitHub-docs run), unlike `candidate-retrieve` |
| `corpus.extensions` | `[".md"]` |
| `corpus.import_policy` | `cikm_flat_path_sha1_v1` or `cikm_flat_path_sha1_lower_v1`; the row's `doc_id` is the policy's id, so it joins the retrieval requests' ids |
| `corpus.exclude_policy` | absent or `null` (no exclusion), `cikm_faq_pattern_v1`, or `cikm_faq_pattern_v2` with `corpus.faq_patterns` (required under v2, refused otherwise) |
| `support_path` | optional; input-relative; a campaign's `support.json` byte-copied into the input root (`{"records": [...], "targets": [{"corpus_id", "target_id"}]}`); RA takes the sorted unique `target_id`s |

The document inventory is `candidate_retrieval`'s own rule: sorted
`rglob("*.md")` under the corpus root (a symlink aborts), the import
policy's document id, the exclusion policy; excluded documents are named in
the receipt and the manifest, never measured.

## Outputs

| File | Content |
| --- | --- |
| `docstyle_metrics.v1.jsonl` | one canonical-JSON row per admitted document: `doc_id`, `doc_path` (corpus-relative), `corpus_id`, `commit`, `error`, the export's 31 metric columns under the export's names, then `chars`, `sentence_count_basic`, `entity_type_counts`, `top_entities`; rows in path order |
| `docstyle_summary.v1.json` | per numeric column: `count`, `mean`, `median`, `p25`, `p75`, `min`, `max`, `stdev`; `spearman_rho` over the four focal signals (`imperative_verb_ratio`, `constraint_density`, `vagueness_density`, `entity_density`); when a support file was named, `support`: per support row its values and percentile ranks per column, and the unmatched ids |
| `docstyle_metrics_manifest.json` | `retrieval_arena.docstyle_metrics.v1`: the config, `python_version`, `spacy` (`spacy_version`, `meta` `lang`/`name`/`version`/`spacy_version`, `pipeline`, `model_directory_inventory_sha256`), the two catalogs' names, versions, sha256 and entry counts, `algorithm_choices`, row / error / excluded counts and ids, the support file's sha256 and id count, `table_sha256` |
| `operator_receipt.v1.json` | `retrieval_arena.docstyle_measurement_operator_receipt.v1`: status, candidate, policy and patterns, request and config hashes, excluded count and ids, row and error counts, the three outputs' sha256, the image identity, isolation, timestamps, self-hash |

No timestamp enters the table, the summary or the manifest; two runs over
the same candidate and request are byte-identical (asserted by the unit
test). A document whose measurement raises keeps its row with `error` set
and every metric `null`; the manifest and receipt count such rows.

## Definitions

The paper's text, under the export's column names
(`github_per_document.csv` of the paper's data package; paper
`moving_avg_ttr` → `moving_avg_type_token_ratio`, paper `repo_path` →
`doc_path`; the paper's `sentence_count` under Basic NLP is the spaCy
count, its Text Volume `sentence_count_basic` the regex count). The file
and Markdown-structure columns are computed from the **raw Markdown
source**, fence-aware. The text columns — `word_count`,
`sentence_count_basic`, `paragraph_count`, the two marker densities and
the spaCy input — read the **text view**: the source with fenced code
blocks (fence lines and contents) and inline code spans removed; front
matter and Liquid tags stay. This is reading revision **R1** of the S052
calibration (2026-09-21): the export README's "code blocks … are parsed
structurally, not stripped" read as *code is structure, not text*; with
the raw source `word_count` reproduced the paper's export on 71.5 % of the
rows, with the text view on 99.7 %.

- **File**: `bytes`, `chars`, `lines`.
- **Markdown structure (13)**: `header_count`, `header_h1_count` …
  `header_h6_count`, `code_fence_count`, `code_block_lines`,
  `bullet_list_count`, `numbered_list_count`, `checklist_count`,
  `list_item_count`.
- **Text volume**: `word_count` (whitespace-separated), `paragraph_count`,
  `sentence_count_basic`.
- **Vagueness & constraint**: `vagueness_density`, `constraint_density` —
  occurrences per 1 000 **words** of the two catalogs
  `retrieval_arena/docstyle_catalogs/docstyle_{vagueness,constraint}_v1.json`,
  byte-identical copies of the Capstone S051 catalogs (30 vagueness markers;
  32 listed constraint markers although the paper's prose says 28 — the
  catalog metadata records the discrepancy). Matching rule (the paper does
  not state one): text and entries casefolded, U+2019 → U+0027, whitespace
  collapsed; word-boundary, longest match first, non-overlapping — so
  `must not` counts once, `cannot` does not count `can`.
- **Basic NLP (spaCy)**: `token_count` (non-space tokens, punctuation
  included), `sentence_count`, `avg_sentence_len_tokens`,
  `type_token_ratio`, `moving_avg_type_token_ratio` (MATTR, window 50).
- **NER (spaCy)**: `entity_count`, `entity_density` (per token),
  `unique_entity_count`, `entity_mention_ratio`, `entity_type_counts`,
  `top_entities` (10).
- **POS (spaCy)**: `pronoun_count`, `pronoun_density` (per 1 000 tokens),
  `imperative_verb_ratio` (imperative / all verbs).

Zero conventions: every density and ratio is `0.0` on a zero denominator;
`entity_mention_ratio` is `0.0` when `entity_count` is 0; an empty document
has zero numeric columns, empty `entity_type_counts` / `top_entities`, and
its identity fields intact.

**Known paper-tool discrepancies** (reported by the calibration, never
adopted): the paper's tool wrote `list_item_count == bullet_list_count` on
every row while thousands of rows carry numbered items — this command
carries the paper's text (`bullet_list_count + numbered_list_count`,
checklist items counted inside `bullet_list_count` as the export does); and
the tool counted headers and list markers **inside fenced code** (a `#`
comment in a Dockerfile block as an H1) — this command is fence-aware, as
"parsed structurally" requires.

## Algorithm choices

Every choice the paper's text leaves open is fixed here, written into the
manifest (`algorithm_choices`) and never tuned toward the export
(`retrieval_arena/docstyle_measurement.py::ALGORITHM_CHOICES` is the
authoritative list):

| Choice | Rule |
| --- | --- |
| `lines` | `len(text.splitlines())` |
| `word_count` | `len(text_view.split())` (R1; before R1 the raw source) |
| header | ATX only: `^ {0,3}(#{1,6})(?:\s|$)` outside a fence; setext underlines are not headers |
| code fence | a line outside a fence whose lstrip starts with ``` or ~~~ opens a block; the next line starting with the same fence character closes it; an unclosed block runs to the end; `code_fence_count` counts opened blocks, `code_block_lines` the lines strictly between the fence lines |
| bullet / numbered / checklist | `^\s*[-*+]\s+\S` / `^\s*\d+[.)]\s+\S` outside a fence, any indent; a checklist is a bullet whose item text starts with `[ ]`, `[x]` or `[X]` |
| `paragraph_count` | every maximal run of non-blank lines of the text view, headers and list items included (R1; before R1: runs outside fences whose first line was neither a header nor a list item) |
| `sentence_count_basic` | `len(re.findall(r'[.!?]+(?=\s|$)', text_view))` |
| spaCy input | the whole text view in one `nlp()` call, `exclude=[]` |
| `type_token_ratio` | distinct casefolded non-space token texts / `token_count` |
| MATTR | window 50 over the casefolded non-space token sequence; equal to TTR when `token_count` ≤ 50 (the export decides the window: MATTR == TTR on every document ≤ 50 tokens and none above) |
| entities | `doc.ents`; unique = distinct `ent.text` (case-sensitive); `top_entities` ordered by (−count, text) |
| pronouns | `token.pos_ == "PRON"` |
| all verbs / imperative | `token.pos_ == "VERB"`; imperative = `tag_ == "VB"`, `dep_ == "ROOT"`, no `nsubj` / `nsubjpass` / `csubj` / `expl` child |
| quantiles | the order statistic at index `int(n · p)` of the ascending values (the export's rule) |
| stdev | sample (n − 1); `null` when n < 2 |
| percentile rank | (count strictly below + ½ count equal) / n over the measured rows |
| Spearman ρ | mid-rank ties over rows with `token_count > 0` — a declared departure from the paper's `verify_stats.py`, which keeps every row; `null` with `reason` `constant_column` or `insufficient_rows` |

## The model

`spacy==3.8.16` and `en-core-web-sm==3.8.0` — the base model the paper's
fine-tuned NER was built from (`en_core_web_sm-3.8.0` in the export's
driver) — are rows of `containers/verification/requirements.lock`, installed
into the provisioned base image from vendored wheels; nothing is installed
at run. The manifest carries `spacy.__version__`, `nlp.meta`, the pipeline
component names and the inventory sha256 of the installed model directory.
The NER and POS columns differ from the paper's fine-tuned model by
construction; the calibration (the S052 record) sizes the difference per
column and never corrects for it.
