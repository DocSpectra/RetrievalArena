# Architecture conventions and follow-up decisions

Established September 24, 2026 after the source audit at `8424d9b979b1623631e3b6f0a9ed1399af9110b6`. These are documentation and development conventions, not new runtime fields or a migration plan. Start with [Extending RA](extending.md) for implementation instructions.

## Use precise names

| Term | Meaning and naming convention |
| --- | --- |
| Experiment | A declared dataset/condition/scoring comparison. Use a descriptive study/config name; retain historical IDs such as E002c as aliases, not the only explanation. |
| Provider | Code that produces retrieval/answer predictions. Use its exact `tests/<provider>` name. The YAML key `tests` is a historical provider-list name; call correctness checks **unit tests**. |
| Execution path | Say **YAML harness** or **candidate operator**. Do not say only “RA run,” “v2” or “direct lane” when path-specific behavior matters. Capstone's direct lane is a caller workflow, not a synonym for either RA interface. |
| Evaluation | A named scoring or diagnostic surface, such as `score-ranked` or `score-bertscore`. State the metric and policy separately from the provider. |
| Prediction contract | Say **legacy provider predictions** or **ranked-v2 predictions**; cite the validator/schema. The filename `predictions.jsonl` does not determine compatibility. |
| Dataset stage | Distinguish **acquisition**, **document preparation**, **question sourcing**, **support construction**, and **independent judgment**. “Preprocessing” alone does not identify a transform. |
| Support | Qualify it as **automatically derived support**, **supplied support records**, or **independently judged relevance**. Supplied records are not necessarily independently judged. |
| Answer | State the answer policy: extracted text, fixed-model synthesis, or agent-generated answer. A `generated_answer` field alone does not establish synthesis. |

In a new experiment's README or protocol, include a small plain-language contract summary: execution path; provider(s); input and output contracts; document-ID policy and corpus/query identities; files visible to providers versus judges; answer policy; evaluation/policy; source/image/model revisions. Link existing definitions instead of duplicating them. This is documentation, not a new config schema, registry or admission gate.

For example: “E002c (`rag_embedding_topk`), YAML harness, legacy provider predictions, extracted text; ranked scoring requires a validated projection and independently judged qrels; provider input isolation is unresolved.” This describes the audited implementation, not approval to launch it.

## Track divergence without assuming every difference is a defect

| ID | Observed boundary and consequence | Follow-up and owning code | Timing |
| --- | --- | --- | --- |
| RA-D01 | YAML stages gold answers/support; candidate execution withholds them. The same provider name does not imply the same isolation. | Decide how ordinary providers and oracle diagnostics receive distinct inputs. Owners: `harness.py::prepare_input`, `candidate_retrieval.py`. | Resolve isolation for the selected scientific run before launch; broader unification deferred. |
| RA-D02 | YAML computes legacy lexical scores; strict ranked scoring consumes a different artifact contract. | Decide whether an explicit shared projection is needed; preserve canonical IDs, rank/order and provenance. Owners: `schemas.py`, `ranked_scoring.py`, `candidate_retrieval.py::project_ranked_predictions`. | Validate the selected conversion before ranked results; broad scoring API redesign deferred. |
| RA-D03 | YAML providers and candidate-admitted profiles are different sets, with separate validation. | Admit only required providers if using the candidate path. Assess shared metadata only if recurring drift justifies it. Owners: provider runners, `config.py`, `candidate_retrieval.py::PROVIDERS`. | Required-profile availability before its run; a universal provider registry is not planned. |
| RA-D04 | Experiment preparation and candidate staging use different document-ID and exclusion contracts. | Verify joins for the selected corpus and preserve mappings. Consider shared policy helpers if duplicate semantics must change together. Owners: `corpus/dataset_export.py`, `candidate_retrieval.py`, `graph/`. | Mapping and exclusions before sample/support freeze; general importer convergence deferred. |
| RA-D05 | Fixed-model synthesis lives in Capstone while retrieval and scoring live in RA. This may be an appropriate ownership boundary. | Inspect the reusable caller contract before proposing relocation or an RA interface. | Resolve ICSC synthesis integration before answer generation; relocation deferred. |
| RA-D06 | Operator receipts bind the whole source revision/tree, so documentation commits affect matching checkouts. | Continue using the receipt-matching revision; revisit narrower adapter identity only when needed. See [operator contract](operator.md) and [agent guide](../AGENTS.md). | Deferred; no image rebuild solely to review these docs. |
| RA-D07 | Hosted model APIs need outbound network, which every other RA container lacks (`none`, or `host_model_alias` for the local model). | **Decided C1 (Oct 12, 2026):** `external_api` mode, RAGAS `judge`/`probe` only. Bridge network; the Python client admits one provider host over https (request hook), no redirects, proxies or endpoint overrides; credential passed by name, read only by the SDK; receipt records the mode, never the secret. The allowlist is in the client, not a firewall. Owners: `model_providers/base.py`, `containers/ragas/ragas_operator.py`; contract [model providers](model_providers.md). | Navigator and repo-search agent transports remain Ollama-only (follow-up); other operator commands stay offline. |
| RA-D10 | Portability: the RAGAS image is pinned `linux/amd64` with an amd64 wheel-hash lock, so it does not run natively on DGX Spark (linux/arm64) or Apple silicon. | Resolve an arm64 lock and multi-arch base digest (assessed with RA-D09 in C1b). Owner: `containers/ragas/`, `scripts/build-ragas.ps1`. | Recorded C1; not fixed there. |
| RA-D11 | Portability: the RAGAS operator launcher and receipt checks are PowerShell-only (`run-ragas.ps1`, `build-ragas.ps1`, `prepare-ragas-input.ps1`), unlike the verification operator's `.ps1`/`.sh` pair. | Move the logic into the Python orchestrator with thin shims (C1b). | Recorded C1; C1b. |

RA-D08 (orchestrator socket risk) and RA-D09 (DGX Spark gaps) are reserved for C1b. Code paths above are under `retrieval_arena/`. RA-D07 is decided; the other follow-ups are open; none authorizes an implementation refactor. An implementation decision should update its row with the chosen behavior, affected contracts, validation evidence and resolving commit. Link paper-specific consequences from project context rather than maintaining a second divergence table there. A documented difference may remain intentionally separate; close it with that rationale when decided.
