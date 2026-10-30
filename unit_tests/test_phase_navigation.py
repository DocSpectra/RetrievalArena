from __future__ import annotations

import unittest

import numpy as np

from retrieval_arena.graph.phase_navigation import (
    matched_trials,
    phase_dispersion,
    qualify_phase_modes,
    qualify_subspace_polarization,
    sample_random_entries,
    traverse_once,
)
from retrieval_arena.graph.magnetic_spectrum import normalized_magnetic_matrix


class PhaseNavigationTests(unittest.TestCase):
    def test_dispersion_is_global_phase_and_node_order_invariant(self):
        v = np.asarray([0.7, 0.5j, -0.2 + 0.1j], dtype=complex)
        a = phase_dispersion(v)["value"]
        self.assertAlmostEqual(a, phase_dispersion(v * np.exp(1.731j))["value"], places=14)
        self.assertAlmostEqual(a, phase_dispersion(v[[2, 0, 1]])["value"], places=14)

    def test_zero_and_weak_amplitudes_are_safe(self):
        self.assertEqual(phase_dispersion(np.zeros(3, dtype=complex))["status"], "unavailable")
        result = phase_dispersion(np.asarray([1 + 0j, 1e-100j]))
        self.assertEqual(result["status"], "qualified")
        self.assertGreaterEqual(result["value"], 0.0)

    def test_wrapped_angles_near_pi_have_low_circular_dispersion(self):
        v = np.exp(1j * np.asarray([np.pi - 1e-6, -np.pi + 1e-6])) / np.sqrt(2)
        self.assertLess(phase_dispersion(v)["value"], 1e-10)

    def test_v2_refuses_unresolved_mode(self):
        matrix = np.diag([0.0, 0.2, 0.2, 1.0]).astype(complex)
        values, vectors = np.linalg.eigh(matrix)
        result = qualify_phase_modes(matrix, values, vectors)
        self.assertEqual(result["status"], "qualified")
        self.assertEqual(result["V2"]["status"], "unavailable")
        self.assertEqual(result["V2"]["reason"], "unresolved-mode")

    def test_gap_tolerance_changes_only_numerical_availability(self):
        matrix = np.diag([0.0, 0.01, 0.010000001, 1.0]).astype(complex)
        values, vectors = np.linalg.eigh(matrix)
        strict = qualify_phase_modes(matrix, values, vectors, gap_relative=1e-8)
        relaxed = qualify_phase_modes(matrix, values, vectors, gap_relative=1e-10)
        self.assertEqual(strict["V2"]["status"], "unavailable")
        self.assertEqual(relaxed["V2"]["status"], "qualified")

    def test_graph_fixture_families_and_reversal_invariances(self):
        fixtures = {
            "path": (list("abc"), [("a", "b"), ("b", "c")]),
            "in_star": (list("abc"), [("a", "c"), ("b", "c")]),
            "out_star": (list("abc"), [("c", "a"), ("c", "b")]),
            "reciprocal": (list("abc"), [("a", "b"), ("b", "a"), ("b", "c"), ("c", "b")]),
            "cycle": (list("abc"), [("a", "b"), ("b", "c"), ("c", "a")]),
            "disconnected_isolate": (list("abcd"), [("a", "b"), ("c", "d")]),
        }
        for name, (nodes, edges) in fixtures.items():
            with self.subTest(name=name):
                matrix = normalized_magnetic_matrix(nodes, edges, 0.25)
                values, vectors = np.linalg.eigh(matrix)
                phase = qualify_phase_modes(matrix, values, vectors)
                polar = qualify_subspace_polarization(nodes, edges, matrix, values, vectors)
                self.assertIn(phase["status"], {"qualified", "failed"})
                self.assertIn(polar["status"], {"qualified", "unavailable", "failed"})
                reverse = [(t, s) for s, t in edges]
                rev_matrix = normalized_magnetic_matrix(nodes, reverse, 0.25)
                rev_values, rev_vectors = np.linalg.eigh(rev_matrix)
                self.assertTrue(np.allclose(values, rev_values, atol=1e-12))
                rev_polar = qualify_subspace_polarization(nodes, reverse, rev_matrix, rev_values, rev_vectors)
                if polar["status"] == "qualified" and rev_polar["status"] == "qualified":
                    self.assertAlmostEqual(polar["value"], rev_polar["value"], places=12)
        n, e = fixtures["disconnected_isolate"]
        matrix = normalized_magnetic_matrix(n, e, 0.25)
        values, vectors = np.linalg.eigh(matrix)
        full = qualify_subspace_polarization(n, e, matrix, values, vectors, requested_rank=10)
        self.assertEqual(full["actual_rank"], len(n))
        self.assertTrue(full["full_spectrum"])
        self.assertTrue(full["full_spectrum_zero_degeneracy"])

    def test_subspace_statistic_is_invariant_within_complete_cluster(self):
        nodes = ["a", "b", "c", "d"]
        edges = [("a", "b"), ("b", "c"), ("c", "d")]
        matrix = np.diag([0.0, 0.0, 1.0, 2.0]).astype(complex)
        values, vectors = np.linalg.eigh(matrix)
        theta = 0.43
        rotation = np.asarray([[np.cos(theta), -np.sin(theta)],
                               [np.sin(theta), np.cos(theta)]], dtype=complex)
        rotated = vectors.copy()
        rotated[:, :2] = vectors[:, :2] @ rotation
        first = qualify_subspace_polarization(nodes, edges, matrix, values, vectors, requested_rank=2)
        second = qualify_subspace_polarization(nodes, edges, matrix, values, rotated, requested_rank=2)
        self.assertEqual(first["status"], "qualified")
        self.assertEqual(second["status"], "qualified")
        self.assertAlmostEqual(first["value"], second["value"], places=14)
        self.assertEqual(first["actual_rank"], 2)

    def test_random_entries_are_stable_without_replacement_and_sorted_input_independent(self):
        nodes = ["d", "a", "c", "b"]
        a = sample_random_entries(nodes, master_seed=29061029, family="main", view="lwcc", query_id="q")
        b = sample_random_entries(list(reversed(nodes)), master_seed=29061029, family="main", view="lwcc", query_id="q")
        self.assertEqual(a, b)
        self.assertEqual(len(a), len(set(a)))

    def test_traversal_is_outgoing_and_strictly_bounded(self):
        nodes = ["a", "b", "c", "d"]
        edges = [("a", "b"), ("c", "a"), ("b", "d")]
        scores = {n: 0.5 for n in nodes}
        directed = traverse_once(nodes, edges, scores, "a", candidate_budget=2, final_top_k=4)
        self.assertEqual(set(directed["candidate_ids"]), {"a", "b"})
        self.assertLessEqual(directed["candidate_count"], 2)
        undirected = traverse_once(nodes, edges, scores, "a", candidate_budget=4, final_top_k=4, directed=False)
        self.assertIn("c", undirected["candidate_ids"])

    def test_semantic_and_random_use_one_traversal_policy(self):
        nodes = ["a", "b", "c", "d"]
        edges = [("a", "b"), ("b", "c")]
        scores = {"a": 0.1, "b": 0.9, "c": 0.2, "d": 0.0}
        semantic, random = matched_trials(nodes, edges, scores, "q", family="f", view="lwcc",
                                          random_count=3, candidate_budget=2, final_top_k=2)
        self.assertEqual(semantic["trials"][0]["seed_id"], "b")
        self.assertEqual(len(random["trials"]), 3)
        self.assertTrue(all(len(t["candidate_ids"]) <= 2 for t in random["trials"]))
        self.assertEqual(random["seed_ids"], [t["seed_id"] for t in random["trials"]])


if __name__ == "__main__":
    unittest.main()
