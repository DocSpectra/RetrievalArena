"""Opt-in, two-support k=1 comparison over an immutable all-target pool.

Uses the existing source mapper, shared executor, finite v2 selector and editor.
No corpus names, credentials, provider implementation or new numerical policy.
"""
import copy
import time
from pathlib import Path

from . import embedded_links as e
from . import embedded_interface as interface
from . import embedded_refinement as f
from . import embedded_study as study
from .embedded_link_audit import read, write
from .embedded_selection_v2 import DEFAULT_POLICY, BoundedBlockScorer, select_embedded_v2, classify

VERSION = 'retrieval_arena.embedded_competitive.v1'


def disposition_schema():
    proposal = interface.proposal_schema()['properties']['proposals']['items']
    return e._obj({'dispositions': {'type': 'array', 'minItems': 1, 'maxItems': 5,
        'items': e._obj({'target_id': e.S,
            'disposition': {'type': 'string', 'enum': ['supported_link', 'no_supported_link']},
            'reason': e.S, 'proposal': {'anyOf': [proposal, {'type': 'null'}]}})}})


def validate_dispositions(payload, source, targets):
    rows = payload['dispositions']
    ids = [d['target_id'] for d in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(targets):
        raise ValueError('target-disposition-coverage')
    for row in rows:
        p = row['proposal']
        if row['disposition'] == 'no_supported_link':
            if p is not None: raise ValueError('negative-disposition-has-proposal')
        elif not p or p['source_id'] != source or p['target_id'] != row['target_id']:
            raise ValueError('disposition-proposal-identity')
    return rows


def tie_key(source, target):
    return e.digest([3801, source, target])


def supported(verdict):
    dims = [verdict[k] for k in ('relationship_support', 'target_support', 'placement')]
    return (min(dims) >= 1 and sum(dims) >= 5 and not verdict['uncertainty']
            and not verdict['contradiction'] and verdict['relation'] != 'insufficient_evidence')


def confirmation_jobs(records, packets, contents, manifest):
    by_packet = {p['packet_sha256']: p for p in packets}
    jobs = []
    for r in records:
        if not r.get('semantic_eligible'): continue
        p = r['proposal']; packet = by_packet[r['packet_sha256']]
        data, evidence = f.review_disclosure(p, packet, contents, True)
        jobs.append(f.model_job('support_confirmation', data, evidence, p, packet, manifest))
    return jobs


def confirm(records, jobs, output):
    """Validate raw responses again; confirmation never changes Haiku ranking."""
    by_job = {j['job_id']: j for j in jobs}
    results = {pid: row for row in read(output/'summary.json')['results']
               for pid in by_job[row['job_id']]['proposal_ids']}
    result = copy.deepcopy(records)
    for r in result:
        if not r.get('semantic_eligible'): continue
        r['haiku_eligible'] = True
        row = results.get(r['proposal']['proposal_id'])
        if not row or row['status'] != 'complete':
            r.update(semantic_eligible=False, confirmation_status='review_unavailable')
            continue
        job = by_job[row['job_id']]
        f.validate_result(row['payload'], job); f.provider_result(output, row,direct=True)
        verdict, proof = interface.resolve_review(row['payload'], job['evidence'])
        passed = supported(verdict)
        r.update(semantic_eligible=passed, confirmation_status='eligible' if passed else 'semantic_rejected',
            confirmation=verdict, confirmation_declaration=row['payload'], confirmation_resolution=proof,
            confirmation_score=sum(verdict[k] for k in ('relationship_support','target_support','placement')),
            confirmation_job_id=row['job_id'], confirmation_response_sha256=row['raw_response_sha256'],
            confirmation_input_sha256=e.digest(job['evidence']))
    return result


def freeze(records, regions, *, constraint_version=None):
    """Full rejected/accepted pool and semantic winners, before any scoring."""
    f.constraints.admit_version(constraint_version)
    proposals=[r['proposal'] for r in records if 'proposal' in r]
    conflicts = f.constraints.pool_conflicts(proposals) if constraint_version else f.ledger.pool_conflicts(proposals)
    groups = []
    for region in regions:
        good = [r for r in records if r.get('semantic_eligible') and r.get('region_id') == region['region_id']]
        pool = []
        for r in good:
            if not r.get('haiku_eligible') or r.get('confirmation_status') != 'eligible':
                raise ValueError('two-stage-support-required')
            if constraint_version and r['status']!='structural_eligible':raise ValueError('individual-host-admission-required')
            p = r['proposal']; pid = p['proposal_id']
            pool.append({k:p[k] for k in ('proposal_id','source_id','target_id')} | {
                'semantic_score':r['semantic_score'], 'semantic_eligible':True,
                'tie_id':tie_key(p['source_id'],p['target_id']),
                'conflicts':sorted({other for c in conflicts if pid in c['proposal_ids']
                                    for other in c['proposal_ids'] if other != pid})})
        pairs = [(p['source_id'],p['target_id']) for p in pool]
        if len(set(pairs)) != len(pairs):
            if constraint_version is None:raise ValueError('duplicate-directed-pair')
            # Preserve every occurrence/review in records; count each pair once.
            canonical={}
            for candidate in sorted(pool,key=lambda p:(-p['semantic_score'],p['proposal_id'])):
                canonical.setdefault((candidate['source_id'],candidate['target_id']),candidate)
            pool=list(canonical.values())
        pool.sort(key=lambda p:(-p['semantic_score'],p['tie_id'],p['proposal_id']))
        tier = [p for p in pool if p['semantic_score'] == pool[0]['semantic_score']] if pool else []
        groups.append({'region_id':region['region_id'],'pool':pool, 'budget':int(bool(pool)),
            'top_tier':[p['proposal_id'] for p in tier], 'top_tier_pair_count':len(tier),
            'competitive':len(tier)>=2,'semantic_winner':tier[0]['proposal_id'] if tier else None})
    return {'schema_version':VERSION,'records':records,'records_sha256':e.digest(records),
        'groups':groups,'conflicts':conflicts,'tie_seed':3801,'frozen_before_selection':True,
        **({'constraint_version':constraint_version} if constraint_version else {})}


def outcome(plan, arms):
    by_id = {p['proposal_id']:p for p in plan['pool']}
    pairs = {a:sorted([by_id[pid]['source_id'],by_id[pid]['target_id']] for pid in arms[a]['selected'])
             for a in ('S','Z','M')}
    if pairs['S'] != pairs['Z']: raise ValueError('zero-control-pair-mismatch')
    step = arms['M']['trace'][0] if arms['M']['trace'] else None
    evaluable = bool(plan['competitive'] and step and all(
        r['status']=='finite_estimate' for r in step['raw_scores']))
    category = (1 if not plan['pool'] else 2 if not plan['competitive'] else
        3 if step['status']!='resolved_finite_order' else 4 if pairs['S']==pairs['M'] else 5)
    s, m = set(map(tuple,pairs['S'])), set(map(tuple,pairs['M']))
    return {'category':category,'competitive':plan['competitive'],'numerically_evaluable':evaluable,
        'decision_status':step['status'] if step else 'empty_pool','pairs':pairs,
        'pair_sets_equal':s==m,'intersection':sorted(s&m),'symmetric_difference':sorted(s^m),
        'replacements':int(s!=m),'qualified_changed_subset':category==5}


def select_and_apply(inp, out, cfg, *, scorer_factory=BoundedBlockScorer, diagnostic=True):
    pages, _, contents, regions = f.load_sources(inp)
    frozen = read(inp/'common-pool.json'); records = frozen['records']
    if freeze(records,regions,constraint_version=read(inp/'study-manifest.json').get('constraint_version')) != frozen: raise ValueError('competitive-pool-binding')
    manifest = read(inp/'study-manifest.json')
    if manifest.get('maximum_group_arm_edits') != 1: raise ValueError('competitive-budget')
    occurrences = read(inp/'frozen-occurrences.json'); groups=[]; state=[]
    for region, plan in zip(regions,frozen['groups']):
        if region['region_id'] != plan['region_id']: raise ValueError('competitive-region-order')
        nodes=set(region['nodes']); pool=plan['pool']
        graph={'nodes':region['nodes'],'edges':e.project([r for r in occurrences if r['source_id'] in nodes],nodes),
            'audit_binding':{'pool_sha256':e.digest(frozen),'region':region,
                'mask_sha256':e.digest(occurrences),'pages_sha256':e.digest(read(inp/'pages.json'))}}
        policy=dict(DEFAULT_POLICY,maximum=1,max_output_bytes=50*1024**2)
        if manifest.get('constraint_version'):policy['constraint_version']=manifest['constraint_version']
        start=time.monotonic(); scorer=scorer_factory(graph,pool,policy) if plan['competitive'] else None
        # Noncompetitive groups have no numerical selection test. Record fallback
        # without entering the solver, including when every group is singleton.
        def unavailable(prefix):return {'status':'not_tested','reason':'insufficient-top-tier-competition'}
        score_cache={}
        arms={a:select_embedded_v2(pool,graph,policy,score_cache,arm=a,scorer=scorer or unavailable) for a in ('S','Z','M')}
        ranking_seconds=time.monotonic()-start
        result=outcome(plan,arms); final={}
        by_id={p['proposal_id']:p for p in pool}; final_cache={}
        for a in ('S','Z','M'):
            chosen=arms[a]['selected']; key=tuple(chosen)
            if key not in final_cache:
                final_cache[key]=(scorer(key) if scorer and key else
                    {'status':'finite_estimate','schedule_costs':[0.,0.],'analytic_empty_prefix':True} if not key else unavailable(key))
            final[a]={'phase_cost':final_cache[key]}
        group={'region_id':region['region_id'],'source_families':region['source_families'],
            'nodes':len(nodes),'semantic_eligible':len(pool),'requested_budget':1,
            'feasible_budget':plan['budget'],'arms':arms,'final':final,'outcome':result,
            'ranking_seconds':ranking_seconds,'runtime_seconds':time.monotonic()-start,
            'selection_sequence_changed':arms['S']['selected']!=arms['M']['selected'],
            'final_selected_set_changed':result['replacements']==1}
        groups.append(group); state.append((graph,pool,plan,scorer,score_cache))
    # Seal all primary outcomes before any secondary-charge score is computed.
    write(out/'primary-outcomes-sealed.json',{'pool_sha256':e.digest(frozen),'groups':groups,
        'competitive_groups':sum(g['outcome']['competitive'] for g in groups),
        'numerically_evaluable_groups':sum(g['outcome']['numerically_evaluable'] for g in groups),
        'changed_groups':sum(g['outcome']['qualified_changed_subset'] for g in groups)})
    for group,(graph,pool,plan,scorer,cache) in zip(groups,state):
        audit={'group':group,'solves':getattr(scorer,'cache',{}),'score_cache':cache,
               'attempts':getattr(scorer,'attempts',[])}
        root=out/'numerical'/group['region_id'].replace(':','-');root.mkdir(parents=True)
        if len(e.canonical(audit))>100*1024**2:raise ValueError('primary-output-ceiling')
        write(root/'audit.json',audit)
        if diagnostic and plan['competitive']:
            policy=dict(DEFAULT_POLICY,maximum=1,q=.10)
            secondary=scorer_factory(graph,pool,policy)
            rows=[secondary((pid,)) for pid in plan['top_tier']]
            status,index=classify(rows,1e-6)
            diag={'q':.10,'tier':plan['top_tier'],'scores':rows,'status':status,
                'winner':plan['top_tier'][index] if status=='resolved_finite_order' else None,
                'primary_seal_sha256':e.rawhash((out/'primary-outcomes-sealed.json').read_bytes()),
                'solves':getattr(secondary,'cache',{}),'attempts':getattr(secondary,'attempts',[])}
            diag['preference_reversal']=bool(diag['winner'] and group['arms']['M']['trace'][0]['status']=='resolved_finite_order'
                and diag['winner']!=group['arms']['M']['selected'][0])
            if len(e.canonical(diag))>100*1024**2:raise ValueError('diagnostic-output-ceiling')
            write(root/'diagnostic-q010.json',diag)
    applications=f.materialize(inp,out,records,groups,pages,contents,regions)
    write(out/'records.json',records);write(out/'groups.json',groups)
    write(out/'support-provenance.json',{'pool_sha256':e.digest(frozen),'records_sha256':e.digest(records),
        'confirmation_is_preselection_gate':True,'human_validated':False})
    return {'applications':applications,'groups':len(groups),'new_provider_calls':0}


class CompetitiveStudy(study.Study):
    async def lane(self,lane):
        source=self.inp/lane; original=read(source/'study-manifest.json')
        counts=await self.counts(lane,source,original)
        admission=self.offline(lane+'-admission',source,{'token-output':counts},
            {'stage':'prepare-generation','run_id':self.manifest['run_id']+'-'+lane})
        admitted=read(admission/'study-manifest.json');jobs=read(admission/'generation-jobs.json')
        if not jobs:admitted['provider_token_count_complete']=True
        first={p['source_id'] for p in read(source/'packets.json')[:2]}
        batches=[jobs] if lane=='preflight' else [[j for j in jobs if j['source_id'] in first],
                                                  [j for j in jobs if j['source_id'] not in first]]
        gen_outputs=[]; haiku_outputs=[]; sol_outputs=[]; haiku_jobs=[]; sol_jobs=[]
        for number,batch in enumerate(batches):
            name=f'{lane}-{number}'
            generated=await self.role(name+'-generation',admitted,'generation',batch);gen_outputs.append(generated)
            proposals=self.offline(name+'-proposals',source,{'study-manifest.json':admitted,
                'packets.json':admission/'packets.json','model-summary.json':generated/'summary.json',
                'model-jobs.json':batch,'model-output':generated},
                {'stage':'proposals','run_id':self.manifest['run_id']+'-'+lane})
            records=read(proposals/'records.json');hj=read(proposals/'review-jobs.json');haiku_jobs.extend(hj)
            hr=await self.role(name+'-eligibility',admitted,'eligibility',hj);haiku_outputs.append(hr)
            poolinp=self.out/(name+'-support-input')
            if not poolinp.exists():
                poolinp.mkdir();write(poolinp/'records.json',records);write(poolinp/'model-jobs.json',hj)
                write(poolinp/'model-summary.json',read(hr/'summary.json'))
                import shutil
                shutil.copytree(hr,poolinp/'model-output')
            scored=f.eligible_records(poolinp)
            _,_,contents,_=f.load_sources(source)
            sj=confirmation_jobs(scored,read(admission/'packets.json'),contents,admitted);sol_jobs.extend(sj)
            sr=await self.role(name+'-confirmation',admitted,'support_confirmation',sj);sol_outputs.append(sr)
            if lane=='real' and number==0:
                failures=[r for r in records if r['status'] in ('generation_unavailable','interface_rejected','schema_or_access_rejected')]
                failures += [r for output in (hr,sr) for r in read(output/'summary.json')['results'] if r['status']!='complete']
                canary={'frozen_generation_job_ids':[j['job_id'] for j in batch],
                    'technical_success':not failures,'technical_failures':failures,'replacement_packets':0}
                write(self.out/'canary.json',canary)
                if failures:raise RuntimeError('technical-canary-failed')
        gen=self.merge(lane+'-generation-merged',gen_outputs)
        props=self.offline(lane+'-all-proposals',source,{'study-manifest.json':admitted,
            'packets.json':admission/'packets.json','admission-omissions.json':admission/'admission-omissions.json',
            'model-summary.json':gen/'summary.json','model-jobs.json':jobs,'model-output':gen},
            {'stage':'proposals','run_id':self.manifest['run_id']+'-'+lane})
        hr=self.merge(lane+'-eligibility-merged',haiku_outputs)
        sr=self.merge(lane+'-confirmation-merged',sol_outputs)
        # Global conflicts may reject a proposal reviewed during the canary;
        # retain that review as observed evidence but do not resurrect the pair.
        poolinp=self.out/(lane+'-pool-input')
        if not poolinp.exists():
            import shutil
            poolinp.mkdir();shutil.copyfile(props/'records.json',poolinp/'records.json')
            write(poolinp/'model-jobs.json',haiku_jobs);shutil.copyfile(hr/'summary.json',poolinp/'model-summary.json')
            shutil.copytree(hr,poolinp/'model-output')
        records=confirm(f.eligible_records(poolinp),sol_jobs,sr)
        frozen=freeze(records,read(source/'regions.json'),constraint_version=admitted.get('constraint_version'));poolout=self.out/(lane+'-pool-output');poolout.mkdir(exist_ok=True)
        path=poolout/'common-pool.json'
        if path.exists() and read(path)!=frozen:raise ValueError('frozen-pool-drift')
        if not path.exists():write(path,frozen)
        if lane=='real' and self.manifest.get('publish_real_pool_before_selection'):
            receipt=self.out/'real-pool-publication.json'
            if not receipt.exists():raise RuntimeError('real-pool-publication-required-before-selection')
            publication=read(receipt)
            if publication.get('pool_sha256')!=e.digest(frozen) or not publication.get('context_commit'):
                raise ValueError('real-pool-publication-binding')
        selected=self.offline(lane+'-selection',source,{'study-manifest.json':admitted,
            'packets.json':admission/'packets.json','common-pool.json':frozen},
            {'stage':'competitive-select','run_id':self.manifest['run_id']+'-'+lane})
        if lane=='preflight' and not any(a['applied'] for a in read(selected/'applications.json')):
            raise RuntimeError('synthetic-preflight-did-not-reach-supported-edit')
        return {'selection':selected.relative_to(self.out).as_posix(),'support_gate':'two-stage-before-selection',
            'packets_admitted':len(jobs),'packets_omitted':len(read(admission/'admission-omissions.json'))}
