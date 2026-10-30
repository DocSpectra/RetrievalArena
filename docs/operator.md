# The operator and the verification image

Optional Ragas judging uses a [separate pinned image and operator](evaluations/score_ragas.md)
(`scripts/build-ragas.ps1`, `scripts/run-ragas.ps1`), with committed source baked
into the image. It does not extend this verification image's command admission or
give retrieval providers judge access. Its dependency-free contract tests are in
this image's `focused` suite; actual Ragas compatibility checks run in its own image.

Canonical runs use the repository-owned Linux verification image
(`containers/verification/`), not host Python. The image is immutable: the
Dockerfile copies exactly five tracked files (`requirements.lock`,
`verify.py`, `evaluate_profile.py`, `review_packet.py`,
`candidate_retrieval.py`) and labels the image with the source commit, tree,
dependency-lock hash and platform (`linux/amd64`). The repository is mounted
read-only at run time, so every command reads the tree at the receipt's commit.

## Build and receipt

`scripts/build-verification.ps1 [-Tag <tag>] [-Receipt <path>]` (`.sh` on
POSIX) builds from a clean tree and writes the receipt
`retrieval_arena.verification_image_receipt.v2`: `image.reference`
(`sha256:…`) and `image.tag`, `platform`, `source_commit`, `source_tree`,
`dependency_lock_sha256`, `receipt_sha256` (the self-hash). Because the
adapter's identity is the whole repository, **any commit here changes HEAD
and needs a rebuild and a new receipt** before the operator accepts one
again (the S024 follow-up — binding the adapter scripts by their own
hashes — is still open). DocSpectra Capstone tracks the receipts it runs
against (`configs/experiments/ra_s0xx_image_receipt.json`).

## Running a command

```powershell
.\scripts\run-verification.ps1 -Command <command> -InputRoot <in> -OutputRoot <out> [-CandidateRoot <cand>] -Receipt <receipt.json>
```

PowerShell 7 is required. Parameters bind by name (`PositionalBinding =
$false`; only `Command` may be positional). The `.sh` adapter accepts the
same as `--input-root= --output-root= [--candidate-root=] --receipt=` beside
its positional form. The eight `-Command` values:

| Command | What it does |
| --- | --- |
| `focused` | the fixed module list in `containers/verification/verify.py` (23 modules since C2, which added `test_pooled_support`; C1 added `test_model_providers`); on green, the last stdout line is the self-hashed `retrieval_arena.focused_verification_witness.v1` binding commit/tree/image/lock, `focused_suite_success`, the exact `focused_tests_run` count and case ids; consumers read the witness, never unittest prose |
| `all` | `unittest discover` over `unit_tests/` |
| `evaluate-profile` | the dual-profile evaluation over an input root holding `operator-request.json` (`retrieval_arena.profile_evaluation_request.v1`: `config_path`, `input_inventory`, `input_inventory_sha256`) — [dual_profile_evaluation.md](dual_profile_evaluation.md) |
| `score-bertscore` | offline BERTScore over composed answer pairs (`retrieval_arena.bertscore_scoring_request.v1`; container memory raised to 8 GB) — [evaluations/score_bertscore.md](evaluations/score_bertscore.md) |
| `review-packet` | export a blinded review packet and optionally import judgments (below) |
| `candidate-retrieve` | one provider run over a candidate repository (below); expands to the image's `candidate-retrieve-prepare` and `-finalize` around the provider container |
| `measure-graph-spectra` | `normalized_magnetic_spectrum.v1` over graph bundles in the input root (`retrieval_arena.graph_spectra_operator_request.v1`, `config.json`, bundles); single-threaded BLAS, 4 GB; writes `graph_spectra.v1.jsonl`, its manifest and a self-hashed receipt — [evaluations/normalized_magnetic_spectrum.md](evaluations/normalized_magnetic_spectrum.md) |
| `measure-magnetic-attribution` | `magnetic_attribution.v1` over graph bundles (`retrieval_arena.magnetic_attribution_operator_request.v1`, `config.json`, bundles): topology-only cycle selection, declared reversal controls, ground-cluster mass and edge energy, the fixed-mode/relaxation account; single-threaded BLAS, 4 GB; writes `magnetic_attribution.v1.jsonl`, its manifest, `artifacts/` (tables and cluster vectors) and a self-hashed receipt — [evaluations/magnetic_attribution.md](evaluations/magnetic_attribution.md) |
| `audit-trace-provenance` | `trace_provenance_audit.v1` over saved agent traces, the agent's corpus and graph, optional edge classes, pooled-support outputs and graph bundles (`retrieval_arena.trace_provenance_operator_request.v1`, `config.json`); no model; 4 GB; writes trace, action and citation tables, variant records, a summary, the manifest and a self-hashed receipt — [evaluations/trace_provenance_audit.md](evaluations/trace_provenance_audit.md) |
| `compare-answer-elements` | `answer_element_comparison.v1` over saved answers and saved element-judgment records (`retrieval_arena.answer_element_operator_request.v1`, `config.json`); no model; 4 GB; writes element rows, cells, contrasts, element/task summaries, replicate pairs, the summary, an optional blinded review packet, blank response and key, the manifest and a self-hashed receipt — [evaluations/answer_element_comparison.md](evaluations/answer_element_comparison.md) |
| `measure-phase-navigation` / `score-phase-navigation` | normalized phase features and matched semantic/random traversal, followed by a separate known-positive page-access join; judgment inputs are absent from measurement input; bounded CPU/memory — [evaluations/phase_navigation.md](evaluations/phase_navigation.md) |
| `measure-docstyle` | S052: the DocEng 2026 per-document metrics (Appendix B, spaCy `en_core_web_sm` 3.8.0) over every `.md` document of a candidate repository under the request's corpus policy; one phase with the candidate mounted read-only (`-CandidateRoot`); no question, no generative model — [evaluations/measure_docstyle.md](evaluations/measure_docstyle.md) |

Before running, the adapter checks: clean tree, receipt schema and
self-hash, lock hash, HEAD and `write-tree` equal to the receipt, and the
image labels equal to the receipt (`verification-mounted-source-identity-mismatch`,
`verification-image-label-mismatch`, …). Input roots are immutable bundles
with an `operator-request.json` and an exact inventory of every other file;
an output root is a fresh directory containing only
`.retrieval-arena-output-owner` (`verification-operator-output-not-fresh`
otherwise). Every command runs `--network none`, read-only root, `--cap-drop
ALL`, `--pids-limit 512`, a tmpfs scratch, and writes only its results plus a
self-hashed terminal receipt (`retrieval_arena.profile_evaluation_operator_receipt.v1`,
`bertscore_scoring_operator_receipt.v1`, `review_packet_operator_receipt.v1`,
`candidate_retrieval_operator_receipt.v1`) — operational evidence, not
evaluator result truth.

## `candidate-retrieve`

Admits an exact clean candidate Git commit/tree (`-CandidateRoot`), a
complete private-input inventory, and one config-declared provider from the
closed profile table in `retrieval_arena/candidate_retrieval.py`, each pinned
by `image@sha256`: `rag_lexical_topk`, `rag_graph_ranked_expand`,
`rag_llm_guided_search`, `rag_repo_search_agent`, `rag_repo_search_agent_nolinks`.
The request (`retrieval_arena.candidate_retrieval_config.v1`; shapes in
`configs/candidate_retrieval*.example.json`) names the candidate, the
`corpus` block (`root`, `import_policy`, `exclude_policy` —
[corpus_import.md](corpus_import.md)), the provider block and the ranked
budget. Prepare stages the corpus with flat ids and, for a `requires_graph`
provider, extracts the candidate's own link graph (`graph_edges.csv`, hashed
as `graph_edges_sha256` into the launch and the receipt). The provider
container runs as uid/gid 65532:65532 (the owner of `provider-output`) with
`--cap-drop ALL`. Network: every retriever runs `--network none`; the one
exception is the profile network `host_model_alias` (the navigator and the
two repo-search variants), run on Docker's default bridge with `--add-host
host.docker.internal:host-gateway` while the runner's own client enforces the
relay rules (private upstream only, `GET /api/tags` and `POST /api/chat`,
model digest observed per call); the receipt's `isolation.network` records
it. Finalize validates the rows against the profile, publishes ranked-v2
predictions atomically and, for trace-publishing profiles, `traces/` and
`navigation.json` inventoried in the receipt (`traces_inventory_sha256`,
`navigation_sha256`), every trace checked against the declared budgets and
the admitted document ids. This stage does not score or mutate qrels.

## `review-packet`

The request is closed-world `retrieval_arena.review_packet_operator_request.v1`
(`candidates_path`, `policy_path`, nullable `judgment_artifact_path`, the
inventory); candidates are `retrieval_arena.review_candidate.v1` JSONL and
the policy a strict `retrieval_arena.review_policy.v1`. A fresh output
receives `review_packet.v1.json` (canonical compact JSON plus one trailing
LF — compare raw bytes) and, with a judgment artifact, `judgment_import.v1.json`;
export and import are staged together, so an import failure leaves only the
terminal receipt. The schemas and the stratified sampling live in
`retrieval_arena/review_packets.py`; the same functions are the CLI's
`export-review-packet` and `import-judgments`.
