"""Judge-only local Ollama transport, confined to the optional Docker operator.

Moved from retrieval_arena/ragas_ollama.py in C1; behavior unchanged.

Matches RA's local model alias exception: tags and chat only, pinned digest
observed before/after each call. No redirects, proxies, downloads or fallback.
"""
import asyncio
import ipaddress
import json
import os
import re
import socket

from ..ragas_scoring import canonical, require

BASE = "http://host.docker.internal:11434"


class OllamaTransport:
    def __init__(self):
        require(os.environ.get("RA_RAGAS_NETWORK") == "host_model_alias", "judge-network-not-admitted")
        require(os.path.isfile("/.dockerenv"), "judge-container-required")

    async def request(self, route, config, body=None):
        import httpx
        require(route in {"/api/tags", "/api/chat"}, "judge-route")
        require((route == "/api/tags") == (body is None), "judge-method")
        addresses = await asyncio.get_running_loop().getaddrinfo("host.docker.internal", 11434, type=socket.SOCK_STREAM)
        require(bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_private for a in addresses), "judge-public-upstream")
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=config["timeout_seconds"]) as client:
            async with client.stream("POST" if body is not None else "GET", BASE + route,
                                     content=canonical(body) if body is not None else None,
                                     headers={"Content-Type": "application/json"}) as response:
                require(response.status_code == 200, "judge-http-status")
                payload = bytearray()
                async for data in response.aiter_bytes():
                    payload.extend(data)
                    require(len(payload) <= 4 * 1024 * 1024, "judge-response-limit")
        return json.loads(payload)

    async def observe(self, config):
        models = (await self.request("/api/tags", config))["models"]
        matches = [m for m in models if m.get("name") == config["model"]]
        require(len(matches) == 1, "judge-model-absent")
        observed = matches[0].get("digest", "").removeprefix("sha256:")
        require(observed == config["revision"].removeprefix("sha256:"), "judge-model-digest-mismatch")
        return config["revision"]

    async def __call__(self, prompt, schema, config):
        require(config["provider"] == "ollama", "judge-provider")
        require(re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", config["revision"]) is not None, "judge-digest-required")
        await self.observe(config)
        response = await self.request("/api/chat", config, {
            "model": config["model"], "stream": False, "think": config.get("thinking", False), "format": schema,
            "messages": [{"role": "user", "content": prompt}],
            # Conservative byte admission plus schema fits without context truncation.
            "options": {"temperature": config["temperature"], "seed": config["seed"], "top_p": 1.0, "top_k": 1, "repeat_penalty": 1.0,
                        "num_predict": config["max_output_tokens"],
                        "num_ctx": config["max_input_tokens"] + config["max_output_tokens"] + 1024}})
        revision = await self.observe(config)
        require(response.get("model") == config["model"], "judge-response-model")
        require(response.get("done") is True and response.get("done_reason") != "length", "judge-output-truncated")
        usage = {k: response[k] for k in ("prompt_eval_count", "eval_count") if k in response}
        return {"text": response["message"]["content"], "model": config["model"], "revision": revision,
                "usage": usage or None, "cost": None}
