"""Offline generation and evaluation with disjoint input allowlists."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']: os.environ[key]='1'
sys.path.insert(0,'/workspace/source')
from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_file
from retrieval_arena.graph import magnetic_spectrum as ms
from retrieval_arena.graph import page_representations as pr
from retrieval_arena.graph import complementary_context as cc

def read(p): return json.loads(p.read_bytes())
def rows(p): return [json.loads(l) for l in p.read_bytes().splitlines() if l.strip()]
def write(p,x): p.write_bytes(canonical(x)+b'\n')
def jsonl(p,x): p.write_bytes(b''.join(canonical(r)+b'\n' for r in x))
def unique(rs,fields):
    idx={tuple(r[f] for f in fields):r for r in rs}
    if len(idx)!=len(rs): raise ValueError('duplicate-rows')
    return idx

def run(inp,out,command):
    started=time.monotonic()
    if {p.name for p in out.iterdir()}!={'.retrieval-arena-output-owner'}: raise ValueError('output-not-fresh')
    inv=[r for r in regular_inventory(inp) if r['path']!='operator-request.json']; req=read(inp/'operator-request.json')
    if req!={'schema_version':'retrieval_arena.complementary_context_request.v1','command':command,
             'input_inventory':inv,'input_inventory_sha256':ms.digest(inv)}: raise ValueError('input-inventory')
    cfg=read(inp/'config.json')
    if cfg['schema_version']!='retrieval_arena.complementary_context_config.v1' or cfg['charges']!=[0.,.1,.25] or cfg['t']!=1.:
        raise ValueError('config-policy')
    if not 0<cfg['max_runtime_seconds']<=14400 or not 0<cfg['max_output_bytes']<=6*1024**3: raise ValueError('execution-budget')
    def budget():
        if time.monotonic()-started>cfg['max_runtime_seconds']: raise ValueError('runtime-budget')
        if sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>cfg['max_output_bytes']: raise ValueError('output-budget')
    generate=command=='generate-complementary-context'
    if command not in ('generate-complementary-context','evaluate-complementary-context'): raise ValueError('command')
    qs=rows(inp/'questions.jsonl'); graphs=read(inp/'graphs.json'); qids={q['query_id'] for q in qs}
    if len(qids)!=len(qs) or not qs: raise ValueError('query-universe')
    if not graphs or any(not c.isalnum() for c in graphs): raise ValueError('corpus-ids')
    corpus_files={p.relative_to(inp).as_posix() for p in (inp/'corpora').glob('*/corpus/*.md')}
    allowed={'config.json','graphs.json','questions.jsonl'}|corpus_files
    if generate:
        allowed|={'scores.jsonl','historical_predictions.jsonl'}
        allowed|={p.relative_to(inp).as_posix() for p in (inp/'bundles').glob('*/*/*') if p.name in ('kernel.npy','features.npy','manifest.json')}
    else: allowed|={'predictions.jsonl','parity.json','targets.jsonl','populations.json','judgments.jsonl','packet_sources.json'}
    if {r['path'] for r in inv}!=allowed: raise ValueError('undeclared-input')
    contents={}; lengths={}; identities={}
    for c,g in graphs.items():
        if g['nodes']!=sorted(set(g['nodes'])): raise ValueError('node-order')
        cc._adjacency(g['nodes'],g['edges'])
        files={p.stem:p for p in (inp/f'corpora/{c}/corpus').glob('*.md')}
        if set(files)!=set(g['nodes']): raise ValueError('corpus-node-join')
        contents[c]={d:p.read_text(encoding='utf-8') for d,p in files.items()}
        lengths[c]={d:len(t) for d,t in contents[c].items()}
        identities[c]={'content':ms.digest([(d,sha256_file(files[d])) for d in g['nodes']]),
                       'graph':ms.graph_sha256(g['nodes'],g['edges']),'nodes':ms.digest(g['nodes'])}
    if generate:
        scores=unique(rows(inp/'scores.jsonl'),['corpus_id','query_id'])
        old=unique(rows(inp/'historical_predictions.jsonl'),['corpus_id','query_id'])
        expected={(c,q) for c in graphs for q in qids}
        if set(scores)!=expected or set(old)!=expected: raise ValueError('cache-join')
        predictions=[]; features=[]; parity_checks=[]
        for c,g in graphs.items():
            kernels={}
            for q in cfg['charges']:
                path=inp/f'bundles/{c}/q{q:.2f}'
                new=not path.exists()
                if new:
                    path=out/f'bundles/{c}/q{q:.2f}'
                    kernel,receipt=pr.heat_kernel(g['nodes'],g['edges'],q,cfg['t'],cfg['tolerance'])
                    pr.save_bundle(path,g['nodes'],kernel,receipt,identities[c]['content'])
                manifest,kernel,_=pr.load_bundle(path,content_identity=identities[c]['content'],
                    graph_sha256=identities[c]['graph'],q=q,t=cfg['t'])
                if manifest['nodes']!=g['nodes']: raise ValueError('feature-node-order')
                kernels[q]=kernel
                features.append({'corpus_id':c,'q':q,'newly_computed':new,'manifest':manifest,
                                 'manifest_sha256':sha256_file(path/'manifest.json')})
                budget()
            for query in qs:
                qid=query['query_id']
                p=cc.generate(g['nodes'],g['edges'],scores[c,qid]['scores'],kernels,cfg)
                historical=old[c,qid]
                if p['methods']['B1']['selected_ids']!=historical['B1'] or p['methods']['B4']['selected_ids']!=historical['B4'] or p['methods']['G4']!=historical['G4']:
                    raise ValueError('historical-policy-reproduction:'+c+':'+qid)
                parity_checks.append([c,qid])
                predictions.append(dict(p,corpus_id=c,query_id=qid,query_text_group=query['question']))
                budget()
        jsonl(out/'predictions.jsonl',predictions)
        write(out/'parity.json',{'status':'qualified','matched_rows':parity_checks,
              'fields':['B1','B4','G4 all fields'],'scope':'30b full caches; original C2 bridge was verified by 30b',
              'prediction_sha256':sha256_file(out/'predictions.jsonl')})
        write(out/'feature_identities.json',features)
        summary={'prediction_rows':len(predictions),'features':len(features),'new_features':sum(f['newly_computed'] for f in features),
            'methods_runtime_seconds':{m:sum(p['method_runtime_seconds'].get(m,0.) for p in predictions) for m in cc.METHODS},
            'semantic_work':'all cached page scores sorted; B1/B4 timing not separately instrumented; no inference'}
    else:
        frozen=sha256_file(inp/'predictions.jsonl'); parity=read(inp/'parity.json')
        if parity['status']!='qualified' or parity['prediction_sha256']!=frozen: raise ValueError('prediction-hash')
        pred=unique(rows(inp/'predictions.jsonl'),['corpus_id','query_id'])
        targets=unique(rows(inp/'targets.jsonl'),['surface_id','query_id'])
        surfaces={s:r['corpus_id'] for (s,q),r in targets.items()}
        if set(pred)!={(c,q) for c in graphs for q in qids} or set(targets)!={(s,q) for s in surfaces for q in qids}: raise ValueError('label-prediction-join')
        judged={}
        for r in rows(inp/'judgments.jsonl'):
            key=(r['surface_id'],r['query_id']); d=r['support_target_id']
            if d in judged.setdefault(key,{}): raise ValueError('duplicate-judgment')
            judged[key][d]=r['relevance']
        evaluated=[]
        for s,c in surfaces.items():
            g=graphs[c]
            for query in qs:
                qid=query['query_id']; p=pred[c,qid]; t=targets[s,qid]
                if p['query_text_group']!=query['question'] or t['credit_kind']!='direct': raise ValueError('label-identity')
                jd=judged.get((s,qid),{})
                if set(jd)-set(g['nodes']) or set(t['known_target_ids'])!={d for d,r in jd.items() if r>0}: raise ValueError('judgment-universe')
                row=cc.evaluate(g['nodes'],g['edges'],p,t['known_target_ids'],lengths[c],jd)
                evaluated.append(dict(row,surface_id=s,corpus_id=c,query_id=qid,query_text_group=query['question']))
            budget()
        jsonl(out/'per_query.jsonl',evaluated)
        summary=cc.analyze(evaluated,qs,read(inp/'populations.json'),primary_surface=cfg['primary_surface'],
             arm_surfaces=cfg['arm_surfaces'],surface_pairs=cfg['surface_pairs'],draws=cfg['bootstrap_draws'],seed=cfg['bootstrap_seed'])
        idx={(r['surface_id'],r['query_id']):r for r in evaluated}
        # Bounded deterministic gain/loss cards, not a selected-subgroup estimate.
        categories={}; cards=[]
        for a,b in [('M25','Q0'),('M25','B4'),('M25','G4h2'),('M25','G4')]:
            key=a+'-'+b
            for sign,kind in [(1,'rescue'),(-1,'loss')]:
                available=[q for q in sorted(qids) if idx[cfg['primary_surface'],q]['comparisons'][key]['delta']==sign]
                categories[key+':'+kind]=len(available)
                if available:
                    q=available[0]; r=idx[cfg['primary_surface'],q]
                    cards.append({'comparison':key,'category':kind,'query_id':q,'question':r['query_text_group'],
                                  'known_target_ids':r['known_target_ids'],'methods':r['methods'],'comparison_detail':r['comparisons'][key]})
        write(out/'example_cards.json',{'stratum_sizes':categories,'cards':cards})
        # Outcome-blind source-group sample, all methods' candidate outputs, method identities withheld.
        sources=read(inp/'packet_sources.json'); groups={}
        for q in qs: groups.setdefault(sources[q['query_id']],[]).append(q['query_id'])
        chosen=[]
        for source in sorted(groups,key=lambda s:ms.digest(['32',s]))[:8]:
            chosen.append(min(groups[source],key=lambda q:ms.digest(['32',q])))
        packet=[]
        for qid in chosen:
            for c in graphs:
                docs=sorted({d for m in pred[c,qid]['methods'].values() for d in m['selected_ids']},key=lambda d:ms.digest(['32',d]))
                packet.append({'question':next(q['question'] for q in qs if q['query_id']==qid),'source_group':sources[qid],
                    'corpus_id':c,'pages':[{'doc_id':d,'sha256':sha256_file(inp/f'corpora/{c}/corpus/{d}.md'),
                       'full_text':contents[c][d],'judgment':None,'support_kind_options':['unknown','partial','alternative','jointly_necessary'],
                       'evidence_units':None,'joint_page_sets':None} for d in docs],
                    'requirement_units':None,'complete_coverage':None,'note':'blank review packet; no reference answer or method identity; not gold'})
        write(out/'annotation_packet.json',packet)
        summary['packet']={'source_groups':len(chosen),'cards':len(packet),'judgments_recorded':0}
        if sha256_file(inp/'predictions.jsonl')!=frozen: raise ValueError('predictions-mutated')
    write(out/'summary.json',summary); budget()
    manifest={'schema_version':'retrieval_arena.complementary_context_manifest.v1','command':command,
        'input_inventory_sha256':req['input_inventory_sha256'],'identities':identities,'outputs':regular_inventory(out),
        'runtime_seconds':time.monotonic()-started,'code_sha256':sha256_file(Path(cc.__file__)),
        'operator_sha256':sha256_file(Path(__file__)),
        'runtime':{'python':sys.version,'numpy':__import__('numpy').__version__,'scipy':__import__('scipy').__version__,
                   **{k:os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE','RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']}}}
    write(out/'manifest.json',manifest)
    write(out/'receipt.json',{'status':'qualified','manifest_sha256':sha256_file(out/'manifest.json')})
    return summary

def main(command):
    run(Path('/workspace/input'),Path('/workspace/output'),command)
    return 0
