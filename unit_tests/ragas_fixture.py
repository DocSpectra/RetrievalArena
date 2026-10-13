"""Synthetic hand-authored fixture, never independent scientific judgments."""
import copy
import json

from retrieval_arena.ragas_scoring import PREFIX, aggregate, digest, new_record, pending, text_hash


def inputs():
    text = "Enable feature. Check status."
    chunk = {"document_id": "manual", "chunk_id": "steps", "text": text, "sha256": text_hash(text)}
    corpus = {"corpus_id": "synthetic-v1", "chunks": [chunk]}
    corpus["sha256"] = digest(corpus)
    ref = {k: chunk[k] for k in ("document_id", "chunk_id", "sha256")}
    question = {"question_id": "q1", "question": "How do I enable and verify the feature?", "elements": [
        {"element_id": "enable", "requirement": "Enable feature", "acceptance": "State activation", "alternatives": ["Turn it on"], "sources": [ref]},
        {"element_id": "verify", "requirement": "Check status", "acceptance": "State verification", "alternatives": ["Verify status"], "sources": [ref]}]}
    rubrics = {"schema_version": PREFIX + "rubrics.v1", "corpus_sha256": corpus["sha256"],
               "provenance": {"authoring_method": "synthetic_hand_authored_fixture", "reviewer_id": "fixture-author",
                              "frozen_at": "2026-09-24", "independent_of_predictions": True}, "questions": [question]}
    predictions = []
    for condition, answer in [("complete", text), ("omission", "Enable feature."), ("empty", ""),
                              ("absent", None), ("contradiction", "Do not enable feature. Check status."),
                              ("paraphrase", "Turn it on. Verify status."), ("padding", text + " Unrelated. Unrelated.")]:
        predictions.append({"schema_version": PREFIX + "prediction.v1", "question_id": "q1", "question": question["question"],
                            "condition_id": condition, "run_id": "fixture-1", "stage": "answer", "corpus_sha256": corpus["sha256"],
                            "text": answer, "contexts": [ref], "source": {"artifact_sha256": text_hash("synthetic-predictions-v1"), "record_id": condition}})
    for stage in ("seed_context", "final_context"):
        row = copy.deepcopy(predictions[0])
        row.update(stage=stage, condition_id="contexts")
        predictions.append(row)
    config = {"schema_version": PREFIX + "judge_config.v1", "provider": "recorded_fixture", "model": "hand-authored-fixture",
              "revision": "fixture-v1", "temperature": 0, "seed": 7, "retries": 0, "timeout_seconds": 10,
              "max_output_tokens": 1024, "max_input_tokens": 16000, "answer_prompt": "answer_satisfaction.v1",
              "context_prompt": "context_support.v1", "faithfulness": False}
    return {"schema_version": PREFIX + "batch.v1", "corpus": corpus, "units": [[r[k] for k in ("question_id", "condition_id", "run_id", "stage")] for r in predictions], "predictions": predictions}, rubrics, config


def oracle(text, element_id):
    # Exact lookup expectations, not an omission/paraphrase recognition algorithm.
    if element_id == "enable":
        phrase = "Do not enable feature" if text.startswith("Do not") else "Turn it on" if text.startswith("Turn") else "Enable feature"
        verdict = "contradicted" if text.startswith("Do not") else "satisfied"
    else:
        phrase, verdict = ("Verify status" if "Verify status" in text else "Check status"), "satisfied"
    if phrase not in text:
        return {"value": "missing", "reason": json.dumps({"reason": "Fixture omission.", "evidence": []})}
    start = text.index(phrase)
    return {"value": verdict, "reason": json.dumps({"reason": "Hand-authored fixture expectation.",
            "evidence": [{"start": start, "end": start + len(phrase), "text": phrase}]})}


class FakeJudge:
    engine = {"name": "recorded_fixture", "version": "1", "dependency_lock_sha256": "0" * 64, "adapter_sha256": "0" * 64}

    def __init__(self):
        self.calls = 0

    async def score(self, row, rubric, corpus, config, prompts):
        self.calls += 1
        record = new_record(row, rubric, corpus, config, self.engine)
        for name in pending(record):
            # Fixed hand-coded published-metric values; v1/v2 fixtures leave none pending.
            record[name] = {"status": "ok", "value": 0.5}
        if prompts is None:
            return record
        for element in rubric["elements"]:
            raw = oracle(row["text"], element["element_id"])
            record["raw"].append(raw)
            record["verdicts"].append({"schema_version": PREFIX + "element_verdict.v1", "element_id": element["element_id"],
                                      "verdict": raw["value"], **json.loads(raw["reason"])})
        record["aggregate"] = aggregate(record["verdicts"], row, rubric["elements"])
        return record


async def transport(prompt, schema, config):
    """Credential-free mock below the real Ragas API; used only by Docker smoke."""
    if set(schema["properties"]) == {"reason", "value"}:
        payload = json.loads(prompt.split("\n", 1)[1])
        response = oracle(payload["text"], payload["element"]["element_id"])
    elif set(schema["properties"]) == {"classifications"}:
        # ContextRecall: one attributed and one unattributed reference statement.
        response = {"classifications": [{"statement": "Enable feature.", "reason": "Fixture support.", "attributed": 1},
                                        {"statement": "Restart the host.", "reason": "Fixture absence.", "attributed": 0}]}
    elif set(schema["properties"]) == {"claims"}:
        response = {"claims": ["Enable feature.", "Check status."]}
    elif set(schema["properties"]) == {"statements"}:
        # Faithfulness statement generation and NLI have distinct item types.
        if schema["properties"]["statements"].get("items", {}).get("type") == "string":
            response = {"statements": ["Enable feature.", "Check status."]}
        else:
            response = {"statements": [{"statement": "Enable feature.", "reason": "Fixture support.", "verdict": 1},
                                       {"statement": "Check status.", "reason": "Fixture support.", "verdict": 1}]}
    else:
        raise AssertionError("Unexpected Ragas response schema")
    return {"text": json.dumps(response), "model": config["model"], "revision": config["revision"], "usage": None, "cost": None}


def inputs_v3(element_completeness=False):
    """Config v3 / rubrics v2 over the same synthetic rows: published metrics on."""
    batch, rubrics, config = inputs()
    rubrics["schema_version"] = PREFIX + "rubrics.v2"
    for question in rubrics["questions"]:
        question["reference"] = "Enable feature. Check status."
        if not element_completeness:
            question["elements"] = []
    config.update(schema_version=PREFIX + "judge_config.v3", thinking=False, faithfulness=True,
                  element_completeness=element_completeness, context_recall=True,
                  factual_correctness={"mode": "f1", "beta": 1.0, "atomicity": "low", "coverage": "low"})
    return batch, rubrics, config
