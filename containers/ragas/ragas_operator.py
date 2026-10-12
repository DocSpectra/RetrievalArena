"""Closed optional evaluator commands, immutable input inventory and receipts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from retrieval_arena.ragas_scoring import (  # noqa: E402
    PENDING_FAILURES, canonical, digest, fields, is_v4, metric_results, replay, require, score_batch,
    validate_config, write_json,
)

# probe: one structured call that qualifies a config's credential, network and identity.
PROBE_PROMPT = 'Reply with the JSON object {"ok": true}.'
PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
# Network per command; judge/probe also admit external_api for hosted providers (RA-D07).
NETWORKS = {"smoke": {"none"}, "fixture": {"none"}, "replay": {"none"},
            "judge": {"host_model_alias", "external_api"}, "probe": {"host_model_alias", "external_api"}}


def admitted_transport(config, network, output):
    from retrieval_arena.model_providers import transport_for
    validate_config(config)
    require(config["provider"] == "ollama" or is_v4(config), "hosted-provider-requires-config-v4")
    return transport_for(config, network, output)


def inventory(root):
    files = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "input-symlink")
        if path.is_file() and path.name != "operator-request.json":
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def main():
    command = sys.argv[1] if len(sys.argv) == 2 else ""
    require(command in NETWORKS, "operator-command")
    network = os.environ.get("RA_RAGAS_NETWORK", "none")
    require(network in NETWORKS[command], "operator-network")
    if command == "smoke":
        os.environ["RA_RAGAS_SMOKE"] = "1"
        suite = unittest.defaultTestLoader.loadTestsFromNames(["unit_tests.test_ragas_scoring", "unit_tests.test_ragas_api", "unit_tests.test_model_providers"])
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1
    output = Path("/output")
    require(output.is_dir() and {p.name for p in output.iterdir()} == {".retrieval-arena-output-owner"}, "output-not-fresh")
    receipt = {"schema_version": "retrieval_arena.ragas_operator_receipt.v1", "command": command, "network": network,
               "image": os.environ.get("RA_IMAGE_REFERENCE"), "source_commit": os.environ.get("RA_SOURCE_COMMIT"),
               "source_tree": os.environ.get("RA_SOURCE_TREE"), "dependency_lock_sha256": hashlib.sha256((ROOT / "containers/ragas/requirements.lock").read_bytes()).hexdigest(),
               "input_inventory": {}, "outputs": {}, "status": "failed", "error": None}
    try:
        from retrieval_arena.ragas_judge import RagasJudge
        if command == "fixture":
            from unit_tests.ragas_fixture import inputs, transport
            batch, rubrics, config = inputs()
            judge = RagasJudge(transport, ROOT / "containers/ragas/requirements.lock")
            result = asyncio.run(score_batch(batch, rubrics, config, judge,
                                checkpoint=lambda rows: write_json(output / "checkpoint.json", rows)))
            for name, value in (("batch.json", batch), ("rubrics.json", rubrics), ("config.json", config)):
                write_json(output / name, value)
        else:
            source = Path("/input")
            request = json.loads((source / "operator-request.json").read_text())
            fields(request, "schema_version command input_inventory use_cache")
            require(request["schema_version"] == "retrieval_arena.ragas_operator_request.v1", "request-version")
            require(request["command"] == command and type(request["use_cache"]) is bool, "request-policy")
            actual = inventory(source)
            require(actual == request["input_inventory"], "input-inventory-mismatch")
            required = {"config.json"} if command == "probe" else {"batch.json", "rubrics.json", "scores.json" if command == "replay" else "config.json"}
            require(required <= set(actual) and all(p in required or (command == "judge" and p.startswith("cache/") and p.endswith(".json")) for p in actual), "input-files")
            receipt["input_inventory"] = actual
            receipt["request_sha256"] = hashlib.sha256((source / "operator-request.json").read_bytes()).hexdigest()
            if command == "probe":
                config = json.loads((source / "config.json").read_text())
                transport = admitted_transport(config, network, output)
                response = asyncio.run(transport(PROBE_PROMPT, PROBE_SCHEMA, config))
                parsed = json.loads(response["text"])
                ok = parsed == {"ok": True} and response["model"] == config["model"] and response["revision"] == config["revision"]
                write_json(output / "probe.json", {"schema_version": "retrieval_arena.provider_probe.v1",
                                                   "config_sha256": digest(config), "response": response, "parsed": parsed, "ok": ok})
                receipt["status"] = "completed" if ok else "completed_with_failures"
                return 0 if ok else 1
            batch, rubrics = [json.loads((source / name).read_text()) for name in ("batch.json", "rubrics.json")]
            if command == "replay":
                result = replay(batch, rubrics, json.loads((source / "scores.json").read_text()))
            else:
                config = json.loads((source / "config.json").read_text())
                judge = RagasJudge(admitted_transport(config, network, output), ROOT / "containers/ragas/requirements.lock")
                cache = output / "cache"
                cache.mkdir()
                # Frozen successful entries may be supplied to a fresh run; validated on reuse.
                for name in actual:
                    if name.startswith("cache/"):
                        require(Path(name).parent.as_posix() == "cache", "cache-path")
                        (cache / Path(name).name).write_bytes((source / name).read_bytes())
                result = asyncio.run(score_batch(batch, rubrics, config, judge, cache_dir=cache,
                                    use_cache=request["use_cache"], checkpoint=lambda rows: write_json(output / "checkpoint.json", rows)))
        write_json(output / "scores.json", result)
        receipt["status"] = "completed_with_failures" if any(r["status"] not in {"ok", "disabled"} or any(m["status"] in PENDING_FAILURES for m in metric_results(r).values()) for r in result["records"]) else "completed"
        # The fixture deliberately includes one missing prediction.
        return 0 if command == "fixture" or receipt["status"] == "completed" else 1
    except Exception as exc:
        receipt["error"] = type(exc).__name__ + ":" + str(exc) if type(exc).__module__.startswith("retrieval_arena") else type(exc).__name__
        return 1
    finally:
        receipt["outputs"] = inventory(output)
        receipt["sha256"] = digest(receipt)
        write_json(output / "receipt.json", receipt)


if __name__ == "__main__":
    raise SystemExit(main())
