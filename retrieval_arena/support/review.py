"""Human spot-check of LLM-verified pooled support: stratified packet, blinded items, import, error rates.

The packet samples verifier-supported and verifier-not-supported pages separately
(seeded, without replacement) and shows each reviewer item as question, reference
answer and one passage, without the verifier's verdict. The key (item -> stratum)
is written to a separate file the review page never receives.

Import estimates, with Wilson 95% intervals:
  precision  = reviewer-yes / decided items in the verifier-supported stratum
  miss rate  = reviewer-yes / decided items in the verifier-not-supported stratum
`unsure` responses are excluded from both and counted. Limits are stated in the
output: misses outside the pool are not measured; one window, not the whole page,
is shown; a corpus-wide error rate needs the stratum sizes as weights.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter
from pathlib import Path
from typing import Any

from ..errors import ValidationError
from .pooling import read_jsonl, sha256_json, sha256_text, write_json

SCHEMA_PACKET = "retrieval_arena.pooled_support_review_packet.v1"
SCHEMA_KEY = "retrieval_arena.pooled_support_review_key.v1"
SCHEMA_RESPONSES = "retrieval_arena.pooled_support_review_responses.v1"
SCHEMA_RESULT = "retrieval_arena.pooled_support_review_result.v1"
RESPONSES = ("yes", "no", "unsure")
STRATA = {"supported": "verifier_yes", "not_supported": "verifier_no"}
LIMITS = [
    "misses outside the pool are not measured (pages no generator or run retrieved stay unjudged)",
    "the reviewer sees one window, not the whole page",
    "strata are sampled separately; a pooled error rate must weight each stratum by its size",
    "a single reviewer; no inter-annotator agreement",
]


def wilson(successes: int, n: int, z: float = 1.959963984540054) -> dict[str, Any]:
    if n == 0:
        return {"estimate": None, "low": None, "high": None, "n": 0, "successes": 0}
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {"estimate": p, "low": max(0.0, centre - half), "high": min(1.0, centre + half), "n": n, "successes": successes}


def build_packet(qrels_dir: Path, pool_dir: Path, queries: list[dict[str, Any]], per_stratum: int, seed: str,
                 packet_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(packet, key). Items are shuffled across strata by the same seed."""
    if type(per_stratum) is not int or per_stratum < 1 or not seed:
        raise ValidationError("pooled-support-review-parameters")
    inventory = json.loads((Path(pool_dir) / "corpus_inventory.json").read_text(encoding="utf-8"))
    windows = {w["window_id"]: w for w in read_jsonl(Path(pool_dir) / "windows.jsonl")}
    qtext = {q["question_id"]: q for q in queries}
    decisions = [d for d in read_jsonl(Path(qrels_dir) / "page_decisions.jsonl")
                 if inventory.get(d["doc_id"]) == d["page_sha256"] and d["decision"] in STRATA]
    rng = random.Random(seed)
    chosen = []
    for decision, stratum in STRATA.items():
        population = sorted((d for d in decisions if d["decision"] == decision), key=lambda d: (d["query_id"], d["doc_id"]))
        if len(population) < per_stratum:
            raise ValidationError(f"pooled-support-review-stratum-too-small:{stratum}:{len(population)}")
        chosen += [(stratum, d, len(population)) for d in rng.sample(population, per_stratum)]
    rng.shuffle(chosen)
    items, key = [], {}
    for stratum, d, _ in chosen:
        # A supported page shows the window the verifier accepted; an unsupported page its top-ranked window.
        wid = d["answering_window_ids"][0] if d["answering_window_ids"] else d["window_ids"][0]
        item_id = sha256_text(f"{seed}|{d['query_id']}|{d['doc_id']}")[:16]
        q = qtext[d["query_id"]]
        items.append({"item_id": item_id, "query_id": d["query_id"], "question": q["question"],
                      "reference_answer": q["answer"], "doc_id": d["doc_id"], "window_id": wid,
                      "passage": windows[wid]["text"]})
        key[item_id] = {"stratum": stratum, "decision": d["decision"], "query_id": d["query_id"], "doc_id": d["doc_id"]}
    population_sizes = {stratum: sum(1 for d in decisions if d["decision"] == dec) for dec, stratum in STRATA.items()}
    packet = {"schema_version": SCHEMA_PACKET, "packet_id": packet_id, "seed": seed, "per_stratum": per_stratum,
              "responses_allowed": list(RESPONSES), "items": items,
              "qrels_manifest_sha256": sha256_text((Path(qrels_dir) / "qrels_manifest.json").read_text(encoding="utf-8")),
              "instructions": ("For each item: does the PASSAGE state the reference answer, or a necessary part of it? "
                               "Answer yes, no, or unsure. Judge only what the passage states.")}
    packet["packet_sha256"] = sha256_json(packet)
    key_doc = {"schema_version": SCHEMA_KEY, "packet_id": packet_id, "packet_sha256": packet["packet_sha256"],
               "population_sizes": population_sizes, "items": key}
    return packet, key_doc


def import_responses(packet: dict[str, Any], key: dict[str, Any], responses: dict[str, Any]) -> dict[str, Any]:
    """Validate reviewer responses against the packet and compute precision and miss rate."""
    if key["packet_sha256"] != packet["packet_sha256"] or responses.get("packet_id") != packet["packet_id"]:
        raise ValidationError("pooled-support-review-packet-mismatch")
    if responses.get("schema_version") != SCHEMA_RESPONSES or not isinstance(responses.get("responses"), list):
        raise ValidationError("pooled-support-review-responses-schema")
    known = {item["item_id"] for item in packet["items"]}
    seen: dict[str, str] = {}
    for row in responses["responses"]:
        item, answer = row.get("item_id"), row.get("response")
        if item not in known or item in seen or answer not in RESPONSES:
            raise ValidationError(f"pooled-support-review-response:{item}")
        seen[item] = answer
    by_stratum = {s: Counter() for s in STRATA.values()}
    for item, answer in seen.items():
        by_stratum[key["items"][item]["stratum"]][answer] += 1
    yes, no = by_stratum["verifier_yes"], by_stratum["verifier_no"]
    result = {"schema_version": SCHEMA_RESULT, "packet_id": packet["packet_id"], "packet_sha256": packet["packet_sha256"],
              "responses_sha256": sha256_json(responses), "reviewer": responses.get("reviewer"),
              "items_total": len(known), "items_answered": len(seen), "items_missing": sorted(known - set(seen)),
              "counts": {s: dict(c) for s, c in by_stratum.items()},
              "precision": wilson(yes["yes"], yes["yes"] + yes["no"]),
              "miss_rate": wilson(no["yes"], no["yes"] + no["no"]),
              "unsure": {s: c["unsure"] for s, c in by_stratum.items()},
              "population_sizes": key["population_sizes"], "limits": LIMITS,
              "disagreements": sorted(({"item_id": i, **key["items"][i], "response": a} for i, a in seen.items()
                                       if a != "unsure" and (a == "yes") != (key["items"][i]["stratum"] == "verifier_yes")),
                                      key=lambda r: r["item_id"])}
    return result


def packet_files(qrels_dir: Path, pool_dir: Path, queries_path: Path, out_dir: Path, per_stratum: int, seed: str, packet_id: str) -> dict[str, Any]:
    packet, key = build_packet(qrels_dir, pool_dir, read_jsonl(queries_path), per_stratum, seed, packet_id)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    write_json(Path(out_dir) / "review_packet.json", packet)
    write_json(Path(out_dir) / "review_key.json", key)
    return {"packet_id": packet_id, "items": len(packet["items"]), "packet_sha256": packet["packet_sha256"]}


def import_files(packet_path: Path, key_path: Path, responses_path: Path, out_path: Path) -> dict[str, Any]:
    load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))  # noqa: E731
    result = import_responses(load(packet_path), load(key_path), load(responses_path))
    write_json(out_path, result)
    return result
