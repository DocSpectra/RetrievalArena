"""Behavioral qualification, fake transports only; no provider clients."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from retrieval_arena import embedded_competitive as c
from retrieval_arena import embedded_study as s
from retrieval_arena import embedded_refinement as f
from retrieval_arena.embedded_interface_fixture import prepare, MockTransport, mock_count
from retrieval_arena.embedded_link_audit import read, write
from retrieval_arena.embedded_selection_v2 import select_embedded_v2
from unit_tests.test_embedded_model_run import settings
from unit_tests.test_embedded_refinement import fixture


def bundle(root):
    original=root/'original';original.mkdir();fixture(original)
    authored=root/'authored';(authored/'corpus').mkdir(parents=True)
    for n in range(6):shutil.copyfile(original/f'corpus/group0/{n}.md',authored/f'corpus/{n}.md')
    write(authored/'fixture.json',{'baseline_edges':[[str(n),str(n+1)] for n in range(5)],'candidates':[
        {'candidate_key':'validation','source':'0','target':'2','anchor':'validation rules','target_quote':'Validation rules define the source check.'},
        {'candidate_key':'checklist','source':'0','target':'5','anchor':'release checklist','target_quote':'The release checklist completes this workflow.'}],
        'scenarios':[{'id':'mock-witness','candidate_order':['validation','checklist']}]})
    roles=[]
    for role in ('generation','eligibility','support_confirmation'):
        cfg=settings();cfg['spend_cap_usd']=1
        if role=='eligibility':cfg.update(provider='anthropic',model='claude-haiku-5-5',revision='claude-haiku-5-5',generation={'thinking':'disabled','effort':'low'},service_tier='standard_only')
        if role=='support_confirmation':cfg.update(model='gpt-6.1-sol',revision='gpt-6.1-sol')
        schema=c.disposition_schema() if role=='generation' else c.interface.review_schema()
        roles.append({'role':role,'settings':cfg,'schema':schema,'schema_sha256':c.e.digest(schema),
            'prompt_prefix':'MOCK technical fixture.','prompt_sha256':c.e.digest(role),
            'initial_items_max':1 if role=='generation' else 2,'attempts_max':2 if role=='generation' else 4})
    lifecycle={'retry_backoff_seconds':0,'maximum_count_request_bytes':32768}
    policy={'roles':roles,'interface_version':c.interface.VERSION,'lifecycle_policy':lifecycle,
        'competitive_selection_version':c.VERSION,'aggregate_hard_cap_usd':30,
        'provider_token_count_complete':False,'generation_counts':[],'mock_responses':True}
    write(authored/'study-manifest.json',policy);inp=root/'input';inp.mkdir()
    for lane in ('preflight','real'):
        prepare(authored,inp/lane,{'fixture_manifest':'fixture.json','corpus':'corpus','scenario':'mock-witness'})
    manifest={'run_id':'MOCK-COMPETITIVE','interface_version':c.interface.VERSION,
        'competitive_selection_version':c.VERSION,'aggregate_hard_cap_usd':30,
        'lifecycle_policy':lifecycle,'generation_count_cap_usd':{'preflight':1,'real':1},
        'count_attempts':{'preflight':2,'real':2},'maximum_reserved_usd':8,'live_seconds':120,'concurrency':2}
    write(inp/'study-manifest.json',manifest);s.seal(inp)
    return inp,manifest


def plan(scores=(6,6)):
    pool=[{'proposal_id':str(n),'source_id':'0','target_id':str(t),'semantic_score':score,
           'tie_id':c.tie_key('0',str(t)),'semantic_eligible':True,'conflicts':[]}
          for n,(t,score) in enumerate(zip((2,5,4),scores))]
    pool.sort(key=lambda p:(-p['semantic_score'],p['tie_id']))
    top=[p['proposal_id'] for p in pool if p['semantic_score']==pool[0]['semantic_score']]
    return {'pool':pool,'competitive':len(top)>1,'top_tier':top}


def arms(p,values):
    graph={'nodes':list('012345'),'edges':[[str(n),str(n+1)] for n in range(5)]}
    def score(prefix):return values[prefix[0]]
    return {a:select_embedded_v2(p['pool'],graph,{'maximum':1},arm=a,scorer=score) for a in ('S','Z','M')}


def finite(x,y=None):return {'status':'finite_estimate','schedule_costs':[x,x if y is None else y]}


class CompetitiveTests(unittest.TestCase):
    def test_disposition_coverage_and_identity(self):
        good={'dispositions':[{'target_id':'2','disposition':'no_supported_link','reason':'No support.','proposal':None}]}
        f.validate_result(good,{'schema':c.disposition_schema(),'source_id':'0','target_ids':['2']})
        for ids in ([],['2','5']):
            with self.assertRaisesRegex(ValueError,'coverage'):c.validate_dispositions(good,'0',ids)
        bad=copy.deepcopy(good);bad['dispositions']*=2
        with self.assertRaisesRegex(ValueError,'coverage'):c.validate_dispositions(bad,'0',['2'])
        bad=copy.deepcopy(good);bad['dispositions'][0].update(disposition='supported_link')
        with self.assertRaisesRegex(ValueError,'identity'):c.validate_dispositions(bad,'0',['2'])

    def test_exact_count_never_omits_target(self):
        from retrieval_arena.embedded_request_plan import omit_lowest_target_passage
        self.assertIsNone(omit_lowest_target_passage({'target_dispositions_required':True,'targets':[{},{}]}))

    def test_hash_tie_order_is_stable(self):
        self.assertEqual(c.tie_key('0','2'),'45d1b4f5f0cec02713ed956294b33e1708e44d833ba45a5e91516c9e505ee33a')
        self.assertLess(c.tie_key('0','2'),c.tie_key('0','5'))

    def test_changed_unique_winner_and_zero_equality(self):
        p=plan();a=arms(p,{'0':finite(.4),'1':finite(0)})
        r=c.outcome(p,a);self.assertEqual(r['category'],5);self.assertTrue(r['numerically_evaluable'])
        self.assertEqual(r['replacements'],1);self.assertEqual(a['S']['selected'],a['Z']['selected'])

    def test_unchanged_unique_winner(self):
        p=plan();self.assertEqual(c.outcome(p,arms(p,{'0':finite(0),'1':finite(.4)}))['category'],4)

    def test_true_tie_is_evaluable_unresolved(self):
        p=plan();r=c.outcome(p,arms(p,{'0':finite(.4),'1':finite(.4)}))
        self.assertEqual((r['category'],r['decision_status'],r['numerically_evaluable']),(3,'all_candidate_tie',True))

    def test_partial_tie_and_overlap_remain_distinct(self):
        p=plan((6,6,6));a=arms(p,{'0':finite(0),'1':finite(0),'2':finite(1)})
        self.assertEqual(c.outcome(p,a)['decision_status'],'partial_minimum_tie')
        p=plan();a=arms(p,{'0':finite(0,2),'1':finite(1,3)})
        self.assertEqual(c.outcome(p,a)['decision_status'],'unresolved_ranges')

    def test_singleton_top_tier_despite_lower_alternative(self):
        p=plan((6,5));a=arms(p,{'0':finite(1),'1':finite(0)})
        self.assertEqual(c.outcome(p,a)['category'],2)
        self.assertEqual(a['M']['trace'][0]['tier'],['0'])

    def test_unavailable_candidate_is_not_dropped(self):
        p=plan();a=arms(p,{'0':finite(1),'1':{'status':'nonstationary'}});r=c.outcome(p,a)
        self.assertEqual((r['category'],r['numerically_evaluable']),(3,False))
        self.assertEqual(a['M']['selected'],a['S']['selected'])

    def test_empty_pool_category(self):
        p={'pool':[],'competitive':False};self.assertEqual(c.outcome(p,arms(p,{}))['category'],1)

    def test_two_stage_support_prevents_disagreement_admission(self):
        verdict={'relationship_support':2,'target_support':1,'placement':1,
                 'uncertainty':False,'contradiction':False,'relation':'target_elaborates_source'}
        self.assertFalse(c.supported(verdict));verdict['target_support']=2;self.assertTrue(c.supported(verdict))

    def test_full_fake_pipeline_copies_replay_and_cached_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=bundle(root)
            auth={'approved':True,'scope':'bounded_embedded_refinement_v3','study_manifest_sha256':c.e.digest(manifest),
                  'input_inventory_sha256':read(inp/'operator-request.json')['input_inventory_sha256']}
            s.admit(inp,auth);out=root/'out'
            factory=lambda cfg,network,path:MockTransport(cfg,path)
            first=asyncio.run(c.CompetitiveStudy(inp,out,manifest,factory=factory,counter=mock_count).run())
            selected=out/'real-selection-output';g=read(selected/'groups.json')[0]
            self.assertEqual(g['outcome']['category'],5)
            self.assertTrue(read(selected/'numerical'/g['region_id'].replace(':','-')/'diagnostic-q010.json')['preference_reversal'])
            for arm in ('S','Z','M'):
                raw=(selected/arm/'intent-events.jsonl').read_bytes()
                log=[json.loads(line) for line in raw.splitlines()]
                f.ledger.replay(log,read(selected/arm/'trusted-tip.json')['tip_sha256'])
                self.assertEqual(len(read(selected/arm/'host-facts.json')),1)
            self.assertEqual((selected/'S/corpus/0.md').read_bytes(),(selected/'Z/corpus/0.md').read_bytes())
            self.assertNotEqual((selected/'S/corpus/0.md').read_bytes(),(selected/'M/corpus/0.md').read_bytes())
            replay=root/'replay';replay.mkdir()
            c.select_and_apply(out/'real-selection-input',replay,{})
            replay_g=read(replay/'groups.json')[0]
            self.assertEqual(g['outcome'],replay_g['outcome'])
            self.assertEqual(g['arms']['M']['trace'][0]['raw_scores'],replay_g['arms']['M']['trace'][0]['raw_scores'])
            async def forbidden(*a):raise AssertionError('cached restart made a new count')
            second=asyncio.run(c.CompetitiveStudy(inp,out,manifest,factory=factory,counter=forbidden).run())
            self.assertEqual(first,second)

    def test_rejected_and_empty_pipelines_use_no_numerical_solver(self):
        for mode in ('negative','empty','sol-disagreement'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                inp,manifest=bundle(Path(tmp))
                # Empty/negative preflights correctly fail the technical gate;
                # test the real lane in isolation to retain the legitimate funnel.
                def factory(cfg,network,path):
                    use='negative' if mode=='sol-disagreement' and cfg['model']=='gpt-6.1-sol' else 'eligible' if mode=='sol-disagreement' else mode
                    return MockTransport(cfg,path,mode=use)
                out=Path(tmp)/'out';runner=c.CompetitiveStudy(inp,out,manifest,factory=factory,counter=mock_count)
                asyncio.run(runner.lane('real'))
                group=read(out/'real-selection-output/groups.json')[0]
                self.assertEqual(group['outcome']['category'],1)
                self.assertFalse(list((out/'real-selection-output/numerical').glob('*/diagnostic-q010.json')))
                if mode=='sol-disagreement':
                    records=read(out/'real-pool-output/common-pool.json')['records']
                    self.assertTrue(all(r['haiku_eligible'] and r['confirmation_status']=='semantic_rejected' for r in records))

    def test_pool_publication_precedes_real_scoring_and_resume_reuses_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);inp,manifest=bundle(root);manifest['publish_real_pool_before_selection']=True
            out=root/'out';factory=lambda cfg,network,path:MockTransport(cfg,path)
            runner=c.CompetitiveStudy(inp,out,manifest,factory=factory,counter=mock_count)
            with self.assertRaisesRegex(RuntimeError,'publication-required'):
                asyncio.run(runner.lane('real'))
            self.assertFalse((out/'real-selection-output').exists())
            frozen=read(out/'real-pool-output/common-pool.json')
            write(out/'real-pool-publication.json',{'pool_sha256':c.e.digest(frozen),'context_commit':'MOCK-CONTEXT-COMMIT'})
            async def forbidden(*a):raise AssertionError('publication resume repeated a count')
            runner=c.CompetitiveStudy(inp,out,manifest,factory=factory,counter=forbidden)
            asyncio.run(runner.lane('real'))
            self.assertEqual(read(out/'real-selection-output/groups.json')[0]['outcome']['category'],5)


if __name__=='__main__':unittest.main()
