import itertools
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import numpy as np

from retrieval_arena.graph import magnetic_attribution as ma
from retrieval_arena.graph import magnetic_spectrum as ms

SPEC = ms.SpectrumConfig()
CFG = ma.AttributionConfig()
# A fixture of its own (not the consuming study's small cases): a triangle sharing node c with a one-way
# four-cycle that closes back through a, plus a pendant arc.
FIVE = ['a', 'b', 'c', 'd', 'e', 'f']
FIVE_EDGES = [('a', 'b'), ('b', 'c'), ('c', 'a'), ('c', 'd'), ('d', 'e'), ('e', 'a'), ('b', 'f')]
TRIANGLE = [('a', 'b'), ('b', 'c'), ('c', 'a')]


def random_graph(n, p, recip, seed):
    rng = np.random.default_rng(seed)
    nodes = [f'n{i:03d}' for i in range(n)]
    edges = set()
    for i, j in itertools.combinations(range(n), 2):
        if rng.random() < p:
            edges.add((nodes[i], nodes[j]) if rng.random() < .5 else (nodes[j], nodes[i]))
            if rng.random() < recip:
                edges.add((nodes[j], nodes[i]))
                edges.add((nodes[i], nodes[j]))
    return nodes, sorted(edges)


def bundle(nodes, edges, graph_id='g', node_sets=None):
    admitted = ms.admit_graph(nodes, edges)
    out = {'schema_version': ms.BUNDLE_SCHEMA, 'graph_id': graph_id, 'nodes': admitted['nodes'],
           'edges': [list(e) for e in admitted['edges']],
           'canonical_graph_sha256': ms.graph_sha256(admitted['nodes'], admitted['edges'])}
    if node_sets:
        out['node_sets'] = node_sets
    return out


def first_cycle(nodes, edges):
    found = ma.nonreciprocal_cycles(nodes, edges, max_cycles=1)
    return found['cycles'][0] if found['cycles'] else None


def independent_laplacian(nodes, edges, q):
    """Entrywise construction from the definition, independent of magnetic_spectrum."""
    es = set(edges)
    n = len(nodes)
    w = np.zeros((n, n))
    th = np.zeros((n, n))
    for i, u in enumerate(nodes):
        for j, v in enumerate(nodes):
            if i != j and ((u, v) in es or (v, u) in es):
                w[i, j] = 1.0 if (u, v) in es and (v, u) in es else 0.5
                th[i, j] = 2 * np.pi * q * (float((u, v) in es) - float((v, u) in es))
    d = w.sum(axis=1)
    lap = np.zeros((n, n), dtype=complex)
    for i in range(n):
        lap[i, i] = 1.0 if d[i] > 0 else 0.0
        for j in range(n):
            if w[i, j] > 0:
                lap[i, j] = -w[i, j] * np.exp(1j * th[i, j]) / np.sqrt(d[i] * d[j])
    return lap, w, th


class CycleSelectionTests(unittest.TestCase):
    def test_deterministic_order_canonical_rotation_and_reciprocal_exclusion(self):
        edges = [('a', 'b'), ('b', 'c'), ('c', 'a'),            # triangle
                 ('c', 'd'), ('d', 'e'), ('e', 'f'), ('f', 'c'),  # four-cycle
                 ('x', 'y'), ('y', 'z'), ('z', 'x'), ('y', 'x')]  # triangle with a reciprocal arc
        nodes = sorted({v for e in edges for v in e})
        result = ma.nonreciprocal_cycles(nodes, edges)
        self.assertEqual(result['cycles'], [['a', 'b', 'c'], ['c', 'd', 'e', 'f']])
        self.assertEqual(result['status'], 'search_complete')
        self.assertEqual(result['found_per_length'], {'3': 1, '4': 1, '5': 0, '6': 0})
        again = ma.nonreciprocal_cycles(list(reversed(nodes)), list(reversed(edges)))
        self.assertEqual(again, result)

    def test_max_cycles_and_budget_cap_are_deterministic(self):
        nodes, edges = random_graph(30, .25, .1, 7)
        full = ma.nonreciprocal_cycles(nodes, edges, max_cycles=4, budget=100000)
        self.assertEqual(full['status'], 'max_cycles_reached')
        self.assertEqual(len(full['cycles']), 4)
        for c in full['cycles']:
            self.assertEqual(c[0], min(c))
            ma.validate_cycle(edges, c)
        capped = ma.nonreciprocal_cycles(nodes, edges, max_cycles=4, budget=5)
        self.assertEqual(capped['status'], 'budget_exhausted')
        self.assertEqual(capped['expansions'], 5)
        self.assertEqual(capped, ma.nonreciprocal_cycles(nodes, edges, max_cycles=4, budget=5))
        lengths = [len(c) for c in full['cycles']]
        self.assertEqual(lengths, sorted(lengths))

    def test_selection_reads_topology_only(self):
        nodes, edges = random_graph(20, .3, .2, 3)
        with mock.patch.object(np.linalg, 'eigh', side_effect=AssertionError('spectral call')), \
                mock.patch.object(ms, 'spectrum', side_effect=AssertionError('spectral call')):
            result = ma.nonreciprocal_cycles(nodes, edges)
        self.assertTrue(result['cycles'])

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, 'reciprocal-cycle-arc'):
            ma.validate_cycle([('a', 'b'), ('b', 'a'), ('b', 'c'), ('c', 'a')], ['a', 'b', 'c'])
        with self.assertRaisesRegex(ValueError, 'reversal-reciprocal-arc'):
            ma.reverse_arcs([('a', 'b'), ('b', 'a')], [('a', 'b')])
        with self.assertRaisesRegex(ValueError, 'cycle-arc-absent'):
            ma.validate_cycle([('a', 'b'), ('b', 'c')], ['a', 'b', 'c'])
        with self.assertRaisesRegex(ValueError, 'not-a-simple-cycle'):
            ma.validate_cycle([('a', 'b'), ('b', 'a')], ['a', 'b'])
        self.assertIsNone(ma.arcs_form_cycle([('a', 'b'), ('b', 'c'), ('c', 'd')]))
        self.assertEqual(ma.arcs_form_cycle([('b', 'c'), ('c', 'a'), ('a', 'b')]), ['a', 'b', 'c'])


class ControlTests(unittest.TestCase):
    def test_cycle_reversal_preserves_declared_controls(self):
        for seed in range(4):
            nodes, edges = random_graph(25, .2, .25, seed)
            cycle = first_cycle(nodes, edges)
            arcs = ma.validate_cycle(edges, cycle)
            flipped = ma.reverse_arcs(edges, arcs)
            c = ma.control_checks(nodes, edges, nodes, flipped, 'matched_cycle_reversal', arcs)
            self.assertTrue(c['passed'], c['checks'])
            self.assertTrue(c['degree_matched'])
            self.assertEqual(c['descriptors_that_change'], [])
            self.assertLessEqual(c['trophic_incoherence']['abs_diff'], 1e-9)
            self.assertTrue(np.array_equal(ms.normalized_magnetic_matrix(nodes, edges, 0.0),
                                           ms.normalized_magnetic_matrix(nodes, flipped, 0.0)))

    def test_failed_assertion_quarantines(self):
        nodes, edges = random_graph(25, .2, .25, 1)
        arcs = ma.validate_cycle(edges, first_cycle(nodes, edges))
        wrong = ma.reverse_arcs(edges, arcs[:1])     # one arc only: degrees change
        c = ma.control_checks(nodes, edges, nodes, wrong, 'matched_cycle_reversal', arcs)
        self.assertFalse(c['passed'])
        self.assertTrue(c['quarantined'])
        self.assertFalse(c['checks']['declared_change_reproduces_b'])

    def test_forest_and_global_controls_are_not_degree_matched_positive_cases(self):
        path = [('a', 'b'), ('b', 'c'), ('c', 'd')]
        reoriented = ma.reverse_arcs(path, [('a', 'b'), ('c', 'd')])
        nodes = ['a', 'b', 'c', 'd']
        c = ma.control_checks(nodes, path, nodes, reoriented, 'forest_reorientation', [('a', 'b'), ('c', 'd')])
        self.assertTrue(c['passed'])
        self.assertFalse(c['degree_matched'])
        g = ma.control_checks(FIVE, FIVE_EDGES, FIVE, sorted((t, s) for s, t in FIVE_EDGES), 'global_reversal', [])
        self.assertTrue(g['passed'])
        self.assertTrue(g['checks']['in_out_degrees_swapped'])


class IdentityTests(unittest.TestCase):
    def test_energy_sum_and_fields_match_an_independent_construction(self):
        nodes, edges = random_graph(16, .3, .3, 11)
        nodes = nodes + ['zz_isolate']
        for q in (0.10, 0.25):
            lap, w, th = independent_laplacian(nodes, edges, q)
            np.testing.assert_allclose(lap, ms.normalized_magnetic_matrix(nodes, edges, q), atol=1e-14)
            values, vectors = np.linalg.eigh(lap)
            d = w.sum(axis=1)
            for r in range(4):
                u = vectors[:, r]
                mass, i, j, energy = ma.mode_fields(w, th, u)
                self.assertAlmostEqual(mass.sum(), 1.0, places=12)
                self.assertAlmostEqual(energy.sum(), values[r], places=12)
                manual = 0.0
                for a in range(len(nodes)):
                    for b in range(a + 1, len(nodes)):
                        if w[a, b] > 0:
                            fa, fb = u[a] / np.sqrt(d[a]), u[b] / np.sqrt(d[b])
                            manual += w[a, b] * abs(fa - np.exp(1j * th[a, b]) * fb) ** 2
                self.assertAlmostEqual(manual, values[r], places=12)

    def test_fixed_mode_relaxation_account(self):
        for seed in range(3):
            nodes, edges = random_graph(22, .25, .2, 20 + seed)
            arcs = ma.validate_cycle(edges, first_cycle(nodes, edges))
            flipped = ma.reverse_arcs(edges, arcs)
            a = ma._Side(nodes, edges, 0.25, SPEC, CFG)
            b = ma._Side(nodes, flipped, 0.25, SPEC, CFG)
            fc = ma.finite_change(a, b, [sorted(x) for x in arcs], CFG)
            self.assertEqual(fc['status'], 'computed')
            la, _, _ = independent_laplacian(nodes, edges, 0.25)
            lb, _, _ = independent_laplacian(nodes, flipped, 0.25)
            ua = np.linalg.eigh(la)[1][:, 0]
            fixed = np.real(ua.conj() @ (lb - la) @ ua)
            relax = np.linalg.eigvalsh(lb)[0] - np.real(ua.conj() @ lb @ ua)
            self.assertAlmostEqual(fc['fixed_mode_term'], fixed, places=12)
            self.assertAlmostEqual(fc['relaxation_term'], relax, places=12)
            self.assertTrue(fc['account_ok'])
            self.assertTrue(fc['relaxation_nonpositive'])
            self.assertLess(fc['fixed_mode_edge_sum_residual'], 1e-12)
            self.assertEqual(fc['unchanged_edges_fixed_mode_max_abs'], 0.0)


class InvarianceTests(unittest.TestCase):
    def test_global_phase_and_unitary_rotation_within_eigenspace(self):
        # Two disjoint directed triangles: the ground eigenvalue is exactly doubly degenerate.
        edges = [('a', 'b'), ('b', 'c'), ('c', 'a'), ('x', 'y'), ('y', 'z'), ('z', 'x')]
        nodes = ['a', 'b', 'c', 'x', 'y', 'z']
        w, th = ma.undirected_weights(nodes, edges), ma.phases(nodes, edges, 0.25)
        values, vectors = np.linalg.eigh(ms.normalized_magnetic_matrix(nodes, edges, 0.25))
        u = vectors[:, :2]
        rng = np.random.default_rng(5)
        q_mat, _ = np.linalg.qr(rng.standard_normal((2, 2)) + 1j * rng.standard_normal((2, 2)))
        base = ma.mode_fields(w, th, u)
        for other in (u * np.exp(0.7j), u @ q_mat):
            got = ma.mode_fields(w, th, other)
            np.testing.assert_allclose(got[0], base[0], atol=1e-14)
            np.testing.assert_allclose(got[3], base[3], atol=1e-14)
        self.assertAlmostEqual(base[3].sum(), values[:2].mean(), places=13)

    def test_node_permutation(self):
        nodes, edges = random_graph(18, .3, .25, 2)
        ref = ma._Side(nodes, edges, 0.25, SPEC, CFG)
        check = ma.permutation_check(nodes, edges, 0.25, SPEC, CFG, 99, ref)
        self.assertEqual(check['status'], 'computed')
        self.assertTrue(check['passed'], check)

    def test_forest_and_global_reversal_spectral_invariance(self):
        path = ['a', 'b', 'c', 'd']
        e1 = [('a', 'b'), ('b', 'c'), ('c', 'd')]
        e2 = [('b', 'a'), ('b', 'c'), ('d', 'c')]
        for q in (0.10, 0.25):
            np.testing.assert_allclose(np.linalg.eigvalsh(ms.normalized_magnetic_matrix(path, e1, q)),
                                       np.linalg.eigvalsh(ms.normalized_magnetic_matrix(path, e2, q)), atol=1e-14)
            a = ma._Side(FIVE, FIVE_EDGES, q, SPEC, CFG)
            g = ma._Side(FIVE, sorted((t, s) for s, t in FIVE_EDGES), q, SPEC, CFG)
            np.testing.assert_allclose(a.values, g.values, atol=1e-14)
            np.testing.assert_allclose(a.mass, g.mass, atol=1e-12)
            np.testing.assert_allclose(a.energy, g.energy, atol=1e-12)


class ClusterTests(unittest.TestCase):
    def test_cluster_completeness_and_boundary(self):
        c = ma.ground_cluster([0.1, 0.1 + 5e-9, 0.1 + 9e-9, 0.5], 1e-15, CFG)
        self.assertEqual(c['rank'], 3)
        self.assertTrue(c['boundary_qualified'])
        self.assertAlmostEqual(c['mean_eigenvalue'], 0.1 + 14e-9 / 3, places=15)
        chain = ma.ground_cluster([0.0, 0.6e-8, 1.2e-8, 1.0], 1e-15, CFG)
        self.assertEqual(chain['rank'], 2)
        self.assertFalse(chain['boundary_qualified'])
        self.assertEqual(chain['reasons'], ['cluster_boundary_unresolved'])
        whole = ma.ground_cluster([0.3, 0.3], 1e-15, CFG)
        self.assertTrue(whole['boundary_qualified'] and whole['closes_at_spectrum_end'])

    def test_repeated_ground_mode_uses_the_projector_and_blocks_single_mode_account(self):
        edges = [('a', 'b'), ('b', 'c'), ('c', 'a'), ('x', 'y'), ('y', 'z'), ('z', 'x')]
        nodes = ['a', 'b', 'c', 'x', 'y', 'z']
        s = ma._Side(nodes, edges, 0.25, SPEC, CFG)
        self.assertEqual(s.cluster['rank'], 2)
        summary = s.summary(CFG)
        self.assertEqual(summary['fields']['energy_total_label'], 'cluster mean eigenvalue (k > 1), not lambda1')
        self.assertAlmostEqual(summary['fields']['mass_sum'], 1.0, places=13)
        self.assertTrue(summary['fields']['energy_sum_identity_ok'])
        fc = ma.finite_change(s, s, [], CFG)
        self.assertEqual(fc['status'], 'unavailable')
        self.assertIn('clustered_ground_mode', fc['reasons'])

    def test_unqualified_boundary_marks_attribution_unavailable(self):
        s = ma._Side(FIVE, FIVE_EDGES, 0.25, SPEC, CFG)
        with mock.patch.object(ma, 'ground_cluster', return_value={'rank': 1, 'boundary_qualified': False, 'mean_eigenvalue': 0.0,
                                                                   'reasons': ['cluster_boundary_unresolved']}):
            t = ma._Side(FIVE, FIVE_EDGES, 0.25, SPEC, CFG)
        self.assertTrue(s.available)
        self.assertFalse(t.available)
        self.assertEqual(ma.finite_change(t, s, [], CFG)['status'], 'unavailable')
        self.assertIn('cluster_boundary_unresolved', t.summary(CFG)['attribution_reasons'])

    def test_isolate_induced_degeneracy(self):
        # A directed triangle has lambda1 = 1 - cos(pi/2 + 4pi/3) > 0 at q = 0.25; an isolate adds an exact zero row.
        full = ma._Side(['a', 'b', 'c', 'z'], TRIANGLE, 0.25, SPEC, CFG)
        lwcc = ma._Side(['a', 'b', 'c'], TRIANGLE, 0.25, SPEC, CFG)
        self.assertLessEqual(abs(full.values[0]), 1e-15)
        self.assertGreater(lwcc.values[0], 1e-6)
        summary = full.summary(CFG)
        self.assertAlmostEqual(summary['fields']['isolate_mass'], 1.0, places=12)
        self.assertFalse(summary['fields']['energy_fractions_available'])
        np.testing.assert_allclose(full.values[1:], np.sort(np.r_[lwcc.values]), atol=1e-14)


class ResolutionTests(unittest.TestCase):
    def run_pair(self, edges_a, edges_b, control, changed, q):
        with tempfile.TemporaryDirectory() as tmp:
            for name, edges in (('a.json', edges_a), ('b.json', edges_b)):
                (Path(tmp) / name).write_text(json.dumps(bundle(FIVE, edges, name)), encoding='utf-8')
            config = {'schema_version': ma.CONFIG_SCHEMA, 'spectrum_config': {'solver': 'dense'},
                      'attribution_config': {}, 'jobs': [
                          {'job_id': 'p', 'kind': 'pair', 'bundle_a': 'a.json', 'bundle_b': 'b.json',
                           'view': {'kind': 'full'}, 'q': q, 'control': control, 'changed_arcs': changed}]}
            return ma.run_jobs(config, tmp)

    def test_q0_is_unresolved_and_zero_energy_is_safe(self):
        tri = [['a', 'b'], ['b', 'c'], ['c', 'a']]
        flipped = ma.reverse_arcs(FIVE_EDGES, [tuple(x) for x in tri])
        rows, artifacts = self.run_pair(FIVE_EDGES, flipped, 'matched_cycle_reversal', tri, [0.0, 0.25])
        block = rows[0]['q']['0.00']
        self.assertEqual(block['delta_lambda1']['raw'], 0.0)
        self.assertFalse(block['delta_lambda1']['resolved'])
        self.assertTrue(block['a']['near_zero'][0])
        self.assertFalse(block['a']['fields']['energy_fractions_available'])
        self.assertIsNone(block['a']['fields']['changed_cycle']['energy_fraction_on_changed_edges'])
        self.assertTrue(all(e['fraction'] is None for e in block['a']['fields']['top_energy_edges']))
        self.assertTrue(rows[0]['controls']['passed'])
        self.assertIn('tables/' + rows[0]['graph_a']['selection']['view_graph_sha256'][:16] + '_q0.25.edges.csv',
                      artifacts)
        json.dumps(rows, allow_nan=False)

    def test_global_reversal_is_unresolved(self):
        rev = sorted((t, s) for s, t in FIVE_EDGES)
        rows, _ = self.run_pair(FIVE_EDGES, rev, 'global_reversal', [], [0.25])
        block = rows[0]['q']['0.25']
        self.assertFalse(block['delta_lambda1']['resolved'])
        self.assertEqual(block['finite_change']['status'], 'unavailable')


class JobContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        nodes, edges = random_graph(20, .3, .2, 4)
        self.nodes, self.edges = nodes, edges
        (self.root / 'base.json').write_text(json.dumps(bundle(nodes, edges, 'base')), encoding='utf-8')
        sel = ma.nonreciprocal_cycles(nodes, edges, max_cycles=2)
        self.arcs = sel['arcs']
        for k, arcs in enumerate(self.arcs):
            flipped = ma.reverse_arcs(edges, [tuple(a) for a in arcs])
            (self.root / f'v{k}.json').write_text(json.dumps(bundle(nodes, flipped, f'v{k}')), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, ref_index=0, arcs_index=0):
        return {'schema_version': ma.CONFIG_SCHEMA, 'spectrum_config': {'solver': 'dense'},
                'attribution_config': asdict(CFG), 'jobs': [
                    {'job_id': 'sel', 'kind': 'cycle_selection', 'bundle': 'base.json', 'view': {'kind': 'full'},
                     'lengths': [3, 4, 5, 6], 'max_cycles': 2, 'budget': 100000},
                    {'job_id': 'pair0', 'kind': 'pair', 'bundle_a': 'base.json', 'bundle_b': f'v{arcs_index}.json',
                     'view': {'kind': 'full'}, 'q': [0.0, 0.25], 'control': 'matched_cycle_reversal',
                     'changed_arcs': self.arcs[arcs_index], 'selection_ref': {'job_id': 'sel', 'index': ref_index},
                     'permutation_seed': 1}]}

    def test_selection_replay_and_records(self):
        rows, artifacts = ma.run_jobs(self.config(), self.root)
        self.assertEqual(rows[0]['result']['arcs'], self.arcs)
        self.assertFalse(rows[0]['spectral_inputs_read'])
        block = rows[1]['q']['0.25']
        self.assertTrue(rows[1]['controls']['passed'])
        self.assertTrue(block['permutation_check']['passed'])
        self.assertIn(block['finite_change']['status'], ('computed', 'unavailable'))
        self.assertEqual(rows[1]['code_sha256'], ma.code_sha256())
        self.assertEqual(rows[1]['spectrum_code_sha256'], ms.code_sha256())
        self.assertTrue(all(name.startswith(('tables/', 'modes/')) for name in artifacts))
        vec = np.load(__import__('io').BytesIO(artifacts[block['a']['artifacts']['cluster_vectors_npy']]))
        self.assertEqual(vec.shape[0], len(self.nodes))
        again, _ = ma.run_jobs(self.config(), self.root)
        self.assertEqual(json.dumps(rows, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_selection_mismatch_and_config_refusals(self):
        with self.assertRaisesRegex(ValueError, 'selection-arcs-mismatch'):
            ma.run_jobs(self.config(ref_index=1, arcs_index=0), self.root)
        bad = self.config()
        bad['spectrum_config'] = {'solver': 'sparse'}
        with self.assertRaisesRegex(ValueError, 'requires-dense'):
            ma.validate_jobs_config(bad)
        bad = self.config()
        bad['jobs'][1]['control'] = 'whatever'
        with self.assertRaisesRegex(ValueError, 'control'):
            ma.validate_jobs_config(bad)
        bad = self.config()
        bad['jobs'][1]['bundle_b'] = '../x.json'
        with self.assertRaisesRegex(ValueError, 'bundle-path'):
            ma.validate_jobs_config(bad)
        bad = self.config()
        bad['jobs'] = bad['jobs'][1:]
        with self.assertRaisesRegex(ValueError, 'selection-ref'):
            ma.validate_jobs_config(bad)


if __name__ == '__main__':
    unittest.main()
