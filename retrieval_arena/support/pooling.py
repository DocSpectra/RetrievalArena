"""Pooled support candidates: passage windows, two passage generators, evaluated runs, pool union.

Support construction, not independent judgment (docs/evaluations/pooled_support.md).
The pool for a query is the union of
  * the top `generator_depth` pages of passage BM25 and of passage embeddings, both
    matched against the query's reference answer (a page scores its best window), and
  * the top `run_depth` pages of every evaluated run,
with per-source provenance (rank in each source). Pool rules are parameters, never
tuned on outcomes; nothing here names a corpus.

Each pooled page carries the windows its verifier will see: the page's top
`windows_per_generator` windows under each generator, deduplicated. Every window is
scored by both generators, so pages that only a run retrieved get windows too.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from ..errors import ValidationError
from .surface import STOPWORDS, TOKEN_RE

SCHEMA_POOL_CONFIG = "retrieval_arena.pooled_support_pool_config.v1"
SCHEMA_POOL_ROW = "retrieval_arena.pooled_support_pool_row.v1"
SCHEMA_WINDOW = "retrieval_arena.pooled_support_window.v1"
SCHEMA_POOL_MANIFEST = "retrieval_arena.pooled_support_pool_manifest.v1"
POOL_RULE_ID = "pooled_support_pool_v1"
GENERATORS = ("bm25_passage", "embedding_passage")
RUN_FORMATS = ("legacy_predictions", "ranked_prediction_v2")
BM25_K1, BM25_B = 1.2, 0.75


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8", newline="\n")


# ------------------------------------------------------------------ corpus and windows

def load_corpus(corpus_dir: Path, suffixes: tuple[str, ...] = (".md",)) -> dict[str, str]:
    """{doc_id: text}; doc_id is the corpus-relative path without its suffix (RA corpus_doc_id rule)."""
    root = Path(corpus_dir)
    docs: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in suffixes:
            rel = path.relative_to(root).with_suffix("").as_posix()
            docs[rel] = path.read_bytes().decode("utf-8", errors="replace")
    if not docs:
        raise ValidationError("pooled-support-empty-corpus")
    return docs


def make_windows(docs: dict[str, str], window_lines: int, stride_lines: int) -> list[dict[str, Any]]:
    """Fixed line windows per page; the last window may be shorter; blank windows are dropped."""
    if not (isinstance(window_lines, int) and isinstance(stride_lines, int) and 0 < stride_lines <= window_lines):
        raise ValidationError("pooled-support-window-shape")
    out = []
    for doc_id in sorted(docs):
        lines = docs[doc_id].splitlines()
        for start in range(0, max(1, len(lines) - window_lines + stride_lines), stride_lines):
            text = "\n".join(lines[start:start + window_lines]).strip()
            if text:
                end = min(len(lines), start + window_lines)
                out.append({"schema_version": SCHEMA_WINDOW, "window_id": f"{doc_id}#L{start + 1}-{end}",
                            "doc_id": doc_id, "start_line": start + 1, "end_line": end,
                            "text": text, "text_sha256": sha256_text(text)})
    return out


def content_terms(text: str) -> Counter:
    return Counter(t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS and len(t) > 1)


# ------------------------------------------------------------------ generators

class BM25Index:
    """Okapi BM25 over windows (k1 1.2, b 0.75, idf log(1 + (N - df + .5)/(df + .5)))."""

    def __init__(self, windows: list[dict[str, Any]]):
        self.n = len(windows)
        self.lengths = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, w in enumerate(windows):
            counts = content_terms(w["text"])
            self.lengths.append(sum(counts.values()))
            for term, tf in counts.items():
                self.postings[term].append((i, tf))
        self.avg = (sum(self.lengths) / self.n) if self.n else 0.0

    def scores(self, query_text: str) -> list[float]:
        out = [0.0] * self.n
        for term in sorted(set(content_terms(query_text))):
            posting = self.postings.get(term)
            if not posting:
                continue
            df = len(posting)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            for i, tf in posting:
                norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * self.lengths[i] / self.avg)
                out[i] += idf * tf * (BM25_K1 + 1) / norm
        return out


def embedding_scores(windows: list[dict[str, Any]], query_texts: list[str], encoder: Callable[[list[str]], Any]) -> list[list[float]]:
    """Cosine scores (encoder returns L2-normalized rows); one list per query."""
    passages = encoder([w["text"] for w in windows])
    queries = encoder(query_texts)
    matrix = queries @ passages.T
    return [[float(x) for x in row] for row in matrix]


def sentence_transformer_encoder(model_dir: str, batch_size: int = 64) -> Callable[[list[str]], Any]:
    """Local, offline sentence-transformers encoder (imported lazily; runs in the pinned embedding image)."""
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    model = SentenceTransformer(model_dir, device="cpu")
    return lambda texts: model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)


def rank_pages(window_scores: list[float], windows: list[dict[str, Any]], depth: int) -> list[tuple[str, float]]:
    """Pages by their best window (score desc, doc_id asc); positive scores only for BM25-like zeros."""
    best: dict[str, float] = {}
    for score, w in zip(window_scores, windows):
        if score > best.get(w["doc_id"], float("-inf")):
            best[w["doc_id"]] = score
    ranked = sorted(((d, s) for d, s in best.items() if s > 0), key=lambda x: (-x[1], x[0]))
    return ranked[:depth]


def top_windows_per_page(window_scores: list[float], windows: list[dict[str, Any]], pages: set[str], n: int) -> dict[str, list[int]]:
    """For each page in `pages`, its top-n window indices (score desc, start line asc)."""
    by_page: dict[str, list[tuple[float, int, int]]] = defaultdict(list)
    for i, (score, w) in enumerate(zip(window_scores, windows)):
        if w["doc_id"] in pages:
            by_page[w["doc_id"]].append((-score, w["start_line"], i))
    return {d: [i for _, _, i in sorted(v)[:n]] for d, v in by_page.items()}


# ------------------------------------------------------------------ evaluated runs

def read_run(path: Path, fmt: str) -> dict[str, list[str]]:
    """{query_id: [doc ids by rank]} from a provider's legacy predictions or ranked-v2 predictions."""
    if fmt not in RUN_FORMATS:
        raise ValidationError(f"pooled-support-run-format:{fmt}")
    out: dict[str, list[str]] = {}
    for row in read_jsonl(path):
        qid = row.get("query_id" if fmt == "ranked_prediction_v2" else "question_id")
        if not isinstance(qid, str) or qid in out:
            raise ValidationError(f"pooled-support-run-row:{qid}")
        items = row["items"] if fmt == "ranked_prediction_v2" else row["retrieved_context"]
        key = "support_target_id" if fmt == "ranked_prediction_v2" else "doc_id"
        ordered = sorted(items, key=lambda item: item["rank"])
        ranks = [item["rank"] for item in ordered]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValidationError(f"pooled-support-run-ranks:{qid}")
        out[qid] = [item[key] for item in ordered]
    return out


# ------------------------------------------------------------------ pool

def validate_pool_config(config: dict[str, Any]) -> dict[str, Any]:
    keys = {"schema_version", "pool_id", "corpus_id", "generator_depth", "run_depth", "window_lines",
            "stride_lines", "windows_per_generator", "query_field", "runs", "embedding_model_id"}
    if set(config) != keys or config["schema_version"] != SCHEMA_POOL_CONFIG:
        raise ValidationError(f"pooled-support-pool-config-fields:{sorted(set(config) ^ keys)}")
    for name in ("generator_depth", "run_depth", "window_lines", "stride_lines", "windows_per_generator"):
        if type(config[name]) is not int or config[name] < 1:
            raise ValidationError(f"pooled-support-pool-config:{name}")
    if config["query_field"] != "answer":
        raise ValidationError("pooled-support-query-field")  # generators match the reference answer
    ids = [r.get("run_id") for r in config["runs"]]
    if len(ids) != len(set(ids)) or any(set(r) != {"run_id", "path", "format"} or r["format"] not in RUN_FORMATS for r in config["runs"]):
        raise ValidationError("pooled-support-pool-config-runs")
    return config


def build_pool(config: dict[str, Any], queries: list[dict[str, Any]], windows: list[dict[str, Any]],
               docs: dict[str, str], runs: dict[str, dict[str, list[str]]],
               encoder: Callable[[list[str]], Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Pool rows (one per query × page), the windows they reference, and summary counts."""
    validate_pool_config(config)
    qids = [q["question_id"] for q in queries]
    if len(qids) != len(set(qids)) or any(not q.get("answer") for q in queries):
        raise ValidationError("pooled-support-queries")
    page_sha = {d: sha256_text(t) for d, t in docs.items()}
    bm25 = BM25Index(windows)
    emb = embedding_scores(windows, [q["answer"] for q in queries], encoder)
    rows, used = [], set()
    unknown_run_pages = Counter()
    for qi, q in enumerate(queries):
        qid = q["question_id"]
        scores = {"bm25_passage": bm25.scores(q["answer"]), "embedding_passage": emb[qi]}
        sources: dict[str, dict[str, int]] = defaultdict(dict)
        for gen in GENERATORS:
            for rank, (doc, _) in enumerate(rank_pages(scores[gen], windows, config["generator_depth"]), 1):
                sources[doc][gen] = rank
        for run_id in sorted(runs):
            for rank, doc in enumerate(runs[run_id].get(qid, [])[:config["run_depth"]], 1):
                if doc not in docs:
                    unknown_run_pages[run_id] += 1
                    continue
                sources[doc]["run:" + run_id] = rank
        pages = set(sources)
        chosen = {gen: top_windows_per_page(scores[gen], windows, pages, config["windows_per_generator"]) for gen in GENERATORS}
        for doc in sorted(pages):
            idx = list(dict.fromkeys(chosen["bm25_passage"].get(doc, []) + chosen["embedding_passage"].get(doc, [])))
            used.update(idx)
            rows.append({"schema_version": SCHEMA_POOL_ROW, "query_id": qid, "doc_id": doc,
                         "page_sha256": page_sha[doc], "sources": dict(sorted(sources[doc].items())),
                         "window_ids": [windows[i]["window_id"] for i in idx],
                         "best_window_scores": {gen: max((scores[gen][i] for i in idx), default=None) for gen in GENERATORS}})
    kept = [windows[i] for i in sorted(used)]
    summary = {"queries": len(queries), "pool_pairs": len(rows), "windows_referenced": len(kept),
               "pages_per_query_mean": len(rows) / len(queries) if queries else 0.0,
               "unknown_run_pages": dict(unknown_run_pages),
               "pairs_by_source_count": dict(Counter(len(r["sources"]) for r in rows))}
    return rows, kept, summary


def pool_files(config_path: Path, corpus_dir: Path, queries_path: Path, run_root: Path, model_dir: str | None,
               out_dir: Path, encoder: Callable[[list[str]], Any] | None = None) -> dict[str, Any]:
    """`support pool`: write pool.jsonl, windows.jsonl and pool_manifest.json to a fresh out_dir."""
    out_dir = Path(out_dir)
    config = validate_pool_config(json.loads(Path(config_path).read_text(encoding="utf-8")))
    docs = load_corpus(corpus_dir)
    windows = make_windows(docs, config["window_lines"], config["stride_lines"])
    queries = read_jsonl(queries_path)
    runs = {r["run_id"]: read_run(Path(run_root) / r["path"], r["format"]) for r in config["runs"]}
    if encoder is None:
        if not model_dir:
            raise ValidationError("pooled-support-embedding-model-required")
        encoder = sentence_transformer_encoder(model_dir)
    rows, kept, summary = build_pool(config, queries, windows, docs, runs, encoder)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "pool.jsonl", rows)
    write_jsonl(out_dir / "windows.jsonl", kept)
    corpus_inventory = {d: sha256_text(t) for d, t in sorted(docs.items())}
    write_json(out_dir / "corpus_inventory.json", corpus_inventory)
    manifest = {"schema_version": SCHEMA_POOL_MANIFEST, "pool_rule_id": POOL_RULE_ID, "config": config,
                "config_sha256": sha256_json(config), "corpus_id": config["corpus_id"],
                "corpus_pages": len(docs), "corpus_inventory_sha256": sha256_json(corpus_inventory),
                "windows_total": len(windows), "queries_sha256": sha256_json(queries),
                "runs": {rid: {"path": next(r["path"] for r in config["runs"] if r["run_id"] == rid),
                               "sha256": hashlib.sha256((Path(run_root) / next(r["path"] for r in config["runs"] if r["run_id"] == rid)).read_bytes()).hexdigest(),
                               "queries": len(run)} for rid, run in sorted(runs.items())},
                "pool_sha256": hashlib.sha256((out_dir / "pool.jsonl").read_bytes()).hexdigest(),
                "windows_sha256": hashlib.sha256((out_dir / "windows.jsonl").read_bytes()).hexdigest(),
                "summary": summary,
                "label": "support candidates (construction), not judgments"}
    write_json(out_dir / "pool_manifest.json", manifest)
    return manifest
