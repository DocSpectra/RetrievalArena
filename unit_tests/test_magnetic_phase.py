import unittest
from dataclasses import replace
import numpy as np
from retrieval_arena.graph.magnetic_phase import PhaseConfig, magnetic_matrix, measure, measure_subspace, relative_phase_counts

class MagneticPhaseTests(unittest.TestCase):
    def test_empty_edgeless_and_reciprocal(self):
        for ns,es in (([],[]),(['a','b'],[])):
            self.assertIsNone(measure(ns,es)['value'])
        r=measure(['a','b'],[('a','b'),('b','a')])
        self.assertEqual(r['value'],0)
        self.assertEqual(r['per_mode'][0]['neutral'],2)

    def test_chain_permutation_edge_order_and_phase_rotations(self):
        ns=list('abcd'); es=list(zip(ns,ns[1:])); cfg=PhaseConfig(modes=2)
        l=magnetic_matrix(ns,es); e,v=np.linalg.eigh(l); r=measure(ns,es,cfg)
        self.assertEqual(r['status'],'computed')
        rng=np.random.default_rng(9)
        for _ in range(12):
            p=rng.permutation(len(ns)); nn=[ns[i] for i in p]
            np.testing.assert_array_equal(magnetic_matrix(nn,es),l[np.ix_(p,p)])
            self.assertAlmostEqual(measure(nn,list(reversed(es)),cfg)['value'],r['value'])
            rotated=v*np.exp(1j*rng.uniform(-np.pi,np.pi,len(ns)))
            rr=measure(ns,es,cfg,eigenpairs=(e,rotated))
            self.assertEqual(rr['value'],r['value'])
        self.assertEqual(measure(ns,es+es+[('a','a'),('unknown','a')],cfg)['graph_sha256'],r['graph_sha256'])

    def test_repeated_eigenspace_rotations_unavailable(self):
        ns=list('abcd'); es=[('a','b'),('c','d')]
        e,v=np.linalg.eigh(magnetic_matrix(ns,es))
        r=measure(ns,es)
        self.assertIn('selected_mode_or_boundary_unresolved',r['validity_reasons'])
        # Valid unitary mixing of repeated zero and repeated unit eigenvalues.
        u=np.array([[1,1j],[1j,1]])/np.sqrt(2)
        vv=v.copy(); vv[:,:2]=v[:,:2]@u; vv[:,2:]=v[:,2:]@u
        rr=measure(ns,es,eigenpairs=(e,vv))
        self.assertIsNone(rr['value']); self.assertEqual(rr['validity_reasons'],r['validity_reasons'])

    def test_cycle_boundary_and_near_degeneracy(self):
        ns=list('abcd'); es=list(zip(ns,ns[1:]+ns[:1]))
        self.assertIsNone(measure(ns,es,PhaseConfig(q=0,modes=1))['value'])
        self.assertIsNone(measure(ns,es,PhaseConfig(q=1e-12,modes=1))['value'])
        self.assertEqual(measure(ns,es,PhaseConfig(q=.13,modes=1))['status'],'computed')

    def test_q_and_global_edge_reversal(self):
        ns=list('abcd'); es=list(zip(ns,ns[1:])); cfg=PhaseConfig(modes=2)
        r=measure(ns,es,cfg)
        self.assertAlmostEqual(measure(ns,[(t,s) for s,t in es],cfg)['value'],r['value'])
        self.assertAlmostEqual(measure(ns,es,replace(cfg,q=-cfg.q))['value'],r['value'])
        self.assertEqual(measure(ns,es,replace(cfg,q=0))['value'],0)

    def test_amplitudes_neutral_and_pi_boundary(self):
        ns=list('abcd'); es=[('a','b'),('b','c'),('c','d')]
        v=np.array([1,1,np.exp(1j*(np.pi-1e-9)),0],complex)[:,None]
        counts,signs=relative_phase_counts(ns,es,v,PhaseConfig())
        self.assertEqual((counts[0]['neutral'],counts[0]['excluded']),(2,1))
        self.assertEqual(signs,[[0,0,0]])
        r=measure(list('abc'),[('a','b'),('b','c')],PhaseConfig(q=0))
        self.assertIn('insufficient_amplitude_coverage',r['validity_reasons'])

    def test_invalid_eigenpairs_and_inputs(self):
        ns=list('ab'); es=[('a','b')]; e,v=np.linalg.eigh(magnetic_matrix(ns,es))
        self.assertIsNone(measure(ns,es,eigenpairs=(e+1,v))['value'])
        with self.assertRaises(ValueError): measure(['a','a'],es)
        with self.assertRaises(ValueError): measure(ns,es,PhaseConfig(phase_absolute=-1))

    def test_subspace_hand_calculation_and_bound(self):
        ns=list('ab'); cfg=PhaseConfig(modes=1)
        self.assertAlmostEqual(measure_subspace(ns,[('a','b')],cfg)['value'],1)
        self.assertAlmostEqual(measure_subspace(ns,[('a','b')],replace(cfg,q=0))['value'],0)
        self.assertEqual(measure_subspace(ns,[('a','b'),('b','a')],cfg)['value'],0)
        self.assertIsNone(measure_subspace([],[])['value'])
        self.assertIsNone(measure_subspace(ns,[])['value'])

    def test_subspace_complete_cluster_unitary_and_near_degeneracy(self):
        ns=list('abcd'); es=[('a','b'),('c','d')]; cfg=PhaseConfig(modes=1)
        e,v=np.linalg.eigh(magnetic_matrix(ns,es)); r=measure_subspace(ns,es,cfg)
        self.assertEqual(r['actual_modes'],2); self.assertAlmostEqual(r['value'],1)
        u=np.array([[1,1j],[1j,1]])/np.sqrt(2); vv=v.copy(); vv[:,:2]=v[:,:2]@u; vv[:,2:]=v[:,2:]@u
        self.assertAlmostEqual(measure_subspace(ns,es,cfg,eigenpairs=(e,vv))['value'],r['value'])
        cycle=list(zip(ns,ns[1:]+ns[:1]))
        r=measure_subspace(ns,cycle,PhaseConfig(modes=2,q=1e-12))
        self.assertEqual(r['actual_modes'],3)
        self.assertLess(r['value'],1e-10)

    def test_subspace_permutations_rotations_reversal_and_sweep(self):
        ns=list('abcde'); es=list(zip(ns,ns[1:])); cfg=PhaseConfig(modes=2)
        l=magnetic_matrix(ns,es); e,v=np.linalg.eigh(l); r=measure_subspace(ns,es,cfg)
        rng=np.random.default_rng(91)
        for _ in range(12):
            p=rng.permutation(len(ns)); nn=[ns[i] for i in p]
            rr=measure_subspace(nn,list(reversed(es)),cfg)
            self.assertAlmostEqual(rr['value'],r['value'])
            vv=v*np.exp(1j*rng.uniform(-np.pi,np.pi,len(ns)))
            self.assertAlmostEqual(measure_subspace(ns,es,cfg,eigenpairs=(e,vv))['value'],r['value'])
        for cc in (replace(cfg,q=-.25),replace(cfg,gap_relative=1e-7),replace(cfg,gap_relative=1e-9)):
            self.assertAlmostEqual(measure_subspace(ns,es,cc)['value'],r['value'])
        self.assertAlmostEqual(measure_subspace(ns,[(t,s) for s,t in es],cfg)['value'],r['value'])
        self.assertEqual(measure_subspace(ns,es,replace(cfg,q=0))['value'],0)

    def test_subspace_full_spectrum_and_bad_pairs(self):
        ns=list('abc'); es=[('a','b'),('b','c')]
        self.assertLess(measure_subspace(ns,es)['value'],1e-14)
        e,v=np.linalg.eigh(magnetic_matrix(ns,es))
        self.assertIsNone(measure_subspace(ns,es,eigenpairs=(e+1,v))['value'])

if __name__=='__main__': unittest.main()
