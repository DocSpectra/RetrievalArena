from dataclasses import replace
import unittest
import numpy as np
from retrieval_arena.graph import local_objective as lo

class LocalObjective(unittest.TestCase):
    def setUp(self):
        self.nodes = list('0123'); self.edges = [['0','1'], ['1','2'], ['2','3']]

    def test_phase_energy_is_hermitian_quadratic_form(self):
        _, ts = lo.terms(self.nodes, self.edges, .25)
        phi = np.array([.2, .7, -.4, .8]); z = np.exp(1j*phi)
        energy, grad = lo.energy_gradient(phi, ts)
        matrix = lo.ms.unnormalized_magnetic_matrix(self.nodes, self.edges, .25)
        self.assertAlmostEqual(energy, float(np.vdot(z, matrix@z).real), places=12)
        for k in range(4):
            step = np.eye(4)[k] * 1e-6
            diff = (lo.energy_gradient(phi+step, ts)[0]-lo.energy_gradient(phi-step, ts)[0])/2e-6
            self.assertAlmostEqual(grad[k], diff, places=8)

    def test_forest_global_optimum_zero(self):
        for q in [.25, .10]:
            fit = lo.solve(self.nodes, self.edges, q)
            self.assertLess(fit['energy_upper_bound'], 1e-10)
            self.assertLess(abs(lo.spectral(self.nodes, self.edges, q)['lambda1']), 1e-10)

    def test_cycle_analytic_and_alias(self):
        for n in [3,4]:
            nodes = [str(i) for i in range(n)]; edges = [[str(i),str((i+1)%n)] for i in range(n)]
            for q in [.25,.10]:
                exact = n*(1-np.cos(np.angle(np.exp(2j*np.pi*q*n))/n))
                fit = lo.solve(nodes, edges, q)
                self.assertAlmostEqual(fit['energy_upper_bound'], exact, places=9)
                self.assertLessEqual(lo.spectral(nodes, edges, q)['phase_energy_lower_bound'], exact+1e-8)

    def test_reversal_and_gauge(self):
        nodes = list('012'); edges = [['0','1'],['1','2'],['2','0']]
        phi = np.array([.2,.5,-.8]); _, ts = lo.terms(nodes,edges,.25)
        _, reverse = lo.terms(nodes,[[t,s] for s,t in edges],.25)
        e = lo.energy_gradient(phi,ts)[0]
        self.assertAlmostEqual(e,lo.energy_gradient(phi+2.7,ts)[0])
        self.assertAlmostEqual(e,lo.energy_gradient(-phi,reverse)[0])

    def test_isolates_and_complete_cluster(self):
        r = lo.spectral(list('012345'),[['0','1'],['2','3']],.25)
        self.assertEqual(r['ground_cluster_size'],4)
        self.assertTrue(r['ground_cluster_complete'])
        self.assertAlmostEqual(sum(r['projector_mass']),1)

    def test_additive_identity_fair_effort_and_monotonicity(self):
        r = lo.compare(self.nodes,self.edges,[['0','2']],.25)
        self.assertTrue(r['energy_decomposition_identity'])
        self.assertTrue(r['computed_additive_monotonicity'])
        self.assertGreater(r['total_energy_change'],0)
        self.assertEqual(len(r['baseline']['starts']),len(r['edited']['starts']))
        self.assertEqual(r['effort']['total_minimizations_per_graph'],18)
        self.assertEqual(len(r['baseline_scout']['starts'])+len(r['baseline']['starts']),18)
        self.assertEqual(len(r['edited_scout']['starts'])+len(r['edited']['starts']),18)
        self.assertAlmostEqual(r['added_weight'],.5)
        self.assertAlmostEqual(r['baseline_weight'],1.5)

    def test_graph_only_eligibility_refusals(self):
        for additions,reason in [([['0','1']],'existing-pair'),([['1','0']],'reciprocal'),
                                 ([['0','x']],'outside'),([['0','2'],['0','2']],'duplicate'),
                                 ([['0','2'],['2','0']],'reciprocal')]:
            with self.assertRaisesRegex(ValueError,reason): lo.eligible_additions(self.nodes,self.edges,additions)
        with self.assertRaisesRegex(ValueError,'different-baseline'):
            lo.eligible_additions(list('012'),[['0','1']],[['1','2']])

    def test_noop_and_duplicate_edges_do_not_change_operator(self):
        _, ts = lo.terms(self.nodes,self.edges+self.edges,.25)
        _, old = lo.terms(self.nodes,self.edges,.25)
        self.assertEqual(lo.energy_gradient(np.zeros(4),ts)[0],lo.energy_gradient(np.zeros(4),old)[0])

    def test_restart_schedule_is_paired_and_reproducible(self):
        a = lo.starts(self.nodes,lo.Policy()); b = lo.starts(self.nodes,lo.Policy())
        np.testing.assert_array_equal(a,b)
        self.assertFalse(np.array_equal(a,lo.starts(self.nodes,replace(lo.Policy(),seed=1))))

    def test_semantics_are_not_in_numeric_score(self):
        from retrieval_arena.local_objective_audit import fixtures
        marked = next(c for c in fixtures() if c['id']=='semantic-shortcut')
        self.assertFalse(marked['synthetic_semantic_support'])
        self.assertTrue(lo.eligible_additions(marked['nodes'],marked['edges'],marked['additions']))

    def test_rank_must_exceed_restart_uncertainty(self):
        rows = [{'total_energy_change':x,'additions':[[str(i),'z']], 'restart_uncertainty':.2,
                 'qualified':True} for i,x in enumerate([.1,.2])]
        self.assertFalse(lo.ranking_verdict(rows)['stable_beyond_restart_uncertainty'])

    def test_fixed_halo_zero_does_not_invent_boundary(self):
        a = lo.spectral(self.nodes,self.edges,.25)
        b = lo.spectral(self.nodes,list(self.edges),.25)
        self.assertEqual(a,b)

    def test_receipts_are_json_safe_and_keep_all_final_phases(self):
        import json
        json.dumps(lo.spectral(self.nodes,self.edges,.25),allow_nan=False)
        fit = lo.solve(self.nodes,self.edges,.25)
        json.dumps(fit,allow_nan=False)
        self.assertTrue(all(len(s['final_phases'])==len(self.nodes) for s in fit['starts']))
        with self.assertRaisesRegex(ValueError,'invalid-numerical'):
            lo.solve(self.nodes,self.edges,.25,replace(lo.Policy(),starts=0))

    def test_highly_localized_complete_ground_cluster(self):
        # Twenty frustrated triangles plus a two-node balanced component.
        # All 62 nodes stay; the exact ground projector lives on the small pair.
        nodes=[f'{i:02d}' for i in range(62)]; edges=[['00','01']]
        for j in range(2,62,3):
            edges += [[f'{j:02d}',f'{j+1:02d}'],[f'{j+1:02d}',f'{j+2:02d}'],[f'{j+2:02d}',f'{j:02d}']]
        for q in [.25,.1]:
            r=lo.spectral(nodes,edges,q)
            self.assertEqual(r['ground_cluster_size'],1)
            self.assertAlmostEqual(r['effective_support'],2.,places=8)
            self.assertAlmostEqual(r['max_node_mass'],.5,places=8)
            self.assertLess(abs(r['lambda1']),1e-9)

    def test_nonglobal_old_assignment_is_not_true_minimum(self):
        # Individual old-edge energies may redistribute; their optimized sum
        # cannot fall below the true baseline optimum.
        fit=lo.solve(self.nodes,self.edges,.25)
        _,ts=lo.terms(self.nodes,self.edges,.25)
        zero_energy=lo.energy_gradient(np.zeros(4),ts)[0]
        self.assertGreater(zero_energy,fit['energy_upper_bound']+1)

if __name__ == '__main__': unittest.main()
