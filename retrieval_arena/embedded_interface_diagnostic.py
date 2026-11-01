"""Offline saved-response diagnosis. Never changes historical eligibility."""
import json
from . import embedded_interface as i
from . import embedded_links as e
from .embedded_link_audit import read, write


def diagnose(inp, out):
    records=read(inp/'records.json');packets={p['packet_sha256']:p for p in read(inp/'packets.json')}
    pages=read(inp/'pages.json');raw={p['doc_id']:(inp/'corpus'/p['path']).read_bytes() for p in pages}
    jobs={j['job_id']:j for j in read(inp/'generation-jobs.json')};rows=[]
    for record in records:
        item=record['raw_item'];packet=packets[record['packet_sha256']];data=i.disclosure(packet,raw)
        source=raw[item['source_id']];a,z=item['source_span']
        candidates=[]
        for p in data['source_paragraphs']:
            for sentence in p['sentences']:
                matches=i.exact_matches(sentence['text'],item['anchor'])
                for at in matches:
                    mapping=sentence['raw_byte_map'][at:at+len(item['anchor'])]
                    candidates.append({'sentence_id':sentence['sentence_id'],'text':sentence['text'],
                        'span':[mapping[0][0],mapping[-1][1]],'matches_in_sentence':len(matches)})
        target=next(t for t in data['targets'] if t['target_id']==item['target_id'])
        targets=[]
        for quote in item['target_evidence']:
            matches=[]
            for p in target['paragraphs']:
                # Historical quotes can be raw Markdown or display text; report
                # both exact policies, without normalizing either one.
                matches.append({'passage_id':p['passage_id'],
                    'display_matches':len(i.exact_matches(p['text'],quote['quote'])),
                    'raw_matches':len(i.exact_matches(p['raw_excerpt'],quote['quote']))})
            targets.append({'returned':quote,'passages':matches})
        categories=[]
        if source[a:z].decode(errors='replace')!=item['anchor']:
            categories.append('wrong_offsets_identifiable_text' if candidates else 'anchor_text_absent_from_disclosure')
        if len(candidates)>1 or any(c['matches_in_sentence']>1 for c in candidates):categories.append('ambiguous_repeated_text')
        if candidates and not any(c['text']==item['sentence'] for c in candidates):categories.append('sentence_mismatch')
        if any(c in item['anchor'] for c in '\\`[]<>*_\r\n'):categories.append('unsupported_anchor_syntax')
        if any(not any(p['raw_matches'] or p['display_matches'] for p in q['passages']) for q in targets):
            categories.append('target_quote_absent_from_disclosure')
        rows.append({'job_id':record['job_id'],'ordinal':record['ordinal'],'source_id':item['source_id'],
            'anchor':item['anchor'],'historical_status':record['status'],'historical_reason':record.get('reason'),
            'categories':categories,'exact_source_candidates':candidates,'target_evidence':targets,
            'counterfactual_only':True,'semantic_validity':'not inferred'})
    empties=[]
    for result in read(inp/'generation-summary.json')['results']:
        if result['status']=='complete' and result['payload']['proposals']==[]:
            empties.append({'job_id':result['job_id'],'source_id':jobs[result['job_id']]['source_id'],
                'recorded_explanation':None,'note':'Schema contains only proposals; no reason was recorded.'})
    reviews=[]
    for file in sorted((inp/'review-model-output/jobs').glob('*/attempt-*/transport-result.json')):
        response=read(file);payload=json.loads(response['text']);job=read(inp/'review-jobs.json')
        job=next(j for j in job if j['job_id']==file.parent.parent.name)
        checks=[]
        for role in ('source','target'):
            for q in payload[role+'_evidence']:
                matches=[]
                for p in job['evidence'][role]:
                    matches.extend(p['raw_span'][0]+at for at in i.exact_matches(p['raw_excerpt'],q['quote']))
                checks.append({'role':role,'returned':q,'exact_disclosed_raw_positions':matches})
        from .embedded_model_run import validate_result
        try:
            validate_result(payload,job);historical_error=None
        except ValueError as exc:historical_error=str(exc)
        reviews.append({'file':file.relative_to(inp).as_posix(),'response_sha256':e.rawhash(file.read_bytes()),
            'payload':payload,'exact_checks':checks,
            'historical_validation_error':historical_error,
            'disposition':'substantive_negative' if historical_error is None else 'unavailable_exact_evidence_failure',
            'quotes_identifiable_in_disclosure':all(c['exact_disclosed_raw_positions'] for c in checks)})
    result={'schema_version':i.VERSION+'.diagnosis','historical_verdicts_changed':False,
        'proposals':rows,'empty_responses':empties,'review_attempts':reviews,'provider_calls':0}
    write(out/'diagnosis.json',result)
    return result
