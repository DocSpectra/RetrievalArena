"""Focused fake Responses checks for the portable model-turn boundary."""
import json
import unittest

from retrieval_arena.model_turn import ResponseTurn, TurnRequest, redact_error, request_payload, tools_for, validate_action


def fixture(*, calls=1, model="gpt-6-luna", status="completed", name="search", args=None, usage=True):
    args = {"query": "pump"} if args is None else args
    return {"id": "resp_fixture", "model": model, "status": status, "service_tier": "default",
            "output": [{"type": "function_call", "call_id": "call_1", "name": name,
                        "arguments": json.dumps(args)} for _ in range(calls)],
            "usage": {"input_tokens": 12, "output_tokens": 7} if usage else None}


class ModelTurnTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.raws = []
        self.request = TurnRequest([{"role": "user", "content": "Find pump"}], ["search"],
                                   "openai", "gpt-6-luna", "https://api.openai.com/v1/responses", 100)

    def turn(self, response):
        def create(**payload):
            self.assertFalse(payload["store"])
            self.assertEqual(payload["tool_choice"], "required")
            self.assertFalse(payload["parallel_tool_calls"])
            self.assertEqual(len(payload["tools"]), 1)
            return response
        return ResponseTurn(create, self.events.append, lambda raw: self.raws.append(raw) or "raw:1")

    def test_valid_action_and_nullable_usage(self):
        action, meta = self.turn(fixture(usage=False)).complete(self.request)
        self.assertEqual(action, {"action": "search", "query": "pump"})
        self.assertEqual(meta["call_id"], "call_1")
        self.assertIsNone(meta["usage"]["cached_input"])
        self.assertIsNone(meta["usage"]["input"])
        self.assertEqual(self.events[-1]["event"], "response")

    def test_preflight_and_generation_share_request_body(self):
        expected = request_payload(self.request)
        seen = []
        ResponseTurn(lambda **payload: seen.append(payload) or fixture(), self.events.append,
                     lambda raw: "raw:1").complete(self.request)
        self.assertEqual(seen, [expected])

    def test_read_and_follow_bounds_are_in_schema_and_local_validation(self):
        for action in ("read", "follow"):
            fields = tools_for([action])[0]["parameters"]["properties"]
            self.assertEqual(fields["start_line"]["minimum"], 1)
            self.assertEqual(fields["line_count"]["maximum"], 80)
            args = {"doc_id": "page", "start_line": 1, "line_count": 80}
            if action == "follow":
                args["source_doc_id"] = "source"
            self.assertEqual(validate_action(action, args, [action])["action"], action)
            with self.assertRaisesRegex(ValueError, "action-field-range"):
                validate_action(action, {**args, "line_count": 81}, [action])

    def test_multiple_zero_malformed_wrong_model_and_incomplete_rejected(self):
        for response in (fixture(calls=0), fixture(calls=2), fixture(args={"wrong": 1}),
                         fixture(model="other"), fixture(status="incomplete")):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    self.turn(response).complete(self.request)
        self.assertTrue(all(e["event"] == "failure_after_request" for e in self.events[1::2]))

    def test_refusal_extra_field_and_missing_call_id(self):
        for response in (fixture(name="answer"), fixture(args={"query": "x", "secret": "y"}),
                         {**fixture(), "output": [{"type": "function_call", "name": "search",
                           "arguments": '{"query":"x"}'}]},
                         {**fixture(), "output": [{"type": "message", "content": []}]}):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    self.turn(response).complete(self.request)

    def test_no_retry_after_possible_billing(self):
        calls = []
        def fail(**payload):
            calls.append(payload)
            raise TimeoutError("possibly billed")
        with self.assertRaises(TimeoutError):
            ResponseTurn(fail, self.events.append, self.raws.append).complete(self.request)
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.events[-1]["possible_billable"])

    def test_masked_key_fragment_redacted_from_error(self):
        value = "Incorrect API key provided: sk-proj-********abcd. invalid_api_key"
        self.assertEqual(redact_error(value), "Incorrect API key provided: [REDACTED_API_KEY] invalid_api_key")

    def test_ollama_does_not_claim_forced_tool_enforcement(self):
        request = TurnRequest(self.request.history, ["search"], "ollama", "qwen3:14b",
                              "http://127.0.0.1:11434/v1/responses", 100)
        response = fixture(model="qwen3:14b")
        captured = []
        turn = ResponseTurn(lambda **payload: captured.append(payload) or response,
                            self.events.append, lambda raw: "raw:2")
        _, meta = turn.complete(request)
        self.assertNotIn("tool_choice", captured[0])
        self.assertFalse(meta["provider_schema_enforcement_verified"])


if __name__ == "__main__":
    unittest.main()
