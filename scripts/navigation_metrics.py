"""Navigation metrics from S026 action traces (EXP-1c, exploratory).

Reads one ``traces/<query_id>.jsonl`` file per query (schema
``e007b.action_trace.v1``, policy ``bounded_provider_action_controller_v1``)
and the evaluator's support targets, and recomputes the behavioural metrics
the report shows, from the traces alone:

- ``success_at_n``: a visited page (the start page included) is a support
  target of the query; the slot value is the success rate over queries.
- ``hops_to_first_support``: opens before the first support page is entered
  (0 when the start page is a target); undefined without success; the slot
  value averages the successful queries.
- ``pages_per_success``: total opens over all queries divided by the number
  of successful queries; undefined without any success.
- ``dead_ends``: opens that land on a page with no admissible outgoing link;
  the slot value averages over queries.
- ``opens`` and ``model_calls`` per query (means), terminal-reason counts.

Usage: ``python scripts/navigation_metrics.py --traces <dir> --support
<support.json> [--per-query]``; prints one JSON object. The support file is
``{"records": [{"query_id", "target_ids"}, ...]}``; it is the judge's input
and never enters the provider container.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCHEMA = "e007b.action_trace.v1"
POLICY = "bounded_provider_action_controller_v1"
TERMINALS = ("answer", "budget_exhausted", "invalid_action", "provider_error")


def read_trace(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if (len(rows) < 3 or rows[0].get("record") != "header" or rows[-1].get("record") != "terminal"
            or rows[0].get("schema_version") != SCHEMA or rows[0].get("policy") != POLICY):
        raise ValueError(f"trace shape: {path}")
    header, actions, terminal = rows[0], rows[1:-1], rows[-1]
    opens = [row for row in actions if row["action"] == "open"]
    visits = [header["start_doc_id"]] + [row["details"]["doc_id"] for row in opens]
    if visits != terminal["visits"] or len(opens) != terminal["opens"]:
        raise ValueError(f"trace visits disagree with actions: {path}")
    return {"query_id": header["question_id"], "visits": visits, "opens": len(opens),
            "model_calls": int(terminal["model_calls"]),
            "dead_ends": sum(1 for row in opens if row["details"].get("dead_end")),
            "terminal": terminal["terminal"]["reason"], "cited_pages": list(terminal["answer"]["cited_pages"]),
            "answer_text": terminal["answer"]["text"]}


def query_metrics(trace: dict[str, Any], targets: set[str]) -> dict[str, Any]:
    first = next((index for index, doc_id in enumerate(trace["visits"]) if doc_id in targets), None)
    return {"query_id": trace["query_id"], "success": first is not None,
            "hops_to_first_support": first, "opens": trace["opens"], "model_calls": trace["model_calls"],
            "dead_ends": trace["dead_ends"], "terminal": trace["terminal"],
            "cited_support": sum(1 for doc_id in trace["cited_pages"] if doc_id in targets),
            "cited_pages": len(trace["cited_pages"])}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def slot_metrics(per_query: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [row for row in per_query if row["success"]]
    total_opens = sum(row["opens"] for row in per_query)
    return {
        "query_count": len(per_query),
        "success_count": len(successes),
        "success_at_n": _mean([float(row["success"]) for row in per_query]),
        "hops_to_first_support": _mean([float(row["hops_to_first_support"]) for row in successes]),
        "pages_per_success": round(total_opens / len(successes), 6) if successes else None,
        "dead_ends": _mean([float(row["dead_ends"]) for row in per_query]),
        "opens": _mean([float(row["opens"]) for row in per_query]),
        "model_calls": _mean([float(row["model_calls"]) for row in per_query]),
        "terminals": {name: sum(1 for row in per_query if row["terminal"] == name) for name in TERMINALS},
    }


def compute(traces_dir: Path, support: dict[str, set[str]]) -> dict[str, Any]:
    traces = {path.stem: read_trace(path) for path in sorted(traces_dir.glob("*.jsonl"))}
    if set(traces) != set(support):
        raise ValueError("traces and support targets name different queries")
    per_query = [query_metrics(traces[query_id], support[query_id]) for query_id in sorted(support)]
    return {"schema_version": "retrieval_arena.navigation_metrics.v1", "slot": slot_metrics(per_query),
            "per_query": per_query}


def read_support(path: Path) -> dict[str, set[str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {row["query_id"]: set(row["target_ids"]) for row in document["records"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--per-query", action="store_true", help="include the per-query rows")
    arguments = parser.parse_args(argv)
    result = compute(arguments.traces, read_support(arguments.support))
    if not arguments.per_query:
        result.pop("per_query")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
