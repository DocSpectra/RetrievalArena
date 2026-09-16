# Retrieval evaluation

How a retrieval provider is run and how its output is scored. The table of
every evaluation is [evaluations/README.md](evaluations/README.md); each row
links its own page. The provider reference is the table in
[../AGENTS.md](../AGENTS.md).

## The provider contract

A provider is one directory `tests/<name>/` with a `runner.py` and a
container build context. The container reads `/input` and writes `/output`:

- `/input/questions.jsonl` — one `{"question_id", "question"}` per line;
- `/input/corpus/` — one Markdown file per document; the document id is the
  file stem (flat `<stem>__<sha1[:8]>` ids at `candidate-retrieve`);
- `/input/graph_edges.csv` (optional) — directed `source,target` link graph;
- `/input/config.yaml` — the provider's knobs, written as JSON (the `config`
  block of the experiment YAML or the request);
- `/output/predictions.jsonl` — one row per question: `question_id`,
  `question`, `generated_answer`, `retrieved_context` (a ranked list of
  `{"doc_id", "text", "score", "source"}` items; most runners add `rank`);
  navigators add `answer_policy` and `cited_pages`, the `best_passage_v1`
  policy adds `answer_provenance`;
- `/output/metadata.json` — the runner's name and resolved knobs; navigators
  also write `traces/<question_id>.jsonl` and `navigation.json`.

`tests/_common_runner.py` illustrates the row shape (`write_outputs`,
`load_input`); the runners carry their own copies rather than importing it.
Knobs are the `config.get(...)` keys of each runner, listed per row under
`docs/evaluations/`.

## Two ways a provider runs

1. An experiment YAML: `python -m retrieval_arena.cli run --config
   configs/<experiment>.yaml` builds each test's `image` from its
   `build_context`, stages the dataset into `/input`
   (`retrieval_arena/harness.py`) and runs the container (`--network none`
   when the test sets `network_disabled: true`); `configs/experiment.example.yaml`
   is the shape.
2. One config-declared profile at `candidate-retrieve`: the operator
   (`scripts/run-verification.ps1 -Command candidate-retrieve`) admits one
   provider from the closed profile table in
   `retrieval_arena/candidate_retrieval.py`, prepares the candidate
   repository's corpus, runs the provider as uid 65532 and publishes
   ranked-v2 predictions with a receipt — see [operator.md](operator.md).

## The scoring chain

- Predictions → `score-ranked` (`retrieval_arena/ranked_scoring.py`): strict
  qrels, run and policy artifacts, arithmetic delegated to
  `ir-measures==0.4.3` at the policy's `context_item_budget_k`;
  `python -m retrieval_arena.cli score-ranked --dataset --qrels --predictions
  --policy --out-dir`, or `evaluate-profile` through the operator
  (`retrieval_arena/dual_profile.py`, [dual_profile_evaluation.md](dual_profile_evaluation.md)).
- Answers → `score-bertscore` (`dual_profile.py::run_bertscore_offline`):
  provider-pinned offline BERTScore over answer pairs the caller composes.
- Navigator traces → `scripts/navigation_metrics.py`
  (`e007b.action_trace.v1`) and `scripts/search_navigation_metrics.py`
  (`e007c.action_trace.v1`), read with the judge's support file.
- The corpus itself → `python -m retrieval_arena.cli corpus reachability
  --dataset <dir>` (`retrieval_arena/support/reachability.py`).

## Metric ids

Ranked v2 (`ranked_scoring.py`; per query, then `query_macro` aggregation):

| Id | Meaning |
| --- | --- |
| `evidence_recall` (`macro_evidence_recall`) | fraction of a query's support targets in the top-k — the primary metric, at the policy's `context_item_budget_k` (ir-measures `Recall@k`) |
| `complete_support` (`macro_complete_support`) | 1 when every support target of the query is in the top-k, else 0 |
| `ndcg` (`macro_ndcg`) | `nDCG@k`, identity gain, `log2(rank+1)` discount; `None` when unjudged items sit in the top-k under a pooled policy |
| `reciprocal_rank` (`mrr`) | `RR@k`, the first support target's reciprocal rank |

The primary result is macro evidence recall at the registered context-item
budget. The plain-language guide and comparison of these metrics is
[ranked_retrieval_metrics_v2_plan.md#metric-guide](ranked_retrieval_metrics_v2_plan.md#metric-guide).
BERTScore records `precision`, `recall`, `f1` per query (profile families
`bertscore_precision`, `bertscore_recall`, `bertscore_f1`); navigation,
search-navigation and reachability ids are on their rows in the table.
