"""Model provider layer (C1): settings, ledger, admission and fake transports.

No network and no credentials: every SDK client is a fake. The last class needs
Ragas and runs only in the optional Docker image (RA_RAGAS_SMOKE=1).
"""
import asyncio
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from retrieval_arena.errors import ValidationError
from retrieval_arena.model_providers import CostLedger, SpendStop, transport_for
from retrieval_arena.model_providers.anthropic import AnthropicTransport
from retrieval_arena.model_providers.base import host_guard, validate_settings
from retrieval_arena.model_providers.openai import OpenAITransport
from retrieval_arena.ragas_scoring import PREFIX, validate_config
from unit_tests.ragas_fixture import inputs_v3

# A fake value shaped like a key; it must never appear in any output.
FAKE_KEY = "sk-ant-api03-FAKE-" + "x" * 40
HAIKU_PRICING = {"currency": "USD", "input_per_mtok": 0.10, "output_per_mtok": 0.50, "source": "unit-test",
                 "long_context": {"above_input_tokens": 100000, "input_per_mtok": 0.50, "output_per_mtok": 2.50}}


def hosted_config(provider="anthropic"):
    _, _, config = inputs_v3()
    model = {"anthropic": "claude-haiku-5-5", "openai": "gpt-6-luna"}[provider]
    generation = ({"thinking": "disabled", "effort": "low"} if provider == "anthropic"
                  else {"reasoning_effort": "low", "strict": False})
    config.update(schema_version=PREFIX + "judge_config.v4", provider=provider, model=model, revision=model,
                  temperature=None, seed=None, thinking=False, generation=generation,
                  pricing=copy.deepcopy(HAIKU_PRICING), spend_cap_usd=0.05)
    return config


def message(text='{"ok": true}', stop="end_turn", model="claude-haiku-5-5", input_tokens=212, output_tokens=8):
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens, cache_creation_input_tokens=0,
                            cache_read_input_tokens=0, service_tier="standard")
    return SimpleNamespace(model=model, stop_reason=stop, usage=usage, _request_id="req_fake_1",
                           content=[SimpleNamespace(type="text", text=text)])


class FakeMessages:
    def __init__(self, replies):
        self.replies, self.bodies = list(replies), []

    async def create(self, **body):
        self.bodies.append(body)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(body) if callable(reply) else reply


@contextmanager
def env(**extra):
    """Fake credentials; ambient endpoint overrides removed (restored on exit)."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_KEY, "OPENAI_API_KEY": FAKE_KEY, **extra}):
        for name in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OPENAI_API_BASE"):
            if name not in extra:
                os.environ.pop(name, None)
        yield


class SettingsTests(unittest.TestCase):
    def test_v4_hosted_config_validates_and_v1_to_v3_unchanged(self):
        validate_config(hosted_config())
        validate_config(hosted_config("openai"))
        _, _, v3 = inputs_v3()
        validate_config(v3)
        with self.assertRaises(ValidationError):
            validate_config({**v3, "generation": None})  # v3 gains no fields

    def test_refusals(self):
        config = hosted_config()
        cases = [
            {"provider": "vllm"},                                   # unknown provider
            {"revision": "latest"},                                 # missing identity
            {"revision": "claude-haiku-5"},                         # pin must be the model id
            {"temperature": 0},                                     # no faked determinism
            {"seed": 7},
            {"thinking": "low"},                                    # hosted thinking lives in generation
            {"generation": {"thinking": "disabled", "effort": "xhigh"}},
            {"generation": {"thinking": "enabled", "effort": "low"}},
            {"pricing": None},
            {"pricing": {**HAIKU_PRICING, "source": ""}},
            {"spend_cap_usd": 0},
            {"spend_cap_usd": 51},
        ]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validate_config({**config, **change})

    def test_ollama_v4_keeps_digest_and_sampling(self):
        config = {**hosted_config(), "provider": "ollama", "model": "qwen3:14b", "revision": "a" * 64,
                  "temperature": 0, "seed": 7, "generation": None, "pricing": None, "spend_cap_usd": None}
        validate_settings(config)
        with self.assertRaises(ValidationError):
            validate_settings({**config, "revision": "qwen3:14b"})

    def test_transport_for_network_and_external_admission(self):
        config = hosted_config()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValidationError):
                transport_for(config, "host_model_alias", temp)     # wrong mode for a hosted provider
            with env(), patch("os.path.isfile", return_value=False), self.assertRaises(ValidationError):
                transport_for(config, "external_api", temp)         # container required
            with patch.dict(os.environ, {}, clear=True), patch("os.path.isfile", return_value=True), self.assertRaises(ValidationError):
                transport_for(config, "external_api", temp)         # credential absent
            with env(ANTHROPIC_BASE_URL="https://example.invalid"), patch("os.path.isfile", return_value=True), self.assertRaises(ValidationError):
                transport_for(config, "external_api", temp)         # endpoint override

    def test_host_guard_admits_only_the_provider_host(self):
        guard = host_guard("anthropic")
        url = lambda scheme, host, port=None: SimpleNamespace(url=SimpleNamespace(scheme=scheme, host=host, port=port))
        asyncio.run(guard(url("https", "api.anthropic.com")))
        for bad in (url("http", "api.anthropic.com"), url("https", "api.openai.com"),
                    url("https", "api.anthropic.com.evil.test"), url("https", "api.anthropic.com", 8443)):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                asyncio.run(guard(bad))


class LedgerTests(unittest.TestCase):
    def test_reserve_report_fail_stop_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cost-ledger.jsonl"
            ledger = CostLedger(path, HAIKU_PRICING, 0.01, provider="anthropic", model="claude-haiku-5-5")
            # 10,000 in (x1.25 cache-write bound) + 1,000 out = 0.00125 + 0.0005
            self.assertAlmostEqual(ledger.projection(10000, 1000), 0.00175)
            first = ledger.reserve(10000, 1000, "a" * 64)
            cost = ledger.report(first, {"input_tokens": 212, "output_tokens": 8}, "req_1")
            self.assertAlmostEqual(cost, 212 * 0.10e-6 + 8 * 0.50e-6)
            second = ledger.reserve(10000, 1000, "b" * 64)
            ledger.fail(second, "APITimeoutError")             # failed call keeps its full projection
            self.assertAlmostEqual(ledger.spent, cost + 0.00175)
            resumed = CostLedger(path, HAIKU_PRICING, 0.01, provider="anthropic", model="claude-haiku-5-5")
            self.assertAlmostEqual(resumed.spent, ledger.spent)
            for _ in range(4):
                resumed.reserve(10000, 1000, "c" * 64)
            with self.assertRaises(SpendStop):
                resumed.reserve(10000, 1000, "d" * 64)
            events = [json.loads(line)["event"] for line in path.read_text().splitlines()]
            self.assertEqual(events[-1], "stopped")
            with self.assertRaises(ValidationError):
                resumed.report(99, {"input_tokens": 1, "output_tokens": 1}, None)

    def test_long_context_tier_applies_above_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CostLedger(Path(temp) / "l.jsonl", HAIKU_PRICING, 1, provider="anthropic", model="m")
            self.assertAlmostEqual(ledger.cost({"input_tokens": 100001, "output_tokens": 0}), 100001 * 0.50e-6)
            self.assertAlmostEqual(ledger.cost({"input_tokens": 100000, "output_tokens": 0}), 100000 * 0.10e-6)

    def test_usage_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = CostLedger(Path(temp) / "l.jsonl", HAIKU_PRICING, 1, provider="anthropic", model="m")
            reservation = ledger.reserve(10, 10, "a" * 64)
            with self.assertRaises(ValidationError):
                ledger.report(reservation, {"input_tokens": None, "output_tokens": 3}, None)


class AnthropicTransportTests(unittest.TestCase):
    def make(self, temp, replies, cap=0.05):
        ledger = CostLedger(Path(temp) / "cost-ledger.jsonl", HAIKU_PRICING, cap, provider="anthropic", model="claude-haiku-5-5")
        fake = SimpleNamespace(messages=FakeMessages(replies))
        with env():
            transport = AnthropicTransport(ledger, "external_api", client=fake, require_container=False,
                                           transform=lambda schema: {**schema, "additionalProperties": False})
        return transport, fake.messages, ledger

    def config(self):
        return {**hosted_config(), "max_input_tokens": 2000, "max_output_tokens": 256}

    def test_request_shape_usage_and_no_credential_in_outputs(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        with tempfile.TemporaryDirectory() as temp, env():
            transport, fake, ledger = self.make(temp, [message()])
            result = asyncio.run(transport("Reply ok.", schema, self.config()))
            body = fake.bodies[0]
            self.assertNotIn("temperature", body)
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["output_config"]["effort"], "low")
            self.assertEqual(body["output_config"]["format"]["schema"]["additionalProperties"], False)
            self.assertEqual((result["model"], result["revision"], result["text"]), ("claude-haiku-5-5", "claude-haiku-5-5", '{"ok": true}'))
            self.assertEqual(result["usage"]["request_id"], "req_fake_1")
            self.assertAlmostEqual(result["cost"], 212 * 0.10e-6 + 8 * 0.50e-6)
            # Reservation: (2000 + 1024 overhead) x 1.25 x input + 256 x output.
            reserved = json.loads(ledger.path.read_text().splitlines()[0])
            self.assertAlmostEqual(reserved["projected_usd"], 3024 * 1.25 * 0.10e-6 + 256 * 0.50e-6)
            outputs = ledger.path.read_text() + json.dumps(result)
            self.assertNotIn(FAKE_KEY, outputs)
            self.assertNotIn("x" * 40, outputs)

    def test_refusal_truncation_and_model_mismatch_are_errors_but_billed(self):
        for reply, reason in ((message(stop="refusal"), "provider-stop-refusal"),
                              (message(stop="max_tokens"), "provider-stop-max_tokens"),
                              (message(model="claude-sonnet-5-5"), "provider-response-model")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                transport, _, ledger = self.make(temp, [reply])
                with self.assertRaises(ValidationError) as caught:
                    asyncio.run(transport("p", {"type": "object"}, self.config()))
                self.assertEqual(str(caught.exception), reason)
                self.assertEqual(json.loads(ledger.path.read_text().splitlines()[-1])["event"], "reported")

    def test_request_failure_keeps_reservation_and_hides_detail(self):
        with tempfile.TemporaryDirectory() as temp, env():
            transport, _, ledger = self.make(temp, [ConnectionError("upstream said " + FAKE_KEY)])
            with self.assertRaises(RuntimeError) as caught:
                asyncio.run(transport("p", {"type": "object"}, self.config()))
            self.assertNotIn(FAKE_KEY, str(caught.exception))
            self.assertNotIn(FAKE_KEY, ledger.path.read_text())
            self.assertEqual(json.loads(ledger.path.read_text().splitlines()[-1])["event"], "failed")

    def test_spend_cap_stops_before_the_call(self):
        with tempfile.TemporaryDirectory() as temp:
            transport, fake, _ = self.make(temp, [message()], cap=0.0001)
            with self.assertRaises(SpendStop):
                asyncio.run(transport("p", {"type": "object"}, self.config()))
            self.assertEqual(fake.bodies, [])


class OpenAITransportTests(unittest.TestCase):
    def make(self, temp, replies):
        ledger = CostLedger(Path(temp) / "cost-ledger.jsonl", HAIKU_PRICING, 0.05, provider="openai", model="gpt-6-luna")
        fake = SimpleNamespace(responses=FakeMessages(replies))
        with env():
            transport = OpenAITransport(ledger, "external_api", client=fake, require_container=False)
        return transport, fake.responses, ledger

    def reply(self, status="completed", model="gpt-6-luna-2026-09-01"):
        usage = SimpleNamespace(input_tokens=300, output_tokens=10, input_tokens_details=SimpleNamespace(cached_tokens=100))
        return SimpleNamespace(model=model, status=status, usage=usage, id="resp_1", _request_id="req_o1",
                               output_text='{"ok": true}', service_tier="default")

    def test_request_shape_cached_usage_and_status(self):
        config = {**hosted_config("openai"), "max_input_tokens": 2000, "max_output_tokens": 256}
        with tempfile.TemporaryDirectory() as temp:
            transport, fake, _ = self.make(temp, [self.reply()])
            result = asyncio.run(transport("p", {"type": "object"}, config))
            body = fake.bodies[0]
            self.assertEqual(body["text"]["format"]["type"], "json_schema")
            self.assertIs(body["store"], False)
            self.assertNotIn("temperature", body)
            self.assertEqual((result["usage"]["input_tokens"], result["usage"]["cache_read_tokens"]), (200, 100))
            self.assertAlmostEqual(result["cost"], 200 * 0.10e-6 + 100 * 0.10e-6 * 0.1 + 10 * 0.50e-6)
        for reply, reason in ((self.reply(status="incomplete"), "provider-status-incomplete"),
                              (self.reply(model="gpt-6-lunar"), "provider-response-model")):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as temp:
                transport, _, _ = self.make(temp, [reply])
                with self.assertRaises(ValidationError) as caught:
                    asyncio.run(transport("p", {"type": "object"}, config))
                self.assertEqual(str(caught.exception), reason)


@unittest.skipUnless(os.environ.get("RA_RAGAS_SMOKE") == "1", "optional Docker Ragas API suite")
class RagasOnAnthropicTests(unittest.TestCase):
    def test_published_metrics_through_the_anthropic_transport(self):
        from anthropic import transform_schema
        from retrieval_arena.ragas_judge import RagasJudge
        from retrieval_arena.ragas_scoring import score_batch
        from unit_tests.ragas_fixture import transport as fixture

        schemas = []

        def answer(body):
            # The fixture oracle answers by schema shape; the sent schema is SDK-transformed.
            schemas.append(body["output_config"]["format"]["schema"])
            prompt = body["messages"][0]["content"]
            step = fixture(prompt, body["output_config"]["format"]["schema"], {"model": "m", "revision": "r"})
            try:
                step.send(None)  # the fixture never awaits; it finishes on the first step
            except StopIteration as done:
                reply = done.value
            return message(text=reply["text"], input_tokens=900, output_tokens=60)

        batch, rubrics, _ = inputs_v3()
        config = hosted_config()
        with tempfile.TemporaryDirectory() as temp, env():
            ledger = CostLedger(Path(temp) / "cost-ledger.jsonl", HAIKU_PRICING, 0.05, provider="anthropic", model="claude-haiku-5-5")
            fake = SimpleNamespace(messages=FakeMessages([answer] * 64))
            transport = AnthropicTransport(ledger, "external_api", client=fake, require_container=False, transform=transform_schema)
            judge = RagasJudge(transport, Path(__file__).resolve().parents[1] / "containers/ragas/requirements.lock")
            result = asyncio.run(score_batch(batch, rubrics, config, judge))
            # Provider-neutral: the same oracle behind the bare fixture transport scores identically.
            direct = asyncio.run(score_batch(batch, rubrics, config, RagasJudge(
                fixture, Path(__file__).resolve().parents[1] / "containers/ragas/requirements.lock")))
        metrics = ("faithfulness", "context_recall", "factual_correctness")
        through = [[r["status"]] + [r[m] for m in metrics] for r in result["records"]]
        self.assertEqual(through, [[r["status"]] + [r[m] for m in metrics] for r in direct["records"]])
        self.assertIn({"status": "ok", "value": 1.0}, [row[1] for row in through])
        self.assertFalse({"parse_error", "judge_error"} & {row[i]["status"] for row in through for i in (1, 2, 3)})
        self.assertTrue(all(s.get("additionalProperties") is False for s in schemas))
        costs = [m["value"] for r in result["records"] for k, m in r["measurements"].items()
                 if k == "cost" and m["status"] == "available"]
        self.assertTrue(costs and all(isinstance(c, float) for values in costs for c in values))
        self.assertNotIn(FAKE_KEY, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
