"""The ``compare-answer-elements`` operator entry: one phase, no candidate, no model.

Reads ``/workspace/input`` (``operator-request.json``, ``config.json`` and every file the config names), compares
saved answers and saved element judgments with ``retrieval_arena.answer_element_comparison`` and writes to
``/workspace/output``: the row, answer, issue, cell, contrast, element, task and replicate-pair tables (JSONL),
``answer_element.summary.json`` (coverage, historical reconstruction, breakdowns), the review selection, packet,
blank response and key when a selection is declared, ``answer_element_manifest.json`` and
``operator_receipt.v1.json``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_bytes, sha256_file
from retrieval_arena import answer_element_comparison as aec

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
REQUEST_SCHEMA = "retrieval_arena.answer_element_operator_request.v1"
RECEIPT_SCHEMA = "retrieval_arena.answer_element_operator_receipt.v1"
TABLES = {"answer_element.rows.jsonl": "rows", "answer_element.answers.jsonl": "answers",
          "answer_element.issues.jsonl": "issues", "answer_element.cells.jsonl": "cells",
          "answer_element.contrasts.jsonl": "contrasts", "answer_element.elements.jsonl": "elements",
          "answer_element.tasks.jsonl": "tasks", "answer_element.pairs.jsonl": "pairs"}
SUMMARY = "answer_element.summary.json"
REVIEW = {"answer_element.selection.json": "selection", "answer_element.review_packet.json": "packet",
          "answer_element.review_response_blank.json": "response", "answer_element.review_key.json": "key"}
MANIFEST = "answer_element_manifest.json"
OUTPUTS = (*TABLES, SUMMARY, *REVIEW, MANIFEST)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _identity():
    return {"source_commit": os.environ.get("RA_SOURCE_COMMIT", ""),
            "source_tree": os.environ.get("RA_SOURCE_TREE", ""),
            "dependency_lock_sha256": os.environ.get("RA_DEPENDENCY_LOCK_SHA256", ""),
            "image_reference": os.environ.get("RA_IMAGE_REFERENCE", ""),
            "platform": os.environ.get("RA_PLATFORM", "")}


def _request():
    request = json.loads((INPUT / "operator-request.json").read_bytes())
    if (not isinstance(request, dict) or set(request) != {
            "schema_version", "command", "config_path", "input_inventory", "input_inventory_sha256"
        } or request["schema_version"] != REQUEST_SCHEMA
            or request["command"] != "compare-answer-elements" or request["config_path"] != "config.json"):
        raise ValueError("answer-element-request")
    inventory = regular_inventory(INPUT)
    actual = [row for row in inventory if row["path"] != "operator-request.json"]
    if request["input_inventory"] != actual or request["input_inventory_sha256"] != sha256_bytes(canonical(actual)):
        raise ValueError("answer-element-request-inventory")
    config = json.loads((INPUT / "config.json").read_bytes())
    aec.validate_config(config)
    present = {row["path"] for row in actual}
    named = {"config.json"} | aec.input_paths(config)
    if present != named:
        raise ValueError("answer-element-input-root-contents")
    return request, config


def _receipt(status, exit_code, started, error=None, extra=None):
    value = {"schema_version": RECEIPT_SCHEMA, "command": "compare-answer-elements", "status": status,
             "exit_code": exit_code, "error_code": None if error is None else str(error),
             "metric_version": aec.VERSION, "metric_code_sha256": aec.code_sha256(),
             "request_sha256": sha256_file(INPUT / "operator-request.json") if (INPUT / "operator-request.json").is_file() else None,
             "config_sha256": sha256_file(INPUT / "config.json") if (INPUT / "config.json").is_file() else None,
             "answer_count": None, "outputs": {name: None for name in OUTPUTS}, "identity": _identity(),
             "isolation": {"network": "none", "input_mount": "read_only", "root_filesystem": "read_only"},
             "started_at": started, "finished_at": _now(), "receipt_sha256": "0" * 64}
    value.update(extra or {})
    value["receipt_sha256"] = sha256_bytes(canonical(value))
    return value


def _cleanup():
    for path in OUTPUT.glob("*.pending"):
        path.unlink(missing_ok=True)
    for name in OUTPUTS:
        (OUTPUT / name).unlink(missing_ok=True)


def main() -> int:
    started = _now()
    try:
        if (OUTPUT / OWNER).read_text(encoding="utf-8") != "retrieval_arena.output_root.v1\n":
            raise ValueError("answer-element-output-owner")
        if set(path.name for path in OUTPUT.iterdir()) != {OWNER}:
            raise ValueError("answer-element-output-not-fresh")
        _, config = _request()
        t0 = time.time()
        result = aec.run(config, INPUT)
        data = {name: b"".join(canonical(row) + b"\n" for row in result[key]) for name, key in TABLES.items()}
        summary = {"summary": result["summary"], "historical": result["historical"],
                   "breakdowns": result["breakdowns"], "aggregation": aec.AGGREGATION,
                   "aggregation_policy": aec.AGGREGATION_POLICY, "relaxed_rule": aec.RELAXED_RULE}
        data[SUMMARY] = canonical(summary) + b"\n"
        for name, key in REVIEW.items():
            if key in result:
                data[name] = canonical(result[key]) + b"\n"
        manifest = {"schema_version": "retrieval_arena.answer_element_manifest.v1", "metric_version": aec.VERSION,
                    "metric_code_sha256": aec.code_sha256(), "config_sha256": sha256_file(INPUT / "config.json"),
                    "rows": {name: len(result[key]) for name, key in TABLES.items()},
                    "outputs": {name: sha256_bytes(blob) for name, blob in data.items()},
                    "measure_seconds": round(time.time() - t0, 1)}
        data[MANIFEST] = canonical(manifest) + b"\n"
        for name, blob in data.items():
            (OUTPUT / (name + ".pending")).write_bytes(blob)
        extra = {"answer_count": len(config["answers"]),
                 "outputs": {name: (sha256_bytes(data[name]) if name in data else None) for name in OUTPUTS}}
        receipt = _receipt("completed", 0, started, extra=extra)
        (OUTPUT / "operator_receipt.v1.json.pending").write_bytes(canonical(receipt) + b"\n")
        for name in data:
            (OUTPUT / (name + ".pending")).replace(OUTPUT / name)
        (OUTPUT / "operator_receipt.v1.json.pending").replace(OUTPUT / "operator_receipt.v1.json")
        return 0
    except Exception as error:
        _cleanup()
        if (OUTPUT.is_dir() and (OUTPUT / OWNER).is_file()
                and (OUTPUT / OWNER).read_text(encoding="utf-8") == "retrieval_arena.output_root.v1\n"):
            (OUTPUT / "operator_receipt.v1.json").write_bytes(canonical(_receipt("failed", 1, started, error)) + b"\n")
        print(str(error), file=sys.stderr)
        return 1
