import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from retrieval_arena.errors import ValidationError
from retrieval_arena.ragas_scoring import (
    PROMPTS, aggregate, digest, prompt_for, replay, score_batch, scoring_identity,
    export_legacy_predictions, validate_config, validate_inputs, validate_verdicts,
)
from unit_tests.ragas_fixture import FakeJudge, inputs, inputs_v3


class RagasScoringTests(unittest.TestCase):
    def setUp(self):
        self.batch, self.rubrics, self.config = inputs()

    def score(self, **kwargs):
        return asyncio.run(score_batch(self.batch, self.rubrics, self.config, kwargs.pop("judge", FakeJudge()), **kwargs))

    def test_fixed_denominator_omission_contradiction_padding(self):
        result = self.score()
        records = {r["unit"][1]: r for r in result["records"][:7]}
        for condition, score in {"complete": 1, "omission": .5, "empty": 0, "contradiction": .5, "paraphrase": 1, "padding": 1}.items():
            self.assertEqual(records[condition]["aggregate"]["completeness"], score)
            self.assertEqual(records[condition]["aggregate"]["denominator"], 2)
        self.assertEqual(records["contradiction"]["aggregate"]["counts"]["contradicted"], 1)
        self.assertEqual(records["absent"]["status"], "missing_output")
        self.assertIsNone(records["absent"]["aggregate"])
        self.assertFalse(result["summary"][3]["complete_coverage"])

    def test_replay_and_tamper_refusal(self):
        result = self.score()
        self.assertEqual(replay(self.batch, self.rubrics, result), result)
        result["records"][0]["aggregate"]["completeness"] = 0
        result["sha256"] = digest({k: v for k, v in result.items() if k != "sha256"})
        with self.assertRaises(ValidationError):
            replay(self.batch, self.rubrics, result)

    def test_duplicate_unknown_missing_elements(self):
        row = self.batch["predictions"][0]
        elements = self.rubrics["questions"][0]["elements"]
        good = self.score()["records"][0]["verdicts"]
        for bad in [good + good[:1], good[:1], [{**good[0], "element_id": "unknown"}, good[1]]]:
            with self.assertRaises(ValidationError):
                validate_verdicts(bad, row, elements)

    def test_span_refusal(self):
        good = self.score()["records"][0]["verdicts"]
        good[0]["evidence"][0]["text"] = "invented"
        with self.assertRaises(ValidationError):
            aggregate(good, self.batch["predictions"][0], self.rubrics["questions"][0]["elements"])

    def test_join_and_duplicate_refusals(self):
        mutations = [
            lambda b: b["predictions"].append(copy.deepcopy(b["predictions"][0])),
            lambda b: b["predictions"][0].update(question_id="unknown"),
            lambda b: b["predictions"][0].update(corpus_sha256="0" * 64),
            lambda b: b["predictions"][0]["contexts"][0].update(sha256="0" * 64),
            lambda b: b["predictions"][0]["contexts"][0].update(chunk_id="unknown"),
            lambda b: b["predictions"][-1].update(text="fabricated context"),
        ]
        for mutate in mutations:
            b = copy.deepcopy(self.batch)
            mutate(b)
            with self.assertRaises(ValidationError):
                validate_inputs(b, self.rubrics)

    def test_empty_rubric_and_identity_refusal(self):
        self.rubrics["questions"][0]["elements"] = []
        with self.assertRaises(ValidationError):
            self.score()
        self.config["model"] = ""
        with self.assertRaises(ValidationError):
            validate_config(self.config)

    def test_answer_prompt_has_no_context_or_source(self):
        row = copy.deepcopy(self.batch["predictions"][1])
        row["contexts"] = [{"secret": "do not reveal"}]
        element = copy.deepcopy(self.rubrics["questions"][0]["elements"][0])
        element["sources"] = [{"secret": "rubric-source-sentinel"}]
        prompt = prompt_for(row, element, self.config)
        self.assertNotIn("do not reveal", prompt)
        self.assertNotIn("rubric-source-sentinel", prompt)
        self.assertNotEqual(PROMPTS["answer_satisfaction.v1"], PROMPTS["context_support.v1"])

    def test_v2_nested_reason_prompt_is_versioned_and_keeps_answer_isolation(self):
        self.config["answer_prompt"] = "answer_satisfaction.v2"
        self.config["context_prompt"] = "context_support.v2"
        validate_config(self.config)
        row = copy.deepcopy(self.batch["predictions"][1])
        row["contexts"] = [{"secret": "context-sentinel"}]
        element = copy.deepcopy(self.rubrics["questions"][0]["elements"][0])
        element["sources"] = [{"secret": "source-sentinel"}]
        prompt = prompt_for(row, element, self.config)
        self.assertIn("serialized JSON object STRING", prompt)
        self.assertIn("Each evidence object has ONLY text", prompt)
        self.assertNotIn("context-sentinel", prompt)
        self.assertNotIn("source-sentinel", prompt)
        self.config["context_prompt"] = "context_support.v1"
        with self.assertRaises(ValidationError):
            validate_config(self.config)

    def test_v3_prompt_requires_all_clauses_and_preserves_answer_isolation(self):
        self.config["answer_prompt"] = "answer_satisfaction.v3"
        self.config["context_prompt"] = "context_support.v3"
        validate_config(self.config)
        row = copy.deepcopy(self.batch["predictions"][1])
        row["contexts"] = [{"secret": "context-sentinel"}]
        element = copy.deepcopy(self.rubrics["questions"][0]["elements"][0])
        element["sources"] = [{"secret": "source-sentinel"}]
        prompt = prompt_for(row, element, self.config)
        self.assertIn("EVERY mandatory fact", prompt)
        self.assertIn("silence is missing, not contradiction", prompt)
        self.assertIn("serialized JSON object STRING", prompt)
        self.assertNotIn("context-sentinel", prompt)
        self.assertNotIn("source-sentinel", prompt)
        self.config["context_prompt"] = "context_support.v2"
        with self.assertRaises(ValidationError):
            validate_config(self.config)

    def test_v4_string_quote_prompt_and_span_projection(self):
        from retrieval_arena.ragas_judge import ParseFailure, resolve_string_evidence
        self.config["answer_prompt"] = "answer_satisfaction.v4"
        self.config["context_prompt"] = "context_support.v4"
        validate_config(self.config)
        row = copy.deepcopy(self.batch["predictions"][1])
        row["contexts"] = [{"secret": "context-sentinel"}]
        element = copy.deepcopy(self.rubrics["questions"][0]["elements"][0])
        element["sources"] = [{"secret": "source-sentinel"}]
        prompt = prompt_for(row, element, self.config)
        self.assertIn("EVERY mandatory fact", prompt)
        self.assertIn("list of exact quote STRINGS", prompt)
        self.assertNotIn("context-sentinel", prompt)
        self.assertNotIn("source-sentinel", prompt)
        self.assertEqual(resolve_string_evidence({"reason": "quoted", "evidence": ["éx"]}, "aéxéx")["evidence"],
                         [{"start": 1, "end": 3, "text": "éx"}])
        with self.assertRaises(ParseFailure):
            resolve_string_evidence({"reason": "bad", "evidence": [{"text": "éx"}]}, "aéx")
        with self.assertRaises(ParseFailure):
            resolve_string_evidence({"reason": "bad", "evidence": ["absent"]}, "aéx")

    def test_v2_exact_quote_offsets_are_derived_and_bad_quotes_fail(self):
        from retrieval_arena.ragas_judge import ParseFailure, resolve_quoted_evidence
        detail = {"reason": "quoted", "evidence": [{"text": "éx"}]}
        self.assertEqual(resolve_quoted_evidence(detail, "aéxéx")["evidence"],
                         [{"start": 1, "end": 3, "text": "éx"}])
        with self.assertRaises(ParseFailure):
            resolve_quoted_evidence({"reason": "wrong", "evidence": [{"text": "missing"}]}, "aéxéx")
        with self.assertRaises(ParseFailure):
            resolve_quoted_evidence({"reason": "wrong", "evidence": [{"text": "éx", "end": 3}]}, "aéxéx")

    def test_excessive_input_is_failure_and_never_calls_judge(self):
        self.config["max_input_tokens"] = 1
        judge = FakeJudge()
        result = self.score(judge=judge)
        self.assertEqual(judge.calls, 0)
        self.assertEqual(result["records"][0]["status"], "input_too_large")
        self.assertIsNone(result["records"][0]["aggregate"])

    def test_cache_repeats_invalidation_and_no_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            judge = FakeJudge()
            a = self.score(judge=judge, cache_dir=temp)
            self.assertEqual(judge.calls, 7)
            b = self.score(judge=judge, cache_dir=temp)
            self.assertEqual(a, b)
            self.assertEqual(judge.calls, 7)
            self.score(judge=judge, cache_dir=temp, use_cache=False)
            self.assertEqual(judge.calls, 14)
            self.config["seed"] += 1
            self.score(judge=judge, cache_dir=temp)
            self.assertEqual(judge.calls, 21)

    def test_full_identity_changes(self):
        row = self.batch["predictions"][0]
        rubric = self.rubrics["questions"][0]
        corpus = self.batch["corpus"]
        baseline = scoring_identity(row, rubric, corpus, self.config, FakeJudge.engine)
        for field, value in [("model", "other"), ("revision", "v2"), ("temperature", .1), ("max_output_tokens", 512), ("retries", 1)]:
            changed = {**self.config, field: value}
            self.assertNotEqual(baseline, scoring_identity(row, rubric, corpus, changed, FakeJudge.engine))
        for changed in [{**row, "text": "different"}, {**row, "contexts": []}]:
            self.assertNotEqual(baseline, scoring_identity(changed, rubric, corpus, self.config, FakeJudge.engine))
        altered = copy.deepcopy(rubric)
        altered["elements"][0]["acceptance"] = "Other criteria"
        self.assertNotEqual(baseline, scoring_identity(row, altered, corpus, self.config, FakeJudge.engine))

    def test_predictions_unchanged_and_checkpoint(self):
        before = copy.deepcopy(self.batch)
        checkpoints = []
        self.score(checkpoint=checkpoints.append)
        self.assertEqual(self.batch, before)
        self.assertEqual([len(c) for c in checkpoints], list(range(1, 10)))

    def test_declared_missing_unit_refused(self):
        self.batch["predictions"].pop()
        with self.assertRaises(ValidationError):
            self.score()

    def test_export_preserves_answer_bytes_and_refuses_bad_context(self):
        prediction = self.batch["predictions"][0]
        manifest = {"schema_version": "retrieval_arena.ragas_export_manifest.v1", "corpus": self.batch["corpus"],
                    "questions": [{"question_id": "q1", "question": prediction["question"]}],
                    "condition_id": "export", "run_id": "r1", "context_refs": {"q1": prediction["contexts"]}}
        legacy = {"question_id": "q1", "question": prediction["question"], "generated_answer": "  Raw answer\n", "retrieved_context": [
            {"doc_id": "manual", "text": self.batch["corpus"]["chunks"][0]["text"], "rank": 1, "score": 1.0}]}
        raw = json.dumps(legacy).encode()
        batch = export_legacy_predictions(raw, manifest)
        self.assertEqual(batch["predictions"][0]["text"], "  Raw answer\n")
        validate_inputs(batch, self.rubrics)
        legacy["retrieved_context"][0]["text"] = "changed"
        with self.assertRaises(ValidationError):
            export_legacy_predictions(json.dumps(legacy).encode(), manifest)
        manifest["context_refs"]["q1"] = []
        absent = export_legacy_predictions(b"", manifest)
        self.assertIsNone(absent["predictions"][0]["text"])

    def test_ollama_digest_checks_before_and_after_call(self):
        from retrieval_arena.ragas_ollama import OllamaTransport
        with patch.dict("os.environ", {"RA_RAGAS_NETWORK": "host_model_alias"}), patch("os.path.isfile", return_value=True):
            client = OllamaTransport()
        config = {**self.config, "provider": "ollama", "model": "model:v1", "revision": "a" * 64}
        tags = {"models": [{"name": "model:v1", "digest": "a" * 64}]}
        response = {"model": "model:v1", "message": {"content": "{}"}, "done": True, "done_reason": "stop", "eval_count": 4}
        client.request = AsyncMock(side_effect=[tags, response, tags])
        result = asyncio.run(client("prompt", {}, config))
        self.assertEqual(result["revision"], "a" * 64)
        self.assertEqual([c.args[0] for c in client.request.call_args_list], ["/api/tags", "/api/chat", "/api/tags"])
        self.assertIs(client.request.call_args_list[1].args[2]["think"], False)
        config = {**config, "schema_version": "retrieval_arena.ragas_judge_config.v2", "thinking": "low"}
        validate_config(config)
        client.request = AsyncMock(side_effect=[tags, response, tags])
        asyncio.run(client("prompt", {}, config))
        self.assertEqual(client.request.call_args_list[1].args[2]["think"], "low")
        for invalid in (True, "xhigh", 0):
            with self.assertRaises(ValidationError):
                validate_config({**config, "thinking": invalid})
        client.request = AsyncMock(return_value={"models": [{"name": "model:v1", "digest": "b" * 64}]})
        with self.assertRaises(ValidationError):
            asyncio.run(client("prompt", {}, config))
        self.assertEqual(client.request.call_count, 1)

    def test_v3_published_metrics_policy_and_replay(self):
        batch, rubrics, config = inputs_v3()
        judge = FakeJudge()
        result = asyncio.run(score_batch(batch, rubrics, config, judge))
        self.assertEqual(result["schema_version"], "retrieval_arena.ragas_scores.v2")
        records = {(r["unit"][1], r["unit"][3]): r for r in result["records"]}
        complete = records[("complete", "answer")]
        self.assertEqual((complete["schema_version"], complete["status"], complete["aggregate"]),
                         ("retrieval_arena.ragas_judgment.v2", "disabled", None))
        for name in ("faithfulness", "context_recall", "factual_correctness"):
            self.assertEqual(complete[name], {"status": "ok", "value": 0.5})
            self.assertEqual(records[("empty", "answer")][name]["status"], "empty_answer")
            self.assertEqual(records[("absent", "answer")][name]["status"], "missing_output")
            self.assertEqual(records[("contexts", "final_context")][name]["status"], "not_applicable")
        # Five non-empty answers are judged; empty, absent and context stages are not.
        self.assertEqual(judge.calls, 5)
        group = next(g for g in result["summary"] if g["condition_id"] == "complete")
        self.assertIsNone(group["complete_coverage"])
        self.assertEqual(group["failures"], {})
        self.assertEqual(group["metrics"]["context_recall"], {"scored": 1, "statuses": {"ok": 1}, "macro_observed": 0.5})
        self.assertEqual(replay(batch, rubrics, result), result)
        tampered = copy.deepcopy(result)
        tampered["records"][0]["context_recall"]["value"] = 0.9
        tampered["sha256"] = digest({k: v for k, v in tampered.items() if k != "sha256"})
        with self.assertRaises(ValidationError):
            replay(batch, rubrics, tampered)

    def test_v3_config_and_pairing_refusals(self):
        batch, rubrics, config = inputs_v3()
        validate_config(config)
        for key, value in [("mode", "accuracy"), ("atomicity", "medium"), ("coverage", 1), ("beta", 0)]:
            with self.assertRaises(ValidationError):
                validate_config({**config, "factual_correctness": {**config["factual_correctness"], key: value}})
        with self.assertRaises(ValidationError):
            validate_config({**config, "context_recall": 1})
        bad = copy.deepcopy(rubrics)
        bad["questions"][0]["reference"] = None
        with self.assertRaises(ValidationError):
            asyncio.run(score_batch(batch, bad, config, FakeJudge()))
        # The element metric still needs elements when it is on.
        with self.assertRaises(ValidationError):
            asyncio.run(score_batch(batch, rubrics, {**config, "element_completeness": True}, FakeJudge()))
        # A v1 config never accepts a rubric without elements.
        v1 = inputs()[2]
        with self.assertRaises(ValidationError):
            asyncio.run(score_batch(batch, rubrics, v1, FakeJudge()))

    def test_v3_with_elements_keeps_completeness(self):
        batch, rubrics, config = inputs_v3(element_completeness=True)
        result = asyncio.run(score_batch(batch, rubrics, config, FakeJudge()))
        first = result["records"][0]
        self.assertEqual((first["status"], first["aggregate"]["completeness"]), ("ok", 1))
        self.assertEqual(first["factual_correctness"], {"status": "ok", "value": 0.5})
        self.assertEqual(replay(batch, rubrics, result), result)

    def test_v3_cache_skips_only_complete_metrics(self):
        batch, rubrics, config = inputs_v3()
        with tempfile.TemporaryDirectory() as temp:
            judge = FakeJudge()
            first = asyncio.run(score_batch(batch, rubrics, config, judge, cache_dir=temp))
            second = asyncio.run(score_batch(batch, rubrics, config, judge, cache_dir=temp))
            self.assertEqual(first, second)
            self.assertEqual(judge.calls, 5)

    def test_ollama_network_gate(self):
        from retrieval_arena.ragas_ollama import OllamaTransport
        with patch.dict("os.environ", {"RA_RAGAS_NETWORK": "none"}):
            with self.assertRaises(ValidationError):
                OllamaTransport()


if __name__ == "__main__":
    unittest.main()
