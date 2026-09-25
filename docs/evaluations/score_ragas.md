# Optional Ragas required-element evaluator

`required_element_completeness.v1` is RA's custom equal-weight, fixed-denominator
metric implemented with Ragas 0.4.3 `DiscreteMetric`. It is **not** Ragas
`FactualCorrectness(mode="recall")`. Every frozen element gets exactly one
`satisfied`, `missing` or `contradicted` verdict. Completeness is the exact satisfied
count divided by the frozen element count, without rounding. Contradictions earn
no completeness credit and retain a separate count. Duplicates never earn credit.

The same independently authored rubric applies to `seed_context`, `final_context`
and `answer`, with separate `context_support.v1` and `answer_satisfaction.v1`
prompts. Answer judging receives the question, acceptance criteria, alternatives
and answer text; retrieved contexts and rubric source passages are absent. It may
not use the rubric to supply missing answer content. Both prompts require handling
conflicts, valid equivalents, negation, prerequisites, arguments and versions.
Evidence spans are validated exact substrings with Unicode character offsets.

The initial live local-model technical probe found that both tested models put
ordinary prose in Ragas `DiscreteMetric`'s outer `reason` string under the v1
prompts. This yields explicit `parse_error`, not a score. The first v2 probe
returned nested JSON but miscounted character offsets. The paired
`answer_satisfaction.v2` / `context_support.v2` prompts therefore request exact
nonempty evidence quotes without offsets. The adapter maps each quote to its
first exact occurrence in the supplied text using Unicode character indices;
missing quotes and extra fields fail. The raw response remains available. For
repeated identical substrings, the first occurrence is a deterministic locator,
not proof that the model intended that occurrence. Prompt versions cannot be
mixed; v1 remains accepted for replay. A v2 software check is not judge
calibration.

Ragas's built-in `Faithfulness` (collections API, v0.4.3) is an independent optional
output over answer and retrieved context. It does not establish completeness or
truth. Ranked retrieval, lexical scores and offline BERTScore remain separate;
there is no composite score or change to existing profile results.

## Runtime and operator boundary

Use the **dedicated Docker image**, not a host Python environment or a retriever
image. `containers/ragas/requirements.lock` pins the complete Linux/amd64 CPython
3.12 distribution closure and each wheel hash; the Dockerfile pins its Python base
by digest. The build installs with `--require-hashes --only-binary=:all:` and runs
`pip check`. Runtime verifies installed versions against the baked lock.

Ragas 0.4.3 remains unchanged. Its unconstrained dependencies resolved to
`langchain-community==0.4.2`, which removed `chat_models.vertexai` imported by Ragas.
The actual API smoke caught this despite a successful `pip check`. The working
lock uses community 0.3.31 and the LangChain 0.3 family; `requirements.in` records
the compatibility constraints. Resolve new locks inside the pinned Linux base,
never from Windows wheels, and rerun the real API smoke after any change.

The optional operator is `scripts/run-ragas.ps1` (PowerShell 7), separate from the
existing seven-command verification operator. The latter's admission, containers,
network policy and receipts are unchanged. Its `focused` suite now includes the
dependency-free `unit_tests.test_ragas_scoring`; Ragas API checks run in the
optional image. No optional packages were added to `pyproject.toml` or providers.

`build-ragas.ps1` exports committed HEAD with `git archive` and bakes that source
into the image. It refuses uncommitted implementation paths but can coexist with
unrelated working-tree notes: neither the working tree nor Git metadata is mounted
at runtime. The image receipt binds commit/tree, lock, platform, adapter hashes
and immutable image ID. The runner verifies receipt self-hash, adapter bytes,
HEAD/tree and image labels. Rebuild its receipt after a source commit. This is a
separate committed-snapshot contract, not a relaxation of the existing mounted-
source verification operator (RA-D06).

Commands are closed: `smoke`, `fixture`, `replay`, `judge`. The first three use
`--network none`. Only `judge` admits `host_model_alias`: Docker bridge plus
`host.docker.internal:11434`, with **only GET /api/tags and POST /api/chat** in the
client, private-address checks, no proxy/redirects, and the configured model digest
observed before and after every call. The bridge is not an OS-level egress
allowlist; the trusted, baked transport enforces the endpoint/route policy, as in
the existing RA local-model exception. No arbitrary endpoint, remote API, model
download, fallback model or credentials are supported. Retrieval containers gain
no new network access. Live model selection, service version and structured-output
qualification belong to the consuming experiment; no live judge was calibrated
by this integration.

Containers run non-root, read-only, with capabilities dropped, resource bounds,
scratch tmpfs, input read-only and a fresh output directory. The request inventory
must match every input file. Successful or failed runs write a self-hashed operator
receipt with input/output hashes, source/image/lock and actual network mode.

## Smallest reproducible run

From committed RA source, in PowerShell 7 (Docker Linux containers):

```powershell
.\scripts\build-ragas.ps1
.\scripts\run-ragas.ps1 -Command smoke
New-Item -ItemType Directory .tmp/ragas-fixture
New-Item -ItemType File .tmp/ragas-fixture/.retrieval-arena-output-owner
.\scripts\run-ragas.ps1 -Command fixture -OutputRoot .tmp/ragas-fixture
```

The fixture executes real Ragas discrete and faithfulness APIs with a fake
transport, without credentials or paid calls. It writes `batch.json`, `rubrics.json`,
`config.json`, `scores.json`, `checkpoint.json` and `receipt.json`. There are nine
units: seven answer variants and two contexts. Expected answer completeness is
1, 1/2, 0, unavailable (absent output), 1/2 (contradiction), 1 (paraphrase),
1 (padding); both contexts score 1. These expectations are hand-coded test
responses, not evidence that an LLM recognizes paraphrases or omissions. The
fixture command exits zero after executing successfully, while its receipt says
`completed_with_failures` for the intentionally absent prediction.

Offline, deterministic reaggregation of those stored verdicts:

```powershell
.\scripts\prepare-ragas-input.ps1 -Command replay -Batch .tmp/ragas-fixture/batch.json -Rubrics .tmp/ragas-fixture/rubrics.json -Artifact .tmp/ragas-fixture/scores.json -InputRoot .tmp/ragas-replay-input
New-Item -ItemType Directory .tmp/ragas-replay
New-Item -ItemType File .tmp/ragas-replay/.retrieval-arena-output-owner
.\scripts\run-ragas.ps1 -Command replay -InputRoot .tmp/ragas-replay-input -OutputRoot .tmp/ragas-replay
```

Replay validates all verdicts, joins, identities and prior counts, recomputes the
aggregate, and rejects tampered scores. The intentionally absent fixture output
makes replay exit **1**, with valid scores and an explicit coverage failure; it
does not fabricate a zero. The dependency-free CLI surface is also available in
the normal RA environment:

```text
python -m retrieval_arena.cli score-ragas --batch batch.json --rubrics rubrics.json --recorded scores.json --out replay.json
```

## Input and output contract (v1)

Authoritative validators and canonical SHA-256 functions:
`retrieval_arena/ragas_scoring.py`. Objects are closed-world unless stated here.
Every `schema_version` below starts with `retrieval_arena.ragas_`.

| Artifact | Fields and validation |
| --- | --- |
| `batch.v1` | `corpus`, `units`, `predictions`; units are `[question_id, condition_id, run_id, stage]`. Declare the complete planned set; missing/unknown/duplicate rows fail. Represent known missing output explicitly with `text: null`. |
| Corpus | `corpus_id`, ordered `chunks`, canonical `sha256` of the first two fields. Each chunk has `document_id`, `chunk_id`, exact `text`, UTF-8 text `sha256`. This is the frozen exported context/source inventory; retain the parent dataset snapshot in experiment provenance. |
| `prediction.v1` | `question_id`, exact `question`, `condition_id`, `run_id`, `stage`, `corpus_sha256`, `text`, ordered `contexts`, `source`. Each context reference has document/chunk IDs and text hash. Context-stage text must equal the referenced chunks joined by two newlines. `source` binds raw source `artifact_sha256` and `record_id`. |
| `rubrics.v1` | `corpus_sha256`, `provenance`, `questions`. Provenance: nonempty `authoring_method`, `reviewer_id`, `frozen_at`, `independent_of_predictions: true`. This is a declared provenance claim, not proof of independent review. |
| Rubric question/element | Question: `question_id`, `question`, nonempty `elements`. Element: unique `element_id`, `requirement`, `acceptance`, list of `alternatives`, nonempty `sources` (same exact context-reference shape). Question sets/text and corpus joins must match. |
| `judge_config.v1` | See `configs/ragas_judge.example.json`: provider, explicit model/revision, temperature, seed, retry count, timeout, input/output token caps, separate prompt versions and faithfulness flag. No blank/unknown/latest identity. The Docker provider requires an observed SHA-256 revision. |
| `element_verdict.v1` | `element_id`, `verdict`, nonempty `reason`, `evidence`. Each evidence entry: `start`, `end` (exclusive), exact `text`. Satisfied/contradicted require spans. Missing/duplicate/unknown element verdicts fail the whole question. Unit identity is inherited from the enclosing judgment. |
| `question_score.v1` | `metric`, `unit`, exact `counts` for all three verdicts, `denominator`, `completeness`. No score is emitted for a failed question. |
| `judgment.v1` | Complete scoring `identity`, `unit`, `status`, `verdicts`, nullable `aggregate`, separate `faithfulness`, raw per-attempt responses/schemas/prompts, `measurements` and nullable `error`. |
| `scores.v1` | Input/rubric hashes, config, engine identity, prompt hashes, ordered records, summary and self-hash. Summaries macro-average scored questions **within each condition/run/stage**, showing total/scored/failure counts and complete coverage. No averaging across conditions, stages or runs. |

Empty/whitespace answer or context gives zero completeness with a nonempty rubric
without a judge call. Empty rubrics are invalid. Missing output, parse failure,
judge failure and excessive input are distinct from measured zero. Faithfulness
states include disabled, not applicable, empty answer, empty context, missing
output, no statements (Ragas NaN), parse/judge failure, and available score. Its
failure does not overwrite successful completeness.

Input admission conservatively counts UTF-8 bytes (including the response schema)
against `max_input_tokens`; no tokenizer download or hidden truncation. The
Ollama call uses an additional 1,024-token envelope allowance and the output cap.
Temperature/seed are configured; top-k=1, top-p=1, repeat penalty=1 and thinking
disabled are fixed in this version. The entire asynchronous transport call has
the configured timeout. Transport failures retry at most `retries` additional
times; malformed verdicts fail immediately. Raw attempts retain observed model
identity, usage/cost when supplied, and latency. Missing measurements are labeled
unavailable; local cost is not invented as zero.

## Export adapter and live invocation

`export_legacy_predictions(raw_bytes, manifest)` is an explicit projection of
legacy RA JSONL. `export-ragas --predictions ... --manifest ... --out batch.json`
exposes it in the normal RA CLI. The `export_manifest.v1` has `corpus`, ordered
`questions` (`question_id`, `question`), `condition_id`, `run_id`, and `context_refs`
mapping each question to ordered references. Every legacy context's `doc_id` and
`text` must match its mapped chunk; no ID guessing or passage extraction occurs.
Prepare a source inventory containing the **exact exported passages** with stable
chunk IDs. It preserves the raw answer text and hashes unmodified input bytes;
it emits answer and final-context units, never infers a seed stage or calls an
extracted answer model synthesis. Missing questions become null outputs; duplicate
or unknown questions fail. Full batch/rubric admission occurs before judging.

For a live run, first freeze independent rubrics and a model available on the
local Ollama service. Replace both identity placeholders in the example config;
do not copy credentials. Then:

```powershell
.\scripts\prepare-ragas-input.ps1 -Command judge -Batch batch.json -Rubrics rubrics.json -Artifact judge.json -InputRoot .tmp/ragas-judge-input -NoCache
New-Item -ItemType Directory .tmp/ragas-judge-output
New-Item -ItemType File .tmp/ragas-judge-output/.retrieval-arena-output-owner
.\scripts\run-ragas.ps1 -Command judge -InputRoot .tmp/ragas-judge-input -OutputRoot .tmp/ragas-judge-output
```

The reusable async surface is `score_batch(batch, rubrics, config, RagasJudge(...))`.
An alternative admitted caller can inject an async transport taking prompt,
response JSON schema and config and returning exactly `text`, observed `model`,
observed `revision`, `usage`, `cost`. It must enforce the caller's network and
identity rules. This is not permission to use an arbitrary host executor.

Cache keys include the full prediction/context, rubric/provenance, corpus, prompt
text/version, model/config, Ragas version, dependency lock and adapter source hash.
Only fully successful records are cached; failures remain retryable. Use
`-NoCache` for independent repeats (no cache reads or writes). To resume, stage a
fresh input with `-CacheRoot <previous-output>/cache` and a fresh output directory;
every reused entry is validated. Completed units are checkpointed after each row.
A killed current row can be retried; its in-flight model call may not have a
persisted response. Keep all failed-run artifacts. Never tune the judge on main
condition outputs or label these mocked software checks scientific calibration.

## Tests and sources

Core contract/export/transport-policy tests are admitted to the existing focused
suite. `test_ragas_api` is additionally run inside the optional image and exercises
actual `DiscreteMetric.ascore` and `Faithfulness.ascore`, failure handling and
replay, with fake responses below the framework API. Standard suite discovery
skips it unless `RA_RAGAS_SMOKE=1`.

Versioned upstream sources: [DiscreteMetric](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/metrics/discrete.py),
[Faithfulness](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/metrics/collections/faithfulness/metric.py).
Software compatibility is not domain calibration or evidence of answer quality.
