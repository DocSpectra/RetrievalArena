"""Consumer-configured source demonstrations and deterministic access inventory.

This offline operator does not import model clients or infer semantic labels.
All authored assertions are carried as authored_fixture provenance.
"""
import copy
import difflib
import html
import math
import os
from pathlib import Path
import posixpath
import re
import time

from . import embedded_links as e
from . import embedded_ledger_v2 as ledger
from .embedded_link_audit import read, write, portable_inventory
from .embedded_selection_v2 import select_embedded_v2, BoundedBlockScorer, DEFAULT_POLICY

VERSION = 'retrieval_arena.embedded_demonstrator.v2'


def proposal_from_anchor(raw, target_raw, source, target, pages, region, anchor, quote, provenance, *, key):
    """Fixture builder only; exact literal quotes must each occur once."""
    a = anchor.encode(); q = quote.encode()
    if raw.count(a) != 1 or target_raw.count(q) != 1: raise ValueError('fixture-nonunique-anchor-or-quote')
    start = raw.index(a); end = start+len(a); sentence = None
    for b in e.blocks(raw)[0]:
        if b['map'] is None: continue
        cs = len(raw[:start].decode()); ce = len(raw[:end].decode())
        if cs in b['map'] and ce-1 in b['map']:
            x, z = b['map'].index(cs), b['map'].index(ce-1)+1
            lo, hi = e.sentence_span(b['content'], x, z)
            sentence = b['content'][lo:hi]; break
    if sentence is None: raise ValueError('fixture-anchor-unmappable')
    by_id = {p['doc_id']:p for p in pages.values()}
    href = posixpath.relpath(by_id[target]['path'], posixpath.dirname(by_id[source]['path']) or '.')
    return {'schema_version':ledger.VERSION+'.proposal', 'proposal_id':e.stable('proposal', key, e.rawhash(raw), start, target),
        'run_id':'authored-demonstration', 'arm_id':'common-pool', 'region_id':region['region_id'],
        'corpus_id':'authored-workflow', 'snapshot_id':provenance['manifest_sha256'],
        'source_id':source, 'target_id':target, 'source_sha256':e.rawhash(raw), 'target_sha256':e.rawhash(target_raw),
        'href':href, 'target_fragment':'', 'coordinate_convention':e.COORDINATES,
        'source_span':[start,end], 'anchor':anchor, 'sentence':sentence,
        'target_evidence':[{'span':[target_raw.index(q),target_raw.index(q)+len(q)],'quote':quote}],
        'relation':'target_elaborates_source', 'stated_intent':'Authored equal-score mechanism illustration.',
        'expected_reading_use':'Consult the named elaboration.', 'basis':'supported_inference',
        'confidence':None, 'confidence_status':'uncalibrated', 'provenance':provenance}


def _graph(contents, pages):
    occurrences = [r for path, p in pages.items() for r in e.occurrences(contents[p['doc_id']], p, pages)]
    return {mode:e.project(occurrences, set(contents), mode) for mode in ['original','content'] } | {
        'navigation':sorted({(r['source_id'],r['target_id']) for r in occurrences
                             if r['class']=='navigation' and r['target_id']})}


def _card(path, scenario, arm, original, edited, proposal, selection, facts):
    before = e.parser().render(original.decode()); after = e.parser().render(edited.decode())
    # Source-based preview: links remain readable but do not navigate to a site.
    before = re.sub(r'<a href="[^"]*"', '<a href="#source"', before)
    after = re.sub(r'<a href="[^"]*"', '<a href="#source"', after)
    diff = ''.join(difflib.unified_diff(original.decode().splitlines(True), edited.decode().splitlines(True),
                                        fromfile='original/'+proposal['source_id']+'.md', tofile=arm+'/'+proposal['source_id']+'.md'))
    path.with_suffix('.diff').write_text(diff, encoding='utf-8')
    info = {'selected_anchor':proposal['anchor'], 'source_span':proposal['source_span'],
            'target':proposal['target_id'], 'target_evidence':proposal['target_evidence'],
            'semantic_score':6, 'provenance':'authored_fixture', 'decision':selection['trace'][0],
            'host_facts':facts}
    write(path.with_suffix('.json'), info)
    document = f'''<!doctype html><meta charset="utf-8"><title>{scenario} · {arm}</title>
<style>body{{font:16px/1.5 system-ui;margin:30px;max-width:1200px;color:#192638}}h1{{font-size:26px}}
.columns{{display:grid;grid-template-columns:1fr 1fr;gap:28px}}article{{padding:20px;background:#f3f6fa;border:1px solid #ccd5df;border-radius:8px}}
a{{color:#1756a4;text-decoration:underline}}pre{{white-space:pre-wrap;background:#eef2f6;padding:14px;font:14px/1.5 monospace}}
.note{{background:#fff2cf;padding:12px}}h2{{font-size:19px}}</style>
<h1>{html.escape(scenario)} · {arm}</h1><p class="note">Authored equal-score illustration (6/6).
Source-based CommonMark preview. Editorial superiority and reader benefit are untested.</p>
<p><b>Decision:</b> {html.escape(selection['trace'][0]['status'])}; source {proposal['source_id']} → target {proposal['target_id']}.
<b>Anchor:</b> {html.escape(proposal['anchor'])}. UTF-8 span {proposal['source_span']}.</p>
<div class="columns"><section><h2>Before</h2><article>{before}</article></section>
<section><h2>After</h2><article>{after}</article></section></div>
<h2>Exact Markdown change</h2><pre>{html.escape(diff)}</pre>
<p><b>Target evidence:</b> {html.escape(proposal['target_evidence'][0]['quote'])}</p>
<p>Visible prose preserved. Previous/next navigation retained. One new directed content pair.</p>
<p><a href="{path.with_suffix('.json').name}">Complete score, span, decision and host facts</a></p>'''
    path.write_text(document, encoding='utf-8')


def apply_arm(out, scenario, arm, contents, pages, graph, region, pool, result, manifest_hash):
    """Materialize isolated copies and bind every application to a trusted log tip."""
    out.mkdir(parents=True); edited = dict(contents); log = []; facts = {}; cards = []
    by_id = {p['proposal_id']:p for p in pool}
    for p in pool:
        p = copy.deepcopy(p); p['arm_id'] = arm
        def append(typ, payload, authority='host'):
            row = ledger.event(log, p, typ, payload, authority)
            ledger.append_event(out/'intent-events.jsonl', row, e.digest(log[-1]) if log else '0'*64)
            log.append(row)
        append('proposed', p, 'fixture_author')
        append('schema_validated', {'proposal_sha256':e.digest(p)})
        _, f = ledger.apply_edit(contents[p['source_id']], contents[p['target_id']], p, pages,
                                  graph['original'], region, {'min_words':1,'max_words':12,'max_links_per_sentence':1}, pool)
        append('structurally_validated', {'eligible':True,'reason':'exact-host-edit-valid','validation_sha256':e.digest(f)})
        append('reviewed', {'kind':'authored_fixture','manifest_sha256':manifest_hash,
                           'semantic_score':6,'relation':p['relation'],'decision':'eligible'}, 'fixture_author')
        append('eligible', {'structural':True,'semantic':True})
        if p['proposal_id'] not in result['selected']:
            append('rejected', {'reason':'not-selected-at-common-budget','no_new_edge':False})
            continue
        rank = result['selected'].index(p['proposal_id'])+1
        append('selected', {'rank':rank,'reason':result['trace'][rank-1]['status'],
            'objective_input_sha256':result['binding_sha256'],'objective_output_sha256':e.digest(result),
            'requested_budget':result['requested_budget'],'achieved_budget':result['achieved_budget'], 'conflicts_skips':[]})
        new, f = ledger.apply_edit(edited[p['source_id']], contents[p['target_id']], p, pages,
            graph['original'], region, {'min_words':1,'max_words':12,'max_links_per_sentence':1}, pool)
        if e.replay_patch(contents[p['source_id']], f) != new or e.replay_patch(new, f) != new:
            raise ValueError('patch-replay')
        edited[p['source_id']] = new; facts[p['proposal_id']] = f
        append('applied', {k:f[k] for k in ['patch_sha256','source_before_sha256','source_after_sha256','occurrence_id','edge_id']} |
                          {'facts_sha256':e.digest(f)})
        append('verified', {k:f[k] for k in ['rendered_before_sha256','rendered_after_sha256','before_graph_sha256','after_graph_sha256']} |
                           {'facts_sha256':e.digest(f)})
        card = out/('source-'+p['source_id']+'.html')
        _card(card, scenario, arm, contents[p['source_id']], new, p, result, f); cards.append(card)
    for path,p in pages.items():
        dest = out/'corpus'/path; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(edited[p['doc_id']])
    final = _graph(edited, pages)
    expected = {(by_id[p]['source_id'],by_id[p]['target_id']) for p in result['selected']}
    for mode in ['original','content']:
        if set(final[mode])-set(graph[mode]) != expected or set(graph[mode])-set(final[mode]): raise ValueError('full-graph-delta')
    if final['navigation'] != graph['navigation']: raise ValueError('navigation-changed')
    for node, raw in contents.items():
        if e.semantic_text(raw) != e.semantic_text(edited[node]): raise ValueError('visible-prose-changed')
        if node not in {by_id[p]['source_id'] for p in result['selected']} and raw != edited[node]:
            raise ValueError('untouched-source-changed')
    tip = e.digest(log[-1]) if log else '0'*64
    replay = ledger.replay(log, tip)
    write(out/'trusted-tip.json', {'tip_sha256':tip,'replay':replay})
    write(out/'host-facts.json', facts); write(out/'selection.json', result)
    write(out/'graph-deltas.json', {'before':graph,'after':final,'added_pairs':sorted(expected),
        'visible_prose_equal':True,'untouched_source_bytes_equal':True,'navigation_retained':True})
    return {'selection':result,'trusted_tip':tip,'graph_delta':sorted(expected),
            'cards':[p.name for p in cards], 'all_source_files_verified':len(contents)}


def fixture_run(inp, out, cfg):
    manifest_path = inp/cfg['manifest']; manifest = read(manifest_path); mh = e.rawhash(manifest_path.read_bytes())
    pages = {p.name:{'path':p.name,'doc_id':p.stem,'raw_sha256':e.rawhash(p.read_bytes())}
             for p in sorted((inp/cfg['corpus']).glob('*.md'))}
    if len(pages) != 6: raise ValueError('fixture-six-sources')
    contents = {p['doc_id']:(inp/cfg['corpus']/path).read_bytes() for path,p in pages.items()}
    graph = _graph(contents, pages)
    if graph['content'] != [tuple(x) for x in manifest['baseline_edges']] or len(graph['navigation']) != 10:
        raise ValueError('fixture-parser-mask-mismatch')
    nodes = sorted(contents); region = {'region_id':'authored-workflow','nodes':nodes,
                                      'component_membership':{n:'workflow-component' for n in nodes}}
    candidates = {}
    for c in manifest['candidates']:
        candidates[c['candidate_key']] = proposal_from_anchor(contents[c['source']], contents[c['target']],
            c['source'], c['target'], pages, region, c['anchor'], c['target_quote'],
            {'kind':'authored_fixture','manifest_sha256':mh,'candidate_key':c['candidate_key'],'timestamp':'authored-fixture'}, key=c['candidate_key'])
    solve_cache = {}; score_cache = {}; summaries = []; card_paths = []
    for scenario in manifest['scenarios']:
        root = out/scenario['id']; root.mkdir()
        pool = [candidates[k] for k in scenario['candidate_order']]
        conflicts = ledger.pool_conflicts(pool)
        selection_pool = [dict(proposal_id=p['proposal_id'],source_id=p['source_id'],target_id=p['target_id'],
             semantic_eligible=True,semantic_score=6,tie_id=f'f{i+1:02}',
             conflicts=[pid for c in conflicts if p['proposal_id'] in c['proposal_ids'] for pid in c['proposal_ids'] if pid != p['proposal_id']])
             for i,p in enumerate(pool)]
        group = {'nodes':nodes,'edges':graph['content'],'audit_binding':{
            'manifest_sha256':mh,'source_inventory':pages,'all_proposals':pool,
            'authored_semantic_assertions':True,'original_full_graph':graph['original'],
            'mask_version':e.VERSION,'ledger_version':ledger.VERSION,'region':region}}
        policy = dict(DEFAULT_POLICY, maximum=scenario['budget'])
        scorer = BoundedBlockScorer(group, selection_pool, policy, solve_cache)
        original = root/'original'; original.mkdir()
        for path,p in pages.items(): (original/path).write_bytes(contents[p['doc_id']])
        arms = {}
        for arm in ['S','Z','M']:
            result = select_embedded_v2(selection_pool, group, policy, score_cache, arm=arm, scorer=scorer)
            arms[arm] = apply_arm(root/arm, scenario['id'], arm, contents, pages, graph, region, pool, result, mh)
            card_paths.extend(f"{scenario['id']}/{arm}/{p}" for p in arms[arm]['cards'])
        if arms['S']['selection']['selected'] != arms['Z']['selection']['selected']: raise ValueError('q0-semantic-parity')
        # Final S/M costs receive the identical schedules and persistent solve cache.
        # Reachability is an ordinary descriptive check, not a utility endpoint.
        import networkx as nx
        for arm in ['S','Z','M']:
            final_score = scorer(tuple(arms[arm]['selection']['selected']))
            if final_score['status'] != 'finite_estimate': raise ValueError('fixture-final-numerical-unavailable')
            dg = nx.DiGraph(); dg.add_nodes_from(nodes); dg.add_edges_from(graph['content'])
            dg.add_edges_from((p['source_id'],p['target_id']) for p in pool
                             if p['proposal_id'] in arms[arm]['selection']['selected'])
            distances = [d for s in nodes for t,d in nx.single_source_shortest_path_length(dg,s).items() if s!=t]
            arms[arm]['final_primary_cost'] = final_score
            arms[arm]['ordinary_reachability'] = {'reachable_ordered_pairs':len(distances),
                'sum_finite_directed_distances':sum(distances),'mean_finite_directed_distance':sum(distances)/len(distances)}
            write(root/arm/'final-numerical-and-reachability.json', {'final_primary_cost':final_score,
                  'ordinary_reachability':arms[arm]['ordinary_reachability']})
        diagnostic = []
        for p in pool:
            pairs = [[p['source_id'],p['target_id']]]
            rows = [bp_compare(group, pairs, .10, seed, solve_cache) for seed in [3501,3502]]
            distance = int(p['target_id'])-int(p['source_id']); cycle = distance+1
            analytic = {}
            for q in [.25,.10]:
                holonomy = math.remainder((distance-1)*2*math.pi*q, 2*math.pi)
                analytic[str(q)] = cycle*(1-math.cos(holonomy/cycle))
            primary = next(r['schedule_costs'] for r,pid in zip(arms['M']['selection']['trace'][0]['raw_scores'],
                arms['M']['selection']['trace'][0]['tier']) if pid==p['proposal_id'])
            if max(abs(x-analytic['0.25']) for x in primary)>1e-8 or max(abs(r['cost']-analytic['0.1']) for r in rows)>1e-8:
                raise ValueError('independent-cycle-formula-mismatch')
            diagnostic.append({'proposal_id':p['proposal_id'],'analytic_raw_cycle_energy':analytic,'q10_schedules':rows})
        write(root/'pool.json', pool); write(root/'diagnostic.json', diagnostic)
        summaries.append({'scenario':scenario['id'],'arms':arms,'diagnostic':diagnostic,'authored_assertions':True})
    write(out/'solves.json', solve_cache); write(out/'score-cache.json', score_cache)
    index = '<!doctype html><meta charset="utf-8"><title>Authored refinement demonstration</title><h1>Authored refinement demonstration</h1><p>Source previews; equal-score designed cases. No editorial efficacy or reader benefit is established.</p><ul>'
    index += ''.join(f'<li><a href="{p}">{p}</a></li>' for p in card_paths)+'</ul>'
    (out/'index.html').write_text(index,encoding='utf-8')
    write(out/'proposal.v2.schema.json', ledger.PROPOSAL_SCHEMA)
    return {'scenarios':summaries,'cards':card_paths,'unique_solves':len(solve_cache)}


def bp_compare(group, pairs, q, seed, cache):
    # Diagnostic also uses the process watchdog, never an unguarded native solve.
    from .embedded_selection_v2 import _worker
    import multiprocessing as mp
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp)/'diagnostic.json')
        proc = mp.get_context('spawn').Process(target=_worker,args=(path,group,pairs,q,DEFAULT_POLICY['numerical'],[seed],cache,100*1024**2))
        proc.start(); proc.join(600)
        if proc.is_alive(): proc.terminate(); proc.join(); raise TimeoutError('diagnostic-wall')
        result = read(Path(path))
        if 'failure' in result: raise ValueError(result['failure'])
        cache.update(result['cache']); return result['rows'][0]


def tokens(text):
    """Frozen v1 lowercase Unicode word runs; no stopword fitting."""
    return set(re.findall(r'\w+(?:[’\'-]\w+)*', text.lower(), flags=re.UNICODE))


def paragraphs(raw, *, source=False):
    """Parser paragraph plus a per-character raw UTF-8 map; omissions are explicit."""
    text = raw.decode(); byte_offsets = [0]
    for ch in text: byte_offsets.append(byte_offsets[-1]+len(ch.encode()))
    rows = []; omitted = []
    for i,b in enumerate(e.blocks(raw)[0]):
        if b['parent'] != 'paragraph_open': continue
        reason = None
        if not b['map']: reason = 'unmappable'
        elif source and (b['in_list'] or b['in_quote']): reason = 'list-or-quote'
        elif source and any(c in b['content'] for c in ('{{','{%','<','|')): reason = 'unsupported-context'
        elif source and any(t.type not in ('text','softbreak','hardbreak','link_open','link_close') for t in b['children']):
            reason = 'unsupported-inline-syntax'
        if reason: omitted.append({'block':i,'reason':reason,'lines':b['lines']}); continue
        visible = []; mapping = []; cursor = 0; failed = False
        for t in b['children']:
            if t.type in ('link_open','link_close'): continue
            if t.type in ('softbreak','hardbreak'): value = '\n'
            elif t.type in ('text','code_inline'): value = t.content
            else: failed = True; break
            at = b['content'].find(value,cursor)
            if at < 0: failed = True; break
            for j,ch in enumerate(value):
                char = b['map'][at+j]
                # A normalized softbreak may map to the LF in CRLF; both facts remain explicit.
                if text[char] != ch: failed = True; break
                mapping.append([byte_offsets[char],byte_offsets[char+1]])
            visible.append(value); cursor=at+len(value)
        value = ''.join(visible)
        if failed or not value or len(mapping)!=len(value):
            omitted.append({'block':i,'reason':'unmappable-semantic-projection','lines':b['lines']}); continue
        limit = 3000 if source else 2000
        if len(value)>limit:
            omitted.append({'block':i,'reason':'overlong','characters':len(value),'limit':limit,'lines':b['lines']}); continue
        # A disclosed source must have at least one legal plain-text word in a link-free sentence.
        legal = []
        if source:
            links = e._parsed_links(b['content'],b['env'])
            for match in re.finditer(r'\w+(?:[’\'-]\w+)*',b['content'],flags=re.UNICODE):
                a,z = match.span(); lo,hi = e.sentence_span(b['content'],a,z)
                if any(lo<=x<hi for x,_,_ in links): continue
                if any(c in match[0] for c in '\\`[]<>*_'): continue
                legal.append([byte_offsets[b['map'][a]],byte_offsets[b['map'][z-1]+1]])
            if not legal:
                omitted.append({'block':i,'reason':'no-host-admissible-anchor','lines':b['lines']}); continue
        rows.append({'block':i,'text':value,'raw_span':[mapping[0][0],mapping[-1][1]],
                     'raw_byte_map':mapping,'legal_word_spans':legal,'lines':b['lines']})
    return rows, omitted


def access_run(inp, out, cfg):
    started = time.monotonic(); pages = read(inp/cfg['pages']); regions = read(inp/cfg['regions'])
    (out/'packets').mkdir()
    if isinstance(pages,list): pages={p['path']:p for p in pages}
    by_id={p['doc_id']:p for p in pages.values()}
    if len(regions)!=4 or any(r['assignment']!='development' for r in regions): raise ValueError('development-only-four-groups')
    raw={p['doc_id']:(inp/cfg['corpus']/path).read_bytes() for path,p in pages.items()}
    for n,r in raw.items():
        if e.rawhash(r)!=by_id[n]['raw_sha256']: raise ValueError('source-hash-mismatch')
    original=set(map(tuple,read(inp/cfg['original'])['edges']))
    parsed={}; source_parsed={}; omissions=[]
    nodes=set(n for r in regions for n in r['nodes'])
    for n in sorted(nodes):
        parsed[n], omitted=paragraphs(raw[n]); omissions.extend(dict(doc_id=n,role='target',**x) for x in omitted)
        source_parsed[n], omitted=paragraphs(raw[n],source=True); omissions.extend(dict(doc_id=n,role='source',**x) for x in omitted)
        if time.monotonic()-started>600: raise TimeoutError('access-construction-wall')
    packets=[]; groups=[]
    for region in regions:
        membership=region['component_membership']; possible={}
        for s in sorted(region['nodes']):
            possible[s]=[t for t in sorted(region['nodes']) if s!=t and membership[s]==membership[t]
                          and (s,t) not in original and (t,s) not in original]
        legal=[s for s in region['nodes'] if source_parsed[s] and possible[s]]
        selected=sorted(legal,key=lambda s:e.digest([3602,region['region_id'],s]))[:10]
        packet_start=len(packets); no_evidence=[]
        for s in selected:
            src=sorted(source_parsed[s],key=lambda b:e.digest([3602,s,*b['raw_span']]))[:3]
            def overlap(b, target):
                a=tokens(b['text']); z=tokens(target['text']); return len(a&z)/len(a|z) if a|z else 0.
            targets=[]
            for t in possible[s]:
                ranked=sorted(parsed[t],key=lambda b:(-max(overlap(x,b) for x in src),b['raw_span']))
                if not ranked:
                    no_evidence.append({'source':s,'target':t,'reason':'no-mappable-under-limit-target-paragraph'}); continue
                score=max(overlap(x,ranked[0]) for x in src)
                heading=next((b['content'] for b in e.blocks(raw[t])[0] if b['parent']=='heading_open'),by_id[t]['path'])
                targets.append({'target_id':t,'path':by_id[t]['path'],'title':heading,'source_sha256':e.rawhash(raw[t]),
                                'jaccard':score,'paragraphs':ranked[:2]})
            targets=sorted(targets,key=lambda t:(-t['jaccard'],t['target_id']))[:5]
            if not targets:
                no_evidence.append({'source':s,'reason':'no-disclosable-targets'}); continue
            packet={'schema_version':VERSION+'.access_packet','region_id':region['region_id'],'source_id':s,
                'source_path':by_id[s]['path'],'source_sha256':e.rawhash(raw[s]),'component_id':membership[s],
                'mask_status':'frozen-source-only-heuristic-unvalidated-semantics','source_paragraphs':src,
                'targets':targets,'max_proposals':2,'seed':3602,'graph_scores_disclosed':False}
            packet['packet_sha256']=e.digest(packet); packets.append(packet)
            write(out/'packets'/(packet['packet_sha256']+'.json'),packet)
            if time.monotonic()-started>600: raise TimeoutError('access-construction-wall')
        groups.append({'region_id':region['region_id'],'source_families':region['source_families'],'nodes':len(region['nodes']),
            'components':len(set(membership.values())),'legal_source_pages':sum(bool(source_parsed[s]) for s in region['nodes']),
            'legal_anchor_word_spans':sum(len(p['legal_word_spans']) for s in region['nodes'] for p in source_parsed[s]),
            'permissible_ordered_pairs':sum(map(len,possible.values())), 'source_pages_with_opportunity':len(legal),
            'hash_selected_sources':selected,'packets':len(packets)-packet_start,
            'disclosed_source_paragraphs':sum(len(p['source_paragraphs']) for p in packets[packet_start:]),
            'disclosed_target_pages':sum(len(p['targets']) for p in packets[packet_start:]),
            'omitted_unavailable_target_maps':no_evidence,'halo':0})
    write(out/'paragraph-omissions.json',omissions); write(out/'packets.json',packets)
    write(out/'opportunity.json',{'groups':groups,'packets':len(packets),'max_proposals':2*len(packets),
        'access_runtime_seconds':time.monotonic()-started,'tokenizer':"lowercase Unicode \\w+(?:[’'-]\\w+)*; no stopwords",
        'model_calls':0,'source_mask_semantics_validated':False,'reserved_outcomes_inspected':False})
    return {'groups':groups,'packets':len(packets),'max_proposals':2*len(packets),'model_calls':0}


def run(inp, out):
    import resource
    started=time.monotonic(); request=read(inp/'operator-request.json')
    inv=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    if inv!=request['input_inventory'] or e.digest(inv)!=request['input_inventory_sha256']: raise ValueError('input-inventory-mismatch')
    if set(p.name for p in out.iterdir())!={'.retrieval-arena-output-owner'}: raise ValueError('output-not-fresh')
    cfg=read(inp/'config.json')
    if request['stage']=='fixtures': result=fixture_run(inp,out,cfg)
    elif request['stage']=='access':
        import multiprocessing as mp
        proc=mp.get_context('spawn').Process(target=_access_worker,args=(inp,out,cfg))
        proc.start(); proc.join(600)
        if proc.is_alive():
            proc.terminate(); proc.join(1)
            if proc.is_alive(): proc.kill(); proc.join()
            write(out/'access-timeout.json',{'status':'resource_limit','ceiling_seconds':600})
            raise TimeoutError('access-process-wall-watchdog')
        if proc.exitcode != 0: raise RuntimeError('access-worker-failed')
        result=read(out/'access-result.json')
    else: raise ValueError('demonstrator-stage')
    write(out/'summary.json',{'schema_version':VERSION,'stage':request['stage'],'result':result,
        'runtime_seconds':time.monotonic()-started,'peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'model_calls':0,'provider_spend_usd':0,'reserved_outcomes_inspected':False,
        'source_image':{k:os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE','RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']}})
    inv=portable_inventory(out); write(out/'manifest.json',{'outputs':inv,'outputs_sha256':e.digest(inv)})


def _access_worker(inp, out, cfg):
    write(out/'access-result.json',access_run(inp,out,cfg))
