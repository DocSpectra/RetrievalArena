from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .errors import ValidationError


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"Invalid JSON in {path} line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValidationError(f"{path} line {line_number} must be a JSON object.")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def validate_dataset(dataset_path: Path) -> tuple[list[dict[str, str]], dict[str, str]]:
    required = [dataset_path / "corpus", dataset_path / "questions.jsonl", dataset_path / "answers.jsonl"]
    for path in required:
        if not path.exists():
            raise ValidationError(f"Dataset missing required path: {path}")
    if not (dataset_path / "corpus").is_dir():
        raise ValidationError(f"Dataset corpus must be a directory: {dataset_path / 'corpus'}")
    questions_raw = read_jsonl(dataset_path / "questions.jsonl")
    answers_raw = read_jsonl(dataset_path / "answers.jsonl")
    questions: list[dict[str, str]] = []
    question_ids: set[str] = set()
    for row in questions_raw:
        qid = row.get("question_id")
        question = row.get("question")
        if not isinstance(qid, str) or not qid:
            raise ValidationError("Each question requires non-empty string question_id.")
        if qid in question_ids:
            raise ValidationError(f"Duplicate question_id in questions: {qid}")
        if not isinstance(question, str) or not question:
            raise ValidationError(f"Question {qid} requires non-empty question text.")
        question_ids.add(qid)
        questions.append({"question_id": qid, "question": question})
    answers: dict[str, str] = {}
    for row in answers_raw:
        qid = row.get("question_id")
        answer = row.get("answer")
        if not isinstance(qid, str) or not qid:
            raise ValidationError("Each answer requires non-empty string question_id.")
        if qid in answers:
            raise ValidationError(f"Duplicate question_id in answers: {qid}")
        if qid not in question_ids:
            raise ValidationError(f"Answer for unknown question_id: {qid}")
        if not isinstance(answer, str):
            raise ValidationError(f"Answer {qid} requires string answer.")
        answers[qid] = answer
    missing_answers = question_ids - set(answers)
    if missing_answers:
        raise ValidationError(f"Missing answers for question_id values: {sorted(missing_answers)}")
    graph_edges = dataset_path / "graph_edges.csv"
    if graph_edges.exists():
        with graph_edges.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["source", "target"]:
                raise ValidationError("graph_edges.csv header must be source,target")
    return questions, answers


def validate_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValidationError(f"Missing metadata.json: {path}")
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid metadata.json: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValidationError("metadata.json must be a JSON object.")
    return metadata


def validate_predictions(path: Path, questions: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not path.exists():
        raise ValidationError(f"Missing predictions.jsonl: {path}")
    predictions = read_jsonl(path)
    expected_ids = {q["question_id"] for q in questions}
    seen: set[str] = set()
    by_question = {q["question_id"]: q["question"] for q in questions}
    for row in predictions:
        qid = row.get("question_id")
        if not isinstance(qid, str) or not qid:
            raise ValidationError("Each prediction requires non-empty string question_id.")
        if qid in seen:
            raise ValidationError(f"Duplicate prediction for question_id: {qid}")
        if qid not in expected_ids:
            raise ValidationError(f"Prediction for unknown question_id: {qid}")
        seen.add(qid)
        question = row.get("question")
        if not isinstance(question, str) or not question:
            raise ValidationError(f"Prediction {qid} requires question string.")
        if question != by_question[qid]:
            raise ValidationError(f"Prediction {qid} question text does not match dataset.")
        if not isinstance(row.get("generated_answer"), str):
            raise ValidationError(f"Prediction {qid} requires generated_answer string.")
        context = row.get("retrieved_context", [])
        if not isinstance(context, list):
            raise ValidationError(f"Prediction {qid} retrieved_context must be a list.")
        for item in context:
            if not isinstance(item, dict):
                raise ValidationError(f"Prediction {qid} retrieved_context entries must be objects.")
            if "score" in item and not isinstance(item["score"], (int, float)):
                raise ValidationError(f"Prediction {qid} context score must be numeric.")
    missing = expected_ids - seen
    if missing:
        raise ValidationError(f"Missing predictions for question_id values: {sorted(missing)}")
    return predictions