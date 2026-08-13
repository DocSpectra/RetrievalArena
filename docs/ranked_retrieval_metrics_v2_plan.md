# Ranked-retrieval metrics v2 plan

Status: literature/design gate open; implementation is not authorized

## Why this is a new contract

RetrievalArena 0.1 scores token overlap between generated and reference
answers. Prediction validation accepts loosely structured retrieved-context
objects, and the drift layer computes a set-based support recall. Those
surfaces do not define qrels, stable ranked-item identity, ties, graded
relevance, cutoffs, or ranked-metric availability. Ranked retrieval therefore
requires versioned schemas and scorers rather than a configuration switch.

## Initial literature findings

Official TREC practice uses metric families with distinct interpretations:
nDCG for ranked/graded utility, Recall for coverage, and MRR when the first
relevant result has special meaning. TREC 2025 RAG retrieval reports nDCG at
30 and 100 plus Recall at 100; these cutoffs describe that track and are not
automatically valid for RetrievalArena. TREC also warns that qrels must match
the collection and that pooling can leave judgments incomplete.

The foundational nDCG work motivates cumulative gain, rank discount, and
normalization against an ideal ordering when relevance is graded. Recent WSDM
and CIKM work confirms that RAG and attributed retrieval need evaluation beyond
answer overlap, while current RAG evaluation research warns that traditional
human-browsing rank assumptions may not fully model an LLM consuming a context
set. Accordingly, v2 will retain several interpretable measures and will not
invent a composite score.

Reference status for this planning record is `zotero_unchecked`.

## Provisional metric-role matrix

| Measure | Intended question | Design constraint |
| --- | --- | --- |
| evidence/support Recall@k | Did the context budget contain the required evidence? | Define support unit and multi-support denominator first |
| nDCG@k | Were more useful items placed earlier? | Requires defensible relevance grades, gain, discount, and tie policy |
| reciprocal rank / MRR | How early was the first sufficient item? | Secondary unless one item can satisfy the information need |
| lexical answer F1 | Did answer text overlap the reference? | Legacy secondary diagnostic, not retrieval effectiveness |

The primary endpoint remains unselected until the qrels/support audit proves
which measure matches the registered claim. The cutoff must come from the
retriever/context budget and be frozen before outcome inspection.

## Required v2 surfaces

1. A qrels/support schema with question ID, ranked-item ID, support-group ID,
   binary or graded relevance, judgment provenance, and availability.
2. A ranked prediction schema with unique item IDs, explicit order, optional
   source score, deterministic tie handling, and truncation metadata.
3. Pure per-question scorers and explicitly macro-aggregated corpus outputs.
4. Explicit unavailable/undefined states; missing judgments are not coerced to
   zero and unjudged items are handled only by the frozen policy.
5. Replay manifests binding dataset, qrels, run, scorer, cutoffs, and config.
6. Reports and evidence export that distinguish primary, secondary, legacy,
   and diagnostic metrics.
7. A compatibility rule for v1 answer-scoring artifacts.

## Implementation map after design acceptance

- `retrieval_arena/schemas.py`: dataset/qrels and ranked-run admission.
- `retrieval_arena/scoring.py`: pure versioned formulas and aggregation.
- `retrieval_arena/drift.py` and `audit.py`: consume named v2 outputs without
  duplicating formulas.
- `retrieval_arena/replay_manifests.py`: content-address all scoring inputs.
- `retrieval_arena/reports.py` and `evidence_export.py`: expose metric roles and
  availability.
- `retrieval_arena/harness.py` and CLI: select only registered metric policies.
- `unit_tests/`: independent formula oracles and all edge branches.

## Literature/design acceptance gate

Before implementation, freeze ranked-item identity, relevance scale, support
semantics, cutoffs, tie and duplicate rules, missing/unjudged behavior,
aggregation unit, uncertainty inputs, primary endpoint, multiplicity policy,
and v1 compatibility. No E-EXP1 result may be opened to make these choices.

## Sources for the full review

- Järvelin and Kekäläinen, *Cumulated Gain-based Evaluation of IR
  Techniques*, ACM TOIS 2002, DOI `10.1145/582415.582418`.
- Official TREC 2025 RAG Retrieval Only results and track proceedings.
- Official TREC relevance-judgment and pooling guidance.
- CIKM 2024, *An Evaluation Framework for Attributed Information Retrieval
  using Large Language Models*, DOI `10.1145/3627673.3679172`.
- WSDM 2025 proceedings and LLM4Eval workshop record.
- Recent official TREC, WSDM, CIKM, and SDM retrieval/ranking papers selected
  by PRE-S019q-32A's documented inclusion criteria.

