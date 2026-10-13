"""Generic, atomic immutable-container review-packet export/import ingress."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from retrieval_arena.review_packets import build_review_packet, import_judgments
from retrieval_arena.schemas import read_jsonl

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
REQUEST = "operator-request.json"
SCHEMA = "retrieval_arena.review_packet_operator_request.v1"
RECEIPT_SCHEMA = "retrieval_arena.review_packet_operator_receipt.v1"
RECEIPT = "operator_receipt.v1.json"
STAGING = ".review-packet-staging"


class AdmissionError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _inventory(root: Path, *, exclude: set[str] | None = None) -> list[dict[str, str]]:
    if not root.is_dir() or root.is_symlink():
        raise AdmissionError("review-packet-root-invalid")
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in (exclude or set()):
            continue
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AdmissionError("review-packet-symlink-rejected")
        if path.is_dir():
            continue
        if not stat.S_ISREG(mode):
            raise AdmissionError("review-packet-nonregular-input")
        rows.append({"path": relative, "sha256": _sha(path.read_bytes())})
    return rows


def _inside(relative: object) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or Path(relative).is_absolute() or any(part in ("", ".", "..") for part in Path(relative).parts):
        raise AdmissionError("review-packet-path-not-admitted")
    return INPUT / relative


def _request() -> dict[str, Any]:
    path = INPUT / REQUEST
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdmissionError("review-packet-request-invalid") from error
    fields = {"schema_version", "command", "candidates_path", "policy_path", "judgment_artifact_path", "input_inventory", "input_inventory_sha256"}
    if not isinstance(request, dict) or set(request) != fields:
        raise AdmissionError("review-packet-request-fields-invalid")
    if request["schema_version"] != SCHEMA or request["command"] != "review-packet":
        raise AdmissionError("review-packet-request-command-invalid")
    if not isinstance(request["input_inventory"], list) or request["input_inventory_sha256"] != _sha(_canonical(request["input_inventory"])):
        raise AdmissionError("review-packet-input-inventory-hash-invalid")
    if request["input_inventory"] != _inventory(INPUT, exclude={REQUEST}):
        raise AdmissionError("review-packet-input-inventory-mismatch")
    for name in ("candidates_path", "policy_path"):
        path = _inside(request[name])
        if not path.is_file() or path.is_symlink():
            raise AdmissionError("review-packet-input-missing")
    if request["judgment_artifact_path"] is not None:
        path = _inside(request["judgment_artifact_path"])
        if not path.is_file() or path.is_symlink():
            raise AdmissionError("review-packet-judgment-missing")
    return request


def _admit_output() -> None:
    marker = OUTPUT / OWNER
    if not OUTPUT.is_dir() or OUTPUT.is_symlink() or not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8") != "retrieval_arena.output_root.v1\n":
        raise AdmissionError("review-packet-output-owner-invalid")
    if [path.name for path in OUTPUT.iterdir()] != [OWNER]:
        raise AdmissionError("review-packet-output-not-fresh")


def _write(path: Path, value: dict[str, Any]) -> None:
    """Publish the canonical byte form required for fixture identity checks."""
    path.write_bytes(_canonical(value) + b"\n")


def _receipt(request: dict[str, Any] | None, inputs: list[dict[str, str]], outputs: list[dict[str, str]], *, code: int, error: str | None, started: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "command": "review-packet",
        "request_sha256": _sha((INPUT / REQUEST).read_bytes()) if (INPUT / REQUEST).is_file() else None,
        "input_inventory": inputs,
        "input_inventory_sha256": _sha(_canonical(inputs)),
        "output_inventory": outputs,
        "output_inventory_sha256": _sha(_canonical(outputs)),
        "identity": {"source_commit": os.environ.get("RA_SOURCE_COMMIT", ""), "source_tree": os.environ.get("RA_SOURCE_TREE", ""), "dependency_lock_sha256": os.environ.get("RA_DEPENDENCY_LOCK_SHA256", ""), "image_reference": os.environ.get("RA_IMAGE_REFERENCE", ""), "platform": os.environ.get("RA_PLATFORM", "")},
        "exit_classification": "completed" if code == 0 else "admission_failure" if code == 2 else "operator_failure",
        "exit_code": code,
        "error_code": error,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "isolation": {"network": "none", "root_filesystem": "read_only", "input_mount": "read_only", "source_mount": "read_only", "tmpdir": "/tmp"},
        "publication": {"atomic": True, "partial_outputs_published": False, "cleanup": "staging_removed"},
        "receipt_sha256": "0" * 64,
    }
    value["receipt_sha256"] = _sha(_canonical(value))
    return value


def main() -> int:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    request: dict[str, Any] | None = None
    inputs: list[dict[str, str]] = []
    admitted = False
    code = 1
    error: str | None = None
    stage: Path | None = None
    success: dict[str, Any] | None = None
    try:
        _admit_output(); admitted = True
        request = _request(); inputs = request["input_inventory"]
        # Build and import entirely below a private output staging directory.
        stage = OUTPUT / STAGING; stage.mkdir()
        packet = build_review_packet(read_jsonl(_inside(request["candidates_path"])), json.loads(_inside(request["policy_path"]).read_text(encoding="utf-8")))
        _write(stage / "review_packet.v1.json", packet)
        if request["judgment_artifact_path"] is not None:
            judgment = json.loads(_inside(request["judgment_artifact_path"]).read_text(encoding="utf-8"))
            _write(stage / "judgment_import.v1.json", import_judgments(judgment, packet, json.loads(_inside(request["policy_path"]).read_text(encoding="utf-8"))))
        staged = _inventory(stage)
        for row in staged:
            pending = OUTPUT / (row["path"] + ".pending")
            pending.write_bytes((stage / row["path"]).read_bytes())
        for row in staged:
            (OUTPUT / (row["path"] + ".pending")).replace(OUTPUT / row["path"])
        shutil.rmtree(stage); stage = None
        success = {"packet_sha256": packet["packet_sha256"], "frame_count": packet["frame_count"], "imported": request["judgment_artifact_path"] is not None}
        code = 0
    except AdmissionError as exc:
        error = str(exc); print(error, file=sys.stderr); code = 2
    except Exception as exc:
        error = type(exc).__name__; print(error, file=sys.stderr); code = 1
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        # Roll back every non-terminal output after a failed import/build.
        if admitted and code != 0:
            for path in list(OUTPUT.iterdir()):
                if path.name not in {OWNER, RECEIPT}:
                    if path.is_dir(): shutil.rmtree(path, ignore_errors=True)
                    else: path.unlink(missing_ok=True)
        if admitted:
            try:
                outputs = _inventory(OUTPUT, exclude={OWNER, RECEIPT})
                _write(OUTPUT / RECEIPT, _receipt(request, inputs, outputs, code=code, error=error, started=started))
            except Exception as exc:
                # A terminal receipt is part of the atomic publication set.
                # If it cannot be written, no packet/import/receipt survives.
                for path in list(OUTPUT.iterdir()):
                    if path.name != OWNER:
                        if path.is_dir(): shutil.rmtree(path, ignore_errors=True)
                        else: path.unlink(missing_ok=True)
                success = None; code = 1
                print(f"review-packet-receipt-write-failed:{type(exc).__name__}", file=sys.stderr)
    if code == 0 and success is not None:
        print(json.dumps(success, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
