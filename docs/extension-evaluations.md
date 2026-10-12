# Adding evaluations

Start at [Extending RA](extending.md); consult the [evaluation catalog](evaluations/README.md) before adding a surface. A new retrieval method is a provider, covered by the [experiment workflow](extension-experiments.md). A new metric or judging procedure changes how evidence is assessed.

## Reuse an existing scoring surface

- Fixed-element answer/context completeness: optional [Ragas Docker evaluator](evaluations/score_ragas.md), `ragas_scoring.py` contracts/arithmetic, `ragas_judge.py` framework bridge, `ragas_ollama.py` admitted local judge transport. Its separate immutable image/receipt leaves base verification and retrieval dependencies unchanged. Use its no-network smoke before changing pinned dependencies.
- Legacy answer token overlap: `retrieval_arena/scoring.py`, automatically used by YAML runs. This is not ranked retrieval accuracy or independently judged answer correctness.
- Ranked retrieval: `retrieval_arena/ranked_scoring.py`, exposed by `score-ranked` and profile evaluation. Read [score_ranked](evaluations/score_ranked.md) and `validate_ranked_inputs` for the exact dataset, qrels, prediction and policy shapes.
- Offline answer similarity: `dual_profile.py::run_bertscore_offline`, exposed by `score-bertscore`; the caller supplies answer pairs and a pinned provider. See [score_bertscore](evaluations/score_bertscore.md).
- Blinded review and external judgments: `review_packets.py`, CLI `export-review-packet` and `import-judgments`; read [dual profile evaluation](dual_profile_evaluation.md) before assuming the existing rubric fits a new study.
- Corpus and trace diagnostics: existing [catalog pages](evaluations/README.md) identify docstyle, support reachability and navigation owners.

Strict ranked predictions are not legacy `predictions.jsonl`: they use `query_id`, a schema version, context budget/exhaustion fields and ranked support-target items. The dataset includes ordered queries, judgment provenance and support groups; qrels include target relevance and judgment identity. Preserve query/target joins when projecting legacy output, and validate rather than renaming fields casually. The candidate operator has its own projection in `candidate_retrieval.py`; YAML execution does not automatically apply it.

Ranked validation enforces exact fields, policy values, duplicate rules and score/tie order. Complete versus pooled judgments affect availability: pooled nDCG is unavailable with unjudged top-k items; zero-relevant queries are rejected by default. Declare these choices before inspecting results. The scorer's `uncertainty_unit` field does not itself implement a study's uncertainty analysis.

## Implement a genuinely new evaluation

1. Specify the measured construct, required evidence, scoring unit, denominator, missing/unjudged behavior, aggregation and direction of improvement. State whether it is a retrieval, answer, navigation or corpus measure; do not silently substitute one for another.
2. Choose its owning module and versioned input/output contract. Extend a compatible scorer or add a separate module when the construct differs. Python validators are authoritative here; there is no universal metric-plugin registry or top-level schema directory to edit.
3. Trace the full consumer chain. For ranked changes inspect `ranked_scoring.py` policy validation, computation, item/aggregate output and replay hashes; for profile exposure inspect `dual_profile.py` and `profile_evaluation.py`. Adjust reports/exporters that consume the changed result. Version incompatible semantics instead of relabeling old outputs.
4. Expose only the needed invocation. A CLI command requires parser and dispatch in `cli.py`. A new immutable operator command also requires adapters and verification-container admission/dispatch; follow [operator.md](operator.md). A standalone diagnostic need not become an operator command without a scientific need.
5. Add small behavioral fixtures with hand-checkable expected scores, ties, duplicate/missing IDs, unjudged cases and replay behavior as applicable. Existing examples are `unit_tests/test_ranked_scoring.py`, `test_profile_evaluation.py`, `test_dual_profile.py` and `test_review_packets.py`. Select the suite by inspecting `containers/verification/verify.py`, not by assuming all tests run in `focused`.
6. Document the metric and its limitations in `docs/evaluations/<name>.md`, link it from the catalog, and update the relevant contract page. Record the experiment's selected policy separately in project context.

For independent answer scoring, first verify that the proposed rubric and blinded packet can be represented by the existing review contracts. That compatibility has not been established by this documentation audit. Automated overlap-derived support and BERTScore do not replace independent judgments.
