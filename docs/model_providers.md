# Model providers

Added in C1 (October 12, 2026). `retrieval_arena/model_providers/` is RA's one model-access layer: a structured-completion transport per provider, a per-run spend ledger, and the `external_api` network admission (RA-D07 in [architecture conventions](architecture-conventions.md)). It replaces the Ollama-only RAGAS transport and generalizes the OpenAI bridge and cost ledger that lived in the ICSC context repository. It does not choose a provider: a caller's validated config names one.

## Call shape

Every transport is `async transport(prompt, response_schema, settings)` and returns exactly `{text, model, revision, usage, cost}`. This is the RAGAS transport contract ([score_ragas](evaluations/score_ragas.md), "Export adapter and live invocation"), enforced by `RagasJudge._generate`. `model_providers.transport_for(config, network, output_dir)` returns the admitted transport for a validated config.

| Provider | Module | Endpoint and admission | Identity (`revision`) |
| --- | --- | --- | --- |
| `ollama` | `ollama.py` (moved unchanged from `ragas_ollama.py`, which remains a re-export) | `host_model_alias`: `http://host.docker.internal:11434`, routes `/api/tags` and `/api/chat`, private upstream only | SHA-256 digest, observed before and after each call |
| `anthropic` | `anthropic.py`, official `anthropic` SDK (optional) | `external_api`: `https://api.anthropic.com` only | the model id; the response's `model` must equal it |
| `openai` | `openai.py`, official `openai` SDK (optional), Responses API | `external_api`: `https://api.openai.com` only | the model id; the response's `model` must equal it or extend it with a `-<suffix>` snapshot |

Hosted models have no digest. The record keeps the requested model id as `revision` and puts the provider's `request_id`, `response_model`, stop reason or status, `service_tier`, decoding settings and UTC `date` in `usage`. RAGAS keeps every attempt's response in its raw traces.

Two call shapes are not covered yet:
- Free-text completion.
- Agent action turns. `model_turn.py` keeps its `{"openai", "ollama"}` contract unchanged; an Anthropic mapping is a follow-up.

The navigator and repo-search agents keep their own Ollama transports (follow-up under RA-D07).

## Settings (RAGAS judge config v4)

`retrieval_arena.ragas_judge_config.v4` has v3's metric semantics, emits `judgment.v2`/`scores.v2`, and adds three fields. Configs v1–v3 validate exactly as before, so their bundles replay unchanged. Examples: `configs/ragas_judge_anthropic.example.json`.

| Field | Ollama | Anthropic | OpenAI |
| --- | --- | --- | --- |
| `revision` | digest | equals `model` | equals `model` |
| `temperature`, `seed` | required numbers | `null`: hosted Claude rejects sampling overrides; none are sent and determinism is never claimed | `null` |
| `thinking` | Ollama `think` flag | `false` (thinking lives in `generation`) | `false` |
| `generation` | `null` | `{thinking: disabled\|adaptive, effort: low\|medium\|high\|xhigh\|max}`; `disabled` only at effort `high` or below | `{reasoning_effort, strict}` |
| `pricing` | `null` | `{currency: USD, input_per_mtok, output_per_mtok, long_context: null \| {above_input_tokens, input_per_mtok, output_per_mtok}, source}` | same |
| `spend_cap_usd` | `null` | `> 0` and `≤ 50` per run | same |

Refusals cover:
- an unknown provider;
- a blank/`latest`/`unknown` identity, or a hosted revision that differs from the model;
- sampling overrides on a hosted model;
- disabled thinking above effort `high`;
- missing prices or an unsourced price table;
- a cap outside the range.

The RAGAS operator refuses a hosted provider under configs v1–v3.

Anthropic requests use `output_config.format` (`json_schema`). The schema is passed through the SDK's `anthropic.transform_schema`, which adds `additionalProperties: false` and moves unsupported constraints into descriptions. RAGAS still validates the reply strictly against the original schema. The sent schema's hash is recorded.

These outcomes are errors, never judgments: a `stop_reason` other than `end_turn` (`refusal`, `max_tokens`, `pause_turn`), a response model mismatch, or more than one text block. The same holds for OpenAI with any status other than `completed`.

SDK retries are off (`max_retries=0`). The caller's `retries` is the only retry policy, and each retry is a new reservation.

## Spend ledger

`CostLedger` writes `cost-ledger.jsonl` beside the run's outputs. It works in three steps:

1. **Reserve before every call.** The reservation is `(max_input_tokens + 1024) × input rate × 1.25 + max_output_tokens × output rate`:
   - the 1,024-token allowance covers provider framing (C0 measured about 190 tokens of structured-output overhead);
   - the 1.25 factor bounds a cache write;
   - the long-context rate applies when the input cap exceeds its threshold.
   
   RAGAS `max_input_tokens` is a conservative UTF-8 byte bound, which is at least the token count.
2. **Record reported usage** as returned (input, cache write at 1.25×, cache read at 0.1×, output including thinking). Usage is never estimated from prompt length.
3. **On failure, keep the full projection.** A failed call may still be billable.

Spend is reported cost plus open and failed projections. A reservation that would exceed `spend_cap_usd` writes a `stopped` event and raises `SpendStop` before any request. A ledger resumes from an existing file.

Billed-but-rejected calls (refusal, truncation) are recorded as `reported`. Request ids are in the ledger.

Prices are inputs, not RA defaults. The C1 examples record their source; recheck it before a run.

## external_api network mode (RA-D07)

- **Containers only.** The client refuses to start outside a container (`/.dockerenv`); only unit tests pass `require_container=False`.
- **One host.** The client allows only the provider's host over https on port 443, enforced by an httpx request hook. Redirects and environment proxies are off (`follow_redirects=False`, `trust_env=False`), and environment endpoint overrides (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `OPENAI_API_BASE`) are refused.
- **Credentials by name.** The credential reaches the container by name only (`docker run --env ANTHROPIC_API_KEY`, with the value taken from the launching process's environment) and is read only by the SDK. RA checks presence, never prints the value, and redacts it from diagnostics.
- **Receipts.** They record the network mode (`external_api`), never the credential. Tests assert the value is absent from ledgers, responses and errors.
- **Bridge network.** The container runs on Docker's bridge network without `--add-host`. The allowlist is the client's, not a firewall's: code in the image could open other connections. The admitted images run only RA's operator code.

## RAGAS operator

`scripts/run-ragas.ps1` passes `-Network external_api -CredentialEnv ANTHROPIC_API_KEY|OPENAI_API_KEY` (closed set) for `judge` and `probe`. The Python operator does all admission.

`probe` is one structured call (`{"ok": boolean}`) under a full v4 config. It qualifies credential, network, identity, ledger and parse before a judge run, and writes `probe.json` and `cost-ledger.jsonl`. Prepare its input with `prepare-ragas-input.ps1 -Command probe -Artifact config.json -InputRoot <new>`.

The image lock pins `anthropic==1.13.0`; its dependencies and `openai` were already in the RAGAS closure.

## Tests

`unit_tests/test_model_providers.py` is in the `focused` suite and the RAGAS image smoke. It runs offline with fake SDK clients and covers:
- settings refusals, network and container admission, the host guard;
- ledger reserve/report/fail/stop/resume and the long-context tier;
- request shapes, refusal/truncation/model-mismatch handling;
- credential absence in outputs.

In the RAGAS image only, RAGAS metrics run through the Anthropic transport with a fake client and must equal the bare fixture transport's scores.

## Portability

The layer is pure Python with lazily imported optional SDKs, so it imports without them; the verification image has neither. The RAGAS image it runs in is still `linux/amd64` with an amd64 wheel lock (RA-D10), and its launcher is PowerShell (RA-D11).

The embedded refinement caller uses an optional per-call audit callback on both hosted transports, preserving requests/reservations, canonical SDK JSON responses and redacted failures. Optional explicit service_tier settings pin Standard processing; existing callers remain unchanged. See [the integration contract](evaluations/embedded_refinement.md).
