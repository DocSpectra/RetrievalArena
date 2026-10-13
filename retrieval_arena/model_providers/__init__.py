"""Model providers: one structured-completion transport per provider, one ledger.

See docs/model_providers.md. Provider SDKs are optional and imported lazily, so
this package imports without them (the verification image has neither).
"""
from __future__ import annotations

from pathlib import Path

from .base import CREDENTIAL_ENV, NETWORK, PROVIDERS, require, validate_settings  # noqa: F401
from .ledger import CostLedger, SpendStop  # noqa: F401


def transport_for(config, network, output_dir):
    """The admitted transport for a validated config; ledger at <output_dir>/cost-ledger.jsonl."""
    provider = config["provider"]
    require(provider in PROVIDERS, "provider-unknown")
    require(network == NETWORK[provider], "provider-network-mismatch")
    if provider == "ollama":
        from .ollama import OllamaTransport
        return OllamaTransport()
    ledger = CostLedger(Path(output_dir) / "cost-ledger.jsonl", config["pricing"], config["spend_cap_usd"],
                        provider=provider, model=config["model"])
    if provider == "anthropic":
        from .anthropic import AnthropicTransport
        return AnthropicTransport(ledger, network)
    from .openai import OpenAITransport
    return OpenAITransport(ledger, network)
