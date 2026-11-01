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
    if authorization.get('input_inventory_sha256')!=request['input_inventory_sha256']:
        raise ValueError('authorization-input-binding')
    if authorization.get('model')!='gpt-6-luna' or authorization.get('provider')!='openai':
        raise ValueError('authorization-provider-binding')
    jobs=read(inp/'jobs.json')
    if len(jobs)>40: raise ValueError('token-job-ceiling')
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


async def count_jobs(jobs, out, count, *, timeout=120, wall_seconds=7200):
    semaphore=asyncio.Semaphore(2); started=time.monotonic(); attempts=[]
    maximum_attempts=20*len(jobs)  # <=10 deterministic disclosure levels, one technical retry each
    (out/'counts').mkdir();(out/'admitted').mkdir()
    async def one(job):
        body=copy.deepcopy(job['body']); prefix,raw=body['input'].split(MARKER,1)
        disclosed=json.loads(raw); records=[]
        for level in range(10):
            response=None
            for retry in range(2):
                async with semaphore:
                    remaining=wall_seconds-(time.monotonic()-started)
                    if remaining<=0 or len(attempts)>=maximum_attempts:
                        return {'job_id':job['job_id'],'status':'resource_limit','attempts':records}
                    identity=e.digest(body); attempt={'job_id':job['job_id'],'request_sha256':identity,
                        'disclosure_level':level,'technical_retry':retry,'attempt':len(attempts)+1}
                    attempts.append(attempt);records.append(attempt)
                    try:
                        response=await asyncio.wait_for(count(model=body['model'],input=body['input'],text=body['text']),
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
    try: await count_jobs(jobs,out,count)
    finally: await client.close()
    inv=portable_inventory(out)
    write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv),
        'authorization_sha256':e.digest(authorization),'generation_calls':0})


def smoke():
    """Local SDK surface/lock check, with no client, key or network access."""
    import importlib.metadata as metadata
    import inspect
    from openai.resources.responses.input_tokens import AsyncInputTokens
    parameters=inspect.signature(AsyncInputTokens.count).parameters
    if not {'model','input','text'}<=set(parameters): raise ValueError('sdk-token-count-shape-unavailable')
    return {'schema_version':VERSION+'.smoke','openai':metadata.version('openai'),
            'anthropic':metadata.version('anthropic'),'count_parameters':sorted(parameters),
            'clients_constructed':0,'provider_calls':0}


if __name__=='__main__':
    import sys
    if sys.argv[1:]==['smoke']: print(e.canonical(smoke()).decode())
    elif len(sys.argv)==5 and sys.argv[1]=='count':
        asyncio.run(run(Path(sys.argv[2]),Path(sys.argv[3]),read(Path(sys.argv[4]))))
    else: raise SystemExit('usage: embedded_token_admission smoke | count INPUT OUTPUT AUTHORIZATION')
