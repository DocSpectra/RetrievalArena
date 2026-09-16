# `oracle_graph_support`

Graph-support oracle for calibration bounds: reads the support targets of each question from `faq_support_audit.jsonl`, takes BM25 seeds, and adds the support targets reachable from the seeds within `hop_budget` hops of `graph_edges.csv` — so its score bounds what a graph-following retriever could recover from those seeds. Diagnostic, reads support labels, never a scientific system.

**Input:** `/input/questions.jsonl`, `/input/corpus/*.md`, `/input/graph_edges.csv`, `/input/faq_support_audit.jsonl` (oracle input), `/input/config.yaml`.

**Output:** `/output/predictions.jsonl` (`question_id`, `question`, `generated_answer`, `retrieved_context`) and `/output/metadata.json` with an `oracle_graph_support` sub-object per row (seeds, support targets, reachable/unreachable targets, `min_support_distance`); items `selection_role` (`seed` | `oracle_reachable_support`), `graph_distance`, `rank`; `oracle_graph_support_diagnostics.jsonl`; metadata `diagnostic: true`, `oracle_inputs`, reachability rates.

## Knobs

| Key | Default |
| --- | --- |
| `seed_top_k` | `5` |
| `target_top_k` | `5` |
| `hop_budget` | `2` |
| `final_top_k` | `seed_top_k + target_top_k` |
| `max_context_chars_per_doc` | `1200` |
| `directed` | `false` |

**Metrics it feeds:** ranked v2 (`evidence_recall`, `complete_support`, `ndcg`, `reciprocal_rank`) over `retrieved_context` — read as a bound, not a system result.

**Admission:** not admitted at `candidate-retrieve` (runs from an experiment YAML only) (it reads support labels the endpoint never stages); a family of the BigData pilot plans.

**Tests:** no runner test; `unit_tests/test_harness.py::test_prepare_input_copies_optional_faq_support_audit` and `unit_tests/test_config.py` cover its inputs.

**Source:** `tests/oracle_graph_support/runner.py`.
