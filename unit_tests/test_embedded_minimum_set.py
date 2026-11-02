import copy
import itertools
from pathlib import Path
import shutil
import tempfile
import unittest

from retrieval_arena import embedded_minimum_set as m
from retrieval_arena import embedded_minimum_set_study as study
from retrieval_arena import embedded_curated as c
from retrieval_arena import embedded_links as e
from retrieval_arena.embedded_link_audit import read, write, portable_inventory
from unit_tests import test_embedded_curated as fixtures


def finite(a,b=None):return {'status':'finite_estimate','schedule_costs':[a,a if b is None else b]}


class MinimumSet(unittest.TestCase):
    def setUp(self):
        self.graph={'nodes':list('012345678'),'edges':[[str(i),str(i+1)] for i in range(8)]}
        self.pool=[dict(proposal_id=pid,source_id=s,target_id=t,semantic_score=6,
            semantic_eligible=True,tie_id=pid,conflicts=[]) for pid,s,t in [('a','0','2'),('b','0','5'),('c','3','8')]]

    def select(self, rows, pool=None, arm='M'):
        return m.select_minimum_set(self.pool if pool is None else pool,self.graph,{'maximum':1},
            arm=arm,scorer=lambda prefix:rows[prefix[0]])

    def test_separated_tie_and_original_v2(self):
        rows=dict(a=finite(1),b=finite(0),c=finite(0))
        new=self.select(rows)
        old=m.v2.select_embedded_v2(self.pool,self.graph,{'maximum':1},scorer=lambda p:rows[p[0]])
        self.assertEqual(old['selected'],['a']);self.assertEqual(old['trace'][0]['status'],'partial_minimum_tie')
        self.assertEqual(new['selected'],['b']);self.assertEqual(new['trace'][0]['status'],'separated_minimum_set')
        self.assertEqual(new['trace'][0]['minimum_set']['member_ids'],['b','c'])
        self.assertEqual(new['score_provenance']['binding_sha256'],old['binding_sha256'])
        self.assertNotEqual(new['binding_sha256'],old['binding_sha256'])

    def test_semantic_inside_and_permuted_input(self):
        for rows,expected in [(dict(a=finite(1),b=finite(0),c=finite(0)),['b']),
                              (dict(a=finite(0),b=finite(1),c=finite(0)),['a'])]:
            results=[self.select(rows,list(p)) for p in itertools.permutations(self.pool)]
            self.assertTrue(all(r['selected']==expected for r in results))
            comparable=lambda r:{k:v for k,v in r['trace'][0].items() if k!='score_cache_ids'}
            self.assertTrue(all(comparable(r)==comparable(results[0]) for r in results))
            self.assertEqual(results[0]['trace'][0]['selected_tie_id'],expected[0])

    def test_unique_all_tie_empty_single_and_q0(self):
        rows=dict(a=finite(1),b=finite(0),c=finite(2))
        self.assertEqual(self.select(rows)['trace'][0]['status'],'resolved_finite_order')
        tie=self.select(dict.fromkeys('abc',finite(0)))
        self.assertEqual(tie['trace'][0]['status'],'all_candidate_tie');self.assertEqual(tie['selected'],['a'])
        self.assertEqual(self.select({},[])['selected'],[])
        self.assertEqual(self.select(rows,self.pool[:1])['trace'][0]['status'],'single_option')
        self.assertEqual(self.select({},arm='S')['selected'],self.select({},arm='Z')['selected'])
        with self.assertRaisesRegex(ValueError,'k1'):m.select_minimum_set([],self.graph,{'maximum':2})

    def test_overlaps_chains_and_wide_envelope(self):
        cases=[([finite(0,2),finite(1),finite(4)],'minimum-envelope-too-wide'),
               ([finite(0),finite(.75),finite(1.5)],'outsider-not-strictly-separated'),
               ([finite(0,.9),finite(.5,1.4),finite(4)],'minimum-envelope-too-wide')]
        for rows,reason in cases:
            status,index,detail=m.classify(rows,1.)
            self.assertEqual((status,index),('unresolved_ranges',None));self.assertEqual(detail['fallback_reason'],reason)

    def test_exact_boundaries(self):
        # atol=1 makes exact binary boundary arithmetic explicit.
        self.assertEqual(m.classify([finite(0),finite(1),finite(3)],1)[0],'separated_minimum_set')
        self.assertEqual(m.classify([finite(0),finite(1),finite(2)],1)[0],'unresolved_ranges')
        self.assertEqual(m.classify([finite(0),finite(1.0000001),finite(3)],1)[0],'resolved_finite_order')
        self.assertEqual(m.classify([finite(0),finite(1)],1)[0],'all_candidate_tie')
        self.assertEqual(m.classify([finite(0),finite(1.0000001)],1)[0],'resolved_finite_order')

    def test_unavailable_precedence_and_raw_tiny_negative(self):
        for failure,status in [('nonstationary','unscorable_candidate'),('resource_limit','runtime_exhaustion')]:
            rows=dict(a=finite(1),b=finite(0),c={'status':failure})
            result=self.select(rows)
            self.assertEqual(result['selected'],['a']);self.assertEqual(result['trace'][0]['status'],status)
        self.assertEqual(m.classify([{'status':'nonstationary'},{'status':'resource_limit'}])[0],'runtime_exhaustion')
        for values in [[float('nan'),0],[-1e-8,0],[0]]:
            self.assertEqual(m.classify([{'status':'finite_estimate','schedule_costs':values}])[0],'unscorable_candidate')
        rows=dict(a=finite(1),b=finite(-1e-10,0),c=finite(0))
        result=self.select(rows);self.assertEqual(result['selected'],['b'])
        self.assertEqual(result['trace'][0]['raw_scores'][1]['schedule_costs'][0],-1e-10)

    def scores(self):
        policy=dict(m.v2.DEFAULT_POLICY,maximum=1)
        scorer=m.v2.BoundedBlockScorer(self.graph,self.pool,policy)
        costs={p['proposal_id']:scorer((p['proposal_id'],)) for p in self.pool}
        old=m.v2.select_embedded_v2(self.pool,self.graph,policy,scorer=lambda p:costs[p[0]])
        return policy,old,dict(solves=scorer.cache,candidate_costs=costs)

    def test_actual_graph_tied_cycles_and_stale_provenance(self):
        policy,old,audit=self.scores()
        self.assertEqual(old['selected'],['a'])
        self.assertEqual(self.select(audit['candidate_costs'])['selected'],['b'])
        proof=m.replay_scores(self.pool,self.graph,policy,old,audit)
        self.assertTrue(proof['v2_replay_equal']);self.assertGreater(proof['starts'],0)
        bad=copy.deepcopy(old);bad['policy']['seeds']=[3502,3501]
        with self.assertRaisesRegex(ValueError,'policy'):m.replay_scores(self.pool,self.graph,policy,bad,audit)
        pool=copy.deepcopy(self.pool);pool[0]['tie_id']='changed'
        with self.assertRaisesRegex(ValueError,'binding'):m.replay_scores(pool,self.graph,policy,old,audit)
        bad=copy.deepcopy(audit);bad['solves'].pop(next(iter(bad['solves'])))
        with self.assertRaisesRegex(ValueError,'missing-solve'):m.replay_scores(self.pool,self.graph,policy,old,bad)
        bad=copy.deepcopy(audit);next(iter(bad['solves'].values()))['starts'][0]['final_phases'][1]+=.1
        with self.assertRaisesRegex(ValueError,'energy-gradient'):m.replay_scores(self.pool,self.graph,policy,old,bad)

    def test_tiny_scoring_selection_edit_and_external_ledger_replay(self):
        # Existing authored full-text fixture supplies actual patch/ledger contracts.
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,frozen=fixtures.Curated().prepare(root)
            historical=root/'historical';historical.mkdir()
            c.select(inp,historical,{'diagnostic':False})
            # Seal a genuine historical numerical output, as the operator does.
            write(historical/'summary.json',{'image':{'RA_SOURCE_COMMIT':'1'*40,'RA_IMAGE_REFERENCE':'sha256:'+'2'*64}})
            inv=portable_inventory(historical);write(historical/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
            shutil.copytree(historical,inp/'historical')
            write(inp/'study-binding.json',{'historical_receipt':{'source_commit':'1'*40,'image':{'reference':'sha256:'+'2'*64}},
                'historical_files':{p.relative_to(historical).as_posix():e.rawhash(p.read_bytes()) for p in historical.rglob('*') if p.is_file()}})
            out=root/'selected';out.mkdir();study.select(inp,out,{})
            groups=read(out/'groups.json')
            self.assertEqual(groups[0]['arms']['M']['selected'],groups[0]['historical_M_v2']['selected'])
            shutil.copytree(out,inp/'result');write(inp/'ledger-anchors.json',read(out/'ledger-anchors.json'))
            checked=root/'checked';checked.mkdir();proof=study.verify(inp,checked,{})
            self.assertEqual(proof['files_verified'],24);self.assertEqual(len(proof['changed_groups_freshly_replayed']),1)
            anchors=read(inp/'ledger-anchors.json');anchors['M']='0'*64;write(inp/'ledger-anchors.json',anchors)
            with self.assertRaisesRegex(ValueError,'tip'):study.verify(inp,checked,{})

    def test_cycle_traversal_replay_gauge_and_tamper(self):
        arcs=[['a','b'],['b','c'],['a','c']]
        make=lambda cycle:{'candidates':[{'after_arcs':arcs,'cycle':study.cycle_record(cycle,arcs)}]}
        baseline=study.canonical_structure(make(list('abc')))
        for cycle in itertools.permutations('abc'):
            self.assertEqual(study.canonical_structure(make(list(cycle))),baseline)
        bad=make(list('abc'));bad['candidates'][0]['cycle']['flux']+=.1
        with self.assertRaisesRegex(ValueError,'cycle-receipt'):study.canonical_structure(bad)


if __name__=='__main__':unittest.main()
