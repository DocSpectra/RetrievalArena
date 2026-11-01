"""Portable offline audit stage. Corpus selection belongs to the consumer."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import re
import shutil
import time

from . import embedded_links as el
from .candidate_retrieval import regular_inventory

def read(p): return json.loads(p.read_bytes())
def write(p,x): p.write_bytes(el.canonical(x)+b'\n')
def portable_inventory(root):
    return sorted(regular_inventory(root),key=lambda r:r['path'])

def fixture_proposal(pages,contents,edges,regions,cfg):
    """First valid mechanical addition in development order; no semantic claim."""
    by_id={r['doc_id']:r for r in pages.values()}; reasons=Counter()
    for rid in regions['selected_region_ids']:
        region=next(r for r in regions['regions'] if r['region_id']==rid)
        if region['assignment']!='development': continue
        for source in region['nodes']:
            raw=contents[source]; text=raw.decode('utf-8')
            bs,_=el.blocks(raw)
            for b in bs:
                if not b['map'] or b['parent']!='paragraph_open' or b['in_list'] or b['in_quote']: continue
                if any(t.type not in ('text','softbreak','hardbreak') for t in b['children']): continue
                matches=list(re.finditer(r'\b\w+(?:[’\'-]\w+)*\b',b['content']))
                if len(matches)<2: continue
                a,z=matches[0].start(),matches[1].end()
                ca,cz=b['map'][a],b['map'][z-1]+1
                span=[len(text[:ca].encode()),len(text[:cz].encode())]
                lo,hi=el.sentence_span(b['content'],a,z)
                for target in region['nodes']:
                    if source==target or region['component_membership'][source]!=region['component_membership'][target]: continue
                    if (source,target) in edges or (target,source) in edges: continue
                    traw=contents[target]
                    evidence=[0,min(len(traw),64)]
                    # Align arbitrary fixture evidence to UTF-8 boundaries.
                    while True:
                        try: quote=traw[:evidence[1]].decode('utf-8'); break
                        except UnicodeDecodeError: evidence[1]-=1
                    href='/'+by_id[target]['path']
                    response={'fixture':'mechanical-only','source_id':source,'target_id':target}
                    p={'schema_version':el.VERSION+'.proposal','proposal_id':el.stable('proposal','fixture',source,target,span),
                        'run_id':'offline-fixture','arm_id':'fixture','region_id':rid,
                        'corpus_id':cfg['corpus_id'],'snapshot_id':cfg['snapshot_id'],'source_id':source,'target_id':target,
                        'source_sha256':el.rawhash(raw),'target_sha256':el.rawhash(traw),'href':href,'target_fragment':'',
                        'coordinate_convention':el.COORDINATES,'source_span':span,'anchor':raw[span[0]:span[1]].decode(),
                        'sentence':b['content'][lo:hi],'target_evidence':[{'span':evidence,'quote':quote}],
                        'relation':'insufficient_evidence','stated_intent':'Mechanical fixture; relationship unassessed.',
                        'expected_reading_use':'Unassessed fixture only.','basis':'unsupported','confidence':None,
                        'confidence_status':'uncalibrated','provenance':{'provider':'offline','model':'mock',
                            'prompt_version':'fixture-v1','schema_version':el.VERSION+'.proposal','attempt_id':'fixture-1',
                            'timestamp':'2026-11-01T00:00:00Z','response_sha256':el.digest(response),
                            'source_access':[source,target],'retry_of':None,'status':'mock-model'}}
                    try:
                        edited,facts=el.apply_edit(raw,traw,p,pages,edges,region,cfg['edit_limits'],proposed_pool=[p])
                        return p,edited,facts,response,dict(reasons)
                    except ValueError as error: reasons[str(error)]+=1
    return None,None,None,None,dict(reasons)

def run(inp,out):
    started=time.monotonic()
    if {p.name for p in out.iterdir()}!={'.retrieval-arena-output-owner'}: raise ValueError('output-not-fresh')
    inv=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    req=read(inp/'operator-request.json')
    if req!={'schema_version':'retrieval_arena.embedded_links_request.v1','command':'audit-embedded-links',
        'input_inventory':inv,'input_inventory_sha256':el.digest(inv)}: raise ValueError('input-inventory')
    cfg=read(inp/'config.json'); rows=read(inp/'pages.json')
    if cfg['schema_version']!='retrieval_arena.embedded_links_config.v1' or cfg['halo_hops']!=0:
        raise ValueError('configuration')
    if not 0<cfg['max_runtime_seconds']<=3600 or not 0<cfg['max_output_bytes']<=1024**3: raise ValueError('budget')
    pages={r['path']:r for r in rows}; ids=[r['doc_id'] for r in rows]
    if len(pages)!=len(rows) or len(set(ids))!=len(ids) or ids!=sorted(ids): raise ValueError('identity-collision')
    paths={r['path'] for r in inv}
    if paths!={'pages.json','config.json','historical-graph.json','historical-scaffold-graph.json'}|{'corpus/'+p for p in pages}: raise ValueError('input-allowlist')
    contents={}; occurrences=[]; unsupported=[]
    for p,row in pages.items():
        if Path(p).is_absolute() or '..' in Path(p).parts or '\\' in p: raise ValueError('unsafe-page-path')
        raw=(inp/'corpus'/p).read_bytes()
        if el.rawhash(raw)!=row['raw_sha256']: raise ValueError('source-hash')
        contents[row['doc_id']]=raw
        occurrences.extend(el.occurrences(raw,row,pages))
        # Non-executed surface markers, including code/image/reference definitions.
        text=raw.decode('utf-8'); bs,ts=el.blocks(raw)
        counts=Counter(t.type for t in ts if t.type in ('fence','code_block','html_block'))
        counts['template_markers']=len(re.findall(r'\{\{[<%]|\{%|\{\{',text))
        counts['reference_definitions']=len(re.findall(r'^\s*\[[^\]]+\]:',text,re.M))
        counts['images']=sum(t.type=='image' for b in bs for t in b['children'])
        unsupported.append({'doc_id':row['doc_id'],'counts':dict(counts)})
        if time.monotonic()-started>cfg['max_runtime_seconds']: raise ValueError('runtime-budget')
    # Fragment evidence is recorded separately from page resolution. Invalid or
    # nonstandard heading aliases remain ambiguous in the primary content mask.
    el.qualify_fragments(occurrences,contents)
    nodes=sorted(ids); views={}
    for mode in ['original','content','sensitivity']:
        edges=el.project(occurrences,set(nodes),mode)
        views[mode]={'nodes':nodes,'edges':edges,'summary':el.summary(nodes,edges)}
        write(out/(mode+'.json'),views[mode])
    historical=read(inp/'historical-graph.json')
    if historical['nodes']!=nodes: raise ValueError('historical-node-join')
    old=set(map(tuple,historical['edges'])); new=set(map(tuple,views['original']['edges']))
    write(out/'historical-comparison.json',{'historical_sha256':el.digest(historical),
        'historical_summary':el.summary(nodes,sorted(old)),
        'historical_scaffold_summary':el.summary(nodes,read(inp/'historical-scaffold-graph.json')['edges']),
        'historical_only_edges':sorted(old-new),'occurrence_only_edges':sorted(new-old),
        'note':'Historical regex importer counts unused reference definitions/code; new CommonMark occurrences use declared site aliases.'})
    write(out/'occurrences.json',occurrences); write(out/'unsupported-surfaces.json',unsupported)
    write(out/'source-manifest.json',rows)
    regions=el.freeze_regions(pages,contents,list(map(tuple,views['content']['edges'])),cfg,views['original']['edges'])
    write(out/'regions.json',regions)
    packet=[]
    for cls in ['substantive','navigation','ambiguous','other_supported']:
        candidates=sorted((r for r in occurrences if r['class']==cls),key=lambda r:el.digest([cfg['region_seed'],r['occurrence_id']]))
        packet.extend(candidates[:cfg['inspection_per_class']])
    write(out/'source-only-inspection.json',{'selection':'class-stratified hash order; no outcomes or human verdicts',
        'occurrences':packet,'human_validated':False})
    write(out/'proposal.schema.json',{'$schema':'https://json-schema.org/draft/2020-12/schema',**el.PROPOSAL_SCHEMA})
    write(out/'event.schema.json',{'$schema':'https://json-schema.org/draft/2020-12/schema',**el.EVENT_SCHEMA})
    write(out/'relation-definitions.json',el.RELATIONS)
    edges=list(map(tuple,views['content']['edges']))
    p,edited,facts,response,failed=fixture_proposal(pages,contents,list(map(tuple,views['original']['edges'])),regions,cfg)
    smoke={'status':'no-eligible-fixture','failed_mechanical_checks':failed}
    if p:
        by_id={r['doc_id']:r for r in rows}; raw=contents[p['source_id']]
        # A complete isolated local corpus copy, never the released corpus.
        shutil.copytree(inp/'corpus',out/'fixture-corpus',copy_function=shutil.copyfile)
        (out/'fixture-corpus'/by_id[p['source_id']]['path']).write_bytes(edited)
        write(out/'fixture-proposal.json',p); write(out/'fixture-host-facts.json',facts)
        (out/'fixture-raw-response.json').write_bytes(el.canonical(response))
        el.verify_response_bytes((out/'fixture-raw-response.json').read_bytes(),p['provenance']['response_sha256'])
        review_response={'mock':'eligible'}
        (out/'fixture-review-raw-response.json').write_bytes(el.canonical(review_response))
        blind=el.blind_review_input(p,raw,contents[p['target_id']]); write(out/'fixture-blinded-input.json',blind)
        log=[]
        def emit(typ,payload,authority='host'): log.append(el.event(log,p,typ,payload,authority))
        emit('proposed',p,'model'); emit('schema_validated',{'proposal_sha256':el.digest(p)})
        emit('structurally_validated',{'eligible':True,'reason':'mechanical fixture only','validation_sha256':el.digest(facts)})
        emit('reviewed',{'reviewer_provider':'offline','reviewer_model':'mock','rubric_version':'fixture-only',
            'decision':'eligible','evidence':p['target_evidence'],'uncertainty':'Unassessed mock decision; no semantic validation.',
            'response_sha256':el.digest(review_response),'blinded_input_sha256':el.digest(blind),'status':'mock-model'},'reviewer')
        emit('eligible',{'structural':True,'semantic':True})
        emit('selected',{'rank':1,'reason':'first valid mechanical development fixture; no score',
            'objective_input_sha256':el.digest(None),'objective_output_sha256':el.digest(None),
            'requested_budget':1,'achieved_budget':1,'conflicts_skips':list(failed)})
        emit('applied',{k:facts[k] for k in ['patch_sha256','source_before_sha256','source_after_sha256','occurrence_id','edge_id']}|{'facts_sha256':el.digest(facts)})
        emit('verified',{k:facts[k] for k in ['rendered_before_sha256','rendered_after_sha256','before_graph_sha256','after_graph_sha256']}|{'facts_sha256':el.digest(facts)})
        # Failed/rejected proposals stay in the same append-only history.
        rejected=copy_proposal(p,'duplicate-pair')
        log.append(el.event(log,rejected,'proposed',rejected,'model'))
        log.append(el.event(log,rejected,'rejected',{'reason':'existing-directed-pair-no-new-edge','no_new_edge':True}))
        failure=copy_proposal(p,'stale-source')
        log.append(el.event(log,failure,'proposed',failure,'model'))
        log.append(el.event(log,failure,'failed',{'reason':'fixture-stale-source'}))
        (out/'fixture-events.jsonl').write_bytes(b''.join(el.canonical(r)+b'\n' for r in log))
        replay=el.replay(log,el.digest(log[-1]))
        assert el.replay_patch(raw,facts)==edited==el.replay_patch(edited,facts)
        # Re-import the changed file from disk and hold every other file fixed.
        after=[]
        for path,row in pages.items(): after.extend(el.occurrences((out/'fixture-corpus'/path).read_bytes(),row,pages))
        el.qualify_fragments(after,contents|{p['source_id']:edited})
        after_edges=set(el.project(after,set(nodes)))
        assert after_edges-set(edges)=={(p['source_id'],p['target_id'])} and not set(edges)-after_edges
        nav=el.navigation_view(edited,by_id[p['source_id']],pages)
        added=[r for r in nav if r['destination']==p['target_id']]
        assert len(added)==1 and added[0]['anchor']==p['anchor'] and p['sentence'].strip() in added[0]['sentence_context'].replace('['+p['anchor']+']('+p['href']+')',p['anchor'])
        write(out/'fixture-navigation.json',added)
        smoke={'status':'qualified-mechanical-fixture','semantic_status':'unassessed-mock-only',
            'source_id':p['source_id'],'target_id':p['target_id'],'region_id':p['region_id'],
            'added_edges':1,'removed_edges':0,'unchanged_other_files':len(nodes)-1,
            'ledger':replay,'patch_replay_idempotent':True,'semantic_text_identical':True,
            'raw_proposer_response_hash_verified':True,'raw_reviewer_response_hash_verified':el.verify_response_bytes(
                (out/'fixture-review-raw-response.json').read_bytes(),el.digest(review_response)),
            'existing_retriever':'raw Markdown corpus search can index destinations; use frozen semantic text view',
            'failed_mechanical_checks':failed}
    write(out/'fixture-smoke.json',smoke)
    write(out/'provider-accounting.json',{'attempts':0,'input_tokens':0,'output_tokens':0,'paid_cost':0,
        'outbound_calls':0,'model_calls':0,'mock_records_excluded_from_accounting':True})
    # Freeze an explicit ranking-input file and hashes for all pages.
    ranking={v:el.semantic_text(raw) for v,raw in contents.items()}; write(out/'semantic-text.json',ranking)
    totals=Counter(r['class'] for r in occurrences); resolved=Counter(r['resolution'] for r in occurrences)
    result={'schema_version':el.VERSION+'.audit','nodes':len(nodes),'occurrences':len(occurrences),
        'classes':dict(totals),'resolution':dict(resolved),
        'fragment_resolution':dict(Counter(r['fragment_resolution'] for r in occurrences)),
        'views':{k:v['summary'] for k,v in views.items()},
        'regions':{'selected':len(regions['selected_region_ids']),'shortage':regions['shortage'],
                   'by_assignment':dict(Counter(r['assignment'] for r in regions['regions']))},
        'smoke':smoke,'runtime_seconds':time.monotonic()-started,'model_calls':0,
        'limits':'CommonMark source view only; HTML/template semantics and classification unvalidated.'}
    write(out/'summary.json',result)
    outputs=portable_inventory(out)
    if sum(r['size_bytes'] for r in outputs)>cfg['max_output_bytes']: raise ValueError('output-budget')
    write(out/'embedded-links-manifest.json',{'schema_version':el.VERSION+'.manifest','input_inventory_sha256':el.digest(inv),
        'outputs':outputs,'outputs_sha256':el.digest(outputs),'config_sha256':el.digest(cfg)})
    return result

def copy_proposal(p,suffix):
    import copy
    new=copy.deepcopy(p); new['proposal_id']=el.stable('proposal',p['proposal_id'],suffix)
    return new
