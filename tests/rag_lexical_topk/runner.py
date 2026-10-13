"""BM25 whole-document top-k provider with two deterministic answer policies.

``head_of_document_v1`` (default) reproduces the legacy behaviour byte for
byte: every retrieved context item carries the head snippet of its document
and ``generated_answer`` is the space-join of the non-empty snippets.

``best_passage_v1`` keeps the ranking and the context snippets unchanged but
answers with the three best BM25 passages of the top-k documents, in
document order, together with ``answer_provenance`` (document id, nearest
preceding heading, passage index, raw character span).  No model is used.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
HEADING_RE = re.compile(r"^#{1,3}\s")
ANSWER_POLICIES = ("head_of_document_v1", "best_passage_v1")
DEFAULT_ANSWER_POLICY = "head_of_document_v1"
BEST_PASSAGE_COUNT = 3


class Passage(NamedTuple):
    text: str
    section_heading: str | None
    passage_index: int
    char_start: int
    char_end: int


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def truncate(snippet: str, *, max_chars: int) -> str:
    if max_chars > 0 and len(snippet) > max_chars:
        return snippet[:max_chars].rsplit(" ", 1)[0].strip()
    return snippet


def answer_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip("# ").strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    snippet = " ".join(lines)
    return truncate(snippet, max_chars=max_chars)


def bm25_scores(query_tokens: list[str], corpus_tokens: dict[str, list[str]]) -> list[tuple[float, str]]:
    num_docs = len(corpus_tokens)
    avg_len = sum(len(doc_tokens) for doc_tokens in corpus_tokens.values()) / num_docs if num_docs else 0.0
    doc_freq: Counter[str] = Counter()
    for doc_tokens in corpus_tokens.values():
        doc_freq.update(set(doc_tokens))

    k1 = 1.5
    b = 0.75
    query = Counter(query_tokens)
    scored: list[tuple[float, str]] = []
    for doc_id, doc_tokens in corpus_tokens.items():
        tf = Counter(doc_tokens)
        doc_len = len(doc_tokens)
        score = 0.0
        for token, query_count in query.items():
            if tf[token] == 0:
                continue
            idf = math.log(1.0 + (num_docs - doc_freq[token] + 0.5) / (doc_freq[token] + 0.5))
            denom = tf[token] + k1 * (1.0 - b + b * (doc_len / avg_len if avg_len else 0.0))
            score += query_count * idf * ((tf[token] * (k1 + 1.0)) / denom)
        scored.append((score, doc_id))
    return sorted(scored, key=lambda item: (-item[0], item[1]))


def split_passages(text: str) -> list[Passage]:
    """Split a Markdown document into paragraph passages with raw character spans.

    Blocks are separated by level 1-3 headings (``^#{1,3}\\s``) and blank
    lines.  Each non-heading block is normalized exactly like the head
    snippet (lines starting with ``#`` dropped, ``strip("# ").strip()`` per
    line, single-space join); blocks whose normalized text is empty are not
    passages.  ``char_start``/``char_end`` delimit the raw block in ``text``
    (first line start to last line end, line terminators excluded).
    """
    passages: list[Passage] = []
    heading: str | None = None
    block: list[tuple[str, int, int]] = []

    def flush() -> None:
        if not block:
            return
        lines = [line.strip("# ").strip() for line, _, _ in block if line.strip() and not line.startswith("#")]
        normalized = " ".join(lines)
        if normalized:
            passages.append(Passage(normalized, heading, len(passages), block[0][1], block[-1][2]))
        block.clear()

    offset = 0
    for raw in text.splitlines(keepends=True):
        line = raw.splitlines()[0]  # keepends leaves exactly one terminator, if any
        start = offset
        end = offset + len(line)
        offset += len(raw)
        if not line.strip():
            flush()
            continue
        if HEADING_RE.match(line):
            flush()
            heading = line.lstrip("#").strip()
            continue
        block.append((line, start, end))
    flush()
    return passages


def head_of_document_answer(context: list[dict[str, Any]]) -> str:
    return " ".join(item["text"] for item in context if item["text"])


def best_passage_answer(question_tokens: list[str], ranked_docs: list[str], corpus: dict[str, str],
                        max_chars: int) -> tuple[str, list[dict[str, Any]]]:
    """Return the best-passage answer and its provenance for the ranked documents."""
    candidates: dict[str, tuple[int, str, Passage]] = {}
    passage_tokens: dict[str, list[str]] = {}
    for doc_rank, doc_id in enumerate(ranked_docs, start=1):
        for passage in split_passages(corpus[doc_id]):
            key = f"{doc_rank}:{passage.passage_index}"
            candidates[key] = (doc_rank, doc_id, passage)
            passage_tokens[key] = tokens(passage.text)
    if not candidates:
        return "", []
    scored = bm25_scores(question_tokens, passage_tokens)
    ordered = sorted(scored, key=lambda item: (-item[0], candidates[item[1]][0], candidates[item[1]][2].passage_index))
    if any(score > 0.0 for score, _ in ordered):
        ordered = [item for item in ordered if item[0] > 0.0]
    selected = sorted((candidates[key] for _, key in ordered[:BEST_PASSAGE_COUNT]),
                      key=lambda item: (item[0], item[2].passage_index))
    answer = truncate(" ".join(passage.text for _, _, passage in selected), max_chars=max_chars)
    provenance = [
        {
            "doc_id": doc_id,
            "section_heading": passage.section_heading,
            "passage_index": passage.passage_index,
            "char_start": passage.char_start,
            "char_end": passage.char_end,
        }
        for _, doc_id, passage in selected
    ]
    return answer, provenance


def main(input_dir: Path = Path("/input"), output_dir: Path = Path("/output")) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    config = read_config(input_dir / "config.yaml")
    top_k = max(1, int(config.get("top_k", 5)))
    max_context_chars = max(0, int(config.get("max_context_chars_per_doc", 1500)))
    answer_policy = config.get("answer_policy", DEFAULT_ANSWER_POLICY)
    if answer_policy not in ANSWER_POLICIES:
        raise ValueError(f"unknown answer_policy: {answer_policy!r}")

    questions = read_jsonl(input_dir / "questions.jsonl")
    corpus_root = input_dir / "corpus"
    corpus = {
        path.relative_to(corpus_root).with_suffix("").as_posix(): path.read_text(encoding="utf-8")
        for path in corpus_root.rglob("*.md")
    }
    corpus_tokens = {doc_id: tokens(text) for doc_id, text in corpus.items()}

    with (output_dir / "predictions.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for question in questions:
            question_tokens = tokens(question["question"])
            ranked = bm25_scores(question_tokens, corpus_tokens)[:top_k]
            context = [
                {
                    "doc_id": doc_id,
                    "text": answer_text(corpus[doc_id], max_chars=max_context_chars),
                    "score": float(score),
                    "source": "lexical_topk",
                    "rank": rank,
                }
                for rank, (score, doc_id) in enumerate(ranked, start=1)
            ]
            row: dict[str, Any] = {
                "question_id": question["question_id"],
                "question": question["question"],
                "retrieved_context": context,
            }
            if answer_policy == "best_passage_v1":
                answer, provenance = best_passage_answer(
                    question_tokens, [doc_id for _, doc_id in ranked], corpus, max_context_chars)
                row["generated_answer"] = answer
                row["answer_provenance"] = provenance
            else:
                row["generated_answer"] = head_of_document_answer(context)
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    (output_dir / "metadata.json").write_text(
        json.dumps(
            {
                "name": "rag_lexical_topk",
                "deterministic": True,
                "ranker": "bm25",
                "top_k": top_k,
                "max_context_chars_per_doc": max_context_chars,
                "answer_policy": answer_policy,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
