"""Full-page, all-candidate two-reviewer orchestration; no provider implementation.

Public evidence is a closed projection. Private bindings and review inventories
never enter prompts. Selection reuses verified numerical dependencies, not labels.
"""
import copy
import json
import shutil
import time
from pathlib import Path

from . import embedded_links as e
from . import embedded_interface as interface
from . import embedded_curated as curated
from . import embedded_competitive as competitive
from . import embedded_minimum_set as minimum
from . import embedded_minimum_set_study as historical
from . import embedded_model_run as model
from . import embedded_study as study
from .embedded_link_audit import read, write, portable_inventory

VERSION='retrieval_arena.embedded_blinded_support.v1'
ROLES=('eligibility','support_confirmation')
PUBLIC={'schema_version','anchor','source_sentence','source_span','source_id','target_id',
        'pages','omissions','editable_anchor_only'}
PAGE={'role','doc_id','sha256','passage_id','raw_span','text','readonly'}


def disclosure(packet):
    if set(packet)!=PUBLIC or packet['omissions'] or not packet['editable_anchor_only']:
        raise ValueError('blinded-public-fields')
    if len(packet['pages'])!=2 or [p['role'] for p in packet['pages']]!=['source','target']:
        raise ValueError('blinded-page-roles')
    evidence={'interface_version':interface.VERSION}
    for page in packet['pages']:
        if set(page)!=PAGE:raise ValueError('blinded-page-fields')
        raw=page['text'].encode()
        if (page['sha256']!=e.rawhash(raw) or page['raw_span']!=[0,len(raw)] or
            page['doc_id']!=packet[page['role']+'_id'] or not page['readonly']):
            raise ValueError('blinded-whole-page-binding')
        # Literal full-page mapping; source/target IDs cannot cross-satisfy quotes.
        mapping=[];at=0
        for char in page['text']:
            mapping.append([at,at+len(char.encode())]);at+=len(char.encode())
        evidence[page['role']]=[{'passage_id':page['passage_id'],'role':page['role'],
            'doc_id':page['doc_id'],'source_sha256':page['sha256'],'raw_span':page['raw_span'],
            'text':page['text'],'raw_excerpt':page['text'],'raw_byte_map':mapping}]
    a,z=packet['source_span'];source=packet['pages'][0]['text'].encode()
    if source[a:z].decode()!=packet['anchor']:raise ValueError('blinded-anchor-binding')
    return copy.deepcopy(packet),evidence


def jobs_for(records, packets, manifest):
    jobs={r:[] for r in ROLES};bindings=[]
    for row in records:
        if row['status']!='admitted':continue
        p=row['proposal'];packet=packets[row['packet_sha256']]
        if e.digest(packet)!=row['packet_sha256']:raise ValueError('blinded-packet-hash')
        public,evidence=disclosure(packet)
        if public!=curated.review_packet(p,public['pages'][0]['text'].encode(),public['pages'][1]['text'].encode()):
            raise ValueError('blinded-candidate-packet')
        binding={'proposal_id':p['proposal_id'],'audit_id':row['audit_id'],
            'evidence_sha256':e.digest(public),'jobs':{}}
        for role in ROLES:
            policy=next(r for r in manifest['roles'] if r['role']==role)
            if policy['schema']!=interface.review_schema():raise ValueError('blinded-schema')
            job={'source_id':p['source_id'],'target_id':p['target_id'],
                'schema':policy['schema'],'prompt':policy['prompt_prefix']+'\nDOCUMENT_EVIDENCE_JSON\n'+e.canonical(public).decode(),
                'evidence':evidence}
            job['job_id']=e.digest(job);jobs[role].append(job);binding['jobs'][role]=job['job_id']
        if jobs[ROLES[0]][-1]['prompt']!=jobs[ROLES[1]][-1]['prompt']:
            raise ValueError('blinded-evidence-parity')
        bindings.append(binding)
    if len({b['proposal_id'] for b in bindings})!=len(bindings):raise ValueError('blinded-duplicate-candidate')
    return jobs,bindings


def prepare(inp,out,cfg):
    pages,_,contents,regions=curated.f.load_sources(inp);frozen=read(inp/'curated-freeze.json')
    for name,h in frozen['input_bindings'].items():
        if e.rawhash((inp/name).read_bytes())!=h:raise ValueError('blinded-input-binding')
    for row in frozen['records']:
        if row['status']=='admitted':
            p=row['proposal']
            if curated.review_packet(p,contents[p['source_id']],contents[p['target_id']])!=frozen['packets'][row['packet_sha256']]:
                raise ValueError('blinded-source-packet-binding')
    jobs,bindings=jobs_for(frozen['records'],frozen['packets'],read(inp/'study-manifest.json'))
    write(out/'jobs.json',jobs);write(out/'private-bindings.json',bindings)
    if (inp/'synthetic-fixtures.json').exists():
        synthetic={role:[] for role in ROLES}
        for fixture in read(inp/'synthetic-fixtures.json'):
            source,target=fixture['source'].encode(),fixture['target'].encode()
            at=source.index(fixture['anchor'].encode())
            p={'proposal_id':fixture['key'],'source_id':'synthetic-source','target_id':fixture['key'],
               'anchor':fixture['anchor'],'sentence':fixture['sentence'],'source_span':[at,at+len(fixture['anchor'].encode())]}
            packet=curated.review_packet(p,source,target);h=e.digest(packet)
            rows,_=jobs_for([{'status':'admitted','audit_id':fixture['key'],'proposal':p,'packet_sha256':h}],{h:packet},read(inp/'study-manifest.json'))
            for role in ROLES:synthetic[role].extend(rows[role])
        write(out/'preflight-jobs.json',synthetic)
    return {'candidates':len(bindings),'jobs':sum(map(len,jobs.values())),'groups':len(regions),
            'source_files':len(pages),'provider_calls':0}


def pool(frozen,regions,jobs,bindings,outputs):
    """Retain every review, including negatives and technical exhaustion."""
    reviews={};inventory=[]
    if {b['proposal_id'] for b in bindings}!={r['proposal']['proposal_id'] for r in frozen['records'] if r['status']=='admitted'}:
        raise ValueError('blinded-complete-inventory')
    for role in ROLES:
        expected=[b['jobs'][role] for b in bindings]
        if len(set(expected))!=len(expected) or set(expected)!={j['job_id'] for j in jobs[role]}:
            raise ValueError('blinded-job-coverage')
        if set(expected)!={r['job_id'] for r in outputs[role]['results']} or len(outputs[role]['results'])!=len(expected):
            raise ValueError('blinded-response-coverage')
    for binding in bindings:
        assessments={};states={};raw={}
        for role in ROLES:
            job=next(j for j in jobs[role] if j['job_id']==binding['jobs'][role])
            result=next(r for r in outputs[role]['results'] if r['job_id']==job['job_id'])
            raw[role]=copy.deepcopy(result)
            raw[role].pop('cache_reused',None)
            if result['status']!='complete':states[role]='technical_unavailable';continue
            model.validate_result(result['payload'],job)
            verdict,proof=interface.resolve_review(result['payload'],job['evidence'])
            assessments[role]={'verdict':verdict,'declaration':result['payload'],'resolution':proof,
                'score':sum(verdict[k] for k in curated.DIMS)}
            states[role]='pass' if competitive.supported(verdict) else 'negative'
        a,b=[states[r] for r in ROLES]
        category=('technical_unavailable' if 'technical_unavailable' in (a,b) else
                  'both_pass' if a==b=='pass' else 'haiku_only' if a=='pass' else
                  'sol_only' if b=='pass' else 'both_negative')
        if category=='both_pass':reviews[binding['proposal_id']]={
            'first':assessments[ROLES[0]]['verdict'],'confirmation':assessments[ROLES[1]]['verdict']}
        inventory.append({**binding,'status':category,'states':states,'assessments':assessments,'responses':raw})
    common=curated.analysis_pool(frozen['records'],regions,'B',reviews)
    return {'schema_version':VERSION,'inventory':inventory,'common_pool':common,
            'jobs_sha256':e.digest(jobs),'bindings_sha256':e.digest(bindings),'frozen_before_selection':True}


def select_supported(inp,out,support,*,loaded=None):
    # Replays original score provenance before binding the newly ranked pool.
    pages,contents,regions,frozen,oldgroups,proofs=loaded or historical.load_bound(inp)
    jobs=read(inp/'jobs.json');bindings=read(inp/'private-bindings.json')
    summaries={role:{'results':[row['responses'][role] for row in support['inventory']]} for role in ROLES}
    if pool(frozen,regions,jobs,bindings,summaries)!=support:raise ValueError('blinded-support-replay')
    occurrences=read(inp/'frozen-occurrences.json');groups=[]
    policy=dict(minimum.v2.DEFAULT_POLICY,maximum=1,constraint_version=curated.constraints.VERSION)
    for region,plan,old in zip(regions,support['common_pool']['groups'],oldgroups):
        graph=historical.graph_for(region,frozen,occurrences)
        graph['audit_binding']['supported_pool_sha256']=e.digest(support)
        costs=old['candidate_costs']
        def scorer(prefix):
            if len(prefix)!=1 or prefix[0] not in costs:raise ValueError('blinded-score-prefix')
            return costs[prefix[0]]
        arms={a:minimum.select_minimum_set(plan['pool'],graph,policy,{},arm=a,scorer=scorer) for a in ('S','Z','M')}
        if arms['S']['selected']!=arms['Z']['selected']:raise ValueError('blinded-zero-control')
        step=arms['M']['trace'][0] if arms['M']['trace'] else None
        competitive_group=len(plan['top_tier'])>1
        changed=arms['M']['selected']!=arms['S']['selected']
        resolved=bool(step and step['status'] in ('resolved_finite_order','separated_minimum_set'))
        by_id={p['proposal_id']:p for p in plan['pool']}
        pairsets={a:{(by_id[pid]['source_id'],by_id[pid]['target_id']) for pid in arms[a]['selected']} for a in arms}
        groups.append({**plan,'arms':arms,'competitive':competitive_group,'numerically_evaluable':bool(competitive_group and step and
            all(r['status']=='finite_estimate' for r in step['raw_scores'])),
            'qualified_changed_subset':bool(competitive_group and changed and resolved),
            'decision_status':step['status'] if step else 'empty_pool',
            'pairs':{a:sorted(v) for a,v in pairsets.items()},
            'intersection':sorted(pairsets['S']&pairsets['M']),
            'symmetric_difference':sorted(pairsets['S']^pairsets['M']),
            'historical_semantic_winner':old['arms']['S']['selected'],
            'candidate_costs':{pid:costs[pid] for pid in plan['top_tier']}})
    write(out/'groups.json',groups);write(out/'score-reuse-proof.json',proofs)
    write(out/'primary-outcomes-sealed.json',{'support_sha256':e.digest(support),'selector':minimum.VERSION,'groups':groups})
    materialize(inp,out,support,frozen,groups,pages,contents,regions)
    return {'groups':len(groups),'supported':sum(len(g['pool']) for g in groups),
        'competitive':sum(g['competitive'] for g in groups),'evaluable':sum(g['numerically_evaluable'] for g in groups),
        'changed':sum(g['qualified_changed_subset'] for g in groups),'provider_calls':0}


def materialize(inp,out,support,frozen,groups,pages,contents,regions):
    """Use the existing model-review editor and anchored ledger, retain curator origin."""
    by_id={r['proposal']['proposal_id']:r for r in frozen['records'] if r['status']=='admitted'}
    records=[];packets=[]
    for row in support['inventory']:
        original=by_id[row['proposal_id']];p=copy.deepcopy(original['proposal'])
        packet=frozen['packets'][original['packet_sha256']]
        packets.append({'packet_sha256':original['packet_sha256'],'source_paragraphs':[
            {'raw_span':packet['pages'][0]['raw_span']}]})
        record={'proposal':p,'region_id':p['region_id'],'packet_sha256':original['packet_sha256'],
            'status':'structural_eligible','structural_facts_sha256':original['host_facts_sha256'],
            'semantic_eligible':row['status']=='both_pass','semantic_score':row['assessments'].get(ROLES[0],{}).get('score',0)}
        for role,key in zip(ROLES,('review','confirmation')):
            if role not in row['assessments']:continue
            record[key]=row['assessments'][role]['verdict']
            record[key+'_response_sha256']=row['responses'][role]['raw_response_sha256']
            record[key+'_input_sha256']=row['evidence_sha256']
        if row['status']=='both_pass':record.update(haiku_eligible=True,confirmation_status='eligible')
        records.append(record)
    # Editor inputs are an isolated projection; original frozen inputs stay intact.
    projected=out/'editor-input';projected.mkdir()
    for name in ('original.json','frozen-occurrences.json','study-manifest.json'):
        shutil.copyfile(inp/name,projected/name)
    write(projected/'packets.json',packets)
    curated.f.materialize(projected,out,records,groups,pages,contents,regions)
    for arm in ('S','Z','M'):
        for row in support['inventory']:
            if row['proposal_id'] not in {pid for g in groups for pid in g['arms'][arm]['selected']}:continue
            p=by_id[row['proposal_id']]['proposal'];packet=frozen['packets'][by_id[row['proposal_id']]['packet_sha256']]
            # Full target card with disabled outgoing links and escaped HTML source.
            import html
            card=out/arm/(p['proposal_id'].replace(':','-')+'.html')
            with card.open('a',encoding='utf-8') as stream:
                stream.write('<h2>Complete frozen destination</h2><pre>'+html.escape(packet['pages'][1]['text'])+'</pre>')
    anchors={a:read(out/a/'trusted-tip.json')['tip_sha256'] for a in ('S','Z','M')}
    write(out/'ledger-anchors.json',anchors)


def select_stage(inp,out,cfg):
    return select_supported(inp,out,read(inp/'support-pool.json'))


def verify(inp,out,cfg):
    """Fresh projection, exact copies/ledger replay, and changed-tier numerical replay."""
    import tempfile
    result=inp/'result';support=read(inp/'support-pool.json')
    anchors=read(inp/'ledger-anchors.json')
    if read(result/'ledger-anchors.json')!=anchors:raise ValueError('blinded-external-ledger-anchor')
    with tempfile.TemporaryDirectory() as temp:
        replay=Path(temp);select_supported(inp,replay,support)
        actual=[r for r in portable_inventory(result) if r['path'] not in ('manifest.json','summary.json')]
        if portable_inventory(replay)!=actual:raise ValueError('blinded-independent-artifact-replay')
    pages,_,_,regions=curated.f.load_sources(inp);frozen=read(inp/'curated-freeze.json')
    groups=read(result/'groups.json');fresh=[];occurrences=read(inp/'frozen-occurrences.json')
    policy=dict(minimum.v2.DEFAULT_POLICY,maximum=1,constraint_version=curated.constraints.VERSION)
    for region,group in zip(regions,groups):
        if not group['qualified_changed_subset']:continue
        graph=historical.graph_for(region,frozen,occurrences)
        graph['audit_binding']['supported_pool_sha256']=e.digest(support)
        scorer=minimum.v2.BoundedBlockScorer(graph,group['pool'],policy)
        selection=minimum.select_minimum_set(group['pool'],graph,policy,{},scorer=scorer)
        rows=selection['trace'][0]['raw_scores'];tier=selection['trace'][0]['tier']
        parity=all(row['status']==group['candidate_costs'][pid]['status'] and
            row.get('schedule_costs')==group['candidate_costs'][pid].get('schedule_costs') for pid,row in zip(tier,rows))
        old=group['arms']['M']
        winner_parity=(selection['selected']==old['selected'] and selection['binding_sha256']==old['binding_sha256'] and
            selection['trace'][0]['status']==old['trace'][0]['status'] and selection['trace'][0]['minimum_set']==old['trace'][0]['minimum_set'])
        # Cache-hit bookkeeping differs in a fresh solve; costs/status and the
        # complete minimum-set decision must reproduce without that history.
        write(out/(region['region_id'].replace(':','-')+'-fresh-audit.json'),{'solves':scorer.cache,'attempts':scorer.attempts,
            'selection':selection,'cost_parity':parity,'winner_parity':winner_parity})
        if not parity or not winner_parity:raise ValueError('blinded-fresh-winner-replay')
        fresh.append(region['region_id'])
    return {'independent_replay':True,'files_per_copy':len(pages),'copies':4,'external_anchors':anchors,'fresh_changed_groups':fresh}


def serialize_requests(inp,out):
    """SDK-only, offline final-body audit; constructs no clients and sends no requests."""
    import anthropic
    from .model_providers.anthropic import AnthropicTransport
    from .model_providers.openai import OpenAITransport
    manifest=read(inp/'study-manifest.json');out.mkdir(exist_ok=False);inventory=[];hashes={}
    for lane,file in [('real','jobs.json'),('preflight','preflight-jobs.json')]:
        hashes[lane]={}
        for role,jobs in read(inp/file).items():
            settings=next(r['settings'] for r in manifest['roles'] if r['role']==role)
            hashes[lane][role]={}
            if settings['provider']=='anthropic':
                transport=object.__new__(AnthropicTransport);transport.transform=anthropic.transform_schema
                request=transport.request
            else:request=OpenAITransport.request
            for job in jobs:
                body=request(job['prompt'],job['schema'],settings)
                sent=body['messages'][0]['content'] if settings['provider']=='anthropic' else body['input']
                if sent!=job['prompt']:raise ValueError('blinded-final-prompt')
                data=json.loads(sent.split('\nDOCUMENT_EVIDENCE_JSON\n')[1]);disclosure(data)
                if len(e.canonical(body))>manifest['lifecycle_policy']['maximum_count_request_bytes']:
                    raise ValueError('blinded-full-page-request-byte-cap')
                name=lane+'/'+role+'/'+job['job_id']+'.json'
                (out/name).parent.mkdir(parents=True,exist_ok=True)
                write(out/name,body);hashes[lane][role][job['job_id']]=e.digest(body)
                inventory.append({'lane':lane,'role':role,'job_id':job['job_id'],'body_sha256':e.digest(body),
                    'body_bytes':len(e.canonical(body)),'prompt_bytes':len(sent.encode()),'evidence_sha256':e.digest(data)})
    write(out/'request-inventory.json',inventory);write(out/'request-hashes.json',hashes)
    return {'jobs':len(inventory),'maximum_body_bytes':max(r['body_bytes'] for r in inventory),'clients_constructed':0,'provider_calls':0}


async def run(inp,out,authorization,*,factory=model.transport_for,counter=model.sdk_count):
    """Combined live lifecycle; only the existing shared executor creates clients."""
    req=read(inp/'operator-request.json');rows=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    manifest=read(inp/'study-manifest.json')
    if rows!=req['input_inventory'] or e.digest(rows)!=req['input_inventory_sha256']:raise ValueError('blinded-input-inventory')
    if (authorization.get('approved') is not True or authorization.get('scope')!='bounded_embedded_refinement_v3' or
        authorization.get('study_manifest_sha256')!=e.digest(manifest) or authorization.get('input_inventory_sha256')!=req['input_inventory_sha256']):
        raise ValueError('blinded-combined-authorization')
    if manifest['aggregate_hard_cap_usd']>30 or manifest['maximum_reserved_usd']>manifest['aggregate_hard_cap_usd'] or manifest['live_seconds']>7200 or manifest['concurrency']!=2:
        raise ValueError('blinded-resource-limits')
    if not manifest.get('mock_responses'):
        verification=authorization.get('official_verification',{})
        if verification.get('verified') is not True or verification.get('manifest_sha256')!=e.digest(manifest):raise ValueError('blinded-official-verification-required')
        for role in manifest['roles']:
            settings=role['settings'];entry=next(r for r in verification['roles'] if r['role']==role['role'])
            if any(entry[k]!=settings[k] for k in ('model','revision')) or not entry.get('official_source') or not entry.get('verified_utc'):
                raise ValueError('blinded-model-identity')
            if any(entry[k]>settings['pricing'][k] for k in ('input_per_mtok','output_per_mtok')) or entry['context_tokens']<settings['max_input_tokens']+settings['max_output_tokens']:
                raise ValueError('blinded-tariff-or-context')
    for role in manifest['roles']:model.validate_settings(role['settings'])
    caps=sum(r['settings']['spend_cap_usd'] for r in manifest['roles'])
    if caps>manifest['maximum_reserved_usd']+1e-10:raise ValueError('blinded-role-reservations')
    out.mkdir(exist_ok=True);header=out/'study-binding.json';binding=e.digest([manifest,req])
    if header.exists():
        if read(header)['binding_sha256']!=binding:raise ValueError('blinded-resume-binding')
    else:
        if any(p.name!='.retrieval-arena-output-owner' for p in out.iterdir()):raise ValueError('blinded-output-not-fresh')
        write(header,{'binding_sha256':binding,'started_unix':time.time()})
    start=read(header)['started_unix']
    jobs=read(inp/'jobs.json');bindings=read(inp/'private-bindings.json');preflight=read(inp/'preflight-jobs.json')
    fresh_jobs,fresh_bindings=jobs_for(read(inp/'curated-freeze.json')['records'],read(inp/'curated-freeze.json')['packets'],manifest)
    if fresh_jobs!=jobs or fresh_bindings!=bindings:raise ValueError('blinded-final-jobs-replay')
    outputs={}
    for lane,inventory in [('preflight',preflight),('real',jobs)]:
        for role in ROLES:
            policy=next(r for r in manifest['roles'] if r['role']==role)
            if (len(inventory[role])!=(2 if lane=='preflight' else manifest['candidate_count']) or
                policy['initial_items_max']!=manifest['candidate_count']+2 or policy['attempts_max']!=2*policy['initial_items_max']):
                raise ValueError('blinded-review-job-limits')
            for job in inventory[role]:
                if job['job_id']!=e.digest({k:v for k,v in job.items() if k!='job_id'}):raise ValueError('blinded-job-binding')
            roleout=out/(lane+'-'+role);cfgpath=out/(lane+'-'+role+'-config.json')
            n=len(inventory[role]);settings=copy.deepcopy(policy['settings'])
            # Each disjoint partition receives its exact frozen per-job reservation.
            settings['spend_cap_usd']=round(policy['reservation_per_job_usd']*n,12)
            cfg={'role':role,'settings':settings,'remaining_inference_seconds':max(0,manifest['live_seconds']-(time.time()-start))}
            if not manifest.get('mock_responses'):
                cfg['frozen_request_hashes']=manifest['final_request_hashes'][lane][role]
            if cfgpath.exists():cfg=read(cfgpath)
            else:write(cfgpath,cfg)
            derived=copy.deepcopy(manifest);r=next(r for r in derived['roles'] if r['role']==role)
            r.update(initial_items_max=n,attempts_max=2*n,settings=settings)
            result=await model.execute(cfg,derived,inventory[role],roleout,factory=factory,counter=counter)
            if result.get('halt_reason'):raise ValueError('blinded-provider-circuit-open')
            if lane=='preflight':
                for index,row in enumerate(result['results']):
                    if row['status']!='complete' or competitive.supported(row['payload'])!=(index==0):raise ValueError('blinded-preflight-failed')
            else:outputs[role]=result
    support=pool(read(inp/'curated-freeze.json'),read(inp/'regions.json'),jobs,bindings,outputs)
    path=out/'support-pool.json'
    if path.exists() and read(path)!=support:raise ValueError('blinded-pool-drift')
    write(path,support)
    # Publication must cover every review, not only supported rows.
    publication=out/'pool-publication.json'
    if not publication.exists():raise ValueError('blinded-pool-publication-required-before-selection')
    pub=read(publication)
    if pub['support_sha256']!=e.digest(support) or not __import__('re').fullmatch('[0-9a-f]{40}',pub['context_commit']):
        raise ValueError('blinded-pool-publication-binding')
    selected=out/'selection'
    if not selected.exists():
        selected.mkdir();select_supported(inp,selected,support)
    return {'status':'selected; independent verification and card inspection required','support_sha256':e.digest(support)}
