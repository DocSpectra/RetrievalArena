from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..schemas import read_jsonl, write_jsonl
from ..snapshots import corpus_doc_id


SUPPORT_CONSTRUCTION_METHOD = "answer_lexical_overlap_v1"
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def write_answer_overlap_support(
    dataset_path: Path,
    *,
    top_k: int = 5,
    min_answer_token_coverage: float = 0.25,
) -> dict[str, Any]:
    answers = read_jsonl(dataset_path / "answers.jsonl")
    questions = {row["question_id"]: row["question"] for row in read_jsonl(dataset_path / "questions.jsonl")}
    docs = _document_tokens(dataset_path / "corpus")
    rows: list[dict[str, Any]] = []
    for answer_row in sorted(answers, key=lambda item: str(item.get("question_id") or "")):
        question_id = str(answer_row["question_id"])
        answer = str(answer_row.get("answer", ""))
        answer_tokens = sorted(_tokens(answer))
        scored = []
        for doc in docs:
            matched = sorted(set(answer_tokens) & doc["tokens"])
            coverage = 0.0 if not answer_tokens else len(matched) / len(set(answer_tokens))
            if coverage >= min_answer_token_coverage:
                scored.append(
                    {
                        "doc_id": doc["doc_id"],
                        "score": coverage,
                        "answer_token_coverage": coverage,
                        "matched_answer_tokens": matched,
                    }
                )
        scored = sorted(scored, key=lambda item: (-float(item["score"]), str(item["doc_id"])))[:top_k]
        rows.append(
            {
                "question_id": question_id,
                "question": questions.get(question_id),
                "support_construction_method": SUPPORT_CONSTRUCTION_METHOD,
                "support_label": "supported" if scored else "unsupported",
                "answer_content_tokens": len(set(answer_tokens)),
                "top_docs": scored,
            }
        )
    destination = dataset_path / "faq_support_audit.jsonl"
    write_jsonl(destination, rows)
    target_doc_ids = sorted({doc["doc_id"] for row in rows for doc in row["top_docs"]})
    return {
        "support_construction_method": SUPPORT_CONSTRUCTION_METHOD,
        "support_audit_file": "faq_support_audit.jsonl",
        "supported_question_ids": sorted(row["question_id"] for row in rows if row["top_docs"]),
        "support_target_doc_ids": target_doc_ids,
        "support_target_count": len(target_doc_ids),
        "support_query_count": len(rows),
        "top_k": top_k,
        "min_answer_token_coverage": min_answer_token_coverage,
    }


def _document_tokens(corpus_dir: Path) -> list[dict[str, Any]]:
    docs = []
    for path in sorted(corpus_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(corpus_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        docs.append({"doc_id": corpus_doc_id(relative), "path": relative, "tokens": _tokens(text)})
    return docs


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(text)}
