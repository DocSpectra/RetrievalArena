"""Search-navigation metrics from S046 action traces (EXP-1e, exploratory).

Reads one ``traces/<query_id>.jsonl`` file per query (schema
``e007c.action_trace.v1``, policy ``bounded_repo_search_controller_v1``) and
the evaluator's support targets, and recomputes every slot metric the report
shows, from the traces alone -- with each success attributed to the route
that produced it:

- ``success_at_n``: a visited (opened) page is a support target of the query.
- ``landing_hit``: a support target appears among the landing search's hits
  (before any open) -- the vocabulary effect, independent of what the model
  then does.
- ``success_route``: the route of the first support page opened --
  ``landing`` (offered by the landing search), ``search`` (offered by a
  later search), ``link`` (an outlink of the current page that no search had
  offered), or ``none``.
- ``link_new``: the followed edge (from the page the reader was on to the
  support page) is absent from the baseline graph given with
  ``--baseline-graph``; only defined for a ``link`` route.
- ``hops_to_first_support``: opens before the first support page;
  ``cost_to_first_support``: opens plus searches spent before it (the landing
  search counts as one search).
- ``opens``, ``searches``, ``model_calls``, ``dead_ends`` per query (means),
  ``queries_with_zero_hit_search``, terminal-reason counts, and the slot
  columns ``successes_by_route``, ``successes_via_new_link``,
  ``landing_hit_rate``, ``cost_to_first_support_by_route``.

Usage: ``python scripts/search_navigation_metrics.py --traces <dir> --support
<support.json> [--baseline-graph <graph_edges.csv>] [--per-query]``; prints one
JSON object. The support file is ``{"records": [{"query_id", "target_ids"}, ...]}``;
it is the judge's input and never enters the provider container.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

SCHEMA = "e007c.action_trace.v1"
POLICY = "bounded_repo_search_controller_v1"
ROUTES = ("landing", "search", "link")
TERMINALS = ("answer", "budget_exhausted", "invalid_action", "provider_error")


def read_trace(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if (len(rows) < 3 or rows[0].get("record") != "header" or rows[-1].get("record") != "terminal"
            or rows[0].get("schema_version") != SCHEMA or rows[0].get("policy") != POLICY):
        raise ValueError(f"trace shape: {path}")
    header, actions, terminal = rows[0], rows[1:-1], rows[-1]
    searches = [row for row in actions if row["action"] == "search"]
    opens = [row for row in actions if row["action"] == "open"]
    visits = [row["details"]["doc_id"] for row in opens]
    if (not searches or searches[0]["details"].get("landing") is not True
            or visits != terminal["visits"] or len(opens) != terminal["opens"]
            or len(searches) != terminal["searches"]):
        raise ValueError(f"trace visits or searches disagree with actions: {path}")
    return {"query_id": header["question_id"], "follow_links": header["follow_links"],
            "landing_doc_ids": list(searches[0]["details"]["doc_ids"]),
            "searches": [{"query": row["details"]["query"], "doc_ids": list(row["details"]["doc_ids"])}
                         for row in searches],
            "opens": [{"doc_id": row["details"]["doc_id"], "from_doc_id": row["details"].get("from_doc_id"),
                       "route": row["details"]["route"], "searches_before": row["details"]["searches_before"],
                       "opens_before": row["details"]["opens_before"],
                       "dead_end": bool(row["details"].get("dead_end"))} for row in opens],
            "visits": visits, "open_count": len(opens), "search_count": len(searches),
            "zero_hit_searches": sum(1 for row in searches if not row["details"]["doc_ids"]),
            "model_calls": int(terminal["model_calls"]),
            "dead_ends": sum(1 for row in opens if row["details"].get("dead_end")),
            "terminal": terminal["terminal"]["reason"], "cited_pages": list(terminal["answer"]["cited_pages"]),
            "answer_text": terminal["answer"]["text"]}


def query_metrics(trace: dict[str, Any], targets: set[str],
                  baseline_graph: dict[str, set[str]] | None = None) -> dict[str, Any]:
    first = next((row for row in trace["opens"] if row["doc_id"] in targets), None)
    route = first["route"] if first is not None else "none"
    link_new = None
    if first is not None and route == "link" and baseline_graph is not None:
        link_new = first["doc_id"] not in baseline_graph.get(first["from_doc_id"] or "", set())
    return {"query_id": trace["query_id"], "success": first is not None,
            "landing_hit": any(doc_id in targets for doc_id in trace["landing_doc_ids"]),
            "success_route": route, "link_new": link_new,
            "hops_to_first_support": first["opens_before"] if first is not None else None,
            "cost_to_first_support": (first["opens_before"] + first["searches_before"]) if first is not None else None,
            "opens": trace["open_count"], "searches": trace["search_count"],
            "zero_hit_search": trace["zero_hit_searches"] > 0,
            "model_calls": trace["model_calls"], "dead_ends": trace["dead_ends"], "terminal": trace["terminal"],
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
        "landing_hit_rate": _mean([float(row["landing_hit"]) for row in per_query]),
        "successes_by_route": {route: sum(1 for row in successes if row["success_route"] == route) for route in ROUTES},
        "successes_via_new_link": sum(1 for row in successes if row["link_new"] is True),
        "hops_to_first_support": _mean([float(row["hops_to_first_support"]) for row in successes]),
        "cost_to_first_support": _mean([float(row["cost_to_first_support"]) for row in successes]),
        "cost_to_first_support_by_route": {
            route: _mean([float(row["cost_to_first_support"]) for row in successes if row["success_route"] == route])
            for route in ROUTES},
        "pages_per_success": round(total_opens / len(successes), 6) if successes else None,
        "dead_ends": _mean([float(row["dead_ends"]) for row in per_query]),
        "opens": _mean([float(row["opens"]) for row in per_query]),
        "searches_mean": _mean([float(row["searches"]) for row in per_query]),
        "queries_with_zero_hit_search": sum(1 for row in per_query if row["zero_hit_search"]),
        "model_calls": _mean([float(row["model_calls"]) for row in per_query]),
        "terminals": {name: sum(1 for row in per_query if row["terminal"] == name) for name in TERMINALS},
    }


def read_graph(path: Path) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("source") and row.get("target"):
                graph.setdefault(row["source"], set()).add(row["target"])
    return graph


def compute(traces_dir: Path, support: dict[str, set[str]],
            baseline_graph: dict[str, set[str]] | None = None) -> dict[str, Any]:
    traces = {path.stem: read_trace(path) for path in sorted(traces_dir.glob("*.jsonl"))}
    if set(traces) != set(support):
        raise ValueError("traces and support targets name different queries")
    per_query = [query_metrics(traces[query_id], support[query_id], baseline_graph) for query_id in sorted(support)]
    return {"schema_version": "retrieval_arena.search_navigation_metrics.v1", "slot": slot_metrics(per_query),
            "per_query": per_query}


def read_support(path: Path) -> dict[str, set[str]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return {row["query_id"]: set(row["target_ids"]) for row in document["records"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--support", type=Path, required=True)
    parser.add_argument("--baseline-graph", type=Path, default=None,
                        help="the same seed's baseline export graph_edges.csv (defines link_new)")
    parser.add_argument("--per-query", action="store_true", help="include the per-query rows")
    arguments = parser.parse_args(argv)
    graph = read_graph(arguments.baseline_graph) if arguments.baseline_graph is not None else None
    result = compute(arguments.traces, read_support(arguments.support), graph)
    if not arguments.per_query:
        result.pop("per_query")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
