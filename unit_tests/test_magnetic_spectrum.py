import itertools
import unittest

import numpy as np
from scipy.linalg import eigh as generalized_eigh

from retrieval_arena.graph import magnetic_phase
from retrieval_arena.graph import magnetic_spectrum as ms

GRID = ms.SENSITIVITY_Q
PATH_NODES = ['a', 'b', 'c', 'd', 'isolate']
PATH_EDGES = [('a', 'b'), ('b', 'c'), ('c', 'd')]


def random_graph(n, p, recip, seed):
    rng = np.random.default_rng(seed)
    nodes = [f'n{i:03d}' for i in range(n)]
    edges = set()
    for i, j in itertools.permutations(range(n), 2):
        if i < j and rng.random() < p:
            edges.add((nodes[i], nodes[j]) if rng.random() < .5 else (nodes[j], nodes[i]))
            if rng.random() < recip:
                edges.add((nodes[j], nodes[i]))
                edges.add((nodes[i], nodes[j]))
    return nodes, sorted(edges)


def bundle(nodes, edges, node_sets=None, graph_id='g'):
    admitted = ms.admit_graph(nodes, edges)
    out = {'schema_version': ms.BUNDLE_SCHEMA, 'graph_id': graph_id, 'nodes': admitted['nodes'],
           'edges': [list(e) for e in admitted['edges']],
           'canonical_graph_sha256': ms.graph_sha256(admitted['nodes'], admitted['edges'])}
    if node_sets:
        out['node_sets'] = node_sets
    return out


def eig(nodes, edges, q):
    return np.linalg.eigvalsh(ms.normalized_magnetic_matrix(nodes, edges, q))


class ConstructionTests(unittest.TestCase):
    def test_elementwise_construction_matches_independent_formula(self):
        nodes, edges = random_graph(14, .3, .3, 1)
        nodes = nodes + ['lonely']
        es = set(edges)
        for q in GRID:
            lap = ms.normalized_magnetic_matrix(nodes, edges, q)
            deg = {v: sum((1.0 if (u, v) in es and (v, u) in es else .5) for u in nodes
                          if (u, v) in es or (v, u) in es) for v in nodes}
            for i, u in enumerate(nodes):
                for j, v in enumerate(nodes):
                    if i == j:
                        expected = 1.0 if deg[u] > 0 else 0.0
                    elif (u, v) in es or (v, u) in es:
                        w = 1.0 if (u, v) in es and (v, u) in es else .5
                        a_uv, a_vu = float((u, v) in es), float((v, u) in es)
                        expected = -w * np.exp(2j * np.pi * q * (a_uv - a_vu)) / np.sqrt(deg[u] * deg[v])
                    else:
                        expected = 0.0
                    self.assertLess(abs(lap[i, j] - expected), 1e-15)

    def test_generalized_eigenproblem_reference_on_positive_degree_nodes(self):
        for nodes, edges in ((PATH_NODES, PATH_EDGES), random_graph(25, .15, .2, 2)):
            for q in GRID:
                lu = ms.unnormalized_magnetic_matrix(nodes, edges, q)
                d = np.real(np.diag(lu))
                pos = np.flatnonzero(d > 0)
                ref = generalized_eigh(lu[np.ix_(pos, pos)], np.diag(d[pos]), eigvals_only=True)
                got = eig(nodes, edges, q)
                expected = np.sort(np.concatenate([ref, np.zeros(len(nodes) - len(pos))]))
                np.testing.assert_allclose(got, expected, atol=1e-12)
        np.testing.assert_allclose(eig(PATH_NODES, PATH_EDGES, .25), [0, 0, .5, 1.5, 2], atol=1e-12)

    def test_ignored_normalization_and_scalar_rescaling_are_caught(self):
        for q in (0.0, .25):
            norm = ms.normalized_magnetic_matrix(PATH_NODES, PATH_EDGES, q)
            unnorm = ms.unnormalized_magnetic_matrix(PATH_NODES, PATH_EDGES, q)
            self.assertAlmostEqual(float(np.max(np.abs(norm - unnorm))), .5, places=12)
            e_norm = np.linalg.eigvalsh(norm)
            e_unnorm = np.linalg.eigvalsh(unnorm)
            d = np.real(np.diag(unnorm))
            for scale in (d.max(), d.mean(), d[d > 0].mean()):
                self.assertGreater(float(np.max(np.abs(e_unnorm / scale - e_norm))), 1e-3)
        np.testing.assert_allclose(np.linalg.eigvalsh(ms.unnormalized_magnetic_matrix(PATH_NODES, PATH_EDGES, .25)),
                                   [0, 0, 1 - np.sqrt(2) / 2, 1, 1 + np.sqrt(2) / 2], atol=1e-12)

    def test_unnormalized_comparator_equals_legacy_constructor(self):
        for seed in range(4):
            nodes, edges = random_graph(12, .3, .4, seed)
            for q in GRID:
                np.testing.assert_array_equal(ms.unnormalized_magnetic_matrix(nodes, edges, q),
                                              magnetic_phase.magnetic_matrix(nodes, edges, q))

    def test_uniform_weight_scaling_leaves_operator_invariant(self):
        rng = np.random.default_rng(5)
        w = np.triu(rng.uniform(0, 3, (8, 8)) * (rng.random((8, 8)) < .5), 1)
        w = w + w.T
        theta = np.triu(rng.uniform(-np.pi, np.pi, (8, 8)), 1)
        theta = theta - theta.T
        base = ms.normalized_operator_from_weights(w, theta)
        for c in (1e-3, .5, 7.0, 1e4):
            np.testing.assert_allclose(ms.normalized_operator_from_weights(c * w, theta), base, atol=1e-14)
        self.assertGreater(np.max(np.abs(ms.normalized_operator_from_weights(w, 2 * theta) - base)), 1e-3)


class NumericalTests(unittest.TestCase):
    def test_hermitian_bounds_residual_and_orthogonality(self):
        for seed in range(5):
            nodes, edges = random_graph(30, .12, .25, 10 + seed)
            for q in GRID:
                lap = ms.normalized_magnetic_matrix(nodes, edges, q)
                self.assertLessEqual(float(np.max(np.abs(lap - lap.conj().T))), ms.SpectrumConfig().hermitian_absolute)
                e = np.linalg.eigvalsh(lap)
                self.assertGreaterEqual(e[0], -1e-12)
                self.assertLessEqual(e[-1], 2 + 1e-12)
                rec = ms.spectrum(nodes, edges, q)
                self.assertEqual(rec['status'], 'qualified', rec['reasons'])
                self.assertTrue(rec['full_spectrum'])

    def test_dense_sparse_parity_compares_invariant_subspaces(self):
        # Two components give a repeated zero eigenvalue: compare projectors, never vector phases.
        nodes, edges = random_graph(60, .08, .2, 21)
        extra = ['z1', 'z2', 'z3']
        nodes = nodes + extra
        edges = edges + [('z1', 'z2'), ('z2', 'z3')]
        for q in (0.0, .1, .25):
            dense = ms.spectrum(nodes, edges, q, ms.SpectrumConfig(modes=4))
            sparse = ms.spectrum(nodes, edges, q, ms.SpectrumConfig(modes=4, solver='sparse'))
            self.assertEqual(dense['status'], 'qualified')
            self.assertEqual(sparse['status'], 'qualified', sparse['reasons'])
            np.testing.assert_allclose(sparse['eigenvalues'], dense['eigenvalues'], atol=1e-10)
            e = np.linalg.eigvalsh(ms.normalized_magnetic_matrix(nodes, edges, q))
            if e[4] - e[3] > 1e-6:
                self.assertLess(ms.subspace_distance(dense['_vectors'], sparse['_vectors']), 1e-8)

    def test_permutation_edge_order_and_repeatability(self):
        nodes, edges = random_graph(20, .2, .3, 31)
        lap = ms.normalized_magnetic_matrix(nodes, edges, .25)
        rng = np.random.default_rng(32)
        for _ in range(5):
            p = rng.permutation(len(nodes))
            np.testing.assert_array_equal(ms.normalized_magnetic_matrix([nodes[i] for i in p], edges, .25),
                                          lap[np.ix_(p, p)])
        b = bundle(nodes, edges)
        first = ms.measure_view(b, {'kind': 'lwcc'}, GRID, [0.25])
        shuffled = dict(b, nodes=list(reversed(b['nodes'])), edges=list(reversed(b['edges'])))
        self.assertEqual(ms.measure_view(shuffled, {'kind': 'lwcc'}, GRID, [0.25]), first)
        self.assertEqual(ms.measure_view(b, {'kind': 'lwcc'}, GRID, [0.25]), first)

    def test_input_contract(self):
        admitted = ms.admit_graph(['a', 'b', 'c'], [('a', 'b'), ('a', 'b'), ('b', 'b'), ('b', 'c')])
        self.assertEqual(admitted['edges'], [('a', 'b'), ('b', 'c')])
        self.assertEqual((admitted['duplicate_edges_removed'], admitted['self_loops_removed']), (1, 1))
        for nodes, edges, code in ((['a', 'a'], [], 'duplicate-node'), (['a'], [('a', 'x')], 'unknown-endpoint'),
                                   (['a', 'b'], [('a',)], 'malformed-edge'), ([''], [], 'malformed-node')):
            with self.assertRaisesRegex(ValueError, code):
                ms.admit_graph(nodes, edges)
        b = bundle(['a', 'b'], [('a', 'b')])
        with self.assertRaisesRegex(ValueError, 'bundle-graph-sha256-mismatch'):
            ms.validate_bundle(dict(b, edges=[['b', 'a']]))
        with self.assertRaisesRegex(ValueError, 'bundle-not-canonical'):
            ms.validate_bundle(dict(b, edges=[['a', 'b'], ['a', 'b']]))
        with self.assertRaisesRegex(ValueError, 'node-set-outside-graph'):
            ms.select_view(['a'], [], {'kind': 'induced', 'node_set': 'S'}, {'S': ['x']})

    def test_empty_singleton_isolated_and_disconnected(self):
        empty = ms.spectrum([], [], .25)
        self.assertEqual((empty['status'], empty['reasons']), ('unavailable', ['empty_view']))
        single = ms.spectrum(['a'], [], .25)
        self.assertEqual(single['lambda1'], 0.0)
        self.assertIsNone(single['lambda2'])
        self.assertEqual(single['missing_modes'], {'lambda2': 'fewer_nodes_than_mode_index',
                                                   'lambda3': 'fewer_nodes_than_mode_index'})
        nodes = ['a', 'b', 'c', 'd', 'e', 'iso1', 'iso2']
        edges = [('a', 'b'), ('b', 'c'), ('d', 'e')]
        rec = ms.spectrum(nodes, edges, 0.0, ms.SpectrumConfig(modes=5))
        self.assertEqual(rec['numerical_zero_count'], 4)  # two edge components + two isolates
        self.assertEqual(rec['near_zero'], [True, True, True, True, False])
        self.assertEqual(rec['status'], 'qualified')


class InvarianceAndSensitivityTests(unittest.TestCase):
    def test_q0_uses_the_same_w_and_differs_from_binary_skeleton(self):
        nodes = ['a', 'b', 'c', 'd']
        edges = [('a', 'b'), ('b', 'a'), ('b', 'c'), ('c', 'd'), ('d', 'a')]
        a = np.zeros((4, 4))
        idx = {v: i for i, v in enumerate(nodes)}
        for s, t in edges:
            a[idx[s], idx[t]] = 1
        w = (a + a.T) / 2
        d = w.sum(axis=1)
        ordinary = np.eye(4) - w / np.sqrt(np.outer(d, d))
        np.testing.assert_allclose(ms.normalized_magnetic_matrix(nodes, edges, 0.0), ordinary, atol=1e-15)
        weighted = float(np.linalg.eigvalsh(ordinary)[1])
        legacy = ms.legacy_binary_skeleton_normalized_lambda2(nodes, edges)
        self.assertGreater(abs(weighted - legacy), 1e-3)
        receipt = ms.measure_view(bundle(nodes, edges), {'kind': 'full'}, [0.0], [])
        self.assertAlmostEqual(receipt['weighted_normalized_lambda2_q0'], weighted, places=12)
        self.assertAlmostEqual(receipt['legacy_binary_skeleton_normalized_lambda2'], legacy, places=12)

    def test_fully_reciprocal_graph_is_q_independent(self):
        nodes, edges = random_graph(16, .25, 1.0, 41)
        base = eig(nodes, edges, 0.0)
        for q in GRID:
            np.testing.assert_allclose(eig(nodes, edges, q), base, atol=1e-12)

    def test_global_reversal_conjugates_matrix(self):
        nodes, edges = random_graph(15, .25, .2, 42)
        reversed_edges = [(t, s) for s, t in edges]
        for q in GRID:
            np.testing.assert_array_equal(ms.normalized_magnetic_matrix(nodes, reversed_edges, q),
                                          ms.normalized_magnetic_matrix(nodes, edges, q).conj())
            np.testing.assert_allclose(eig(nodes, reversed_edges, q), eig(nodes, edges, q), atol=1e-12)

    def test_reorienting_a_fixed_weight_forest_preserves_spectrum(self):
        tree = [('r', 'a'), ('r', 'b'), ('a', 'c'), ('a', 'd'), ('b', 'e'), ('x', 'y')]
        nodes = sorted({v for e in tree for v in e} | {'iso'})
        base = eig(nodes, tree, .25)
        for flips in itertools.product((False, True), repeat=len(tree)):
            edges = [(t, s) if f else (s, t) for (s, t), f in zip(tree, flips)]
            np.testing.assert_allclose(eig(nodes, edges, .25), base, atol=1e-12)

    def test_cycle_flux_balance_and_nonzero_response(self):
        cycle = [('a', 'b'), ('b', 'c'), ('c', 'd'), ('d', 'a')]
        nodes = list('abcd')
        self.assertLess(abs(eig(nodes, cycle, .25)[0]), 1e-12)  # flux 2 pi: balanced
        changed = [('a', 'b'), ('b', 'c'), ('c', 'd'), ('a', 'd')]
        self.assertGreater(eig(nodes, changed, .25)[0], .1)  # flux pi: frustrated
        self.assertGreater(eig(nodes, cycle, .125)[0], .1)  # same cycle, different charge
        triangle = [('a', 'b'), ('b', 'c'), ('c', 'a')]
        # Closed form for a uniformly weighted directed n-cycle: 1 - cos((2 pi k + flux) / n), flux = n 2 pi q.
        for q in GRID:
            flux = 3 * 2 * np.pi * q
            expected = sorted(1 - np.cos((2 * np.pi * k + flux) / 3) for k in range(3))
            np.testing.assert_allclose(eig(list('abc'), triangle, q), expected, atol=1e-12)
        self.assertGreater(eig(list('abc'), triangle, .15)[0], .4)


class ComparatorTests(unittest.TestCase):
    def test_trophic_incoherence_fixtures(self):
        path = ms.trophic_incoherence(list('abcd'), [('a', 'b'), ('b', 'c'), ('c', 'd')])
        self.assertLess(path['value'], 1e-20)
        levels, _, _, _ = ms.trophic_levels(list('abcd'), [('a', 'b'), ('b', 'c'), ('c', 'd')])
        np.testing.assert_allclose([levels[v] for v in 'abcd'], [0, 1, 2, 3], atol=1e-12)
        self.assertAlmostEqual(ms.trophic_incoherence(list('abcd'), [('a', 'b'), ('b', 'c'), ('c', 'd'), ('d', 'a')])['value'], 1.0)
        self.assertAlmostEqual(ms.trophic_incoherence(['a', 'b'], [('a', 'b'), ('b', 'a')])['value'], 1.0)
        mixed = ms.trophic_incoherence(list('abcdefgh'), [('a', 'b'), ('b', 'c'), ('d', 'e'), ('e', 'f'), ('f', 'd')])
        self.assertAlmostEqual(mixed['value'], 3 / 5)  # 0 on the path's 2 arcs, 1 on the cycle's 3 arcs
        self.assertEqual((mixed['components_with_edges'], mixed['edgeless_components']), (2, 2))
        edgeless = ms.trophic_incoherence(['a', 'b'], [])
        self.assertEqual((edgeless['status'], edgeless['reasons']), ('unavailable', ['edgeless_view']))

    def test_trophic_incoherence_is_invariant_to_additive_level_shifts(self):
        nodes, edges = random_graph(18, .2, .2, 51)
        levels, groups, _, _ = ms.trophic_levels(nodes, edges)
        base = sum(ms.incoherence_from_levels(levels, ce) for ce in groups)
        shifted = {v: h + 3.7 for v, h in levels.items()}
        self.assertAlmostEqual(sum(ms.incoherence_from_levels(shifted, ce) for ce in groups), base, places=9)
        # Any other level vector on the same component cannot do better than the solved minimizer.
        rng = np.random.default_rng(52)
        for _ in range(5):
            noisy = {v: h + rng.normal(0, .3) for v, h in levels.items()}
            self.assertGreaterEqual(sum(ms.incoherence_from_levels(noisy, ce) for ce in groups), base - 1e-9)

    def test_structural_descriptors(self):
        nodes = ['a', 'b', 'c', 'd', 'iso']
        edges = [('a', 'b'), ('b', 'a'), ('b', 'c'), ('d', 'c')]
        s = ms.structural_descriptors(nodes, edges)
        self.assertEqual((s['nodes'], s['arcs'], s['weak_components'], s['isolates']), (5, 4, 2, 1))
        self.assertEqual((s['lwcc_nodes'], s['largest_scc_nodes'], s['reciprocal_arcs']), (4, 2, 2))
        self.assertEqual((s['sources'], s['sinks']), (1, 1))
        self.assertAlmostEqual(s['reciprocal_arc_fraction'], .5)
        self.assertAlmostEqual(s['weighted_degree']['max'], 1.5)
        self.assertAlmostEqual(s['weighted_degree']['min_positive'], .5)

    def test_view_selection_and_tie_break(self):
        nodes = ['a', 'b', 'c', 'd', 'e']
        edges = [('c', 'd'), ('a', 'b')]
        vn, ve, rec = ms.select_view(nodes, edges, {'kind': 'lwcc'})
        self.assertEqual((vn, ve, rec['lwcc_tie_count']), (['a', 'b'], [('a', 'b')], 2))
        vn, ve, rec = ms.select_view(nodes, edges, {'kind': 'induced', 'node_set': 'S'}, {'S': ['c', 'd', 'e']})
        self.assertEqual((vn, ve), (['c', 'd', 'e'], [('c', 'd')]))
        vn, _, _ = ms.select_view(nodes, edges, {'kind': 'induced_lwcc', 'node_set': 'S'}, {'S': ['c', 'd', 'e']})
        self.assertEqual(vn, ['c', 'd'])

    def test_run_jobs_config_contract(self):
        b = bundle(PATH_NODES, PATH_EDGES)
        config = {'schema_version': ms.CONFIG_SCHEMA, 'spectrum_config': {'modes': 3},
                  'jobs': [{'job_id': 'p', 'bundle': 'b.json', 'view': {'kind': 'full'}, 'normalized_q': [0.25]}]}
        self.assertEqual(ms.validate_jobs_config(config), ms.SpectrumConfig())
        for bad in (dict(config, extra=1), dict(config, jobs=config['jobs'] * 2),
                    dict(config, jobs=[dict(config['jobs'][0], bundle='../b.json')]),
                    dict(config, spectrum_config={'solver': 'magic'})):
            with self.assertRaises(ValueError):
                ms.validate_jobs_config(bad)
        receipt = ms.measure_view(b, {'kind': 'full'}, [0.25], [0.25])
        self.assertEqual(receipt['metric_version'], 'normalized_magnetic_spectrum.v1')
        self.assertEqual(receipt['numerical_status'], 'qualified')
        self.assertFalse(receipt['optimization_admitted'])
        self.assertNotIn('_vectors', receipt['normalized']['0.25'])


if __name__ == '__main__':
    unittest.main()
