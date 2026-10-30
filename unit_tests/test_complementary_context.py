import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
from retrieval_arena.graph import complementary_context as cc
from retrieval_arena.graph import page_representations as pr
from retrieval_arena.graph import support_access_corpora as sc
from retrieval_arena.graph.magnetic_spectrum import digest
from retrieval_arena.candidate_retrieval import canonical, regular_inventory
from containers.verification.complementary_context import run

CFG={'schema_version':'retrieval_arena.complementary_context_config.v1','charges':[0.,.1,.25],
     'primary_surface':'O_C2','arm_surfaces':['O','LT','R0'],
     'surface_pairs':[['LT','O'],['R0','O'],['LT','R0'],['S','O'],['O','O_C2']],
     't':1.,'tolerance':1e-10,'rank_offset':60,'budget':4,
     'traversal':{'max_hops':1,'neighbor_budget':20,'candidate_budget':40,'final_top_k':4,'distance_penalty':.02,'directed':True},
     'bootstrap_draws':20,'bootstrap_seed':32061030,'max_runtime_seconds':60,'max_output_bytes':10000000}

class ComplementaryTests(unittest.TestCase):
    def test_hand_rank_fusion(self):
        nodes=list('abcde'); h=np.eye(5,dtype=complex)
        h[0,1:]=[.1,.3,.2,0]; h[1:,0]=h[0,1:]
        r=cc.select(nodes,list(zip(nodes,[1.,.9,.8,.7,.6])),h)
        self.assertEqual(r['selected_ids'],list('acbd'))
        detail=r['selection_scores'][1]
        self.assertAlmostEqual(detail['fused_score'],1/62+1/61)
        self.assertEqual(detail['semantic_rank'],2)
        self.assertEqual(detail['affinity_rank'],1)
        self.assertEqual(r['positive_affinity_candidates'],3)

    def test_zero_disconnected_and_ties(self):
        nodes=list('abcde'); pairs=[(d,1.) for d in reversed(nodes)]
        r=cc.select(nodes,pairs,np.eye(5))
        self.assertEqual(r['selected_ids'],list('abcd'))
        self.assertEqual(r['positive_affinity_candidates'],0)
        self.assertIsNone(r['selection_scores'][1]['affinity_rank'])
        h=np.eye(5); h[0,4]=h[4,0]=.1
        self.assertEqual(cc.select(nodes,pairs,h)['selected_ids'],list('aebc'))

    def test_normalization_and_invalid(self):
        h=np.array([[4,1j],[-1j,9]])
        self.assertAlmostEqual(cc.normalized_affinity(h,0)[1],1/6)
        for bad in [np.array([[0,0],[0,1]]),np.array([[1j,0],[0,1]]),np.array([[1,np.nan],[0,1]]),np.array([[1,2],[2,1]])]:
            with self.assertRaises(ValueError): cc.normalized_affinity(bad,0)

    def test_reversal_permutation_ordinary_parity(self):
        nodes=list('abcde'); edges=[('a','b'),('b','c'),('c','a'),('b','d')]
        pairs=list(zip(nodes,[1.,.5,.4,.3,.2]))
        kernels={q:pr.heat_kernel(nodes,edges,q,1.)[0] for q in [0.,.1,.25]}
        forward=cc.select(nodes,pairs,kernels[.25])
        reverse=pr.heat_kernel(nodes,[(b,a) for a,b in edges],.25,1.)[0]
        np.testing.assert_allclose(cc.normalized_affinity(kernels[.25],0),cc.normalized_affinity(reverse,0),atol=1e-14)
        self.assertEqual(forward['selected_ids'],cc.select(nodes,pairs,reverse)['selected_ids'])
        order=[4,2,0,3,1]; perm=[nodes[i] for i in order]
        self.assertEqual(forward['selected_ids'],cc.select(perm,pairs,kernels[.25][np.ix_(order,order)])['selected_ids'])
        g=cc.generate(nodes,edges,pairs,{q:kernels[0.] for q in kernels},CFG)
        self.assertEqual(g['methods']['Q0'],g['methods']['M25'])
        self.assertEqual(g['methods']['Q0'],g['methods']['M10'])
        self.assertEqual(g['methods']['Q0']['selected_ids'][0],'a')
        self.assertEqual(len(set(g['methods']['M25']['selected_ids'])),4)

    def test_missing_duplicate_extra_nonfinite_scores(self):
        nodes=list('abcde'); good=list(zip(nodes,[1.,.9,.8,.7,.6]))
        bads=[good[:-1],good+[good[0]],good+[('extra',.2)],[(d,float('nan') if d=='e' else s) for d,s in good]]
        for pairs in bads:
            with self.assertRaises(ValueError): cc.select(nodes,pairs,np.eye(5))

    def test_two_hop_chain_and_branch_caps(self):
        nodes=list('abcde'); scores=list(zip(nodes,[1.,.2,.8,.7,.6]))
        p=cc.generate(nodes,[('a','b'),('b','c'),('c','d')],scores,{q:np.eye(5) for q in [0.,.1,.25]},CFG)
        self.assertEqual(p['methods']['G4']['candidate_ids'],list('ab'))
        self.assertEqual(p['methods']['G4h2']['candidate_ids'],list('abc'))
        self.assertEqual(p['methods']['G4h2']['selected_ids'],list('acb'))
        nodes=['a']+[f'b{i:02}' for i in range(25)]+[f'c{i:02}' for i in range(30)]
        edges=[('a',n) for n in nodes if n.startswith('b')]+[('b00',n) for n in nodes if n.startswith('c')]
        p=cc.generate(nodes,edges,[(n,1. if n=='a' else .1) for n in nodes],{q:np.eye(len(nodes)) for q in [0.,.1,.25]},CFG)
        self.assertEqual(p['methods']['G4']['candidate_count'],21)
        self.assertEqual(p['methods']['G4h2']['candidate_count'],40)
        self.assertNotIn('b20',p['methods']['G4h2']['candidate_ids'])
        self.assertEqual(p['methods']['G4h2']['distances']['c18'],2)
        self.assertNotIn('c19',p['methods']['G4h2']['candidate_ids'])

    def test_requirements_joint_alternative_unknown(self):
        self.assertEqual(cc.requirement_coverage([],None)['status'],'untested')
        units=[[['a','b'],['c']],[['d']]]
        self.assertEqual(cc.requirement_coverage(['a'],units)['covered'],0)
        self.assertEqual(cc.requirement_coverage(['a','b'],units)['covered'],1)
        self.assertTrue(cc.requirement_coverage(['c','d'],units)['complete'])
        with self.assertRaises(ValueError): cc.requirement_coverage([],[[[]]])

    def test_all_paired_cells_exclusives_marginals_routes(self):
        nodes=list('abcdef'); kernels={q:np.eye(6) for q in [0.,.1,.25]}
        p=cc.generate(nodes,[('a','b'),('b','c'),('d','c')],list(zip(nodes,[1.,.9,.8,.7,.6,.5])),kernels,CFG)
        def ev(targets): return cc.evaluate(nodes,[('a','b'),('b','c'),('d','c')],p,targets,{d:10 for d in nodes},{'c':1})
        self.assertEqual(ev(['c'])['comparisons']['B4-G4']['cell'],'a_only')
        self.assertEqual(ev(['a'])['comparisons']['B4-G4']['cell'],'both_hit')
        self.assertEqual(ev(['f'])['comparisons']['B4-G4']['cell'],'neither_hit')
        p['methods']['G4']['selected_ids']=['f']
        self.assertEqual(ev(['f'])['comparisons']['B4-G4']['cell'],'b_only')
        r=ev(['a','c']); self.assertEqual(r['methods']['B4']['additional_hit_beyond_seed'],0)
        self.assertEqual(r['methods']['B4']['new_positive_identity_count_beyond_seed'],1)
        self.assertEqual(r['methods']['B4']['route_strata'],{'within_one_hop':2,'longer_directed_route':1,'wrong_direction_weak':1})
        self.assertEqual(r['methods']['B4']['unjudged_selected_ids'],list('abd'))

    def test_small_operator_end_to_end_and_leak_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); inp=root/'input'; inp.mkdir(); out=root/'output'; out.mkdir(); (out/'.retrieval-arena-output-owner').touch()
            def write(p,v): p.write_bytes(canonical(v)+b'\n')
            def jsonl(p,vs): p.write_bytes(b''.join(canonical(v)+b'\n' for v in vs))
            def req(command):
                p=inp/'operator-request.json'
                if p.exists(): p.unlink()
                inv=regular_inventory(inp)
                write(p,{'schema_version':'retrieval_arena.complementary_context_request.v1','command':command,'input_inventory':inv,'input_inventory_sha256':digest(inv)})
            nodes=list('abcde'); edges=[['a','b'],['b','c']]; graphs={c:{'nodes':nodes,'edges':edges} for c in ['O','LT','R0']}
            write(inp/'config.json',CFG); write(inp/'graphs.json',graphs)
            qs=[{'query_id':'q1','question':'same text'},{'query_id':'q2','question':'same text'}]; jsonl(inp/'questions.jsonl',qs)
            scores=[]; old=[]
            for c in graphs:
                path=inp/f'corpora/{c}/corpus'; path.mkdir(parents=True)
                for n in nodes: (path/(n+'.md')).write_text(n)
                for query in qs:
                    pairs=list(zip(nodes,[1.,.2,.9,.8,.7])); scores.append({'corpus_id':c,'query_id':query['query_id'],'scores':pairs})
                    old.append(dict(sc.generate(nodes,edges,pairs,CFG['traversal']),corpus_id=c,query_id=query['query_id']))
            jsonl(inp/'scores.jsonl',scores); jsonl(inp/'historical_predictions.jsonl',old)
            write(inp/'targets.jsonl',[]); req('generate-complementary-context')
            with self.assertRaisesRegex(ValueError,'undeclared-input'): run(inp,out,'generate-complementary-context')
            (inp/'targets.jsonl').unlink(); req('generate-complementary-context')
            summary=run(inp,out,'generate-complementary-context'); self.assertEqual(summary['prediction_rows'],6)
            self.assertEqual(summary['features'],9)
            # The evaluator sees only frozen predictions, corpus/query and judgment files.
            for n in ['scores.jsonl','historical_predictions.jsonl']: (inp/n).unlink()
            for n in ['predictions.jsonl','parity.json']: (inp/n).write_bytes((out/n).read_bytes())
            surfaces={'O':'O','LT':'LT','R0':'R0','S':'O','O_C2':'O'}
            jsonl(inp/'targets.jsonl',[{'surface_id':s,'corpus_id':c,'query_id':q['query_id'],'known_target_ids':['c'],'credit_kind':'direct'} for s,c in surfaces.items() for q in qs])
            jsonl(inp/'judgments.jsonl',[{'surface_id':s,'query_id':q['query_id'],'support_target_id':'c','relevance':1} for s in surfaces for q in qs])
            write(inp/'populations.json',{'common':['q1','q2']}); write(inp/'packet_sources.json',{'q1':'source','q2':'source'})
            req('evaluate-complementary-context'); evout=root/'evaluation'; evout.mkdir(); (evout/'.retrieval-arena-output-owner').touch()
            summary=run(inp,evout,'evaluate-complementary-context')
            self.assertEqual(summary['bootstrap']['exact_text_groups'],1)
            self.assertEqual(summary['primary']['methods']['B4']['hits'],2)
            self.assertEqual(summary['primary']['comparisons']['B4-G4']['cells']['a_only'],2)
            self.assertEqual(summary['primary']['comparisons']['B4-G4']['interval']['interval_95'],[1.,1.])
            self.assertEqual(summary['packet']['judgments_recorded'],0)
            self.assertEqual(summary['cross_arm']['common']['LT-O']['M25-Q0']['estimate'],0.)
            with self.assertRaisesRegex(ValueError,'output-not-fresh'): run(inp,evout,'evaluate-complementary-context')

if __name__=='__main__': unittest.main()
