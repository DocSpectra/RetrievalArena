"""Portable, stateless one-action model turn; no corpus or scoring policy.

The caller supplies the visible, role-ordered history and allowed actions. This
module intentionally does not choose an action, retry, or follow provider tools.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def redact_error(value: str) -> str:
    """Remove complete or provider-masked API key fragments from diagnostics."""
    secret = os.environ.get("OPENAI_API_KEY")
    if secret:
        value = value.replace(secret, "[REDACTED_API_KEY]")
    return re.sub(r"sk-[A-Za-z0-9_*.-]{8,}", "[REDACTED_API_KEY]", value)


FIELDS = {
    "search": {"query": {"type": "string"}},
    "read": {"doc_id": {"type": "string"}, "start_line": {"type": "integer"}, "line_count": {"type": "integer"}},
    "follow": {"source_doc_id": {"type": "string"}, "doc_id": {"type": "string"},
               "start_line": {"type": "integer"}, "line_count": {"type": "integer"}},
    "note": {"text": {"type": "string"}, "citations": {"type": "array", "items": {
        "type": "object", "properties": {"doc_id": {"type": "string"},
        "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        "required": ["doc_id", "start_line", "end_line"], "additionalProperties": False}}},
    "answer": {"text": {"type": "string"}, "citations": {"type": "array", "items": {
        "type": "object", "properties": {"doc_id": {"type": "string"},
        "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        "required": ["doc_id", "start_line", "end_line"], "additionalProperties": False}}},
}


@dataclass(frozen=True)
class TurnRequest:
    history: list[dict[str, str]]
    allowed_actions: list[str]
    provider: str
    model: str
    endpoint: str
    max_output_tokens: int
    generation: dict[str, Any] = field(default_factory=dict)


def tools_for(actions: list[str]) -> list[dict[str, Any]]:
    if not actions or len(set(actions)) != len(actions) or any(a not in FIELDS for a in actions):
        raise ValueError("invalid-allowed-actions")
    return [{"type": "function", "name": name, "description": "Documentation " + name,
             "strict": True, "parameters": {"type": "object", "properties": FIELDS[name],
                                      "required": list(FIELDS[name]), "additionalProperties": False}}
            for name in actions]


def validate_action(name: str, args: Any, allowed: list[str]) -> dict[str, Any]:
    if name not in allowed or name not in FIELDS or not isinstance(args, dict) or set(args) != set(FIELDS[name]):
        raise ValueError("action-shape-or-not-allowed")
    for key, schema in FIELDS[name].items():
        typ = schema["type"]
        if (typ == "string" and not isinstance(args[key], str) or
            typ == "integer" and type(args[key]) is not int or
            typ == "array" and not isinstance(args[key], list)):
            raise ValueError("action-field-type")
    if name in {"note", "answer"}:
        if not args["text"].strip() or not args["citations"]:
            raise ValueError("action-empty-answer")
        for citation in args["citations"]:
            if (not isinstance(citation, dict) or set(citation) != {"doc_id", "start_line", "end_line"}
                or not isinstance(citation["doc_id"], str)
                or type(citation["start_line"]) is not int or type(citation["end_line"]) is not int):
                raise ValueError("citation-shape")
    return {"action": name, **args}


class ResponseTurn:
    """Invoke a supplied Responses transport exactly once; append credential-free events."""

    def __init__(self, create: Callable[..., Any], emit: Callable[[dict[str, Any]], None],
                 raw: Callable[[dict[str, Any]], str]):
        self.create, self.emit, self.raw = create, emit, raw

    def complete(self, turn: TurnRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        if turn.provider not in {"openai", "ollama"} or turn.max_output_tokens < 1:
            raise ValueError("invalid-turn-provider-or-cap")
        if any(set(m) != {"role", "content"} or m["role"] not in {"system", "user", "assistant"}
               or not isinstance(m["content"], str) for m in turn.history):
            raise ValueError("invalid-visible-history")
        payload: dict[str, Any] = {"model": turn.model, "input": turn.history,
            "tools": tools_for(turn.allowed_actions), "max_output_tokens": turn.max_output_tokens}
        if turn.provider == "openai":
            payload.update({"store": False, "service_tier": "default", "reasoning": {"effort": "medium"},
                            "parallel_tool_calls": False, "tool_choice": "required"})
        else:
            payload["think"] = False
        payload.update(turn.generation)
        if turn.provider == "openai" and any(k in payload for k in ("seed", "num_ctx", "top_k", "repeat_penalty", "think")):
            raise ValueError("ollama-setting-on-openai")
        request_hash = digest(payload)
        self.emit({"event": "request", "provider": turn.provider, "model": turn.model,
                   "endpoint": turn.endpoint, "request_sha256": request_hash})
        start = time.monotonic()
        response_hash = None
        raw_ref = None
        try:
            response = self.create(**payload)
            if hasattr(response, "model_dump"):
                response = response.model_dump(mode="json")
            if not isinstance(response, dict):
                raise ValueError("response-not-object")
            response_hash = digest(response)
            raw_ref = self.raw(response)
            usage = response.get("usage") or {}
            details = usage.get("input_tokens_details") or {}
            out_details = usage.get("output_tokens_details") or {}
            token_usage = {"input": usage.get("input_tokens"), "cached_input": details.get("cached_tokens"),
                           "cache_write": details.get("cache_write_tokens"), "output": usage.get("output_tokens"),
                           "reasoning": out_details.get("reasoning_tokens")}
            status = response.get("status")
            if response.get("model") != turn.model or status != "completed":
                raise ValueError("response-incomplete-or-wrong-model")
            output = response.get("output") or []
            calls = [item for item in output if item.get("type") == "function_call"]
            if len(calls) != 1 or any(item.get("type") not in {"function_call", "reasoning"} for item in output):
                raise ValueError("expected-exactly-one-function-call")
            call = calls[0]
            if not isinstance(call.get("call_id"), str) or not call["call_id"]:
                raise ValueError("missing-call-id")
            action = validate_action(call.get("name"), json.loads(call.get("arguments", "")), turn.allowed_actions)
            metadata = {"provider": turn.provider, "model": response["model"], "request_id": response.get("id"),
                "service_tier": response.get("service_tier"), "status": status, "reasoning_effort":
                payload.get("reasoning", {}).get("effort"), "call_id": call["call_id"], "usage": token_usage,
                "latency_seconds": round(time.monotonic() - start, 6), "request_sha256": request_hash,
                "response_sha256": response_hash, "raw_response_ref": raw_ref,
                "strict_schema_requested": True, "forced_tool_requested": turn.provider == "openai",
                "provider_schema_enforcement_verified": turn.provider == "openai"}
            self.emit({"event": "response", **metadata, "action": action})
            return action, metadata
        except Exception as exc:
            error_text = redact_error(str(exc))
            self.emit({"event": "failure_after_request", "request_sha256": request_hash,
                       "response_sha256": response_hash, "raw_response_ref": raw_ref,
                       "error_type": type(exc).__name__, "error": error_text,
                       "possible_billable": turn.provider == "openai"})
            raise
