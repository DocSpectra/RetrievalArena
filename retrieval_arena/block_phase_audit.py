"""Inventory-bound offline comparison. Inputs/fixtures are consumer-owned."""
from dataclasses import replace
import os
import time
from .embedded_link_audit import read,write,portable_inventory
from .graph import block_phase as bp


def run(inp,out):
    import resource
    started=time.monotonic(); req=read(inp/'operator-request.json')
    inv=[r for r in portable_inventory(inp) if r['path']!='operator-request.json']
    if inv!=req['input_inventory'] or bp.lo.ms.digest(inv)!=req['input_inventory_sha256']:
        raise ValueError('input-inventory-mismatch')
    if set(p.name for p in out.iterdir())!={'.retrieval-arena-output-owner'}:
        raise ValueError('output-not-fresh')
    cfg=read(inp/'policy.json'); cases=read(inp/'cases.json'); cache={}; summaries=[]
    policy=bp.lo.Policy(starts=cfg['starts'],maxiter=cfg['maxiter'],stationarity=cfg['stationarity'],
                        identity_atol=cfg['identity_atol'],rank_atol=cfg['rank_atol'])
    for case in cases:
        if case.get('assignment') not in ('development','fixture'):
            raise ValueError('reserved-outcome-forbidden')
        sets=case.get('sets',[[e] for e in case.get('additions',[])])
        result={'id':case['id'],'assignment':case['assignment'],'case_sha256':bp.lo.ms.digest(case),
                'nodes':len(case['nodes']),'edges':len(case['edges']),'charges':[]}
        for q in cfg['charges']:
            rows=[]
            for additions in sets:
                row={'additions':additions}
                for mode in ('component','block'):
                    row[mode]=[bp.compare(case['nodes'],case['edges'],additions,q,mode,
                              replace(policy,seed=seed),cache) for seed in cfg['seeds']]
                    if time.monotonic()-started>cfg['max_runtime_seconds']:
                        write(out/'partial-case.json',result); write(out/'partial-solves.json',cache)
                        raise TimeoutError('offline-resource-ceiling')
                row['tree']=bp.fixed_tree_cost(case['nodes'],case['edges'],additions,q)
                row['reverse_tree']=bp.fixed_tree_cost(case['nodes'],case['edges'],additions,q,True)
                rows.append(row)
            result['charges'].append({'q':q,'rows':rows,'order':{
                mode:bp.ordering([r[mode] for r in rows],policy.rank_atol) for mode in ('component','block')}})
        write(out/(case['id'].replace(':','-')+'.json'),result)
        summaries.append(result)
        print(case['id'],round(time.monotonic()-started,3),flush=True)
    write(out/'solves.json',cache)
    summary={'schema_version':bp.VERSION+'.audit','cases':summaries,'policy':cfg,
             'runtime_seconds':time.monotonic()-started,'peak_rss_kib_linux':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             'unique_solves':len(cache),'versions':bp.lo.ms.runtime_identity(),
             'image':{k:os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE','RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']},
             'model_calls':0,'reserved_outcomes_inspected':False}
    write(out/'summary.json',summary)
    outputs=portable_inventory(out)
    if sum(r['size_bytes'] for r in outputs)>cfg['max_output_bytes']: raise ValueError('output-budget')
    write(out/'manifest.json',{'outputs':outputs,'outputs_sha256':bp.lo.ms.digest(outputs)})
