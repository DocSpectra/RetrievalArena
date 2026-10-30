"""Frozen seed-preserving rank fusion and separately invoked access evaluation."""
from __future__ import annotations
from collections import Counter, defaultdict
import math
import time
import numpy as np
from .support_access import cached_ranking, _adjacency
from .phase_navigation import traverse_once
from retrieval_arena.diagnostics import _distances_from_targets

VERSION='complementary_context.v1'
METHODS=('B1','B4','G4','G4h2','Q0','M10','M25')
PAIRS=tuple((a,b) for a in METHODS for b in METHODS if a!=b)

def normalized_affinity(kernel,seed_index,*,tolerance=1e-10):
    h=np.asarray(kernel)
    if h.ndim!=2 or h.shape[0]!=h.shape[1] or not np.isfinite(h).all():
        raise ValueError('affinity-kernel')
    if not 0<tolerance<1: raise ValueError('affinity-tolerance')
    diag=np.diag(h)
    if np.max(np.abs(diag.imag))>tolerance or np.any(diag.real<=0):
        raise ValueError('affinity-diagonal')
    denom=np.sqrt(diag[seed_index].real*diag.real)
    if not np.isfinite(denom).all() or np.any(denom<=0): raise ValueError('affinity-denominator')
    affinity=np.abs(h[seed_index])/denom
    if not np.isfinite(affinity).all() or np.any(affinity>1+tolerance):
        raise ValueError('affinity-bound')
    return affinity

def select(nodes,score_pairs,kernel,*,tolerance=1e-10,offset=60,budget=4):
    ranked,scores=cached_ranking(nodes,score_pairs)
    if set(scores)!=set(nodes): raise ValueError('selector-score-universe')
    if offset!=60 or budget!=4 or len(nodes)<budget: raise ValueError('selector-policy')
    if np.shape(kernel)!=(len(nodes),len(nodes)): raise ValueError('selector-kernel-universe')
    seed=ranked[0]; ni={d:i for i,d in enumerate(nodes)}
    affinity=normalized_affinity(kernel,ni[seed],tolerance=tolerance)
    semantic={d:i+1 for i,d in enumerate(ranked[1:])}
    structural=sorted((d for d in nodes if d!=seed and affinity[ni[d]]>tolerance),
                      key=lambda d:(-affinity[ni[d]],d))
    srank={d:i+1 for i,d in enumerate(structural)}
    fused={d:1/(offset+r)+(1/(offset+srank[d]) if d in srank else 0.) for d,r in semantic.items()}
    selected=[seed]+sorted(semantic,key=lambda d:(-fused[d],-scores[d],d))[:budget-1]
    return {'selected_ids':selected,'seed_id':seed,'candidate_count':len(nodes),
            'positive_affinity_candidates':len(structural),
            'selection_scores':[{'doc_id':d,'cosine':scores[d],'affinity':float(affinity[ni[d]]),
                'semantic_rank':semantic.get(d),'affinity_rank':srank.get(d),'fused_score':fused.get(d)} for d in selected]}

def generate(nodes,edges,score_pairs,kernels,config):
    start=time.monotonic(); score_pairs=list(score_pairs)
    ranked,scores=cached_ranking(nodes,score_pairs)
    if set(scores)!=set(nodes): raise ValueError('selector-score-universe')
    _adjacency(nodes,edges)
    traversal=config['traversal']
    if traversal!={'candidate_budget':40,'directed':True,'distance_penalty':.02,'final_top_k':4,'max_hops':1,'neighbor_budget':20}:
        raise ValueError('selector-traversal-policy')
    output={'B1':{'selected_ids':ranked[:1],'candidate_count':len(nodes)},
            'B4':{'selected_ids':ranked[:4],'candidate_count':len(nodes)}}
    timings={}
    for name,hops in [('G4',1),('G4h2',2)]:
        t=time.monotonic(); output[name]=traverse_once(nodes,edges,scores,ranked[0],**dict(traversal,max_hops=hops))
        timings[name]=time.monotonic()-t
    for name,q in [('Q0',0.),('M10',.1),('M25',.25)]:
        t=time.monotonic()
        output[name]=select(nodes,score_pairs,kernels[q],tolerance=config['tolerance'],offset=config['rank_offset'],budget=config['budget'])
        timings[name]=time.monotonic()-t
    return {'schema_version':VERSION,'seed_id':ranked[0],'methods':output,'runtime_seconds':time.monotonic()-start,'method_runtime_seconds':timings}

def requirement_coverage(selected,units):
    """Each unit: alternatives of jointly necessary page sets. None = unknown."""
    if units is None: return {'status':'untested','reason':'no-independent-mapping'}
    if not units: raise ValueError('empty-requirements')
    ids=set(selected); covered=[]
    for u in units:
        if not u or any(not a or len(a)!=len(set(a)) for a in u): raise ValueError('requirement-alternatives')
        covered.append(any(set(alternative)<=ids for alternative in u))
    return {'status':'evaluated','covered':sum(covered),'total':len(units),
            'coverage':sum(covered)/len(units),'complete':all(covered)}

def evaluate(nodes,edges,prediction,targets,lengths,judged):
    if len(targets)!=len(set(targets)) or set(targets)-set(nodes): raise ValueError('target-universe')
    adj,weak=_adjacency(nodes,edges); seed=prediction['seed_id']
    distances=_distances_from_targets([seed],adj); wd=_distances_from_targets([seed],weak)
    target=set(targets); seed_positive=target & {seed}; methods={}
    if set(prediction['methods'])!=set(METHODS): raise ValueError('method-universe')
    for name,p in prediction['methods'].items():
        chosen=p['selected_ids']; ids=set(chosen)
        if len(ids)!=len(chosen) or ids-set(nodes) or len(chosen)>4: raise ValueError('selected-universe')
        if name in ('Q0','M10','M25') and (len(chosen)!=4 or chosen[0]!=seed): raise ValueError('selected-seed-budget')
        positives=sorted(ids & target)
        methods[name]={'hit':int(bool(positives)),'known_positive_ids':positives,
            'known_positive_coverage':len(positives)/len(target) if target else None,
            'additional_positive_ids_beyond_seed':sorted((ids & target)-seed_positive),
            'additional_hit_beyond_seed':int(bool(positives) and not seed_positive),
            'new_positive_identity_count_beyond_seed':len((ids & target)-seed_positive),
            'missing_known_positive_ids':sorted(target-ids),'selected_ids':chosen,'selected_count':len(chosen),
            'selected_characters':sum(lengths[d] for d in chosen),'candidate_count':p['candidate_count'],
            'judged_selected_ids':sorted(ids & set(judged)),'unjudged_selected_ids':sorted(ids-set(judged)),
            'judged_nonpositive_selected_ids':sorted(d for d in ids & set(judged) if judged[d]<=0),
            'route_strata':dict(Counter('within_one_hop' if d in distances and distances[d]<=1 else
                'longer_directed_route' if d in distances else 'wrong_direction_weak' if d in wd else 'separate_component' for d in chosen))}
    comparisons={}
    for a,b in PAIRS:
        x,y=methods[a],methods[b]; xp,yp=set(x['known_positive_ids']),set(y['known_positive_ids'])
        comparisons[a+'-'+b]={'delta':x['hit']-y['hit'],
            'cell':'both_hit' if x['hit'] and y['hit'] else 'a_only' if x['hit'] else 'b_only' if y['hit'] else 'neither_hit',
            'shared_positive_ids':sorted(xp&yp),'a_exclusive_positive_ids':sorted(xp-yp),'b_exclusive_positive_ids':sorted(yp-xp)}
    return {'eligible':bool(target),'known_target_ids':sorted(target),'seed_positive_ids':sorted(seed_positive),
            'methods':methods,'comparisons':comparisons,'requirement_endpoint':requirement_coverage(None,None)}

def analyze(rows,queries,populations,*,primary_surface,arm_surfaces,surface_pairs,draws=2000,seed=32061030):
    qids=[q['query_id'] for q in queries]; texts={q['query_id']:q['question'] for q in queries}
    surfaces=sorted({r['surface_id'] for r in rows}); idx={(r['surface_id'],r['query_id']):r for r in rows}
    if len(idx)!=len(rows) or set(idx)!={(s,q) for s in surfaces for q in qids} or len(qids)!=len(set(qids)):
        raise ValueError('evaluation-join')
    groups=sorted(set(texts.values())); gi={g:i for i,g in enumerate(groups)}
    gc=np.zeros((len(groups),len(qids)),dtype=int)
    for i,q in enumerate(qids): gc[gi[texts[q]],i]=1
    rng=np.random.default_rng(seed)
    weights=np.stack([gc[rng.integers(0,len(groups),len(groups))].sum(axis=0) for _ in range(draws)])
    def interval(vals,ids):
        mask=np.array([q in ids for q in qids]); v=np.asarray(vals,float); den=weights[:,mask].sum(axis=1)
        samples=np.full(draws,np.nan); np.divide(weights[:,mask]@v[mask],den,out=samples,where=den>0)
        valid=samples[np.isfinite(samples)]
        return {'estimate':float(v[mask].mean()) if mask.any() else None,
            'interval_95':np.quantile(valid,[.025,.975]).tolist() if len(valid) else None,
            'draws':draws,'available_draws':len(valid),'empty_draws':draws-len(valid)}
    hit={(s,m):np.array([idx[s,q]['methods'][m]['hit'] for q in qids]) for s in surfaces for m in METHODS}
    def aggregate(s,ids):
        rs=[idx[s,q] for q in qids if q in ids]; n=len(rs)
        out={'queries':n,'no_known_positive_ids':[r['query_id'] for r in rs if not r['eligible']], 'methods':{},'comparisons':{}}
        for m in METHODS:
            ms=[r['methods'][m] for r in rs]; coverage=[v['known_positive_coverage'] for v in ms if v['known_positive_coverage'] is not None]
            out['methods'][m]={'hits':sum(v['hit'] for v in ms),'rate':sum(v['hit'] for v in ms)/n if n else None,
                'rate_interval':interval(hit[s,m],ids),'known_positive_set_coverage':float(np.mean(coverage)) if coverage else None,
                'additional_hit_beyond_seed':sum(v['additional_hit_beyond_seed'] for v in ms),
                'new_positive_identities_beyond_seed':sum(v['new_positive_identity_count_beyond_seed'] for v in ms),
                'selected_count_total':sum(v['selected_count'] for v in ms),
                'selected_count_distribution':dict(Counter(v['selected_count'] for v in ms)),
                'selected_characters_total':sum(v['selected_characters'] for v in ms),
                'candidate_count_total':sum(v['candidate_count'] for v in ms),
                'unjudged_selected_count':sum(len(v['unjudged_selected_ids']) for v in ms),
                'route_strata':dict(sum((Counter(v['route_strata']) for v in ms),Counter()))}
        for a,b in PAIRS:
            key=a+'-'+b; pairs=[r['comparisons'][key] for r in rs]
            out['comparisons'][key]={'interval':interval(hit[s,a]-hit[s,b],ids),
                'cells':{c:sum(p['cell']==c for p in pairs) for c in ('both_hit','a_only','b_only','neither_hit')},
                'rescue_ids':[r['query_id'] for r in rs if r['comparisons'][key]['delta']>0],
                'loss_ids':[r['query_id'] for r in rs if r['comparisons'][key]['delta']<0],
                'shared_positive_pairs':sum(len(p['shared_positive_ids']) for p in pairs),
                'a_exclusive_positive_pairs':sum(len(p['a_exclusive_positive_ids']) for p in pairs),
                'b_exclusive_positive_pairs':sum(len(p['b_exclusive_positive_ids']) for p in pairs)}
        return out
    common=set(populations['common']); panel=set(qids)
    if primary_surface not in surfaces or set(arm_surfaces)-set(surfaces) or any(a not in surfaces or b not in surfaces for a,b in surface_pairs):
        raise ValueError('surface-config')
    inferred={q for q in qids if all(idx[s,q]['eligible'] for s in arm_surfaces)}
    if common!=inferred: raise ValueError('common-positive-population')
    out={'schema_version':VERSION,'bootstrap':{'draws':draws,'seed':seed,'exact_text_groups':len(groups),
         'estimand':'query macro; resample original exact-text groups jointly, fixed masks within draws; conditional on saved corpus/query/label panel'},
         'primary':aggregate(primary_surface,panel),'views':{},'cross_arm':{}}
    for name,ids in [('common',common),('all_panel',panel)]:
        out['views'][name]={s:aggregate(s,ids) for s in surfaces}
        out['cross_arm'][name]={}
        for a,b in surface_pairs:
            contrasts={}
            for m in METHODS:
                contrasts[m+'_absolute']=interval(hit[a,m]-hit[b,m],ids)
            for x,y in [('M25','B4'),('Q0','B4'),('M25','Q0'),('M25','G4'),('M25','G4h2')]:
                contrasts[x+'-'+y]=interval((hit[a,x]-hit[a,y])-(hit[b,x]-hit[b,y]),ids)
            out['cross_arm'][name][a+'-'+b]=contrasts
    out['arm_eligible']={s:aggregate(s,{q for q in qids if idx[s,q]['eligible']}) for s in surfaces}
    return out
