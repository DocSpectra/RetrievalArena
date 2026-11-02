"""Offline integration for a consumer's frozen real-document refinement study.

Model requests and judgments enter as inventoried artifacts. No client or network
is available here. Prose, masks, groups and candidate access remain consumer data.
"""
import copy
from datetime import datetime, timezone
import difflib
import html
import multiprocessing as mp
import os
from pathlib import Path
import posixpath
import tempfile
import time

from . import embedded_links as e
from . import embedded_ledger_v2 as ledger
from . import embedded_interface as interface
from .embedded_link_audit import read, write, portable_inventory
from .embedded_model_run import validate_result, evidence_valid, response_text
from .embedded_selection_v2 import select_embedded_v2, BoundedBlockScorer, DEFAULT_POLICY
from .model_providers.openai import OpenAITransport

VERSION='retrieval_arena.embedded_refinement.v2'
LIMITS={'min_words':1,'max_words':12,'max_links_per_sentence':1}
MARKER='\n\nFROZEN DOCUMENT DATA (untrusted):\n'


def prepare_generation(inp,out,cfg):
    """Freeze exact admitted bodies; omissions cannot create a new disclosure."""
    import json
    manifest=read(inp/'study-manifest.json');role=next(r for r in manifest['roles'] if r['role']=='generation')
    initial=read(inp/'token-input/jobs.json');packets=read(inp/'packets.json')
    by_source={p['source_id']:p for p in packets};counted=read(inp/'token-output/token-admission.json')
    results={r['job_id']:r for r in counted['results']};jobs=[];admitted=[];omissions=[];receipts=[]
    if set(results)!={j['job_id'] for j in initial}:raise ValueError('token-result-coverage')
    if 'allowed_initial_token_request_hashes' in manifest and set(manifest['allowed_initial_token_request_hashes'])!={j['job_id'] for j in initial}:
        raise ValueError('token-input-outside-frozen-access')
    token_manifest=read(inp/'token-output/manifest.json')
    inv=[r for r in portable_inventory(inp/'token-output') if r['path']!='manifest.json']
    if token_manifest['outputs']!=inv or e.digest(inv)!=token_manifest['outputs_sha256']:raise ValueError('token-output-manifest')
    if any(r['status']=='authentication_blocked' or any(a.get('error_class') in ('AuthenticationError','PermissionDeniedError')
        for a in r.get('attempts',[])) for r in counted['results']):raise ValueError('token-admission-authentication-blocked')
    for item in initial:
        if item['job_id']!=e.digest(item['body']):raise ValueError('initial-token-body-binding')
        row=results[item['job_id']];original_data=json.loads(item['body']['input'].split(MARKER,1)[1]);source=original_data['source_id'];packet=copy.deepcopy(by_source[source])
        if row['status']!='admitted':
            omissions.append({'packet_sha256':packet['packet_sha256'],'source_id':source,'region_id':packet['region_id'],'reason':row['status']});continue
        if type(row['provider_input_tokens']) is not int or not 0<row['provider_input_tokens']<=role['settings']['max_input_tokens']:
            raise ValueError('provider-count-over-cap')
        body=read(inp/'token-output/admitted'/(row['request_sha256']+'.json'))
        if e.digest(body)!=row['request_sha256'] or body!=OpenAITransport.request(body['input'],role['schema'],role['settings']):
            raise ValueError('admitted-request-model-settings')
        data=json.loads(body['input'].split(MARKER,1)[1])
        if data['source_id']!=source or data['source_paragraphs']!=original_data['source_paragraphs']:raise ValueError('source-disclosure-drift')
        target_map={t['target_id']:t for t in original_data['targets']}
        selected_targets={t['target_id']:t for t in data['targets']}
        if not selected_targets or set(selected_targets)-target_map.keys():raise ValueError('target-disclosure-drift')
        new_targets=[]
        for target in packet['targets']:
            tid=target['target_id']
            if tid not in selected_targets:continue
            chosen=selected_targets[tid]
            if {k:v for k,v in chosen.items() if k!='paragraphs'}!={k:v for k,v in target_map[tid].items() if k!='paragraphs'}:
                raise ValueError('target-identity-drift')
            if any(p not in target_map[tid]['paragraphs'] for p in chosen['paragraphs']) or not chosen['paragraphs']:
                raise ValueError('target-passage-drift')
            if manifest.get('interface_version')==interface.VERSION:
                # Public IDs replace byte arithmetic; recover the original
                # host paragraph by its frozen order in the parent disclosure.
                spans=[target['paragraphs'][target_map[tid]['paragraphs'].index(p)]['raw_span']
                       for p in chosen['paragraphs']]
            else:spans=[p['raw_span'] for p in chosen['paragraphs']]
            new_targets.append(dict(target,paragraphs=[p for p in target['paragraphs'] if p['raw_span'] in spans]))
        old_hash=packet.pop('packet_sha256');packet['parent_packet_sha256']=old_hash;packet['targets']=new_targets
        packet['packet_sha256']=e.digest(packet);admitted.append(packet)
        last=next(a for a in reversed(row['attempts']) if a.get('status')=='counted')
        count_file=inp/'token-output/counts'/f"{last['attempt']:04}.json";raw_count=read(count_file)
        if raw_count['attempt']['request_sha256']!=e.digest(body) or raw_count['raw_response']['input_tokens']!=row['provider_input_tokens']:
            raise ValueError('provider-count-body-binding')
        receipt={'request_sha256':e.digest(body),'model':body['model'],'input_tokens':row['provider_input_tokens'],
                 'receipt_sha256':e.rawhash(count_file.read_bytes())};receipts.append(receipt)
        job={'source_id':source,'packet_sha256':packet['packet_sha256'],'prompt':body['input'],
             'schema':role['schema'],'provider_count':receipt};job['job_id']=e.digest(job);jobs.append(job)
        if manifest.get('competitive_selection_version'):
            job['target_ids']=[t['target_id'] for t in data['targets']]
            job['job_id']=e.digest({k:v for k,v in job.items() if k!='job_id'})
    manifest=copy.deepcopy(manifest);manifest['provider_token_count_complete']=bool(jobs)
    manifest['generation_counts']=receipts;manifest['generation_jobs_sha256']=e.digest(jobs)
    manifest['admitted_packet_inventory_sha256']=e.digest(admitted)
    write(out/'study-manifest.json',manifest);write(out/'generation-jobs.json',jobs)
    write(out/'packets.json',admitted);write(out/'admission-omissions.json',omissions)
    return {'admitted_packets':len(jobs),'omitted_packets':len(omissions),'inference_authorized_by_this_stage':False}


def prepare_interface(inp,out,cfg):
    """Version only the interface; retain exact parent paragraphs/target order."""
    _,_,contents,_=load_sources(inp);parents=read(inp/'packets.json')
    (out/'host-disclosures').mkdir()
    children=[];proof=[];jobs=[];role=next(r for r in read(inp/'study-manifest.json')['roles'] if r['role']=='generation')
    for parent in parents:
        if e.digest({k:v for k,v in parent.items() if k!='packet_sha256'})!=parent['packet_sha256']:
            raise ValueError('parent-packet-hash')
        child=copy.deepcopy(parent);old=child.pop('packet_sha256')
        child.update(parent_packet_sha256=old,interface_version=interface.VERSION)
        child['packet_sha256']=e.digest(child);data=interface.disclosure(child,contents)
        public=interface.public_disclosure(data)
        if read(inp/'study-manifest.json').get('competitive_selection_version'):
            public.update(max_proposals=len(public['targets']),target_dispositions_required=True)
        prompt=role['prompt_prefix']+MARKER+e.canonical(public).decode()
        body=OpenAITransport.request(prompt,role['schema'],role['settings'])
        jobs.append({'job_id':e.digest(body),'body':body})
        children.append(child)
        proof.append({'parent_packet_sha256':old,'child_packet_sha256':child['packet_sha256'],
            'source_id':child['source_id'],'parent_passages_sha256':e.digest([parent['source_paragraphs'],parent['targets']]),
            'child_passages_sha256':e.digest([child['source_paragraphs'],child['targets']]),
            'new_paragraphs_or_targets':0,'host_disclosure_sha256':e.digest(data),'public_disclosure_sha256':e.digest(public),
            'source_sentences':sum(len(p['sentences']) for p in data['source_paragraphs']),
            'unmapped_source_paragraphs':[p['passage_id'] for p in data['source_paragraphs'] if not p['sentences']]})
        write(out/'host-disclosures'/(old+'.json'),data)
    write(out/'packets.json',children);write(out/'parent-child.json',proof);write(out/'jobs.json',jobs)
    return {'packets':len(children),'new_paragraphs_or_targets':0,'provider_calls':0}


def load_sources(inp):
    rows=read(inp/'pages.json');pages={p['path']:p for p in rows};by_id={p['doc_id']:p for p in rows}
    contents={p['doc_id']:(inp/'corpus'/p['path']).read_bytes() for p in rows}
    for node,raw in contents.items():
        if e.rawhash(raw)!=by_id[node]['raw_sha256']:raise ValueError('immutable-source-hash')
    regions=read(inp/'regions.json')
    if any(r['assignment']!='development' or r['halo_nodes'] for r in regions):raise ValueError('development-zero-halo-only')
    if set(contents)!={n for r in regions for n in r['nodes']}:raise ValueError('source-group-coverage')
    return pages,by_id,contents,regions


def provider_result(inp,row,*,direct=False):
    if row['status']!='complete':return None
    p=inp/('jobs' if direct else 'model-output/jobs')/row['job_id']/row['raw_response_file']
    if e.rawhash(p.read_bytes())!=row['raw_response_sha256']:raise ValueError('provider-response-file-binding')
    body=read(p);provider='openai' if 'output' in body else 'anthropic'
    text=response_text(body,provider)
    if __import__('json').loads(text)!=row['payload'] or text!=row['transport_result']['text']:
        raise ValueError('provider-payload-binding')
    return p


def source_passage(row, raw):
    a,z=row['raw_span']
    return {'raw_span':[a,z],'raw_excerpt':raw[a:z].decode('utf-8')}


def review_disclosure(p, packet, contents, use_ids=False):
    if use_ids:return interface.review_disclosure(p,packet,contents)
    sources=[b for b in packet['source_paragraphs'] if b['raw_span'][0]<=p['source_span'][0]<p['source_span'][1]<=b['raw_span'][1]]
    if len(sources)!=1:raise ValueError('source-anchor-outside-packet')
    target=next(t for t in packet['targets'] if t['target_id']==p['target_id'])
    evidence={'source':[source_passage(sources[0],contents[p['source_id']])],
              'target':[source_passage(b,contents[p['target_id']]) for b in target['paragraphs']]}
    evidence_valid(p['target_evidence'],evidence['target'])
    data={'source_id':p['source_id'],'target_id':p['target_id'],'source_sha256':p['source_sha256'],
        'target_sha256':p['target_sha256'],'source_span':p['source_span'],'anchor':p['anchor'],
        'source_sentence':p['sentence'],'source_paragraph':evidence['source'][0],
        'target_passages':evidence['target']}
    return data,evidence


def model_job(role, data, evidence, p, packet, manifest, *, proposal_ids=None):
    policy=next(r for r in manifest['roles'] if r['role']==role)
    row={'source_id':p['source_id'],'target_id':p['target_id'],'packet_sha256':packet['packet_sha256'],
         'proposal_ids':proposal_ids or [p['proposal_id']], 'schema':policy['schema'],
         'prompt':policy['prompt_prefix']+MARKER+e.canonical(data).decode(), 'evidence':evidence}
    row['job_id']=e.digest(row);return row


def proposals(inp,out,cfg):
    if read(inp/'model-summary.json').get('authentication_blocked'):raise ValueError('generation-authentication-blocked')
    pages,by_id,contents,regions=load_sources(inp);region_by_id={r['region_id']:r for r in regions}
    packets={p['packet_sha256']:p for p in read(inp/'packets.json')}
    jobs={j['job_id']:j for j in read(inp/'model-jobs.json')}
    records=[];manifest=read(inp/'study-manifest.json');interface_log=[]
    use_ids=manifest.get('interface_version')==interface.VERSION
    if (inp/'admission-omissions.json').exists():
        for row in read(inp/'admission-omissions.json'):
            records.append(dict(row,status='generation_unavailable',technical_status='token-'+row['reason']))
    for result in read(inp/'model-summary.json')['results']:
        job=jobs[result['job_id']];packet=packets[job['packet_sha256']]
        if result['status']!='complete':
            records.append({'job_id':result['job_id'],'packet_sha256':packet['packet_sha256'],
                'source_id':packet['source_id'],'region_id':packet['region_id'],
                'status':'generation_unavailable','technical_status':result['status']});continue
        validate_result(result['payload'],job);raw_file=provider_result(inp,result)
        region=region_by_id[packet['region_id']]
        if manifest.get('competitive_selection_version'):
            from .embedded_competitive import validate_dispositions
            dispositions=validate_dispositions(result['payload'],packet['source_id'],[t['target_id'] for t in packet['targets']])
            items=[d['proposal'] for d in dispositions if d['disposition']=='supported_link']
            for d in dispositions:
                if d['disposition']=='no_supported_link':
                    records.append({'job_id':result['job_id'],'packet_sha256':packet['packet_sha256'],
                        'source_id':packet['source_id'],'region_id':packet['region_id'],'status':'no_supported_link',
                        'target_id':d['target_id'],'reason':d['reason'],'raw_response_sha256':result['raw_response_sha256']})
        else:items=result['payload']['proposals']
        for ordinal,item in enumerate(items):
            record={'job_id':result['job_id'],'packet_sha256':packet['packet_sha256'],'raw_item':item,
                'raw_response_sha256':result['raw_response_sha256'],'ordinal':ordinal,'region_id':packet['region_id']}
            if use_ids:
                interface.event(interface_log,'model_declared',dict(record),'model')
                try:
                    item,witness=interface.resolve_proposal(item,interface.disclosure(packet,contents),contents)
                    record['host_resolution']=witness
                    interface.event(interface_log,'host_resolved',{'job_id':result['job_id'],'ordinal':ordinal,'resolution':witness},'host')
                except (ValueError,UnicodeError) as exc:
                    record.update(status='interface_rejected',reason=str(exc))
                    interface.event(interface_log,'host_rejected',dict(record),'host');records.append(record);continue
            if item['source_id']!=packet['source_id'] or item['target_id'] not in {t['target_id'] for t in packet['targets']}:
                records.append(dict(record,status='schema_or_access_rejected',reason='outside-frozen-disclosure'));continue
            source,target=item['source_id'],item['target_id']
            role=next(r for r in manifest['roles'] if r['role']=='generation')
            p={'schema_version':ledger.VERSION+'.proposal','run_id':cfg['run_id'],'arm_id':'common-pool',
               'region_id':region['region_id'],'corpus_id':region['corpus_id'],'snapshot_id':region['snapshot_id'],
               **item,'source_sha256':e.rawhash(contents[source]),'target_sha256':e.rawhash(contents[target]),
               'href':posixpath.relpath(by_id[target]['path'],posixpath.dirname(by_id[source]['path']) or '.'),
               'target_fragment':'','coordinate_convention':e.COORDINATES,'confidence_status':'uncalibrated',
               'provenance':{'kind':'model_response','provider':role['settings']['provider'],'model':role['settings']['model'],
                   'prompt_version':role['prompt_sha256'],'schema_version':role['schema_sha256'],
                   'attempt_id':result['job_id']+':'+str(result['attempt']),
                   'timestamp':datetime.fromtimestamp(result['completed_at_unix'],timezone.utc).isoformat(),
                   'response_sha256':e.rawhash(raw_file.read_bytes()),
                   'source_access':[source]+[t['target_id'] for t in packet['targets']],
                   'retry_of':None if result['attempt']==0 else result['job_id']+':0','status':'model-response'}}
            p['proposal_id']=e.stable('proposal',cfg['run_id'],item,p['provenance']['response_sha256'],ordinal)
            if manifest.get('mock_responses'):
                p['provenance'].update(kind='mock_response',provider='MOCK:'+role['settings']['provider'],
                    model='MOCK:'+role['settings']['model'],status='mock-model')
            try:
                ledger.validate_proposal(p);record['proposal']=p
                review_disclosure(p,packet,contents,use_ids)
                _,facts=ledger.apply_edit(contents[source],contents[target],p,pages,read(inp/'original.json')['edges'],region,LIMITS)
                record.update(status='structural_eligible',structural_facts_sha256=e.digest(facts))
                if p['relation']=='insufficient_evidence' or p['basis']=='unsupported':
                    record.update(status='proposer_rejected',reason='proposer-insufficient-evidence')
            except (ValueError,UnicodeError) as exc:record.update(status='structural_rejected',reason=str(exc))
            records.append(record)
    pool=[r['proposal'] for r in records if 'proposal' in r]
    conflicts=ledger.pool_conflicts(pool)
    for record in records:
        if 'proposal' not in record:continue
        pid=record['proposal']['proposal_id']
        bad=[c for c in conflicts if pid in c['proposal_ids'] and c['reason']=='proposed-reciprocal-partner']
        if bad:record.update(status='structural_rejected',reason='proposed-reciprocal-partner')
    review_jobs=[]
    for record in records:
        if record['status']!='structural_eligible':continue
        p=record['proposal'];packet=packets[record['packet_sha256']];data,evidence=review_disclosure(p,packet,contents,use_ids)
        review_jobs.append(model_job('eligibility',data,evidence,p,packet,manifest))
    write(out/'records.json',records);write(out/'conflicts.json',conflicts);write(out/'review-jobs.json',review_jobs)
    if use_ids:
        (out/'interface-events.jsonl').write_bytes(b''.join(e.canonical(row)+b'\n' for row in interface_log))
        tip=e.digest(interface_log[-1]) if interface_log else '0'*64
        write(out/'interface-trusted-tip.json',interface.replay(interface_log,tip))
    return {'records':len(records),'proposals':len(pool),'structural_eligible':len(review_jobs),
            'generation_unavailable':sum(r['status']=='generation_unavailable' for r in records)}


def eligible_records(inp):
    if read(inp/'model-summary.json').get('authentication_blocked'):raise ValueError('eligibility-authentication-blocked')
    records=read(inp/'records.json');jobs={j['job_id']:j for j in read(inp/'model-jobs.json')}
    results={pid:row for row in read(inp/'model-summary.json')['results'] for pid in jobs[row['job_id']]['proposal_ids']}
    for record in records:
        if record['status']!='structural_eligible':continue
        p=record['proposal'];result=results.get(p['proposal_id'])
        if not result or result['status']!='complete':
            record.update(semantic_eligible=False,semantic_status='review_unavailable');continue
        validate_result(result['payload'],jobs[result['job_id']]);provider_result(inp,result)
        verdict=result['payload']
        if jobs[result['job_id']].get('evidence',{}).get('interface_version')==interface.VERSION:
            verdict,proof=interface.resolve_review(verdict,jobs[result['job_id']]['evidence'])
            record['review_declaration']=result['payload'];record['review_resolution']=proof
        score=sum(verdict[k] for k in ['relationship_support','target_support','placement'])
        eligible=(min(verdict[k] for k in ['relationship_support','target_support','placement'])>=1 and
                  score>=5 and not verdict['uncertainty'] and not verdict['contradiction'] and verdict['relation']!='insufficient_evidence')
        record.update(semantic_eligible=eligible,semantic_score=score,semantic_status='eligible' if eligible else 'semantic_rejected',
            review=verdict,review_job_id=result['job_id'],review_response_sha256=result['raw_response_sha256'],
            review_input_sha256=e.digest(jobs[result['job_id']]['evidence']))
    return records


def diagnostic_worker(path,graph):
    from .graph import local_objective as lo
    result={'ordinary':lo.ordinary(graph['nodes'],graph['edges']),
            'normalized':{str(q):lo.spectral(graph['nodes'],graph['edges'],q) for q in [.25,0.]}}
    write(Path(path),result)


def diagnostics(graph, deadline, cache):
    key=e.digest(graph)
    if key in cache:return dict(cache[key],cache_reused=True,cache_key=key)
    remaining=deadline-time.monotonic()
    if remaining<=0:return {'status':'resource_limit','cache_key':key}
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp)/'diagnostic.json';proc=mp.get_context('spawn').Process(target=diagnostic_worker,args=(str(path),graph))
        proc.start();proc.join(max(0,deadline-time.monotonic()))
        if proc.is_alive():
            proc.terminate();proc.join(1)
            if proc.is_alive():proc.kill();proc.join()
            return {'status':'resource_limit','cache_key':key}
        if proc.exitcode!=0 or not path.exists():return {'status':'unavailable','cache_key':key}
        cache[key]={'status':'available',**read(path)}
    return dict(cache[key],cache_reused=False,cache_key=key)


def select_groups(inp,out,records,regions,occurrences,scorer_factory=BoundedBlockScorer,with_diagnostics=True):
    deadline=time.monotonic()+2400;groups=[]
    conflicts=ledger.pool_conflicts([r['proposal'] for r in records if 'proposal' in r])
    for region in regions:
        relevant=[r for r in records if r.get('region_id')==region['region_id']]
        good=[r for r in relevant if r.get('semantic_eligible')]
        pool=[{'proposal_id':r['proposal']['proposal_id'],'source_id':r['proposal']['source_id'],
            'target_id':r['proposal']['target_id'],'semantic_score':r['semantic_score'],'semantic_eligible':True,
            'conflicts':[pid for c in conflicts if r['proposal']['proposal_id'] in c['proposal_ids']
                         for pid in c['proposal_ids'] if pid!=r['proposal']['proposal_id']]} for r in good]
        fixture_policy=read(inp/'study-manifest.json')
        if fixture_policy.get('authored_fixture'):
            order=fixture_policy['authored_tie_order']
            for candidate in pool:
                candidate['tie_id']='f%02d'%order.index({k:candidate[k] for k in ('source_id','target_id')})
        # project() filters targets and expects source-scoped occurrence rows.
        # Region views are induced: incoming edges from other groups are not halo.
        nodes=set(region['nodes'])
        graph={'nodes':region['nodes'],'edges':e.project([r for r in occurrences if r['source_id'] in nodes],nodes),
               'audit_binding':{'region':region,'all_records':relevant,'frozen_mask_sha256':e.digest(occurrences),
                    'original_full_graph_sha256':e.digest(read(inp/'original.json')),
                    'source_inventory_sha256':e.digest(read(inp/'pages.json')),
                    'study_manifest_sha256':e.digest(read(inp/'study-manifest.json')),
                    'input_inventory_sha256':read(inp/'operator-request.json')['input_inventory_sha256'] if (inp/'operator-request.json').exists() else None}}
        # Reserve half of the 100 MiB group output ceiling for traces and final
        # descriptors. This is an explicit storage sublimit, never a rank gate.
        maximum=read(inp/'study-manifest.json').get('maximum_group_arm_edits',4)
        if type(maximum) is not int or not 0<=maximum<=4:raise ValueError('maximum-group-arm-edits')
        policy=dict(DEFAULT_POLICY,maximum=maximum,wall_seconds=max(.001,min(600,deadline-time.monotonic())),max_output_bytes=50*1024**2)
        scorer=scorer_factory(graph,pool,policy);score_cache={};solve_cache=getattr(scorer,'cache',{})
        start=time.monotonic();arms={a:select_embedded_v2(pool,graph,policy,score_cache,arm=a,scorer=scorer) for a in ['S','Z','M']}
        if arms['S']['selected']!=arms['Z']['selected'] or len({a['achieved_budget'] for a in arms.values()})!=1:
            raise ValueError('unmatched-budget-or-zero-control')
        by_id={p['proposal_id']:p for p in pool};final={};diagnostic_cache={}
        for arm in ['S','Z','M']:
            chosen=arms[arm]['selected']
            final[arm]={'phase_cost':scorer(tuple(chosen)) if chosen else {'status':'finite_estimate','schedule_costs':[0.,0.],'analytic_empty_prefix':True}}
            edited={'nodes':graph['nodes'],'edges':sorted(set(map(tuple,graph['edges']))|{
                (by_id[pid]['source_id'],by_id[pid]['target_id']) for pid in chosen})}
            if with_diagnostics:final[arm]['diagnostics']=diagnostics(edited,min(scorer.deadline,deadline),diagnostic_cache)
        group={'region_id':region['region_id'],'source_families':region['source_families'],'nodes':len(region['nodes']),
            'proposed':sum(r['status']!='generation_unavailable' for r in relevant),
            'generation_unavailable':sum(r['status']=='generation_unavailable' for r in relevant),
            'structural_eligible':sum(r['status'] in ('structural_eligible','proposer_rejected') for r in relevant),
            'proposer_rejected':sum(r['status']=='proposer_rejected' for r in relevant),
            'review_unavailable':sum(r.get('semantic_status')=='review_unavailable' for r in relevant),
            'semantic_eligible':len(pool),'requested_budget':maximum,'feasible_budget':arms['S']['feasible_budget'],
            'arms':arms,'final':final,'runtime_seconds':time.monotonic()-start,
            'selection_sequence_changed':arms['S']['selected']!=arms['M']['selected'],
            'final_selected_set_changed':set(arms['S']['selected'])!=set(arms['M']['selected']),
            'shared_proposal_ids':sorted(set(arms['S']['selected'])&set(arms['M']['selected'])),
            'final_energy_comparison_available':all(final[a]['phase_cost']['status']=='finite_estimate' for a in ['S','M'])}
        root=out/'numerical'/region['region_id'].replace(':','-');root.mkdir(parents=True)
        numerical={'solves':solve_cache,'score_cache':score_cache,'diagnostics':diagnostic_cache,'group':group}
        if len(e.canonical(numerical))>100*1024**2:raise ValueError('group-numerical-output-ceiling')
        write(root/'audit.json',numerical);groups.append(group)
    return groups


def case_card(path,arm,proposal,record,step,raw,edited,packet):
    paragraph=next(b for b in packet['source_paragraphs'] if b['raw_span'][0]<=proposal['source_span'][0]<proposal['source_span'][1]<=b['raw_span'][1])
    a,z=paragraph['raw_span'];before=raw[a:z];shift=len(edited)-len(raw);after=edited[a:z+shift]
    # HTML/script input is displayed as literal text; all outgoing links disabled.
    md=e.parser();md.options['html']=False
    import re
    render=lambda b:re.sub(r'<a href="[^"]*"','<a href="#source"',md.render(b.decode()))
    diff=''.join(difflib.unified_diff(raw.decode().splitlines(True),edited.decode().splitlines(True),fromfile='original',tofile=arm))
    path.with_suffix('.diff').write_text(diff,encoding='utf-8')
    info={'proposal':proposal,'eligibility':record['review'],'selection_step':step,'source_paragraph_span':[a,z]}
    if 'confirmation' in record:info['preselection_support_confirmation']=record['confirmation']
    write(path.with_suffix('.json'),info)
    label='MOCK fixture assertion' if proposal['provenance']['kind']=='mock_response' else 'AI-reference eligibility'
    path.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<style>body{font:16px/1.5 system-ui;margin:28px;max-width:1250px;color:#192638}.cols{display:grid;grid-template-columns:1fr 1fr;gap:24px}article{padding:20px;background:#f3f6fa;border:1px solid #ccd5df}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f6;padding:16px}h1{font-size:24px}a{color:#1756a4}</style>'
        f'<h1>{html.escape(arm)} · {html.escape(proposal["anchor"])}</h1><p>{label} {record["semantic_score"]}/6. Source-based paragraph preview; reader benefit and human accuracy untested.</p>'
        f'<p>{html.escape(proposal["source_id"])} → {html.escape(proposal["target_id"])}<br>Decision: {html.escape(step["status"])}; raw source span {proposal["source_span"]}.</p>'
        f'<div class="cols"><section><h2>Before</h2><article>{render(before)}</article></section><section><h2>After</h2><article>{render(after)}</article></section></div>'
        f'<h2>Exact full-file diff</h2><pre>{html.escape(diff)}</pre><p>All source bytes outside the inserted link syntax and all visible prose are mechanically verified.</p>',encoding='utf-8')


def materialize(inp,out,records,groups,pages,contents,regions):
    packets={p['packet_sha256']:p for p in read(inp/'packets.json')};region_by_id={r['region_id']:r for r in regions}
    original=read(inp/'original.json')['edges'];all_pool=[r['proposal'] for r in records if 'proposal' in r]
    frozen_occurrences=read(inp/'frozen-occurrences.json');baseline_content=e.project(frozen_occurrences,set(contents))
    by_id={r['proposal']['proposal_id']:r for r in records if 'proposal' in r};manifest=read(inp/'study-manifest.json')
    review_role=next(r for r in manifest['roles'] if r['role']=='eligibility')
    for path,p in pages.items():
        dest=out/'original/corpus'/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(contents[p['doc_id']])
    output=[];cards=[]
    for arm in ['S','Z','M']:
        root=out/arm;root.mkdir();edited=dict(contents);log=[];facts={};selected_steps={}
        for group in groups:
            for step in group['arms'][arm]['trace']:selected_steps[step['selected']]=(group,step)
        for record in records:
            if 'proposal' not in record:continue
            p=copy.deepcopy(record['proposal']);p['arm_id']=arm
            def append(typ,payload,authority='host'):
                row=ledger.event(log,p,typ,payload,authority)
                ledger.append_event(root/'intent-events.jsonl',row,e.digest(log[-1]) if log else '0'*64);log.append(row)
            append('proposed',p,'model');append('schema_validated',{'proposal_sha256':e.digest(p)})
            structural=record['status'] in ('structural_eligible','proposer_rejected')
            append('structurally_validated',{'eligible':structural,'reason':'exact-host-valid' if structural else record['reason'],
                'validation_sha256':record.get('structural_facts_sha256',e.digest({'reason':record.get('reason')}))})
            if not structural:
                append('rejected',{'reason':record['reason'],'no_new_edge':False});continue
            if record['status']=='proposer_rejected':
                append('rejected',{'reason':record['reason'],'no_new_edge':False});continue
            if 'review' not in record:
                append('failed',{'reason':'independent-review-unavailable'});continue
            review_metadata={}
            if 'confirmation' in record:
                confirmation_role=next(r for r in manifest['roles'] if r['role']=='support_confirmation')
                review_metadata={'reviewer_provider':review_role['settings']['provider']+'+'+confirmation_role['settings']['provider'],
                    'reviewer_model':review_role['settings']['model']+'+'+confirmation_role['settings']['model'],
                    'response_sha256':e.digest([record['review_response_sha256'],record['confirmation_response_sha256']]),
                    'blinded_input_sha256':e.digest([record['review_input_sha256'],record['confirmation_input_sha256']])}
            elif manifest.get('competitive_selection_version') and record.get('haiku_eligible'):
                append('failed',{'reason':'preselection-confirmation-unavailable'});continue
            append('reviewed',{'reviewer_provider':review_role['settings']['provider'],'reviewer_model':review_role['settings']['model'],
                'rubric_version':review_role['schema_sha256'],'decision':'eligible' if record['semantic_eligible'] else 'reject',
                'evidence':record['review']['target_evidence'],'uncertainty':str(record['review']['uncertainty']),
                'response_sha256':record['review_response_sha256'],'blinded_input_sha256':record['review_input_sha256'],
                'status':'mock-model' if manifest.get('mock_responses') else 'model-response',**review_metadata},'reviewer')
            if not record['semantic_eligible']:
                append('rejected',{'reason':'independent-semantic-rejection','no_new_edge':False});continue
            append('eligible',{'structural':True,'semantic':True})
            if p['proposal_id'] not in selected_steps:
                append('rejected',{'reason':'not-selected-at-common-budget','no_new_edge':False});continue
            group,step=selected_steps[p['proposal_id']];selection=group['arms'][arm]
            append('selected',{'rank':step['step'],'reason':step['status'],'objective_input_sha256':selection['binding_sha256'],
                'objective_output_sha256':e.digest(selection),'requested_budget':selection['requested_budget'],
                'achieved_budget':selection['achieved_budget'],'conflicts_skips':[]})
            raw=contents[p['source_id']]
            new,f=ledger.apply_edit(edited[p['source_id']],contents[p['target_id']],p,pages,original,
                                   region_by_id[p['region_id']],LIMITS,all_pool)
            if e.replay_patch(raw,f)!=new or e.replay_patch(new,f)!=new:raise ValueError('patch-replay')
            edited[p['source_id']]=new;facts[p['proposal_id']]=f
            append('applied',{k:f[k] for k in ['patch_sha256','source_before_sha256','source_after_sha256','occurrence_id','edge_id']}|{'facts_sha256':e.digest(f)})
            append('verified',{k:f[k] for k in ['rendered_before_sha256','rendered_after_sha256','before_graph_sha256','after_graph_sha256']}|{'facts_sha256':e.digest(f)})
            card=root/(p['proposal_id'].replace(':','-')+'.html');case_card(card,arm,p,record,step,raw,new,packets[record['packet_sha256']]);cards.append(card.relative_to(out).as_posix())
        selected_sources={by_id[pid]['proposal']['source_id'] for pid in selected_steps}
        for path,p in pages.items():
            node=p['doc_id'];before=contents[node];after=edited[node]
            if e.semantic_text(before)!=e.semantic_text(after):raise ValueError('visible-prose-changed')
            if node not in selected_sources and before!=after:raise ValueError('untouched-source-bytes')
            if node in selected_sources:
                old=e.occurrences(before,p,pages);new=e.occurrences(after,p,pages)
                nav=lambda rows:sorted((r['target_id'] or '',r['href'],r['source_syntax']) for r in rows if r['class']=='navigation')
                if nav(old)!=nav(new):raise ValueError('navigation-changed')
                expected={(node,by_id[pid]['proposal']['target_id']) for pid in selected_steps
                          if by_id[pid]['proposal']['source_id']==node}
                before_pairs=set(e.project(old,set(contents),'original'));after_pairs=set(e.project(new,set(contents),'original'))
                if after_pairs-before_pairs!=expected or before_pairs-after_pairs:raise ValueError('original-parser-graph-delta')
            dest=root/'corpus'/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(after)
        tip=e.digest(log[-1]) if log else '0'*64;replay=ledger.replay(log,tip)
        if not log:(root/'intent-events.jsonl').write_bytes(b'')
        write(root/'trusted-tip.json',{'tip_sha256':tip,'replay':replay});write(root/'host-facts.json',facts)
        added=sorted([f['added_edges'][0] for f in facts.values()])
        write(root/'graph-deltas.json',{'original_before':original,'content_before':baseline_content,
            'original_after':sorted(set(map(tuple,original))|set(map(tuple,added))),
            'content_after':sorted(set(map(tuple,baseline_content))|set(map(tuple,added))),
            'added_pairs':added,'removed_pairs':[],'frozen_mask_sha256':e.digest(frozen_occurrences),
            'navigation_changed':False,'scope':'all development source nodes; original fixed mask retained; no site execution'})
        output.append({'arm':arm,'applied':len(facts),'tip_sha256':tip,'all_source_files_verified':len(contents),
            'visible_prose_equal':True,'untouched_source_bytes_equal':True,'navigation_retained':True,
            'target_evidence_scope':'frozen baseline byte quotes; final target semantic text verified even when its link syntax also changes',
            'added_pairs':sorted([f['added_edges'][0] for f in facts.values()])})
    write(out/'applications.json',output);write(out/'case-card-inventory.json',cards)
    label='OFFLINE MOCK fixture labels' if manifest.get('mock_responses') else 'AI-reference labels'
    (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>Refinement source previews</h1><p>'+label+'; no human accuracy or reader benefit established.</p><ul>'+''.join(f'<li><a href="{c}">{html.escape(c)}</a></li>' for c in cards)+'</ul>',encoding='utf-8')
    return output


def select_and_apply(inp,out,cfg):
    pages,by_id,contents,regions=load_sources(inp);records=eligible_records(inp)
    groups=select_groups(inp,out,records,regions,read(inp/'frozen-occurrences.json'))
    applications=materialize(inp,out,records,groups,pages,contents,regions)
    write(out/'records.json',records);write(out/'groups.json',groups)
    review_log=[]
    for record in records:
        if 'review_declaration' in record:
            interface.event(review_log,'reviewer_declared',{'job_id':record['review_job_id'],
                'response_sha256':record['review_response_sha256'],'declaration':record['review_declaration']},'reviewer')
            interface.event(review_log,'review_resolved',{'job_id':record['review_job_id'],
                'resolution':record['review_resolution'],'resolved':record['review']},'host')
    if review_log:
        (out/'review-interface-events.jsonl').write_bytes(b''.join(e.canonical(row)+b'\n' for row in review_log))
        write(out/'review-interface-trusted-tip.json',interface.replay(review_log,e.digest(review_log[-1])))
    selected={pid for group in groups for arm in group['arms'].values() for pid in arm['selected']}
    records_by_id={r['proposal']['proposal_id']:r for r in records if 'proposal' in r}
    union={}
    for pid in selected:
        p=records_by_id[pid]['proposal'];key=e.digest([p['source_id'],p['target_id'],p['source_span'],p['anchor'],p['href']])
        union.setdefault(key,[]).append(pid)
    packets={p['packet_sha256']:p for p in read(inp/'packets.json')};manifest=read(inp/'study-manifest.json');jobs=[]
    for key in sorted(union,key=lambda k:e.digest([3602,k])):
        ids=sorted(union[key]);record=records_by_id[ids[0]];p=record['proposal'];packet=packets[record['packet_sha256']]
        data,evidence=review_disclosure(p,packet,contents,manifest.get('interface_version')==interface.VERSION)
        jobs.append(model_job('blinded_assessment',data,evidence,p,packet,manifest,proposal_ids=ids))
    write(out/'assessment-jobs.json',jobs);write(out/'assessment-union.json',union)
    return {'groups':groups,'applications':applications,'unique_final_assessment_jobs':len(jobs)}


def assemble(inp,out,cfg):
    if read(inp/'model-summary.json').get('authentication_blocked'):raise ValueError('assessment-authentication-blocked')
    jobs={j['job_id']:j for j in read(inp/'model-jobs.json')};assessments=[]
    for result in read(inp/'model-summary.json')['results']:
        job=jobs[result['job_id']];row={'job_id':result['job_id'],'proposal_ids':job['proposal_ids'],'status':result['status']}
        if result['status']=='complete':
            validate_result(result['payload'],job);provider_result(inp,result)
            assessment=result['payload']
            if job.get('evidence',{}).get('interface_version')==interface.VERSION:
                assessment,proof=interface.resolve_review(assessment,job['evidence'])
                row.update(assessment_declaration=result['payload'],assessment_resolution=proof)
            row.update(assessment=assessment,raw_response_sha256=result['raw_response_sha256'],
                score=sum(result['payload'][k] for k in ['relationship_support','target_support','placement']))
        assessments.append(row)
    write(out/'assessments.json',assessments);write(out/'groups.json',read(inp/'groups.json'))
    return {'assessments':assessments,'shared_across_arms':True,'reference':'AI only; independent eligibility and final roles; no human validation'}


def run(inp,out):
    req=read(inp/'operator-request.json');inv=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    if inv!=req['input_inventory'] or e.digest(inv)!=req['input_inventory_sha256']:raise ValueError('refinement-input-inventory')
    if set(p.name for p in out.iterdir())!={'.retrieval-arena-output-owner'}:raise ValueError('output-not-fresh')
    cfg=read(inp/'config.json');started=time.monotonic();stage=cfg['stage']
    if stage=='prepare-generation':result=prepare_generation(inp,out,cfg)
    elif stage=='prepare-interface':result=prepare_interface(inp,out,cfg)
    elif stage=='diagnose-interface':
        from .embedded_interface_diagnostic import diagnose
        result=diagnose(inp,out)
    elif stage=='prepare-authored-interface':
        from .embedded_interface_fixture import prepare
        result=prepare(inp,out,cfg)
    elif stage=='proposals':result=proposals(inp,out,cfg)
    elif stage=='select':result=select_and_apply(inp,out,cfg)
    elif stage=='competitive-select':
        from .embedded_competitive import select_and_apply as competitive_select_and_apply
        result=competitive_select_and_apply(inp,out,cfg)
    elif stage=='assemble':result=assemble(inp,out,cfg)
    else:raise ValueError('refinement-stage')
    write(out/'summary.json',{'schema_version':VERSION,'stage':stage,'result':result,'runtime_seconds':time.monotonic()-started,
        'provider_calls':0,'reserved_outcomes_inspected':False,'input_request_sha256':e.digest(req),
        'image':{k:os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE','RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']}})
    inv=portable_inventory(out);write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
