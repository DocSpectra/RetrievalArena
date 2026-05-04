from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
TRACE_SCHEMA_VERSION = "e007b.action_trace.v1"
POLICY_NAME = "mock_llm_guided_fixture_controller_v1"


@dataclass(frozen=True)
class SearchConfig:
    search_top_k: int = 5
    inspect_budget: int = 8
    follow_budget_per_page: int = 2
    max_actions: int = 30
    final_top_k: int = 5
    max_context_chars_per_doc: int = 1200
    directed: bool = False


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def parse_config(raw: dict[str, Any]) -> SearchConfig:
    return SearchConfig(
        search_top_k=max(1, int(raw.get("search_top_k", raw.get("top_k", 5)))),
        inspect_budget=max(1, int(raw.get("inspect_budget", 8))),
        follow_budget_per_page=max(0, int(raw.get("follow_budget_per_page", 2))),
        max_actions=max(3, int(raw.get("max_actions", 30))),
        final_top_k=max(1, int(raw.get("final_top_k", raw.get("top_k", 5)))),
        max_context_chars_per_doc=max(0, int(raw.get("max_context_chars_per_doc", 1200))),
        directed=bool(raw.get("directed", False)),
    )


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def answer_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip("# ").strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    snippet = " ".join(lines)
    if max_chars > 0 and len(snippet) > max_chars:
        return snippet[:max_chars].rsplit(" ", 1)[0].strip()
    return snippet


def bm25_scores(query_tokens: list[str], corpus_tokens: dict[str, list[str]], candidates: set[str] | None = None) -> list[tuple[float, str]]:
    num_docs = len(corpus_tokens)
    avg_len = sum(len(doc_tokens) for doc_tokens in corpus_tokens.values()) / num_docs if num_docs else 0.0
    doc_freq: Counter[str] = Counter()
    for doc_tokens in corpus_tokens.values():
        doc_freq.update(set(doc_tokens))
    query = Counter(query_tokens)
    scored: list[tuple[float, str]] = []
    for doc_id in candidates if candidates is not None else set(corpus_tokens):
        doc_tokens = corpus_tokens.get(doc_id, [])
        tf = Counter(doc_tokens)
        doc_len = len(doc_tokens)
        score = 0.0
        for token, query_count in query.items():
            if tf[token] == 0:
                continue
            idf = math.log(1.0 + (num_docs - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
            denom = tf[token] + 1.5 * (1.0 - 0.75 + 0.75 * (doc_len / avg_len if avg_len else 0.0))
            score += query_count * idf * ((tf[token] * 2.5) / denom)
        scored.append((score, doc_id))
    return sorted(scored, key=lambda item: (-item[0], item[1]))


def read_edges(input_dir: Path, *, directed: bool) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = defaultdict(set)
    edge_path = input_dir / "graph_edges.csv"
    if not edge_path.exists():
        return edges
    with edge_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            source = row.get("source", "")
            target = row.get("target", "")
            if not source or not target:
                continue
            edges[source].add(target)
            if not directed:
                edges[target].add(source)
    return edges


def trace_action(actions: list[dict[str, Any]], action: str, *, budget_remaining: int, details: dict[str, Any]) -> None:
    actions.append({"step": len(actions) + 1, "action": action, "budget_remaining": budget_remaining, "details": details})


def run_question(question: dict[str, str], corpus: dict[str, str], corpus_tokens: dict[str, list[str]], edges: dict[str, set[str]], config: SearchConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    query_tokens = tokens(question["question"])
    ranked = bm25_scores(query_tokens, corpus_tokens)
    bm25_by_doc = {doc_id: score for score, doc_id in ranked}
    actions: list[dict[str, Any]] = []
    queue = [doc_id for _, doc_id in ranked[: config.search_top_k]]
    seen = set(queue)
    inspected: set[str] = set()
    context_doc_ids: list[str] = []
    trace_action(actions, "search", budget_remaining=config.max_actions - 1, details={"query": question["question"], "top_doc_ids": queue, "controller": "mock_provider_fixture"})
    while queue and len(actions) < config.max_actions - 1 and len(context_doc_ids) < config.final_top_k:
        if len(inspected) >= config.inspect_budget:
            break
        doc_id = queue.pop(0)
        if doc_id in inspected or doc_id not in corpus:
            continue
        inspected.add(doc_id)
        trace_action(actions, "inspect_page", budget_remaining=config.max_actions - len(actions) - 1, details={"doc_id": doc_id, "controller_decision": "inspect_highest_ranked_candidate"})
        if doc_id not in context_doc_ids:
            context_doc_ids.append(doc_id)
            trace_action(actions, "add_context", budget_remaining=config.max_actions - len(actions) - 1, details={"doc_id": doc_id, "reason": "mock_controller_selected_relevant_page"})
        neighbors = sorted(edges.get(doc_id, set()) & set(corpus))
        followed = [candidate for _, candidate in bm25_scores(query_tokens, corpus_tokens, set(neighbors)) if candidate not in seen][: config.follow_budget_per_page]
        if followed and len(actions) < config.max_actions - 1 and len(context_doc_ids) < config.final_top_k:
            trace_action(actions, "follow_link", budget_remaining=config.max_actions - len(actions) - 1, details={"from_doc_id": doc_id, "to_doc_ids": followed, "controller_decision": "follow_ranked_links"})
            queue.extend(followed)
            seen.update(followed)
    trace_action(actions, "stop", budget_remaining=max(0, config.max_actions - len(actions) - 1), details={"reason": "mock_controller_stop", "context_count": len(context_doc_ids)})
    context = [
        {"doc_id": doc_id, "text": answer_text(corpus[doc_id], max_chars=config.max_context_chars_per_doc), "score": float(bm25_by_doc.get(doc_id, 0.0)), "source": "llm_guided_search_mock", "selection_role": "mock_agent_context", "rank": rank}
        for rank, doc_id in enumerate(context_doc_ids, start=1)
    ]
    return (
        {"question_id": question["question_id"], "question": question["question"], "generated_answer": " ".join(item["text"] for item in context if item["text"]), "retrieved_context": context, "action_trace": actions},
        {"question_id": question["question_id"], "schema_version": TRACE_SCHEMA_VERSION, "policy": POLICY_NAME, "controller": {"type": "mock", "local_only": True, "allow_network": False}, "actions": actions, "final_context_doc_ids": context_doc_ids},
    )


def run(input_dir: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    config = parse_config(read_config(input_dir / "config.yaml"))
    questions = read_jsonl(input_dir / "questions.jsonl")
    corpus = {path.stem: path.read_text(encoding="utf-8") for path in (input_dir / "corpus").glob("*.md")}
    corpus_tokens = {doc_id: tokens(text) for doc_id, text in corpus.items()}
    edges = read_edges(input_dir, directed=config.directed)
    predictions = []
    traces = []
    for question in questions:
        prediction, trace = run_question(question, corpus, corpus_tokens, edges, config)
        predictions.append(prediction)
        traces.append(trace)
    with (output_dir / "predictions.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in predictions:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    with (output_dir / "action_traces.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in traces:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (output_dir / "metadata.json").write_text(
        json.dumps({"name": "rag_llm_guided_search_mock", "experiment_id": "E007b", "deterministic": True, "policy": POLICY_NAME, "trace_schema_version": TRACE_SCHEMA_VERSION, "uses_llm": False, "uses_llm_controller": "mock", "provider": "mock", "local_only": True, "allow_network": False}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    run(Path("/input"), Path("/output"))
