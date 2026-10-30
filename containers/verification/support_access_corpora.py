"""Immutable evaluator for separately generated corpus-aware predictions."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,'/workspace/source')
from retrieval_arena.candidate_retrieval import canonical,regular_inventory,sha256_bytes,sha256_file
from retrieval_arena.graph import support_access as sa
from retrieval_arena.graph import support_access_corpora as sc
from containers.verification.support_access import _excerpt

def read(p): return json.loads(p.read_bytes())
def rows(p):
    with p.open(encoding='utf-8') as f:
        return [json.loads(l) for l in f if l.strip()]
def write(p,v): p.write_bytes(canonical(v)+b'\n')
def jsonl(p,vs):
    with p.open('wb') as f:
        for v in vs: f.write(canonical(v)+b'\n')
def unique(vs,key):
    out={}
    for v in vs:
        k=key(v)
        if k in out: raise ValueError('corpus-duplicate-row:'+str(k))
        out[k]=v
    return out

def run(inp,out):
    started=time.monotonic()
    cfg=read(inp/'config.json'); request=read(inp/'operator-request.json')
    if cfg['schema_version']!='retrieval_arena.support_access_evaluation.v2': raise ValueError('corpus-config')
    inv=[r for r in regular_inventory(inp) if r['path']!='operator-request.json']
    if request!={'schema_version':'retrieval_arena.support_access_operator_request.v2',
        'command':'compare-support-access-corpora','input_inventory':inv,'input_inventory_sha256':sha256_bytes(canonical(inv))}:
        raise ValueError('corpus-input-inventory')
    required={'config.json','populations.json','targets.jsonl','questions.jsonl','predictions.jsonl','scores.jsonl','graphs.json','parity.json','historical_rows.jsonl'}
    required|={p.relative_to(inp).as_posix() for p in (inp/'corpora').glob('*/corpus/*.md')}
    if {r['path'] for r in inv}!=required: raise ValueError('corpus-undeclared-input')
    if read(inp/'parity.json')['status']!='qualified': raise ValueError('corpus-parity-block')
    if not 0<cfg['max_output_bytes']<=4*1024**3 or not 0<cfg['max_runtime_seconds']<=7200: raise ValueError('corpus-budget')
    def budget():
        if time.monotonic()-started>cfg['max_runtime_seconds']: raise ValueError('corpus-runtime-budget')
        if sum(r['size_bytes'] for r in regular_inventory(out))>cfg['max_output_bytes']: raise ValueError('corpus-output-budget')
    qs=rows(inp/'questions.jsonl'); qids={q['query_id'] for q in qs}
    if len(qs)!=len(qids): raise ValueError('corpus-duplicate-query')
    graphs=read(inp/'graphs.json')
    pred=unique(rows(inp/'predictions.jsonl'),lambda p:(p['corpus_id'],p['query_id']))
    scores=unique(rows(inp/'scores.jsonl'),lambda p:(p['corpus_id'],p['query_id']))
    expected={(c,q) for c in graphs for q in qids}
    if set(pred)!=expected or set(scores)!=expected: raise ValueError('corpus-prediction-join')
    for c,g in graphs.items():
        if not c.isalnum(): raise ValueError('corpus-id')
        nodes=g['nodes']; sa._adjacency(nodes,g['edges'])
        paths=list((inp/'corpora'/c/'corpus').glob('*.md'))
        if sorted(p.stem for p in paths)!=sorted(nodes): raise ValueError('corpus-full-pages')
        for q in qs:
            p=pred[c,q['query_id']]
            if p['query_text_group']!=q['question']: raise ValueError('corpus-question-identity')
            rebuilt=sc.generate(nodes,g['edges'],scores[c,q['query_id']]['scores'],cfg['traversal'])
            if any(rebuilt[k]!=p[k] for k in rebuilt): raise ValueError('corpus-generated-consistency')
            budget()
    frozen_hash=sha256_file(inp/'predictions.jsonl')
    targets=unique(rows(inp/'targets.jsonl'),lambda t:(t['surface_id'],t['query_id']))
    if set(targets)!={(s,q) for s in cfg['surfaces'] for q in qids}: raise ValueError('corpus-target-join')
    evaluated=[]
    for s,c in cfg['surfaces'].items():
        if c not in graphs: raise ValueError('corpus-surface-join')
        g=graphs[c]
        for q in qs:
            qid=q['query_id']; t=targets[s,qid]
            if set(t)!={'surface_id','corpus_id','query_id','known_target_ids','credit_kind'} or t['corpus_id']!=c:
                raise ValueError('corpus-target-schema')
            row=sc.evaluate(g['nodes'],g['edges'],pred[c,qid],t['known_target_ids'],cfg['traversal'],cfg['semantic_depths'],credit_kind=t['credit_kind'])
            evaluated.append({'surface_id':s,'corpus_id':c,'query_id':qid,'query_text_group':q['question'],**row})
            budget()
    if sha256_file(inp/'predictions.jsonl')!=frozen_hash: raise ValueError('corpus-predictions-changed')
    pops=read(inp/'populations.json'); pops['primary_surfaces']=cfg['primary_surfaces']
    summary=sc.analyze(evaluated,qs,pops,cfg['pairs'],draws=cfg['bootstrap_draws'],seed=cfg['bootstrap_seed'])
    idx={(r['surface_id'],r['query_id']):r for r in evaluated}
    historical=unique(rows(inp/'historical_rows.jsonl'),lambda r:r['query_id'])
    if set(historical)!=qids: raise ValueError('corpus-historical-join')
    historical_fields=['hits','cell','G4_minus_B4','selected_counts','known_target_ids_in_view','failure','known_support_selected','best_known_positive_semantic_rank']
    for q in qids:
        if any(historical[q][k]!=idx['O_C2',q][k] for k in historical_fields): raise ValueError('corpus-historical-reproduction:'+q)
    historical_intervals=sa.paired_intervals([dict(r,job_id='O_C2') for r in evaluated if r['surface_id']=='O_C2'],job_ids=['O_C2'],draws=2000,seed=30061029)
    summary['historical_reproduction']={'queries':len(qids),'fields':historical_fields,'old_C2_positive_pairs':sum(len(idx['O_C2',q]['known_target_ids_in_view']) for q in qids),'intervals':historical_intervals}
    jsonl(out/'per_query_comparison.jsonl',evaluated)
    write(out/'support_access_summary.json',summary)
    cards=[]
    for category,ids in sc.example_ids(summary['transitions']['LT-O'],idx,a='LT',b='O').items():
        for q in ids:
            card={'category':category,'query_id':q,'question':idx['O',q]['query_text_group'],'arms':{},
                'sufficiency_and_necessary_composition':'unknown; illustrative executor excerpts do not supply gold labels'}
            for s in cfg['primary_surfaces']:
                c=cfg['surfaces'][s]; p=pred[c,q]; row=idx[s,q]
                docs=sorted(set(row['known_target_ids_in_view'])|set(p['B4'])|set(p['G4']['candidate_ids']))
                pages={}
                for d in docs:
                    path=inp/'corpora'/c/'corpus'/(d+'.md'); opath=inp/'corpora'/cfg['surfaces'][cfg['primary_surfaces'][0]]/'corpus'/(d+'.md')
                    h=sha256_file(path)
                    pages[d]={'sha256':h,'excerpt':_excerpt(path,card['question']),
                        'relative_to_original':'new_id' if not opath.exists() else 'unchanged_bytes' if sha256_file(opath)==h else 'changed_bytes_same_id',
                        'move_or_derived_mapping':'not inferred from ID; caller-owned saved provenance if available'}
                card['arms'][s]={'prediction':p,'evaluation':row,'pages':pages}
            cards.append(card)
    write(out/'example_cards.json',cards)
    budget()
    manifest={'schema_version':'retrieval_arena.support_access_corpora_manifest.v2','input_inventory_sha256':request['input_inventory_sha256'],
        'prediction_sha256':frozen_hash,'outputs':regular_inventory(out),'runtime_seconds':time.monotonic()-started,
        'code_sha256':{'core':sha256_file(Path(sc.__file__)),'operator':sha256_file(Path(__file__))},
        'runtime':{k:os.environ.get(v) for k,v in {'source_commit':'RA_SOURCE_COMMIT','source_tree':'RA_SOURCE_TREE','image':'RA_IMAGE_REFERENCE','lock_sha256':'RA_DEPENDENCY_LOCK_SHA256'}.items()},
        'counts':{'evaluated_rows':len(evaluated),'prediction_rows':len(pred),'cards':len(cards)}}
    write(out/'support_access_manifest.json',manifest)
    receipt={'schema_version':'retrieval_arena.support_access_corpora_receipt.v2','status':'qualified','manifest_sha256':sha256_file(out/'support_access_manifest.json'),'receipt_sha256':'0'*64}
    receipt['receipt_sha256']=sha256_bytes(canonical(receipt)); write(out/'operator_receipt.v2.json',receipt)
    return summary

def main():
    inp=Path('/workspace/input'); out=Path('/workspace/output')
    if {p.name for p in out.iterdir()}!={'.retrieval-arena-output-owner'}: raise ValueError('corpus-output-not-fresh')
    run(inp,out)
    return 0
