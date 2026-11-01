"""Anthropic Messages API structured-output transport (official SDK, optional).

external_api only: the client reaches https://api.anthropic.com and nothing else
(no redirects, no proxies, no environment endpoint overrides, SDK retries off;
the caller's own retry policy applies). The credential is read by the SDK from
ANTHROPIC_API_KEY, which a container receives by name only. A refusal or a
truncated response is an error, never a parsed judgment.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from .base import REQUEST_OVERHEAD_TOKENS, admit_external, host_guard, redact, require


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


class AnthropicTransport:
    def __init__(self, ledger, network, *, client=None, transform=None, require_container=True):
        base_url = admit_external("anthropic", network, require_container)
        self.ledger = ledger
        if client is None or transform is None:
            import anthropic
            transform = transform or anthropic.transform_schema
        self.transform = transform
        if client is None:
            http = anthropic.DefaultAsyncHttpxClient(trust_env=False, follow_redirects=False,
                                                     event_hooks={"request": [host_guard("anthropic")]})
            client = anthropic.AsyncAnthropic(base_url=base_url, max_retries=0, http_client=http)
        self.client = client

    def request(self, prompt, schema, config):
        generation = config["generation"]
        # The SDK moves unsupported constraints into descriptions; the caller
        # still validates the reply against the original schema.
        body = {"model": config["model"], "max_tokens": config["max_output_tokens"],
                "messages": [{"role": "user", "content": prompt}],
                "thinking": {"type": generation["thinking"]},
                "output_config": {"effort": generation["effort"],
                                  "format": {"type": "json_schema", "schema": self.transform(schema)}}}
        if 'service_tier' in config: body['service_tier']=config['service_tier']
        return body

    async def __call__(self, prompt, schema, config, *, audit=None):
        require(config["provider"] == "anthropic", "judge-provider")
        body = self.request(prompt, schema, config)
        reservation = self.ledger.reserve(config["max_input_tokens"] + REQUEST_OVERHEAD_TOKENS,
                                          config["max_output_tokens"], _sha(body))
        if audit is not None: audit({'event':'request','body':body,'reservation':reservation})
        try:
            message = await self.client.messages.create(**body)
        except Exception as exc:
            self.ledger.fail(reservation, type(exc).__name__, getattr(exc, "request_id", None))
            if audit is not None:
                audit({'event':'failure','error_class':type(exc).__name__,
                       'status_code':getattr(exc,'status_code',None),'request_id':getattr(exc,'request_id',None),
                       'redacted_response':('[authentication payload suppressed]' if type(exc).__name__ in ('AuthenticationError','PermissionDeniedError')
                                            else redact(str(getattr(exc,'body','')), 'anthropic'))})
            raise RuntimeError("anthropic-request-failed:" + type(exc).__name__ + ":" + redact(str(getattr(exc, "status_code", "")), "anthropic")) from None
        if audit is not None:
            audit({'event':'response','body':message.model_dump(mode='json'),
                   'request_id':getattr(message,'_request_id',None)})
        request_id = getattr(message, "_request_id", None)
        raw = message.usage
        usage = {"input_tokens": raw.input_tokens, "output_tokens": raw.output_tokens,
                 "cache_write_tokens": getattr(raw, "cache_creation_input_tokens", None),
                 "cache_read_tokens": getattr(raw, "cache_read_input_tokens", None)}
        cost = self.ledger.report(reservation, usage, request_id)
        require(message.model == config["model"], "provider-response-model")
        # refusal (with stop_details), max_tokens, pause_turn: not a judgment.
        require(message.stop_reason == "end_turn", "provider-stop-" + str(message.stop_reason))
        texts = [block.text for block in message.content if block.type == "text"]
        require(len(texts) == 1, "provider-text-block")
        record = {**usage, "service_tier": getattr(raw, "service_tier", None), "request_id": request_id,
                  "response_model": message.model, "stop_reason": message.stop_reason,
                  "thinking": config["generation"]["thinking"], "effort": config["generation"]["effort"],
                  "sent_schema_sha256": _sha(body["output_config"]["format"]["schema"]),
                  "date": datetime.now(timezone.utc).date().isoformat()}
        return {"text": texts[0], "model": config["model"], "revision": config["revision"], "usage": record, "cost": cost}
