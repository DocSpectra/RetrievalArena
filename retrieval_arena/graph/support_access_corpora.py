"""Corpus-namespaced support access, generated predictions and joint contrasts.

Version 2 is separate from v1 historical replay. No label enters generation.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import math
import numpy as np
from . import support_access as sa
from .phase_navigation import traverse_once

VERSION = 'support_access_corpora.v2'
POLICIES = ('B1','B4','G4','Bm')

def generate(nodes, edges, score_pairs, traversal):
    score_pairs=list(score_pairs)
    ranked,scores=sa.cached_ranking(nodes,score_pairs)
    if set(scores)!=set(nodes): raise ValueError('corpus-score-universe')
    if traversal.get('directed') is not True or traversal.get('max_hops')!=1:
        raise ValueError('corpus-requires-directed-one-hop')
    sa._adjacency(nodes,edges)
    trial=traverse_once(nodes,edges,scores,ranked[0],**traversal)
    # New consistency assertion, not a claim of independent historical replay.
    prediction=sa.freeze_prediction(nodes,edges,score_pairs,trial,traversal)
    prediction['schema_version']='retrieval_arena.support_access_prediction.v2'
    return prediction

def evaluate(nodes,edges,prediction,targets,traversal,depths,*,credit_kind='direct'):
    if credit_kind!='direct': raise ValueError('corpus-derived-credit-not-direct')
    if len(targets)!=len(set(targets)) or set(targets)-set(nodes):
        raise ValueError('corpus-target-universe')
    row=sa.evaluate_prediction(nodes,edges,prediction,targets,traversal,depths)
    row.update(schema_version='retrieval_arena.support_access_evaluation.v2',
               no_known_positive_in_arm=not row['eligible'])
    return row

def parity(nodes, generated, old_predictions, new_scores, old_scores, *, atol,rtol):
    discrepancies=[]; maximum=0.
    for qid in sorted(old_scores):
        new=dict(new_scores[qid]); old=dict(old_scores[qid])
        if set(new)!=set(nodes) or set(old)!=set(nodes): raise ValueError('parity-score-universe')
        for d in nodes:
            err=abs(new[d]-old[d]); maximum=max(maximum,err)
            if not math.isclose(new[d],old[d],abs_tol=atol,rel_tol=rtol):
                discrepancies.append({'query_id':qid,'field':'score','doc_id':d,'old':old[d],'new':new[d]})
        a,b=generated[qid],old_predictions[qid]
        for field in ('B1','B4'):
            if a[field]!=b[field]: discrepancies.append({'query_id':qid,'field':field,'old':b[field],'new':a[field]})
        for field in ('seed_id','candidate_ids','selected_ids'):
            if a['G4'][field]!=b['G4'][field]: discrepancies.append({'query_id':qid,'field':'G4.'+field,'old':b['G4'][field],'new':a['G4'][field]})
    return {'status':'qualified' if not discrepancies else 'failed','queries':len(old_scores),
            'absolute_tolerance':atol,'relative_tolerance':rtol,'maximum_absolute_score_error':maximum,
            'discrepancies':discrepancies}

def masked_aggregate(rows,ids,*,all_panel=False):
    subset=[r for r in rows if r['query_id'] in set(ids)]
    if len(subset)!=len(ids): raise ValueError('population-row-join')
    if not all_panel and any(not r['eligible'] for r in subset): raise ValueError('population-without-target')
    # Preserve missing-target state; zero is only the named all-panel saved-label estimate.
    if all_panel:
        for r in subset:
            if not r['eligible'] and (any(r['hits'].values()) or r['failure']['stage']!='unavailable'):
                raise ValueError('no-target-state')
    available=[r for r in subset if r['eligible']]
    descriptive=sa.aggregate(available)
    n=len(subset)
    return {'queries':n,'known_target_queries':len(available),
        'no_known_positive_ids':[r['query_id'] for r in subset if not r['eligible']],
        'estimate_kind':'all-panel-saved-label-access' if all_panel else 'positive-eligible',
        'hit_counts':{p:sum(r['hits'][p] for r in subset) for p in POLICIES},
        'hit_rates':{p:sum(r['hits'][p] for r in subset)/n if n else None for p in POLICIES},
        'paired_cells':{c:sum(r['cell']==c for r in subset) for c in sa.CELLS},
        'target_eligible_diagnostics':descriptive}

def analyze(rows,queries,populations,pairs,*,draws,seed):
    """Resample all original exact-text groups once, then apply fixed masks.

    Linear contrast terms keep interaction and component arithmetic together.
    """
    qids=[q['query_id'] for q in queries]
    if len(qids)!=len(set(qids)) or not qids or draws<1: raise ValueError('corpus-query-or-bootstrap')
    surfaces=sorted({r['surface_id'] for r in rows})
    index={}
    for r in rows:
        key=(r['surface_id'],r['query_id'])
        if key in index: raise ValueError('corpus-duplicate-evaluation')
        index[key]=r
    if set(index)!={(s,q) for s in surfaces for q in qids}: raise ValueError('corpus-evaluation-join')
    if any(index[s,q]['query_text_group']!=x['question'] for s in surfaces for q,x in zip(qids,queries)):
        raise ValueError('corpus-text-group-identity')
    groups=sorted({q['question'] for q in queries})
    group_index={g:i for i,g in enumerate(groups)}
    qi={q:i for i,q in enumerate(qids)}
    group_counts=np.zeros((len(groups),len(qids)),dtype=np.int64)
    for q in queries: group_counts[group_index[q['question']],qi[q['query_id']]]=1
    rng=np.random.default_rng(seed)
    weights=np.stack([group_counts[rng.integers(0,len(groups),len(groups))].sum(axis=0) for _ in range(draws)])
    def interval(values,mask):
        values=np.asarray(values,float); mask=np.asarray(mask,bool)
        denom=weights[:,mask].sum(axis=1)
        samples=np.full(draws,np.nan)
        np.divide(weights[:,mask]@values[mask],denom,out=samples,where=denom>0)
        valid=samples[np.isfinite(samples)]
        return {'estimate':float(values[mask].mean()) if mask.any() else None,
            'interval_95':np.quantile(valid,[.025,.975]).tolist() if len(valid) else None,
            'queries':int(mask.sum()),'draws':draws,'available_draws':len(valid),'empty_draws':draws-len(valid),
            'seed':seed,'text_groups_in_joint_resample':len(groups),'estimand':'query-macro',
            'conditional_on_fixed_corpora_and_labels':True}
    hit={(s,p):np.array([index[s,q]['hits'][p] for q in qids]) for s in surfaces for p in POLICIES}
    masks={'common':set(populations['common']),'all_panel':set(qids)}
    if not masks['common']<=set(qids): raise ValueError('corpus-population-universe')
    inferred={q for q in qids if all(index[s,q]['eligible'] for s in populations['primary_surfaces'])}
    if masks['common']!=inferred: raise ValueError('corpus-common-population-label-mismatch')
    out={'schema_version':VERSION,'populations':{},'contrasts':{},'transitions':{},'transition_tables':{}}
    for pop,ids in masks.items():
        mask=[q in ids for q in qids]
        out['populations'][pop]={}
        for s in surfaces:
            # Old label/synthetic surfaces may not have targets on every common query.
            selected=[index[s,q] for q in qids]
            all_mode=pop=='all_panel' or any(not index[s,q]['eligible'] for q in ids)
            agg=masked_aggregate(selected,sorted(ids),all_panel=all_mode)
            agg['intervals']={f'G4_minus_{p}':interval(hit[s,'G4']-hit[s,p],mask) for p in ('B1','B4','Bm')}
            out['populations'][pop][s]=agg
        out['contrasts'][pop]={}
        for a,b in pairs:
            out['contrasts'][pop][a+'-'+b]={
                'graph_increment':interval((hit[a,'G4']-hit[a,'B4'])-(hit[b,'G4']-hit[b,'B4']),mask),
                **{p:interval(hit[a,p]-hit[b,p],mask) for p in ('B4','G4')}}
    out['arm_eligible']={}
    for s in surfaces:
        ids=[q for q in qids if index[s,q]['eligible']]
        agg=masked_aggregate([index[s,q] for q in qids],ids)
        agg['intervals']={f'G4_minus_{p}':interval(hit[s,'G4']-hit[s,p],[q in ids for q in qids]) for p in ('B1','B4','Bm')}
        out['arm_eligible'][s]=agg
    for a,b in pairs:
        transitions=[]
        for q in qids:
            x,y=index[a,q],index[b,q]
            transitions.append({'query_id':q,'in_common':q in masks['common'],
                'a_delta':x['G4_minus_B4'],'b_delta':y['G4_minus_B4'],
                'interaction':x['G4_minus_B4']-y['G4_minus_B4'],
                'a_hits':x['hits'],'b_hits':y['hits'],'a_failure':x['failure']['stage'],'b_failure':y['failure']['stage'],
                'a_best_rank':x['best_known_positive_semantic_rank'],'b_best_rank':y['best_known_positive_semantic_rank'],
                'a_only_targets':sorted(set(x['known_target_ids_in_view'])-set(y['known_target_ids_in_view'])),
                'b_only_targets':sorted(set(y['known_target_ids_in_view'])-set(x['known_target_ids_in_view']))})
        out['transitions'][a+'-'+b]=transitions
        out['transition_tables'][a+'-'+b]={}
        for pop,ids in masks.items():
            subset=[t for t in transitions if t['query_id'] in ids]
            out['transition_tables'][a+'-'+b][pop]={
                'from_surface':b,'to_surface':a,'queries':len(subset),
                'delta_matrix':{str(before):{str(after):sum(t['b_delta']==before and t['a_delta']==after for t in subset) for after in (-1,0,1)} for before in (-1,0,1)},
                'failure_transitions':[{'from_stage':before,'to_stage':after,'queries':n} for (before,after),n in sorted(Counter((t['b_failure'],t['a_failure']) for t in subset).items())]}
    return out

def example_ids(transitions,index,*,a,b):
    categories=defaultdict(list)
    for t in sorted(transitions,key=lambda t:t['query_id']):
        q=t['query_id']
        category=('improved' if t['interaction']>0 else 'worsened' if t['interaction']<0 else
            'unchanged_graph_only' if any(index[s,q]['cell']=='graph_only' for s in (a,b)) else 'remaining_unchanged')
        categories[category].append(q)
    return {c:categories[c][:2] for c in ('improved','worsened','unchanged_graph_only','remaining_unchanged')}
