"""End-to-end offline fixtures. All provider records here are synthetic fakes."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_refinement as f
from retrieval_arena.embedded_link_audit import read,write
from retrieval_arena.graph import block_phase as bp
from retrieval_arena.model_providers.openai import OpenAITransport
from unit_tests.test_embedded_model_run import settings


def proposal_schema():
    properties={k:copy.deepcopy(e.PROPOSAL_SCHEMA['properties'][k]) for k in ['source_id','target_id','source_span',
        'anchor','sentence','target_evidence','relation','stated_intent','expected_reading_use','basis']}
    properties['confidence']={'anyOf':[{'type':'number','minimum':0,'maximum':1},{'type':'null'}]}
    return e._obj({'proposals':{'type':'array','items':e._obj(properties),'maxItems':2}})


def review_schema():
    return e._obj({'relation':{'enum':list(e.RELATIONS)},**{k:{'type':'integer','minimum':0,'maximum':2}
        for k in ['relationship_support','target_support','placement']},'uncertainty':{'type':'boolean'},
        'contradiction':{'type':'boolean'},'source_evidence':{'type':'array','items':e.EVIDENCE,'minItems':1},
        'target_evidence':{'type':'array','items':e.EVIDENCE,'minItems':1},'assessment':e.S})


def fake_result(inp, job, payload, provider='openai'):
    text=e.canonical(payload).decode()
    body=({'model':'gpt-6-luna','output':[{'type':'message','content':[{'type':'output_text','text':text}]}]}
          if provider=='openai' else {'model':'claude-haiku-5-5','content':[{'type':'text','text':text}]})
    dest=inp/'model-output/jobs'/job['job_id']/'attempt-0/provider-response.json';dest.parent.mkdir(parents=True,exist_ok=True);write(dest,body)
    return {'job_id':job['job_id'],'status':'complete','payload':payload,'transport_result':{'text':text},
            'raw_response_file':'attempt-0/provider-response.json','raw_response_sha256':e.rawhash(dest.read_bytes()),
            'attempt':0,'completed_at_unix':1700000000}


class DirectScorer:
    """Small fixture kernel only; production watchdog is independently tested."""
    def __init__(self,graph,pool,policy):
        self.graph=graph;self.pool={p['proposal_id']:p for p in pool};self.policy=policy;self.cache={};self.deadline=time.monotonic()+600;self.attempts=[]
    def __call__(self,prefix):
        additions=[[self.pool[p]['source_id'],self.pool[p]['target_id']] for p in prefix]
        rows=[bp.compare(self.graph['nodes'],self.graph['edges'],additions,.25,'block',bp.lo.Policy(seed=seed),self.cache)
              for seed in [3501,3502]]
        return {'status':'finite_estimate','schedule_costs':[r['cost'] for r in rows],'schedules':rows}


def fixture(inp):
    pages=[];contents={};regions=[];packets=[];jobs=[];results=[]
    for g in range(4):
        nodes=[f'g{g}-{i}' for i in range(6)]
        for i,node in enumerate(nodes):
            text=(f'# Page {i}\n\n'+('Review validation rules.\n\nRead the release checklist.\n\n' if i==0 else
                 'Validation rules define the source check.\n\n' if i==2 else
                 'The release checklist completes this workflow.\n\n' if i==5 else 'A plain workflow step.\n\n'))
            if i<5:text+=f'Continue the workflow with [the next step]({i+1}.md).\n\n[Next]({i+1}.md)\n'
            if i>0:text+=f'\n[Previous]({i-1}.md)\n'
            raw=text.encode();path=f'group{g}/{i}.md';p={'doc_id':node,'path':path,'raw_sha256':e.rawhash(raw)};pages.append(p);contents[node]=raw
            target=inp/'corpus'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        region={'region_id':f'group:{g}','assignment':'development','nodes':nodes,'halo_nodes':[],
            'component_membership':{n:'component-'+str(g) for n in nodes},'source_families':[f'fixture-{g}'],
            'corpus_id':'synthetic-unit-fixture','snapshot_id':'fake-sdk-fixture'};regions.append(region)
        def para(n,quote):
            raw=contents[n];a=raw.index(quote.encode());return {'text':quote,'raw_span':[a,a+len(quote.encode())]}
        packet={'source_id':nodes[0],'source_path':f'group{g}/0.md','source_sha256':e.rawhash(contents[nodes[0]]),
            'region_id':region['region_id'],'source_paragraphs':[para(nodes[0],'Review validation rules.'),para(nodes[0],'Read the release checklist.')],
            'targets':[{'target_id':nodes[2],'paragraphs':[para(nodes[2],'Validation rules define the source check.')]},
                       {'target_id':nodes[5],'paragraphs':[para(nodes[5],'The release checklist completes this workflow.')]}]}
        packet['packet_sha256']=e.digest(packet);packets.append(packet)
        job={'source_id':nodes[0],'packet_sha256':packet['packet_sha256'],'schema':proposal_schema(),'prompt':'fake generation only'}
        job['job_id']=e.digest(job);jobs.append(job)
        if g==2:results.append({'job_id':job['job_id'],'status':'technical_unavailable'});continue
        items=[]
        for target,anchor,source_sentence,target_quote in [(2,'validation rules','Review validation rules.','Validation rules define the source check.'),
                                                          (5,'release checklist','Read the release checklist.','The release checklist completes this workflow.')][:2 if g==0 else 1]:
            raw=contents[nodes[0]];a=raw.index(anchor.encode());traw=contents[nodes[target]];t=traw.index(target_quote.encode())
            items.append({'source_id':nodes[0],'target_id':nodes[target],'source_span':[a,a+len(anchor)],'anchor':anchor,
                'sentence':source_sentence,'target_evidence':[{'span':[t,t+len(target_quote)],'quote':target_quote}],
                'relation':'target_elaborates_source','stated_intent':'Synthetic fixture assertion, no live provider.',
                'expected_reading_use':'Unit fixture only.','basis':'supported_inference','confidence':None})
        results.append(fake_result(inp,job,{'proposals':items}))
    mapping={p['path']:p for p in pages}
    occurrences=[r for p in pages for r in e.occurrences(contents[p['doc_id']],p,mapping)]
    manifest={'roles':[{'role':role,'settings':{'provider':provider,'model':model},'schema':schema,'schema_sha256':e.digest(schema),
        'prompt_prefix':'Synthetic fake '+role,'prompt_sha256':e.digest(role)} for role,provider,model,schema in [
        ('generation','openai','gpt-6-luna',proposal_schema()),('eligibility','anthropic','claude-haiku-5-5',review_schema()),
        ('blinded_assessment','openai','gpt-6.1-sol',review_schema())]]}
    for name,value in [('pages.json',pages),('regions.json',regions),('packets.json',packets),('study-manifest.json',manifest),
        ('original.json',{'nodes':sorted(contents),'edges':e.project(occurrences,set(contents),'original')}),
        ('frozen-occurrences.json',occurrences),('model-jobs.json',jobs),('model-summary.json',{'results':results})]:write(inp/name,value)
    return manifest,contents


class Refinement(unittest.TestCase):
    def test_group_views_exclude_cross_group_incoming_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'input';inp.mkdir();fixture(inp)
            out=Path(tmp)/'output';out.mkdir();graphs=[]
            rows=read(inp/'frozen-occurrences.json')
            rows.append({'source_id':'g1-0','target_id':'g0-0','class':'substantive'})
            def scorer(graph,pool,policy):
                graphs.append(graph)
                return DirectScorer(graph,pool,policy)
            groups=f.select_groups(inp,out,[],read(inp/'regions.json'),rows,
                scorer_factory=scorer,with_diagnostics=False)
            self.assertEqual(len(groups),4)
            for graph in graphs:
                self.assertTrue(all(s in graph['nodes'] and t in graph['nodes'] for s,t in graph['edges']))
                self.assertNotIn(('g1-0','g0-0'),graph['edges'])

    def test_any_model_authentication_block_refuses_semantic_interpretation(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp);write(inp/'model-summary.json',{'authentication_blocked':True,'results':[]})
            for method,args in [(f.proposals,(inp,inp,{})),(f.eligible_records,(inp,)),(f.assemble,(inp,inp,{}))]:
                with self.assertRaisesRegex(ValueError,'authentication-blocked'):method(*args)

    def test_proposer_abstention_is_not_rescued_by_an_independent_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'in';out=Path(tmp)/'out';inp.mkdir();out.mkdir();fixture(inp)
            rows=read(inp/'model-summary.json');job=read(inp/'model-jobs.json')[0]
            payload=rows['results'][0]['payload'];payload['proposals'][0]['relation']='insufficient_evidence'
            rows['results'][0]=fake_result(inp,job,payload);write(inp/'model-summary.json',rows)
            f.proposals(inp,out,{'run_id':'fake-unit-run'})
            self.assertEqual(sum(r['status']=='proposer_rejected' for r in read(out/'records.json')),1)
            self.assertEqual(len(read(out/'review-jobs.json')),3)

    def test_token_authentication_failure_cannot_become_empty_real_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'in';out=Path(tmp)/'out';inp.mkdir();out.mkdir();fixture(inp)
            (inp/'token-input').mkdir();write(inp/'token-input/jobs.json',[{'job_id':e.digest({}),'body':{}}])
            token=inp/'token-output';token.mkdir();write(token/'token-admission.json',{'results':[
                {'job_id':e.digest({}),'status':'technical_unavailable','attempts':[{'error_class':'AuthenticationError'}]}]})
            from retrieval_arena.embedded_link_audit import portable_inventory
            inv=portable_inventory(token);write(token/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
            with self.assertRaisesRegex(ValueError,'authentication-blocked'):f.prepare_generation(inp,out,{})

    def test_token_admission_binds_exact_body_and_retained_target_subset(self):
        from retrieval_arena.embedded_link_audit import portable_inventory
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'in';out=Path(tmp)/'out';inp.mkdir();out.mkdir();study,contents=fixture(inp)
            role=study['roles'][0];role['settings']=settings();write(inp/'study-manifest.json',study)
            packet=read(inp/'packets.json')[0]
            def passage(p,node):
                a,z=p['raw_span'];return {'raw_span':[a,z],'raw_excerpt':contents[node][a:z].decode(),'semantic_text':p['text']}
            data={'source_id':packet['source_id'],'source_paragraphs':[passage(p,packet['source_id']) for p in packet['source_paragraphs']],
                'targets':[{'target_id':t['target_id'],'paragraphs':[passage(p,t['target_id']) for p in t['paragraphs']]} for t in packet['targets']]}
            full=OpenAITransport.request(role['prompt_prefix']+f.MARKER+e.canonical(data).decode(),role['schema'],role['settings'])
            truncated=copy.deepcopy(data);truncated['targets']=truncated['targets'][:1]
            actual=OpenAITransport.request(role['prompt_prefix']+f.MARKER+e.canonical(truncated).decode(),role['schema'],role['settings'])
            (inp/'token-input').mkdir();write(inp/'token-input/jobs.json',[{'job_id':e.digest(full),'body':full}])
            token=inp/'token-output';(token/'counts').mkdir(parents=True);(token/'admitted').mkdir()
            request_hash=e.digest(actual);write(token/'admitted'/(request_hash+'.json'),actual)
            attempt={'attempt':1,'status':'counted','request_sha256':request_hash}
            write(token/'counts/0001.json',{'attempt':attempt,'raw_response':{'input_tokens':100}})
            write(token/'token-admission.json',{'results':[{'job_id':e.digest(full),'status':'admitted',
                'request_sha256':request_hash,'provider_input_tokens':100,'attempts':[attempt]}]})
            inv=portable_inventory(token);write(token/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
            result=f.prepare_generation(inp,out,{})
            self.assertEqual(result['admitted_packets'],1)
            new=read(out/'packets.json')[0];self.assertEqual(len(new['targets']),1)
            self.assertEqual(new['parent_packet_sha256'],packet['packet_sha256'])
            self.assertTrue(read(out/'study-manifest.json')['provider_token_count_complete'])

    def test_complete_fake_provider_to_patches_and_blinded_union(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'input';inp.mkdir();manifest,contents=fixture(inp);proposal_out=Path(tmp)/'proposals';proposal_out.mkdir()
            first=f.proposals(inp,proposal_out,{'run_id':'fake-unit-run'})
            self.assertEqual(first['structural_eligible'],4);self.assertEqual(first['generation_unavailable'],1)
            records=read(proposal_out/'records.json');write(inp/'records.json',records)
            review_jobs=read(proposal_out/'review-jobs.json');write(inp/'model-jobs.json',review_jobs)
            reviews=[];by_id={r['proposal']['proposal_id']:r for r in records if 'proposal' in r}
            for job in review_jobs:
                record=by_id[job['proposal_ids'][0]];p=record['proposal'];weak=p['region_id']=='group:3'
                source=job['evidence']['source'][0];quote=p['sentence'];start=source['raw_span'][0]+source['raw_excerpt'].encode().index(quote.encode())
                payload={'relation':'target_elaborates_source','relationship_support':1 if weak else 2,
                    'target_support':1 if weak else 2,'placement':2,'uncertainty':False,'contradiction':False,
                    'source_evidence':[{'span':[start,start+len(quote)],'quote':quote}],
                    'target_evidence':p['target_evidence'],'assessment':'Synthetic independent fake verdict.'}
                reviews.append(fake_result(inp,job,payload,'anthropic'))
            write(inp/'model-summary.json',{'results':reviews});out=Path(tmp)/'selected';out.mkdir()
            original=f.select_groups
            with patch.object(f,'select_groups',side_effect=lambda *args:original(*args,scorer_factory=DirectScorer,with_diagnostics=False)):
                selected=f.select_and_apply(inp,out,{})
            self.assertEqual([g['feasible_budget'] for g in selected['groups']],[1,1,0,0])
            self.assertEqual([a['applied'] for a in selected['applications']],[2,2,2])
            g=selected['groups'][0];chosen=g['arms']['M']['selected'][0]
            self.assertEqual(by_id[chosen]['proposal']['target_id'],'g0-5')
            for arm in ['S','Z','M']:
                self.assertTrue(read(out/arm/'trusted-tip.json')['replay']['unique_events']>0)
                self.assertEqual(read(out/arm/'graph-deltas.json')['removed_pairs'],[])
            self.assertEqual(len(read(out/'case-card-inventory.json')),6)
            for job in read(out/'assessment-jobs.json'):
                data=json.loads(job['prompt'].split(f.MARKER,1)[1])
                self.assertFalse({'arm','score','relation','stated_intent','verdict','prior_assessment'}&data.keys())
            self.assertLessEqual(selected['unique_final_assessment_jobs'],3)

    def test_tampered_source_is_refused_before_pool_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp);fixture(inp);(inp/'corpus/group0/0.md').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'immutable-source'):f.load_sources(inp)

    def test_provider_payload_cannot_drift_from_retained_raw_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp);fixture(inp);row=read(inp/'model-summary.json')['results'][0]
            row['payload']['proposals'][0]['anchor']='a different anchor'
            with self.assertRaisesRegex(ValueError,'provider-payload-binding'):f.provider_result(inp,row)


if __name__=='__main__':unittest.main()
