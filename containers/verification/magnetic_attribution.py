"""The ``measure-magnetic-attribution`` operator entry: one phase, no candidate, no model.

Reads ``/workspace/input`` (``operator-request.json``, ``config.json`` and the graph bundles the config names),
runs every job with ``retrieval_arena.graph.magnetic_attribution`` and writes to ``/workspace/output``:
``magnetic_attribution.v1.jsonl`` (one record per job, config order), ``magnetic_attribution_manifest.json``
(record and artifact hashes), ``artifacts/`` (per graph view and q: node-mass and edge-energy tables and the
ground-cluster vectors as ``.npy``) and ``operator_receipt.v1.json``. BLAS runs single-threaded so repeated
runs on one image are bitwise comparable.
"""

from __future__ import annotations

import os

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"

import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_bytes, sha256_file  # noqa: E402
from retrieval_arena.graph import magnetic_attribution as ma  # noqa: E402
from retrieval_arena.graph import magnetic_spectrum as ms  # noqa: E402

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
REQUEST_SCHEMA = "retrieval_arena.magnetic_attribution_operator_request.v1"
RECEIPT_SCHEMA = "retrieval_arena.magnetic_attribution_operator_receipt.v1"
TABLE = "magnetic_attribution.v1.jsonl"
MANIFEST = "magnetic_attribution_manifest.json"
ARTIFACTS = "artifacts"
OUTPUTS = (TABLE, MANIFEST)


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
            or request["command"] != "measure-magnetic-attribution" or request["config_path"] != "config.json"):
        raise ValueError("magnetic-attribution-request")
    inventory = regular_inventory(INPUT)
    actual = [row for row in inventory if row["path"] != "operator-request.json"]
    if request["input_inventory"] != actual or request["input_inventory_sha256"] != sha256_bytes(canonical(actual)):
        raise ValueError("magnetic-attribution-request-inventory")
    config = json.loads((INPUT / "config.json").read_bytes())
    ma.validate_jobs_config(config)
    expected = {"config.json"} | {job[key] for job in config["jobs"]
                                  for key in ("bundle", "bundle_a", "bundle_b") if key in job}
    if {row["path"] for row in actual} != expected:
        raise ValueError("magnetic-attribution-input-root-contents")
    return request, config


def _receipt(status, exit_code, started, error=None, extra=None):
    value = {"schema_version": RECEIPT_SCHEMA, "command": "measure-magnetic-attribution", "status": status,
             "exit_code": exit_code, "error_code": None if error is None else str(error),
             "metric_version": ma.VERSION, "metric_code_sha256": ma.code_sha256(),
             "spectrum_metric_version": ms.VERSION, "spectrum_code_sha256": ms.code_sha256(),
             "request_sha256": sha256_file(INPUT / "operator-request.json") if (INPUT / "operator-request.json").is_file() else None,
             "config_sha256": sha256_file(INPUT / "config.json") if (INPUT / "config.json").is_file() else None,
             "job_count": None, "outputs": {name: None for name in OUTPUTS},
             "identity": _identity(), "runtime": ms.runtime_identity(),
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
    shutil.rmtree(OUTPUT / ARTIFACTS, ignore_errors=True)


def main() -> int:
    started = _now()
    try:
        if (OUTPUT / OWNER).read_text(encoding="utf-8") != "retrieval_arena.output_root.v1\n":
            raise ValueError("magnetic-attribution-output-owner")
        if set(path.name for path in OUTPUT.iterdir()) != {OWNER}:
            raise ValueError("magnetic-attribution-output-not-fresh")
        _, config = _request()
        t0 = time.time()
        rows, artifacts = ma.run_jobs(config, INPUT)
        metric_version = ('magnetic_attribution.v2' if config['schema_version'] == ma.CONFIG_SCHEMA_V2 else ma.VERSION)
        table = b"".join(canonical(row) + b"\n" for row in rows)
        inventory = []
        for name in sorted(artifacts):
            path = OUTPUT / ARTIFACTS / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(artifacts[name])
            inventory.append({"path": f"{ARTIFACTS}/{name}", "sha256": sha256_bytes(artifacts[name]),
                              "size_bytes": len(artifacts[name])})
        manifest = {"schema_version": "retrieval_arena.magnetic_attribution_manifest.v1", "metric_version": metric_version,
                    "metric_code_sha256": ma.code_sha256(), "spectrum_code_sha256": ms.code_sha256(),
                    "config_sha256": sha256_file(INPUT / "config.json"), "runtime": ms.runtime_identity(),
                    "jobs": [{"job_id": r["job_id"], "kind": r["kind"], "receipt_sha256": sha256_bytes(canonical(r))}
                             for r in rows],
                    "artifacts": inventory, "artifact_inventory_sha256": sha256_bytes(canonical(inventory)),
                    "table_sha256": sha256_bytes(table), "measure_seconds": round(time.time() - t0, 1)}
        pending = {TABLE: table, MANIFEST: canonical(manifest) + b"\n"}
        for name, data in pending.items():
            (OUTPUT / (name + ".pending")).write_bytes(data)
        extra = {"metric_version": metric_version, "job_count": len(rows), "outputs": {name: sha256_bytes(data) for name, data in pending.items()},
                 "artifact_count": len(inventory), "artifact_inventory_sha256": manifest["artifact_inventory_sha256"]}
        receipt = _receipt("completed", 0, started, extra=extra)
        (OUTPUT / "operator_receipt.v1.json.pending").write_bytes(canonical(receipt) + b"\n")
        for name in pending:
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
