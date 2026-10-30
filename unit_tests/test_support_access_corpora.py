from __future__ import annotations
import copy
import json
from pathlib import Path
import tempfile
import unittest
from retrieval_arena.graph import support_access_corpora as sc
from retrieval_arena.graph import support_access as sa
from retrieval_arena.graph.support_access_rank import cache_key
from retrieval_arena.candidate_retrieval import canonical,regular_inventory,sha256_bytes

POLICY=dict(max_hops=1,neighbor_budget=20,candidate_budget=40,final_top_k=4,distance_penalty=.02,directed=True)

class CorpusSupportTests(unittest.TestCase):
    def setUp(self):
        self.nodes=list('abcdefgh')
        self.scores=[[d,1-i*.1] for i,d in enumerate(self.nodes)]
        self.edges=[['a','e']]
    def pred(self): return sc.generate(self.nodes,self.edges,self.scores,POLICY)
    def row(self,s,q,t,text=None):
        return dict(surface_id=s,corpus_id=s,query_id=q,query_text_group=text or q,
            **sc.evaluate(self.nodes,self.edges,self.pred(),t,POLICY,[1,2,4,8]))
    def test_generation_new_page_and_exact_historical_consistency(self):
        p=self.pred()
        self.assertEqual(p['B4'],list('abcd'))
        self.assertEqual(p['G4']['selected_ids'],list('ae'))
        nodes=self.nodes+['new']
        p=sc.generate(nodes,self.edges,self.scores+[['new',2]],POLICY)
        self.assertEqual(p['B1'],['new'])
    def test_ties_complete_scores_and_namespace_rejection(self):
        tied=[[d,.5] for d in reversed(self.nodes)]
        self.assertEqual(sc.generate(self.nodes,[],tied,POLICY)['B4'],list('abcd'))
        for scores in [self.scores[:-1],self.scores+self.scores[:1],self.scores+[['outside',.4]],self.scores[:-1]+[['h',float('nan')]]]:
            with self.assertRaises(ValueError): sc.generate(self.nodes,self.edges,scores,POLICY)
        with self.assertRaises(ValueError): sc.generate(self.nodes,[['a','outside']],self.scores,POLICY)
        with self.assertRaises(ValueError): sc.evaluate(self.nodes,self.edges,self.pred(),['outside'],POLICY,[1])
    def test_changed_content_and_policy_change_cache_identity(self):
        a=cache_key('old',{'model':'fixed','decoding':'utf8'})
        self.assertNotEqual(a,cache_key('rewritten',{'model':'fixed','decoding':'utf8'}))
        self.assertNotEqual(a,cache_key('old',{'model':'other','decoding':'utf8'}))
        self.assertEqual(a,cache_key('old',{'decoding':'utf8','model':'fixed'}))
    def test_empty_targets_and_derived_credit_boundary(self):
        r=self.row('A','q',[])
        self.assertTrue(r['no_known_positive_in_arm'])
        self.assertEqual(r['failure']['stage'],'unavailable')
        self.assertEqual(r['hits']['G4'],0)
        with self.assertRaises(ValueError): sc.masked_aggregate([r],['q'])
        self.assertEqual(sc.masked_aggregate([r],['q'],all_panel=True)['queries'],1)
        with self.assertRaises(ValueError): sc.evaluate(self.nodes,self.edges,self.pred(),['e'],POLICY,[1],credit_kind='derived')
    def test_all_cells_and_actual_count(self):
        rows=[self.row('A',str(i),[t]) for i,t in enumerate('aebf')]
        self.assertEqual([r['cell'] for r in rows],list(sa.CELLS))
        self.assertEqual(rows[1]['G4_minus_B4'],1)
        self.assertEqual(rows[2]['G4_minus_Bm'],-1)
        self.assertEqual(rows[0]['selected_counts']['G4'],2)
    def test_joint_interaction_masks_and_label_only_surface(self):
        qs=[{'query_id':'q1','question':'duplicate'},{'query_id':'q2','question':'duplicate'},{'query_id':'q3','question':'other'}]
        rows=[]
        for s,ts in [('O',[['b'],['e'],['f']]),('LT',[['e'],['e'],[]]),('S',[['b'],['a'],['f']])]:
            rows += [self.row(s,q['query_id'],t,q['question']) for q,t in zip(qs,ts)]
        pops={'common':['q1','q2'],'primary_surfaces':['O','LT']}
        a=sc.analyze(rows,qs,pops,[['LT','O'],['S','O']],draws=100,seed=5)
        b=sc.analyze(list(reversed(rows)),qs,pops,[['LT','O'],['S','O']],draws=100,seed=5)
        self.assertEqual(a,b)
        self.assertEqual(a['contrasts']['common']['LT-O']['graph_increment']['estimate'],1)
        # Resampling keeps both duplicate IDs together and masks missing targets inside draws.
        ci=a['contrasts']['common']['LT-O']['graph_increment']
        self.assertEqual(ci['interval_95'],[1,1]); self.assertGreater(ci['empty_draws'],0)
        self.assertEqual(a['contrasts']['all_panel']['LT-O']['graph_increment']['estimate'],2/3)
        self.assertEqual(a['transitions']['S-O'][1]['a_hits']['G4'],1)
        self.assertEqual(a['transitions']['S-O'][1]['b_hits']['G4'],1)
        self.assertEqual(a['transitions']['S-O'][1]['interaction'],-1)
        self.assertEqual(a['arm_eligible']['LT']['queries'],2)
        self.assertEqual(a['transition_tables']['LT-O']['common']['delta_matrix']['-1']['1'],1)
        self.assertEqual(a['transition_tables']['LT-O']['common']['queries'],2)
        with self.assertRaises(ValueError): sc.analyze(rows,qs,dict(pops,common=['q1']),[['LT','O']],draws=2,seed=1)
        with self.assertRaises(ValueError): sc.analyze(rows+rows[:1],qs,pops,[['LT','O']],draws=2,seed=1)
    def test_parity_checks_score_tolerances_and_exact_ids(self):
        p=self.pred(); s={'q':self.scores}
        self.assertEqual(sc.parity(self.nodes,{'q':p},{'q':p},s,s,atol=1e-7,rtol=1e-6)['status'],'qualified')
        altered=copy.deepcopy(p); altered['B4']=list('abce')
        self.assertEqual(sc.parity(self.nodes,{'q':altered},{'q':p},s,s,atol=1e-7,rtol=1e-6)['status'],'failed')
        shifted=copy.deepcopy(s); shifted['q'][0][1]+=.01
        self.assertEqual(sc.parity(self.nodes,{'q':p},{'q':p},shifted,s,atol=1e-7,rtol=1e-6)['status'],'failed')
    def test_small_operator_generation_to_evaluation(self):
        from containers.verification import support_access_corpora as op
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'input'; out=Path(tmp)/'output'; inp.mkdir(); out.mkdir()
            def write(name,v): (inp/name).write_bytes(canonical(v)+b'\n')
            def jl(name,vs): (inp/name).write_bytes(b''.join(canonical(v)+b'\n' for v in vs))
            qs=[{'query_id':str(i),'question':str(i)} for i in range(4)]
            predictions=[]; targets=[]; historical=[]; scores=[]
            for s in ['O','LT']:
                corpus=inp/'corpora'/s/'corpus'; corpus.mkdir(parents=True)
                for d in self.nodes: (corpus/(d+'.md')).write_text('# '+d,encoding='utf8')
                for i,t in enumerate('aebf'):
                    p=self.pred(); predictions.append(dict(corpus_id=s,query_id=str(i),query_text_group=str(i),**p))
                    targets.append(dict(surface_id=s,corpus_id=s,query_id=str(i),known_target_ids=[t],credit_kind='direct'))
                    scores.append(dict(corpus_id=s,query_id=str(i),scores=self.scores))
                    if s=='O': historical.append(self.row('O',str(i),[t]))
            targets += [dict(t,surface_id='O_C2') for t in targets if t['surface_id']=='O']
            cfg={'schema_version':'retrieval_arena.support_access_evaluation.v2','surfaces':{'O':'O','LT':'LT','O_C2':'O'},'primary_surfaces':['O','LT'],
                'pairs':[['LT','O']],'bootstrap_draws':20,'bootstrap_seed':1,'semantic_depths':[1,2,4,8],'traversal':POLICY,'max_output_bytes':2**24,'max_runtime_seconds':60}
            write('config.json',cfg); write('populations.json',{'common':[q['query_id'] for q in qs]})
            write('graphs.json',{s:dict(nodes=self.nodes,edges=self.edges) for s in ['O','LT']})
            write('parity.json',{'status':'qualified'}); jl('questions.jsonl',qs); jl('predictions.jsonl',predictions)
            jl('targets.jsonl',targets); jl('scores.jsonl',scores); jl('historical_rows.jsonl',historical)
            inv=regular_inventory(inp); write('operator-request.json',dict(schema_version='retrieval_arena.support_access_operator_request.v2',command='compare-support-access-corpora',input_inventory=inv,input_inventory_sha256=sha256_bytes(canonical(inv))))
            result=op.run(inp,out)
            self.assertEqual(result['populations']['common']['O']['paired_cells'],dict.fromkeys(sa.CELLS,1))
            self.assertEqual(result['contrasts']['common']['LT-O']['graph_increment']['estimate'],0)
            receipt=json.loads((out/'operator_receipt.v2.json').read_bytes()); saved=receipt['receipt_sha256']; receipt['receipt_sha256']='0'*64
            self.assertEqual(saved,sha256_bytes(canonical(receipt)))

if __name__=='__main__': unittest.main()
