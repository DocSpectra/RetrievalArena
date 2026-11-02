"""Offline curator ingress and diagnostic over immutable real source bytes.

No provider client is imported here. Curator assertions never become references.
The consumer owns pair identities, assessments, reading policy and interpretation.
"""
import copy
import difflib
import html
import re
import time

from . import embedded_links as e
from . import embedded_ledger_v2 as ledger
from . import embedded_constraints as constraints
from . import embedded_refinement as f
from .embedded_demonstrator import proposal_from_anchor
from .embedded_competitive import tie_key, supported
from .embedded_link_audit import read, write
from .embedded_selection_v2 import DEFAULT_POLICY, BoundedBlockScorer, select_embedded_v2, classify

VERSION = 'retrieval_arena.embedded_curated.v1'
DIMS = ('relationship_support', 'target_support', 'placement')
LIMITS = {'min_words':1, 'max_words':12, 'max_links_per_sentence':1}


def review_packet(p, source, target):
    """Same allowlisted role-bound full evidence for both independent reviewers."""
    pages = []
    for role, node, raw in [('source',p['source_id'],source),('target',p['target_id'],target)]:
        pages.append({'role':role, 'doc_id':node, 'sha256':e.rawhash(raw),
            'passage_id':e.stable('full-page',role,node,e.rawhash(raw)),
            'raw_span':[0,len(raw)], 'text':raw.decode('utf-8'), 'readonly':True})
    return {'schema_version':VERSION+'.review-evidence', 'anchor':p['anchor'],
        'source_sentence':p['sentence'], 'source_span':p['source_span'],
        'source_id':p['source_id'], 'target_id':p['target_id'], 'pages':pages,
        'omissions':[], 'editable_anchor_only':True}


def analysis_pool(records, regions, analysis='A', reviews=None):
    if analysis not in ('A','B'): raise ValueError('curated-analysis')
    if analysis=='B' and reviews is None:
        return {'analysis':'B','status':'unrun-not-authorized','groups':[
            {'region_id':r['region_id'],'pool':[],'top_tier':[],'semantic_winner':None} for r in regions]}
    pool=[]
    for r in records:
        if r['status']!='admitted':continue
        p=r['proposal'];score=r['curator']['semantic_score']
        if analysis=='B':
            pair=reviews.get(p['proposal_id'],{})
            if not all(supported(pair[k]) for k in ('first','confirmation') if k in pair) or set(pair)!={'first','confirmation'}:continue
            score=sum(pair['first'][k] for k in DIMS)
        pool.append({k:p[k] for k in ('proposal_id','source_id','target_id','region_id')} |
            {'semantic_score':score,'semantic_eligible':True,'tie_id':tie_key(p['source_id'],p['target_id']),'conflicts':[]})
    conflicts=constraints.pool_conflicts([r['proposal'] for r in records if r['status']=='admitted'])
    groups=[]
    for region in regions:
        rows=[copy.deepcopy(p) for p in pool if p['region_id']==region['region_id']]
        for p in rows:
            p['conflicts']=sorted({pid for c in conflicts if p['proposal_id'] in c['proposal_ids'] for pid in c['proposal_ids'] if pid!=p['proposal_id']})
        rows.sort(key=lambda p:(-p['semantic_score'],p['tie_id'],p['proposal_id']))
        top=[p['proposal_id'] for p in rows if p['semantic_score']==rows[0]['semantic_score']] if rows else []
        groups.append({'region_id':region['region_id'],'pool':rows,'top_tier':top,
            'semantic_winner':top[0] if top else None,'budget':int(bool(rows))})
    return {'analysis':analysis,'status':'frozen','groups':groups,'conflicts':conflicts}


def prepare(inp, out, cfg):
    pages,by_id,contents,regions=f.load_sources(inp)
    inventory=read(inp/'audit-inventory.json');drafts=read(inp/'curation-decisions.json')
    pairs=inventory['pairs'];by_draft={r['audit_id']:r for r in drafts}
    if len(by_draft)!=len(drafts) or set(by_draft)!={r['audit_id'] for r in pairs}:raise ValueError('curated-complete-audit')
    if len(pairs)>40 or len({(r['source']['doc_id'],r['target']['doc_id']) for r in pairs})!=len(pairs):raise ValueError('curated-pair-inventory')
    region_by={r['region_id']:r for r in regions};original=read(inp/'original.json')['edges']
    mh=e.digest([inventory,drafts]);records=[];packets={}
    for pair in pairs:
        d=by_draft[pair['audit_id']];s=pair['source']['doc_id'];t=pair['target']['doc_id']
        if pair['source']!=by_id[s] or pair['target']!=by_id[t]:raise ValueError('curated-page-binding')
        region=region_by[pair['region_id']]
        if s not in region['nodes'] or t not in region['nodes']:raise ValueError('curated-group-binding')
        row={'audit_id':pair['audit_id'],'pair':pair,'curation':d,'full_page_hashes':[e.rawhash(contents[s]),e.rawhash(contents[t])],
             'independent_reviews':{'haiku':'unrun','sol':'unrun'},'prior_reviews':'Historical labels remain tied to historical disclosures; see consumer atlas.'}
        if 'reject' in d:
            row.update(status='curator_rejected',reason=d['reject']);records.append(row);continue
        verdict=dict(zip(DIMS,d['scores']),uncertainty=False,contradiction=False,relation=d['relation'])
        if not supported(verdict):raise ValueError('curator-assessment-gate')
        if not d.get('checklist') or not d.get('counterargument') or not d.get('reason'):raise ValueError('curator-evidence-checklist')
        row['curator']=dict(verdict,semantic_score=sum(d['scores']))
        try:
            p=proposal_from_anchor(contents[s],contents[t],s,t,pages,region,d['anchor'],d['target_quote'],
                {'kind':'curator','manifest_sha256':mh,'candidate_key':pair['audit_id'],'timestamp':cfg['timestamp']},key=pair['audit_id'])
            p.update(run_id=cfg['run_id'],corpus_id=region['corpus_id'],snapshot_id=region['snapshot_id'],
                relation=d['relation'],stated_intent=d['reason'],expected_reading_use=d['checklist'][0],basis='explicit_in_source' if d['relation']=='source_continues_to_target' else 'supported_inference')
            row['proposal']=p
            _,facts=ledger.apply_edit(contents[s],contents[t],p,pages,original,region,LIMITS,constraint_version=constraints.VERSION)
            row['host_facts_sha256']=e.digest(facts)
            packet=review_packet(p,contents[s],contents[t]);packet_hash=e.digest(packet)
            if sum(len(raw) for raw in (contents[s],contents[t]))>196608 or len(e.canonical(packet))>262144:
                row.update(status='evidence_omitted',reason='whole-pair-evidence-byte-cap');records.append(row);continue
            row.update(status='host_valid',packet_sha256=packet_hash)
            packets[packet_hash]=packet
        except ValueError as exc:
            row.update(status='host_rejected',reason=str(exc))
        records.append(row)
    ordered=[]
    per_group=[sorted([r for r in records if r['status']=='host_valid' and r['pair']['region_id']==g['region_id']],
        key=lambda r:(r['pair']['source']['path'],r['pair']['target']['path'])) for g in regions]
    for rank in range(max(map(len,per_group),default=0)):
        ordered.extend(g[rank] for g in per_group if rank<len(g))
    for i,r in enumerate(ordered):r.update(status='admitted' if i<16 else 'capacity_omitted',retention_rank=i)
    frozen={'schema_version':VERSION,'curation_sha256':mh,'records':records,
        'packets':packets,'A':analysis_pool(records,regions),'B':analysis_pool(records,regions,'B'),
        'input_bindings':{name:e.rawhash((inp/name).read_bytes()) for name in
            ('pages.json','regions.json','original.json','frozen-occurrences.json','audit-inventory.json','curation-decisions.json')},
        'provider_calls':0,'human_validated':False,'frozen_before_real_scores':True}
    write(out/'curated-freeze.json',frozen)
    return {'audited_pairs':len(records),'admitted':sum(r['status']=='admitted' for r in records),'provider_calls':0}


def _card(path, p, row, step, raw, edited, target):
    md=e.parser();md.options['html']=False
    def render(b):return re.sub(r'<a href="[^"]*"','<a href="#source"',md.render(b.decode()))
    a,z=p['source_span'];left=raw.rfind(b'\n\n',0,a)+2;right=raw.find(b'\n\n',z)
    if right<0:right=len(raw)
    offset=len(edited)-len(raw)
    diff=''.join(difflib.unified_diff(raw.decode().splitlines(True),edited.decode().splitlines(True),fromfile='original',tofile='A'))
    path.with_suffix('.diff').write_text(diff,encoding='utf-8')
    write(path.with_suffix('.json'),{'proposal':p,'curator':row['curator'],'counterargument':row['curation']['counterargument'],
        'checklist':row['curation']['checklist'],'independent_reviews':row['independent_reviews'],'selection':step})
    path.write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
        '<style>body{font:16px/1.5 system-ui;margin:28px;max-width:1200px}.cols{display:grid;grid-template-columns:1fr 1fr;gap:25px}article{background:#f3f6fa;padding:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}a{color:#1756a4}</style>'
        '<h1>Analysis A: curator diagnostic</h1><p>AI curator hypothesis; independent reviews unrun. No recommendation or reader-benefit validation.</p>'
        f'<p>{html.escape(p["source_id"])} → {html.escape(p["target_id"])}</p><p>Decision: {html.escape(step["status"])}</p>'
        f'<div class="cols"><section><h2>Before</h2><article>{render(raw[left:right])}</article></section><section><h2>After</h2><article>{render(edited[left:right+offset])}</article></section></div>'
        f'<h2>Exact change</h2><pre>{html.escape(diff)}</pre><h2>Destination: complete frozen source preview</h2>{render(target)}',encoding='utf-8')


def materialize(inp,out,frozen,groups,pages,contents,regions):
    original=read(inp/'original.json')['edges'];occurrences=read(inp/'frozen-occurrences.json')
    rows=[r for r in frozen['records'] if r['status']=='admitted'];proposals=[r['proposal'] for r in rows]
    by_id={r['proposal']['proposal_id']:r for r in rows};region_by={r['region_id']:r for r in regions}
    original_root=out/'A/original/corpus'
    for path,p in pages.items():
        dest=original_root/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(contents[p['doc_id']])
    anchors={};applications=[];cards=[]
    for arm in ('S','Z','M'):
        root=out/'A'/arm;root.mkdir(parents=True);edited=dict(contents);log=[];facts={}
        selected={pid:(g,g['arms'][arm]['trace'][0]) for g in groups for pid in g['arms'][arm]['selected']}
        proof=constraints.validate_selected(proposals,list(selected),original)
        for row in rows:
            p=copy.deepcopy(row['proposal']);p['arm_id']='A-'+arm
            def emit(typ,payload,authority='host'):
                event=ledger.event(log,p,typ,payload,authority)
                ledger.append_event(root/'intent-events.jsonl',event,e.digest(log[-1]) if log else '0'*64);log.append(event)
            emit('proposed',p,'curator');emit('schema_validated',{'proposal_sha256':e.digest(p)})
            emit('structurally_validated',{'eligible':True,'reason':'individual-host-valid','validation_sha256':row['host_facts_sha256']})
            emit('reviewed',dict(row['curator'],kind='curator',manifest_sha256=p['provenance']['manifest_sha256'],decision='eligible'),'curator')
            emit('eligible',{'structural':True,'semantic':True})
            pid=p['proposal_id']
            if pid not in selected:
                emit('rejected',{'reason':'not-selected-at-k1-curator-tier','no_new_edge':False});continue
            g,step=selected[pid];result=g['arms'][arm]
            emit('selected',{'rank':1,'reason':step['status'],'objective_input_sha256':result['binding_sha256'],
                'objective_output_sha256':e.digest(result),'requested_budget':1,'achieved_budget':1,'conflicts_skips':[]})
            new,fact=ledger.apply_edit(contents[p['source_id']],contents[p['target_id']],p,pages,original,region_by[p['region_id']],LIMITS,proposals,constraint_version=constraints.VERSION)
            edited[p['source_id']]=new;facts[pid]=fact
            emit('applied',{k:fact[k] for k in ('patch_sha256','source_before_sha256','source_after_sha256','occurrence_id','edge_id')}|{'facts_sha256':e.digest(fact)})
            emit('verified',{k:fact[k] for k in ('rendered_before_sha256','rendered_after_sha256','before_graph_sha256','after_graph_sha256')}|{'facts_sha256':e.digest(fact)})
            path=root/(pid.replace(':','-')+'.html');_card(path,p,row,step,contents[p['source_id']],new,contents[p['target_id']]);cards.append(str(path.relative_to(out)))
        replayed,_=constraints.replay_assignment(proposals,list(selected),original,contents,facts)
        if replayed!=edited:raise ValueError('curated-assignment-replay')
        for path,p in pages.items():
            node=p['doc_id'];before=contents[node];after=edited[node]
            if e.semantic_text(before)!=e.semantic_text(after):raise ValueError('curated-visible-prose')
            if before!=after:
                old=e.occurrences(before,p,pages);new=e.occurrences(after,p,pages)
                nav=lambda rs:sorted((r['href'],r['source_syntax']) for r in rs if r['class']=='navigation')
                if nav(old)!=nav(new):raise ValueError('curated-navigation')
                expected={(node,by_id[pid]['proposal']['target_id']) for pid in selected if by_id[pid]['proposal']['source_id']==node}
                for mode in ('original','content'):
                    pre=set(e.project(old,set(contents),mode));post=set(e.project(new,set(contents),mode))
                    if post-pre!=expected or pre-post:raise ValueError('curated-graph-delta')
            dest=root/'corpus'/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(after)
        tip=e.digest(log[-1]) if log else '0'*64;replay=ledger.replay(log,tip)
        if not log:(root/'intent-events.jsonl').write_bytes(b'')
        write(root/'trusted-tip.json',{'tip_sha256':tip,'replay':replay});write(root/'host-facts.json',facts);write(root/'selected-set-proof.json',proof)
        anchors[arm]=tip
        applications.append({'arm':arm,'files_verified':len(contents),'selected':list(selected),'added_pairs':proof['added_pairs'],
            'visible_prose_equal':True,'navigation_equal':True,'assignment_replay':True,'tip_sha256':tip})
    write(out/'ledger-anchors.json',anchors);write(out/'applications.json',applications);write(out/'cards.json',cards)
    return applications


def select(inp,out,cfg,scorer_factory=BoundedBlockScorer):
    pages,_,contents,regions=f.load_sources(inp);frozen=read(inp/'curated-freeze.json')
    for name,digest in frozen['input_bindings'].items():
        if e.rawhash((inp/name).read_bytes())!=digest:raise ValueError('curated-frozen-input')
    if analysis_pool(frozen['records'],regions)!=frozen['A']:raise ValueError('curated-A-pool-drift')
    receipt=read(inp/'freeze-receipt.json')
    if receipt['curated_freeze_sha256']!=e.rawhash((inp/'curated-freeze.json').read_bytes()) or not re.fullmatch('[0-9a-f]{40}',receipt['context_commit']):raise ValueError('curated-freeze-receipt')
    occurrences=read(inp/'frozen-occurrences.json');groups=[];states=[]
    for region,plan in zip(regions,frozen['A']['groups']):
        nodes=set(region['nodes']);pool=plan['pool']
        graph={'nodes':region['nodes'],'edges':e.project([r for r in occurrences if r['source_id'] in nodes],nodes),
            'audit_binding':{'freeze_sha256':e.digest(frozen),'region':region,'mask_sha256':e.digest(occurrences)}}
        policy=dict(DEFAULT_POLICY,maximum=1,constraint_version=constraints.VERSION)
        scorer=scorer_factory(graph,pool,policy);costs={};start=time.monotonic()
        # Top tier first, then canonical lower tiers. Every candidate remains recorded.
        for p in pool:costs[p['proposal_id']]=scorer((p['proposal_id'],))
        def cached(prefix):return costs[prefix[0]]
        arms={a:select_embedded_v2(pool,graph,policy,{},arm=a,scorer=cached) for a in ('S','Z','M')}
        if arms['S']['selected']!=arms['Z']['selected']:raise ValueError('curated-q0-control')
        step=arms['M']['trace'][0] if pool else None
        status=step['status'] if step else 'empty_pool';competitive=len(plan['top_tier'])>1
        outcome='empty' if not pool else 'insufficient_top_tier_competition' if not competitive else 'qualified_changed_subset' if status=='resolved_finite_order' and arms['M']['selected']!=arms['S']['selected'] else 'resolved_agreement' if status=='resolved_finite_order' else 'competitive_unresolved'
        group={'region_id':region['region_id'],'nodes':len(nodes),'pool_count':len(pool),'top_tier':plan['top_tier'],'arms':arms,
            'candidate_costs':costs,'outcome':outcome,'competitive':competitive,
            'numerically_evaluable':competitive and all(costs[pid]['status']=='finite_estimate' for pid in plan['top_tier']),
            'runtime_seconds':time.monotonic()-start}
        groups.append(group);states.append((graph,pool,scorer))
    write(out/'primary-outcomes-sealed.json',{'analysis':'A','groups':groups,'B':frozen['B'],'freeze_receipt':receipt})
    for group,(graph,pool,scorer) in zip(groups,states):
        root=out/'numerical'/group['region_id'].replace(':','-');root.mkdir(parents=True)
        audit={'solves':scorer.cache,'attempts':scorer.attempts,'candidate_costs':group['candidate_costs']}
        if len(e.canonical(audit))>100*1024**2:raise ValueError('curated-primary-output-cap')
        write(root/'audit.json',audit)
        if cfg.get('diagnostic',True) and pool:
            secondary=scorer_factory(graph,pool,dict(DEFAULT_POLICY,maximum=1,q=.10,constraint_version=constraints.VERSION))
            rows=[secondary((pid,)) for pid in group['top_tier']];status,index=classify(rows,1e-6)
            diag={'q':.10,'tier':group['top_tier'],'scores':rows,'status':status,'winner':group['top_tier'][index] if index is not None else None,
                'primary_seal_sha256':e.rawhash((out/'primary-outcomes-sealed.json').read_bytes()),'solves':secondary.cache,'attempts':secondary.attempts}
            if len(e.canonical(diag))>100*1024**2:raise ValueError('curated-diagnostic-output-cap')
            write(root/'diagnostic-q010.json',diag)
    applications=materialize(inp,out,frozen,groups,pages,contents,regions)
    write(out/'groups.json',groups)
    return {'analysis':'A','groups':len(groups),'candidates':sum(g['pool_count'] for g in groups),
        'competitive_groups':sum(g['competitive'] for g in groups),'evaluable_groups':sum(g['numerically_evaluable'] for g in groups),
        'changed_groups':sum(g['outcome']=='qualified_changed_subset' for g in groups),'applications':applications,'B':'unrun-not-authorized','provider_calls':0}


def verify(inp,out,cfg):
    """Separate offline replay over sealed results and externally copied tips."""
    import json
    import numpy as np
    from .graph import block_phase as bp
    pages,_,contents,regions=f.load_sources(inp);frozen=read(inp/'curated-freeze.json');result=inp/'result'
    anchors=read(inp/'ledger-anchors.json');groups=read(result/'groups.json')
    proposals=[r['proposal'] for r in frozen['records'] if r['status']=='admitted'];by_id={p['proposal_id']:p for p in proposals}
    original=read(inp/'original.json')['edges'];file_count=0;fits_count=0;starts_count=0;replayed=[]
    for arm in ('S','Z','M'):
        root=result/'A'/arm;facts=read(root/'host-facts.json')
        log=[json.loads(x) for x in (root/'intent-events.jsonl').read_bytes().splitlines()]
        ledger.replay(log,anchors[arm])
        selected=[pid for g in groups for pid in g['arms'][arm]['selected']]
        edited,_=constraints.replay_assignment(proposals,selected,original,contents,facts)
        for path,p in pages.items():
            raw=(root/'corpus'/path).read_bytes()
            if raw!=edited[p['doc_id']] or e.semantic_text(raw)!=e.semantic_text(contents[p['doc_id']]):raise ValueError('curated-independent-copy')
            file_count+=1
    for path,p in pages.items():
        if (result/'A/original/corpus'/path).read_bytes()!=contents[p['doc_id']]:raise ValueError('curated-original-copy')
        file_count+=1
    for region,group,plan in zip(regions,groups,frozen['A']['groups']):
        root=result/'numerical'/region['region_id'].replace(':','-');audit=read(root/'audit.json')
        records=[(.25,audit)]
        if (root/'diagnostic-q010.json').exists():records.append((.10,read(root/'diagnostic-q010.json')))
        for q,record in records:
            for fit in record['solves'].values():
                nodes=fit['support_nodes'];edges=fit['support_edges'];_,terms=bp.lo.terms(nodes,edges,q)
                for start in fit['starts']:
                    energy,gradient=bp.lo.energy_gradient(np.array(start['final_phases']),terms)
                    if abs(energy-start['energy'])>1e-9 or abs(float(np.max(np.abs(gradient),initial=0.))-start['gradient_inf'])>1e-9:raise ValueError('curated-numerical-replay')
                    if start['stationary']!=(float(np.max(np.abs(gradient),initial=0.))<=DEFAULT_POLICY['numerical']['stationarity']):raise ValueError('curated-stationarity-replay')
                    starts_count+=1
                valid=[i for i,s in enumerate(fit['starts']) if s['stationary']]
                best=min(valid or range(len(fit['starts'])),key=lambda i:(fit['starts'][i]['energy'],i))
                if best!=fit['best_start'] or abs(fit['starts'][best]['energy']-fit['energy_upper_bound'])>1e-9:raise ValueError('curated-best-start-replay')
                fits_count+=1
        if group['outcome']=='qualified_changed_subset':
            nodes=set(region['nodes']);occ=read(inp/'frozen-occurrences.json')
            graph={'nodes':region['nodes'],'edges':e.project([r for r in occ if r['source_id'] in nodes],nodes)}
            scorer=BoundedBlockScorer(graph,plan['pool'],dict(DEFAULT_POLICY,maximum=1,constraint_version=constraints.VERSION))
            rows=[scorer((pid,)) for pid in plan['top_tier']]
            for pid,row in zip(plan['top_tier'],rows):
                old=group['candidate_costs'][pid]
                if row['status']!=old['status'] or row.get('schedule_costs')!=old.get('schedule_costs'):raise ValueError('curated-fresh-numerical-parity')
            status,index=classify(rows,1e-6)
            if status!='resolved_finite_order' or plan['top_tier'][index]!=group['arms']['M']['selected'][0]:raise ValueError('curated-winner-replay')
            write(out/(region['region_id'].replace(':','-')+'-fresh-replay.json'),{'scores':rows,'solves':scorer.cache,'attempts':scorer.attempts})
            replayed.append(region['region_id'])
    proof={'files_verified':file_count,'ledger_arms':3,'numerical_fits_recomputed':fits_count,
        'numerical_starts_recomputed':starts_count,'changed_groups_freshly_replayed':replayed,'provider_calls':0}
    write(out/'verification.json',proof);return proof
