"""Executed in the pinned optional image, not required by the base RA suite."""
import asyncio
import copy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from retrieval_arena.ragas_scoring import replay, score_batch
from unit_tests.ragas_fixture import inputs, transport


@unittest.skipUnless(os.environ.get("RA_RAGAS_SMOKE") == "1", "optional Docker Ragas API suite")
class RagasAPITests(unittest.TestCase):
    def setUp(self):
        from retrieval_arena.ragas_judge import RagasJudge
        self.batch, self.rubrics, self.config = inputs()
        self.lock = Path(__file__).resolve().parents[1] / "containers/ragas/requirements.lock"
        self.judge = RagasJudge(transport, self.lock)

    def score(self):
        # The real framework imports and APIs run; credentials/network never do.
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            return asyncio.run(score_batch(self.batch, self.rubrics, self.config, self.judge))

    def test_real_discrete_api_all_fixture_rows(self):
        result = self.score()
        self.assertEqual(result["engine"]["version"], "0.4.3")
        self.assertEqual([r["aggregate"]["completeness"] if r["aggregate"] else None for r in result["records"]],
                         [1, .5, 0, None, .5, 1, 1, 1, 1])
        self.assertEqual(replay(self.batch, self.rubrics, result), result)
        self.assertTrue(result["records"][0]["raw"])

    def test_real_faithfulness_api_and_empty_policy(self):
        self.config["faithfulness"] = True
        self.batch["predictions"][1]["contexts"] = []
        result = self.score()
        self.assertEqual(result["records"][0]["faithfulness"], {"status": "ok", "value": 1.0})
        self.assertEqual(result["records"][1]["faithfulness"]["status"], "empty_context")
        self.assertEqual(result["records"][2]["faithfulness"]["status"], "empty_answer")
        self.assertEqual(result["records"][7]["faithfulness"]["status"], "not_applicable")

    def test_real_context_recall_and_factual_correctness_apis(self):
        from unit_tests.ragas_fixture import inputs_v3
        self.batch, self.rubrics, self.config = inputs_v3()
        result = self.score()
        first = result["records"][0]
        self.assertEqual(first["status"], "disabled")
        # Hand-coded fake responses: 1 of 2 reference statements attributed; all claims verified.
        self.assertEqual(first["context_recall"], {"status": "ok", "value": 0.5})
        self.assertEqual(first["factual_correctness"], {"status": "ok", "value": 1.0})
        self.assertEqual(first["faithfulness"], {"status": "ok", "value": 1.0})
        self.assertEqual(replay(self.batch, self.rubrics, result), result)

        async def broken(prompt, schema, config):
            response = await transport(prompt, schema, config)
            if set(schema["properties"]) == {"claims"}:
                response["text"] = "not json"
            return response
        self.judge.transport = broken
        result = self.score()
        first = result["records"][0]
        self.assertEqual(first["factual_correctness"]["status"], "parse_error")
        self.assertEqual(first["context_recall"]["status"], "ok")

    def test_parse_and_identity_failures(self):
        for failure, expected in [("parse", "parse_error"), ("identity", "judge_error")]:
            async def broken(prompt, schema, config):
                result = await transport(prompt, schema, config)
                if failure == "parse":
                    result["text"] = "not json"
                else:
                    result["revision"] = "different-model"
                return result
            self.judge.transport = broken
            result = self.score()
            self.assertEqual(result["records"][0]["status"], expected)
            self.assertIsNone(result["records"][0]["aggregate"])
            self.assertIsNotNone(result["records"][0]["raw"][0]["response"])

    def test_retry_and_faithfulness_failure_separation(self):
        self.config["faithfulness"] = True
        self.config["retries"] = 1
        calls = []

        async def flaky(prompt, schema, config):
            calls.append(prompt)
            if len(calls) == 1 or set(schema["properties"]) == {"statements"}:
                raise TimeoutError("mock timeout")
            return await transport(prompt, schema, config)

        self.judge.transport = flaky
        result = self.score()
        self.assertEqual(result["records"][0]["status"], "ok")
        self.assertEqual(result["records"][0]["faithfulness"]["status"], "judge_error")
        self.assertEqual(result["records"][0]["raw"][1]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
