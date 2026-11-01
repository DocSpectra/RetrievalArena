import unittest
from retrieval_arena.embedded_link_selection import feasible_sets, select

class EmbeddedSelection(unittest.TestCase):
    def pool(self):
        return [{'proposal_id':i,'source_id':s,'target_id':t,'semantic_score':score,
                 'semantic_eligible':True,'conflicts':conflicts} for i,s,t,score,conflicts in
                [('a','s1','t1',6,['b','c']),('b','s2','t2',5,['a']),('c','s3','t3',5,['a'])]]

    def test_shared_budget_avoids_semantic_greedy_dead_end(self):
        r=select(self.pool(),'S'); self.assertEqual(r['common_feasible_budget'],2)
        self.assertEqual(r['selected'],['b','c'])

    def test_source_files_conflict_and_ids_break_semantic_ties(self):
        pool=self.pool()[1:]; pool[1]['source_id']=pool[0]['source_id']
        self.assertEqual(select(pool,'S')['selected'],['b'])

    def test_magnetic_recomputes_prefix_and_keeps_budget(self):
        calls=[]
        def score(prefix):
            calls.append(prefix); return {'cost':0. if prefix[0]=='c' else 1.,'uncertainty':1e-6,'qualified':True}
        r=select(self.pool(),'M',score)
        self.assertEqual(r['selected'],['c','b']); self.assertIn(('c','b'),calls)
        self.assertEqual(r['common_feasible_budget'],select(self.pool(),'S')['common_feasible_budget'])

    def test_exact_tie_uses_semantic_rank_then_id(self):
        r=select(self.pool(),'M',lambda _: {'cost':0.,'uncertainty':1e-6,'qualified':True})
        self.assertEqual(r['selected'],['b','c'])

    def test_unresolved_and_unqualified_never_fallback(self):
        for fn,reason in [(lambda p:{'cost':0. if p[0]=='b' else .1,'uncertainty':1.,'qualified':True},'unresolved'),
                          (lambda p:{'cost':0.,'uncertainty':0.,'qualified':False},'unqualified')]:
            with self.assertRaisesRegex(ValueError,reason): select(self.pool(),'M',fn)

    def test_zero_pool_and_semantic_ineligibility(self):
        self.assertEqual(select([],'S')['common_feasible_budget'],0)
        p=self.pool(); p[0]['semantic_eligible']=False
        with self.assertRaisesRegex(ValueError,'not-semantically'): feasible_sets(p)

    def test_shortlist_three_fixed_and_no_semantic_rescore(self):
        pool=[{'proposal_id':str(i),'source_id':str(i),'target_id':'t','semantic_score':5,
               'semantic_eligible':True,'conflicts':[]} for i in range(5)]
        r=select(pool,'M',lambda p:{'cost':0.,'uncertainty':1e-6,'qualified':True})
        self.assertTrue(all(len(t['choices'])<=3 for t in r['trace']))
        self.assertEqual(r['selected'],['0','1','2','3'])
        with self.assertRaisesRegex(ValueError,'selection-policy'): select(pool,'M',shortlist=4)

    def test_reciprocal_and_duplicate_pool_cannot_increase_budget(self):
        p=self.pool()[1:];p[1]['source_id']=p[0]['target_id'];p[1]['target_id']=p[0]['source_id']
        with self.assertRaisesRegex(ValueError,'pool-reciprocal'): select(p,'S')

    def test_nonfinite_callback_rejected(self):
        with self.assertRaisesRegex(ValueError,'unqualified'):
            select(self.pool(),'M',lambda p:{'cost':float('nan'),'uncertainty':0.,'qualified':True})

if __name__=='__main__': unittest.main()
