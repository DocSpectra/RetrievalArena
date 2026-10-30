"""Offline page-representation operator; labels are never classification truth here."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import sys
import time
for key in ['OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[key]='1'
os.environ['MPLCONFIGDIR']='/tmp/matplotlib'
sys.path.insert(0,'/workspace/source')
import numpy as np
from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_file
from retrieval_arena.graph import magnetic_spectrum as ms
from retrieval_arena.graph import page_representations as pr

def write(p,x): p.write_bytes(canonical(x)+b'\n')
def read(p): return json.loads(p.read_bytes())

def run(inp,out):
    started=time.monotonic()
    if any(p.name!='.retrieval-arena-output-owner' for p in out.iterdir()): raise ValueError('page-output-not-fresh')
    inv=[r for r in regular_inventory(inp) if r['path']!='operator-request.json']
    req=read(inp/'operator-request.json')
    if req!={'schema_version':'retrieval_arena.page_representations_request.v1','command':'export-page-representations',
             'input_inventory':inv,'input_inventory_sha256':ms.digest(inv)}:
        raise ValueError('page-input-inventory')
    cfg=read(inp/'config.json')
    expected={'schema_version','graphs','parameters','kernel_tolerance','map_anchors','card_count','max_runtime_seconds','max_output_bytes'}
    if set(cfg)!=expected or cfg['schema_version']!='retrieval_arena.page_representations_config.v1': raise ValueError('page-config')
    if not 0<cfg['max_runtime_seconds']<=14400 or not 0<cfg['max_output_bytes']<=6*1024**3: raise ValueError('page-budget')
    if not 4<=cfg['map_anchors']<=40 or not 0<=cfg['card_count']<=40: raise ValueError('page-card-map-count')
    if not cfg['graphs'] or not cfg['parameters']: raise ValueError('page-empty-jobs')
    if len({tuple(p) for p in cfg['parameters']})!=len(cfg['parameters']): raise ValueError('page-duplicate-parameters')
    if not all(isinstance(k,str) and k.isalnum() and isinstance(v,str) and Path(v).name==v for k,v in cfg['graphs'].items()):
        raise ValueError('page-graph-path')
    pages=read(inp/'pages.json'); nodes=[r['doc_id'] for r in pages]
    if nodes!=sorted(set(nodes)): raise ValueError('page-order')
    allowed={'config.json','pages.json',*cfg['graphs'].values(),*('corpus/'+v+'.md' for v in nodes)}
    if {r['path'] for r in inv}!=allowed: raise ValueError('page-undeclared-input')
    texts={v:(inp/'corpus'/f'{v}.md').read_text(encoding='utf-8') for v in nodes}
    for row in pages:
        if row['raw_sha256']!=sha256_file(inp/'corpus'/f"{row['doc_id']}.md"): raise ValueError('page-content-hash')
    content=ms.digest([(v,sha256_file(inp/'corpus'/f'{v}.md')) for v in nodes])
    anchors=sorted(nodes,key=lambda v:hashlib.sha256(v.encode()).hexdigest())[:cfg['map_anchors']]
    write(out/'review_cards.json',pr.review_cards(pages,texts,cfg['card_count']))
    write(out/'cleaned_text_inventory.json',[{'doc_id':v,'clean_sha256':hashlib.sha256(pr.clean_text(texts[v]).encode()).hexdigest(),
                                            'clean_characters':len(pr.clean_text(texts[v]))} for v in nodes])
    results=[]; maps={}; summaries={}; simple_by_family={}
    for family,bundle_path in cfg['graphs'].items():
        graph=read(inp/bundle_path); admitted=ms.validate_bundle(graph)
        if admitted['nodes']!=nodes: raise ValueError('page-graph-node-join')
        edges=admitted['edges']; simple,details=pr.simple_features(nodes,edges)
        write(out/f'{family}_simple.json',{'rows':simple,'definition':details})
        simple_by_family[family]=simple
        maps[family]={}
        for q,t in cfg['parameters']:
            if time.monotonic()-started>cfg['max_runtime_seconds']: raise ValueError('page-runtime-budget')
            key=f'q{q:.2f}_t{t:g}'
            kernel,receipt=pr.heat_kernel(nodes,edges,q,t,cfg['kernel_tolerance'])
            manifest=pr.save_bundle(out/f'{family}_{key}',nodes,kernel,receipt,content)
            # Exercise the actual downstream loader before accepting an export.
            pr.load_bundle(out/f'{family}_{key}',content_identity=content,graph_sha256=receipt['graph_sha256'],q=q,t=t)
            table=pr.descriptive_map(nodes,kernel,anchors)
            write(out/f'{family}_{key}_map.json',{'anchors':anchors,'rows':table})
            maps[family][key]=table
            results.append({'family':family,'key':key,'bundle':f'{family}_{key}',**receipt,
                            'manifest_sha256':sha256_file(out/f'{family}_{key}/manifest.json')})
            summaries[f'{family}/{key}']={
                'edges':len(edges),'isolates':sum(r['isolate'] for r in simple),
                'weak_components':len(set(r['component_id'] for r in simple)),
                'low_anchor_coupling':sum(r['low_anchor_coupling'] for r in table),
                'self_mass_share_median':float(np.median([r['self_mass_share'] for r in table])),
                'effective_row_support_median':float(np.median([r['effective_row_support'] for r in table]))}
            if sum(p.stat().st_size for p in out.rglob('*') if p.is_file())>cfg['max_output_bytes']: raise ValueError('page-output-budget')
    # Same fixed display coordinates and limits; no data-fitted rotation or role names.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    selected=[(f,k) for f,rs in maps.items() for k in rs if k in ['q0.25_t1','q0.00_t1']]
    if selected:
        fig,axes=plt.subplots(1,len(selected),figsize=(4*len(selected),4),squeeze=False)
        extent=max(1e-6,max(abs(r[d]) for f,k in selected for r in maps[f][k] for d in ['x','y']))*1.05
        for ax,(f,k) in zip(axes.flat,selected):
            rows=maps[f][k]
            ax.scatter([r['x'] for r in rows],[r['y'] for r in rows],c=[r['group'] for r in rows],s=7,cmap='tab10',vmin=0,vmax=9,alpha=.6)
            ax.set(xlim=(-extent,extent),ylim=(-extent,extent),title=f'{f}: {k}',xlabel='distance to anchor 0 minus 1',ylabel='distance to anchor 2 minus 3')
        fig.suptitle('Fixed-anchor heat-row maps; overlaps and groups do not establish document roles')
        fig.tight_layout(); fig.savefig(out/'page_maps.png',dpi=160); plt.close(fig)
    stability={}
    families=list(maps)
    if len(families)==2:
        a,b=families
        for key in maps[a]:
            left,right=maps[a][key],maps[b][key]
            stability[key]={'anchor_group_agreement_all':sum(x['group']==y['group'] for x,y in zip(left,right))/len(nodes),
                'low_coupling_either':sum(x['low_anchor_coupling'] or y['low_anchor_coupling'] for x,y in zip(left,right)),
                'warning':'same fixed anchors; group agreement can include ties/low coupling, not semantic validation'}
    summary={'bundles':results,'descriptive':summaries,'scaffold_stability':stability,
             'role_endpoint':'untested: no qualified independent role taxonomy; no classifier fitted',
             'anchors':anchors,'content_identity':content,'runtime_seconds':time.monotonic()-started}
    write(out/'summary.json',summary)
    manifest={'schema_version':'retrieval_arena.page_representations_operator_manifest.v1',
        'input_inventory_sha256':ms.digest(inv),'config_sha256':sha256_file(inp/'config.json'),
        'code_sha256':sha256_file(Path(pr.__file__)),'operator_sha256':sha256_file(Path(__file__)),
        'normalized_operator_sha256':ms.code_sha256(),
        'outputs':regular_inventory(out),'runtime_seconds':time.monotonic()-started,
        'runtime':{'python':sys.version,'numpy':np.__version__,'scipy':__import__('scipy').__version__,
                   **{k:os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE','RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']}}}
    write(out/'page_representations_manifest.json',manifest)
    write(out/'operator_receipt.v1.json',{'status':'qualified','manifest_sha256':sha256_file(out/'page_representations_manifest.json')})
    return summary

def main():
    run(Path('/workspace/input'),Path('/workspace/output'))
    return 0
