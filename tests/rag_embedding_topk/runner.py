from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


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


def answer_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip("# ").strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    snippet = " ".join(lines)
    if max_chars > 0 and len(snippet) > max_chars:
        return snippet[:max_chars].rsplit(" ", 1)[0].strip()
    return snippet


def signed_hash(token: str, *, dimensions: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    index = value % dimensions
    sign = 1.0 if (value >> 63) == 0 else -1.0
    return index, sign


def document_frequencies(corpus_tokens: dict[str, list[str]]) -> Counter[str]:
    doc_freq: Counter[str] = Counter()
    for doc_tokens in corpus_tokens.values():
        doc_freq.update(set(doc_tokens))
    return doc_freq


def hashed_tfidf_vector(doc_tokens: list[str], *, doc_freq: Counter[str], num_docs: int, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    token_counts = Counter(doc_tokens)
    if not token_counts:
        return vector
    for token, count in token_counts.items():
        idf = math.log(1.0 + (num_docs + 1.0) / (doc_freq[token] + 1.0))
        index, sign = signed_hash(token, dimensions=dimensions)
        vector[index] += sign * (1.0 + math.log(count)) * idf
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


input_dir = Path("/input")
output_dir = Path("/output")
output_dir.mkdir(parents=True, exist_ok=True)

config = read_config(input_dir / "config.yaml")
top_k = max(1, int(config.get("top_k", 5)))
max_context_chars = max(0, int(config.get("max_context_chars_per_doc", 1500)))
dimensions = max(64, int(config.get("embedding_dimensions", 512)))
model_id = str(config.get("model_id", "local-hashing-tfidf-v1"))
embedding_backend = str(config.get("embedding_backend", "local_hashing_tfidf"))

questions = read_jsonl(input_dir / "questions.jsonl")
corpus = {path.stem: path.read_text(encoding="utf-8") for path in (input_dir / "corpus").glob("*.md")}
corpus_tokens = {doc_id: tokens(text) for doc_id, text in corpus.items()}
doc_freq = document_frequencies(corpus_tokens)
num_docs = len(corpus_tokens)
doc_vectors = {
    doc_id: hashed_tfidf_vector(doc_tokens, doc_freq=doc_freq, num_docs=num_docs, dimensions=dimensions)
    for doc_id, doc_tokens in corpus_tokens.items()
}

with (output_dir / "predictions.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
    for question in questions:
        query_vector = hashed_tfidf_vector(tokens(question["question"]), doc_freq=doc_freq, num_docs=num_docs, dimensions=dimensions)
        ranked = sorted(
            ((cosine(query_vector, doc_vector), doc_id) for doc_id, doc_vector in doc_vectors.items()),
            key=lambda item: (-item[0], item[1]),
        )[:top_k]
        context = [
            {
                "doc_id": doc_id,
                "text": answer_text(corpus[doc_id], max_chars=max_context_chars),
                "score": float(score),
                "source": "embedding_topk",
                "rank": rank,
            }
            for rank, (score, doc_id) in enumerate(ranked, start=1)
        ]
        generated = " ".join(item["text"] for item in context if item["text"])
        handle.write(
            json.dumps(
                {
                    "question_id": question["question_id"],
                    "question": question["question"],
                    "generated_answer": generated,
                    "retrieved_context": context,
                },
                sort_keys=True,
            )
            + "\n"
        )

(output_dir / "metadata.json").write_text(
    json.dumps(
        {
            "name": "rag_embedding_topk",
            "deterministic": True,
            "ranker": "cosine",
            "embedding_backend": embedding_backend,
            "model_id": model_id,
            "embedding_dimensions": dimensions,
            "top_k": top_k,
            "max_context_chars_per_doc": max_context_chars,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
