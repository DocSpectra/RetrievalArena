"""Source-bound model interface; exact, fail-closed projection to the v2 editor.

No model coordinates, fuzzy matching, case folding or punctuation repair. The
existing CommonMark character map is the only display/raw projection. Repeated
text within a cited sentence/passage is refused. IDs bind document identity as
well as bytes: same text in another document cannot satisfy an evidence citation.
"""
import copy
import re

from . import embedded_links as e
from .embedded_request_plan import disclosure as legacy_disclosure

VERSION = 'retrieval_arena.embedded_interface.v1'
POLICY = 'commonmark_exact_map_unique_v1'


def proposal_schema():
    fields = {k: copy.deepcopy(e.PROPOSAL_SCHEMA['properties'][k]) for k in
              ('source_id', 'target_id', 'anchor', 'relation', 'stated_intent',
               'expected_reading_use', 'basis')}
    fields.update(source_sentence_id=e.S,
        confidence={'anyOf':[{'type':'number','minimum':0,'maximum':1},{'type':'null'}]},
        target_evidence={'type':'array','items':citation_schema(),'minItems':1,'maxItems':3})
    return e._obj({'proposals':{'type':'array','items':e._obj(fields),'maxItems':2}})


def citation_schema():
    return e._obj({'passage_id':e.S,'quote':e.S})


def review_schema():
    return e._obj({'relation':{'enum':list(e.RELATIONS)},
        **{k:{'type':'integer','minimum':0,'maximum':2} for k in
           ('relationship_support','target_support','placement')},
        'uncertainty':{'type':'boolean'},'contradiction':{'type':'boolean'},
        **{k:{'type':'array','items':citation_schema(),'minItems':1,'maxItems':3}
           for k in ('source_evidence','target_evidence')},'assessment':e.S})


def passage(row, doc_id, raw, role):
    """Keep the already disclosed paragraph and its tested original byte map."""
    a,z=row['raw_span']; text=row['text']; mapping=row.get('raw_byte_map')
    if mapping is None:
        # Exact literal paragraphs (e.g. consumer-authored technical fixtures).
        if raw[a:z].decode()!=text: raise ValueError('interface-map-required')
        mapping=[]; at=a
        for ch in text:
            mapping.append([at,at+len(ch.encode())]); at+=len(ch.encode())
    if len(mapping)!=len(text) or not text: raise ValueError('interface-map-length')
    for ch,(lo,hi) in zip(text,mapping):
        if not a<=lo<hi<=z or raw[lo:hi].decode()!=ch: raise ValueError('interface-map-byte')
    if any(x[1]>y[0] for x,y in zip(mapping,mapping[1:])):raise ValueError('interface-map-order')
    identity=[VERSION,POLICY,role,doc_id,e.rawhash(raw),[a,z],text,mapping]
    result={'passage_id':e.stable('passage',*identity),'doc_id':doc_id,
        'source_sha256':e.rawhash(raw),'role':role,'text':text,'raw_span':[a,z],
        'raw_excerpt':raw[a:z].decode(),'raw_byte_map':mapping}
    if role=='source':
        result['sentences']=[]
        for block in e.blocks(raw)[0]:
            if not block['map']:continue
            offsets=[0]
            for ch in raw.decode():offsets.append(offsets[-1]+len(ch.encode()))
            content=block['content'];seen=set()
            for word in re.finditer(r'\S+',content):
                lo,hi=e.sentence_span(content,*word.span())
                if (lo,hi) in seen:continue
                seen.add((lo,hi)); chars=block['map'][lo:hi]
                if not chars or any(c is None for c in chars):continue
                smap=[[offsets[c],offsets[c+1]] for c in chars]
                if not all(a<=x<y<=z for x,y in smap):continue
                sentence=content[lo:hi]
                # This is the editor's unchanged host sentence, with its source
                # syntax. All bytes lie inside the original disclosed paragraph.
                result['sentences'].append({'sentence_id':e.stable('sentence',*identity,lo,hi,sentence),
                    'text':sentence,'raw_byte_map':smap,'raw_span':[smap[0][0],smap[-1][1]]})
    return result


def disclosure(packet, raw_by_id):
    # Validate historical raw/display map and source hashes before adding IDs.
    legacy_disclosure(packet,raw_by_id) if all('raw_byte_map' in p for p in packet['source_paragraphs']) else None
    source=packet['source_id']
    if e.rawhash(raw_by_id[source])!=packet['source_sha256']:raise ValueError('interface-stale-source')
    sources=[passage(p,source,raw_by_id[source],'source') for p in packet['source_paragraphs']]
    targets=[]
    for t in packet['targets']:
        node=t['target_id']; raw=raw_by_id[node]
        if 'source_sha256' in t and e.rawhash(raw)!=t['source_sha256']:raise ValueError('interface-stale-target')
        targets.append({k:t[k] for k in ('target_id','path','title') if k in t} |
            {'raw_sha256':e.rawhash(raw),'paragraphs':[passage(p,node,raw,'target') for p in t['paragraphs']]})
    return {'interface_version':VERSION,'resolution_policy':POLICY,'source_id':source,
        'source_sha256':e.rawhash(raw_by_id[source]),'source_paragraphs':sources,'targets':targets,'max_proposals':2}


def public_disclosure(data):
    """Byte arithmetic and maps stay host-side; exact text and IDs go to models."""
    def public(p):
        result={k:p[k] for k in ('passage_id','doc_id','source_sha256','role','text')}
        if 'sentences' in p:result['sentences']=[{k:s[k] for k in ('sentence_id','text')} for s in p['sentences']]
        return result
    return {k:v for k,v in data.items() if k not in ('source_paragraphs','targets')} | {
        'source_paragraphs':[public(p) for p in data['source_paragraphs']],
        'targets':[{k:v for k,v in t.items() if k!='paragraphs'} | {'paragraphs':[public(p) for p in t['paragraphs']]} for t in data['targets']]}


def exact_matches(text, quote):
    if not quote:raise ValueError('empty-exact-quote')
    return [m.start() for m in re.finditer('(?='+re.escape(quote)+')',text)]


def resolve_quote(quote, p, raw, *, contiguous=False):
    positions=exact_matches(p['text'],quote)
    if not positions:raise ValueError('quote-absent-from-cited-disclosure')
    if len(positions)!=1:raise ValueError('ambiguous-repeated-quote')
    at=positions[0]; mapping=p['raw_byte_map'][at:at+len(quote)]
    a,z=mapping[0][0],mapping[-1][1]
    if e.rawhash(raw)!=p['source_sha256']:raise ValueError('interface-stale-evidence')
    if any(raw[x:y].decode()!=ch for ch,(x,y) in zip(quote,mapping)):raise ValueError('interface-projection-byte')
    raw_quote=raw[a:z].decode()
    if contiguous and raw_quote!=quote:raise ValueError('anchor-not-contiguous-raw-text')
    return {'span':[a,z],'quote':raw_quote}, {'returned_quote':quote,'raw_quote':raw_quote,
        'span':[a,z],'raw_byte_map':mapping,'policy':POLICY,'passage_id':p['passage_id'],
        'doc_id':p['doc_id'],'source_sha256':p['source_sha256']}


def citations(items, passages, raw_by_id, role):
    by_id={p['passage_id']:p for p in passages}
    resolved=[];proof=[]
    for item in items:
        p=by_id.get(item['passage_id'])
        if p is None or p['role']!=role:raise ValueError('missing-stale-or-cross-document-evidence-id')
        value,witness=resolve_quote(item['quote'],p,raw_by_id[p['doc_id']])
        resolved.append(value);proof.append(witness)
    return resolved,proof


def resolve_proposal(item, data, raw_by_id):
    if item['source_id']!=data['source_id']:raise ValueError('source-id-outside-disclosure')
    target=next((t for t in data['targets'] if t['target_id']==item['target_id']),None)
    if target is None:raise ValueError('target-id-outside-disclosure')
    sentences=[(s,p) for p in data['source_paragraphs'] for s in p['sentences']
               if s['sentence_id']==item['source_sentence_id']]
    if len(sentences)!=1:raise ValueError('missing-stale-source-sentence-id')
    sentence,p=sentences[0]
    bound={**sentence,**{k:p[k] for k in ('passage_id','doc_id','source_sha256')}}
    anchor,anchor_proof=resolve_quote(item['anchor'],bound,raw_by_id[item['source_id']],contiguous=True)
    evidence,proof=citations(item['target_evidence'],target['paragraphs'],raw_by_id,'target')
    result={k:v for k,v in item.items() if k!='source_sentence_id'}
    result.update(source_span=anchor['span'],sentence=sentence['text'],target_evidence=evidence)
    return result,{'schema_version':VERSION+'.resolution','policy':POLICY,'declaration':copy.deepcopy(item),
        'source_sentence_id':item['source_sentence_id'],'anchor':anchor_proof,'target_evidence':proof,
        'resolved_sha256':e.digest(result)}


def review_disclosure(proposal, packet, raw_by_id):
    data=disclosure(packet,raw_by_id)
    a,z=proposal['source_span']
    sources=[p for p in data['source_paragraphs'] if p['raw_span'][0]<=a<z<=p['raw_span'][1]]
    if len(sources)!=1:raise ValueError('source-anchor-outside-packet')
    target=next(t for t in data['targets'] if t['target_id']==proposal['target_id'])
    evidence={'source':sources,'target':target['paragraphs'],'interface_version':VERSION}
    public=public_disclosure(dict(data,source_paragraphs=sources,targets=[target]))
    # Independent reviewer and final assessor see the proposed occurrence and
    # exact same evidence. No rationale, prior score, verdict or arm is exposed.
    public.update(target_id=proposal['target_id'],anchor=proposal['anchor'],source_sentence=proposal['sentence'])
    return public,evidence


def resolve_review(payload, evidence, raw_by_id=None):
    if raw_by_id is None:
        # Validation against only the disclosed packet, never a full-file search.
        # Constructing padded bytes preserves absolute spans and source hashes
        # would be unsound; instead validate the exact maps against local excerpt.
        resolved=copy.deepcopy(payload);proof=[]
        for role in ('source','target'):
            by_id={p['passage_id']:p for p in evidence[role]}
            rows=[]
            for item in payload[role+'_evidence']:
                p=by_id.get(item['passage_id'])
                if p is None or p['role']!=role:raise ValueError('missing-stale-or-cross-document-evidence-id')
                matches=exact_matches(p['text'],item['quote'])
                if len(matches)!=1:raise ValueError('ambiguous-repeated-quote' if matches else 'quote-absent-from-cited-disclosure')
                at=matches[0];mapping=p['raw_byte_map'][at:at+len(item['quote'])]
                a,z=mapping[0][0],mapping[-1][1];base=p['raw_span'][0];raw=p['raw_excerpt'].encode()
                if any(raw[x-base:y-base].decode()!=ch for ch,(x,y) in zip(item['quote'],mapping)):
                    raise ValueError('interface-projection-byte')
                quote=raw[a-base:z-base].decode();rows.append({'span':[a,z],'quote':quote})
                proof.append({'role':role,'passage_id':p['passage_id'],'doc_id':p['doc_id'],
                    'source_sha256':p['source_sha256'],'returned_quote':item['quote'],'raw_quote':quote,'span':[a,z],'policy':POLICY})
            resolved[role+'_evidence']=rows
        return resolved,proof
    result=copy.deepcopy(payload);proof=[]
    for role in ('source','target'):
        result[role+'_evidence'],w=citations(payload[role+'_evidence'],evidence[role],raw_by_id,role);proof.extend(w)
    return result,proof


def event(log, typ, payload, authority):
    allowed={'model_declared':'model','host_resolved':'host','host_rejected':'host',
             'reviewer_declared':'reviewer','review_resolved':'host'}
    if allowed.get(typ)!=authority:raise ValueError('interface-event-authority')
    row={'schema_version':VERSION+'.event','type':typ,'authority':authority,'payload':payload,
         'previous_sha256':e.digest(log[-1]) if log else '0'*64}
    row['event_id']=e.digest(row);log.append(row);return row


def replay(log, tip):
    rebuilt=[]
    for row in log:
        expected=event(rebuilt,row['type'],row['payload'],row['authority'])
        if row!=expected:raise ValueError('interface-ledger-chain')
    actual=e.digest(log[-1]) if log else '0'*64
    if actual!=tip:raise ValueError('untrusted-interface-ledger-tip')
    return {'events':len(log),'tip_sha256':actual}
