"""Provider-neutral structured completion: settings, admission and host policy.

One call shape for every provider: async transport(prompt, response_schema,
settings) -> {text, model, revision, usage, cost} (the RAGAS transport contract,
docs/model_providers.md). Credentials are passed to containers by name and read
only by the provider SDK; this module checks presence, never prints a value.
"""
from __future__ import annotations

import os
import re

from ..errors import ValidationError

PROVIDERS = ("ollama", "anthropic", "openai")
# The only host an external_api client may reach, per provider (RA-D07).
HOSTS = {"anthropic": "api.anthropic.com", "openai": "api.openai.com"}
CREDENTIAL_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
# Endpoint overrides the SDKs would otherwise honor silently.
ENDPOINT_ENV = {"anthropic": ("ANTHROPIC_BASE_URL",), "openai": ("OPENAI_BASE_URL", "OPENAI_API_BASE")}
NETWORK = {"ollama": "host_model_alias", "anthropic": "external_api", "openai": "external_api"}
ANTHROPIC_EFFORTS = ("low", "medium", "high", "xhigh", "max")
OPENAI_EFFORTS = ("none", "minimal", "low", "medium", "high")
# Allowance for provider-side request framing (structured-output schema prompt);
# C0 measured about 190 tokens. Reserved, never estimated from prompt length.
REQUEST_OVERHEAD_TOKENS = 1024
MAX_SPEND_CAP_USD = 50.0


def require(ok, message):
    if not ok:
        raise ValidationError(message)


def exact(value, names):
    require(isinstance(value, dict) and set(value) == set(names.split()), "provider-settings-fields")


def validate_pricing(pricing):
    exact(pricing, "currency input_per_mtok output_per_mtok long_context source")
    require(pricing["currency"] == "USD", "pricing-currency")
    rates = [pricing["input_per_mtok"], pricing["output_per_mtok"]]
    if pricing["long_context"] is not None:
        exact(pricing["long_context"], "above_input_tokens input_per_mtok output_per_mtok")
        require(type(pricing["long_context"]["above_input_tokens"]) is int and pricing["long_context"]["above_input_tokens"] > 0, "pricing-tier")
        rates += [pricing["long_context"]["input_per_mtok"], pricing["long_context"]["output_per_mtok"]]
    require(all(type(r) in (int, float) and r > 0 for r in rates), "pricing-rate")
    require(isinstance(pricing["source"], str) and pricing["source"].strip() != "", "pricing-source")


def validate_settings(config):
    """Provider fields of a provider-aware config (RAGAS judge config v4)."""
    provider = config["provider"]
    require(provider in PROVIDERS, "provider-unknown")
    generation = config["generation"]
    if provider == "ollama":
        require(re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", config["revision"]) is not None, "judge-digest-required")
        require(type(config["temperature"]) in (int, float) and type(config["seed"]) is int, "ollama-decoding")
        require(generation is None and config["pricing"] is None and config["spend_cap_usd"] is None, "ollama-unpriced")
        return
    # Hosted models have no digest: the pin is the model id, and the response must name it.
    require(config["revision"] == config["model"], "hosted-revision-is-model-id")
    # Never fake determinism: hosted Claude rejects sampling overrides; none are sent.
    require(config["temperature"] is None and config["seed"] is None, "hosted-sampling-not-admitted")
    require(config["thinking"] is False, "hosted-thinking-in-generation")
    validate_pricing(config["pricing"])
    cap = config["spend_cap_usd"]
    require(type(cap) in (int, float) and 0 < cap <= MAX_SPEND_CAP_USD, "spend-cap")
    if provider == "anthropic":
        exact(generation, "thinking effort")
        require(generation["thinking"] in ("disabled", "adaptive"), "anthropic-thinking")
        require(generation["effort"] in ANTHROPIC_EFFORTS, "anthropic-effort")
        # Disabled thinking is accepted only at effort high or below.
        require(generation["thinking"] == "adaptive" or generation["effort"] in ("low", "medium", "high"), "anthropic-thinking-effort")
    else:
        exact(generation, "reasoning_effort strict")
        require(generation["reasoning_effort"] in OPENAI_EFFORTS, "openai-reasoning-effort")
        require(type(generation["strict"]) is bool, "openai-strict")


def admit_external(provider, network, require_container=True):
    """Admission for an external_api call. Raises before any client exists."""
    require(provider in HOSTS, "provider-not-external")
    require(network == "external_api", "provider-network-not-admitted")
    if require_container:
        require(os.path.isfile("/.dockerenv"), "provider-container-required")
    require(not any(os.environ.get(name) for name in ENDPOINT_ENV[provider]), "provider-endpoint-override")
    require(bool(os.environ.get(CREDENTIAL_ENV[provider])), "provider-credential-unavailable")
    return "https://" + HOSTS[provider]


def host_guard(provider):
    """httpx request hook: refuse any request not to the admitted host over https."""
    host = HOSTS[provider]

    async def check(request):
        require(request.url.scheme == "https" and request.url.host == host and request.url.port in (None, 443), "provider-host-not-admitted")
    return check


def redact(text, provider):
    """Drop a credential value from diagnostics; unknown values are left alone."""
    secret = os.environ.get(CREDENTIAL_ENV.get(provider, ""), "")
    return text.replace(secret, "[REDACTED]") if secret else text
