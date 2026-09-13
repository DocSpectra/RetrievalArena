"""Two-phase immutable ingress for the candidate-retrieve host carrier."""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from retrieval_arena.candidate_retrieval import (
    GRAPH_EDGES_FILE, TRACES_DIR, canonical, prepare_provider_input, project_ranked_predictions,
    provider_profile, regular_inventory, sha256_bytes, sha256_file, validate_config,
)

INPUT = Path("/workspace/input")
CANDIDATE = Path("/workspace/candidate")
OUTPUT = Path("/workspace/output")
WORK = OUTPUT / ".candidate-retrieval-work"
OWNER = ".retrieval-arena-output-owner"


def _identity():
    return {"source_commit": os.environ.get("RA_SOURCE_COMMIT", ""),
            "source_tree": os.environ.get("RA_SOURCE_TREE", ""),
            "dependency_lock_sha256": os.environ.get("RA_DEPENDENCY_LOCK_SHA256", ""),
            "image_reference": os.environ.get("RA_IMAGE_REFERENCE", ""),
            "platform": os.environ.get("RA_PLATFORM", "")}


def _safe_hash(path):
    try:
        return sha256_file(path) if stat.S_ISREG(path.lstat().st_mode) and not path.is_symlink() else None
    except OSError:
        return None


def _safe_inventory(path):
    try:
        return regular_inventory(path), True
    except Exception:
        return [], False


def _failure(error, exit_code, started, request=None, config=None):
    provider_inventory, provider_inventory_available = _safe_inventory(WORK / "provider-output")
    value = {"schema_version": "retrieval_arena.candidate_retrieval_operator_receipt.v1",
             "command": "candidate-retrieve", "status": "failed", "exit_code": exit_code,
             "error_code": str(error),
             "experiment_id": config.get("experiment_id") if isinstance(config, dict) else None,
             "execution_id": config.get("execution_id") if isinstance(config, dict) else None,
             "output_identity": config.get("output_identity") if isinstance(config, dict) else None,
             "candidate": config.get("candidate") if isinstance(config, dict) else None,
             "provider": config.get("provider") if isinstance(config, dict) else None,
             "request_sha256": _safe_hash(INPUT / "operator-request.json"),
             "config_sha256": _safe_hash(INPUT / "config.json"),
             "provider_output_inventory": provider_inventory,
             "provider_output_inventory_available": provider_inventory_available,
             "predictions_sha256": None, "generated_answers_sha256": None,
             "prediction_count": 0, "generated_answer_count": 0,
             "graph_edges_sha256": None, "traces_inventory_sha256": None, "navigation_sha256": None,
             "identity": _identity(),
             "isolation": {"network": _network(config), "candidate_mount": "read_only",
                           "input_mount": "read_only", "root_filesystem": "read_only"},
             "started_at": started,
             "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
             "receipt_sha256": "0" * 64}
    value["receipt_sha256"] = sha256_bytes(canonical(value))
    return value


def _network(config):
    """The provider container's network, from the declared profile (S026: the navigator alone
    reaches the host model alias; every retriever runs with network none)."""
    try:
        return provider_profile(config["provider"]["name"]).network if isinstance(config, dict) else "none"
    except Exception:
        return "none"


def _config():
    request_path = INPUT / "operator-request.json"
    request = json.loads(request_path.read_bytes())
    if (not isinstance(request, dict) or set(request) != {
            "schema_version", "command", "config_path", "input_inventory", "input_inventory_sha256"
        } or request["schema_version"] != "retrieval_arena.candidate_retrieval_request.v1"
            or request["command"] != "candidate-retrieve" or request["config_path"] != "config.json"):
        raise ValueError("candidate-retrieval-request")
    inventory = regular_inventory(INPUT)
    actual_without_request = [row for row in inventory if row["path"] != "operator-request.json"]
    if (request["input_inventory"] != actual_without_request
            or request["input_inventory_sha256"] != sha256_bytes(canonical(actual_without_request))):
        raise ValueError("candidate-retrieval-request-inventory")
    return request, validate_config(json.loads((INPUT / "config.json").read_bytes()))


def prepare() -> int:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    request = config = None
    try:
        if (OUTPUT / OWNER).read_text(encoding="utf-8") != "retrieval_arena.output_root.v1\n":
            raise ValueError("candidate-retrieval-output-owner")
        if set(path.name for path in OUTPUT.iterdir()) != {OWNER}:
            raise ValueError("candidate-retrieval-output-not-fresh")
        request, config = _config()
        WORK.mkdir()
        provider_input = WORK / "provider-input"
        (WORK / "provider-output").mkdir()
        launch = prepare_provider_input(config, INPUT, CANDIDATE, provider_input)
        launch["request_sha256"] = sha256_file(INPUT / "operator-request.json")
        launch["config_sha256"] = sha256_file(INPUT / "config.json")
        (WORK / "launch.json").write_bytes(canonical(launch) + b"\n")
        return 0
    except Exception as error:
        import shutil
        if WORK.exists() and not WORK.is_symlink():
            shutil.rmtree(WORK)
        if (OUTPUT.is_dir() and (OUTPUT / OWNER).is_file()
                and (OUTPUT / OWNER).read_text(encoding="utf-8") == "retrieval_arena.output_root.v1\n"):
            failure = _failure(error, 2, started, request, config)
            (OUTPUT / "operator_receipt.v1.json").write_bytes(canonical(failure) + b"\n")
        print(str(error), file=sys.stderr)
        return 2


def finalize() -> int:
    started = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        request, config = _config()
        launch = json.loads((WORK / "launch.json").read_bytes())
        provider_input = WORK / "provider-input"
        provider_output = WORK / "provider-output"
        if (launch["provider_input_inventory"] != regular_inventory(provider_input)
                or launch["provider_input_inventory_sha256"] != sha256_bytes(canonical(regular_inventory(provider_input)))):
            raise ValueError("candidate-retrieval-provider-input-changed")
        provider_exit_path = WORK / "provider-exit.json"
        provider_exit = json.loads(provider_exit_path.read_bytes())
        if (not isinstance(provider_exit, dict) or set(provider_exit) != {"exit_code"}
                or not isinstance(provider_exit["exit_code"], int)):
            raise ValueError("candidate-retrieval-provider-exit")
        if provider_exit["exit_code"] != 0:
            raise ValueError("candidate-retrieval-provider-failure")
        predictions, answers = project_ranked_predictions(config, provider_output, INPUT, WORK / "documents.json",
                                                          provider_input)
        pending = OUTPUT / "predictions.v2.jsonl.pending"
        pending.write_bytes(b"".join(canonical(row) + b"\n" for row in predictions))
        answers_pending = OUTPUT / "generated_answers.v1.jsonl.pending"
        answers_pending.write_bytes(b"".join(canonical(row) + b"\n" for row in answers))
        # S025: a graph provider's per-candidate link graph is published beside
        # the predictions; its hash is the one prepare bound in the launch.
        graph_edges_sha256 = launch.get("graph_edges_sha256")
        graph_pending = None
        if graph_edges_sha256 is not None:
            graph_source = provider_input / GRAPH_EDGES_FILE
            if sha256_file(graph_source) != graph_edges_sha256:
                raise ValueError("candidate-retrieval-graph-edges-changed")
            graph_pending = OUTPUT / (GRAPH_EDGES_FILE + ".pending")
            graph_pending.write_bytes(graph_source.read_bytes())
        # S026: the navigator's per-query action traces and run summary are
        # the scientific artifact; they are published beside the predictions
        # (validated by project_ranked_predictions) and inventoried in the receipt.
        profile = provider_profile(config["provider"]["name"])
        traces_pending = navigation_pending = None
        traces_inventory_sha256 = navigation_sha256 = None
        if profile.publishes_traces:
            import shutil
            traces_pending = OUTPUT / (TRACES_DIR + ".pending")
            shutil.copytree(provider_output / TRACES_DIR, traces_pending, symlinks=False)
            traces_inventory_sha256 = sha256_bytes(canonical(regular_inventory(traces_pending)))
            navigation_pending = OUTPUT / "navigation.json.pending"
            navigation_pending.write_bytes((provider_output / "navigation.json").read_bytes())
            navigation_sha256 = sha256_file(navigation_pending)
        receipt = {
            "schema_version": "retrieval_arena.candidate_retrieval_operator_receipt.v1",
            "command": "candidate-retrieve", "status": "completed", "exit_code": 0,
            "experiment_id": config["experiment_id"], "execution_id": config["execution_id"],
            "output_identity": config["output_identity"], "candidate": config["candidate"],
            "provider": config["provider"], "request_sha256": sha256_file(INPUT / "operator-request.json"),
            "config_sha256": sha256_file(INPUT / "config.json"),
            "provider_output_inventory": regular_inventory(provider_output),
            "provider_output_inventory_available": True,
            "predictions_sha256": sha256_file(pending), "prediction_count": len(predictions),
            "generated_answers_sha256": sha256_file(answers_pending),
            "generated_answer_count": len(answers), "graph_edges_sha256": graph_edges_sha256,
            "traces_inventory_sha256": traces_inventory_sha256, "navigation_sha256": navigation_sha256,
            "identity": _identity(),
            "isolation": {"network": launch["network"], "candidate_mount": "read_only",
                          "input_mount": "read_only", "root_filesystem": "read_only"},
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "receipt_sha256": "0" * 64,
        }
        receipt["receipt_sha256"] = sha256_bytes(canonical(receipt))
        (OUTPUT / "operator_receipt.v1.json.pending").write_bytes(canonical(receipt) + b"\n")
        prediction_final = OUTPUT / "predictions.v2.jsonl"
        answers_final = OUTPUT / "generated_answers.v1.jsonl"
        receipt_final = OUTPUT / "operator_receipt.v1.json"
        pending.replace(prediction_final)
        answers_pending.replace(answers_final)
        if graph_pending is not None:
            graph_pending.replace(OUTPUT / GRAPH_EDGES_FILE)
        if traces_pending is not None:
            traces_pending.replace(OUTPUT / TRACES_DIR)
            navigation_pending.replace(OUTPUT / "navigation.json")
        try:
            (OUTPUT / "operator_receipt.v1.json.pending").replace(receipt_final)
        except Exception:
            prediction_final.unlink(missing_ok=True)
            answers_final.unlink(missing_ok=True)
            (OUTPUT / GRAPH_EDGES_FILE).unlink(missing_ok=True)
            _unlink_traces()
            raise
        return 0
    except Exception as error:
        import shutil
        for path in OUTPUT.glob("*.pending"):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        (OUTPUT / "predictions.v2.jsonl").unlink(missing_ok=True)
        (OUTPUT / "generated_answers.v1.jsonl").unlink(missing_ok=True)
        (OUTPUT / GRAPH_EDGES_FILE).unlink(missing_ok=True)
        _unlink_traces()
        failure = _failure(error, 1, started, request, config)
        (OUTPUT / "operator_receipt.v1.json").write_bytes(canonical(failure) + b"\n")
        print(str(error), file=sys.stderr)
        return 1
    finally:
        # Work products are operational scratch, never scientific publication.
        import shutil
        if WORK.exists() and not WORK.is_symlink():
            shutil.rmtree(WORK)


def _unlink_traces() -> None:
    import shutil
    (OUTPUT / "navigation.json").unlink(missing_ok=True)
    if (OUTPUT / TRACES_DIR).is_dir() and not (OUTPUT / TRACES_DIR).is_symlink():
        shutil.rmtree(OUTPUT / TRACES_DIR)


def main(command: str) -> int:
    return prepare() if command == "candidate-retrieve-prepare" else finalize()
