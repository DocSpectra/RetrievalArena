"""Offline interface fixtures. Synthetic responses never count as model results."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from retrieval_arena import embedded_interface as i
from retrieval_arena import embedded_links as e
from retrieval_arena import embedded_refinement as f
from retrieval_arena.embedded_demonstrator import paragraphs
from retrieval_arena.embedded_link_audit import read,write
from retrieval_arena.embedded_model_run import validate_result
from retrieval_arena.embedded_failures import classify
from unit_tests.test_embedded_refinement import fixture,fake_result,DirectScorer


def example(source='Read the validation rules.\n',target='These `validation` rules apply.\n'):
    raw={'a':source.encode(),'b':target.encode()}
    sp,_=paragraphs(raw['a'],source=True);tp,_=paragraphs(raw['b'])
    packet={'source_id':'a','source_path':'a.md','source_sha256':e.rawhash(raw['a']),
        'source_paragraphs':sp,'targets':[{'target_id':'b','path':'b.md','title':'B',
            'source_sha256':e.rawhash(raw['b']),'paragraphs':tp}]}
    data=i.disclosure(packet,raw);sentence=data['source_paragraphs'][0]['sentences'][0]
    item={'source_id':'a','target_id':'b','source_sentence_id':sentence['sentence_id'],
        'anchor':'validation rules','target_evidence':[{'passage_id':data['targets'][0]['paragraphs'][0]['passage_id'],
        'quote':'validation rules'}],'relation':'target_elaborates_source','stated_intent':'Synthetic mock.',
        'expected_reading_use':'Technical fixture only.','basis':'supported_inference','confidence':None}
    return packet,raw,data,item


class Interface(unittest.TestCase):
    def test_faithful_saved_declarations_and_review_regressions(self):
        saved=read(Path(__file__).parent/'fixtures/embedded_interface_saved_responses.json')
        self.assertEqual(len(saved['generation']),8);self.assertEqual(len(saved['reviews']),3)
        for case in saved['generation']:
            item=case['raw_item'];spans=[]
            for p in case['disclosed_source_passages']:
                for at in i.exact_matches(p['raw_excerpt'],item['anchor']):
                    a=p['raw_span'][0]+len(p['raw_excerpt'][:at].encode())
                    spans.append([a,a+len(item['anchor'].encode())])
            self.assertEqual(sorted(spans),sorted(case['expected_source_candidate_spans']))
            if 'wrong_offsets_identifiable_text' in case['expected_diagnostic_categories']:
                self.assertNotIn(item['source_span'],spans)
        for case in saved['reviews']:
            if case['expected_historical_error']:
                with self.assertRaisesRegex(ValueError,case['expected_historical_error']):validate_result(case['payload'],case['job'])
            else:validate_result(case['payload'],case['job'])
            for role in ('source','target'):
                for q in case['payload'][role+'_evidence']:
                    self.assertTrue(any(q['quote'] in p['raw_excerpt'] for p in case['job']['evidence'][role]))

    def test_unique_anchor_and_markdown_evidence_projection(self):
        packet,raw,data,item=example();p,proof=i.resolve_proposal(item,data,raw)
        self.assertEqual(raw['a'][slice(*p['source_span'])],b'validation rules')
        self.assertEqual(p['target_evidence'][0]['quote'],'validation` rules')
        self.assertEqual(proof['target_evidence'][0]['returned_quote'],'validation rules')
        self.assertEqual(p['sentence'],'Read the validation rules.')
        self.assertNotIn('raw_byte_map',json.dumps(i.public_disclosure(data)))

    def test_unicode_crlf_exact_bytes_and_host_sentence(self):
        packet,raw,data,item=example('Café first. Read the validation rules.\r\n','These validation rules apply.\r\n')
        sentence=next(s for s in data['source_paragraphs'][0]['sentences'] if 'validation' in s['text'])
        item['source_sentence_id']=sentence['sentence_id'];p,_=i.resolve_proposal(item,data,raw)
        self.assertEqual(p['sentence'],' Read the validation rules.')
        self.assertEqual(p['source_span'][0],raw['a'].index(b'validation'))

    def test_repeated_anchor_is_refused_without_occurrence_id(self):
        packet,raw,data,item=example('Read validation rules and validation rules.\n')
        with self.assertRaisesRegex(ValueError,'ambiguous'):i.resolve_proposal(item,data,raw)

    def test_sentence_id_disambiguates_across_sentences(self):
        packet,raw,data,item=example('Read validation rules. Use validation rules.\n')
        item['source_sentence_id']=data['source_paragraphs'][0]['sentences'][1]['sentence_id']
        p,_=i.resolve_proposal(item,data,raw)
        self.assertEqual(p['source_span'][0],raw['a'].rindex(b'validation'))

    def test_repeated_quote_is_refused(self):
        packet,raw,data,item=example(target='validation rules and validation rules.\n')
        with self.assertRaisesRegex(ValueError,'ambiguous'):i.resolve_proposal(item,data,raw)

    def test_missing_or_stale_sentence_id(self):
        packet,raw,data,item=example();item['source_sentence_id']='invented'
        with self.assertRaisesRegex(ValueError,'sentence-id'):i.resolve_proposal(item,data,raw)

    def test_source_hash_change_invalidates_ids_and_application(self):
        packet,raw,data,item=example();raw['a']+=b'\n'
        with self.assertRaisesRegex(ValueError,'stale'):i.resolve_proposal(item,data,raw)

    def test_cross_document_same_text_and_role_collision(self):
        packet,raw,data,item=example(target='Read the validation rules.\n')
        source=data['source_paragraphs'][0];target=data['targets'][0]['paragraphs'][0]
        self.assertNotEqual(source['passage_id'],target['passage_id'])
        item['target_evidence'][0]['passage_id']=source['passage_id']
        with self.assertRaisesRegex(ValueError,'cross-document'):i.resolve_proposal(item,data,raw)

    def test_invented_and_undisclosed_quotes_are_refused(self):
        for quote in ['invented text','validation rule','VALIDATION rules']:
            packet,raw,data,item=example();item['target_evidence'][0]['quote']=quote
            if quote=='validation rule':continue  # Exact substrings are allowed; semantics are independent.
            with self.assertRaisesRegex(ValueError,'absent'):i.resolve_proposal(item,data,raw)

    def test_review_source_target_binding_and_valid_negative(self):
        packet,raw,data,item=example();p,_=i.resolve_proposal(item,data,raw)
        public,evidence=i.review_disclosure(p,packet,raw)
        payload={'relation':'insufficient_evidence','relationship_support':0,'target_support':1,'placement':1,
            'uncertainty':True,'contradiction':False,'assessment':'Synthetic negative.',
            'source_evidence':[{'passage_id':evidence['source'][0]['passage_id'],'quote':'validation rules'}],
            'target_evidence':[{'passage_id':evidence['target'][0]['passage_id'],'quote':'validation rules'}]}
        validate_result(payload,{'schema':i.review_schema(),'evidence':evidence})
        resolved,proof=i.resolve_review(payload,evidence)
        self.assertEqual(resolved['relationship_support'],0)
        self.assertEqual(resolved['target_evidence'][0]['quote'],'validation` rules')
        payload['source_evidence'][0]['passage_id']=evidence['target'][0]['passage_id']
        with self.assertRaisesRegex(ValueError,'cross-document'):validate_result(payload,{'schema':i.review_schema(),'evidence':evidence})

    def test_events_replay_and_tamper(self):
        log=[];i.event(log,'model_declared',{'mock':True},'model')
        i.event(log,'host_rejected',{'reason':'ambiguous'},'host')
        self.assertEqual(i.replay(log,e.digest(log[-1]))['events'],2)
        log[0]['payload']['mock']=False
        with self.assertRaisesRegex(ValueError,'chain'):i.replay(log,e.digest(log[-1]))

    def test_failure_kinds_are_distinct_and_retry_closed(self):
        self.assertEqual(classify(RuntimeError('openai-request-failed:AuthenticationError:401')),('authentication',False,True))
        self.assertEqual(classify(RuntimeError('anthropic-request-failed:PermissionDeniedError:403')),('permission',False,True))
        self.assertEqual(classify(RuntimeError('openai-request-failed:BadRequestError:400')),('unsupported_request_or_model',False,True))
        self.assertEqual(classify(TimeoutError()),('timeout',True,False))

    def test_complete_new_schema_to_edit_review_selection_and_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'input';inp.mkdir();manifest,contents=fixture(inp)
            manifest['interface_version']=i.VERSION
            for role in manifest['roles']:
                role['schema']=i.proposal_schema() if role['role']=='generation' else i.review_schema()
                role['schema_sha256']=e.digest(role['schema'])
            write(inp/'study-manifest.json',manifest)
            packets={p['packet_sha256']:p for p in read(inp/'packets.json')};jobs=[];results=[]
            old_jobs={j['job_id']:j for j in read(inp/'model-jobs.json')}
            for result in read(inp/'model-summary.json')['results']:
                old=old_jobs[result['job_id']];packet=packets[old['packet_sha256']]
                data=i.disclosure(packet,contents);job={**old,'schema':i.proposal_schema()};job.pop('job_id')
                job['job_id']=e.digest(job);jobs.append(job)
                if result['status']!='complete':results.append({'job_id':job['job_id'],'status':result['status']});continue
                items=[]
                for item in result['payload']['proposals']:
                    sentence=next(s for p in data['source_paragraphs'] for s in p['sentences'] if item['anchor'] in s['text'])
                    target=next(t for t in data['targets'] if t['target_id']==item['target_id'])
                    quote=item['target_evidence'][0]['quote']
                    item={k:v for k,v in item.items() if k not in ('source_span','sentence','target_evidence')}
                    item.update(source_sentence_id=sentence['sentence_id'],target_evidence=[{'passage_id':target['paragraphs'][0]['passage_id'],'quote':quote}]);items.append(item)
                results.append(fake_result(inp,job,{'proposals':items}))
            write(inp/'model-jobs.json',jobs);write(inp/'model-summary.json',{'results':results})
            proposal_out=Path(tmp)/'proposed';proposal_out.mkdir();f.proposals(inp,proposal_out,{'run_id':'mock-interface'})
            records=read(proposal_out/'records.json');self.assertEqual(sum(r['status']=='structural_eligible' for r in records),4)
            write(inp/'records.json',records);reviews=[];review_jobs=read(proposal_out/'review-jobs.json')
            for job in review_jobs:
                negative=job['source_id']=='g3-0';source=job['evidence']['source'][0];target=job['evidence']['target'][0]
                payload={'relation':'related_context' if negative else 'target_elaborates_source',
                    'relationship_support':1 if negative else 2,'target_support':1 if negative else 2,'placement':2,
                    'uncertainty':negative,'contradiction':False,'assessment':'Mock reference judgment.',
                    'source_evidence':[{'passage_id':source['passage_id'],'quote':source['text']}],
                    'target_evidence':[{'passage_id':target['passage_id'],'quote':target['text']}]}
                reviews.append(fake_result(inp,job,payload,'anthropic'))
            write(inp/'model-jobs.json',review_jobs);write(inp/'model-summary.json',{'results':reviews})
            out=Path(tmp)/'selected';out.mkdir();original=f.select_groups
            with patch.object(f,'select_groups',side_effect=lambda *args:original(*args,scorer_factory=DirectScorer,with_diagnostics=False)):
                selection=f.select_and_apply(inp,out,{})
            self.assertEqual([g['feasible_budget'] for g in selection['groups']],[1,1,0,0])
            self.assertEqual([a['applied'] for a in selection['applications']],[2,2,2])
            self.assertNotEqual(selection['groups'][0]['arms']['S']['selected'],selection['groups'][0]['arms']['M']['selected'])
            final_jobs=read(out/'assessment-jobs.json');write(inp/'model-jobs.json',final_jobs)
            finals=[]
            for job in final_jobs:
                source=job['evidence']['source'][0];target=job['evidence']['target'][0]
                payload={'relation':'target_elaborates_source','relationship_support':2,'target_support':2,'placement':2,
                    'uncertainty':False,'contradiction':False,'assessment':'Mock final.',
                    'source_evidence':[{'passage_id':source['passage_id'],'quote':source['text']}],
                    'target_evidence':[{'passage_id':target['passage_id'],'quote':target['text']}]}
                finals.append(fake_result(inp,job,payload))
            write(inp/'model-summary.json',{'results':finals});write(inp/'groups.json',selection['groups'])
            final_out=Path(tmp)/'final';final_out.mkdir();assembled=f.assemble(inp,final_out,{})
            self.assertEqual(len(assembled['assessments']),3)
            self.assertTrue(all(r['score']==6 for r in assembled['assessments']))
            for arm in ('S','Z','M'):
                self.assertTrue(read(out/arm/'trusted-tip.json')['replay']['unique_events']>0)
