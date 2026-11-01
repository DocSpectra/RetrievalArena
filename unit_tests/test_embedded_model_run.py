import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_model_run as m
from retrieval_arena.embedded_link_audit import write, read, portable_inventory
from retrieval_arena.model_providers.openai import OpenAITransport
from retrieval_arena.model_providers.anthropic import AnthropicTransport
from retrieval_arena.model_providers.ledger import CostLedger

SCHEMA=e._obj({'ok':{'type':'boolean'}})


def settings():
    return {'provider':'openai','model':'gpt-6-luna','revision':'gpt-6-luna','temperature':None,'seed':None,
        'thinking':False,'generation':{'reasoning_effort':'low','strict':True},'max_input_tokens':16000,
        'max_output_tokens':2000,'pricing':{'currency':'USD','input_per_mtok':.1,'output_per_mtok':.5,
        'long_context':None,'source':'unit-test'},'spend_cap_usd':.05,'service_tier':'default'}


def inputs(n=1, cached_count=False):
    cfg={'role':'generation','settings':settings(),'remaining_inference_seconds':60}
    jobs=[];counts=[]
    for i in range(n):
        job={'source_id':'a','prompt':'Frozen request '+str(i),'schema':SCHEMA}
        if cached_count:
            body=OpenAITransport.request(job['prompt'],SCHEMA,cfg['settings'])
            count={'model':'gpt-6-luna','request_sha256':e.digest(body),'input_tokens':20};counts.append(count);job['provider_count']=count
        job['job_id']=e.digest(job);jobs.append(job)
    manifest={'roles':[{'role':'generation','settings':settings(),'initial_items_max':max(n,1),
        'attempts_max':2*max(n,1),'schema_sha256':e.digest(SCHEMA),'prompt_prefix':'Frozen request '}],
        'allowed_source_ids':['a'],'generation_counts':counts,'generation_jobs_sha256':e.digest(jobs),
        'provider_token_count_complete':True,'aggregate_hard_cap_usd':20}
    return cfg,manifest,jobs


class FakeTransport:
    request=staticmethod(OpenAITransport.request)
    def __init__(self,cfg,out,replies):
        self.cfg=cfg;self.replies=list(replies);self.calls=0
        self.ledger=CostLedger(out/'cost-ledger.jsonl',cfg['pricing'],cfg['spend_cap_usd'],provider=cfg['provider'],model=cfg['model'])
    async def __call__(self,prompt,schema,cfg,*,audit=None):
        self.calls+=1;body=self.request(prompt,schema,cfg)
        reservation=self.ledger.reserve(cfg['max_input_tokens']+1024,cfg['max_output_tokens'],e.digest(body))
        if audit:audit({'event':'request','body':body,'reservation':reservation})
        text=self.replies.pop(0)
        raw={'model':cfg['model'],'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}
        if audit:audit({'event':'response','body':raw,'request_id':'fake-unit-test'})
        usage={'input_tokens':20,'output_tokens':8};cost=self.ledger.report(reservation,usage,'fake-unit-test')
        return {'text':text,'model':cfg['model'],'revision':cfg['revision'],'usage':usage,'cost':cost}


class ModelRun(unittest.TestCase):
    def test_provider_usage_violation_stops_without_retry(self):
        cfg,manifest,jobs=inputs(cached_count=True);cfg['settings']['max_output_tokens']=2
        body=OpenAITransport.request(jobs[0]['prompt'],SCHEMA,cfg['settings'])
        jobs[0]['provider_count']['request_sha256']=e.digest(body)
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeTransport(cfg['settings'],Path(tmp),['{"ok":true}'])
            result=asyncio.run(m.execute(cfg,manifest,jobs,Path(tmp),lambda *a:fake))
            self.assertEqual(fake.calls,1);self.assertEqual(result['halt_reason'],'provider_budget_violation')
            self.assertEqual(result['results'][0]['status'],'provider_budget_violation')

    def test_native_openai_audit_retains_sdk_json_and_standard_tier(self):
        from unit_tests.test_model_providers import env,FAKE_KEY
        text='{"ok":true}';body={'model':'gpt-6-luna','output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}
        response=SimpleNamespace(model='gpt-6-luna',status='completed',id='fake',_request_id='fake',output_text=text,
            usage=SimpleNamespace(input_tokens=20,output_tokens=8),model_dump=lambda **kw:body)
        async def create(**kw):return response
        with tempfile.TemporaryDirectory() as tmp,env():
            cfg=settings();ledger=CostLedger(Path(tmp)/'cost.jsonl',cfg['pricing'],.05,provider='openai',model=cfg['model'])
            transport=OpenAITransport(ledger,'external_api',client=SimpleNamespace(responses=SimpleNamespace(create=create)),require_container=False)
            events=[];asyncio.run(transport('Test only',SCHEMA,cfg,audit=events.append))
            self.assertEqual(events[0]['body']['service_tier'],'default');self.assertEqual(events[1]['body'],body)
            self.assertNotIn(FAKE_KEY,e.canonical(events).decode())

    def test_native_anthropic_audit_retains_sdk_json_and_standard_tier(self):
        from unit_tests.test_model_providers import env,FAKE_KEY
        text='{"ok":false}';body={'model':'claude-haiku-5-5','content':[{'type':'text','text':text}]}
        response=SimpleNamespace(model='claude-haiku-5-5',stop_reason='end_turn',_request_id='fake',
            content=[SimpleNamespace(type='text',text=text)],usage=SimpleNamespace(input_tokens=20,output_tokens=8),
            model_dump=lambda **kw:body)
        async def create(**kw):return response
        with tempfile.TemporaryDirectory() as tmp,env():
            cfg=settings();cfg.update(provider='anthropic',model='claude-haiku-5-5',revision='claude-haiku-5-5',
                generation={'thinking':'disabled','effort':'low'},service_tier='standard_only')
            ledger=CostLedger(Path(tmp)/'cost.jsonl',cfg['pricing'],.05,provider='anthropic',model=cfg['model'])
            transport=AnthropicTransport(ledger,'external_api',client=SimpleNamespace(messages=SimpleNamespace(create=create)),
                transform=lambda x:x,require_container=False)
            events=[];asyncio.run(transport('Test only',SCHEMA,cfg,audit=events.append))
            self.assertEqual(events[0]['body']['service_tier'],'standard_only');self.assertEqual(events[1]['body'],body)
            self.assertNotIn(FAKE_KEY,e.canonical(events).decode())

    def test_success_and_cached_success_do_not_repeat_calls(self):
        cfg,manifest,jobs=inputs(cached_count=True)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);fake=FakeTransport(cfg['settings'],out,['{"ok":true}'])
            factory=lambda *args:fake
            async def no_count(*args):raise AssertionError('frozen count must be used')
            first=asyncio.run(m.execute(cfg,manifest,jobs,out,factory,no_count))
            reopened=FakeTransport(cfg['settings'],out,[])
            second=asyncio.run(m.execute(cfg,manifest,jobs,out,lambda *a:reopened,no_count))
            self.assertEqual(fake.calls,1);self.assertTrue(second['results'][0]['cache_reused'])
            self.assertGreater(first['spent_or_reserved_usd'],0)
            self.assertEqual(first['spent_or_reserved_usd'],second['spent_or_reserved_usd'])

    def test_malformed_response_retries_once_with_usage_retained(self):
        cfg,manifest,jobs=inputs()
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);fake=FakeTransport(cfg['settings'],out,['incomplete JSON','{"ok":false}'])
            async def count(*args):return {'input_tokens':20}
            result=asyncio.run(m.execute(cfg,manifest,jobs,out,lambda *a:fake,count))
            self.assertEqual(fake.calls,2);self.assertEqual(result['results'][0]['payload'],{'ok':False})
            self.assertTrue((out/'jobs'/jobs[0]['job_id']/'attempt-0/provider-response.json').exists())
            self.assertEqual(len([r for r in (out/'cost-ledger.jsonl').read_text().splitlines() if json.loads(r)['event']=='reported']),2)

    def test_over_cap_does_not_generate(self):
        cfg,manifest,jobs=inputs()
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeTransport(cfg['settings'],Path(tmp),[])
            async def count(*args):return {'input_tokens':16001}
            result=asyncio.run(m.execute(cfg,manifest,jobs,Path(tmp),lambda *a:fake,count))
            self.assertEqual(fake.calls,0);self.assertEqual(result['results'][0]['status'],'over_cap_unavailable')

    def test_authentication_failure_stops_other_jobs(self):
        class AuthenticationError(Exception):pass
        cfg,manifest,jobs=inputs(3)
        with tempfile.TemporaryDirectory() as tmp:
            fake=FakeTransport(cfg['settings'],Path(tmp),[]);counts=[]
            async def count(*args):counts.append(1);raise AuthenticationError()
            result=asyncio.run(m.execute(cfg,manifest,jobs,Path(tmp),lambda *a:fake,count))
            self.assertLessEqual(len(counts),2);self.assertEqual(fake.calls,0);self.assertTrue(result['authentication_blocked'])

    def test_false_authorization_and_missing_tokens_precede_client(self):
        cfg,manifest,jobs=inputs(cached_count=True)
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'in';out=Path(tmp)/'out';inp.mkdir();out.mkdir()
            write(inp/'config.json',cfg);write(inp/'study-manifest.json',manifest);write(inp/'jobs.json',jobs)
            inv=portable_inventory(inp);write(inp/'operator-request.json',{'input_inventory':inv,'input_inventory_sha256':e.digest(inv)})
            with patch.object(m,'transport_for',side_effect=AssertionError('no client')):
                with self.assertRaisesRegex(ValueError,'authorization-required'):asyncio.run(m.run(inp,out,{'approved':False}))
            manifest['provider_token_count_complete']=False;write(inp/'study-manifest.json',manifest)
            inv=[r for r in portable_inventory(inp) if r['path']!='operator-request.json'];write(inp/'operator-request.json',{'input_inventory':inv,'input_inventory_sha256':e.digest(inv)})
            with self.assertRaisesRegex(ValueError,'token-admission-required'):
                m.admit(inp,{'approved':True,'scope':'bounded_embedded_refinement_v2','study_manifest_sha256':e.digest(manifest)})

    def test_closed_schema_anyof_and_exact_quote(self):
        schema=e._obj({'confidence':{'anyOf':[{'type':'number','minimum':0,'maximum':1},{'type':'null'}]}})
        m.validate_json({'confidence':None},schema)
        for value in [-1,2,'high']:
            with self.assertRaises(ValueError):m.validate_json({'confidence':value},schema)
        m.evidence_valid([{'span':[2,7],'quote':'café'}],[{'raw_span':[2,7],'raw_excerpt':'café'}])
        with self.assertRaises(ValueError):m.evidence_valid([{'span':[2,7],'quote':'cafe'}],[{'raw_span':[2,7],'raw_excerpt':'café'}])

    def test_valid_but_tampered_cached_payload_fails(self):
        cfg,manifest,jobs=inputs(cached_count=True)
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);fake=FakeTransport(cfg['settings'],out,['{"ok":true}'])
            asyncio.run(m.execute(cfg,manifest,jobs,out,lambda *a:fake))
            target=out/'jobs'/jobs[0]['job_id']/'result.json';row=read(target);row['payload']={'ok':False};write(target,row)
            with self.assertRaisesRegex(ValueError,'cached-payload-response-binding'):
                asyncio.run(m.execute(cfg,manifest,jobs,out,lambda *a:fake))
            self.assertEqual(fake.calls,1)

    def test_empty_stage_opens_no_client(self):
        cfg,manifest,jobs=inputs(0)
        with tempfile.TemporaryDirectory() as tmp:
            def factory(*a):raise AssertionError('empty stage must not construct a client')
            result=asyncio.run(m.execute(cfg,manifest,jobs,Path(tmp),factory))
            self.assertEqual(result['completion_attempts_total'],0)

    def test_expired_overall_clock_opens_no_client_and_records_all_jobs(self):
        cfg,manifest,jobs=inputs(2);cfg['remaining_inference_seconds']=0
        with tempfile.TemporaryDirectory() as tmp:
            def factory(*a):raise AssertionError('expired study must not construct a client')
            result=asyncio.run(m.execute(cfg,manifest,jobs,Path(tmp),factory))
            self.assertEqual(len(result['results']),2)
            self.assertTrue(all(r['status']=='resource_limit' for r in result['results']))


if __name__=='__main__':unittest.main()
