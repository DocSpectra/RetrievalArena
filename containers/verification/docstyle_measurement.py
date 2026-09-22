"""S052: the ``measure-docstyle`` operator entry -- one phase, candidate mounted read-only.

Reads ``/workspace/input`` (``operator-request.json``, ``config.json`` and, when the
request names it, the campaign's ``support.json``), admits ``/workspace/candidate``
at exactly the declared commit and tree, measures every admitted ``.md`` document
with the DocEng Appendix B definitions, and writes to ``/workspace/output``
``docstyle_metrics.v1.jsonl``, ``docstyle_summary.v1.json``,
``docstyle_metrics_manifest.json`` and ``operator_receipt.v1.json``. No
retrieval, no question, no generative model.

S055: a config with a ``lexicon`` block also harvests the repository lexicon from the
candidate and writes ``repo_lexicon.v1.json``; a config with ``lexicon_path`` reads a
lexicon from the input root instead. The table schema is ``docstyle_metrics.v2``
(rows gained the identifier columns); file names are unchanged.
"""

from __future__ import annotations

import json
import os
import platform
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from retrieval_arena.candidate_retrieval import admit_candidate, canonical, regular_inventory, sha256_bytes, sha256_file
from retrieval_arena.docstyle_measurement import (
    REQUEST_SCHEMA, inventory_corpus, lexicon_for_config, load_catalogs, load_spacy, load_support_ids, manifest,
    measure_rows, summarize, table_bytes, validate_config,
)
from retrieval_arena.repo_lexicon import LEXICON_FILE, load_stop_catalogs, mention_index

INPUT = Path("/workspace/input")
CANDIDATE = Path("/workspace/candidate")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
RECEIPT_SCHEMA = "retrieval_arena.docstyle_measurement_operator_receipt.v1"
TABLE = "docstyle_metrics.v1.jsonl"
SUMMARY = "docstyle_summary.v1.json"
MANIFEST = "docstyle_metrics_manifest.json"
OUTPUTS = (TABLE, SUMMARY, MANIFEST)
LEXICON = LEXICON_FILE


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _identity():
    return {"source_commit": os.environ.get("RA_SOURCE_COMMIT", ""),
            "source_tree": os.environ.get("RA_SOURCE_TREE", ""),
            "dependency_lock_sha256": os.environ.get("RA_DEPENDENCY_LOCK_SHA256", ""),
            "image_reference": os.environ.get("RA_IMAGE_REFERENCE", ""),
            "platform": os.environ.get("RA_PLATFORM", "")}


def _safe_hash(path: Path):
    try:
        return sha256_file(path) if stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink() else None
    except OSError:
        return None


def _config():
    request = json.loads((INPUT / "operator-request.json").read_bytes())
    if (not isinstance(request, dict) or set(request) != {
            "schema_version", "command", "config_path", "input_inventory", "input_inventory_sha256"
        } or request["schema_version"] != REQUEST_SCHEMA
            or request["command"] != "measure-docstyle" or request["config_path"] != "config.json"):
        raise ValueError("docstyle-request")
    inventory = regular_inventory(INPUT)
    actual_without_request = [row for row in inventory if row["path"] != "operator-request.json"]
    if (request["input_inventory"] != actual_without_request
            or request["input_inventory_sha256"] != sha256_bytes(canonical(actual_without_request))):
        raise ValueError("docstyle-request-inventory")
    config = validate_config(json.loads((INPUT / "config.json").read_bytes()))
    expected = ({"config.json"} | ({config["support_path"]} if "support_path" in config else set())
                | ({config["lexicon_path"]} if "lexicon_path" in config else set()))
    if {row["path"] for row in actual_without_request} != expected:
        raise ValueError("docstyle-input-root-contents")
    return request, config


def _receipt(status, exit_code, started, config, error=None, extra=None):
    value = {"schema_version": RECEIPT_SCHEMA, "command": "measure-docstyle", "status": status,
             "exit_code": exit_code, "error_code": None if error is None else str(error),
             "corpus_id": config.get("corpus_id") if isinstance(config, dict) else None,
             "candidate": config.get("candidate") if isinstance(config, dict) else None,
             "corpus_exclude_policy": (config.get("corpus", {}).get("exclude_policy")
                                       if isinstance(config, dict) and isinstance(config.get("corpus"), dict) else None),
             "corpus_faq_patterns": (config.get("corpus", {}).get("faq_patterns")
                                     if isinstance(config, dict) and isinstance(config.get("corpus"), dict) else None),
             "request_sha256": _safe_hash(INPUT / "operator-request.json"),
             "config_sha256": _safe_hash(INPUT / "config.json"),
             "excluded_document_count": None, "excluded_document_ids": None, "row_count": 0, "error_count": None,
             "outputs": {name: None for name in OUTPUTS + (LEXICON,)},
             "identity": _identity(),
             "isolation": {"network": "none", "candidate_mount": "read_only", "input_mount": "read_only",
                           "root_filesystem": "read_only"},
             "started_at": started, "finished_at": _now(), "receipt_sha256": "0" * 64}
    value.update(extra or {})
    value["receipt_sha256"] = sha256_bytes(canonical(value))
    return value


def main() -> int:
    started = _now()
    config = None
    try:
        if (OUTPUT / OWNER).read_text(encoding="utf-8") != "retrieval_arena.output_root.v1\n":
            raise ValueError("docstyle-output-owner")
        if set(path.name for path in OUTPUT.iterdir()) != {OWNER}:
            raise ValueError("docstyle-output-not-fresh")
        request, config = _config()
        admit_candidate(CANDIDATE, config["candidate"])
        corpus_root = CANDIDATE if config["corpus"]["root"] == "." else CANDIDATE / config["corpus"]["root"]
        rows, excluded = inventory_corpus(corpus_root, config["corpus"])
        support = load_support_ids(INPUT / config["support_path"]) if "support_path" in config else None
        catalogs = load_catalogs()
        stops = load_stop_catalogs()
        lexicon, lexicon_entry = lexicon_for_config(config, CANDIDATE, INPUT, stops)
        lexicon_index = mention_index(lexicon, stops) if lexicon is not None else None
        nlp, spacy_identity = load_spacy()
        table = measure_rows(rows, config["corpus_id"], config["candidate"]["commit"], nlp, catalogs, lexicon_index)
        table_data = table_bytes(table)
        summary = summarize(table, support[0] if support is not None else None)
        manifest_value = manifest(config, spacy_identity, catalogs, table_data, table, excluded, support,
                                  platform.python_version(), lexicon_entry, stops)
        pending = {TABLE: table_data, SUMMARY: canonical(summary) + b"\n", MANIFEST: canonical(manifest_value) + b"\n"}
        if lexicon_entry["origin"] == "built":
            pending[LEXICON] = canonical(lexicon) + b"\n"
        for name, data in pending.items():
            (OUTPUT / (name + ".pending")).write_bytes(data)
        extra = {"excluded_document_count": len(excluded),
                 "excluded_document_ids": manifest_value["excluded_document_ids"],
                 "row_count": len(table), "error_count": manifest_value["error_count"],
                 "outputs": {LEXICON: None, **{name: sha256_bytes(data) for name, data in pending.items()}}}
        receipt = _receipt("completed", 0, started, config, extra=extra)
        (OUTPUT / "operator_receipt.v1.json.pending").write_bytes(canonical(receipt) + b"\n")
        for name in pending:
            (OUTPUT / (name + ".pending")).replace(OUTPUT / name)
        (OUTPUT / "operator_receipt.v1.json.pending").replace(OUTPUT / "operator_receipt.v1.json")
        return 0
    except Exception as error:
        for path in OUTPUT.glob("*.pending"):
            path.unlink(missing_ok=True)
        for name in OUTPUTS + (LEXICON,):
            (OUTPUT / name).unlink(missing_ok=True)
        if (OUTPUT.is_dir() and (OUTPUT / OWNER).is_file()
                and (OUTPUT / OWNER).read_text(encoding="utf-8") == "retrieval_arena.output_root.v1\n"):
            (OUTPUT / "operator_receipt.v1.json").write_bytes(canonical(_receipt("failed", 1, started, config, error)) + b"\n")
        print(str(error), file=sys.stderr)
        return 1
