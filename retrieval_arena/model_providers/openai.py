"""OpenAI Responses API structured-output transport (official SDK, optional).

Ported from the ICSC context gpt-6-luna bridge: same no-retry, no-redirect,
no-proxy posture, generalized to any model with a declared price table.
external_api only (https://api.openai.com); OPENAI_API_KEY by name only.
An incomplete response is an error, never a parsed judgment.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .anthropic import _sha
from .base import REQUEST_OVERHEAD_TOKENS, admit_external, host_guard, redact, require


class OpenAITransport:
    def __init__(self, ledger, network, *, client=None, require_container=True):
        base_url = admit_external("openai", network, require_container)
        self.ledger = ledger
        if client is None:
            import openai
            http = openai.DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False,
                                                  event_hooks={"request": [host_guard("openai")]})
            client = openai.AsyncOpenAI(base_url=base_url + "/v1", max_retries=0, http_client=http)
        self.client = client

    @staticmethod
    def request(prompt, schema, config):
        generation = config["generation"]
        return {"model": config["model"], "input": prompt, "max_output_tokens": config["max_output_tokens"],
                "reasoning": {"effort": generation["reasoning_effort"]}, "store": False,
                "text": {"format": {"type": "json_schema", "name": "ra_structured_output",
                                    "schema": schema, "strict": generation["strict"]}}}

    async def __call__(self, prompt, schema, config):
        require(config["provider"] == "openai", "judge-provider")
        body = self.request(prompt, schema, config)
        reservation = self.ledger.reserve(config["max_input_tokens"] + REQUEST_OVERHEAD_TOKENS,
                                          config["max_output_tokens"], _sha(body))
        try:
            response = await self.client.responses.create(**body)
        except Exception as exc:
            self.ledger.fail(reservation, type(exc).__name__, getattr(exc, "request_id", None))
            raise RuntimeError("openai-request-failed:" + type(exc).__name__ + ":" + redact(str(getattr(exc, "status_code", "")), "openai")) from None
        request_id = getattr(response, "_request_id", None)
        raw = response.usage
        cached = getattr(getattr(raw, "input_tokens_details", None), "cached_tokens", None) or 0
        usage = {"input_tokens": raw.input_tokens - cached, "output_tokens": raw.output_tokens,
                 "cache_write_tokens": None, "cache_read_tokens": cached}
        cost = self.ledger.report(reservation, usage, request_id)
        # Hosted snapshots may carry a dated suffix; the requested id must prefix it.
        require(response.model == config["model"] or response.model.startswith(config["model"] + "-"), "provider-response-model")
        require(response.status == "completed", "provider-status-" + str(response.status))
        record = {**usage, "service_tier": getattr(response, "service_tier", None), "request_id": request_id,
                  "response_id": response.id, "response_model": response.model, "status": response.status,
                  "reasoning_effort": config["generation"]["reasoning_effort"],
                  "date": datetime.now(timezone.utc).date().isoformat()}
        return {"text": response.output_text, "model": config["model"], "revision": config["revision"], "usage": record, "cost": cost}
