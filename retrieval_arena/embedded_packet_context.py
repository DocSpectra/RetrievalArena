"""Single deterministic source-only packet policy; exact bounded raw context.

No model, score, prior label or corpus identity is used. Caller fixes source and
target page identities. Evidence windows never create additional editable IDs.
"""
import copy
import re

from . import embedded_links as e

VERSION = 'retrieval_arena.embedded_packet_context.v1'
POLICY = {'seed': 3901, 'source_paragraphs': 3, 'target_paragraphs': 2,
          'source_chars': 3000, 'target_chars': 2000,
          'window_bytes': 4096, 'page_context_bytes': 6144}


def substantive(text):
    """Prose heuristic, including short imperatives; not a semantic judgment."""
    if re.fullmatch(r'(?is).*\b(?:output|results?)\b.*\bfollowing\.?\s*',text):return False
    for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
        if re.search(r'[.!?]\s*$', sentence) and len(re.findall(r'[^\W\d_]+', sentence, re.UNICODE)) >= 2:
            return True
    return False


def paragraph_rows(raw, *, editable, maximum):
    text = raw.decode('utf-8'); offsets = [0]
    for ch in text: offsets.append(offsets[-1] + len(ch.encode()))
    rows = []; omitted = []
    for n, b in enumerate(e.blocks(raw)[0]):
        reason = None
        if b['parent'] != 'paragraph_open' or b['in_list'] or b['in_quote']:
            reason = 'non-paragraph-or-list-quote'
        elif not b['map'] or not b['content']:
            reason = 'unmapped'
        elif any(s in b['content'] for s in ('{{', '{%', '<', '|')):
            reason = 'unsupported-template-html-table'
        elif editable and any(t.type not in ('text','softbreak','hardbreak','link_open','link_close') for t in b['children']):
            reason = 'unsupported-editable-inline-syntax'
        elif not substantive(e.semantic_text(b['content'].encode())):
            reason = 'fragment-or-dependent-leadin'
        elif len(b['content']) > maximum:
            reason = 'overlong-whole-paragraph'
        if reason:
            omitted.append({'block': n, 'lines': b['lines'], 'text': b['content'], 'reason': reason})
            continue
        mapping = [[offsets[c], offsets[c+1]] for c in b['map']]
        rows.append({'block': n, 'lines': b['lines'], 'text': b['content'],
                     'heading': b['heading'], 'raw_span': [mapping[0][0], mapping[-1][1]],
                     'raw_byte_map': mapping})
    return rows, omitted


def raw_row(raw, a, z):
    text = raw[a:z].decode('utf-8'); at = a; mapping = []
    for ch in text:
        mapping.append([at, at+len(ch.encode())]); at += len(ch.encode())
    return {'text': text, 'raw_span': [a, z], 'raw_byte_map': mapping}


def contexts(raw, selected):
    """Whole top-level CommonMark blocks; never split a list/code fence."""
    text = raw.decode('utf-8'); lines = text.splitlines(keepends=True); starts = [0]
    for line in lines: starts.append(starts[-1]+len(line.encode()))
    tokens = e.blocks(raw)[1]
    units = [{'span': [starts[t.map[0]], starts[t.map[1]]], 'type': t.type}
             for t in tokens if t.map and t.level == 0 and t.nesting != -1]
    result = []; audit = []; used = 0; spans = set()
    for p in selected:
        a, z = p['raw_span']
        index = next(i for i,u in enumerate(units) if u['span'][0] <= a < z <= u['span'][1])
        first = index; last = index; size = units[index]['span'][1]-units[index]['span'][0]
        if size > POLICY['window_bytes']:
            audit.append({'passage_span':[a,z], 'reason':'atomic-anchor-block-over-window-cap'}); continue
        if index and units[index-1]['type'] != 'heading_open':
            prior = units[index-1]['span']; candidate = units[index]['span'][1]-prior[0]
            if candidate <= POLICY['window_bytes']: first = index-1; size = candidate
            else: audit.append({'span':prior,'reason':'preceding-block-over-window-cap'})
        for j in range(index+1,len(units)):
            u = units[j]
            if u['type']=='heading_open': break
            if u['span'][1]-units[first]['span'][0] > POLICY['window_bytes']:
                audit.append({'span':u['span'],'reason':'following-block-over-window-cap'}); break
            last = j
        lo, hi = units[first]['span'][0], units[last]['span'][1]
        if (lo,hi) in spans: continue
        if used+hi-lo > POLICY['page_context_bytes']:
            audit.append({'span':[lo,hi],'reason':'page-context-cap'}); continue
        spans.add((lo,hi)); used += hi-lo
        row = raw_row(raw,lo,hi); row.update(heading=p['heading'], editable=False,
                                          block_types=[u['type'] for u in units[first:last+1]])
        result.append(row)
    return result, audit


def tokens(text):
    return set(re.findall(r'\w+(?:[’\'-]\w+)*',text.lower(),re.UNICODE))


def project(parent, raw_by_id):
    """Keep all page identities; return a shortage when no source is suitable."""
    if e.digest({k:v for k,v in parent.items() if k!='packet_sha256'}) != parent['packet_sha256']:
        raise ValueError('packet-context-parent-hash')
    source = parent['source_id']; raw = raw_by_id[source]
    if e.rawhash(raw) != parent['source_sha256']: raise ValueError('packet-context-stale-source')
    rows, omitted = paragraph_rows(raw,editable=True,maximum=POLICY['source_chars'])
    audit = {'source_id':source,'parent_packet_sha256':parent['packet_sha256'],
             'policy_version':VERSION,'policy':POLICY,'source_omissions':omitted,'targets':[]}
    if not rows:
        audit.update(status='no-suitable-source',prospective_packet_sha256=None)
        return None, audit
    selected = [rows[0]]
    if rows[-1] not in selected: selected.append(rows[-1])
    remaining = sorted([r for r in rows if r not in selected],key=lambda r:e.digest([3901,source,r['raw_span']]))
    selected = sorted((selected+remaining[:POLICY['source_paragraphs']-len(selected)]),key=lambda r:r['raw_span'])
    source_context, context_omissions = contexts(raw,selected)
    child = copy.deepcopy(parent); child.pop('packet_sha256')
    title=re.search(rb'(?m)^title:[^\r\n]*',raw[:e._frontmatter(raw.decode('utf-8'))])
    if title and sum(len(p['text'].encode()) for p in source_context)+len(title[0])<=POLICY['page_context_bytes']:
        title_row=raw_row(raw,*title.span());title_row.update(heading='page title',editable=False,block_types=['frontmatter_title'])
        source_context.insert(0,title_row)
    child.update(parent_packet_sha256=parent['packet_sha256'],context_version=VERSION,
                 source_paragraphs=selected,source_context=source_context)
    source_tokens = [tokens(p['text']) for p in selected]
    for target in child['targets']:
        tid = target['target_id']; traw = raw_by_id[tid]
        if e.rawhash(traw) != target['source_sha256']: raise ValueError('packet-context-stale-target')
        paragraphs, omissions = paragraph_rows(traw,editable=False,maximum=POLICY['target_chars'])
        def overlap(p):
            t = tokens(p['text'])
            return max((len(t&s)/len(t|s) if t|s else 0 for s in source_tokens),default=0)
        chosen = sorted(paragraphs,key=lambda p:(-overlap(p),p['raw_span'][0]))[:POLICY['target_paragraphs']]
        evidence_context, missing = contexts(traw,chosen)
        target.update(paragraphs=chosen,evidence_context=evidence_context)
        audit['targets'].append({'target_id':tid,'paragraph_spans':[p['raw_span'] for p in chosen],
            'omissions':omissions,'context_omissions':missing,'suitable_evidence':bool(chosen)})
    child['packet_sha256'] = e.digest(child)
    audit.update(status='prospective',prospective_packet_sha256=child['packet_sha256'],
                 source_spans=[p['raw_span'] for p in selected],context_omissions=context_omissions)
    return child, audit


def prepare(inp, out, cfg):
    """Immutable offline operator stage; consumer owns page list and prompts."""
    from . import embedded_refinement as f
    from . import embedded_interface as interface
    from .embedded_link_audit import read, write
    from .model_providers.openai import OpenAITransport
    _,_,raw,_=f.load_sources(inp)
    manifest=read(inp/'study-manifest.json')
    role=next(r for r in manifest['roles'] if r['role']=='generation')
    children=[];changes=[];jobs=[];inventory=[]
    (out/'host-disclosures').mkdir()
    for parent in read(inp/'packets.json'):
        child,audit=project(parent,raw);changes.append(audit)
        if child is None:continue
        data=interface.disclosure(child,raw);public=interface.public_disclosure(data)
        if manifest.get('competitive_selection_version'):
            public.update(max_proposals=len(public['targets']),target_dispositions_required=True)
        body=OpenAITransport.request(role['prompt_prefix']+f.MARKER+e.canonical(public).decode(),role['schema'],role['settings'])
        children.append(child);jobs.append({'job_id':e.digest(body),'body':body})
        inventory.append({'source_id':child['source_id'],'parent_packet_sha256':parent['packet_sha256'],
            'prospective_packet_sha256':child['packet_sha256'],'request_sha256':e.digest(body),
            'serialized_request_bytes':len(e.canonical(body)),'provider_input_tokens':None,
            'within_byte_cap':len(e.canonical(body))<=manifest['lifecycle_policy']['maximum_count_request_bytes']})
        write(out/'host-disclosures'/(child['packet_sha256']+'.json'),data)
    write(out/'packets.json',children);write(out/'changes.json',changes)
    write(out/'jobs.json',jobs);write(out/'request-inventory.json',inventory)
    write(out/'policy.json',{'version':VERSION,**POLICY})
    return {'policy_version':VERSION,'parents':len(changes),'packets':len(children),
        'source_shortages':len(changes)-len(children),'target_pairs':sum(len(p['targets']) for p in children),
        'over_byte_cap':sum(not r['within_byte_cap'] for r in inventory),'provider_calls':0}
