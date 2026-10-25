"""Closed pooled-support operator commands with an immutable input inventory and a receipt.

Runs inside a pinned image with the RA source mounted read-only (scripts/run_support.py):
  pool           embedding image, network none
  verify         hosted-model image (anthropic SDK), network external_api only; `resume` continues a stopped run
  escalate       as verify: whole-page judgment of a prior version's undetermined pages (EscalateRun)
  review-packet  any RA image, network none
  import-review  any RA image, network none
Request: /input/operator-request.json {schema_version, command, args, input_inventory}.
Every path in args is relative to /input. Outputs go to a fresh /output.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from retrieval_arena.errors import ValidationError  # noqa: E402

SCHEMA_REQUEST = "retrieval_arena.pooled_support_operator_request.v1"
SCHEMA_RECEIPT = "retrieval_arena.pooled_support_operator_receipt.v1"
NETWORKS = {"pool": {"none"}, "verify": {"external_api"}, "escalate": {"external_api"}, "review-packet": {"none"}, "import-review": {"none"}}
ARGS = {
    "pool": {"config", "corpus", "queries", "run_root", "model_dir"},
    "verify": {"config", "pool", "queries", "prior", "query_ids", "limit", "resume"},
    "escalate": {"config", "pool", "corpus", "queries", "prior", "query_ids", "limit", "resume"},
    "review-packet": {"qrels", "pool", "queries", "per_stratum", "seed", "packet_id"},
    "import-review": {"packet", "key", "responses"},
}


def require(ok, message):
    if not ok:
        raise ValidationError(message)


def sha_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def inventory(root):
    files = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "input-symlink")
        if path.is_file() and path.name not in ("operator-request.json", ".retrieval-arena-output-owner"):
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def inside(source, rel):
    if rel is None:
        return None
    path = (source / rel).resolve()
    require(path == source or source in path.parents, "input-path-escapes")
    return path


def run(command, args, source, output, network):
    from retrieval_arena.support import pooling, review, verification
    p = lambda name: inside(source, args[name])  # noqa: E731
    if command == "pool":
        model = Path("/models/embedding") if args["model_dir"] == "/models/embedding" else p("model_dir")
        manifest = pooling.pool_files(p("config"), p("corpus"), p("queries"), p("run_root"), str(model), output)
        return "completed", {"pool_sha256": manifest["pool_sha256"], "summary": manifest["summary"]}
    if command in ("verify", "escalate"):
        from retrieval_arena.model_providers import transport_for
        config = json.loads(p("config").read_text(encoding="utf-8"))
        verification.validate_verifier_config(config)
        query_ids = json.loads(p("query_ids").read_text(encoding="utf-8")) if args.get("query_ids") else None
        queries = pooling.read_jsonl(p("queries"))
        if args["resume"]:
            # A stopped run's output, staged as input: its state and judgments seed this output (VerifyRun checks the state).
            # The ledger comes too, so the spend cap counts the earlier calls.
            for name in ("verify_state.json", "judgments.jsonl", "cost-ledger.jsonl"):
                if (p("resume") / name).exists():
                    (output / name).write_bytes((p("resume") / name).read_bytes())
        transport = transport_for(config, network, output)
        if command == "escalate":
            vrun = verification.EscalateRun(p("pool"), p("corpus"), queries, config, output, transport, p("prior"), query_ids=query_ids)
        else:
            vrun = verification.VerifyRun(p("pool"), queries, config, output, transport, prior_dir=p("prior"), query_ids=query_ids)
        stats = asyncio.run(vrun.run(limit=args["limit"]))
        manifest = verification.write_qrels(vrun)
        manifest_counts = manifest["counts"]
        status = "completed" if not stats["spend_stopped"] and stats["remaining_windows"] == 0 else "completed_with_failures"
        return status, {"run": stats, "qrels_version": manifest["qrels_version"], "counts": manifest_counts,
                        "qrels_sha256": manifest["qrels_sha256"], "judgments_sha256": manifest["judgments_sha256"],
                        **({"escalation": manifest["escalation"]} if command == "escalate" else {})}
    if command == "review-packet":
        info = review.packet_files(p("qrels"), p("pool"), p("queries"), output, args["per_stratum"], args["seed"], args["packet_id"])
        return "completed", info
    result = review.import_files(p("packet"), p("key"), p("responses"), output / "review_result.json")
    return "completed", {"precision": result["precision"], "miss_rate": result["miss_rate"], "items_missing": len(result["items_missing"])}


def main():
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    require(command in NETWORKS, "operator-command")
    network = os.environ.get("RA_SUPPORT_NETWORK", "none")
    require(network in NETWORKS[command], "operator-network")
    output, source = Path("/output"), Path("/input")
    require(output.is_dir() and {p.name for p in output.iterdir()} <= {".retrieval-arena-output-owner"}, "output-not-fresh")
    receipt = {"schema_version": SCHEMA_RECEIPT, "command": command, "network": network,
               "image": os.environ.get("RA_IMAGE_REFERENCE"), "source_commit": os.environ.get("RA_SOURCE_COMMIT"),
               "source_tree": os.environ.get("RA_SOURCE_TREE"), "source_dirty": os.environ.get("RA_SOURCE_DIRTY"),
               "input_inventory_sha256": None, "outputs": {}, "status": "failed", "error": None, "result": None}
    code = 1
    try:
        request = json.loads((source / "operator-request.json").read_text(encoding="utf-8"))
        require(set(request) == {"schema_version", "command", "args", "input_inventory"}, "request-fields")
        require(request["schema_version"] == SCHEMA_REQUEST and request["command"] == command, "request-policy")
        require(set(request["args"]) == ARGS[command], "request-args")
        actual = inventory(source)
        require(actual == request["input_inventory"], "input-inventory-mismatch")
        receipt["input_inventory_sha256"] = sha_json(actual)
        receipt["request_sha256"] = hashlib.sha256((source / "operator-request.json").read_bytes()).hexdigest()
        receipt["args"] = request["args"]
        receipt["status"], receipt["result"] = run(command, request["args"], source, output, network)
        code = 0 if receipt["status"] == "completed" else 1
    except Exception as exc:
        receipt["error"] = type(exc).__name__ + ":" + str(exc)[:500] if type(exc).__module__.startswith("retrieval_arena") else type(exc).__name__ + ":" + str(exc)[:200]
    finally:
        receipt["outputs"] = inventory(output)
        receipt["sha256"] = sha_json(receipt)
        (output / "receipt.json").write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
