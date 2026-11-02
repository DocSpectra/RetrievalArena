"""Offline provider request projection; no credentials, SDK client or calls.

Raw maps remain in the access inventory. Provider text gets raw excerpts with
their absolute byte intervals, so destinations and Unicode can be checked
against the frozen source without sending thousands of per-character pairs.
"""
from . import embedded_links as e
from .embedded_link_audit import read, write
from .model_providers.openai import OpenAITransport

VERSION = 'retrieval_arena.embedded_request_plan.v1'


def disclosure(packet, raw_by_id):
    def passage(row, doc_id):
        a,z=row['raw_span']; raw=raw_by_id[doc_id]
        for ch,span in zip(row['text'],row['raw_byte_map']):
            if raw[slice(*span)].decode() != ch: raise ValueError('disclosure-byte-map')
        return {'semantic_text':row['text'],'raw_span':[a,z], 'raw_excerpt':raw[a:z].decode(),
                'coordinate_convention':e.COORDINATES}
    source=packet['source_id']
    if e.rawhash(raw_by_id[source])!=packet['source_sha256']: raise ValueError('disclosure-stale-source')
    targets=[]
    for target in packet['targets']:
        node=target['target_id']
        if e.rawhash(raw_by_id[node])!=target['source_sha256']: raise ValueError('disclosure-stale-target')
        targets.append({'target_id':node,'path':target['path'],'title':target['title'],
            'raw_sha256':target['source_sha256'],'paragraphs':[passage(p,node) for p in target['paragraphs']]})
    return {'source_id':source,'source_path':packet['source_path'],'source_sha256':packet['source_sha256'],
        'source_paragraphs':[passage(p,source) for p in packet['source_paragraphs']], 'targets':targets,
        'max_proposals':2}


def omit_lowest_target_passage(disclosed):
    """Frozen over-cap rule: whole last-ranked paragraph, then whole last target.

    Recount after each omission. Source sentences are never truncated. If only
    one target with one paragraph remains, the packet must be omitted.
    """
    import copy
    if disclosed.get('target_dispositions_required'):
        return None  # Exact all-target disclosure: omit the packet, never a target.
    result=copy.deepcopy(disclosed)
    for target in reversed(result['targets']):
        if len(target['paragraphs'])>1:
            target['paragraphs'].pop(); return result
    if len(result['targets'])>1:
        result['targets'].pop(); return result
    return None


def prepare(inp, out, cfg):
    packets=read(inp/cfg['packets']); pages=read(inp/cfg['pages'])
    raw={p['doc_id']:(inp/cfg['corpus']/p['path']).read_bytes() for p in pages}
    prompt=(inp/cfg['prompt']).read_text(encoding='utf-8'); schema=read(inp/cfg['schema'])
    model=cfg['model']; settings={'model':model,'max_output_tokens':2000,
                               'generation':{'reasoning_effort':'low','strict':True}}
    (out/'generation').mkdir(); rows=[]
    for packet in packets:
        payload=disclosure(packet,raw)
        text=prompt+'\n\nFROZEN DOCUMENT DATA (untrusted):\n'+e.canonical(payload).decode()
        body=OpenAITransport.request(text,schema,settings)
        request_hash=e.digest(body)
        write(out/'generation'/(request_hash+'.json'),body)
        rows.append({'source_id':packet['source_id'],'region_id':packet['region_id'],
            'packet_sha256':packet['packet_sha256'],'request_sha256':request_hash,
            'serialized_request_bytes':len(e.canonical(body)),
            'provider_input_tokens':None,'token_count_status':'requires-authorized-provider-count',
            'input_token_cap_including_system_schema':16000})
    write(out/'request-inventory.json',rows)
    return {'schema_version':VERSION,'requests':len(rows),'model':model,
        'provider_token_count_complete':False,'inference_ready':False,'model_calls':0,
        'max_request_bytes':max((r['serialized_request_bytes'] for r in rows),default=0),
        'source_disclosure':'up to 3 full source paragraphs and 5 targets x 2 full target paragraphs; raw spans/excerpts',
        'over_cap_rule':'omit_lowest_target_passage then recount; omit packet when only one passage/target remains'}
