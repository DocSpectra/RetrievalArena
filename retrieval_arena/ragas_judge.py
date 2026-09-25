"""Optional Ragas 0.4.3 bridge. No built-in network transport or credentials.

An admitted caller supplies an async structured-output transport; see the metric
reference for its response contract and the explicit live-execution boundary.
"""
from __future__ import annotations

import asyncio
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import re
import time

from .ragas_scoring import (
    PREFIX, VERDICTS, aggregate, canonical, fields, new_record, require,
    unavailable, validate_config, validate_verdicts,
)


class JudgeFailure(Exception):
    pass


class ParseFailure(Exception):
    pass


def resolve_quoted_evidence(detail, supplied_text):
    """Project v2 exact quotes to deterministic first-occurrence Unicode spans.

    Quotes are model judgments and remain in raw attempts. Offsets are derived
    locally because local structured-output models miscounted them in the pilot.
    An absent/nonmatching quote is a parse failure, never a measured zero.
    """
    evidence = detail.get("evidence")
    if not isinstance(evidence, list):
        raise ParseFailure("evidence-list-required")
    spans = []
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"text"} or not isinstance(item["text"], str) or not item["text"]:
            raise ParseFailure("evidence-quote-required")
        quote = item["text"]
        start = supplied_text.find(quote)
        if start < 0:
            raise ParseFailure("evidence-quote-not-found")
        spans.append({"start": start, "end": start + len(quote), "text": quote})
    return {**detail, "evidence": spans}


def engine_identity(lock_path):
    """Fail if the optional environment drifts from the full distribution lock."""
    lock = Path(lock_path).read_bytes()
    rows = re.findall(rb"^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([a-f0-9]{64})$", lock, re.M)
    require(bool(rows), "dependency-lock-empty")
    for name, pinned, _ in rows:
        require(version(name.decode()) == pinned.decode(), "dependency-version-mismatch:" + name.decode())
    require(version("ragas") == "0.4.3", "ragas-version")
    source = b"".join((Path(__file__).with_name(name)).read_bytes() for name in ("ragas_scoring.py", "ragas_judge.py", "ragas_ollama.py"))
    return {"name": "ragas", "version": "0.4.3", "dependency_lock_sha256": hashlib.sha256(lock).hexdigest(),
            "adapter_sha256": hashlib.sha256(source).hexdigest()}


class RagasJudge:
    """Use DiscreteMetric per frozen element and optional built-in Faithfulness.

    transport(prompt, response_schema, settings) must be async, enforce admission,
    and return text/model/revision/usage/cost. No automatic provider selection.
    The batch API is sequential; instantiate a separate judge per concurrent batch.
    """

    def __init__(self, transport, lock_path):
        self.engine = engine_identity(lock_path)
        # Disable framework analytics BEFORE its first import; no unsolicited network.
        os.environ["RAGAS_DO_NOT_TRACK"] = "true"
        from ragas.llms.base import InstructorBaseRagasLLM
        from ragas.metrics import DiscreteMetric
        from ragas.metrics.collections import Faithfulness

        self.transport = transport
        self.discrete_type = DiscreteMetric
        self.faithfulness_type = Faithfulness
        owner = self

        class CapturingLLM(InstructorBaseRagasLLM):
            def generate(self, prompt, response_model):
                raise RuntimeError("ragas-use-async-interface")

            async def agenerate(self, prompt, response_model):
                return await owner._generate(prompt, response_model)

        self.llm = CapturingLLM()

    async def _generate(self, prompt, response_model):
        # Include structured-output schema in the conservative input budget.
        schema = response_model.model_json_schema()
        if len(prompt.encode("utf-8")) + len(canonical(schema)) > self.config["max_input_tokens"]:
            raise JudgeFailure("input-budget-exceeded")
        for attempt in range(self.config["retries"] + 1):
            started = time.monotonic()
            trace = {"prompt": prompt, "response_schema": schema, "attempt": attempt + 1,
                     "response": None, "error": None}
            self.raw.append(trace)
            try:
                response = await asyncio.wait_for(
                    self.transport(prompt, schema, dict(self.config)), timeout=self.config["timeout_seconds"])
                trace["response"] = response
                fields(response, "text model revision usage cost")
                require(response["model"] == self.config["model"] and response["revision"] == self.config["revision"], "observed-model-identity-mismatch")
                require(isinstance(response["text"], str), "raw-response-text")
                # Token bytes can exceed tokens; transport must enforce max_output_tokens.
                # Also bound response memory without silently truncating it.
                require(len(response["text"].encode("utf-8")) <= 64 * self.config["max_output_tokens"], "response-too-large")
                try:
                    parsed = json.loads(response["text"])
                    require(isinstance(parsed, dict) and set(parsed) == set(response_model.model_fields), "response-fields")
                    return response_model.model_validate(parsed, strict=True)
                except Exception as exc:
                    # Schema/JSON errors never become measured zero.
                    raise ParseFailure(type(exc).__name__) from exc
            except ParseFailure:
                trace["error"] = "parse_error"
                raise
            except Exception as exc:
                trace["error"] = type(exc).__name__
                if attempt == self.config["retries"]:
                    raise JudgeFailure(type(exc).__name__) from exc
            finally:
                trace["latency_seconds"] = time.monotonic() - started

    async def score(self, row, rubric, corpus, config, prompts):
        validate_config(config)
        self.config = dict(config)
        self.raw = []
        record = new_record(row, rubric, corpus, config, self.engine)
        started = time.monotonic()
        try:
            verdicts = []
            for element, prompt in zip(rubric["elements"], prompts):
                metric = self.discrete_type(name="required_element_completeness_v1", prompt="{input}", allowed_values=list(VERDICTS))
                scored = await metric.ascore(llm=self.llm, input=prompt)
                try:
                    detail = json.loads(scored.reason)
                    fields(detail, "reason evidence")
                    if config["answer_prompt"].endswith(".v2"):
                        detail = resolve_quoted_evidence(detail, row["text"])
                    verdicts.append({"schema_version": PREFIX + "element_verdict.v1", "element_id": element["element_id"],
                                     "verdict": scored.value, **detail})
                except Exception as exc:
                    raise ParseFailure(type(exc).__name__) from exc
            try:
                validate_verdicts(verdicts, row, rubric["elements"])
            except Exception as exc:
                raise ParseFailure(type(exc).__name__) from exc
            record["verdicts"] = verdicts
            record["aggregate"] = aggregate(verdicts, row, rubric["elements"])
        except (JudgeFailure, ParseFailure) as exc:
            record.update(status="parse_error" if isinstance(exc, ParseFailure) else "judge_error", error=str(exc))
        # Separate availability: failure here never overwrites completeness.
        if record["faithfulness"]["status"] == "unavailable":
            try:
                chunks = {(c["document_id"], c["chunk_id"]): c["text"] for c in corpus["chunks"]}
                contexts = [chunks[(c["document_id"], c["chunk_id"])] for c in row["contexts"]]
                result = await self.faithfulness_type(llm=self.llm).ascore(
                    user_input=row["question"], response=row["text"], retrieved_contexts=contexts)
                record["faithfulness"] = {"status": "ok", "value": float(result.value)} if math.isfinite(result.value) else unavailable("no_statements")
            except ParseFailure:
                record["faithfulness"] = unavailable("parse_error")
            except Exception:
                record["faithfulness"] = unavailable("judge_error")
        record["raw"] = self.raw
        record["measurements"]["latency_seconds"] = {"status": "available", "value": time.monotonic() - started}
        for field in ("usage", "cost"):
            values = [t["response"].get(field) if isinstance(t["response"], dict) else None for t in self.raw]
            if values and all(v is not None for v in values):
                record["measurements"][field] = {"status": "available", "value": values}
        return record
