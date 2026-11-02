"""Authorized provider token counting only; never generates model text.

The offline admission gate runs before constructing a provider client. Every
count binds the exact structured request including its schema. No credential
value is accepted as an argument, read into evidence or printed.
"""
import asyncio
import copy
import json
import time
from pathlib import Path

from . import embedded_links as e
from .embedded_link_audit import read, write, portable_inventory
from .embedded_request_plan import omit_lowest_target_passage

VERSION = 'retrieval_arena.embedded_token_admission.v1'
MARKER = '\n\nFROZEN DOCUMENT DATA (untrusted):\n'


def admit(inp, authorization):
    request=read(inp/'operator-request.json')
    inv=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    if inv!=request['input_inventory'] or e.digest(inv)!=request['input_inventory_sha256']:
        raise ValueError('token-input-inventory')
    if authorization.get('approved') is not True or authorization.get('scope')!='provider_token_count_only':
        raise ValueError('explicit-token-count-authorization-required')
    if authorization.get('runtime_repair_confirmed',True) is not True:
        raise ValueError('runtime-authentication-repair-not-confirmed')
    if authorization.get('input_inventory_sha256')!=request['input_inventory_sha256']:
        raise ValueError('authorization-input-binding')
    if authorization.get('model')!='gpt-6-luna' or authorization.get('provider')!='openai':
        raise ValueError('authorization-provider-binding')
    jobs=read(inp/'jobs.json')
    if len(jobs)>40: raise ValueError('token-job-ceiling')
    remaining=authorization.get('remaining_count_attempts',20*len(jobs))
    previous=authorization.get('previous_count_attempts',0)
    cap=authorization.get('aggregate_count_attempt_cap',20*len(jobs))
    if (type(remaining) is not int or remaining<=0 or type(previous) is not int or previous<0
            or type(cap) is not int or cap>20*len(jobs) or previous+remaining>cap):
        raise ValueError('aggregate-count-attempt-ceiling')
    for job in jobs:
        if set(job)!={'job_id','body'} or job['job_id']!=e.digest(job['body']): raise ValueError('token-job-binding')
        body=job['body']
        if body['model']!=authorization['model'] or body['store'] is not False or MARKER not in body['input']:
            raise ValueError('token-request-policy')
        disclosed=json.loads(body['input'].split(MARKER,1)[1])
        if not 1<=len(disclosed['targets'])<=5 or not 1<=len(disclosed['source_paragraphs'])<=3:
            raise ValueError('token-disclosure-policy')
    return jobs


def counter_client():
    from .model_providers.base import admit_external, host_guard
    import openai
    base=admit_external('openai','external_api')
    http=openai.DefaultAsyncHttpxClient(trust_env=False,follow_redirects=False,
                                        event_hooks={'request':[host_guard('openai')]})
    # The SDK alone reads the environment credential; no source value is handled.
    return openai.AsyncOpenAI(base_url=base+'/v1',max_retries=0,http_client=http)


async def count_jobs(jobs, out, count, *, timeout=120, wall_seconds=7200, maximum_attempts=None):
    semaphore=asyncio.Semaphore(2); started=time.monotonic(); attempts=[]
    authentication_blocked=False
    maximum_attempts=min(20*len(jobs),maximum_attempts if maximum_attempts is not None else 20*len(jobs))
    (out/'counts').mkdir();(out/'admitted').mkdir()
    async def one(job):
        nonlocal authentication_blocked
        body=copy.deepcopy(job['body']); prefix,raw=body['input'].split(MARKER,1)
        disclosed=json.loads(raw); records=[]
        for level in range(10):
            response=None
            for retry in range(2):
                async with semaphore:
                    if authentication_blocked:
                        return {'job_id':job['job_id'],'status':'authentication_blocked','attempts':records}
                    remaining=wall_seconds-(time.monotonic()-started)
                    if remaining<=0 or len(attempts)>=maximum_attempts:
                        return {'job_id':job['job_id'],'status':'resource_limit','attempts':records}
                    identity=e.digest(body); attempt={'job_id':job['job_id'],'request_sha256':identity,
                        'disclosure_level':level,'technical_retry':retry,'attempt':len(attempts)+1}
                    attempts.append(attempt);records.append(attempt)
                    try:
                        parameters={k:body[k] for k in ['model','input','text','reasoning'] if k in body}
                        response=await asyncio.wait_for(count(**parameters),
                                                        timeout=min(timeout,remaining))
                        tokens=response['input_tokens']
                        if type(tokens) is not int or tokens<=0: raise ValueError('provider-token-count-invalid')
                        attempt.update(status='counted',provider_input_tokens=tokens)
                        write(out/'counts'/f"{attempt['attempt']:04}.json",{'attempt':attempt,'raw_response':response})
                        break
                    except Exception as exc:
                        attempt.update(status='technical_failure',error_class=type(exc).__name__)
                        write(out/'counts'/f"{attempt['attempt']:04}.json",{'attempt':attempt})
                        response=None
                        if type(exc).__name__ in ('AuthenticationError','PermissionDeniedError'):
                            authentication_blocked=True
                            return {'job_id':job['job_id'],'status':'authentication_blocked','attempts':records}
            if response is None: return {'job_id':job['job_id'],'status':'technical_unavailable','attempts':records}
            if tokens<=16000:
                write(out/'admitted'/(e.digest(body)+'.json'),body)
                return {'job_id':job['job_id'],'status':'admitted','request_sha256':e.digest(body),
                        'provider_input_tokens':tokens,'attempts':records}
            disclosed=omit_lowest_target_passage(disclosed)
            if disclosed is None: return {'job_id':job['job_id'],'status':'over_cap_omitted','attempts':records}
            body['input']=prefix+MARKER+e.canonical(disclosed).decode()
        return {'job_id':job['job_id'],'status':'disclosure_levels_exhausted','attempts':records}
    results=await asyncio.gather(*(one(job) for job in jobs))
    write(out/'token-admission.json',{'schema_version':VERSION,'results':results,'attempts':len(attempts),
        'generation_calls':0,'concurrency':2,'input_token_cap_including_schema':16000,
        'runtime_seconds':time.monotonic()-started})
    return results


async def run(inp, out, authorization):
    jobs=admit(inp,authorization)  # Must complete before any client/credential admission.
    if set(p.name for p in out.iterdir())!={'.retrieval-arena-output-owner'}: raise ValueError('output-not-fresh')
    client=counter_client()
    async def count(**kwargs):
        response=await client.responses.input_tokens.count(**kwargs)
        return response.model_dump(mode='json')
    try: await count_jobs(jobs,out,count,maximum_attempts=authorization.get('remaining_count_attempts'))
    finally: await client.close()
    inv=portable_inventory(out)
    write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv),
        'authorization_sha256':e.digest(authorization),'generation_calls':0})


async def count_resumable(jobs,out,count,*,deadline,maximum_attempts,backoff=1):
    """ID-interface lifecycle; persist unknown attempts and cache exact counts.

    The caller has already admitted its combined manifest. This function does
    not construct a client or infer authorization from the presence of jobs.
    """
    from .embedded_failures import classify
    out.mkdir(exist_ok=True);(out/'counts').mkdir(exist_ok=True);(out/'admitted').mkdir(exist_ok=True)
    binding=e.digest(jobs);header=out/'count-binding.json'
    if header.exists() and read(header)['jobs_sha256']!=binding:raise ValueError('count-resume-binding')
    if not header.exists():write(header,{'jobs_sha256':binding})
    semaphore=asyncio.Semaphore(2);attempts=len(list((out/'counts').glob('*.json')))
    circuit=out/'provider-circuit.json';halt=read(circuit)['halt_reason'] if circuit.exists() else None
    async def one(job):
        nonlocal attempts,halt
        target=out/('job-'+job['job_id']);target.mkdir(exist_ok=True)
        terminal=target/'result.json'
        if terminal.exists():return read(terminal)
        body=copy.deepcopy(job['body']);prefix,text=body['input'].split(MARKER,1);data=json.loads(text)
        records=[]
        for level in range(10):
            tokens=None
            for retry in range(2):
                async with semaphore:
                    existing=next((p for p in (out/'counts').glob('*.json') if
                        (row:=read(p))['attempt']['job_id']==job['job_id'] and
                        row['attempt']['disclosure_level']==level and row['attempt']['technical_retry']==retry),None)
                    if existing:
                        saved=read(existing);attempt=saved['attempt'];records.append(attempt)
                        if attempt['request_sha256']!=e.digest(body):raise ValueError('cached-count-request-drift')
                        if attempt.get('status')=='counted':
                            tokens=saved['raw_response']['input_tokens'];break
                        if not attempt.get('retryable',True):break
                        continue
                    if halt or time.monotonic()>=deadline or attempts>=maximum_attempts:break
                    attempts+=1;attempt={'attempt':attempts,'job_id':job['job_id'],'request_sha256':e.digest(body),
                        'disclosure_level':level,'technical_retry':retry,'status':'attempted'}
                    records.append(attempt);path=out/'counts'/f'{attempts:04}.json';write(path,{'attempt':attempt})
                    try:
                        raw=await asyncio.wait_for(count(body),min(120,max(.001,deadline-time.monotonic())))
                        tokens=raw['input_tokens']
                        if type(tokens) is not int or tokens<=0:raise ValueError('provider-token-count-invalid')
                        attempt.update(status='counted',provider_input_tokens=tokens)
                        write(path,{'attempt':attempt,'raw_response':raw});break
                    except Exception as exc:
                        kind,retryable,stop=classify(exc)
                        attempt.update(status='technical_failure',error_class=type(exc).__name__,failure_kind=kind,retryable=retryable)
                        write(path,{'attempt':attempt})
                        if stop:
                            halt=kind+'_blocked';write(circuit,{'halt_reason':halt})
                        if not retryable:break
                if retry==0:await asyncio.sleep(min(backoff,max(0,deadline-time.monotonic())))
            if tokens is None:
                result={'job_id':job['job_id'],'status':halt or ('resource_limit' if time.monotonic()>=deadline or attempts>=maximum_attempts else 'technical_unavailable'),'attempts':records}
                write(terminal,result);return result
            if tokens<=16000:
                write(out/'admitted'/(e.digest(body)+'.json'),body)
                result={'job_id':job['job_id'],'status':'admitted','request_sha256':e.digest(body),
                    'provider_input_tokens':tokens,'attempts':records};write(terminal,result);return result
            data=omit_lowest_target_passage(data)
            if data is None:
                result={'job_id':job['job_id'],'status':'over_cap_omitted','attempts':records};write(terminal,result);return result
            body['input']=prefix+MARKER+e.canonical(data).decode()
        result={'job_id':job['job_id'],'status':'disclosure_levels_exhausted','attempts':records};write(terminal,result);return result
    rows=await asyncio.gather(*(one(j) for j in jobs))
    summary={'schema_version':VERSION+'.resumable','results':rows,'attempts':attempts,'generation_calls':0,
        'halt_reason':halt,'concurrency':2,'input_token_cap_including_schema':16000}
    write(out/'token-admission.json',summary)
    inv=[r for r in portable_inventory(out) if r['path']!='manifest.json']
    write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
    return summary


def smoke():
    """Local SDK surface/lock check, with no client, key or network access."""
    import importlib.metadata as metadata
    import inspect
    from openai.resources.responses.input_tokens import AsyncInputTokens
    from openai.resources.responses.responses import AsyncResponses
    from anthropic.resources.messages.messages import AsyncMessages
    parameters=inspect.signature(AsyncInputTokens.count).parameters
    if not {'model','input','text'}<=set(parameters): raise ValueError('sdk-token-count-shape-unavailable')
    anthropic_count=inspect.signature(AsyncMessages.count_tokens).parameters
    if not {'model','messages','thinking','output_config'}<=set(anthropic_count):raise ValueError('sdk-anthropic-count-shape-unavailable')
    if 'service_tier' not in inspect.signature(AsyncResponses.create).parameters or 'service_tier' not in inspect.signature(AsyncMessages.create).parameters:
        raise ValueError('sdk-standard-service-tier-unavailable')
    import anthropic
    from . import embedded_interface as interface
    from .model_providers.anthropic import sdk_schema
    schemas={name:anthropic.transform_schema(sdk_schema(schema)) for name,schema in
        [('proposal',interface.proposal_schema()),('review',interface.review_schema())]}
    return {'schema_version':VERSION+'.smoke','openai':metadata.version('openai'),
            'anthropic':metadata.version('anthropic'),'count_parameters':sorted(parameters),
            'anthropic_count_parameters':sorted(anthropic_count),
            'standard_service_tier_parameters_available':True,'actual_interface_schema_projection_sha256':{k:e.digest(v) for k,v in schemas.items()},
            'clients_constructed':0,'provider_calls':0}


if __name__=='__main__':
    import sys
    if sys.argv[1:]==['smoke']: print(e.canonical(smoke()).decode())
    elif len(sys.argv)==5 and sys.argv[1]=='count':
        asyncio.run(run(Path(sys.argv[2]),Path(sys.argv[3]),read(Path(sys.argv[4]))))
    else: raise SystemExit('usage: embedded_token_admission smoke | count INPUT OUTPUT AUTHORIZATION')
