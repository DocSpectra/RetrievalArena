"""Deterministic matched-budget selection on an already independently eligible pool.

No generation, semantic judgment, model transport or document edit occurs here.
Numerical callbacks must return cost, uncertainty and qualification separately.
"""
from itertools import combinations
import math


def feasible_sets(pool, maximum=4, *, constraint_version=None):
    from .embedded_constraints import admit_version
    admit_version(constraint_version)
    if len(pool) > 20 or maximum < 0 or maximum > 4:
        raise ValueError('selection-budget')
    ids = [p['proposal_id'] for p in pool]
    if len(set(ids)) != len(ids): raise ValueError('duplicate-proposal-id')
    if any(not p['semantic_eligible'] or p['semantic_score'] < 5 or p['semantic_score'] > 6 for p in pool):
        raise ValueError('pool-not-semantically-eligible')
    allpairs={(p['source_id'],p['target_id']) for p in pool}
    if constraint_version is None and any((t,s) in allpairs for s,t in allpairs): raise ValueError('pool-reciprocal-partners')
    result = []
    for k in range(min(maximum,len(pool)) + 1):
        for subset in combinations(pool,k):
            selected = {p['proposal_id'] for p in subset}
            if len({p['source_id'] for p in subset}) != k: continue
            pairs = [(p['source_id'],p['target_id']) for p in subset]
            if len(set(pairs)) != k or any((t,s) in pairs for s,t in pairs): continue
            if any(selected & set(p['conflicts']) for p in subset): continue
            result.append(frozenset(selected))
    return result


def select(pool, arm, score=None, *, maximum=4, shortlist=3, rank_atol=1e-6):
    if arm not in ('S','M') or shortlist != 3 or rank_atol <= 0:
        raise ValueError('selection-policy')
    feasible = feasible_sets(pool,maximum)
    budget = max(map(len,feasible),default=0)
    completions = [s for s in feasible if len(s)==budget]
    order = sorted(pool,key=lambda p:(-p['semantic_score'],p['proposal_id']))
    chosen=[]; trace=[]; selected=set()
    while len(chosen)<budget:
        available=[p for p in order if p['proposal_id'] not in selected and
                   any(selected|{p['proposal_id']} <= c for c in completions)]
        if not available: raise ValueError('feasible-completion-lost')
        if arm=='S': pick=available[0]; trace.append({'choices':[pick['proposal_id']], 'scores':[]})
        else:
            if score is None: raise ValueError('objective-callback-required')
            candidates=available[:shortlist]
            scored=[(p,score(tuple(chosen+[p['proposal_id']]))) for p in candidates]
            for _,r in scored:
                if not r['qualified'] or not math.isfinite(r['cost']) or not math.isfinite(r['uncertainty']) or r['uncertainty']<0:
                    raise ValueError('unqualified-selection-score')
            numeric=sorted(scored,key=lambda pr:(pr[1]['cost'],-pr[0]['semantic_score'],pr[0]['proposal_id']))
            for (_,a),(_,b) in zip(numeric,numeric[1:]):
                gap=b['cost']-a['cost']; uncertainty=a['uncertainty']+b['uncertainty']
                if gap>rank_atol and gap<=uncertainty:
                    raise ValueError('unresolved-selection-ranking')
                if gap<=rank_atol and uncertainty>4*rank_atol:
                    raise ValueError('unstable-selection-tie')
            minimum=numeric[0][1]['cost']
            tied=[p for p,r in scored if r['cost']-minimum<=rank_atol]
            pick=min(tied,key=lambda p:(-p['semantic_score'],p['proposal_id']))
            trace.append({'choices':[p['proposal_id'] for p in candidates],
                          'scores':[{'proposal_id':p['proposal_id'],**r} for p,r in scored]})
        chosen.append(pick['proposal_id']); selected.add(pick['proposal_id'])
        trace[-1]['selected']=pick['proposal_id']
    return {'arm':arm,'requested_budget':maximum,'common_feasible_budget':budget,
            'selected':chosen,'trace':trace,'selection_objective_evaluations':sum(len(t['scores']) for t in trace),
            'global_optimality_claimed':False,'same_source_cap':1}
