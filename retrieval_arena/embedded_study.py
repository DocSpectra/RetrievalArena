"""Combined, immutable refinement execution; no additional provider stack.

The consumer freezes real and synthetic source bundles, prompts and role caps.
Only the top-level, inventory-bound authorization admits clients. Every derived
job is produced by the existing RA projector; completed jobs are never redrawn.
"""
import asyncio
import copy
import os
from pathlib import Path
import shutil
import time

from . import embedded_links as e
from . import embedded_refinement as f
from . import embedded_model_run as m
from . import embedded_token_admission as t
from . import embedded_interface as interface
from .embedded_link_audit import read,write,portable_inventory
from .model_providers import transport_for

VERSION='retrieval_arena.embedded_study.v1'


def seal(path):
    rows=[r for r in portable_inventory(path) if r['path']!='operator-request.json']
    write(path/'operator-request.json',{'input_inventory':rows,'input_inventory_sha256':e.digest(rows)})


def verify_output(path):
    manifest=read(path/'manifest.json')
    rows=[r for r in portable_inventory(path) if r['path']!='manifest.json']
    if rows!=manifest['outputs'] or e.digest(rows)!=manifest['outputs_sha256']:raise ValueError('study-output-tamper')


def admit(inp,authorization):
    req=read(inp/'operator-request.json');rows=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    if req['input_inventory']!=rows or e.digest(rows)!=req['input_inventory_sha256']:raise ValueError('study-input-inventory')
    manifest=read(inp/'study-manifest.json')
    if authorization.get('approved') is not True or authorization.get('scope')!='bounded_embedded_refinement_v3':
        raise ValueError('explicit-combined-authorization-required')
    if authorization.get('input_inventory_sha256')!=req['input_inventory_sha256'] or authorization.get('study_manifest_sha256')!=e.digest(manifest):
        raise ValueError('combined-authorization-binding')
    ceiling=30 if manifest.get('competitive_selection_version')=='retrieval_arena.embedded_competitive.v1' else 20
    if manifest['interface_version']!=interface.VERSION or manifest['aggregate_hard_cap_usd']>ceiling:
        raise ValueError('combined-policy')
    maximum=0
    for lane in ('real','preflight'):
        study=read(inp/lane/'study-manifest.json')
        if study['interface_version']!=interface.VERSION or study['lifecycle_policy']!=manifest['lifecycle_policy']:
            raise ValueError('lane-interface-policy')
        for role in study['roles']:
            m.validate_settings(role['settings'])
            if role['attempts_max']!=2*role['initial_items_max']:raise ValueError('lane-attempts')
            maximum+=role['settings']['spend_cap_usd']
    maximum+=sum(manifest['generation_count_cap_usd'].values())
    if maximum>manifest['maximum_reserved_usd']+1e-12 or maximum>manifest['aggregate_hard_cap_usd']:
        raise ValueError('combined-reservation-ceiling')
    if manifest['live_seconds']>7200 or manifest['concurrency']!=2:raise ValueError('combined-time-concurrency')
    return manifest


def freeze_pool(records,out):
    write(out/'common-pool.json',{'records':records,'records_sha256':e.digest(records),
        'eligible_proposal_ids':sorted(r['proposal']['proposal_id'] for r in records if r.get('semantic_eligible')),
        'frozen_before_selection':True})


class Study:
    def __init__(self,inp,out,manifest,*,factory=transport_for,counter=m.sdk_count):
        self.inp=inp;self.out=out;self.manifest=manifest;self.factory=factory;self.counter=counter
        out.mkdir(exist_ok=True);header=out/'study-binding.json';binding=e.digest([manifest,read(inp/'operator-request.json')])
        if header.exists() and read(header)['binding_sha256']!=binding:raise ValueError('study-resume-binding')
        if not header.exists():
            if any(p.name!='.retrieval-arena-output-owner' for p in out.iterdir()):raise ValueError('study-output-not-fresh')
            write(header,{'binding_sha256':binding,'started_unix':time.time(),'started_monotonic':time.monotonic()})
        anchor=read(header);elapsed=max(0,time.time()-anchor['started_unix'],time.monotonic()-anchor['started_monotonic'])
        self.deadline=time.monotonic()+max(0,manifest['live_seconds']-elapsed)

    def remaining(self):return max(0,self.deadline-time.monotonic())

    def offline(self,name,source,files,cfg):
        inp=self.out/(name+'-input');out=self.out/(name+'-output')
        if not inp.exists():
            shutil.copytree(source,inp)
            for key,value in files.items():
                dest=inp/key
                if isinstance(value,Path):
                    if value.is_dir():shutil.copytree(value,dest)
                    else:shutil.copyfile(value,dest)
                else:write(dest,value)
            write(inp/'config.json',cfg);seal(inp)
        if (out/'manifest.json').exists():verify_output(out);return out
        out.mkdir(exist_ok=True)
        if any(p.name!='.retrieval-arena-output-owner' for p in out.iterdir()):
            raise ValueError('interrupted-offline-stage-preserve-and-inspect:'+name)
        (out/'.retrieval-arena-output-owner').write_bytes(b'embedded-study\n')
        f.run(inp,out);return out

    async def role(self,name,study,role,jobs):
        out=self.out/(name+'-output');inp=self.out/(name+'-input')
        policy=next(r for r in study['roles'] if r['role']==role)
        # Partition caps add exactly to the frozen parent lane maximum, even if
        # a job exhausts both attempts. No score-dependent transfer is possible.
        derived=copy.deepcopy(study);r=next(r for r in derived['roles'] if r['role']==role)
        per_item=policy['settings']['spend_cap_usd']/max(1,policy['initial_items_max'])
        r['initial_items_max']=len(jobs);r['attempts_max']=2*len(jobs)
        r['settings']['spend_cap_usd']=max(1e-12,len(jobs)*per_item)
        if role=='generation':derived['generation_jobs_sha256']=e.digest(jobs)
        derived['parent_manifest_sha256']=e.digest(study)
        if not inp.exists():
            inp.mkdir();write(inp/'study-manifest.json',derived);write(inp/'jobs.json',jobs)
            write(inp/'config.json',{'role':role,'settings':r['settings'],'remaining_inference_seconds':self.remaining()});seal(inp)
        else:
            if read(inp/'jobs.json')!=jobs or read(inp/'study-manifest.json')!=derived:raise ValueError('derived-role-resume-drift')
        cfg=read(inp/'config.json');derived=read(inp/'study-manifest.json')
        # Reuse the existing immutable model admission and executor. Authorization
        # here is a derived internal receipt after top-level admission, never a
        # consumer approval file or a claim that a user approved this stage alone.
        auth={'approved':True,'scope':'bounded_embedded_refinement_v2','study_manifest_sha256':e.digest(derived),
              'derived_from_combined_manifest_sha256':e.digest(self.manifest)}
        m.admit(inp,auth)
        if (out/'summary.json').exists():
            prior=read(out/'summary.json')
            if prior.get('halt_reason'):raise RuntimeError('provider-circuit-open:'+prior['halt_reason'])
        result=await m.execute(cfg,derived,jobs,out,self.factory,self.counter)
        inv=[r for r in portable_inventory(out) if r['path']!='manifest.json']
        write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
        if result.get('halt_reason'):raise RuntimeError('provider-circuit-open:'+result['halt_reason'])
        return out

    async def counts(self,lane,source,study):
        jobs=read(source/'token-input/jobs.json');out=self.out/(lane+'-counts-output')
        if not jobs:raise ValueError('empty-frozen-count-inventory')
        # Count endpoint belongs to the same admitted shared transport/SDK.
        settings=copy.deepcopy(next(r for r in study['roles'] if r['role']=='generation')['settings'])
        settings['spend_cap_usd']=self.manifest['generation_count_cap_usd'][lane]
        out.mkdir(exist_ok=True)
        if (out/'provider-circuit.json').exists():raise RuntimeError('provider-circuit-open:'+read(out/'provider-circuit.json')['halt_reason'])
        transport=self.factory(settings,'external_api',out)
        async def count(body):
            cap=self.manifest['lifecycle_policy']['maximum_count_request_bytes']
            if len(e.canonical(body))>cap:raise RuntimeError('count-request-byte-cap')
            reservation=transport.ledger.reserve(cap+1024,1,e.digest(body))
            transport.ledger.fail(reservation,'count-billing-contingency-no-invoice')
            return await self.counter(transport,body,settings['provider'])
        try:
            summary=await t.count_resumable(jobs,out,count,deadline=self.deadline,
                maximum_attempts=self.manifest['count_attempts'][lane],backoff=self.manifest['lifecycle_policy']['retry_backoff_seconds'])
        finally:
            if hasattr(transport,'client') and hasattr(transport.client,'close'):await transport.client.close()
        if summary['halt_reason']:raise RuntimeError('provider-circuit-open:'+summary['halt_reason'])
        return out

    def merge(self,name,outputs):
        dest=self.out/name
        if (dest/'manifest.json').exists():verify_output(dest);return dest
        dest.mkdir(exist_ok=False);(dest/'jobs').mkdir();rows=[]
        for output in outputs:
            verify_output(output);summary=read(output/'summary.json');rows.extend(summary['results'])
            if (output/'jobs').exists():
                for p in (output/'jobs').iterdir():shutil.copytree(p,dest/'jobs'/p.name)
        write(dest/'summary.json',{'results':rows,'authentication_blocked':False,
            'merged_outputs':[str(p.relative_to(self.out)) for p in outputs]})
        inv=portable_inventory(dest);write(dest/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})
        return dest

    async def lane(self,lane):
        source=self.inp/lane;study=read(source/'study-manifest.json')
        counted=await self.counts(lane,source,study)
        admission=self.offline(lane+'-admission',source,{'token-output':counted},
            {'stage':'prepare-generation','run_id':self.manifest['run_id']+'-'+lane})
        admitted=read(admission/'study-manifest.json');jobs=read(admission/'generation-jobs.json')
        if not jobs:
            # Complete a legitimate count-omission empty pool without clients.
            admitted['provider_token_count_complete']=True
        generation_outputs=[];review_outputs=[];all_review_jobs=[]
        first_sources={p['source_id'] for p in read(source/'packets.json')[:2]}
        partitions=[jobs] if lane=='preflight' else [[j for j in jobs if j['source_id'] in first_sources],
                                                   [j for j in jobs if j['source_id'] not in first_sources]]
        for number,batch in enumerate(partitions):
            name=f'{lane}-{number}'
            generation=await self.role(name+'-generation',admitted,'generation',batch)
            generation_outputs.append(generation)
            proposed=self.offline(name+'-proposals',source,{'study-manifest.json':admitted,
                'packets.json':admission/'packets.json','model-summary.json':generation/'summary.json',
                'model-jobs.json':batch,'model-output':generation},
                {'stage':'proposals','run_id':self.manifest['run_id']+'-'+lane})
            records=read(proposed/'records.json')
            if lane=='real' and number==0:
                # Empty and editorial/placement refusals are legitimate outcomes.
                # Broken transport/schema/IDs are technical canary failures.
                failures=[r for r in records if r['status'] in ('generation_unavailable','interface_rejected')]
                canary={'frozen_generation_job_ids':[j['job_id'] for j in batch],
                    'technical_success':not failures,'technical_failures':failures,'replacement_packets':0}
                write(self.out/'canary.json',canary)
                if failures:raise RuntimeError('technical-canary-failed')
            review_jobs=read(proposed/'review-jobs.json');all_review_jobs.extend(review_jobs)
            reviews=await self.role(name+'-eligibility',admitted,'eligibility',review_jobs)
            review_outputs.append(reviews)
            if lane=='real' and number==0:
                failures=[r for r in read(reviews/'summary.json')['results'] if r['status']!='complete']
                if failures:
                    canary['technical_success']=False;canary['review_failures']=failures;write(self.out/'canary.json',canary)
                    raise RuntimeError('technical-canary-review-failed')
        generation=self.merge(lane+'-generation-merged',generation_outputs)
        proposals=self.offline(lane+'-all-proposals',source,{'study-manifest.json':admitted,
            'packets.json':admission/'packets.json','admission-omissions.json':admission/'admission-omissions.json',
            'model-summary.json':generation/'summary.json','model-jobs.json':jobs,'model-output':generation},
            {'stage':'proposals','run_id':self.manifest['run_id']+'-'+lane})
        expected=read(proposals/'review-jobs.json')
        if expected!=all_review_jobs:raise ValueError('review-partition-drift')
        reviews=self.merge(lane+'-eligibility-merged',review_outputs)
        pool_input=self.out/(lane+'-pool-input')
        if not pool_input.exists():
            pool_input.mkdir();shutil.copyfile(proposals/'records.json',pool_input/'records.json')
            write(pool_input/'model-jobs.json',expected);shutil.copyfile(reviews/'summary.json',pool_input/'model-summary.json')
            shutil.copytree(reviews,pool_input/'model-output')
        pool=f.eligible_records(pool_input);poolout=self.out/(lane+'-pool-output');poolout.mkdir(exist_ok=True)
        frozen=poolout/'common-pool.json'
        if frozen.exists() and read(frozen)['records_sha256']!=e.digest(pool):raise ValueError('frozen-pool-drift')
        if not frozen.exists():freeze_pool(pool,poolout)
        selected=self.offline(lane+'-selection',source,{'study-manifest.json':admitted,
            'packets.json':admission/'packets.json','records.json':proposals/'records.json',
            'model-summary.json':reviews/'summary.json','model-jobs.json':expected,'model-output':reviews},
            {'stage':'select','run_id':self.manifest['run_id']+'-'+lane})
        if read(selected/'records.json')!=pool:raise ValueError('selection-common-pool-drift')
        final_jobs=read(selected/'assessment-jobs.json')
        final=await self.role(lane+'-final',admitted,'blinded_assessment',final_jobs)
        assembled=self.offline(lane+'-assessment',source,{'study-manifest.json':admitted,
            'groups.json':selected/'groups.json','model-summary.json':final/'summary.json',
            'model-jobs.json':final_jobs,'model-output':final},{'stage':'assemble'})
        if lane=='preflight':
            applications=read(selected/'applications.json');assessments=read(assembled/'assessments.json')
            if not applications or any(a['applied']<1 for a in applications) or not assessments or any(a['status']!='complete' for a in assessments):
                raise RuntimeError('synthetic-preflight-did-not-reach-final-edit')
        return {'selection':selected.relative_to(self.out).as_posix(),'assessment':assembled.relative_to(self.out).as_posix(),
            'packets_admitted':len(jobs),'packets_omitted':len(read(admission/'admission-omissions.json'))}

    async def run(self):
        try:
            preflight=await self.lane('preflight')
            write(self.out/'preflight-success.json',{'technical_only':True,'result':preflight})
            real=await self.lane('real')
            result={'schema_version':VERSION,'status':'complete','preflight':preflight,'real':real}
        except Exception as exc:
            # Error kinds only; never persist arbitrary SDK exception bodies.
            result={'schema_version':VERSION,'status':'checkpoint','error_class':type(exc).__name__,
                'reason':str(exc) if isinstance(exc,(ValueError,RuntimeError)) and not 'request-failed' in str(exc) else 'technical-failure',
                'remaining_seconds':self.remaining()}
            write(self.out/'checkpoint.json',result);raise
        write(self.out/'summary.json',result)
        rows=[r for r in portable_inventory(self.out) if r['path']!='manifest.json']
        write(self.out/'manifest.json',{'outputs':rows,'outputs_sha256':e.digest(rows)})
        return result


async def run(inp,out,authorization):
    if read(inp/'study-manifest.json').get('blinded_support_version'):
        from .embedded_blinded_support import run as blinded_run
        return await blinded_run(inp,out,authorization)
    manifest=admit(inp,authorization)  # Before any client exists.
    if manifest.get('competitive_selection_version'):
        from .embedded_competitive import CompetitiveStudy
        return await CompetitiveStudy(inp,out,manifest).run()
    return await Study(inp,out,manifest).run()


if __name__=='__main__':
    import sys
    asyncio.run(run(Path(sys.argv[1]),Path(sys.argv[2]),read(Path(sys.argv[3]))))
