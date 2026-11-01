"""Source-only content links and constrained edits. No model or spectral calls."""
from __future__ import annotations

import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_inline import link as link_rule

from .graph.links import graph_metrics

VERSION = 'retrieval_arena.embedded_links.v1'
COORDINATES = 'zero-based half-open UTF-8 bytes of exact raw file'
RELATIONS = {
    'source_requires_target':'Target knowledge is a prerequisite; target-before-source ordering.',
    'source_continues_to_target':'Source workflow continues at target.',
    'target_elaborates_source':'Target explains source material in more detail.',
    'target_exemplifies_source':'Target supplies an example of source material.',
    'target_diagnoses_source':'Target diagnoses a problem mentioned by source.',
    'related_context':'Related context without a stronger directed semantic assertion.',
    'insufficient_evidence':'Relationship support is missing or uncertain.'}

def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

def digest(x): return hashlib.sha256(canonical(x)).hexdigest()
def rawhash(x): return hashlib.sha256(x).hexdigest()
def stable(namespace, *parts): return namespace+':'+digest(parts)

def parser(): return MarkdownIt('commonmark', {'html':True})

def _frontmatter(text):
    m = re.match(r'\A---\r?\n.*?\r?\n---(?:\r?\n|$)', text, re.S)
    return m.end() if m else 0

def _body(text):
    # Preserve line/character positions while preventing YAML from becoming prose.
    n = _frontmatter(text)
    return ''.join('\n' if c == '\n' else ' ' for c in text[:n])+text[n:]

def visible_text(raw):
    """Parser token projection, including code/HTML text; link destinations omitted.

    This is a declared CommonMark text view, not the site's rendered Hugo output.
    Block kind/order and literal HTML are retained, so hidden source edits cannot
    disappear through a generic strip-links comparison.
    """
    tokens = parser().parse(_body(raw.decode('utf-8')))
    out=[]
    for t in tokens:
        if t.type == 'inline':
            out.append(['inline', [[c.type, c.content] for c in t.children
                       if c.type not in ('link_open','link_close')]])
        elif t.type in ('fence','code_block','html_block'):
            out.append([t.type,t.content])
        elif t.type not in ('paragraph_open','paragraph_close'):
            out.append([t.type,t.tag,t.nesting])
    return out

def semantic_text(raw):
    """Explicit fixed ranking view, not the existing retriever's raw-source view."""
    def values(x):
        for row in x:
            if row[0]=='inline':
                yield ''.join('\n' if typ in ('softbreak','hardbreak') else v
                              for typ,v in row[1] if typ in ('text','code_inline','softbreak','hardbreak'))
            elif row[0] in ('fence','code_block','html_block'): yield row[1]
    return '\n'.join(values(visible_text(raw)))

def _source_map(content, text, line_start, line_end):
    """Map parser-normalized inline characters to original source characters.

    CommonMark strips indentation/list markers and normalizes CRLF. Every
    content line must match its source line exactly; otherwise editing is refused.
    """
    lines=text.splitlines(keepends=True)
    bases=[]; pos=0
    for line in lines: bases.append(pos); pos+=len(line)
    mapping=[]; cur=line_start
    for i,line in enumerate(content.split('\n')):
        found=False
        while cur < min(line_end,len(lines)):
            raw=lines[cur].rstrip('\r\n'); at=raw.find(line)
            if at>=0:
                if i: mapping.append(bases[cur]-1 if cur else 0)
                mapping.extend(range(bases[cur]+at,bases[cur]+at+len(line)))
                cur+=1; found=True; break
            cur+=1
        if not found: return None
    return mapping if len(mapping)==len(content) else None

def blocks(raw):
    text=raw.decode('utf-8'); env={}; md=parser(); ts=md.parse(_body(text),env)
    result=[]; stack=[]; heading=''
    for t in ts:
        if t.nesting==-1 and stack: stack.pop()
        if t.type=='inline' and t.map:
            parent=stack[-1] if stack else ''
            if parent=='heading_open': heading=t.content.lower()
            mapping=_source_map(t.content,text,*t.map)
            result.append({'content':t.content,'map':mapping,'lines':t.map,'parent':parent,
                'in_list':'list_item_open' in stack,'in_quote':'blockquote_open' in stack,
                'heading':heading,'env':env,'children':t.children})
        if t.nesting==1: stack.append(t.type)
    return result, ts

def _parsed_links(content, env):
    found=[]; md=parser()
    def capture(state,silent):
        start=state.pos; n=len(state.tokens)
        ok=link_rule(state,silent)
        if ok and not silent:
            opens=[t for t in state.tokens[n:] if t.type=='link_open']
            if opens: found.append((start,state.pos,opens[0].attrGet('href')))
        return ok
    md.inline.ruler.at('link',capture)
    md.parseInline(content,env)
    return sorted(set(found))

def _classification(b, start, end):
    content=b['content']
    if b['parent']=='heading_open': return 'other_supported','heading'
    if '{{' in content or '{%' in content or '<' in content or b['in_quote']:
        return 'ambiguous','template-html-or-quote-context'
    # Clear link-only directory/list entries. Mixed prose on the same page survives.
    outside=content[:start]+content[end:]
    outside=re.sub(r'\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\[[^\]]*\]','',outside)
    if not outside.strip(' \t\r\n,;:|/–—-*'):
        return 'navigation','link-only-block'
    if b['in_list'] and (b['heading'] in ('contents','table of contents','on this page','navigation')
                         or not re.search(r'[.!?](?:\s|$)', content)):
        return 'ambiguous','list-without-clear-sentence'
    return 'substantive','paragraph-or-sentence-list'

def resolve(href, source_path, pages):
    """Declared source-relative resolver plus content-root site aliases.

    No shortcode evaluation or external-site fetching. Fragments require a
    unique simple CommonMark heading slug or explicit HTML id; uncertainty fails.
    """
    parsed=urlsplit(href)
    if parsed.scheme or parsed.netloc or any(c in href for c in ['\\','\x00','{{','{%']):
        return None,'unsafe-external-or-template'
    path=unquote(parsed.path)
    if parsed.query: return None,'query-unsupported'
    if not path: return None,'fragment-only-no-page-edge'
    base=path.lstrip('/') if path.startswith('/') else posixpath.join(posixpath.dirname(source_path),path)
    base=posixpath.normpath(base).rstrip('/')
    if base.startswith('../') or base=='..': return None,'path-escape'
    candidates=[base,base+'.md',base+'/index.md',base+'/_index.md']
    if base.endswith(('.html','.htm')): candidates.append(base.rsplit('.',1)[0]+'.md')
    if path.startswith('/'):
        candidates += ['content/'+p for p in candidates.copy()]
    matches=sorted({p for p in candidates if p in pages})
    if len(matches)!=1: return None,'unresolved' if not matches else 'ambiguous-target'
    return matches[0], 'resolved'

def fragment_valid(raw,fragment):
    if not fragment: return True
    fragment=unquote(fragment)
    text=raw.decode('utf-8'); bs,_=blocks(raw); slugs=[]
    for b in bs:
        if b['parent']=='heading_open':
            plain=''.join(t.content for t in b['children'] if t.type in ('text','code_inline'))
            slugs.append(re.sub(r'[^\w\s-]','',plain.lower()).strip().replace(' ','-'))
    slugs+=re.findall(r'\bid=["\']([^"\']+)["\']',text)
    return slugs.count(fragment)==1

def qualify_fragments(rows,contents):
    cache={}
    for r in rows:
        if r['target_id'] and r['fragment']:
            key=(r['target_id'],r['fragment'])
            if key not in cache: cache[key]=fragment_valid(contents[r['target_id']],r['fragment'])
            r['fragment_resolution']='unique-source-heading-or-id' if cache[key] else 'unverified-source-fragment'
            if not cache[key] and r['class']=='substantive':
                r['class']='ambiguous'; r['reason']='unverified-source-fragment'
        else: r['fragment_resolution']='not-applicable'
    return rows

class _HTMLLinks(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=='a':
            for k,v in attrs:
                if k=='href' and v is not None: self.links.append((self.getpos(),self.get_starttag_text(),v))

def occurrences(raw, source, pages):
    text=raw.decode('utf-8'); bs,ts=blocks(raw); result=[]
    offsets=[0]
    for line in text.splitlines(keepends=True): offsets.append(offsets[-1]+len(line))
    def add(start,end,href,kind,cls,reason,context):
        target,status=resolve(href,source['path'],pages)
        frag=urlsplit(href).fragment
        byte_start=len(text[:start].encode()); byte_end=len(text[:end].encode())
        result.append({'occurrence_id':stable('occurrence',source['doc_id'],rawhash(raw),byte_start,byte_end,kind),
            'source_id':source['doc_id'],'source_path':source['path'],'raw_sha256':rawhash(raw),
            'span':[byte_start,byte_end],'coordinate_convention':COORDINATES,
            'line':text.count('\n',0,start)+1,'syntax':kind,'source_syntax':text[start:end],
            'href':href,'target_path':target,'target_id':pages[target]['doc_id'] if target else None,
            'fragment':frag,'resolution':status,'class':cls,'reason':reason,'context':context,
            'edge_id':stable('edge',source['doc_id'],pages[target]['doc_id']) if target else None})
    for b in bs:
        for start,end,href in _parsed_links(b['content'],b['env']):
            cls,reason=_classification(b,start,end)
            if b['map'] is None:
                # Mapping failure is accounted at its full source block, never editable.
                a,z=offsets[b['lines'][0]],offsets[min(b['lines'][1],len(offsets)-1)]
                cls,reason='ambiguous','source-map-unavailable'
            else: a,z=b['map'][start],b['map'][end-1]+1
            syntax='markdown_inline' if '](' in b['content'][start:end] else 'markdown_reference'
            add(a,z,href,syntax,cls,reason,b['content'])
    # HTML occurrences are inventoried but not edited; semantics are not rendered.
    for t in ts:
        regions=[]
        if t.type=='html_block' and t.map: regions=[(offsets[t.map[0]],t.content)]
        elif t.type=='inline' and t.map:
            regions=[(offsets[t.map[0]],text[offsets[t.map[0]]:offsets[t.map[1]]])]
        for base,html in regions:
            h=_HTMLLinks(); h.feed(html)
            ls=html.splitlines(keepends=True)
            for (line,col),tag,href in h.links:
                a=base+sum(map(len,ls[:line-1]))+col
                cls='navigation' if re.search(r'<(?:nav|menu)\b',html,re.I) else 'ambiguous'
                add(a,a+len(tag),href,'html_anchor',cls,'html-not-rendered',html)
    return sorted(result,key=lambda r:(r['span'],r['syntax']))

def project(rows,nodes,mode='content'):
    classes={'content':{'substantive'},'sensitivity':{'substantive','ambiguous'},
             'original':{'substantive','ambiguous','navigation','other_supported'}}[mode]
    return sorted({(r['source_id'],r['target_id']) for r in rows if r['class'] in classes
                   and r['target_id'] in nodes and r['source_id']!=r['target_id']})

def summary(nodes,edges):
    m=graph_metrics(edges,nodes)
    undirected={tuple(sorted(e)) for e in edges}
    m['undirected_edges']=len(undirected)
    m['cycle_rank']=len(undirected)-len(nodes)+m['weak_component_count']
    m['orientation_endpoint']='forest-or-isolated-uninformative' if not m['cycle_rank'] else 'cycles-present-unqualified'
    m['connected_coverage']=m['largest_component_size']/len(nodes) if nodes else 0
    m['graph_sha256']=digest({'nodes':nodes,'edges':edges})
    return m

def sentence_span(text,start,end):
    """Fixed lexical sentence: punctuation followed by whitespace, or line breaks."""
    cuts=[0]+[m.end() for m in re.finditer(r'[.!?](?=\s|$)|\n',text)]+[len(text)]
    lo=max(c for c in cuts if c<=start); hi=min(c for c in cuts if c>=end)
    return lo,hi

def pool_conflicts(proposals):
    """Unordered conflict pairs in a shared pre-selection pool; no scores used.

    Callers retain all proposals and record the conflicts/skips in the ledger.
    A reciprocal proposal pair is structurally ineligible on both sides. A
    duplicate page pair or overlapping anchor cannot consume two edit slots.
    """
    by_id={}
    for p in proposals:
        validate_schema(p,PROPOSAL_SCHEMA)
        if p['proposal_id'] in by_id: raise ValueError('duplicate-proposal-id')
        by_id[p['proposal_id']]=p
    conflicts=[]
    ordered=sorted(by_id)
    for i,a in enumerate(ordered):
        p=by_id[a]
        for b in ordered[i+1:]:
            q=by_id[b]
            if (p['corpus_id'],p['snapshot_id'])!=(q['corpus_id'],q['snapshot_id']): continue
            reason=None
            if (p['source_id'],p['target_id'])==(q['target_id'],q['source_id']): reason='proposed-reciprocal-partner'
            elif (p['source_id'],p['target_id'])==(q['source_id'],q['target_id']): reason='duplicate-page-pair'
            elif p['source_id']==q['source_id'] and p['source_sha256']==q['source_sha256']:
                a0,a1=p['source_span']; b0,b1=q['source_span']
                if a0<b1 and b0<a1: reason='overlapping-proposed-anchors'
            if reason: conflicts.append({'proposal_ids':[a,b],'reason':reason})
    return conflicts

def apply_edit(raw,target_raw,proposal,pages,baseline_edges,region,limits,prior_spans=(),proposed_pool=()):
    """Return bytes and host facts, without writing to an original corpus."""
    validate_schema(proposal,PROPOSAL_SCHEMA)
    if proposed_pool:
        if proposal['proposal_id'] not in {p['proposal_id'] for p in proposed_pool}: raise ValueError('proposal-pool-binding')
        for conflict in pool_conflicts(proposed_pool):
            if proposal['proposal_id'] in conflict['proposal_ids'] and conflict['reason']=='proposed-reciprocal-partner':
                raise ValueError('proposed-reciprocal-partner')
    if proposal['coordinate_convention']!=COORDINATES: raise ValueError('coordinates')
    if proposal['source_sha256']!=rawhash(raw) or proposal['target_sha256']!=rawhash(target_raw):
        raise ValueError('stale-source-or-target')
    source=proposal['source_id']; target=proposal['target_id']
    if any(region.get(k) is not None and proposal[k]!=region[k] for k in ('corpus_id','snapshot_id')):
        raise ValueError('corpus-snapshot-identity')
    by_id={r['doc_id']:r for r in pages.values()}
    if source not in by_id or target not in by_id or source==target: raise ValueError('identity')
    if proposal['region_id']!=region['region_id'] or source not in region['nodes'] or target not in region['nodes']:
        raise ValueError('region-scope')
    if region['component_membership'].get(source)!=region['component_membership'].get(target):
        raise ValueError('baseline-component')
    if (source,target) in set(map(tuple,baseline_edges)): raise ValueError('existing-directed-pair-no-new-edge')
    if (target,source) in set(map(tuple,baseline_edges)): raise ValueError('reciprocal-partner')
    resolved,status=resolve(proposal['href'],by_id[source]['path'],pages)
    if status!='resolved' or pages[resolved]['doc_id']!=target: raise ValueError('target-resolution')
    frag=urlsplit(proposal['href']).fragment
    if frag!=proposal['target_fragment'] or not fragment_valid(target_raw,frag): raise ValueError('fragment')
    for evidence in proposal['target_evidence']:
        a,z=evidence['span']
        if not 0<=a<z<=len(target_raw) or target_raw[a:z].decode('utf-8')!=evidence['quote']:
            raise ValueError('target-evidence')
    start,end=proposal['source_span']; text=raw.decode('utf-8')
    if not 0<=start<end<=len(raw) or raw[start:end].decode('utf-8')!=proposal['anchor']:
        raise ValueError('anchor-span')
    # Deliberately narrow v1: only literal prose text, no inline markup/escapes.
    anchor=proposal['anchor']
    if any(c in anchor for c in '\\`[]<>*_\r\n') or anchor!=anchor.strip(): raise ValueError('anchor-syntax')
    words=re.findall(r'\w+(?:[’\'-]\w+)*',anchor,flags=re.UNICODE)
    if not limits['min_words']<=len(words)<=limits['max_words'] or limits['max_links_per_sentence']!=1:
        raise ValueError('lexical-span-limit')
    cstart=len(raw[:start].decode('utf-8')); cend=len(raw[:end].decode('utf-8'))
    bs,_=blocks(raw); selected=None
    for b in bs:
        mapping=b['map']
        if mapping and cstart in mapping and cend-1 in mapping:
            a=mapping.index(cstart); z=mapping.index(cend-1)+1
            if b['content'][a:z]!=anchor: continue
            if b['parent']!='paragraph_open' or b['in_quote'] or b['in_list']:
                raise ValueError('heading-navigation-or-list-placement')
            if any(c in b['content'] for c in ('{{','{%','<','|')): raise ValueError('unsupported-context')
            # Refuse pre-existing links/code around the selected source span.
            for x,y,_ in _parsed_links(b['content'],b['env']):
                if a<y and z>x: raise ValueError('nested-or-overlapping-anchor')
            # Source anchors must occur in a plain text inline token. This
            # conservative context guard refuses code/emphasis on the whole block.
            if any(t.type not in ('text','softbreak','hardbreak','link_open','link_close') for t in b['children']):
                raise ValueError('unsupported-inline-syntax')
            lo,hi=sentence_span(b['content'],a,z)
            if re.search(r'[.!?](?=\s)|\n',b['content'][a:z]): raise ValueError('anchor-crosses-sentence')
            if proposal['sentence']!=b['content'][lo:hi]: raise ValueError('sentence-evidence')
            if any(lo<=x<hi for x,_,_ in _parsed_links(b['content'],b['env'])): raise ValueError('sentence-link-budget')
            selected=(b,lo,hi)
            break
    if selected is None: raise ValueError('not-editable-prose')
    for a,z in prior_spans:
        if start<z and end>a: raise ValueError('overlap')
    href=proposal['href']
    if re.search(r'[\s()<>\[\]"\x00-\x1f]',href): raise ValueError('unsafe-link-syntax')
    insertion=b'['+raw[start:end]+b']('+href.encode()+b')'
    edited=raw[:start]+insertion+raw[end:]
    before=visible_text(raw); after=visible_text(edited)
    # CommonMark may split adjacent text tokens around the new link.
    def merge(view):
        view=copy.deepcopy(view)
        for row in view:
            if row[0]=='inline':
                merged=[]
                for typ,val in row[1]:
                    if typ=='text' and merged and merged[-1][0]=='text': merged[-1][1]+=val
                    else: merged.append([typ,val])
                row[1]=merged
        return view
    if merge(before)!=merge(after) or semantic_text(raw)!=semantic_text(edited): raise ValueError('rendered-text-changed')
    src=by_id[source]
    old=project(occurrences(raw,src,pages),set(by_id)); new=project(occurrences(edited,src,pages),set(by_id))
    if set(new)-set(old)!={(source,target)} or set(old)-set(new): raise ValueError('graph-delta')
    patch={'source_id':source,'span':[start,end],'old':anchor,'replacement':insertion.decode()}
    return edited,{'authority':'host','source_before_sha256':rawhash(raw),'source_after_sha256':rawhash(edited),
        'patch':patch,'patch_sha256':digest(patch),'rendered_before_sha256':digest(merge(before)),
        'rendered_after_sha256':digest(merge(after)),'semantic_before_sha256':rawhash(semantic_text(raw).encode()),
        'semantic_after_sha256':rawhash(semantic_text(edited).encode()),
        'before_graph_sha256':digest(old),'after_graph_sha256':digest(new),'added_edges':[[source,target]],
        'graph_hash_scope':'source-only parser projection; corpus fragment mask audited separately',
        'removed_edges':[],'occurrence_id':next(r['occurrence_id'] for r in occurrences(edited,src,pages)
            if r['target_id']==target and r['class']=='substantive'), 'edge_id':stable('edge',source,target)}

def _obj(properties,required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,
            'additionalProperties':False}
S={'type':'string','minLength':1}; H={'type':'string','pattern':'^[0-9a-f]{64}$'}
SPAN={'type':'array','items':{'type':'integer','minimum':0},'minItems':2,'maxItems':2}
EVIDENCE=_obj({'span':SPAN,'quote':S})
PROPOSAL_SCHEMA=_obj({
    'schema_version':{'const':VERSION+'.proposal'},'proposal_id':S,'run_id':S,'arm_id':S,'region_id':S,
    'corpus_id':S,'snapshot_id':S,'source_id':S,'target_id':S,'source_sha256':H,'target_sha256':H,
    'href':S,'target_fragment':{'type':'string'},'coordinate_convention':{'const':COORDINATES},
    'source_span':SPAN,'anchor':S,'sentence':S,'target_evidence':{'type':'array','items':EVIDENCE,'minItems':1},
    'relation':{'enum':list(RELATIONS)},'stated_intent':S,'expected_reading_use':S,
    'basis':{'enum':['explicit_in_source','supported_inference','unsupported']},
    'confidence':{'type':['number','null'],'minimum':0,'maximum':1},
    'confidence_status':{'const':'uncalibrated'},
    'provenance':_obj({'provider':S,'model':S,'prompt_version':S,'schema_version':S,'attempt_id':S,
        'timestamp':S,'response_sha256':H,'source_access':{'type':'array','items':S,'minItems':1},
        'retry_of':{'type':['string','null']},'status':{'enum':['mock-model','model-response']}})})

def validate_schema(value,schema):
    """Closed subset used by our emitted JSON Schemas; no network/dependency."""
    if not schema:
        canonical(value)
        return value
    if 'const' in schema and value!=schema['const']: raise ValueError('schema-const')
    if 'enum' in schema and value not in schema['enum']: raise ValueError('schema-enum')
    typ=schema.get('type'); types=typ if isinstance(typ,list) else [typ] if typ else []
    matches={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),
        'integer':type(value)==int,'number':type(value) in (int,float),'null':value is None,'boolean':type(value)==bool}
    if types and not any(matches[t] for t in types): raise ValueError('schema-type')
    if isinstance(value,dict):
        props=schema['properties']
        if set(schema['required'])-value.keys() or set(value)-props.keys(): raise ValueError('schema-fields')
        for k,v in value.items(): validate_schema(v,props[k])
    elif isinstance(value,list):
        if not schema.get('minItems',0)<=len(value)<=schema.get('maxItems',float('inf')): raise ValueError('schema-length')
        for v in value: validate_schema(v,schema['items'])
    elif isinstance(value,str):
        if len(value)<schema.get('minLength',0) or ('pattern' in schema and not re.fullmatch(schema['pattern'],value)):
            raise ValueError('schema-string')
    elif type(value) in (int,float):
        if not schema.get('minimum',-float('inf'))<=value<=schema.get('maximum',float('inf')): raise ValueError('schema-number')
    canonical(value)  # refuses NaN/Infinity
    return value

TRANSITIONS={None:{'proposed'},'proposed':{'schema_validated','rejected','failed'},
    'schema_validated':{'structurally_validated','rejected','failed'},
    'structurally_validated':{'reviewed','rejected','failed'},'reviewed':{'eligible','rejected','failed'},
    'eligible':{'selected','rejected','failed'},'selected':{'applied','rejected','failed'},
    'applied':{'verified','failed','reverted'},'verified':{'reverted'},
    'failed':{'reverted'},'rejected':set(),'reverted':set()}

EVENT_PAYLOADS={
    'proposed':PROPOSAL_SCHEMA,
    'schema_validated':_obj({'proposal_sha256':H}),
    'structurally_validated':_obj({'eligible':{'type':'boolean'},'reason':S,'validation_sha256':H}),
    'reviewed':_obj({'reviewer_provider':S,'reviewer_model':S,'rubric_version':S,
        'decision':{'enum':['eligible','reject','uncertain']},'evidence':{'type':'array','items':EVIDENCE,'minItems':1},
        'uncertainty':S,'response_sha256':H,'blinded_input_sha256':H,'status':{'enum':['mock-model','model-response']}}),
    'eligible':_obj({'structural':{'const':True},'semantic':{'const':True}}),
    'selected':_obj({'rank':{'type':'integer','minimum':1},'reason':S,'objective_input_sha256':H,
        'objective_output_sha256':H,'requested_budget':{'type':'integer','minimum':0},
        'achieved_budget':{'type':'integer','minimum':0},'conflicts_skips':{'type':'array','items':S}}),
    'applied':_obj({'facts_sha256':H,'patch_sha256':H,'source_before_sha256':H,'source_after_sha256':H,
        'occurrence_id':S,'edge_id':S}),
    'verified':_obj({'facts_sha256':H,'rendered_before_sha256':H,'rendered_after_sha256':H,
        'before_graph_sha256':H,'after_graph_sha256':H}),
    'rejected':_obj({'reason':S,'no_new_edge':{'type':'boolean'}}),
    'failed':_obj({'reason':S}), 'reverted':_obj({'reason':S,'restored_source_sha256':H})}
EVENT_SCHEMA=_obj({'schema_version':{'const':VERSION+'.event'},'event_id':H,'previous_sha256':H,
    'proposal_id':S,'run_id':S,'arm_id':S,'region_id':S,'type':{'enum':list(EVENT_PAYLOADS)},
    'authority':{'enum':['model','reviewer','host']},'timestamp':S,
    'supersedes_event_id':{'type':['string','null']},'payload':{'type':'object','properties':{},'required':[]}})
# Payload is a tagged union checked separately; JSON Schema carries the same union.
EVENT_SCHEMA['properties']['payload']={}
EVENT_SCHEMA['allOf']=[{'if':{'properties':{'type':{'const':typ}}},
    'then':{'properties':{'payload':schema}}} for typ,schema in EVENT_PAYLOADS.items()]

def event(log,proposal,typ,payload,authority='host',supersedes=None,timestamp=None):
    row={'schema_version':VERSION+'.event','previous_sha256':digest(log[-1]) if log else '0'*64,
        **{k:proposal[k] for k in ('proposal_id','run_id','arm_id','region_id')},
        'type':typ,'authority':authority,'timestamp':timestamp or proposal['provenance']['timestamp'],
        'supersedes_event_id':supersedes,'payload':payload}
    row['event_id']=digest(row)
    replay([*log,row])
    return row

def replay(log,trusted_tip=None):
    """Validate chain and legal transitions. Exact duplicate delivery is idempotent.

    A trusted tip is needed against complete chain rewriting/truncation. Provider
    accounting belongs in another file and cannot alter scientific verdicts.
    """
    seen={}; states={}; previous='0'*64; bindings={}; facts={}; decisions={}; latest={}
    for r in log:
        if r['event_id'] in seen:
            if r!=seen[r['event_id']]: raise ValueError('duplicate-event-tamper')
            continue
        validate_schema(r,EVENT_SCHEMA); validate_schema(r['payload'],EVENT_PAYLOADS[r['type']])
        body={k:v for k,v in r.items() if k!='event_id'}
        if digest(body)!=r['event_id'] or r['previous_sha256']!=previous: raise ValueError('ledger-chain')
        pid=r['proposal_id']; typ=r['type']; payload=r['payload']
        correction=(typ==states.get(pid) and typ not in ('applied','verified','reverted','failed','rejected')
                    and r['supersedes_event_id']==latest.get(pid))
        if typ not in TRANSITIONS[states.get(pid)] and not correction: raise ValueError('ledger-transition')
        if r['supersedes_event_id'] is not None:
            old=seen.get(r['supersedes_event_id'])
            if old is None or old['proposal_id']!=pid: raise ValueError('invalid-supersession')
        if r['authority']!=({'proposed':'model','reviewed':'reviewer'}.get(typ,'host')): raise ValueError('ledger-authority')
        binding=[r[k] for k in ('run_id','arm_id','region_id')]
        if pid in bindings and bindings[pid]!=binding: raise ValueError('identity-drift')
        bindings[pid]=binding
        if typ=='proposed':
            if any(payload[k]!=r[k] for k in ('proposal_id','run_id','arm_id','region_id')): raise ValueError('proposal-binding')
            facts[pid]={'proposal':payload}
        elif typ=='schema_validated' and payload['proposal_sha256']!=digest(facts[pid]['proposal']): raise ValueError('schema-binding')
        elif typ=='structurally_validated': decisions[pid]={'structural':payload['eligible']}
        elif typ=='reviewed': decisions[pid]['semantic']=payload['decision']=='eligible'
        elif typ=='eligible' and not all(decisions[pid].values()): raise ValueError('ineligible-selection')
        elif typ=='verified':
            if payload['rendered_before_sha256']!=payload['rendered_after_sha256']: raise ValueError('identity-verification')
            if payload['facts_sha256']!=facts[pid]['applied']['facts_sha256']: raise ValueError('application-binding')
        if typ=='applied': facts[pid]['applied']=payload
        states[pid]=typ; seen[r['event_id']]=r; latest[pid]=r['event_id']; previous=digest(r)
    if trusted_tip is not None and previous!=trusted_tip: raise ValueError('untrusted-ledger-tip')
    return {'states':states,'unique_events':len(seen),'tip_sha256':previous}

def append_event(path,row,trusted_tip=None):
    """Single-writer append API. Callers retain the returned tip externally."""
    import os
    old=[json.loads(line) for line in path.read_bytes().splitlines() if line] if path.exists() else []
    if trusted_tip is not None: replay(old,trusted_tip)
    result=replay([*old,row])
    if not any(r['event_id']==row['event_id'] for r in old):
        with path.open('ab') as stream:
            stream.write(canonical(row)+b'\n'); stream.flush(); os.fsync(stream.fileno())
    return result

def blind_review_input(proposal,source_raw,target_raw):
    """Allowlist: no arm, scores, proposer intent or earlier verdicts."""
    return {'source_passage':proposal['sentence'],'anchor':proposal['anchor'],
        'target_passages':[e['quote'] for e in proposal['target_evidence']],
        'source_sha256':rawhash(source_raw),'target_sha256':rawhash(target_raw)}

def verify_response_bytes(raw,expected_hash):
    if rawhash(raw)!=expected_hash: raise ValueError('raw-response-hash')
    return True

def navigation_view(raw,source,pages):
    return [{'destination':r['target_id'],'href':r['href'],'source_syntax':r['source_syntax'],
             'anchor':''.join(t.content for t in parser().parseInline(r['source_syntax'])[0].children
                              if t.type in ('text','code_inline')),
             'sentence_context':r['context'],'occurrence_id':r['occurrence_id']} for r in occurrences(raw,source,pages)
            if r['target_id'] and r['syntax'].startswith('markdown')]

def replay_patch(raw,facts):
    """Idempotent host patch replay; require trusted ledger-bound facts digest."""
    if digest(facts['patch'])!=facts['patch_sha256']: raise ValueError('patch-tamper')
    if rawhash(raw)==facts['source_after_sha256']: return raw
    if rawhash(raw)!=facts['source_before_sha256']: raise ValueError('stale-replay-source')
    p=facts['patch']; a,z=p['span']
    if raw[a:z].decode('utf-8')!=p['old']: raise ValueError('patch-old-span')
    new=raw[:a]+p['replacement'].encode()+raw[z:]
    if rawhash(new)!=facts['source_after_sha256']: raise ValueError('patch-after-hash')
    return new

def freeze_regions(pages,contents,edges,config,exclusion_edges=None):
    """Source families merged for duplicates and shared literal include references.

    No halo in v1. All external edges are reported. Hash order is independent of
    spectral outcomes; all components and isolates remain in each selected core.
    """
    depth=config['source_family_depth']; family={}
    for p,row in pages.items():
        parts=p.split('/')
        family[row['doc_id']]='/'.join(parts[:min(depth,len(parts)-1)]) or '<root>'
    parents={f:f for f in family.values()}
    def root(x):
        while parents[x]!=x: x=parents[x]
        return x
    def union(a,b):
        a,b=root(a),root(b)
        if a!=b: parents[max(a,b)]=min(a,b)
    overlaps=[]; exact={}; include_owners={}; unresolved=[]
    for p,row in pages.items():
        raw=contents[row['doc_id']]; text=raw.decode('utf-8')
        # Body duplicates, including template bodies; no score-dependent threshold.
        body=text[_frontmatter(text):].replace('\r\n','\n').strip()
        key=rawhash(body.encode())
        if body and key in exact:
            other=exact[key]; union(family[row['doc_id']],family[other])
            overlaps.append({'kind':'exact-body','source':row['doc_id'],'other':other,'body_sha256':key})
        elif body: exact[key]=row['doc_id']
        # Literal include syntax only; dynamic/template expansions remain unknown.
        for m in re.finditer(r'\{\{[<%]\s*(include|partial)\b([^\n]*?)[>%]\}\}',text):
            quoted=re.findall(r'["\']([^"\']+)["\']',m[2])
            if not quoted:
                unresolved.append({'source':row['doc_id'],'syntax':m[0],'reason':'dynamic-or-unquoted-include'})
            for ref in quoted:
                include_owners.setdefault(ref,set()).add(row['doc_id'])
                matches=[q for q in pages if q==ref or q.endswith('/'+ref)]
                if len(matches)==1:
                    other=pages[matches[0]]['doc_id']; include_owners[ref].add(other)
                else: unresolved.append({'source':row['doc_id'],'syntax':m[0],'reason':'unresolved-literal-include','reference':ref})
    # Reuse the established source-only normalized-body/shingle grouping rather
    # than declaring template-like duplicates independent across source families.
    from .page_role_annotation import leakage_groups
    duplicate_audit=leakage_groups([{'doc_id':v,'source_family':family[v]} for v in sorted(family)],
        {v:raw.decode('utf-8') for v,raw in contents.items()},
        threshold=config.get('duplicate_threshold',.8),shingle_words=5,minimum_tokens=30)
    grouped={}
    for v,g in duplicate_audit['groups'].items(): grouped.setdefault(g,[]).append(v)
    for members in grouped.values():
        for v in members[1:]: union(family[members[0]],family[v])
    for ref,owners in sorted(include_owners.items()):
        ordered=sorted(owners)
        for v in ordered[1:]: union(family[ordered[0]],family[v])
        overlaps.append({'kind':'shared-literal-include','reference':ref,'owners':ordered})
    groups={}
    for v,f in family.items(): groups.setdefault(root(f),[]).append(v)
    all_regions=[]
    global_components=summary(sorted(family),edges)['weak_components']
    global_membership={v:stable('baseline-component',c['nodes']) for c in global_components for v in c['nodes']}
    for group,nodes in sorted(groups.items()):
        nodes=sorted(nodes); ns=set(nodes); internal=[e for e in edges if e[0] in ns and e[1] in ns]
        local=summary(nodes,internal)
        components=local['weak_components']; membership={v:stable('component',c['nodes']) for c in components for v in c['nodes']}
        es=set(map(tuple,exclusion_edges if exclusion_edges is not None else internal))
        opportunity=sum(1 for c in components for a in c['nodes'] for b in c['nodes']
            if a!=b and (a,b) not in es and (b,a) not in es)
        rid=stable('region',nodes)
        all_regions.append({'region_id':rid,'source_families':sorted({family[v] for v in nodes}),
            'corpus_id':config.get('corpus_id'),'snapshot_id':config.get('snapshot_id'),
            'nodes':nodes,'core_nodes':nodes,'halo_nodes':[],'halo_edges':[],
            'omitted_external_edges':[e for e in edges if (e[0] in ns)!=(e[1] in ns)],
            'component_membership':membership,'global_baseline_membership':{v:global_membership[v] for v in nodes},
            'aggregation_weight':1,'candidate_ordered_pairs':opportunity,'summary':local,
            'assignment':'unselected','order_key':digest([config['region_seed'],rid])})
    ordered=sorted((r for r in all_regions if r['candidate_ordered_pairs']>0),key=lambda r:r['order_key'])
    selected=ordered[:min(config['max_cores'],8)]
    # First four development, next four reserved; no outcome-based resampling.
    for i,r in enumerate(selected): r['assignment']='development' if i<4 else 'reserved-evaluation'
    return {'seed':config['region_seed'],'source_family_rule':f'first {depth} parent path components',
        'grouping':'exact normalized body duplicates and literal shared includes; no halo',
        'overlap_audit':overlaps,'near_duplicate_audit':duplicate_audit,'unresolved_includes':unresolved,
        'dependency_limit':'Dynamic includes/partials cannot be resolved; these partitions are not independent samples.',
        'regions':all_regions,'selected_region_ids':[r['region_id'] for r in selected],
        'shortage':max(0,8-len(selected)),'shared_halo_nodes':0,'shared_halo_edges':0,
        'all_source_nodes_accounted':len(family),'evaluation_outcomes_inspected':False}
