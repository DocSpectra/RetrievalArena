"""Inventory-bound, priced structured calls for an authorized refinement stage.

The host freezes role jobs; this executor does not search, select or edit a
corpus. Successful schemas are reference judgments, never host application facts.
"""
import asyncio
import json
import os
import time
from pathlib import Path

from . import embedded_links as e
from .embedded_link_audit import read, write, portable_inventory
from .model_providers import transport_for
from .model_providers.base import validate_settings
from .embedded_failures import classify

VERSION='retrieval_arena.embedded_model_run.v1'


def validate_json(value, schema):
    """Closed JSON-schema subset used by the consumer's frozen role schemas."""
    if 'anyOf' in schema:
        for option in schema['anyOf']:
            try: validate_json(value,option); return
            except ValueError: pass
        raise ValueError('schema-anyOf')
    if schema.get('type')=='object':
        if not isinstance(value,dict) or set(schema.get('required',[]))-value.keys(): raise ValueError('schema-object')
        if schema.get('additionalProperties') is not False or set(value)-schema['properties'].keys(): raise ValueError('schema-fields')
        for key,item in value.items(): validate_json(item,schema['properties'][key])
    elif schema.get('type')=='array':
        if not isinstance(value,list) or not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',float('inf')):
            raise ValueError('schema-array')
        for item in value:validate_json(item,schema['items'])
    else: e.validate_schema(value,schema)
    e.canonical(value)


def evidence_valid(evidence, passages):
    for quote in evidence:
        start,end=quote['span']
        if not any(a<=start<end<=z and raw.encode()[start-a:end-a].decode('utf-8')==quote['quote']
                   for a,z,raw in [(p['raw_span'][0],p['raw_span'][1],p['raw_excerpt']) for p in passages]):
            raise ValueError('review-quote-outside-exact-disclosure')


def validate_result(payload, job):
    validate_json(payload,job['schema'])
    if 'target_ids' in job:
        from .embedded_competitive import validate_dispositions
        validate_dispositions(payload, job['source_id'], job['target_ids'])
    if job.get('evidence'):
        from . import embedded_interface as interface
        if job['evidence'].get('interface_version')==interface.VERSION:
            interface.resolve_review(payload,job['evidence'])
            return
        evidence_valid(payload['source_evidence'],job['evidence']['source'])
        evidence_valid(payload['target_evidence'],job['evidence']['target'])


def response_text(body, provider):
    if provider=='anthropic':return ''.join(c['text'] for c in body['content'] if c['type']=='text')
    return ''.join(c['text'] for row in body['output'] if row['type']=='message'
                   for c in row['content'] if c['type']=='output_text')


def admit(inp, authorization):
    req=read(inp/'operator-request.json')
    inv=[row for row in portable_inventory(inp) if row['path']!='operator-request.json']
    if inv!=req['input_inventory'] or e.digest(inv)!=req['input_inventory_sha256']: raise ValueError('model-input-inventory')
    cfg=read(inp/'config.json');manifest=read(inp/'study-manifest.json')
    if authorization.get('approved') is not True or authorization.get('scope')!='bounded_embedded_refinement_v2':
        raise ValueError('explicit-inference-authorization-required')
    if authorization.get('study_manifest_sha256')!=e.digest(manifest):raise ValueError('authorization-manifest-binding')
    if not manifest.get('provider_token_count_complete'):raise ValueError('generation-token-admission-required')
    roles={r['role']:r for r in manifest['roles']};role=roles[cfg['role']]
    if cfg['settings']!=role['settings']:raise ValueError('role-settings-binding')
    validate_settings(cfg['settings'])
    ceiling=30 if manifest.get('competitive_selection_version')=='retrieval_arena.embedded_competitive.v1' else 20
    if sum(r['settings']['spend_cap_usd'] for r in roles.values())>manifest['aggregate_hard_cap_usd'] or manifest['aggregate_hard_cap_usd']>ceiling:
        raise ValueError('aggregate-spend-cap')
    if not 0<=cfg['remaining_inference_seconds']<=7200:raise ValueError('inference-wall-policy')
    jobs=read(inp/'jobs.json')
    if len(jobs)>role['initial_items_max'] or role['attempts_max']>2*role['initial_items_max']:raise ValueError('role-item-budget')
    if cfg['role']=='generation' and e.digest(jobs)!=manifest['generation_jobs_sha256']:
        raise ValueError('generation-job-pool-binding')
    for job in jobs:
        if job['job_id']!=e.digest({k:v for k,v in job.items() if k!='job_id'}):raise ValueError('job-binding')
        if e.digest(job['schema'])!=role['schema_sha256']:raise ValueError('role-schema-binding')
        if not job['prompt'].startswith(role['prompt_prefix']):raise ValueError('role-prompt-binding')
        if job.get('source_id') not in manifest['allowed_source_ids']:raise ValueError('source-outside-authorized-access')
        if cfg['role']=='generation' and job.get('provider_count') not in manifest['generation_counts']:
            raise ValueError('unfrozen-generation-count')
    return cfg,manifest,jobs


async def sdk_count(transport, body, provider):
    if provider=='openai':
        response=await transport.client.responses.input_tokens.count(**{
            k:body[k] for k in ['model','input','text','reasoning'] if k in body})
    else:
        response=await transport.client.messages.count_tokens(**{
            k:body[k] for k in ['model','messages','thinking','output_config'] if k in body})
    return response.model_dump(mode='json')


async def execute(cfg, manifest, jobs, out, factory=transport_for, counter=sdk_count):
    """Persist attempts before calls; a restart never resets attempts or spend."""
    out.mkdir(exist_ok=True);role=next(r for r in manifest['roles'] if r['role']==cfg['role'])
    binding=e.digest([VERSION,cfg,manifest,jobs]);header=out/'execution-binding.json'
    if header.exists() and read(header)['binding_sha256']!=binding:raise ValueError('resume-binding-mismatch')
    if not header.exists():
        if any(p.name!='.retrieval-arena-output-owner' for p in out.iterdir()):raise ValueError('output-not-fresh')
        write(header,{'binding_sha256':binding,'started_at_unix':time.time(),'started_monotonic':time.monotonic()})
    anchor=read(header);started=anchor['started_at_unix']
    elapsed=max(0,time.time()-started,time.monotonic()-anchor['started_monotonic'])
    deadline=time.monotonic()+max(0,cfg['remaining_inference_seconds']-elapsed)
    if not jobs or time.monotonic()>=deadline:
        rows=[{'job_id':j['job_id'],'status':'resource_limit','reason':'overall_inference_wall_ceiling'} for j in jobs]
        summary={'schema_version':VERSION,'role':cfg['role'],'results':rows,'completion_attempts_total':0,
            'token_count_attempts_total':0,'spent_or_reserved_usd':0,'authentication_blocked':False,
            'started_at_unix':started,'finished_at_unix':time.time(),'runtime_seconds':0,'binding_sha256':binding}
        write(out/'summary.json',summary);return summary
    transport=factory(cfg['settings'],'external_api',out)
    (out/'jobs').mkdir(exist_ok=True);semaphore=asyncio.Semaphore(2);blocked=False;block_reason=None
    policy=manifest.get('lifecycle_policy')
    from . import embedded_interface as interface
    interface_path=out/'response-interface-events.jsonl'
    interface_log=[json.loads(line) for line in interface_path.read_bytes().splitlines()] if interface_path.exists() else []
    if interface_log:interface.replay(interface_log,read(out/'response-interface-tip.json')['tip_sha256'])
    def interface_event(typ,payload,authority):
        row=interface.event(interface_log,typ,payload,authority)
        with interface_path.open('ab') as stream:stream.write(e.canonical(row)+b'\n');stream.flush();os.fsync(stream.fileno())
        write(out/'response-interface-tip.json',{'tip_sha256':e.digest(row)})
    circuit=out/'provider-circuit.json'
    if policy and circuit.exists():
        blocked=True;block_reason=read(circuit)['halt_reason']
    attempt_files=list((out/'jobs').glob('*/attempt-*/attempt.json'))
    attempted=len(attempt_files);count_attempts=len(list((out/'jobs').glob('*/count-*.json')))
    result_rows=[]
    async def one(job):
        nonlocal attempted,count_attempts,blocked,block_reason
        target=out/'jobs'/job['job_id'];target.mkdir(exist_ok=True);terminal=target/'result.json'
        if terminal.exists():
            prior=read(terminal)
            if prior.get('status')=='complete':
                validate_result(prior['payload'],job)
                raw=read(target/prior['raw_response_file'])
                if e.rawhash((target/prior['raw_response_file']).read_bytes())!=prior['raw_response_sha256']:raise ValueError('cached-response-tamper')
                if prior['transport_result']['text']!=response_text(raw,cfg['settings']['provider']) or prior['payload']!=json.loads(prior['transport_result']['text']):
                    raise ValueError('cached-payload-response-binding')
            return dict(prior,cache_reused=True)
        body=transport.request(job['prompt'],job['schema'],cfg['settings']);request_hash=e.digest(body)
        write(target/'request.json',body)
        if policy and len(e.canonical(body))>policy['maximum_count_request_bytes']:
            result={'job_id':job['job_id'],'status':'request_byte_cap_unavailable'}
            write(terminal,result);return result
        tokens=None;count_record=job.get('provider_count')
        if count_record and count_record['request_sha256']==request_hash and count_record['model']==cfg['settings']['model']:
            tokens=count_record['input_tokens']
        if tokens is None:
            prior_counts=sorted(target.glob('count-*.json'))
            for prior in reversed(prior_counts):
                saved=read(prior)
                if saved.get('status')=='counted' and saved['request_sha256']==request_hash:
                    tokens=saved['raw_response']['input_tokens'];break
            for retry in range(len(prior_counts),2) if tokens is None else []:
                async with semaphore:
                    if blocked or time.monotonic()>=deadline or count_attempts>=2*len(jobs):break
                    count_attempts+=1
                    if policy:write(target/f'count-{retry}.json',{'request_sha256':request_hash,'status':'attempted'})
                    try:
                        if policy:
                            reserve=transport.ledger.reserve(policy['maximum_count_request_bytes']+1024,1,request_hash)
                            transport.ledger.fail(reserve,'count-billing-contingency-no-invoice')
                        raw=await asyncio.wait_for(counter(transport,body,cfg['settings']['provider']),min(120,max(.001,deadline-time.monotonic())))
                        tokens=raw['input_tokens']
                        if type(tokens) is not int or tokens<=0:raise ValueError('invalid-provider-token-count')
                        write(target/f'count-{retry}.json',{'request_sha256':request_hash,'raw_response':raw,'status':'counted'})
                        break
                    except Exception as exc:
                        kind,transient,halt=classify(exc)
                        write(target/f'count-{retry}.json',{'request_sha256':request_hash,'status':'technical_failure','error_class':type(exc).__name__,'failure_kind':kind})
                        if halt:
                            blocked=True;block_reason=kind+'_blocked'
                            if policy:write(circuit,{'halt_reason':block_reason,'failure_kind':kind})
                        if policy and not transient:break
                        if policy and retry==0:await asyncio.sleep(min(policy['retry_backoff_seconds'],max(0,deadline-time.monotonic())))
        if tokens is None or tokens>cfg['settings']['max_input_tokens']:
            result={'job_id':job['job_id'],'status':block_reason if blocked else
                'token_unavailable' if tokens is None else 'over_cap_unavailable','input_tokens':tokens}
            write(terminal,result);return result
        existing=list(target.glob('attempt-*/attempt.json'))
        for retry in range(len(existing),2):
            async with semaphore:
                if blocked or time.monotonic()>=deadline or attempted>=role['attempts_max']:break
                attempted+=1;attempt_dir=target/f'attempt-{retry}';attempt_dir.mkdir(exist_ok=True)
                meta={'job_id':job['job_id'],'attempt':retry,'request_sha256':request_hash,
                      'input_tokens':tokens,'status':'attempted','started_at_unix':time.time()}
                write(attempt_dir/'attempt.json',meta);reservation=None
                def audit(row):
                    nonlocal reservation
                    if row['event']=='request':reservation=row['reservation']
                    write(attempt_dir/(row['event']+'.json'),row)
                    if row['event']=='response':write(attempt_dir/'provider-response.json',row['body'])
                try:
                    response=await asyncio.wait_for(transport(job['prompt'],job['schema'],cfg['settings'],audit=audit),
                                                    min(120,max(.001,deadline-time.monotonic())))
                    write(attempt_dir/'transport-result.json',response)
                    usage=response['usage'];total_input=usage['input_tokens']+(usage.get('cache_write_tokens') or 0)+(usage.get('cache_read_tokens') or 0)
                    if total_input>cfg['settings']['max_input_tokens'] or usage['output_tokens']>cfg['settings']['max_output_tokens'] or transport.ledger.spent>cfg['settings']['spend_cap_usd']+1e-12:
                        blocked=True;block_reason='provider_budget_violation'
                        raise RuntimeError('provider-usage-budget-violation')
                    payload=json.loads(response['text'])
                    if job.get('evidence',{}).get('interface_version')==interface.VERSION:
                        interface_event('reviewer_declared',{'job_id':job['job_id'],'attempt':retry,
                            'declaration':payload,'response_sha256':e.rawhash((attempt_dir/'provider-response.json').read_bytes())},'reviewer')
                    validate_result(payload,job)
                    if job.get('evidence',{}).get('interface_version')==interface.VERSION:
                        resolved,proof=interface.resolve_review(payload,job['evidence'])
                        interface_event('review_resolved',{'job_id':job['job_id'],'attempt':retry,'resolution':proof,'resolved':resolved},'host')
                    raw=read(attempt_dir/'provider-response.json')
                    if response['text']!=response_text(raw,cfg['settings']['provider']):raise ValueError('raw-response-text-binding')
                    result={'job_id':job['job_id'],'status':'complete','payload':payload,
                        'request_sha256':request_hash,'input_tokens':tokens,'transport_result':response,
                        'raw_response_file':f'attempt-{retry}/provider-response.json',
                        'raw_response_sha256':e.rawhash((attempt_dir/'provider-response.json').read_bytes()),
                        'raw_response_format':'canonical SDK model_dump JSON plus LF; not HTTP wire bytes',
                        'attempt':retry,'completed_at_unix':time.time()}
                    meta['status']='complete';write(attempt_dir/'attempt.json',meta);write(terminal,result);return result
                except Exception as exc:
                    if reservation is not None:transport.ledger.fail(reservation,type(exc).__name__)
                    kind,transient,halt=classify(exc)
                    if policy and job.get('evidence',{}).get('interface_version')==interface.VERSION:
                        interface_event('host_rejected',{'job_id':job['job_id'],'attempt':retry,
                            'failure_kind':kind,'error_class':type(exc).__name__},'host')
                    meta.update(status='technical_failure',error_class=type(exc).__name__,failure_kind=kind)
                    write(attempt_dir/'attempt.json',meta)
                    # Transports retain redacted SDK failures, not credential values.
                    if halt:
                        blocked=True;block_reason=kind+'_blocked'
                        if policy:write(circuit,{'halt_reason':block_reason,'failure_kind':kind})
                    if policy and not transient:break
                    if policy and retry==0:await asyncio.sleep(min(policy['retry_backoff_seconds'],max(0,deadline-time.monotonic())))
        result={'job_id':job['job_id'],'status':block_reason if blocked else 'technical_unavailable',
                'reason':'wall_ceiling' if time.monotonic()>=deadline else 'role_attempt_ceiling' if attempted>=role['attempts_max'] else 'technical_attempts_exhausted'}
        write(terminal,result);return result
    try:result_rows=await asyncio.gather(*(one(job) for job in jobs))
    finally:
        if hasattr(transport,'client') and hasattr(transport.client,'close'):await transport.client.close()
    summary={'schema_version':VERSION,'role':cfg['role'],'results':result_rows,'completion_attempts_total':attempted,
        'token_count_attempts_total':count_attempts,'spent_or_reserved_usd':transport.ledger.spent,
        'authentication_blocked':block_reason=='authentication_blocked','halt_reason':block_reason,'started_at_unix':started,'finished_at_unix':time.time(),
        'runtime_seconds':time.time()-started,'binding_sha256':binding,
        'image':{k:os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE','RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']}}
    write(out/'summary.json',summary);return summary


async def run(inp,out,authorization):
    cfg,manifest,jobs=admit(inp,authorization)
    result=await execute(cfg,manifest,jobs,out)
    inv=[r for r in portable_inventory(out) if r['path']!='manifest.json'];write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv),
        'input_request_sha256':e.digest(read(inp/'operator-request.json')),'authorization_sha256':e.digest(authorization)})
    return result


if __name__=='__main__':
    import sys
    if len(sys.argv)!=4:raise SystemExit('usage: embedded_model_run INPUT OUTPUT AUTHORIZATION')
    asyncio.run(run(Path(sys.argv[1]),Path(sys.argv[2]),read(Path(sys.argv[3]))))
