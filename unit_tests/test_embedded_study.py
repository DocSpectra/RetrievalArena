"""Offline lifecycle tests for the combined approval path; no SDK clients."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
import shutil

from retrieval_arena import embedded_token_admission as t
from retrieval_arena import embedded_model_run as m
from retrieval_arena import embedded_study as s
from retrieval_arena.embedded_link_audit import read,write
from unit_tests.test_embedded_token_admission import job
from unit_tests.test_embedded_model_run import inputs,FakeTransport


class StudyLifecycle(unittest.TestCase):
    def test_combined_pipeline_with_mock_responses_and_cached_restart(self):
        from retrieval_arena import embedded_interface as interface
        from retrieval_arena.embedded_interface_fixture import prepare,MockTransport,mock_count
        from unit_tests.test_embedded_refinement import fixture
        from unit_tests.test_embedded_model_run import settings
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);original=root/'original';original.mkdir();fixture(original)
            authored=root/'authored';(authored/'corpus').mkdir(parents=True)
            for n in range(6):shutil.copyfile(original/f'corpus/group0/{n}.md',authored/f'corpus/{n}.md')
            fm={'baseline_edges':[[str(n),str(n+1)] for n in range(5)],'candidates':[
                {'candidate_key':'validation','source':'0','target':'2','anchor':'validation rules','target_quote':'Validation rules define the source check.'},
                {'candidate_key':'checklist','source':'0','target':'5','anchor':'release checklist','target_quote':'The release checklist completes this workflow.'}],
                'scenarios':[{'id':'mock-witness','candidate_order':['validation','checklist']}]}
            write(authored/'fixture.json',fm)
            roles=[]
            for role in ('generation','eligibility','blinded_assessment'):
                cfg=settings();cfg['spend_cap_usd']=1
                if role=='eligibility':cfg.update(provider='anthropic',model='claude-haiku-5-5',revision='claude-haiku-5-5',generation={'thinking':'disabled','effort':'low'},service_tier='standard_only')
                schema=interface.proposal_schema() if role=='generation' else interface.review_schema()
                roles.append({'role':role,'settings':cfg,'schema':schema,'schema_sha256':s.e.digest(schema),
                    'prompt_prefix':'MOCK technical fixture.','prompt_sha256':s.e.digest(role),
                    'initial_items_max':1 if role=='generation' else 2,'attempts_max':2 if role=='generation' else 4})
            lifecycle={'retry_backoff_seconds':0,'maximum_count_request_bytes':65536}
            policy={'roles':roles,'interface_version':interface.VERSION,'lifecycle_policy':lifecycle,
                'aggregate_hard_cap_usd':20,'provider_token_count_complete':False,'generation_counts':[],
                'mock_responses':True}
            write(authored/'study-manifest.json',policy)
            inp=root/'input';inp.mkdir()
            for lane in ('preflight','real'):
                prepare(authored,inp/lane,{'fixture_manifest':'fixture.json','corpus':'corpus','scenario':'mock-witness'})
            manifest={'run_id':'MOCK-COMBINED','interface_version':interface.VERSION,'aggregate_hard_cap_usd':20,
                'lifecycle_policy':lifecycle,'generation_count_cap_usd':{'preflight':1,'real':1},
                'count_attempts':{'preflight':2,'real':20},'maximum_reserved_usd':8,'live_seconds':60,'concurrency':2}
            write(inp/'study-manifest.json',manifest);s.seal(inp)
            auth={'approved':True,'scope':'bounded_embedded_refinement_v3','study_manifest_sha256':s.e.digest(manifest),
                'input_inventory_sha256':read(inp/'operator-request.json')['input_inventory_sha256'],
                'note':'synthetic test authorization; no live clients'}
            s.admit(inp,auth);out=root/'out'
            factory=lambda cfg,network,path:MockTransport(cfg,path)
            first=asyncio.run(s.Study(inp,out,manifest,factory=factory,counter=mock_count).run())
            self.assertEqual(first['status'],'complete')
            self.assertEqual([a['applied'] for a in read(out/'real-selection-output/applications.json')],[1,1,1])
            self.assertTrue(read(out/'real-selection-output/groups.json')[0]['selection_sequence_changed'])
            async def forbidden(*args):raise AssertionError('restart must reuse counts')
            second=asyncio.run(s.Study(inp,out,manifest,factory=factory,counter=forbidden).run())
            self.assertEqual(first,second)

    def test_counts_resume_omissions_and_completed_counts_without_calls(self):
        calls=[]
        async def count(body):
            calls.append(body)
            if len(calls)==1:raise TimeoutError()
            return {'input_tokens':17000 if len(calls)==2 else 100,'mock':True}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            first=asyncio.run(t.count_resumable([job()],out,count,deadline=time.monotonic()+60,maximum_attempts=20,backoff=0))
            self.assertEqual(first['attempts'],3);self.assertEqual(first['results'][0]['status'],'admitted')
            async def forbidden(*a):raise AssertionError('no repeated count')
            second=asyncio.run(t.count_resumable([job()],out,forbidden,deadline=time.monotonic()+60,maximum_attempts=20))
            self.assertEqual(first,second);self.assertEqual(len(calls),3)

    def test_count_authentication_and_permissions_open_distinct_persistent_circuits(self):
        for error,kind in [('AuthenticationError','authentication'),('PermissionDeniedError','permission')]:
            failure=type(error,(Exception,),{})
            calls=[]
            async def count(body):calls.append(body);raise failure()
            with tempfile.TemporaryDirectory() as tmp:
                out=Path(tmp)
                first=asyncio.run(t.count_resumable([job()],out,count,deadline=time.monotonic()+60,maximum_attempts=20,backoff=0))
                self.assertEqual(first['halt_reason'],kind+'_blocked');self.assertEqual(len(calls),1)
                second=asyncio.run(t.count_resumable([job()],out,count,deadline=time.monotonic()+60,maximum_attempts=20,backoff=0))
                self.assertEqual(second['attempts'],1);self.assertEqual(len(calls),1)

    def test_unknown_interrupted_counts_consume_attempt_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);calls=[]
            async def count(body):calls.append(1);return {'input_tokens':100}
            first=asyncio.run(t.count_resumable([job()],out,count,deadline=time.monotonic()+60,maximum_attempts=20,backoff=0))
            terminal=next(out.glob('job-*/result.json'));terminal.unlink()
            record=read(out/'counts/0001.json');record['attempt']['status']='attempted';record.pop('raw_response')
            write(out/'counts/0001.json',record)
            second=asyncio.run(t.count_resumable([job()],out,count,deadline=time.monotonic()+60,maximum_attempts=20,backoff=0))
            self.assertEqual(second['attempts'],2);self.assertEqual(len(calls),2)

    def test_unsupported_count_does_not_retry(self):
        class BadRequestError(Exception):pass
        calls=[]
        async def count(body):calls.append(1);raise BadRequestError()
        with tempfile.TemporaryDirectory() as tmp:
            result=asyncio.run(t.count_resumable([job()],Path(tmp),count,deadline=time.monotonic()+60,maximum_attempts=20,backoff=0))
            self.assertEqual(len(calls),1);self.assertEqual(result['halt_reason'],'unsupported_request_or_model_blocked')

    def test_model_circuit_survives_resume_and_distinguishes_permission(self):
        cfg,manifest,jobs=inputs(cached_count=True)
        manifest['lifecycle_policy']={'retry_backoff_seconds':0,'maximum_count_request_bytes':65536}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);fake=FakeTransport(cfg['settings'],out,[])
            async def failure(*args,**kwargs):raise RuntimeError('openai-request-failed:PermissionDeniedError:403')
            fake.__class__=type('PermissionTransport',(FakeTransport,),{'__call__':failure})
            first=asyncio.run(m.execute(cfg,manifest,jobs,out,lambda *a:fake))
            self.assertEqual(first['halt_reason'],'permission_blocked');self.assertFalse(first['authentication_blocked'])
            second=asyncio.run(m.execute(cfg,manifest,jobs,out,lambda *a:fake))
            self.assertEqual(second['completion_attempts_total'],1)

    def test_no_combined_approval_precedes_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp);write(inp/'study-manifest.json',{});s.seal(inp)
            with self.assertRaisesRegex(ValueError,'combined-authorization-required'):s.admit(inp,{'approved':False})
