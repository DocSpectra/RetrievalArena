from __future__ import annotations

import io
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Iterator

from retrieval_arena.cli import main as cli_main
from retrieval_arena.corpus.dataset_export import build_reachability_surface
from retrieval_arena.hashing import sha256_file
from retrieval_arena.manifests import read_manifest
from retrieval_arena.support.reachability import (
    SUPPORT_REACHABILITY_FILE,
    SUPPORT_REACHABILITY_METHOD,
    compute_support_reachability,
    write_support_reachability,
)


CREATED_AT = "2026-09-14T00:00:00Z"


def _write_fixture(dataset: Path, *, flat_ids: bool = False, with_graph: bool = True) -> None:
    """REACH-1: five documents, root -> a -> b, c an orphan, d unlinked and unasked."""
    corpus = dataset / "corpus"
    corpus.mkdir(parents=True)
    names = {"index": "index", "a": "a", "b": "b", "c": "c", "d": "d"}
    if flat_ids:
        names = {"index": "index__0a1b2c3d", "a": "a__11111111", "b": "b__22222222", "c": "c__33333333", "d": "d__44444444"}
    (corpus / f"{names['index']}.md").write_text("# Root\n\nSee [a](a.md).\n", encoding="utf-8")
    (corpus / f"{names['a']}.md").write_text("# A\n\nSee [b](b.md).\n", encoding="utf-8")
    (corpus / f"{names['b']}.md").write_text("# B\n\nLeaf page with three lines.\nThird line.\n", encoding="utf-8")
    (corpus / f"{names['c']}.md").write_text("# C\n\nNobody links here.\n", encoding="utf-8")
    (corpus / f"{names['d']}.md").write_text("# D\n", encoding="utf-8")
    if with_graph:
        (dataset / "graph_edges.csv").write_text(
            f"source,target\n{names['index']},{names['a']}\n{names['a']},{names['b']}\n", encoding="utf-8"
        )
    (dataset / "questions.jsonl").write_text(
        "".join(
            json.dumps({"question_id": qid, "question": f"Question {qid}?"}) + "\n"
            for qid in ("q1", "q2", "q3")
        ),
        encoding="utf-8",
    )
    rows = [
        {"question_id": "q1", "support_label": "supported", "top_docs": [{"doc_id": names["b"]}]},
        {"question_id": "q2", "support_label": "partially_supported", "top_docs": [{"doc_id": names["c"]}]},
        {"question_id": "q3", "support_label": "supported", "top_docs": [{"doc_id": names["a"]}, {"doc_id": names["c"]}]},
    ]
    (dataset / "faq_support_audit.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


class SupportReachabilityTests(unittest.TestCase):
    @contextmanager
    def workspace_tempdir(self) -> Iterator[Path]:
        root = Path(".tmp_unit_tests")
        root.mkdir(exist_ok=True)
        path = root / f"tmp_{uuid.uuid4().hex}"
        path.mkdir()
        try:
            yield path.resolve()
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_reach_1_hops_orphans_and_question_aggregates(self):
        with self.workspace_tempdir() as tmp:
            dataset = tmp / "dataset"
            _write_fixture(dataset)
            surface = compute_support_reachability(dataset)
            self.assertEqual(surface["status"], "ok")
            self.assertEqual(surface["root_doc_id"], "index")
            self.assertEqual(surface["graph"], {"node_count": 5, "edge_count": 2})
            by_id = {row["doc_id"]: row for row in surface["targets"]}
            self.assertEqual(sorted(by_id), ["a", "b", "c"])
            self.assertEqual(by_id["b"]["hops_from_root"], 2)
            self.assertEqual(by_id["c"]["hops_from_root"], "unreachable")
            self.assertEqual(by_id["c"]["hops_from_root_undirected"], "unreachable")
            self.assertEqual(by_id["a"]["hops_from_root"], 1)
            self.assertEqual((by_id["a"]["in_degree"], by_id["a"]["out_degree"]), (1, 1))
            self.assertEqual((by_id["c"]["in_degree"], by_id["c"]["out_degree"]), (0, 0))
            self.assertEqual((by_id["b"]["lines"], by_id["b"]["chars"]), (4, len("# B\n\nLeaf page with three lines.\nThird line.\n")))
            aggregate = surface["aggregate"]
            self.assertEqual(aggregate["support_documents"], 3)
            self.assertEqual(aggregate["corpus_document_count"], 5)
            self.assertEqual(aggregate["support_fraction_of_corpus"], 0.6)
            self.assertEqual(aggregate["orphan_fraction"], round(1 / 3, 6))
            self.assertEqual(aggregate["questions_with_target_within_3_hops_fraction"], round(2 / 3, 6))
            self.assertEqual(aggregate["questions_all_targets_orphan_fraction"], round(1 / 3, 6))
            self.assertEqual(aggregate["fraction_within_1_hops"], round(1 / 3, 6))
            self.assertEqual(aggregate["fraction_within_2_hops"], round(2 / 3, 6))
            self.assertEqual(aggregate["fraction_within_3_hops"], round(2 / 3, 6))
            self.assertEqual(aggregate["fraction_within_8_hops"], round(2 / 3, 6))
            self.assertEqual(aggregate["median_hops"], 1.5)
            self.assertEqual(aggregate["targets_unreachable"], 1)
            self.assertEqual(aggregate["support_class_mix"], {"partially_supported": 1, "supported": 2})
            questions = {row["question_id"]: row for row in surface["questions"]}
            self.assertEqual(questions["q1"]["min_hops_from_root"], 2)
            self.assertEqual(questions["q2"]["min_hops_from_root"], "unreachable")
            self.assertTrue(questions["q2"]["all_targets_orphans"])
            self.assertFalse(questions["q3"]["all_targets_orphans"])
            self.assertTrue(questions["q3"]["has_target_within_3_hops"])
            self.assertEqual(questions["q3"]["support_documents"], 2)
            self.assertEqual(questions["q3"]["support_fraction_of_corpus"], 0.4)

    def test_reach_2_missing_graph_is_unavailable_without_numbers(self):
        with self.workspace_tempdir() as tmp:
            dataset = tmp / "dataset"
            _write_fixture(dataset, with_graph=False)
            surface = compute_support_reachability(dataset)
            self.assertEqual(surface["status"], "unavailable")
            self.assertEqual(surface["reason"], "graph_edges_missing")
            self.assertNotIn("aggregate", surface)
            self.assertNotIn("targets", surface)
            (dataset / "faq_support_audit.jsonl").unlink()
            (dataset / "graph_edges.csv").write_text("source,target\n", encoding="utf-8")
            self.assertEqual(compute_support_reachability(dataset)["reason"], "support_audit_missing")

    def test_flat_ids_resolve_the_root_and_the_file_is_deterministic(self):
        with self.workspace_tempdir() as tmp:
            dataset = tmp / "dataset"
            _write_fixture(dataset, flat_ids=True)
            path, surface = write_support_reachability(dataset)
            self.assertEqual(path.name, SUPPORT_REACHABILITY_FILE)
            self.assertEqual(surface["root_doc_id"], "index__0a1b2c3d")
            first = sha256_file(path)
            write_support_reachability(dataset)
            self.assertEqual(sha256_file(path), first)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["method"], SUPPORT_REACHABILITY_METHOD)

    def test_build_records_the_surface_like_the_support_audit(self):
        with self.workspace_tempdir() as tmp:
            dataset = tmp / "dataset"
            _write_fixture(dataset)
            built = build_reachability_surface(dataset, None, query_set_id="toy-queries", created_at=CREATED_AT)
            manifest = read_manifest(built["manifest_path"])
            self.assertEqual(manifest["manifest_type"], "support_reachability")
            self.assertEqual(manifest["reachability_method"], SUPPORT_REACHABILITY_METHOD)
            self.assertEqual(manifest["support_reachability_file"], SUPPORT_REACHABILITY_FILE)
            self.assertEqual(manifest["support_reachability_file_hash"], sha256_file(dataset / SUPPORT_REACHABILITY_FILE))
            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(manifest["query_set_id"], "toy-queries")
            self.assertIsNone(manifest["dataset_preparation_manifest_hash"])
            self.assertEqual(manifest["aggregate"]["support_documents"], 3)
            self.assertRegex(manifest["support_audit_file_hash"], r"^[0-9a-f]{64}$")
            self.assertRegex(manifest["graph_edge_file_hash"], r"^[0-9a-f]{64}$")

    def test_cli_corpus_reachability_prints_the_summary(self):
        with self.workspace_tempdir() as tmp:
            dataset = tmp / "dataset"
            _write_fixture(dataset)
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli_main(["corpus", "reachability", "--dataset", str(dataset)])
            self.assertEqual(code, 0)
            summary = json.loads(out.getvalue())
            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["root_doc_id"], "index")
            self.assertEqual(summary["surface_sha256"], sha256_file(dataset / SUPPORT_REACHABILITY_FILE))
            self.assertTrue((dataset / "support_reachability_manifest.json").is_file())
            self.assertEqual(summary["aggregate"]["fraction_within_3_hops"], round(2 / 3, 6))


if __name__ == "__main__":
    unittest.main()
