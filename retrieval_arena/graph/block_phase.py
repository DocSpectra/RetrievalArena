"""Exact support cancellation and finite-algorithm unit-phase estimates.

Block decomposition is exact for the optimum, not a global-optimum certificate
for the bounded solver. Historical local_objective.v1 is unchanged.
"""
from collections import deque
from dataclasses import asdict
import numpy as np
import networkx as nx
from . import local_objective as lo

VERSION = 'retrieval_arena.block_phase.v1'


def partitions(nodes, edges, mode):
    graph = lo.ms.admit_graph(nodes, edges)
    nodes, edges = graph['nodes'], graph['edges']
    g = nx.Graph(); g.add_nodes_from(nodes); g.add_edges_from(edges)
    if mode == 'component':
        groups = [set(c) for c in nx.connected_components(g)]
        rows = [(sorted(c), sorted((s,t) for s,t in edges if s in c)) for c in groups]
    elif mode == 'block':
        rows = []
        for block in nx.biconnected_component_edges(g):
            pairs = {tuple(sorted(e)) for e in block}
            arcs = sorted((s,t) for s,t in edges if tuple(sorted((s,t))) in pairs)
            rows.append((sorted({n for e in pairs for n in e}), arcs))
        rows += [([n], []) for n in sorted(nx.isolates(g))]
    else:
        raise ValueError('partition-mode')
    return sorted(rows, key=lambda r: (r[0], r[1]))


def tree_phases(nodes, edges, q, reverse=False):
    nodes, ts = lo.terms(nodes, edges, q)
    adjacency = {n: [] for n in nodes}
    for i,j,w,theta in zip(*ts):
        adjacency[nodes[i]].append((nodes[j],float(theta)))
        adjacency[nodes[j]].append((nodes[i],-float(theta)))
    phase = {}; tree = []
    for root in sorted(nodes, reverse=reverse):
        if root in phase: continue
        phase[root] = 0.; queue = deque([root])
        while queue:
            s = queue.popleft()
            for t,theta in sorted(adjacency[s],reverse=reverse):
                if t in phase: continue
                phase[t] = phase[s]-theta; tree.append([s,t]); queue.append(t)
    return np.array([phase[n] for n in nodes]), tree


def fixed_tree_cost(nodes, edges, additions, q, reverse=False):
    lo.eligible_additions(nodes,edges,additions)
    phi, tree = tree_phases(nodes,edges,q,reverse)
    _,ts = lo.terms(nodes,additions,q)
    return {'cost':lo.energy_gradient(phi,ts)[0], 'phases':phi.tolist(), 'tree':tree,
            'reference':'reverse_sorted_bfs' if reverse else 'sorted_bfs',
            'global_optimum_claimed':False}


def cached_solve(nodes, edges, q, policy, cache):
    key = lo.ms.digest([VERSION,nodes,edges,q,asdict(policy)])
    if key not in cache:
        fit = lo.solve(nodes,edges,q,policy)
        # Recompute every emitted solution, including unsuccessful starts.
        _, ts = lo.terms(nodes,edges,q)
        errors = [abs(lo.energy_gradient(np.array(s['final_phases']),ts)[0]-s['energy'])
                  for s in fit['starts']]
        fit['identity_error'] = max(errors,default=0.)
        fit['has_stationary_solution'] = any(s['stationary'] for s in fit['starts'])
        fit['selected_gradient_inf'] = fit['starts'][fit['best_start']]['gradient_inf']
        fit['support_nodes'] = nodes; fit['support_edges'] = edges
        cache[key] = fit
    return key, cache[key]


def compare(nodes, edges, additions, q, mode, policy=lo.Policy(), cache=None):
    """Subtract only changed partitions; identical partitions cancel symbolically."""
    if cache is None: cache = {}
    graph = lo.ms.admit_graph(nodes,edges); nodes,edges = graph['nodes'],graph['edges']
    lo.eligible_additions(nodes,edges,additions)
    edited = sorted(set(map(tuple,edges)) | set(map(tuple,additions)))
    before, after = partitions(nodes,edges,mode), partitions(nodes,edited,mode)
    keyed = lambda rows: {lo.ms.digest([n,e]):(n,e) for n,e in rows}
    old,new = keyed(before),keyed(after); unchanged = sorted(old.keys() & new.keys())
    results = []
    for parts in [old,new]:
        fits = [cached_solve(*parts[k],q,policy,cache) for k in sorted(parts) if k not in unchanged]
        results.append(fits)
    bf,ef = results
    base = sum(f['energy_upper_bound'] for _,f in bf)
    edit = sum(f['energy_upper_bound'] for _,f in ef)
    delta = edit-base
    fits = bf+ef
    stationary = all(f['has_stationary_solution'] for _,f in fits)
    identity = all(f['identity_error'] <= policy.identity_atol for _,f in fits)
    weight = float(sum(lo.terms(nodes,edges,q)[1][2]))
    status = ('nonstationary' if not stationary else 'identity_failure' if not identity else
              'negative_marginal' if delta < -policy.identity_atol else 'finite_estimate')
    return {'mode':mode,'q':q,'seed':policy.seed,'status':status,'cost':delta,
            'cost_per_frozen_weight':delta/weight if weight else None,'baseline_weight':weight,
            'baseline_affected_energy':base,'edited_affected_energy':edit,
            'baseline_solve_keys':[k for k,_ in bf], 'edited_solve_keys':[k for k,_ in ef],
            'all_nodes_accounted':len(nodes), 'baseline_partition_count':len(before),
            'edited_partition_count':len(after),'cancelled_partition_count':len(unchanged),
            'cancelled_partition_keys':unchanged,
            'largest_affected_support':max((len(f['support_nodes']) for _,f in fits),default=0),
            'global_optimum_claimed':False}


def ordering(rows, atol=1e-6):
    """Rows are alternatives, each with two schedule estimates; ranges are not CIs."""
    if not rows: return {'status':'empty','selected':None}
    if any(any(r['status']!='finite_estimate' for r in row) for row in rows):
        return {'status':'unscorable_pool','selected':None}
    low=[min(r['cost'] for r in row) for row in rows]
    high=[max(r['cost'] for r in row) for row in rows]
    if len(rows)==1: return {'status':'single_candidate','selected':0}
    if max(high)-min(low)<=atol:
        return {'status':'tie','selected':0}
    winner=min(range(len(rows)),key=lambda i:high[i])
    if all(high[winner]+atol<low[i] for i in range(len(rows)) if i!=winner):
        return {'status':'resolved_finite_order','selected':winner}
    return {'status':'unresolved','selected':None}
