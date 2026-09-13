"""Bounded link-following navigator over one candidate corpus (S026, EXP-1c).

Promoted from ``rag_llm_guided_search_mock``: the same bounded action
controller and ``e007b.action_trace.v1`` trace shape, with the S026 action set
(``open(link)``, ``back``, ``answer(cited_pages, text)``), a real local model
behind Ollama's chat API, and one trace file per question.

Start state: the corpus root ``index.md`` plus the question. ``open`` follows
a link the current page carries (resolved through ``graph_edges.csv``, the
candidate's own directed link graph), ``back`` returns to the previous page,
``answer`` ends the navigation with cited pages and free text. Budgets:
``page_visit_budget`` opens and ``model_call_budget`` model calls per
question. Anything else is an invalid action and ends the trace.

Model reach follows the direct lane's relay rules inside this container: one
fixed upstream host that must resolve to a private address, the allowlisted
Ollama routes only (``GET /api/tags``, ``POST /api/chat``), and the model
digest observed before the run and after every call against the declared
policy. The container sees the corpus, the questions, and the link graph;
never answers, support labels, or evaluator records.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

TRACE_SCHEMA_VERSION = "e007b.action_trace.v1"
COMPATIBLE_ACTION_SCHEMA_VERSION = "e007a.action_trace.v1"
PROVIDER_POLICY_NAME = "bounded_provider_action_controller_v1"
PROVIDER_NAME = "rag_llm_guided_search"
ITEM_SOURCE = "llm_guided_navigation"
ANSWER_POLICY = "agent_answer_v1"
PROMPT_VERSION = "e007c_navigator_prompt_v1"
RESPONSE_SCHEMA_VERSION = "e007c.navigator_action.v1"
ACTIONS = ("open", "back", "answer")
TERMINALS = ("answer", "budget_exhausted", "invalid_action", "provider_error")
ALLOWED_REQUESTS = {("GET", "/api/tags"), ("POST", "/api/chat")}
START_DOCUMENT = "index.md"
DIGEST_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")


class NavigatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelPolicy:
    backend: str
    model: str
    digest: str
    temperature: float
    top_p: float
    seed: int
    context_window_tokens: int
    max_output_tokens: int
    timeout_seconds: float
    max_attempts: int
    endpoint: str
    thinking: bool

    @property
    def bare_digest(self) -> str:
        return self.digest.removeprefix("sha256:")


@dataclass(frozen=True)
class NavigatorConfig:
    top_k: int
    max_context_chars_per_doc: int
    answer_policy: str
    page_visit_budget: int
    model_call_budget: int
    page_char_limit: int
    model_policy: ModelPolicy


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_model_policy(raw: Any) -> ModelPolicy:
    if not isinstance(raw, dict):
        raise NavigatorError("model_policy_missing")
    if raw.get("backend") != "ollama":
        raise NavigatorError("model_policy_backend")
    digest = str(raw.get("digest", ""))
    if DIGEST_RE.fullmatch(digest) is None:
        raise NavigatorError("model_policy_digest")
    policy = ModelPolicy(
        backend="ollama", model=str(raw["model"]), digest=digest,
        temperature=float(raw["temperature"]), top_p=float(raw["top_p"]), seed=int(raw["seed"]),
        context_window_tokens=int(raw["context_window_tokens"]),
        max_output_tokens=int(raw["max_output_tokens"]),
        timeout_seconds=float(raw["timeout_seconds"]), max_attempts=int(raw["max_attempts"]),
        endpoint=str(raw["endpoint"]), thinking=bool(raw.get("thinking", False)))
    if policy.max_attempts != 1:
        raise NavigatorError("model_policy_max_attempts")
    parsed = urlparse(policy.endpoint)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in ("", "/"):
        raise NavigatorError("model_policy_endpoint")
    return policy


def parse_config(raw: dict[str, Any]) -> NavigatorConfig:
    if raw.get("answer_policy", ANSWER_POLICY) != ANSWER_POLICY:
        raise NavigatorError("answer_policy_unsupported")
    return NavigatorConfig(
        top_k=max(1, int(raw.get("top_k", 5))),
        max_context_chars_per_doc=max(0, int(raw.get("max_context_chars_per_doc", 1500))),
        answer_policy=ANSWER_POLICY,
        page_visit_budget=max(1, int(raw.get("page_visit_budget", 8))),
        model_call_budget=max(1, int(raw.get("model_call_budget", 12))),
        page_char_limit=max(1, int(raw.get("page_char_limit", 12000))),
        model_policy=parse_model_policy(raw.get("model_policy")))


def answer_text(text: str, *, max_chars: int) -> str:
    """Head-of-document snippet, byte-identical to the lexical runner's."""
    lines = [line.strip("# ").strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    snippet = " ".join(lines)
    if max_chars > 0 and len(snippet) > max_chars:
        return snippet[:max_chars].rsplit(" ", 1)[0].strip()
    return snippet


def read_edges(path: Path) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = defaultdict(set)
    if not path.exists():
        return edges
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            source, target = row.get("source", ""), row.get("target", "")
            if source and target:
                edges[source].add(target)
    return edges


def start_doc_id() -> str:
    suffix = hashlib.sha1(START_DOCUMENT.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"index__{suffix}"


def page_view(doc_id: str, text: str, *, limit: int) -> dict[str, Any]:
    truncated = len(text) > limit
    return {"doc_id": doc_id, "text": text[:limit], "truncated": truncated, "char_count": len(text)}


# --- model client (relay rules) -------------------------------------------------

def resolve_private(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError as error:
        raise NavigatorError(f"upstream_resolution_failed:{host}:{error}") from error
    addresses: set[str] = set()
    for info in infos:
        raw = info[4][0].split("%", maxsplit=1)[0]
        address = ipaddress.ip_address(raw)
        if address.is_unspecified or address.is_multicast or address.is_loopback or address.is_global:
            raise NavigatorError(f"upstream_address_prohibited:{address}")
        addresses.add(address.compressed)
    if not addresses:
        raise NavigatorError("upstream_resolution_empty")
    return tuple(sorted(addresses))


class ModelClient:
    def __init__(self, policy: ModelPolicy):
        self.policy = policy
        parsed = urlparse(policy.endpoint)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 11434
        self.upstream_addresses = resolve_private(self.host, self.port)
        self.base = f"http://{self.host}:{self.port}"

    def _request(self, method: str, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if (method, route) not in ALLOWED_REQUESTS:
            raise NavigatorError(f"route_not_allowlisted:{method} {route}")
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + route, data=payload, method=method,
                                         headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.policy.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout,
                http.client.HTTPException) as error:
            raise NavigatorError(f"provider_request_failed:{method} {route}:{error}") from error
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise NavigatorError("provider_response_not_object")
        return value

    def observe_identity(self) -> str:
        tags = self._request("GET", "/api/tags")
        for model in tags.get("models", []):
            if isinstance(model, dict) and model.get("name") == self.policy.model:
                digest = str(model.get("digest", ""))
                if digest != self.policy.bare_digest:
                    raise NavigatorError(f"model_digest_mismatch:{digest}")
                return digest
        raise NavigatorError(f"model_not_present:{self.policy.model}")

    def chat(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {
            "model": self.policy.model, "messages": messages, "stream": False,
            "think": self.policy.thinking, "format": schema,
            "options": {"temperature": self.policy.temperature, "top_p": self.policy.top_p,
                        "seed": self.policy.seed, "num_ctx": self.policy.context_window_tokens,
                        "num_predict": self.policy.max_output_tokens},
        }
        started = time.monotonic()
        response = self._request("POST", "/api/chat", body)
        latency_ms = (time.monotonic() - started) * 1000.0
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise NavigatorError("provider_response_missing_content")
        usage = {"input_tokens": response.get("prompt_eval_count"), "output_tokens": response.get("eval_count"),
                 "done_reason": response.get("done_reason"), "latency_ms": round(latency_ms, 1)}
        try:
            parsed = json.loads(content.strip())
        except json.JSONDecodeError as error:
            raise NavigatorError(f"provider_response_not_json:{content[:200]!r}") from error
        return parsed, usage


# --- prompt and action schema -----------------------------------------------------

def action_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["action"],
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "link": {"type": "string"},
            "cited_pages": {"type": "array", "items": {"type": "string"}},
            "text": {"type": "string"},
            "reason": {"type": "string"},
        },
    }


SYSTEM_PROMPT = (
    "You navigate a documentation site to answer one question, the way a developer reads docs. "
    "You see one page at a time: its text and the links it carries (as page ids). "
    "Return exactly one JSON object and nothing else.\n"
    "Actions:\n"
    '- {"action":"open","link":"<page id>"}: open a link that is listed on the current page.\n'
    '- {"action":"back"}: return to the previous page (only when history depth is greater than 1).\n'
    '- {"action":"answer","cited_pages":["<page id>", ...],"text":"<answer>"}: finish; cite only pages you have visited; '
    "write the answer in at most 120 words from what the visited pages say.\n"
    "Rules: any action outside this list, a link not on the current page, back at the root page, or a citation of an "
    "unvisited page ends the navigation as invalid. Opens and model calls are budgeted; when opens_remaining is 0 you "
    "must answer or go back. The root index page is a table of contents: do not answer from it unless it literally "
    "states the answer; open the most promising link instead. Never answer that the documentation does not cover the "
    "question while opens remain; keep navigating (open, or back and open another link) until a visited page answers "
    "it or the budget is spent. Use the page ids' path words to guess where the answer lives. Never claim to have "
    "used reference answers, labels, or hidden knowledge."
)


def build_messages(observation: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Observation (JSON):\n" + json.dumps(observation, sort_keys=True, ensure_ascii=False)},
    ]


def build_observation(question: dict[str, str], page: dict[str, Any], links: list[str], history: list[str],
                      *, opens_remaining: int, calls_remaining: int) -> dict[str, Any]:
    allowed = ["answer"]
    if opens_remaining > 0 and links:
        allowed.insert(0, "open")
    if len(history) > 1:
        allowed.append("back")
    return {
        "question_id": question["question_id"],
        "question": question["question"],
        "current_page": page,
        "links": links,
        "history": list(history),
        "visited_pages": sorted(set(history)),
        "budget": {"opens_remaining": opens_remaining, "calls_remaining": calls_remaining},
        "allowed_actions": allowed,
    }


# --- controller -----------------------------------------------------------------

def trace_action(actions: list[dict[str, Any]], action: str, *, budget_remaining: int,
                 details: dict[str, Any]) -> None:
    actions.append({"step": len(actions) + 1, "action": action, "budget_remaining": budget_remaining,
                    "details": details})


def navigate(question: dict[str, str], corpus: dict[str, str], edges: dict[str, set[str]],
             config: NavigatorConfig, client: ModelClient, start: str) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = config.model_policy
    stack = [start]
    visits: list[str] = [start]
    opens = 0
    calls = 0
    dead_ends = 0
    actions: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []
    terminal: dict[str, Any] | None = None
    answer: dict[str, Any] = {"cited_pages": [], "text": ""}

    def admissible(doc_id: str) -> list[str]:
        return sorted(edges.get(doc_id, set()) & set(corpus))

    while terminal is None:
        if calls >= config.model_call_budget:
            terminal = {"reason": "budget_exhausted", "exhausted": "model_calls"}
            break
        current = stack[-1]
        links = admissible(current)
        observation = build_observation(
            question, page_view(current, corpus[current], limit=config.page_char_limit), links, stack,
            opens_remaining=config.page_visit_budget - opens,
            calls_remaining=config.model_call_budget - calls)
        call_index = calls + 1
        try:
            requested, usage = client.chat(build_messages(observation), action_schema())
            digest = client.observe_identity()
        except NavigatorError as error:
            calls += 1
            provider_calls.append({"request_index": call_index, "current_page": current, "error": str(error)})
            terminal = {"reason": "provider_error", "error": str(error)}
            break
        calls += 1
        provider_calls.append({"request_index": call_index, "current_page": current, "links_offered": len(links),
                               "requested_action": requested, "usage": usage, "model_digest": digest,
                               "prompt_version": PROMPT_VERSION})
        remaining = config.model_call_budget - calls
        if not isinstance(requested, dict) or requested.get("action") not in ACTIONS:
            terminal = {"reason": "invalid_action", "validation_error": "unsupported_action", "requested": requested}
            break
        name = requested["action"]
        if name == "open":
            link = requested.get("link")
            if opens >= config.page_visit_budget:
                terminal = {"reason": "budget_exhausted", "exhausted": "page_visits", "requested": requested}
                break
            if not isinstance(link, str) or link not in links:
                terminal = {"reason": "invalid_action", "validation_error": "link_not_on_page", "requested": requested}
                break
            opens += 1
            stack.append(link)
            visits.append(link)
            dead_end = not admissible(link)
            dead_ends += int(dead_end)
            trace_action(actions, "open", budget_remaining=remaining,
                         details={"from_doc_id": current, "doc_id": link, "open_index": opens,
                                  "dead_end": dead_end, "revisit": visits.count(link) > 1})
        elif name == "back":
            if len(stack) < 2:
                terminal = {"reason": "invalid_action", "validation_error": "back_at_root", "requested": requested}
                break
            stack.pop()
            trace_action(actions, "back", budget_remaining=remaining,
                         details={"from_doc_id": current, "doc_id": stack[-1]})
        else:
            cited = requested.get("cited_pages", [])
            text = requested.get("text", "")
            visited = set(visits)
            if (not isinstance(cited, list) or any(not isinstance(c, str) or c not in visited for c in cited)
                    or not isinstance(text, str)):
                terminal = {"reason": "invalid_action", "validation_error": "answer_cites_unvisited_page",
                            "requested": requested}
                break
            cited = list(dict.fromkeys(cited))
            answer = {"cited_pages": cited, "text": text}
            trace_action(actions, "answer", budget_remaining=remaining,
                         details={"cited_pages": cited, "text": text})
            terminal = {"reason": "answer"}
    trace_action(actions, "stop", budget_remaining=max(0, config.model_call_budget - calls),
                 details={"terminal": terminal})
    context_ids = answer["cited_pages"][: config.top_k]
    context = [{"doc_id": doc_id, "text": answer_text(corpus[doc_id], max_chars=config.max_context_chars_per_doc),
                "score": float(len(context_ids) - index), "source": ITEM_SOURCE, "rank": index + 1}
               for index, doc_id in enumerate(context_ids)]
    prediction = {"question_id": question["question_id"], "question": question["question"],
                  "generated_answer": answer["text"], "retrieved_context": context,
                  "answer_policy": ANSWER_POLICY, "cited_pages": answer["cited_pages"]}
    trace = {
        "question_id": question["question_id"],
        "schema_version": TRACE_SCHEMA_VERSION,
        "compatible_action_schema_version": COMPATIBLE_ACTION_SCHEMA_VERSION,
        "policy": PROVIDER_POLICY_NAME,
        "controller": {"type": "provider_action", "provider": policy.backend, "model_id": policy.model,
                       "model_digest": policy.digest, "prompt_version": PROMPT_VERSION,
                       "response_schema_version": RESPONSE_SCHEMA_VERSION,
                       "temperature": policy.temperature, "top_p": policy.top_p, "seed": policy.seed,
                       "max_tokens": policy.max_output_tokens, "thinking": policy.thinking,
                       "page_visit_budget": config.page_visit_budget,
                       "model_call_budget": config.model_call_budget, "page_char_limit": config.page_char_limit},
        "start_doc_id": start,
        "actions": actions,
        "provider_calls": provider_calls,
        "visits": visits,
        "opens": opens,
        "model_calls": calls,
        "dead_ends": dead_ends,
        "terminal": terminal,
        "final_context_doc_ids": context_ids,
        "answer": answer,
        "paper_facing_evidence": False,
    }
    return prediction, trace


def write_trace(path: Path, trace: dict[str, Any]) -> None:
    """One JSONL file per question: a header line, one line per action, one terminal line."""
    header = {k: trace[k] for k in ("question_id", "schema_version", "compatible_action_schema_version", "policy",
                                    "controller", "start_doc_id")}
    header["record"] = "header"
    rows = [header]
    for action in trace["actions"]:
        rows.append({"record": "action", **action})
    rows.append({"record": "terminal", "terminal": trace["terminal"], "visits": trace["visits"],
                 "opens": trace["opens"], "model_calls": trace["model_calls"], "dead_ends": trace["dead_ends"],
                 "answer": trace["answer"], "final_context_doc_ids": trace["final_context_doc_ids"],
                 "provider_calls": trace["provider_calls"], "paper_facing_evidence": False})
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def metadata(config: NavigatorConfig) -> dict[str, Any]:
    policy = config.model_policy
    return {
        "name": PROVIDER_NAME, "deterministic": False, "top_k": config.top_k,
        "max_context_chars_per_doc": config.max_context_chars_per_doc, "answer_policy": ANSWER_POLICY,
        "page_visit_budget": config.page_visit_budget, "model_call_budget": config.model_call_budget,
        "page_char_limit": config.page_char_limit,
        "model_policy": {"backend": policy.backend, "model": policy.model, "digest": policy.digest,
                         "temperature": policy.temperature, "top_p": policy.top_p, "seed": policy.seed,
                         "context_window_tokens": policy.context_window_tokens,
                         "max_output_tokens": policy.max_output_tokens,
                         "timeout_seconds": policy.timeout_seconds, "max_attempts": policy.max_attempts,
                         "endpoint": policy.endpoint, "thinking": policy.thinking},
        "trace_schema_version": TRACE_SCHEMA_VERSION, "provider_policy": PROVIDER_POLICY_NAME,
        "prompt_version": PROMPT_VERSION, "start_document": START_DOCUMENT,
    }


def run(input_dir: Path, output_dir: Path, *, client_factory=ModelClient) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    config = parse_config(read_config(input_dir / "config.yaml"))
    questions = read_jsonl(input_dir / "questions.jsonl")
    corpus = {path.stem: path.read_text(encoding="utf-8") for path in sorted((input_dir / "corpus").glob("*.md"))}
    edges = read_edges(input_dir / "graph_edges.csv")
    start = start_doc_id()
    summary: dict[str, Any] = {"schema_version": "retrieval_arena.navigation_run.v1", "provider": PROVIDER_NAME,
                               "start_doc_id": start, "question_count": len(questions),
                               "upstream_host": urlparse(config.model_policy.endpoint).hostname}
    if start not in corpus:
        summary.update({"status": "unavailable", "reason": "start_page_missing"})
        (output_dir / "navigation.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 4
    client = client_factory(config.model_policy)
    summary["upstream_addresses"] = list(client.upstream_addresses)
    summary["model_digest_before"] = client.observe_identity()
    traces_dir = output_dir / "traces"
    traces_dir.mkdir()
    predictions = []
    terminals: dict[str, int] = {name: 0 for name in TERMINALS}
    total_calls = total_opens = 0
    started = time.monotonic()
    for question in questions:
        prediction, trace = navigate(question, corpus, edges, config, client, start)
        predictions.append(prediction)
        write_trace(traces_dir / f"{question['question_id']}.jsonl", trace)
        terminals[trace["terminal"]["reason"]] += 1
        total_calls += trace["model_calls"]
        total_opens += trace["opens"]
    summary["model_digest_after"] = client.observe_identity()
    summary.update({"status": "completed", "terminals": terminals, "model_calls": total_calls,
                    "page_opens": total_opens, "wall_seconds": round(time.monotonic() - started, 1)})
    with (output_dir / "predictions.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in predictions:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    (output_dir / "metadata.json").write_text(json.dumps(metadata(config), indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    (output_dir / "navigation.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the S026 bounded documentation navigator.")
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--output-dir", default="/output")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(run(Path(arguments.input_dir), Path(arguments.output_dir)))
