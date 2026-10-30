"""Offline matched-entry and separate known-support scoring operators."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_name] = "1"

sys.path.insert(0, "/workspace/source")
from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_bytes, sha256_file  # noqa: E402
from retrieval_arena.graph import magnetic_spectrum as ms  # noqa: E402
from retrieval_arena.graph import phase_navigation as pn  # noqa: E402

INPUT = Path("/workspace/input")
OUTPUT = Path("/workspace/output")
OWNER = ".retrieval-arena-output-owner"
RUN_REQUEST = "retrieval_arena.phase_navigation_operator_request.v1"
SCORE_REQUEST = "retrieval_arena.phase_navigation_scoring_request.v1"


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request(schema, command):
    request = json.loads((INPUT / "operator-request.json").read_bytes())
    if (not isinstance(request, dict) or request.get("schema_version") != schema
            or request.get("command") != command or request.get("config_path") != "config.json"):
        raise ValueError("phase-navigation-request")
    inventory = regular_inventory(INPUT)
    actual = [row for row in inventory if row["path"] != "operator-request.json"]
    if request.get("input_inventory") != actual or request.get("input_inventory_sha256") != sha256_bytes(canonical(actual)):
        raise ValueError("phase-navigation-request-inventory")
    return request, json.loads((INPUT / "config.json").read_bytes())


def _measure_config(config):
    expected = {"schema_version", "master_seed", "random_count", "traversal", "queries_path", "corpus_path", "jobs"}
    if set(config) != expected or config["schema_version"] != "retrieval_arena.phase_navigation_config.v1":
        raise ValueError("phase-navigation-config-fields")
    if not isinstance(config["jobs"], list) or not config["jobs"]:
        raise ValueError("phase-navigation-jobs")
    if not isinstance(config["master_seed"], int) or not isinstance(config["random_count"], int) or config["random_count"] != 128:
        raise ValueError("phase-navigation-seed-policy")
    if set(config["traversal"]) != {"max_hops", "neighbor_budget", "candidate_budget", "final_top_k", "distance_penalty", "directed"}:
        raise ValueError("phase-navigation-traversal")
    return config


def _load_queries(path):
    rows = [json.loads(line) for line in (INPUT / path).read_text(encoding="utf-8").splitlines() if line.strip()]
    seen = set()
    for row in rows:
        if set(row) != {"query_id", "question", "scores"} or row["query_id"] in seen:
            raise ValueError("phase-navigation-query-schema-or-duplicate")
        if not isinstance(row["question"], str) or not isinstance(row["scores"], dict):
            raise ValueError("phase-navigation-query-types")
        if any(not isinstance(k, str) or not isinstance(v, (int, float)) or not __import__("math").isfinite(v)
               for k, v in row["scores"].items()):
            raise ValueError("phase-navigation-query-score-types")
        seen.add(row["query_id"])
    return rows


def _graph_features(nodes, edges, job):
    desc = ms.structural_descriptors(nodes, edges)
    result = {"job_id": job["job_id"], "family": job["family"], "variant": job["variant"],
              "kind": job["kind"], "view_graph_sha256": ms.graph_sha256(nodes, edges),
              "structural": desc, "spectra": {}}
    if not job["measure_spectrum"]:
        result["spectra_status"] = "not_requested_for_full_scope_companion"
        return result
    desc["trophic_incoherence"] = ms.trophic_incoherence(nodes, edges)
    for q in (0.0, 0.10, 0.25):
        matrix = ms.normalized_magnetic_matrix(nodes, edges, q)
        cfg = ms.SpectrumConfig(modes=len(nodes), solver="dense")
        qualified = ms.spectrum(nodes, edges, q, cfg, operator="normalized")
        values = np.asarray(qualified["eigenvalues"], dtype=np.float64)
        vectors = qualified.get("_vectors")
        record = {"lambda1": float(values[0]), "lambda2": float(values[1]) if len(values)>1 else None,
                  "spectrum_status": qualified["status"], "spectrum_checks": {k: qualified.get(k) for k in
                      ("residual_relative", "orthogonality_max_abs", "hermitian_max_abs", "computed_modes_within_bound")}}
        if q in (0.10, 0.25) and vectors is not None:
            record["phase"] = pn.qualify_phase_modes(matrix, values[:3], vectors[:, :3]) if len(values)>=3 else {
                "status": "unavailable", "reason": "adjacent-modes-missing", "V1": None, "V2": None}
            record["polarization"] = pn.qualify_subspace_polarization(nodes, edges, matrix, values, vectors)
        result["spectra"][str(q)] = record
    return result


def measure():
    request, config = _request(RUN_REQUEST, "measure-phase-navigation")
    config = _measure_config(config)
    queries = _load_queries(config["queries_path"])
    corpus_root = (INPUT / config["corpus_path"]).resolve()
    if INPUT.resolve() not in corpus_root.parents or not corpus_root.is_dir():
        raise ValueError("phase-navigation-corpus-path")
    corpus_files = {str(p.relative_to(INPUT)).replace("\\", "/") for p in corpus_root.rglob("*") if p.is_file()}
    required = {"config.json", "input-manifest.json", config["queries_path"]} | {job["bundle"] for job in config["jobs"]} | corpus_files
    actual = {row["path"] for row in regular_inventory(INPUT) if row["path"] != "operator-request.json"}
    if actual != required:
        raise ValueError("phase-navigation-input-contents")
    started = time.monotonic()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    features_path = OUTPUT / "graph_features.jsonl"
    trials_path = OUTPUT / "navigation_trials.jsonl"
    views_path = OUTPUT / "job_views.jsonl"
    feature_count = trial_count = 0
    with features_path.open("w", encoding="utf-8", newline="\n") as features, trials_path.open("w", encoding="utf-8", newline="\n") as trials, views_path.open("w", encoding="utf-8", newline="\n") as views:
        seen_jobs = set()
        for job in config["jobs"]:
            if set(job) != {"job_id", "bundle", "family", "variant", "kind", "view", "measure_spectrum"} or job["job_id"] in seen_jobs:
                raise ValueError("phase-navigation-job-schema-or-duplicate")
            if not isinstance(job["measure_spectrum"], bool) or job["kind"] not in {"fixed_lwcc", "full_scope"}:
                raise ValueError("phase-navigation-job-policy")
            seen_jobs.add(job["job_id"])
            bundle = json.loads((INPUT / job["bundle"]).read_bytes())
            admitted = ms.validate_bundle(bundle)
            nodes, edges, view_record = ms.select_view(admitted["nodes"], admitted["edges"], job["view"], bundle.get("node_sets"))
            if not nodes:
                raise ValueError("phase-navigation-empty-view")
            feature = _graph_features(nodes, edges, job)
            feature["view"] = view_record
            features.write(json.dumps(feature, sort_keys=True, separators=(",", ":")) + "\n")
            views.write(json.dumps({"job_id": job["job_id"], "node_ids": nodes,
                                    "node_ids_sha256": ms.digest(nodes)}, sort_keys=True, separators=(",", ":")) + "\n")
            feature_count += 1
            allowed = set(nodes)
            for query in queries:
                scores = {doc: float(score) for doc, score in query["scores"].items() if doc in allowed}
                if not scores:
                    raise ValueError("phase-navigation-no-cached-scores-in-view:" + query["query_id"])
                semantic, random = pn.matched_trials(nodes, edges, scores, query["query_id"], family=job["family"],
                    view=job["kind"], master_seed=config["master_seed"], random_count=config["random_count"], **config["traversal"])
                row = {"job_id": job["job_id"], "family": job["family"], "variant": job["variant"],
                       "kind": job["kind"], "query_id": query["query_id"], "question": query["question"],
                       "semantic": semantic["trials"][0], "random": random["trials"],
                       "random_seed_ids": random["seed_ids"], "random_first_half_seed_ids": random["first_half"],
                       "random_last_half_seed_ids": random["last_half"], "view_nodes": len(nodes),
                       "view_nodes_sha256": ms.digest(nodes),
                       "semantic_entry_outdegree": sum(1 for s, _ in edges if s == semantic["trials"][0]["seed_id"])}
                trials.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")
                trial_count += 1
    _write_manifest("measure-phase-navigation", [features_path, trials_path, views_path], started,
                    {"graph_jobs": feature_count, "query_graph_rows": trial_count}, request["input_inventory_sha256"])


def score():
    request, config = _request(SCORE_REQUEST, "score-phase-navigation")
    expected = {"schema_version", "trials_path", "targets_path", "view_nodes_path"}
    if set(config) != expected or config["schema_version"] != "retrieval_arena.phase_navigation_scoring_config.v1":
        raise ValueError("phase-navigation-scoring-config")
    required = {"config.json", config["trials_path"], config["targets_path"], config["view_nodes_path"]}
    actual = {row["path"] for row in regular_inventory(INPUT) if row["path"] != "operator-request.json"}
    if actual != required: raise ValueError("phase-navigation-scoring-input-contents")
    targets = {}
    for line in (INPUT / config["targets_path"]).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if set(row) != {"query_id", "known_target_ids"} or row["query_id"] in targets:
            raise ValueError("phase-navigation-target-schema-or-duplicate")
        if (not isinstance(row["query_id"], str) or not isinstance(row["known_target_ids"], list)
                or any(not isinstance(x, str) for x in row["known_target_ids"])
                or len(row["known_target_ids"]) != len(set(row["known_target_ids"]))):
            raise ValueError("phase-navigation-target-types")
        targets[row["query_id"]] = row["known_target_ids"]
    view_nodes = {}
    for line in (INPUT / config["view_nodes_path"]).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if set(row) != {"job_id", "node_ids", "node_ids_sha256"} or row["job_id"] in view_nodes:
            raise ValueError("phase-navigation-view-schema-or-duplicate")
        from retrieval_arena.graph import magnetic_spectrum as _ms
        if _ms.digest(row["node_ids"]) != row["node_ids_sha256"]:
            raise ValueError("phase-navigation-view-hash")
        view_nodes[row["job_id"]] = set(row["node_ids"])
    started = time.monotonic(); out = OUTPUT / "navigation_scores.jsonl"; count = 0
    with out.open("w", encoding="utf-8", newline="\n") as handle:
        seen = set()
        for line in (INPUT / config["trials_path"]).read_text(encoding="utf-8").splitlines():
            if not line: continue
            row = json.loads(line); key = (row["job_id"], row["query_id"])
            if key in seen or row["query_id"] not in targets: raise ValueError("phase-navigation-score-join")
            seen.add(key)
            if row["job_id"] not in view_nodes: raise ValueError("phase-navigation-view-join")
            targets_in_view = set(targets[row["query_id"]]) & view_nodes[row["job_id"]]
            score = pn.query_macro([row["semantic"]], row["random"], targets_in_view)
            output = {"job_id": row["job_id"], "family": row["family"], "variant": row["variant"],
                      "kind": row["kind"], "query_id": row["query_id"], "query_text_group": row["question"],
                      "eligible_fixed_lwcc": bool(targets_in_view) if row["kind"] == "fixed_lwcc" else True,
                      "known_target_count_in_view": len(targets_in_view),
                      "scores": score, "random_first_half_hit_rate": None, "random_last_half_hit_rate": None}
            first = [pn.score_trial(t, targets_in_view)["hit"] for t in row["random"][:64]]
            last = [pn.score_trial(t, targets_in_view)["hit"] for t in row["random"][64:]]
            output["random_first_half_hit_rate"] = float(sum(first)/len(first)) if first else None
            output["random_last_half_hit_rate"] = float(sum(last)/len(last)) if last else None
            handle.write(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n"); count += 1
    if len(seen) == 0: raise ValueError("phase-navigation-empty-scoring-input")
    _write_manifest("score-phase-navigation", [out], started, {"scored_query_graph_rows": count}, request["input_inventory_sha256"])


def _write_manifest(command, outputs, started, counts, input_inventory_sha256):
    manifest = {"schema_version": "retrieval_arena.phase_navigation_operator_manifest.v1",
                "command": command, "outputs": [{"path": p.name, "sha256": sha256_file(p), "bytes": p.stat().st_size} for p in outputs],
                "counts": counts, "runtime_seconds": time.monotonic()-started,
                "input_inventory_sha256": input_inventory_sha256,
                "code_sha256": {"phase_navigation": pn.code_sha256(), "normalized_spectrum": ms.code_sha256()},
                "runtime": {"python": sys.version.split()[0], "platform": sys.platform,
                            "numpy": __import__("numpy").__version__, "source_commit": os.environ.get("RA_SOURCE_COMMIT"),
                            "source_tree": os.environ.get("RA_SOURCE_TREE"),
                            "dependency_lock_sha256": os.environ.get("RA_DEPENDENCY_LOCK_SHA256"),
                            "image_reference": os.environ.get("RA_IMAGE_REFERENCE"),
                            "container_platform": os.environ.get("RA_PLATFORM")}}
    path = OUTPUT / "phase_navigation_manifest.json"
    path.write_bytes(canonical(manifest) + b"\n")
    zero = "0" * 64
    receipt = {"schema_version": "retrieval_arena.phase_navigation_operator_receipt.v1", "command": command,
               "status": "qualified", "manifest_sha256": sha256_file(path), "counts": counts,
               "input_inventory_sha256": input_inventory_sha256, "receipt_sha256": zero}
    receipt["receipt_sha256"] = sha256_bytes(canonical(receipt))
    (OUTPUT / "operator_receipt.v1.json").write_bytes(canonical(receipt)+b"\n")


def main(command):
    if not OUTPUT.is_dir() or {p.name for p in OUTPUT.iterdir()} != {OWNER}:
        raise ValueError("verification-operator-output-not-fresh")
    if command == "measure-phase-navigation": measure()
    elif command == "score-phase-navigation": score()
    else: raise ValueError("phase-navigation-command")
    return 0
