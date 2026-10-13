"""Bounded repo-search agent over one candidate corpus (S046, EXP-1e).

Promoted from ``rag_llm_guided_search`` (the bounded action controller, the
relay-rule model client, the ``e007b`` trace shape) and ``rag_iterative_search``
(a search loop over the corpus). The reader is an agent with the repository in
front of it: it lands by a search over the corpus's
Markdown, opens a hit, reads it, follows a relative link (``follow_links``
variant) or greps again with words it learned from the page, and answers.

Start state: the corpus plus the question; the trace opens with an automatic
landing search (no model call), so the reader lands deterministically. The
landing is the request's ``landing_policy`` (S046b): ``search_question_text``
greps the whole question literally (E-EXP1e); ``tokenised_content_words_v1``
splits the question into content words (``[a-z0-9]+`` runs of at least two
characters, ``STOP_WORDS_EN_V1`` removed) and ranks files by how many of
those words they carry as whole tokens -- a developer whose whole-question
grep finds nothing greps its words. Actions: ``search(query)`` (a literal, case-insensitive
line grep; several words match a line carrying every word), ``open(doc_id)``
(a hit of any search so far, or, in the links variant, an admissible outlink
of the current page resolved through ``graph_edges.csv``), ``back``, and
``answer(cited_pages, text)`` with ``cited_pages`` a subset of the visited
pages. Budgets per question: ``search_budget`` searches (the landing counts
as the first), ``page_visit_budget`` opens, ``model_call_budget`` calls.
Anything else is an invalid action and ends the trace.

The search tool reads the corpus directory only: a query is text matched
against page lines, never a path; ``support.json``, ``answers.json``, the
evaluator roots and the network are outside the container. Model reach is
the navigator's: one fixed private upstream, ``GET /api/tags`` and
``POST /api/chat`` only, the digest observed before the run and after every
call.
"""

from __future__ import annotations

import argparse
import csv
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

TRACE_SCHEMA_VERSION = "e007c.action_trace.v1"
COMPATIBLE_ACTION_SCHEMA_VERSION = "e007b.action_trace.v1"
PROVIDER_POLICY_NAME = "bounded_repo_search_controller_v1"
PROVIDER_NAME = "rag_repo_search_agent"
PROVIDER_NAME_NOLINKS = "rag_repo_search_agent_nolinks"
ITEM_SOURCE = "repo_search_navigation"
ANSWER_POLICY = "agent_answer_v1"
RESPONSE_SCHEMA_VERSION = "e007d.repo_search_action.v1"
# S046b: the landing policies (closed). The prompt version follows the policy:
# v1 verbatim under the literal landing, v1 plus one sentence under the
# tokenised one.
LANDING_QUESTION_TEXT = "search_question_text"
LANDING_TOKENISED = "tokenised_content_words_v1"
LANDING_POLICIES = (LANDING_QUESTION_TEXT, LANDING_TOKENISED)
PROMPT_VERSIONS = {LANDING_QUESTION_TEXT: "e007d_repo_search_prompt_v1", LANDING_TOKENISED: "e007d_repo_search_prompt_v2"}
STOP_WORDS_NAME = "STOP_WORDS_EN_V1"
# The fixed stop-word list of the tokenised landing; adding or removing a word is a deviation.
STOP_WORDS_EN_V1 = (
    "a an the and or but if of to in on at by for from with without into onto over under about as is are was "
    "were be been being am do does did doing have has had having can could should would may might must will "
    "shall i you he she it we they me him her us them my your his its our their this that these those what "
    "which who whom whose when where why how not no yes any some all each every there here than then so too "
    "very just also only up down out off again further once more most other such own same").split()
TOKEN_RE = re.compile(r"[a-z0-9]+")
MIN_TOKEN_CHARS = 2
ACTIONS = ("search", "open", "back", "answer")
ROUTES = ("landing", "search", "link")
TERMINALS = ("answer", "budget_exhausted", "invalid_action", "provider_error")
# S057: the one line a recovered refusal adds to the next observation (``last_action_feedback``).
RECOVERY_FEEDBACK = ("That page is already open or was not offered. Answer from the open page, search, "
                     "open a page listed as offered, or go back.")
ALLOWED_REQUESTS = {("GET", "/api/tags"), ("POST", "/api/chat")}
DIGEST_RE = re.compile(r"^(sha256:)?[0-9a-f]{64}$")
HIT_TEXT_CHARS = 200
QUERY_CHAR_LIMIT = 200


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
class ReaderConfig:
    top_k: int
    max_context_chars_per_doc: int
    answer_policy: str
    search_budget: int
    search_hit_files: int
    search_hit_lines_per_file: int
    page_visit_budget: int
    model_call_budget: int
    page_char_limit: int
    follow_links: bool
    landing_policy: str
    model_policy: ModelPolicy
    # S057: None when the request does not declare it (today's controller); 0 or 2 when declared.
    invalid_action_recovery: int | None = None

    @property
    def recoveries_allowed(self) -> int:
        return self.invalid_action_recovery or 0

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME if self.follow_links else PROVIDER_NAME_NOLINKS

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSIONS[self.landing_policy]

    @property
    def tokenised_landing(self) -> bool:
        return self.landing_policy == LANDING_TOKENISED


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


def _recovery(raw: dict[str, Any]) -> int | None:
    """S057: absent -> None (today's controller); declared -> 0 or 2, nothing else."""
    if "invalid_action_recovery" not in raw:
        return None
    value = raw["invalid_action_recovery"]
    if isinstance(value, bool) or value not in (0, 2):
        raise NavigatorError("invalid_action_recovery")
    return int(value)


def parse_config(raw: dict[str, Any]) -> ReaderConfig:
    if raw.get("answer_policy", ANSWER_POLICY) != ANSWER_POLICY:
        raise NavigatorError("answer_policy_unsupported")
    if not isinstance(raw.get("follow_links"), bool):
        raise NavigatorError("follow_links_missing")
    if "landing_policy" not in raw:
        raise NavigatorError("landing_policy_missing")
    if raw["landing_policy"] not in LANDING_POLICIES:
        raise NavigatorError("landing_policy_unknown")
    return ReaderConfig(
        top_k=max(1, int(raw.get("top_k", 5))),
        max_context_chars_per_doc=max(0, int(raw.get("max_context_chars_per_doc", 1500))),
        answer_policy=ANSWER_POLICY,
        search_budget=max(1, int(raw.get("search_budget", 3))),
        search_hit_files=max(1, int(raw.get("search_hit_files", 10))),
        search_hit_lines_per_file=max(1, int(raw.get("search_hit_lines_per_file", 3))),
        page_visit_budget=max(1, int(raw.get("page_visit_budget", 8))),
        model_call_budget=max(1, int(raw.get("model_call_budget", 12))),
        page_char_limit=max(1, int(raw.get("page_char_limit", 12000))),
        follow_links=raw["follow_links"],
        landing_policy=raw["landing_policy"],
        model_policy=parse_model_policy(raw.get("model_policy")),
        invalid_action_recovery=_recovery(raw))


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


def page_view(doc_id: str, text: str, *, limit: int) -> dict[str, Any]:
    truncated = len(text) > limit
    return {"doc_id": doc_id, "text": text[:limit], "truncated": truncated, "char_count": len(text)}


# --- the search tool ---------------------------------------------------------------

def query_words(query: str) -> list[str]:
    """The literal, lower-cased, whitespace-separated words of a query (at most QUERY_CHAR_LIMIT chars)."""
    return [word for word in query[:QUERY_CHAR_LIMIT].lower().split() if word]


def search_corpus(corpus: dict[str, str], query: str, *, hit_files: int, lines_per_file: int) -> list[dict[str, Any]]:
    """Literal, case-insensitive line grep over the in-memory corpus.

    A line matches when it contains every word of the query as a substring
    (no regex, no path semantics: ``../support.json`` is four characters of
    text). Files are ordered by matching-line count descending, then by
    document id; at most ``hit_files`` files, each with its first
    ``lines_per_file`` matching lines in file order. A query without words
    or without hits returns an empty list.
    """
    words = query_words(query)
    if not words:
        return []
    matches: list[tuple[int, str, list[dict[str, Any]]]] = []
    for doc_id in sorted(corpus):
        count = 0
        lines: list[dict[str, Any]] = []
        for number, line in enumerate(corpus[doc_id].splitlines(), 1):
            lowered = line.lower()
            if all(word in lowered for word in words):
                count += 1
                if len(lines) < lines_per_file:
                    lines.append({"line_number": number, "text": line.strip()[:HIT_TEXT_CHARS]})
        if count:
            matches.append((count, doc_id, lines))
    matches.sort(key=lambda row: (-row[0], row[1]))
    hits = []
    for count, doc_id, lines in matches[:hit_files]:
        for line in lines:
            hits.append({"doc_id": doc_id, "path": f"{doc_id}.md", "line_number": line["line_number"],
                         "text": line["text"], "matching_lines": count})
    return hits


# --- the tokenised landing (S046b, landing_policy tokenised_content_words_v1) ------

def tokens(text: str) -> list[str]:
    """The ``[a-z0-9]+`` runs of the lower-cased text (no stemming, no substrings)."""
    return TOKEN_RE.findall(text.lower())


def landing_terms(question: str) -> list[str]:
    """The question's content words: its first QUERY_CHAR_LIMIT characters tokenised, tokens shorter
    than MIN_TOKEN_CHARS and STOP_WORDS_EN_V1 dropped, duplicates removed, order kept."""
    stop = set(STOP_WORDS_EN_V1)
    return list(dict.fromkeys(t for t in tokens(question[:QUERY_CHAR_LIMIT]) if len(t) >= MIN_TOKEN_CHARS and t not in stop))


def landing_search(corpus: dict[str, str], terms: list[str], *, hit_files: int, lines_per_file: int) -> list[dict[str, Any]]:
    """Rank corpus files by the landing terms they carry as whole tokens.

    A file's matched set is the landing terms occurring as whole tokens anywhere
    in it. Files are ranked by matched-set size descending, then by the largest
    number of landing terms on one line descending, then by document id; at most
    ``hit_files`` files are offered, each with at most ``lines_per_file`` lines
    chosen by landing terms on the line descending then line number ascending.
    Every hit carries the file's matched set (``matched_words``, in landing-term
    order) and its size. No landing terms, or no file carrying any of them, is a
    zero-hit landing (``[]``). Reads the corpus only: a term is a token, never a
    path or a pattern.
    """
    if not terms:
        return []
    wanted = set(terms)
    ranked: list[tuple[int, int, str, list[str], list[dict[str, Any]]]] = []
    for doc_id in sorted(corpus):
        matched: set[str] = set()
        lines: list[tuple[int, int, str]] = []
        for number, line in enumerate(corpus[doc_id].splitlines(), 1):
            on_line = wanted & set(tokens(line))
            if on_line:
                matched |= on_line
                lines.append((-len(on_line), number, line.strip()[:HIT_TEXT_CHARS]))
        if matched:
            lines.sort()
            best = -lines[0][0]
            ranked.append((-len(matched), -best, doc_id, [t for t in terms if t in matched],
                           [{"line_number": n, "text": text} for _, n, text in lines[:lines_per_file]]))
    ranked.sort(key=lambda row: row[:3])
    hits = []
    for _, _, doc_id, words, lines in ranked[:hit_files]:
        for line in lines:
            hits.append({"doc_id": doc_id, "path": f"{doc_id}.md", "line_number": line["line_number"],
                         "text": line["text"], "matched_words": words, "matched_word_count": len(words)})
    return hits


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

def action_schema(allowed: list[str] | None = None) -> dict[str, Any]:
    """The structured-output schema of one call; ``action`` is limited to the actions the observation allows."""
    return {
        "type": "object",
        "required": ["action"],
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": list(allowed) if allowed else list(ACTIONS)},
            "query": {"type": "string"},
            "doc_id": {"type": "string"},
            "cited_pages": {"type": "array", "items": {"type": "string"}},
            "text": {"type": "string"},
            "reason": {"type": "string"},
        },
    }


SYSTEM_PROMPT = (
    "You answer one question from a documentation repository the way a developer with the repository checked out "
    "does: you grep it, open the most promising file, read it, and grep again with better words or follow a link. "
    "Return exactly one JSON object and nothing else.\n"
    "Actions:\n"
    '- {"action":"search","query":"<words>"}: grep the docs for lines containing every word of the query '
    "(literal, case-insensitive; no regex, no paths). You see up to 10 files with up to 3 matching lines each. "
    "Choose specific words that the answer page would use; a search with no hits still costs a search.\n"
    '- {"action":"open","doc_id":"<page id>"}: open a page listed among the search hits'
    "<LINK_CLAUSE>.\n"
    '- {"action":"back"}: return to the previously opened page (only when history depth is greater than 1).\n'
    '- {"action":"answer","cited_pages":["<page id>", ...],"text":"<answer>"}: finish; cite only pages you have '
    "opened; write the answer in at most 120 words from what the opened pages say.\n"
    "Rules: any action outside this list, a page id not offered, back at the first page, or a citation of an "
    "unopened page ends the run as invalid. Searches, opens and calls are budgeted (see budget); when a budget "
    "is 0 that action is not offered. Search hits show one line per match; open a page before answering from "
    "it. Never answer that the documentation does not cover the question while opens or searches remain; keep "
    "reading (open a hit, search with other words<FOLLOW_CLAUSE>) until an opened page answers it or the budget "
    "is spent. Use the page ids' path words and the hit lines to judge where the answer lives. Never claim to "
    "have used reference answers, labels, or hidden knowledge."
)
# S046b (prompt v2): the v1 text plus this one sentence, present only under the tokenised landing.
TOKENISED_LANDING_SENTENCE = (
    " The first search was made for you from the question's content words (landing_terms in the observation): its "
    "hits are the files carrying the most of those words, and a hit line need not contain the whole question."
)


def system_prompt(follow_links: bool, tokenised_landing: bool = False) -> str:
    return (SYSTEM_PROMPT
            .replace("<LINK_CLAUSE>", " or among the links of the current page" if follow_links else "")
            .replace("<FOLLOW_CLAUSE>", ", follow a link" if follow_links else "")
            + (TOKENISED_LANDING_SENTENCE if tokenised_landing else ""))


def build_messages(observation: dict[str, Any], *, follow_links: bool, tokenised_landing: bool = False) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt(follow_links, tokenised_landing)},
        {"role": "user", "content": "Observation (JSON):\n" + json.dumps(observation, sort_keys=True, ensure_ascii=False)},
    ]


def build_observation(question: dict[str, str], *, page: dict[str, Any] | None, links: list[str],
                      searches: list[dict[str, Any]], offered_hits: list[str], history: list[str],
                      searches_remaining: int, opens_remaining: int, calls_remaining: int,
                      follow_links: bool, landing_terms: list[str] | None = None,
                      feedback: str | None = None) -> dict[str, Any]:
    # The page already open is not offered again (re-opening it reads nothing new;
    # the probe showed a six-fold re-open loop on one page); earlier pages stay openable.
    current = page["doc_id"] if page else None
    openable = sorted((set(offered_hits) | (set(links) if follow_links else set())) - {current})
    allowed = []
    if opens_remaining > 0 and openable:
        allowed.append("open")
    if searches_remaining > 0:
        allowed.append("search")
    allowed.append("answer")
    if len(history) > 1:
        allowed.append("back")
    # The latest search shows its hit lines; earlier searches keep their query
    # and the ids they offered (which stay openable), so three searches plus a
    # 12 000-character page fit the 8 192-token context.
    shown = [{"search_index": s["search_index"], "query": s["query"],
              "doc_ids": list(dict.fromkeys(h["doc_id"] for h in s["hits"]))} for s in searches[:-1]]
    if searches:
        shown.append(searches[-1])
    observation = {
        "question_id": question["question_id"],
        "question": question["question"],
        "searches": shown,
        "current_page": page,
        "history": list(history),
        "visited_pages": sorted(set(history)),
        "openable_pages": openable,
        "budget": {"searches_remaining": searches_remaining, "opens_remaining": opens_remaining,
                   "calls_remaining": calls_remaining},
        "allowed_actions": allowed,
    }
    if follow_links:
        observation["links"] = links
    if landing_terms is not None:
        observation["landing_terms"] = list(landing_terms)
    if feedback is not None:
        # S057: only in the observation right after a recovered refusal; absent otherwise, so a run
        # that is never refused sends the model exactly today's observation.
        observation["last_action_feedback"] = feedback
    return observation


# --- controller -----------------------------------------------------------------

def trace_action(actions: list[dict[str, Any]], action: str, *, budget_remaining: int,
                 details: dict[str, Any]) -> None:
    actions.append({"step": len(actions) + 1, "action": action, "budget_remaining": budget_remaining,
                    "details": details})


def navigate(question: dict[str, str], corpus: dict[str, str], edges: dict[str, set[str]],
             config: ReaderConfig, client: ModelClient) -> tuple[dict[str, Any], dict[str, Any]]:
    policy = config.model_policy
    stack: list[str] = []
    visits: list[str] = []
    opens = 0
    calls = 0
    searches = 0
    dead_ends = 0
    zero_hit_searches = 0
    actions: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []
    search_views: list[dict[str, Any]] = []
    # Every id a search has offered so far, with the index (1 = landing) of the
    # first search that offered it: the route of an open is decided here.
    offered_by_search: dict[str, int] = {}
    terminal: dict[str, Any] | None = None
    answer: dict[str, Any] = {"cited_pages": [], "text": ""}
    recovered_steps: list[int] = []
    feedback: str | None = None

    def admissible(doc_id: str) -> list[str]:
        return sorted(edges.get(doc_id, set()) & set(corpus))

    # S046b: the landing is the request's policy; the model's own searches are
    # the literal all-words line grep under either policy.
    terms: list[str] | None = landing_terms(question["question"]) if config.tokenised_landing else None

    def run_search(query: str, *, landing: bool) -> None:
        nonlocal searches, zero_hit_searches
        searches += 1
        if landing and terms is not None:
            hits = landing_search(corpus, terms, hit_files=config.search_hit_files,
                                  lines_per_file=config.search_hit_lines_per_file)
        else:
            hits = search_corpus(corpus, query, hit_files=config.search_hit_files,
                                 lines_per_file=config.search_hit_lines_per_file)
        doc_ids = list(dict.fromkeys(hit["doc_id"] for hit in hits))
        for doc_id in doc_ids:
            offered_by_search.setdefault(doc_id, searches)
        if not doc_ids:
            zero_hit_searches += 1
        view = {"search_index": searches, "query": query, "hits": hits}
        details = {"search_index": searches, "query": query, "landing": landing,
                   "doc_ids": doc_ids, "hit_count": len(hits),
                   "searches_remaining": config.search_budget - searches}
        if landing and terms is not None:
            view["landing_terms"] = list(terms)
            details["landing_terms"] = list(terms)
        search_views.append(view)
        trace_action(actions, "search", budget_remaining=config.model_call_budget - calls, details=details)

    run_search(question["question"], landing=True)

    while terminal is None:
        if calls >= config.model_call_budget:
            terminal = {"reason": "budget_exhausted", "exhausted": "model_calls"}
            break
        current = stack[-1] if stack else None
        links = admissible(current) if (current is not None and config.follow_links) else []
        page = page_view(current, corpus[current], limit=config.page_char_limit) if current else None
        observation = build_observation(
            question, page=page,
            links=links, searches=search_views, offered_hits=list(offered_by_search), history=stack,
            searches_remaining=config.search_budget - searches, opens_remaining=config.page_visit_budget - opens,
            calls_remaining=config.model_call_budget - calls, follow_links=config.follow_links, landing_terms=terms,
            feedback=feedback)
        feedback = None
        call_index = calls + 1
        try:
            requested, usage = client.chat(build_messages(observation, follow_links=config.follow_links,
                                                          tokenised_landing=config.tokenised_landing),
                                           action_schema(observation["allowed_actions"]))
            digest = client.observe_identity()
        except NavigatorError as error:
            calls += 1
            provider_calls.append({"request_index": call_index, "current_page": current, "error": str(error)})
            terminal = {"reason": "provider_error", "error": str(error)}
            break
        calls += 1
        provider_calls.append({"request_index": call_index, "current_page": current, "links_offered": len(links),
                               "hits_offered": len(offered_by_search), "requested_action": requested,
                               "usage": usage, "model_digest": digest, "prompt_version": config.prompt_version})
        remaining = config.model_call_budget - calls
        if not isinstance(requested, dict) or requested.get("action") not in ACTIONS:
            terminal = {"reason": "invalid_action", "validation_error": "unsupported_action", "requested": requested}
            break
        name = requested["action"]
        if name == "search":
            query = requested.get("query")
            if searches >= config.search_budget:
                terminal = {"reason": "budget_exhausted", "exhausted": "searches", "requested": requested}
                break
            if not isinstance(query, str):
                terminal = {"reason": "invalid_action", "validation_error": "query_not_text", "requested": requested}
                break
            run_search(query, landing=False)
        elif name == "open":
            doc_id = requested.get("doc_id")
            if opens >= config.page_visit_budget:
                terminal = {"reason": "budget_exhausted", "exhausted": "page_visits", "requested": requested}
                break
            via_search = isinstance(doc_id, str) and doc_id in offered_by_search
            via_link = isinstance(doc_id, str) and doc_id in links
            if not (via_search or via_link) or doc_id == current:
                # S057: a refused open is recovered up to the declared number of times -- the refusal
                # stands (S046's loop guard), the next observation says why, the call already counted.
                if len(recovered_steps) < config.recoveries_allowed:
                    recovered_steps.append(call_index)
                    provider_calls[-1].update({"recovered": True, "validation_error": "page_not_offered"})
                    feedback = RECOVERY_FEEDBACK
                    continue
                terminal = {"reason": "invalid_action", "validation_error": "page_not_offered", "requested": requested}
                break
            # Route precedence: a page any search has offered is a search
            # route (landing when the landing search offered it), so a link
            # is credited only where grep did not find the page.
            if via_search:
                route = "landing" if offered_by_search[doc_id] == 1 else "search"
            else:
                route = "link"
            opens += 1
            stack.append(doc_id)
            visits.append(doc_id)
            dead_end = not admissible(doc_id)
            dead_ends += int(dead_end)
            # S046b: the page view's truncation is recorded per open (E-EXP1e I8 read it by hand).
            trace_action(actions, "open", budget_remaining=remaining,
                         details={"from_doc_id": current, "doc_id": doc_id, "open_index": opens, "route": route,
                                  "search_index": offered_by_search.get(doc_id), "dead_end": dead_end,
                                  "revisit": visits.count(doc_id) > 1,
                                  "searches_before": searches, "opens_before": opens - 1,
                                  "truncated": len(corpus[doc_id]) > config.page_char_limit,
                                  "char_count": len(corpus[doc_id])})
        elif name == "back":
            if len(stack) < 2:
                terminal = {"reason": "invalid_action", "validation_error": "back_at_first_page", "requested": requested}
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
                       "model_digest": policy.digest, "prompt_version": config.prompt_version,
                       "response_schema_version": RESPONSE_SCHEMA_VERSION,
                       "temperature": policy.temperature, "top_p": policy.top_p, "seed": policy.seed,
                       "max_tokens": policy.max_output_tokens, "thinking": policy.thinking,
                       "search_budget": config.search_budget, "search_hit_files": config.search_hit_files,
                       "search_hit_lines_per_file": config.search_hit_lines_per_file,
                       "page_visit_budget": config.page_visit_budget,
                       "model_call_budget": config.model_call_budget, "page_char_limit": config.page_char_limit},
        "follow_links": config.follow_links,
        "landing": config.landing_policy,
        "landing_query": question["question"],
        "landing_terms": terms,
        "start_doc_id": None,
        "actions": actions,
        "provider_calls": provider_calls,
        "visits": visits,
        "opens": opens,
        "searches": searches,
        "zero_hit_searches": zero_hit_searches,
        "model_calls": calls,
        "dead_ends": dead_ends,
        "terminal": terminal,
        "final_context_doc_ids": context_ids,
        "answer": answer,
        "paper_facing_evidence": False,
    }
    if config.invalid_action_recovery is not None:
        trace["controller"]["invalid_action_recovery"] = config.invalid_action_recovery
        trace["invalid_actions_recovered"] = {"count": len(recovered_steps), "steps": recovered_steps}
    return prediction, trace


def write_trace(path: Path, trace: dict[str, Any]) -> None:
    """One JSONL file per question: a header line, one line per action (searches included), one terminal line."""
    header = {k: trace[k] for k in ("question_id", "schema_version", "compatible_action_schema_version", "policy",
                                    "controller", "follow_links", "landing", "landing_query", "landing_terms", "start_doc_id")}
    header["record"] = "header"
    rows = [header]
    for action in trace["actions"]:
        rows.append({"record": "action", **action})
    rows.append({"record": "terminal", "terminal": trace["terminal"], "visits": trace["visits"],
                 "opens": trace["opens"], "searches": trace["searches"], "zero_hit_searches": trace["zero_hit_searches"],
                 "model_calls": trace["model_calls"], "dead_ends": trace["dead_ends"],
                 "answer": trace["answer"], "final_context_doc_ids": trace["final_context_doc_ids"],
                 "provider_calls": trace["provider_calls"], "paper_facing_evidence": False,
                 **({"invalid_actions_recovered": trace["invalid_actions_recovered"]}
                    if "invalid_actions_recovered" in trace else {})})
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def metadata(config: ReaderConfig) -> dict[str, Any]:
    policy = config.model_policy
    return {
        "name": config.provider_name, "deterministic": False, "top_k": config.top_k,
        "max_context_chars_per_doc": config.max_context_chars_per_doc, "answer_policy": ANSWER_POLICY,
        "search_budget": config.search_budget, "search_hit_files": config.search_hit_files,
        "search_hit_lines_per_file": config.search_hit_lines_per_file,
        "page_visit_budget": config.page_visit_budget, "model_call_budget": config.model_call_budget,
        "page_char_limit": config.page_char_limit, "follow_links": config.follow_links,
        "model_policy": {"backend": policy.backend, "model": policy.model, "digest": policy.digest,
                         "temperature": policy.temperature, "top_p": policy.top_p, "seed": policy.seed,
                         "context_window_tokens": policy.context_window_tokens,
                         "max_output_tokens": policy.max_output_tokens,
                         "timeout_seconds": policy.timeout_seconds, "max_attempts": policy.max_attempts,
                         "endpoint": policy.endpoint, "thinking": policy.thinking},
        "trace_schema_version": TRACE_SCHEMA_VERSION, "provider_policy": PROVIDER_POLICY_NAME,
        "prompt_version": config.prompt_version, "landing": config.landing_policy,
        **({"stop_words": list(STOP_WORDS_EN_V1), "stop_word_list": STOP_WORDS_NAME} if config.tokenised_landing else {}),
        **({"invalid_action_recovery": config.invalid_action_recovery}
           if config.invalid_action_recovery is not None else {}),
    }


def run(input_dir: Path, output_dir: Path, *, client_factory=ModelClient) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    config = parse_config(read_config(input_dir / "config.yaml"))
    questions = read_jsonl(input_dir / "questions.jsonl")
    corpus = {path.stem: path.read_text(encoding="utf-8") for path in sorted((input_dir / "corpus").glob("*.md"))}
    edges = read_edges(input_dir / "graph_edges.csv")
    summary: dict[str, Any] = {"schema_version": "retrieval_arena.navigation_run.v1", "provider": config.provider_name,
                               "start_doc_id": None, "landing": config.landing_policy,
                               "follow_links": config.follow_links, "question_count": len(questions),
                               "document_count": len(corpus),
                               "upstream_host": urlparse(config.model_policy.endpoint).hostname}
    if not corpus:
        summary.update({"status": "unavailable", "reason": "corpus_empty"})
        (output_dir / "navigation.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 4
    client = client_factory(config.model_policy)
    summary["upstream_addresses"] = list(client.upstream_addresses)
    summary["model_digest_before"] = client.observe_identity()
    traces_dir = output_dir / "traces"
    traces_dir.mkdir()
    predictions = []
    terminals: dict[str, int] = {name: 0 for name in TERMINALS}
    total_calls = total_opens = total_searches = total_recovered = 0
    started = time.monotonic()
    for question in questions:
        prediction, trace = navigate(question, corpus, edges, config, client)
        predictions.append(prediction)
        write_trace(traces_dir / f"{question['question_id']}.jsonl", trace)
        terminals[trace["terminal"]["reason"]] += 1
        total_calls += trace["model_calls"]
        total_opens += trace["opens"]
        total_searches += trace["searches"]
        total_recovered += trace.get("invalid_actions_recovered", {}).get("count", 0)
    summary["model_digest_after"] = client.observe_identity()
    summary.update({"status": "completed", "terminals": terminals, "model_calls": total_calls,
                    "page_opens": total_opens, "searches": total_searches,
                    "wall_seconds": round(time.monotonic() - started, 1)})
    if config.invalid_action_recovery is not None:
        summary["invalid_actions_recovered"] = total_recovered
    with (output_dir / "predictions.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in predictions:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    (output_dir / "metadata.json").write_text(json.dumps(metadata(config), indent=2, sort_keys=True) + "\n",
                                              encoding="utf-8")
    (output_dir / "navigation.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the S046 bounded repo-search agent reader.")
    parser.add_argument("--input-dir", default="/input")
    parser.add_argument("--output-dir", default="/output")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(run(Path(arguments.input_dir), Path(arguments.output_dir)))
