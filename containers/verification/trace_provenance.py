"""The ``audit-trace-provenance`` operator entry: one phase, no candidate, no model.

Reads ``/workspace/input`` (``operator-request.json``, ``config.json`` and every file the config names), audits
every declared cell with ``retrieval_arena.trace_provenance`` and writes to ``/workspace/output``:
``trace_provenance.traces.jsonl``, ``trace_provenance.actions.jsonl``, ``trace_provenance.citations.jsonl`` (config
order), ``trace_provenance.variants.json``, ``trace_provenance.summary.json``, ``trace_provenance_manifest.json``
(row counts and hashes) and ``operator_receipt.v1.json``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_bytes, sha256_file
from retrieval_arena import trace_provenance as tp

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
REQUEST_SCHEMA = "retrieval_arena.trace_provenance_operator_request.v1"
RECEIPT_SCHEMA = "retrieval_arena.trace_provenance_operator_receipt.v1"
TABLES = ("trace_provenance.traces.jsonl", "trace_provenance.actions.jsonl", "trace_provenance.citations.jsonl")
VARIANTS = "trace_provenance.variants.json"
SUMMARY = "trace_provenance.summary.json"
MANIFEST = "trace_provenance_manifest.json"
OUTPUTS = (*TABLES, VARIANTS, SUMMARY, MANIFEST)


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
            or request["command"] != "audit-trace-provenance" or request["config_path"] != "config.json"):
        raise ValueError("trace-provenance-request")
    inventory = regular_inventory(INPUT)
    actual = [row for row in inventory if row["path"] != "operator-request.json"]
    if request["input_inventory"] != actual or request["input_inventory_sha256"] != sha256_bytes(canonical(actual)):
        raise ValueError("trace-provenance-request-inventory")
    config = json.loads((INPUT / "config.json").read_bytes())
    tp.validate_config(config)
    present = {row["path"] for row in actual}
    named = {"config.json"} | tp.input_paths(config)
    corpora = {v["corpus"].rstrip("/") + "/" for v in config["variants"]}
    extra = {p for p in present - named if not any(p.startswith(c) for c in corpora)}
    if not named <= present or extra:
        raise ValueError("trace-provenance-input-root-contents")
    return request, config


def _receipt(status, exit_code, started, error=None, extra=None):
    value = {"schema_version": RECEIPT_SCHEMA, "command": "audit-trace-provenance", "status": status,
             "exit_code": exit_code, "error_code": None if error is None else str(error),
             "metric_version": tp.VERSION, "metric_code_sha256": tp.code_sha256(),
             "request_sha256": sha256_file(INPUT / "operator-request.json") if (INPUT / "operator-request.json").is_file() else None,
             "config_sha256": sha256_file(INPUT / "config.json") if (INPUT / "config.json").is_file() else None,
             "cell_count": None, "outputs": {name: None for name in OUTPUTS}, "identity": _identity(),
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
            raise ValueError("trace-provenance-output-owner")
        if set(path.name for path in OUTPUT.iterdir()) != {OWNER}:
            raise ValueError("trace-provenance-output-not-fresh")
        _, config = _request()
        t0 = time.time()
        traces, actions, citations, variants = tp.run_jobs(config, INPUT)
        data = {name: b"".join(canonical(row) + b"\n" for row in rows)
                for name, rows in zip(TABLES, (traces, actions, citations))}
        data[VARIANTS] = canonical(variants) + b"\n"
        data[SUMMARY] = canonical(tp.summarize(traces, actions, citations)) + b"\n"
        manifest = {"schema_version": "retrieval_arena.trace_provenance_manifest.v1", "metric_version": tp.VERSION,
                    "metric_code_sha256": tp.code_sha256(), "config_sha256": sha256_file(INPUT / "config.json"),
                    "rows": {TABLES[0]: len(traces), TABLES[1]: len(actions), TABLES[2]: len(citations)},
                    "outputs": {name: sha256_bytes(blob) for name, blob in data.items()},
                    "replay_policy": tp.REPLAY_POLICY, "span_policy": tp.SPAN_POLICY,
                    "measure_seconds": round(time.time() - t0, 1)}
        data[MANIFEST] = canonical(manifest) + b"\n"
        for name, blob in data.items():
            (OUTPUT / (name + ".pending")).write_bytes(blob)
        extra = {"cell_count": len(config["cells"]), "outputs": {name: sha256_bytes(blob) for name, blob in data.items()}}
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
