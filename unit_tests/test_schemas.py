from pathlib import Path
import shutil
import unittest
import uuid
from contextlib import contextmanager
from typing import Iterator

from retrieval_arena.errors import ValidationError
from retrieval_arena.schemas import validate_dataset, validate_predictions, write_jsonl


class SchemaTests(unittest.TestCase):
    @contextmanager
    def workspace_tempdir(self) -> Iterator[str]:
        root = Path(".tmp_unit_tests")
        root.mkdir(exist_ok=True)
        path = root / f"tmp_{uuid.uuid4().hex}"
        path.mkdir()
        try:
            yield str(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_validate_dataset_accepts_toy_dataset(self):
        questions, answers = validate_dataset(Path("examples/toy_docs"))
        self.assertEqual(len(questions), 3)
        self.assertEqual(set(answers), {"q001", "q002", "q003"})


    def test_validate_predictions_rejects_missing_question(self):
        questions, _ = validate_dataset(Path("examples/toy_docs"))
        with self.workspace_tempdir() as tmp:
            path = Path(tmp) / "predictions.jsonl"
            write_jsonl(path, [{"question_id": "q001", "question": questions[0]["question"], "generated_answer": "x", "retrieved_context": []}])
            with self.assertRaisesRegex(ValidationError, "Missing predictions"):
                validate_predictions(path, questions)


    def test_validate_predictions_rejects_unknown_question(self):
        questions, _ = validate_dataset(Path("examples/toy_docs"))
        rows = [{"question_id": q["question_id"], "question": q["question"], "generated_answer": "x", "retrieved_context": []} for q in questions]
        rows[0]["question_id"] = "unknown"
        with self.workspace_tempdir() as tmp:
            path = Path(tmp) / "predictions.jsonl"
            write_jsonl(path, rows)
            with self.assertRaisesRegex(ValidationError, "unknown question_id"):
                validate_predictions(path, questions)


    def test_validate_predictions_rejects_duplicate_question(self):
        questions = [
            {"question_id": "q001", "question": "Question one?"},
            {"question_id": "q002", "question": "Question two?"},
        ]
        rows = [
            {"question_id": "q001", "question": "Question one?", "generated_answer": "x", "retrieved_context": []},
            {"question_id": "q001", "question": "Question one?", "generated_answer": "y", "retrieved_context": []},
        ]
        with self.workspace_tempdir() as tmp:
            path = Path(tmp) / "predictions.jsonl"
            write_jsonl(path, rows)
            with self.assertRaisesRegex(ValidationError, "Duplicate prediction"):
                validate_predictions(path, questions)


if __name__ == "__main__":
    unittest.main()
