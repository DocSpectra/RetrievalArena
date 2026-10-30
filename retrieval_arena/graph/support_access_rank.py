"""Offline complete-score generation with a locked historical local encoder.

Run in the provisioned embedding image. Only label-free generation inputs and
the model/lock are mounted. Every copied source byte is pinned in the image.
"""
from __future__ import annotations
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

from retrieval_arena.candidate_retrieval import canonical,regular_inventory,sha256_bytes,sha256_file
from retrieval_arena.graph import support_access_corpora as sc

def rows(p):
    with p.open(encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]
def write(p,v): p.write_bytes(canonical(v)+b'\n')
def jsonl(p,vs):
    with p.open('wb') as f:
        for v in vs: f.write(canonical(v)+b'\n')

def cache_key(text,policy):
    return sha256_bytes(canonical({'text_sha256':sha256_bytes(text.encode('utf-8')),'policy':policy}))

def run(inp,out,modeldir,lockpath,runnerpath):
    started=time.monotonic()
    if list(out.iterdir()): raise ValueError('ranking-output-not-fresh')
    cfg=json.loads((inp/'config.json').read_bytes())
    if cfg['schema_version']!='retrieval_arena.support_access_generation.v2': raise ValueError('ranking-config')
    if cfg['queries_path']!='questions.jsonl': raise ValueError('ranking-query-path')
    names=cfg['corpora']
    if len(names)!=len(set(names)): raise ValueError('ranking-corpus-duplicate')
    required={'config.json','questions.jsonl','parity_queries.jsonl','parity_predictions.jsonl'}
    for name in names:
        if not name.isalnum(): raise ValueError('ranking-corpus-id')
        required.add(name+'/graph.json')
        required|={p.relative_to(inp).as_posix() for p in (inp/name/'corpus').glob('*.md')}
    reference=cfg.get('encoding_reference')
    if reference:
        if reference!={'corpus_id':names[0],'queries_path':'encoding-reference/questions.jsonl','corpus_path':'encoding-reference/corpus'}:
            raise ValueError('ranking-encoding-reference')
        required.add(reference['queries_path'])
        required|={p.relative_to(inp).as_posix() for p in (inp/reference['corpus_path']).glob('*.md')}
    inv=regular_inventory(inp)
    if {r['path'] for r in inv}!=required: raise ValueError('ranking-undeclared-input-or-label')
    qs=rows(inp/cfg['queries_path'])
    if any(set(q)!={'query_id','question'} for q in qs) or len(qs)!=len({q['query_id'] for q in qs}):
        raise ValueError('ranking-query-schema')
    spec=importlib.util.spec_from_file_location('locked_encoder',runnerpath)
    runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
    locked=runner.validate_model_lock(lockpath,modeldir,expected_model_id=cfg['model_id'])
    if locked['revision']!=cfg['revision']: raise ValueError('ranking-model-revision')
    if {r['path'] for r in regular_inventory(modeldir)}!={r['path'] for r in locked['files']}:
        raise ValueError('ranking-model-extra-files')
    runner.set_offline_environment()
    from sentence_transformers import SentenceTransformer
    model=SentenceTransformer(str(modeldir))
    policy={'encoding_policy':cfg['encoding_policy'],'model_lock_sha256':sha256_file(lockpath),
        'model_id':cfg['model_id'],'revision':cfg['revision'],'batch_size':cfg['batch_size'],
        'decoding':'UTF-8 strict, Python universal newlines; complete Markdown unchanged',
        'max_seq_length':model.max_seq_length,'tokenizer_class':type(model.tokenizer).__name__,
        'normalize_embeddings':True,'cosine':'sum(a*b for a,b in zip(float64 Python lists)); normalized float32 model outputs',
        'runtime':{n:importlib.metadata.version(n) for n in ('torch','sentence-transformers','transformers','tokenizers','numpy')},
        'runner_sha256':sha256_file(runnerpath), 'model_modules':str(model)}
    # This complete identity keys every text. IDs alone are never cache keys.
    cache={}; events=[]
    def encode(texts,stage):
        # Padding/batch composition can change float32 output slightly. Preserve
        # every historical occurrence and order; content deduplication is not a
        # numerically compatible encoding optimization. Keys include batch input.
        seqhash=sha256_bytes(canonical(texts))
        keys=[cache_key(t,dict(policy,encoding_sequence_sha256=seqhash,position=i)) for i,t in enumerate(texts)]
        vectors=model.encode(texts,batch_size=cfg['batch_size'],normalize_embeddings=True,show_progress_bar=False)
        result=[]
        for k,v in zip(keys,vectors):
            vector=list(map(float,v))
            if len(vector)!=384 or not all(map(math.isfinite,vector)): raise ValueError('ranking-vector')
            cache[k]=vector; result.append(vector)
        events.append({'stage':stage,'input_texts':len(texts),'new_distinct_texts':len(set(texts)),
            'encoded_text_occurrences':len(texts),'reused_text_occurrences':0,'encode_invocations':1,
            'encoding_sequence_sha256':seqhash,'batches':math.ceil(len(texts)/cfg['batch_size'])})
        return result, keys
    encodeqs=rows(inp/reference['queries_path']) if reference else qs
    query_ids=[q.get('query_id',q.get('question_id')) for q in encodeqs]
    if len(query_ids)!=len(set(query_ids)): raise ValueError('ranking-reference-query-duplicate')
    qv_all,qkeys_all=encode([q['question'] for q in encodeqs],'queries')
    qindex={qid:(q['question'],v,k) for qid,q,v,k in zip(query_ids,encodeqs,qv_all,qkeys_all)}
    if any(q['query_id'] not in qindex or q['question']!=qindex[q['query_id']][0] for q in qs): raise ValueError('ranking-reference-query-text')
    qv=[qindex[q['query_id']][1] for q in qs]; qkeys=[qindex[q['query_id']][2] for q in qs]
    predictions=[]; allscores={}; graphs={}; docidentities={}
    for name in names:
        graph=json.loads((inp/name/'graph.json').read_bytes()); graphs[name]=graph
        paths=sorted((inp/name/'corpus').glob('*.md'),key=lambda p:p.stem)
        ids=[p.stem for p in paths]
        if ids!=sorted(graph['nodes']) or len(ids)!=len(set(ids)): raise ValueError('ranking-document-universe')
        encodepaths=sorted((inp/reference['corpus_path']).glob('*.md'),key=lambda p:p.stem) if reference and name==reference['corpus_id'] else paths
        dv_all,dkeys_all=encode([p.read_text(encoding='utf-8') for p in encodepaths],name)
        dindex={p.stem:(p.read_text(encoding='utf-8'),v,k) for p,v,k in zip(encodepaths,dv_all,dkeys_all)}
        if any(p.stem not in dindex or p.read_text(encoding='utf-8')!=dindex[p.stem][0] for p in paths): raise ValueError('ranking-reference-document-text')
        dv=[dindex[p.stem][1] for p in paths]; dkeys=[dindex[p.stem][2] for p in paths]
        docidentities[name]=[{'doc_id':p.stem,'raw_sha256':sha256_file(p),'cache_key':k} for p,k in zip(paths,dkeys)]
        scores={}
        for q,v in zip(qs,qv):
            pairs=[[d,runner.cosine(v,w)] for d,w in zip(ids,dv)]
            pred=sc.generate(ids,graph['edges'],pairs,cfg['traversal'])
            scores[q['query_id']]=pairs
            predictions.append({'corpus_id':name,'query_id':q['query_id'],'query_text_group':q['question'],**pred})
        allscores[name]=scores
        if time.monotonic()-started>cfg['max_runtime_seconds']: raise ValueError('ranking-runtime-budget')
    oldqs={q['query_id']:q['scores'].items() for q in rows(inp/'parity_queries.jsonl')}
    oldpred={q['query_id']:q for q in rows(inp/'parity_predictions.jsonl')}
    newpred={p['query_id']:p for p in predictions if p['corpus_id']==names[0]}
    result=sc.parity(graphs[names[0]]['nodes'],newpred,oldpred,allscores[names[0]],oldqs,
        atol=cfg['parity_absolute_tolerance'],rtol=cfg['parity_relative_tolerance'])
    write(out/'parity.json',result)
    jsonl(out/'predictions.jsonl',predictions)
    jsonl(out/'scores.jsonl',({'corpus_id':name,'query_id':qid,'scores':pairs} for name,vs in allscores.items() for qid,pairs in vs.items()))
    write(out/'graphs.json',graphs)
    write(out/'encoding_inventory.json',{'policy':policy,'documents':docidentities,'query_cache_keys':dict(zip([q['query_id'] for q in qs],qkeys)),
        'events':events,'encoded_vector_keys':len(cache),'distinct_encoded_texts':len({p.read_text(encoding='utf8') for name in names for p in (inp/name/'corpus').glob('*.md')} | {q['question'] for q in encodeqs} | ({p.read_text(encoding='utf8') for p in (inp/reference['corpus_path']).glob('*.md')} if reference else set())),
        'encoded_text_occurrences':sum(e['encoded_text_occurrences'] for e in events),'encode_invocations':sum(e['encode_invocations'] for e in events),
        'runtime_seconds':time.monotonic()-started,'paid_calls':0,'outbound_calls':0})
    write(out/'ranking_manifest.json',{'schema_version':'retrieval_arena.support_access_rank_manifest.v2',
        'source_commit':os.environ.get('RA_SOURCE_COMMIT'),'source_tree':os.environ.get('RA_SOURCE_TREE'),
        'input_inventory':inv,'input_inventory_sha256':sha256_bytes(canonical(inv)),
        'outputs':regular_inventory(out),'parity_status':result['status']})
    if sum(r['size_bytes'] for r in regular_inventory(out))>cfg['max_output_bytes']: raise ValueError('ranking-output-budget')
    if result['status']!='qualified': raise ValueError('ranking-parity-failed')
    print(json.dumps({'parity':result['status'],'encoded_vector_keys':len(cache),'events':events,'seconds':time.monotonic()-started}))

if __name__=='__main__':
    run(Path('/input'),Path('/output'),Path('/model'),Path('/provenance/embedding_models.lock.json'),Path('/app/runner.py'))
