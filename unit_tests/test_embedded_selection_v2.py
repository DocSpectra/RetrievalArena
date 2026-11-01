import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_ledger_v2 as ledger
from retrieval_arena import embedded_demonstrator as demo
from retrieval_arena.embedded_selection_v2 import select_embedded_v2, BoundedBlockScorer, DEFAULT_POLICY
from retrieval_arena.graph import block_phase as bp


def sleepy_worker(*args): time.sleep(10)


class SelectionV2(unittest.TestCase):
    def setUp(self):
        self.graph={'nodes':list('012345'),'edges':[[str(i),str(i+1)] for i in range(5)]}
        self.pool=[self.p('a','0','2'),self.p('b','0','5')]

    def p(self, pid, source, target, score=6, conflicts=()):
        return {'proposal_id':pid,'source_id':source,'target_id':target,
                'semantic_eligible':True,'semantic_score':score,'conflicts':list(conflicts)}

    def numeric(self, prefix):
        pairs={p['proposal_id']:[p['source_id'],p['target_id']] for p in self.pool}
        rows=[bp.compare(self.graph['nodes'],self.graph['edges'],[pairs[p] for p in prefix],.25,'block',
                         bp.lo.Policy(seed=seed)) for seed in [3501,3502]]
        return {'status':'finite_estimate','schedule_costs':[r['cost'] for r in rows],'schedules':rows}

    def run_select(self, **kw):
        return select_embedded_v2(self.pool,self.graph,{'maximum':kw.pop('maximum',1)},**kw)

    def test_analytic_witness_and_q0(self):
        s=self.run_select(arm='S'); z=self.run_select(arm='Z'); m=self.run_select(scorer=self.numeric)
        self.assertEqual(s['selected'],['a']); self.assertEqual(z['selected'],s['selected'])
        self.assertEqual(m['selected'],['b']); self.assertEqual(m['magnetic_changed_steps'],1)
        self.assertEqual(z['trace'][0]['raw_scores'][0]['schedule_costs'],[0.,0.])
        self.assertAlmostEqual(m['trace'][0]['raw_scores'][0]['schedule_costs'][0],3*(1-3**.5/2),places=8)

    def test_translated_true_tie(self):
        self.pool[1]=self.p('b','3','5')
        r=self.run_select(scorer=self.numeric)
        self.assertEqual(r['trace'][0]['status'],'all_candidate_tie'); self.assertEqual(r['magnetic_changed_steps'],0)

    def test_empty_and_single(self):
        self.pool=[]; self.assertEqual(self.run_select()['achieved_budget'],0)
        self.pool=[self.p('a','0','2')]
        r=self.run_select(scorer=self.numeric)
        self.assertEqual(r['trace'][0]['status'],'single_option')

    def test_partial_minimum_tie(self):
        self.pool.append(self.p('c','1','4'))
        r=self.run_select(scorer=lambda p:{'status':'finite_estimate','schedule_costs':[1.,1.] if p[-1]=='c' else [0.,0.]})
        self.assertEqual(r['trace'][0]['status'],'partial_minimum_tie'); self.assertEqual(r['selected'],['a'])

    def test_unresolved_ranges(self):
        r=self.run_select(scorer=lambda p:{'status':'finite_estimate','schedule_costs':[0.,2.] if p[-1]=='a' else [1.,1.]})
        self.assertEqual(r['trace'][0]['status'],'unresolved_ranges')

    def test_nonstationary_not_deleted_and_budget_retained(self):
        r=self.run_select(scorer=lambda p:{'status':'nonstationary'} if p[-1]=='a' else {'status':'finite_estimate','schedule_costs':[0.,0.]})
        self.assertEqual(r['trace'][0]['status'],'unscorable_candidate')
        self.assertEqual(r['selected'],['a']); self.assertEqual(r['achieved_budget'],r['feasible_budget'])

    def test_runtime_fallback_and_single_failure(self):
        r=self.run_select(scorer=lambda p:{'status':'resource_limit'})
        self.assertEqual(r['trace'][0]['status'],'runtime_exhaustion'); self.assertEqual(r['selected'],['a'])
        self.pool=self.pool[:1]
        self.assertEqual(self.run_select(scorer=lambda p:{'status':'nonstationary'})['trace'][0]['status'],'unscorable_candidate')

    def test_repeated_score_cache_and_identity(self):
        cache={}; calls=[]
        def score(p): calls.append(p); return self.numeric(p)
        first=self.run_select(scorer=score,score_cache=cache)
        self.run_select(scorer=score,score_cache=cache)
        self.assertEqual(len(calls),2)
        self.pool[0]['semantic_score']=5
        second=self.run_select(scorer=score,score_cache=cache)
        self.assertNotEqual(first['binding_sha256'],second['binding_sha256']); self.assertEqual(len(calls),3)

    def test_entire_highest_tier_no_shortlist(self):
        self.pool += [self.p('c','1','4'),self.p('d','2','5'),self.p('e','1','5',5)]
        r=self.run_select(scorer=lambda p:{'status':'finite_estimate','schedule_costs':[0.,0.] if p[-1]=='d' else [1.,1.]})
        self.assertEqual(r['selected'],['d']); self.assertEqual(r['trace'][0]['tier'],list('abcd'))

    def test_completion_preserving_conflicts_and_source_cap(self):
        self.pool=[self.p('a','0','2',6,['b','c']),self.p('b','1','3',5,['a']),self.p('c','2','4',5,['a'])]
        r=self.run_select(arm='S',maximum=2)
        self.assertEqual(r['selected'],['b','c']); self.assertNotIn('a',r['trace'][0]['completion_preserving_alternatives'])
        self.pool=[self.p('a','0','2'),self.p('b','0','5')]
        self.assertEqual(self.run_select(arm='S',maximum=4)['achieved_budget'],1)

    def test_pair_and_reciprocal_conflicts(self):
        self.pool=[self.p('a','0','2'),self.p('b','0','2')]
        self.assertEqual(self.run_select(arm='S',maximum=4)['feasible_budget'],1)
        self.pool=[self.p('a','0','2'),self.p('b','2','0')]
        with self.assertRaisesRegex(ValueError,'reciprocal'): self.run_select(arm='S')

    def test_joint_prefix_recomputation_merges_blocks(self):
        self.pool=[self.p('a','0','2'),self.p('b','1','3')]
        calls=[]
        def score(prefix): calls.append(prefix); return self.numeric(prefix)
        r=self.run_select(scorer=score,maximum=2)
        self.assertTrue(any(len(p)==2 for p in calls))
        final=r['trace'][-1]['raw_scores'][0]
        self.assertEqual(final['schedules'][0]['largest_affected_support'],4)
        self.assertGreater(abs(final['schedule_costs'][0]-2*self.numeric(('a',))['schedule_costs'][0]),.01)

    def test_negative_and_nan_failures(self):
        for costs in [[-1.,-1.],[float('nan'),0.]]:
            r=self.run_select(scorer=lambda p:{'status':'finite_estimate','schedule_costs':costs})
            self.assertEqual(r['trace'][0]['status'],'unscorable_candidate')

    def test_process_watchdog(self):
        policy=dict(DEFAULT_POLICY,wall_seconds=.2)
        scorer=BoundedBlockScorer(self.graph,self.pool,policy,worker=sleepy_worker)
        start=time.monotonic(); result=scorer(('a',))
        self.assertEqual(result['status'],'resource_limit'); self.assertLess(time.monotonic()-start,3)
        self.assertEqual(scorer.attempts[0]['status'],'resource_limit')


class FixtureLedgerV2(unittest.TestCase):
    def setUp(self):
        from unit_tests.test_embedded_links import EmbeddedLinks
        old=EmbeddedLinks(); old.setUp(); self.old=old
        self.p=copy.deepcopy(old.p); self.p['schema_version']=ledger.VERSION+'.proposal'
        self.p['provenance']={'kind':'authored_fixture','manifest_sha256':e.digest({'fixture':True}),
                              'candidate_key':'anchor','timestamp':'authored'}

    def test_no_fabricated_model_and_exact_byte_edit(self):
        ledger.validate_proposal(self.p)
        self.assertNotIn('provider',self.p['provenance'])
        new,f=ledger.apply_edit(self.old.raw,self.old.target,self.p,self.old.pages,[],self.old.region,self.old.limits)
        self.assertEqual(e.replay_patch(self.old.raw,f),new)
        raw='Café engine works.\r\n'.encode(); p=copy.deepcopy(self.p)
        a=raw.index(b'engine'); p.update(source_sha256=e.rawhash(raw),source_span=[a,a+6],sentence='Café engine works.')
        new,_=ledger.apply_edit(raw,self.old.target,p,self.old.pages,[],self.old.region,self.old.limits)
        self.assertTrue(new.endswith(b'\r\n'))

    def test_stale_sources_and_duplicate_application(self):
        p=copy.deepcopy(self.p); p['source_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'stale'): ledger.apply_edit(self.old.raw,self.old.target,p,self.old.pages,[],self.old.region,self.old.limits)
        new,_=ledger.apply_edit(self.old.raw,self.old.target,self.p,self.old.pages,[],self.old.region,self.old.limits)
        with self.assertRaisesRegex(ValueError,'stale'): ledger.apply_edit(new,self.old.target,self.p,self.old.pages,[],self.old.region,self.old.limits)

    def test_authored_ledger_tip_authority_and_tamper(self):
        log=[ledger.event([],self.p,'proposed',self.p,'fixture_author')]
        log.append(ledger.event(log,self.p,'schema_validated',{'proposal_sha256':e.digest(self.p)}))
        tip=e.digest(log[-1]); self.assertEqual(ledger.replay(log+[log[-1]],tip)['unique_events'],2)
        bad=copy.deepcopy(log); bad[0]['payload']['anchor']='changed'
        with self.assertRaisesRegex(ValueError,'chain'): ledger.replay(bad,tip)
        with self.assertRaisesRegex(ValueError,'authority'): ledger.event([],self.p,'proposed',self.p,'model')
        with self.assertRaisesRegex(ValueError,'tip'): ledger.replay(log[:1],tip)
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'log'; ledger.append_event(p,log[0]); ledger.append_event(p,log[0])
            self.assertEqual(len(p.read_bytes().splitlines()),1)

    def test_authored_provenance_closed(self):
        p=copy.deepcopy(self.p); p['provenance']['provider']='invented'
        with self.assertRaises(ValueError): ledger.validate_proposal(p)

    def test_source_paragraph_masks_and_byte_maps(self):
        raw='Café engine works.\r\n\r\n- list text.\r\n\r\nThe [manual](b.md) explains engines.\r\n'.encode()
        rows,omitted=demo.paragraphs(raw,source=True)
        self.assertEqual(len(rows),1); self.assertTrue(any(r['reason']=='no-host-admissible-anchor' for r in omitted))
        for row in rows:
            for ch,span in zip(row['text'],row['raw_byte_map']): self.assertEqual(raw[slice(*span)].decode(),ch)
        self.assertTrue(rows[0]['legal_word_spans'])

    def test_target_semantic_projection_excludes_destinations(self):
        rows,_=demo.paragraphs(b'The [manual](b.md) explains engines.\n')
        self.assertEqual(rows[0]['text'],'The manual explains engines.')
        self.assertNotIn('b.md',rows[0]['text'])

    def test_access_materializes_four_groups_and_excludes_original_pairs(self):
        from retrieval_arena.embedded_link_audit import write, read
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inp=root/'input'; out=root/'output'; inp.mkdir();out.mkdir();(inp/'corpus').mkdir()
            pages=[]; regions=[]; edges=[]
            for i in range(4):
                nodes=[f'{i}{n}' for n in 'abc']
                for n in nodes:
                    raw=b'The engine manual explains this workflow.\n'
                    (inp/'corpus'/(n+'.md')).write_bytes(raw)
                    pages.append({'doc_id':n,'path':n+'.md','raw_sha256':e.rawhash(raw)})
                edges += [[nodes[0],nodes[1]],[nodes[1],nodes[2]]]
                regions.append({'assignment':'development','region_id':str(i),'nodes':nodes,'source_families':['fixture'],
                                'component_membership':{n:str(i) for n in nodes}})
            write(inp/'pages.json',pages);write(inp/'regions.json',regions);write(inp/'original.json',{'edges':edges})
            result=demo.access_run(inp,out,{'pages':'pages.json','regions':'regions.json','original':'original.json','corpus':'corpus'})
            self.assertEqual(len(result['groups']),4);self.assertEqual(result['packets'],8)
            self.assertEqual(len(list((out/'packets').glob('*.json'))),8)
            for packet in read(out/'packets.json'):
                for target in packet['targets']:
                    pair=[packet['source_id'],target['target_id']]
                    self.assertNotIn(pair,edges);self.assertNotIn(pair[::-1],edges)


if __name__=='__main__': unittest.main()
