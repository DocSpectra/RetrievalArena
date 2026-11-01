import unittest
from dataclasses import replace
import numpy as np
from retrieval_arena.graph import block_phase as bp

class BlockPhase(unittest.TestCase):
    def setUp(self):
        self.nodes=list('012345'); self.edges=[[str(i),str(i+1)] for i in range(5)]

    def test_cycle_witness_and_discriminating_order(self):
        for q in [.25,.1]:
            for target in [2,5]:
                result=bp.compare(self.nodes,self.edges,[['0',str(target)]],q,'block')
                n=target+1; flux=np.angle(np.exp(2j*np.pi*q*(target-1)))
                self.assertAlmostEqual(result['cost'],n*(1-np.cos(flux/n)),places=8)
                self.assertEqual(result['status'],'finite_estimate')

    def test_true_translated_tie(self):
        a=bp.compare(self.nodes,self.edges,[['0','2']],.25,'block')
        b=bp.compare(self.nodes,self.edges,[['3','5']],.25,'block')
        self.assertAlmostEqual(a['cost'],b['cost'],places=8)

    def test_articulation_cancellation_keeps_isolate(self):
        nodes=list('012345678'); edges=[['0','1'],['1','2'],['2','0']]+[[str(i),str(i+1)] for i in range(2,7)]
        cache={};r=bp.compare(nodes,edges,[['2','4']],.25,'block',cache=cache)
        self.assertEqual(r['largest_affected_support'],3)
        self.assertEqual(r['all_nodes_accounted'],9)
        self.assertTrue(all('0' not in f['support_nodes'] and '8' not in f['support_nodes'] for f in cache.values()))
        self.assertAlmostEqual(r['cost'],3*(1-np.cos(np.pi/6)),places=8)

    def test_partition_edges_exactly_once_with_reciprocal(self):
        edges=self.edges+[['1','0'],['2','0']]
        parts=bp.partitions(self.nodes+['x'],edges,'block')
        arcs=[e for _,es in parts for e in es]
        self.assertEqual(sorted(arcs),sorted(map(tuple,edges)))
        self.assertIn((['x'],[]),parts)

    def test_joint_additions_merge_blocks(self):
        r=bp.compare(self.nodes,self.edges,[['0','2'],['1','3']],.25,'block')
        self.assertEqual(r['largest_affected_support'],4)
        one=bp.compare(self.nodes,self.edges,[['0','2']],.25,'block')['cost']
        self.assertGreater(abs(r['cost']-2*one),1e-4)

    def test_block_matches_component_small_witness(self):
        edges=self.edges+[['2','0']]
        for q in [.25,.1]:
            a=bp.compare(self.nodes,edges,[['2','4']],q,'component')
            b=bp.compare(self.nodes,edges,[['2','4']],q,'block')
            self.assertAlmostEqual(a['cost'],b['cost'],places=8)

    def test_arbitrary_articulation_rotation_preserves_energy(self):
        edges=[['0','1'],['1','2'],['2','0'],['2','3'],['3','4'],['4','2']]
        phi=np.array([.1,.3,.8,-.4,1.2]); nodes=list('01234')
        _,terms=bp.lo.terms(nodes,edges,.25)
        total=bp.lo.energy_gradient(phi,terms)[0]; subtotal=0.
        for ns,es in bp.partitions(nodes,edges,'block'):
            p=np.array([phi[nodes.index(n)] for n in ns])+2.7
            subtotal+=bp.lo.energy_gradient(p,bp.lo.terms(ns,es,.25)[1])[0]
        self.assertAlmostEqual(total,subtotal,places=12)

    def test_tree_gauge_reversal_and_qzero(self):
        add=[['0','2']]
        a=bp.fixed_tree_cost(self.nodes,self.edges,add,.25)
        b=bp.fixed_tree_cost(self.nodes,[e[::-1] for e in self.edges],[e[::-1] for e in add],.25)
        self.assertAlmostEqual(a['cost'],b['cost'])
        self.assertEqual(bp.fixed_tree_cost(self.nodes,self.edges,add,0.)['cost'],0.)

    def test_cache_binds_charge_and_seed(self):
        cache={};p=bp.lo.Policy()
        a,_=bp.cached_solve(self.nodes,self.edges,.25,p,cache)
        b,_=bp.cached_solve(self.nodes,self.edges,.1,p,cache)
        c,_=bp.cached_solve(self.nodes,self.edges,.25,replace(p,seed=3502),cache)
        self.assertEqual(len({a,b,c}),3)

    def test_eligibility_refusals(self):
        for addition in [[['1','0']],[['0','x']],[['0','1']]]:
            with self.assertRaises(ValueError):bp.compare(self.nodes,self.edges,addition,.25,'block')
        with self.assertRaises(ValueError):bp.compare(self.nodes+['x'],self.edges,[['0','x']],.25,'block')

    def test_order_abstention_and_tie(self):
        row=lambda a,b:[{'status':'finite_estimate','cost':a},{'status':'finite_estimate','cost':b}]
        self.assertEqual(bp.ordering([row(0,0),row(1,1)])['status'],'resolved_finite_order')
        self.assertEqual(bp.ordering([row(0,2),row(1,1)])['status'],'unresolved')
        self.assertEqual(bp.ordering([row(0,0),row(0,0)])['status'],'tie')
        self.assertEqual(bp.ordering([[{'status':'negative_marginal','cost':-1}]])['status'],'unscorable_pool')

if __name__=='__main__':unittest.main()
