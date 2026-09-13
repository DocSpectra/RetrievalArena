"""Closed candidate-bound prediction production for ranked-v2 evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .errors import ValidationError

CONFIG_SCHEMA = "retrieval_arena.candidate_retrieval_config.v1"
PREDICTION_SCHEMA = "retrieval_arena.ranked_prediction.v2"
PROVIDER_SCHEMA = "retrieval_arena.lexical_provider_output.v1"
PROVIDER_NAME = "rag_lexical_topk"
ANSWER_POLICIES = ("head_of_document_v1", "best_passage_v1")
DEFAULT_ANSWER_POLICY = "head_of_document_v1"
PROVIDER_ROW_FIELDS = {"question_id", "question", "generated_answer", "retrieved_context"}
PROVENANCE_FIELDS = {"doc_id", "section_heading", "passage_index", "char_start", "char_end"}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def regular_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.is_dir() or root.is_symlink():
        raise ValidationError("candidate-retrieval-root-invalid")
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise ValidationError("candidate-retrieval-symlink-rejected")
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            raise ValidationError("candidate-retrieval-nonregular-rejected")
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path),
                     "size_bytes": path.stat().st_size})
    return rows


def _exact(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(code)
    return value


def _relative(value: Any, code: str) -> str:
    if (not isinstance(value, str) or not value or Path(value).is_absolute()
            or "\\" in value or any(part in {"", ".", ".."} for part in Path(value).parts)):
        raise ValidationError(code)
    return value


def validate_config(value: Any) -> dict[str, Any]:
    config = _exact(value, {
        "schema_version", "experiment_id", "execution_id", "candidate", "corpus",
        "queries_path", "provider", "ranked", "input_inventory",
        "input_inventory_sha256", "output_identity",
    }, "candidate-retrieval-config-fields")
    if config["schema_version"] != CONFIG_SCHEMA:
        raise ValidationError("candidate-retrieval-config-schema")
    for name in ("experiment_id", "execution_id", "output_identity"):
        if not isinstance(config[name], str) or not config[name]:
            raise ValidationError(f"candidate-retrieval-{name}")
    candidate = _exact(config["candidate"], {"commit", "tree"}, "candidate-retrieval-candidate-fields")
    if any(not isinstance(candidate[name], str) or len(candidate[name]) != 40
           or any(ch not in "0123456789abcdef" for ch in candidate[name])
           for name in ("commit", "tree")):
        raise ValidationError("candidate-retrieval-candidate-identity")
    corpus = _exact(config["corpus"], {"root", "extensions", "import_policy"},
                    "candidate-retrieval-corpus-fields")
    _relative(corpus["root"], "candidate-retrieval-corpus-root")
    if corpus["extensions"] != [".md"] or corpus["import_policy"] != "cikm_flat_path_sha1_v1":
        raise ValidationError("candidate-retrieval-import-policy")
    _relative(config["queries_path"], "candidate-retrieval-queries-path")
    provider_fields = {"name", "image", "platform", "network", "top_k", "max_context_chars_per_doc"}
    if (not isinstance(config["provider"], dict)
            or set(config["provider"]) not in (provider_fields, provider_fields | {"answer_policy"})):
        raise ValidationError("candidate-retrieval-provider-fields")
    # Legacy requests carry no answer_policy; the effective policy is always recorded.
    provider = {**config["provider"],
                "answer_policy": config["provider"].get("answer_policy", DEFAULT_ANSWER_POLICY)}
    config = {**config, "provider": provider}
    if (provider["name"] != PROVIDER_NAME or provider["platform"] != "linux/amd64"
            or provider["network"] != "none"
            or not isinstance(provider["image"], str)
            or not provider["image"].startswith("retrieval-arena/rag-lexical-topk@sha256:")
            or re.fullmatch(r"retrieval-arena/rag-lexical-topk@sha256:[0-9a-f]{64}", provider["image"]) is None
            or isinstance(provider["top_k"], bool) or not isinstance(provider["top_k"], int)
            or provider["top_k"] < 1
            or isinstance(provider["max_context_chars_per_doc"], bool)
            or not isinstance(provider["max_context_chars_per_doc"], int)
            or provider["max_context_chars_per_doc"] < 0
            or provider["answer_policy"] not in ANSWER_POLICIES):
        raise ValidationError("candidate-retrieval-provider-policy")
    ranked = _exact(config["ranked"], {"schema_version", "context_item_budget_k"},
                    "candidate-retrieval-ranked-fields")
    if (ranked["schema_version"] != PREDICTION_SCHEMA
            or ranked["context_item_budget_k"] != provider["top_k"]):
        raise ValidationError("candidate-retrieval-ranked-policy")
    inventory = config["input_inventory"]
    if (not isinstance(inventory, list) or inventory != sorted(inventory, key=lambda row: row.get("path", ""))
            or any(not isinstance(row, dict) or set(row) != {"path", "sha256", "size_bytes"}
                   for row in inventory)
            or config["input_inventory_sha256"] != sha256_bytes(canonical(inventory))):
        raise ValidationError("candidate-retrieval-inventory")
    return config


def _git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(["/usr/bin/git", "-c", f"safe.directory={repo}", "-C", str(repo), *arguments], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                                 "TZ": "UTC", "GIT_CONFIG_NOSYSTEM": "1",
                                 "GIT_CONFIG_GLOBAL": "/dev/null"}, check=False)
    if result.returncode:
        raise ValidationError("candidate-retrieval-git-failure")
    return result.stdout.strip()


def prepare_provider_input(config: dict[str, Any], input_root: Path, candidate_root: Path,
                           provider_input: Path) -> dict[str, Any]:
    config = validate_config(config)
    payload_inventory = [row for row in regular_inventory(input_root)
                         if row["path"] not in {"config.json", "operator-request.json"}]
    if payload_inventory != config["input_inventory"]:
        raise ValidationError("candidate-retrieval-input-inventory-mismatch")
    if (_git(candidate_root, "rev-parse", "HEAD") != config["candidate"]["commit"]
            or _git(candidate_root, "rev-parse", "HEAD^{tree}") != config["candidate"]["tree"]
            or _git(candidate_root, "status", "--porcelain", "--untracked-files=all")):
        raise ValidationError("candidate-retrieval-candidate-admission")
    corpus_root = candidate_root / config["corpus"]["root"]
    if not corpus_root.is_dir() or corpus_root.is_symlink():
        raise ValidationError("candidate-retrieval-corpus-root")
    queries = input_root / config["queries_path"]
    if not queries.is_file() or queries.is_symlink():
        raise ValidationError("candidate-retrieval-queries-missing")
    if provider_input.exists() or not provider_input.parent.is_dir():
        raise ValidationError("candidate-retrieval-provider-input-root")
    provider_input.mkdir()
    (provider_input / "corpus").mkdir()
    document_rows = []
    for source in sorted(corpus_root.rglob("*.md")):
        if source.is_symlink() or not source.is_file():
            raise ValidationError("candidate-retrieval-corpus-file")
        relative = source.relative_to(corpus_root)
        relative_text = relative.as_posix()
        stem = re.sub(r"[^A-Za-z0-9]+", "_", relative.with_suffix("").as_posix()).strip("_").lower()
        suffix = hashlib.sha1(relative_text.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        imported_name = f"{stem}__{suffix}.md"
        destination = provider_input / "corpus" / imported_name
        if destination.exists():
            raise ValidationError("candidate-retrieval-document-id-collision")
        destination.write_bytes(source.read_bytes())
        text = source.read_text(encoding="utf-8")
        lines = [line.strip("# ").strip() for line in text.splitlines()
                 if line.strip() and not line.startswith("#")]
        snippet = " ".join(lines)
        limit = config["provider"]["max_context_chars_per_doc"]
        if limit > 0 and len(snippet) > limit:
            snippet = snippet[:limit].rsplit(" ", 1)[0].strip()
        document_rows.append({"path": relative_text, "imported_path": imported_name,
                              "doc_id": Path(imported_name).with_suffix("").as_posix(),
                              "sha256": sha256_file(source), "size_bytes": source.stat().st_size,
                              "expected_text": snippet})
    if not document_rows:
        raise ValidationError("candidate-retrieval-corpus-empty")
    (provider_input / "questions.jsonl").write_bytes(queries.read_bytes())
    provider_config = {"top_k": config["provider"]["top_k"],
                       "max_context_chars_per_doc": config["provider"]["max_context_chars_per_doc"],
                       "answer_policy": config["provider"]["answer_policy"]}
    (provider_input / "config.yaml").write_bytes(canonical(provider_config) + b"\n")
    (provider_input.parent / "documents.json").write_bytes(canonical(document_rows) + b"\n")
    return {"schema_version": "retrieval_arena.candidate_retrieval_launch.v1",
            "provider_image": config["provider"]["image"], "platform": "linux/amd64",
            "network": "none", "candidate_commit": config["candidate"]["commit"],
            "candidate_tree": config["candidate"]["tree"],
            "document_inventory_sha256": sha256_bytes(canonical(document_rows)),
            "provider_input_inventory": regular_inventory(provider_input),
            "provider_input_inventory_sha256": sha256_bytes(canonical(regular_inventory(provider_input)))}


def _validate_provenance(value: Any, context_ids: set[str], by_id: dict[Any, dict[str, Any]],
                         provider_input: Path | None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValidationError("candidate-retrieval-provider-provenance")
    for entry in value:
        _exact(entry, PROVENANCE_FIELDS, "candidate-retrieval-provider-provenance")
        heading = entry["section_heading"]
        if (not isinstance(entry["doc_id"], str) or entry["doc_id"] not in context_ids
                or heading is not None and not isinstance(heading, str)
                or any(isinstance(entry[name], bool) or not isinstance(entry[name], int)
                       for name in ("passage_index", "char_start", "char_end"))
                or entry["passage_index"] < 0 or not 0 <= entry["char_start"] < entry["char_end"]):
            raise ValidationError("candidate-retrieval-provider-provenance")
        if provider_input is not None:
            imported = by_id[entry["doc_id"]].get("imported_path")
            document = provider_input / "corpus" / imported if isinstance(imported, str) else None
            if (document is None or not document.is_file() or document.is_symlink()
                    or entry["char_end"] > len(document.read_text(encoding="utf-8"))):
                raise ValidationError("candidate-retrieval-provider-provenance")
    return value


def project_ranked_predictions(config: dict[str, Any], provider_output: Path, input_root: Path,
                               documents_path: Path, provider_input: Path | None = None,
                               ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    config = validate_config(config)
    predictions_path = provider_output / "predictions.jsonl"
    metadata_path = provider_output / "metadata.json"
    if (set(path.name for path in provider_output.iterdir()) != {"metadata.json", "predictions.jsonl"}
            or predictions_path.is_symlink() or metadata_path.is_symlink()):
        raise ValidationError("candidate-retrieval-provider-output-shape")
    metadata = _exact(json.loads(metadata_path.read_bytes()), {
        "name", "deterministic", "ranker", "top_k", "max_context_chars_per_doc", "answer_policy"
    }, "candidate-retrieval-provider-metadata-fields")
    provider = config["provider"]
    answer_policy = provider["answer_policy"]
    best_passage = answer_policy == "best_passage_v1"
    if metadata != {"name": PROVIDER_NAME, "deterministic": True, "ranker": "bm25",
                    "top_k": provider["top_k"],
                    "max_context_chars_per_doc": provider["max_context_chars_per_doc"],
                    "answer_policy": answer_policy}:
        raise ValidationError("candidate-retrieval-provider-metadata")
    rows = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    questions = [json.loads(line) for line in (input_root / config["queries_path"]).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if (not questions or any(not isinstance(row, dict) or set(row) != {"question_id", "question"}
                             or not isinstance(row["question_id"], str) or not row["question_id"]
                             or not isinstance(row["question"], str) or not row["question"] for row in questions)
            or [row["question_id"] for row in questions] != list(dict.fromkeys(row["question_id"] for row in questions))):
        raise ValidationError("candidate-retrieval-questions")
    if (len(rows) != len(questions)
            or [(row.get("question_id"), row.get("question")) for row in rows]
            != [(row["question_id"], row["question"]) for row in questions]):
        raise ValidationError("candidate-retrieval-query-completeness")
    documents = json.loads(documents_path.read_bytes())
    if not isinstance(documents, list):
        raise ValidationError("candidate-retrieval-document-inventory")
    by_id = {row.get("doc_id"): row for row in documents if isinstance(row, dict)}
    if len(by_id) != len(documents) or None in by_id:
        raise ValidationError("candidate-retrieval-document-inventory")
    result = []
    answers: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    row_fields = PROVIDER_ROW_FIELDS | {"answer_provenance"} if best_passage else PROVIDER_ROW_FIELDS
    for row in rows:
        _exact(row, row_fields, "candidate-retrieval-provider-row-fields")
        query_id = row["question_id"]
        if (not isinstance(query_id, str) or not query_id or query_id in seen_queries
                or not isinstance(row["generated_answer"], str)):
            raise ValidationError("candidate-retrieval-query-identity")
        seen_queries.add(query_id)
        context = row["retrieved_context"]
        if not isinstance(context, list) or len(context) > provider["top_k"]:
            raise ValidationError("candidate-retrieval-provider-context")
        items = []
        seen_items: set[str] = set()
        for source_rank, item in enumerate(context, 1):
            _exact(item, {"doc_id", "text", "score", "source", "rank"},
                   "candidate-retrieval-provider-item-fields")
            score = item["score"]
            admitted = by_id.get(item.get("doc_id"))
            if (item["source"] != "lexical_topk" or item["rank"] != source_rank
                    or not isinstance(item["doc_id"], str) or not item["doc_id"]
                    or item["doc_id"] in seen_items or isinstance(score, bool)
                    or not isinstance(score, (int, float)) or not math.isfinite(score)
                    or admitted is None or item["text"] != admitted.get("expected_text")):
                raise ValidationError("candidate-retrieval-provider-item")
            seen_items.add(item["doc_id"])
            items.append({"support_target_id": item["doc_id"], "rank": source_rank,
                          "score": float(score), "source_rank": source_rank,
                          "source_item_id": item["doc_id"],
                          "source_provenance_sha256": sha256_bytes(canonical(item))})
        expected = sorted(items, key=lambda item: (-item["score"], item["support_target_id"]))
        if items != expected:
            raise ValidationError("candidate-retrieval-provider-order")
        result.append({"schema_version": PREDICTION_SCHEMA, "query_id": query_id,
                       "context_item_budget_k": provider["top_k"], "max_rank": len(items),
                       "retrieval_exhausted": len(items) < provider["top_k"], "items": items,
                       "duplicate_collapse_count": 0, "duplicate_collapse_sha256": None})
        answer = {"query_id": query_id, "generated_answer": row["generated_answer"]}
        if best_passage:
            answer["answer_policy"] = answer_policy
            answer["answer_provenance"] = _validate_provenance(
                row["answer_provenance"], seen_items, by_id, provider_input)
        answers.append(answer)
    return result, answers
