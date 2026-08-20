"""Registered, non-interactive dual-profile container ingress.

This module deliberately owns only in-container admission and publication.  Host
mount construction and container lifetime remain the responsibility of the
PowerShell/POSIX adapters (and, when called by DocSpectra, its launcher).
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retrieval_arena.profile_evaluation import evaluate_profile_files


INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
REQUEST = "operator-request.json"
REQUEST_SCHEMA = "retrieval_arena.profile_evaluation_request.v1"
RECEIPT_SCHEMA = "retrieval_arena.profile_evaluation_operator_receipt.v1"


class AdmissionError(ValueError):
    """Stable, fail-closed operator admission failure."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _regular_inventory(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, str]]:
    if not root.is_dir() or root.is_symlink():
        raise AdmissionError("operator-root-invalid")
    ignored = exclude or set()
    rows: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in ignored:
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AdmissionError("operator-symlink-rejected")
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            raise AdmissionError("operator-nonregular-file-rejected")
        rows.append({"path": relative, "sha256": _file_sha(path)})
    return rows


def _inside(root: Path, relative: str) -> Path:
    candidate = root / relative
    if not relative or Path(relative).is_absolute() or "\\" in relative or any(part in ("", ".", "..") for part in Path(relative).parts):
        raise AdmissionError("operator-path-not-admitted")
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise AdmissionError("operator-path-not-admitted") from error
    return candidate


def _read_request() -> tuple[dict[str, Any], Path, list[dict[str, str]]]:
    request_path = INPUT / REQUEST
    if not request_path.is_file() or request_path.is_symlink():
        raise AdmissionError("operator-request-missing")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdmissionError("operator-request-invalid") from error
    expected = {"schema_version", "command", "config_path", "input_inventory", "input_inventory_sha256"}
    if not isinstance(request, dict) or set(request) != expected:
        raise AdmissionError("operator-request-fields-invalid")
    if request["schema_version"] != REQUEST_SCHEMA or request["command"] != "evaluate-profile":
        raise AdmissionError("operator-request-command-invalid")
    if not isinstance(request["input_inventory"], list) or request["input_inventory_sha256"] != _sha(_canonical(request["input_inventory"])):
        raise AdmissionError("operator-input-inventory-hash-invalid")
    inventory = request["input_inventory"]
    if any(not isinstance(row, dict) or set(row) != {"path", "sha256"} or not isinstance(row["path"], str) or not isinstance(row["sha256"], str) or len(row["sha256"]) != 64 for row in inventory):
        raise AdmissionError("operator-input-inventory-invalid")
    if inventory != sorted(inventory, key=lambda row: row["path"]):
        raise AdmissionError("operator-input-inventory-order-invalid")
    actual = _regular_inventory(INPUT, exclude={REQUEST})
    if inventory != actual:
        raise AdmissionError("operator-input-inventory-mismatch")
    if not isinstance(request["config_path"], str):
        raise AdmissionError("operator-config-path-invalid")
    config = _inside(INPUT, request["config_path"])
    if not config.is_file() or config.is_symlink():
        raise AdmissionError("operator-config-missing")
    return request, config, actual


def _admit_output() -> None:
    if not OUTPUT.is_dir() or OUTPUT.is_symlink():
        raise AdmissionError("operator-output-root-invalid")
    marker = OUTPUT / OWNER
    if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8") != "retrieval_arena.output_root.v1\n":
        raise AdmissionError("operator-output-owner-marker-invalid")
    if [path.name for path in OUTPUT.iterdir()] != [OWNER]:
        raise AdmissionError("operator-output-root-not-fresh")


def _receipt(request: dict[str, Any] | None, inputs: list[dict[str, str]], exit_code: int, started: str, error: str | None) -> dict[str, Any]:
    identity = {
        "source_commit": os.environ.get("RA_SOURCE_COMMIT", ""),
        "source_tree": os.environ.get("RA_SOURCE_TREE", ""),
        "dependency_lock_sha256": os.environ.get("RA_DEPENDENCY_LOCK_SHA256", ""),
        "image_reference": os.environ.get("RA_IMAGE_REFERENCE", ""),
        "platform": os.environ.get("RA_PLATFORM", ""),
    }
    value: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "command": "evaluate-profile",
        "argv": ["evaluate-profile", "--config", "/workspace/input/" + (request["config_path"] if request else ""), "--out-dir", "/workspace/output"],
        "effective_config_path": "/workspace/input/" + (request["config_path"] if request else ""),
        "effective_output_path": "/workspace/output",
        "request_sha256": _file_sha(INPUT / REQUEST) if (INPUT / REQUEST).is_file() else None,
        "input_inventory": inputs,
        "input_inventory_sha256": _sha(_canonical(inputs)),
        "output_inventory": _regular_inventory(OUTPUT, exclude={OWNER, "operator_receipt.v1.json"}),
        "identity": identity,
        "exit_classification": "completed" if exit_code == 0 else "admission_failure" if exit_code == 2 else "evaluator_failure",
        "exit_code": exit_code,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "error_code": error,
        "isolation": {"network": "none", "root_filesystem": "read_only", "input_mount": "read_only", "source_mount": "read_only", "tmpdir": "/tmp"},
        "publication": {"partial_outputs_published": False, "cleanup": "container_ephemeral_tmpfs"},
        "receipt_sha256": "0" * 64,
    }
    value["receipt_sha256"] = _sha(_canonical(value))
    return value


def main() -> int:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    request: dict[str, Any] | None = None
    inputs: list[dict[str, str]] = []
    error: str | None = None
    exit_code = 1
    output_admitted = False
    published: list[Path] = []
    try:
        _admit_output()
        output_admitted = True
        request, config, inputs = _read_request()
        staging = Path("/tmp/evaluation")
        staging.mkdir(parents=True, exist_ok=False)
        evaluated = evaluate_profile_files(config, staging)
        try:
            for artifact in staging.iterdir():
                if artifact.is_symlink() or not artifact.is_file():
                    raise AdmissionError("operator-staged-output-invalid")
                destination = OUTPUT / (artifact.name + ".pending")
                destination.write_bytes(artifact.read_bytes())
                published.append(destination)
            for pending in published:
                pending.replace(OUTPUT / pending.name.removesuffix(".pending"))
        except Exception:
            for destination in published:
                destination.unlink(missing_ok=True)
                destination.with_name(destination.name.removesuffix(".pending")).unlink(missing_ok=True)
            raise
        print(json.dumps({"evaluation_profile": evaluated["result"]["evaluation_profile"], "result_sha256": evaluated["result"]["result_sha256"]}, sort_keys=True))
        exit_code = 0
    except AdmissionError as exc:
        error = str(exc)
        print(error, file=sys.stderr)
        exit_code = 2
    except Exception as exc:  # evaluator error remains bounded operational evidence
        error = type(exc).__name__
        print(error, file=sys.stderr)
        exit_code = 1
    finally:
        if output_admitted and OUTPUT.is_dir() and not OUTPUT.is_symlink():
            try:
                receipt = _receipt(request, inputs, exit_code, started, error)
                (OUTPUT / "operator_receipt.v1.json").write_bytes(json.dumps(receipt, sort_keys=True, indent=2).encode("utf-8") + b"\n")
            except Exception as exc:
                print(f"operator-receipt-write-failed:{type(exc).__name__}", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
