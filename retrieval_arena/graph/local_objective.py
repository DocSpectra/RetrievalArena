"""Fixed-node magnetic descriptors and bounded unit-phase feasible solutions.

No semantic utility or global nonconvex optimality is asserted. See
docs/evaluations/local_objective.md for counting and admission rules.
"""
from dataclasses import asdict, dataclass
from collections import deque
import hashlib
import time

import numpy as np
from scipy.optimize import minimize

from . import magnetic_spectrum as ms

VERSION = 'retrieval_arena.local_objective.v1'


@dataclass(frozen=True)
class Policy:
    starts: int = 8
    maxiter: int = 2000
    stationarity: float = 1e-7
    identity_atol: float = 1e-9
    spectral_atol: float = 1e-9
    cluster_atol: float = 1e-8
    rank_atol: float = 1e-6
    seed: int = 3501


def terms(nodes, edges, q):
    admitted = ms.admit_graph(nodes, edges)
    if not np.isfinite(q) or not 0 <= q <= .5:
        raise ValueError('invalid-charge')
    nodes = admitted['nodes']; idx = {n: i for i, n in enumerate(nodes)}
    arcs = set(admitted['edges'])
    pairs = sorted({tuple(sorted((s, t))) for s, t in arcs})
    i = np.array([idx[s] for s, t in pairs], dtype=int)
    j = np.array([idx[t] for s, t in pairs], dtype=int)
    w = np.array([.5 * ((s, t) in arcs) + .5 * ((t, s) in arcs) for s, t in pairs])
    theta = np.array([2 * np.pi * q * (int((s, t) in arcs) - int((t, s) in arcs)) for s, t in pairs])
    return nodes, (i, j, w, theta)


def energy_gradient(phi, edge_terms):
    i, j, w, theta = edge_terms
    d = phi[i] - phi[j] - theta
    # This form avoids cancellation at a consistent phase assignment.
    energy = float(np.sum(4 * w * np.sin(d / 2) ** 2))
    deriv = 2 * w * np.sin(d)
    grad = np.zeros(len(phi))
    np.add.at(grad, i, deriv); np.add.at(grad, j, -deriv)
    return energy, grad


def anchors(nodes, edges):
    return [nodes.index(min(c)) for c in ms.weak_components(nodes, edges)]


def starts(nodes, p):
    """Identical, graph-independent starts on each pair; never use eigenvector angles."""
    result = [np.zeros(len(nodes))]
    for k in range(1, p.starts):
        result.append(np.array([2 * np.pi * int(hashlib.sha256(
            f'{p.seed}:{k}:{n}'.encode()).hexdigest()[:13], 16) / 16**13 for n in nodes]))
    return result


def solve(nodes, edges, q, p=Policy(), extra=()):
    if p.starts < 2 or p.maxiter < 1 or any(not np.isfinite(v) or v <= 0 for v in
            [p.stationarity, p.identity_atol, p.spectral_atol, p.cluster_atol, p.rank_atol]):
        raise ValueError('invalid-numerical-policy')
    nodes, ts = terms(nodes, edges, q)
    locked = anchors(nodes, edges)
    free = np.array([i for i in range(len(nodes)) if i not in locked], dtype=int)
    records = []; phases = []
    for k, initial in enumerate(starts(nodes, p) + list(extra)):
        initial = np.asarray(initial).copy()
        for comp in ms.weak_components(nodes, edges):
            ids = [nodes.index(n) for n in comp]; initial[ids] -= initial[nodes.index(min(comp))]
        def fun(x):
            phi = np.zeros(len(nodes)); phi[free] = x
            val, grad = energy_gradient(phi, ts)
            return val, grad[free]
        if free.size and len(ts[0]):
            fit = minimize(fun, initial[free], jac=True, method='L-BFGS-B',
                           options={'maxiter': p.maxiter, 'gtol': p.stationarity / 10,
                                    'ftol': 1e-15, 'maxls': 40})
            phi = np.zeros(len(nodes)); phi[free] = fit.x
            nit, success, message = int(fit.nit), bool(fit.success), str(fit.message)
        else:
            phi = np.zeros(len(nodes)); nit, success, message = 0, True, 'edgeless'
        val, grad = energy_gradient(phi, ts)
        stationarity = float(np.max(abs(grad))) if len(nodes) else 0.
        phases.append(phi)
        records.append({'start': k, 'initial_sha256': ms.digest(initial.tolist()),
                        'initial_phases': initial.tolist(), 'final_phases': phi.tolist(), 'energy': val,
                        'gradient_inf': stationarity, 'iterations': nit,
                        'optimizer_success': success, 'message': message,
                        'stationary': stationarity <= p.stationarity})
    qualified = [i for i, r in enumerate(records) if r['stationary']]
    # Preserve a nonstationary best feasible solution, but never qualify it.
    best = min(qualified or range(len(records)), key=lambda i: (records[i]['energy'], i))
    energies = [r['energy'] for r in records]
    spread = max(energies) - min(energies)
    result = {'energy_upper_bound': records[best]['energy'], 'best_start': best,
              'phases': phases[best].tolist(), 'starts': records,
              'all_stationary': len(qualified) == len(records),
              'restart_spread': spread, 'global_optimum_claimed': False,
              'lower_bound': 0., 'lower_bound_kind': 'nonnegativity',
              'zero_amplitude_phase_extraction': 'not used',
              'phase_uniqueness': 'not assumed; one arbitrary gauge per fixed component'}
    return result


def spectral(nodes, edges, q, p=Policy()):
    matrix = ms.normalized_magnetic_matrix(nodes, edges, q)
    _, ts = terms(nodes, edges, q)
    degree = np.zeros(len(nodes)); np.add.at(degree, ts[0], ts[2]); np.add.at(degree, ts[1], ts[2])
    vals, vecs = np.linalg.eigh(matrix)
    residual = float(np.linalg.norm(matrix @ vecs - vecs * vals, ord='fro'))
    orth = float(np.linalg.norm(vecs.conj().T @ vecs - np.eye(len(nodes)), ord='fro'))
    if not len(nodes):
        raise ValueError('empty-view')
    count = int(np.sum(vals - vals[0] <= p.cluster_atol))
    mass = np.sum(abs(vecs[:, :count]) ** 2, axis=1) / count
    # Unnormalized relaxation is a rigorous bound for unit magnitudes: ||z||²=n.
    lu = ms.unnormalized_magnetic_matrix(nodes, edges, q)
    ev = np.linalg.eigvalsh(lu)
    return {'lambda1': float(vals[0]), 'lambda2': float(vals[1]) if len(vals) > 1 else None,
            'ground_cluster_size': count, 'ground_cluster_complete': True,
            'cluster_gap': float(vals[count] - vals[0]) if count < len(vals) else None,
            'projector_mass': mass.tolist(), 'effective_support': float(1 / np.sum(mass**2)),
            'max_node_mass': float(max(mass)), 'residual_fro': residual,
            'orthogonality_fro': orth, 'degree': degree.tolist(),
            'qualified': bool(residual <= p.spectral_atol and orth <= p.spectral_atol and
                         vals[0] >= -p.spectral_atol and vals[-1] <= 2 + p.spectral_atol),
            'phase_energy_lower_bound': max(0., len(nodes) * (float(ev[0]) - p.spectral_atol)),
            'lower_bound_kind': 'n * (unnormalized lambda1 - residual allowance)',
            'normalization_mechanism': 'added edges change W, D and normalized old-edge entries'}


def ordinary(nodes, edges):
    adj = {n: set() for n in nodes}
    for s, t in edges: adj[s].add(t)
    reachable = 0; distance = 0
    for n in nodes:
        dist = {n: 0}; queue = deque([n])
        while queue:
            x = queue.popleft()
            for y in sorted(adj[x]):
                if y not in dist: dist[y] = dist[x] + 1; queue.append(y)
        reachable += len(dist) - 1; distance += sum(dist.values())
    arcs = set(map(tuple, edges))
    degrees = {n: sum(n in e for e in {tuple(sorted(x)) for x in arcs}) for n in nodes}
    return {'nodes': len(nodes), 'arcs': len(arcs), 'weak_components': len(ms.weak_components(nodes, edges)),
            'reciprocal_pairs': sum((t, s) in arcs for s, t in arcs) // 2,
            'max_outdegree': max(map(len, adj.values()), default=0),
            'max_undirected_degree': max(degrees.values(), default=0),
            'directed_reachable_pairs': reachable,
            'mean_finite_directed_distance': distance / reachable if reachable else None}


def eligible_additions(nodes, edges, additions):
    nodes, edges = ms.admit_graph(nodes, edges)['nodes'], set(map(tuple, edges))
    if len(set(map(tuple, additions))) != len(additions): raise ValueError('duplicate-addition')
    components = {n: k for k, c in enumerate(ms.weak_components(nodes, edges)) for n in c}
    for s, t in additions:
        if s not in nodes or t not in nodes: raise ValueError('outside-fixed-view')
        if s == t: raise ValueError('self-link')
        if (s, t) in edges: raise ValueError('existing-pair-no-new-edge')
        if (t, s) in edges or [t, s] in additions or (t, s) in additions:
            raise ValueError('reciprocal-completion-refused')
        if components[s] != components[t]: raise ValueError('different-baseline-component')
    return True


def compare(nodes, edges, additions, q, p=Policy()):
    """Fair paired effort with two shared cross-starts after the eight fixed starts."""
    eligible_additions(nodes, edges, additions)
    nodes = sorted(nodes); edited = sorted(set(map(tuple, edges)) | set(map(tuple, additions)))
    t0 = time.monotonic()
    b0 = solve(nodes, edges, q, p); e0 = solve(nodes, edited, q, p)
    # Reuse and preserve the original starts, adding exactly two paired starts.
    extra = [b0['phases'], e0['phases']]
    bp = solve(nodes, edges, q, p, extra=extra); ep = solve(nodes, edited, q, p, extra=extra)
    sb = spectral(nodes, edges, q, p); se = spectral(nodes, edited, q, p)
    bp['lower_bound'] = sb['phase_energy_lower_bound']; ep['lower_bound'] = se['phase_energy_lower_bound']
    bp['lower_bound_kind'] = ep['lower_bound_kind'] = sb['lower_bound_kind']
    _, oldterms = terms(nodes, edges, q); _, newterms = terms(nodes, additions, q)
    old_at_edit = energy_gradient(np.array(ep['phases']), oldterms)[0]
    added_at_edit = energy_gradient(np.array(ep['phases']), newterms)[0]
    fixed_cost = energy_gradient(np.array(bp['phases']), newterms)[0]
    denom = float(sum(oldterms[2])); added_weight = float(sum(newterms[2]))
    delta = ep['energy_upper_bound'] - bp['energy_upper_bound']
    uncertainty = bp['restart_spread'] + ep['restart_spread'] + p.rank_atol
    monotone = delta >= -p.identity_atol
    identity = abs(old_at_edit + added_at_edit - ep['energy_upper_bound']) <= p.identity_atol
    solver_ok = bp['all_stationary'] and ep['all_stationary']
    return {'q': q, 'graph_sha256': ms.graph_sha256(nodes, edges),
            'edited_graph_sha256': ms.graph_sha256(nodes, edited), 'additions': additions,
            'baseline': bp, 'edited': ep, 'spectral_baseline': sb, 'spectral_edited': se,
            'ordinary_baseline': ordinary(nodes, edges), 'ordinary_edited': ordinary(nodes, edited),
            'baseline_weight': denom, 'added_weight': added_weight, 'total_energy_change': delta,
            'old_energy_at_edited_solution': old_at_edit, 'added_energy_at_edited_solution': added_at_edit,
            'old_energy_change': old_at_edit - bp['energy_upper_bound'],
            'fixed_baseline_phase_added_cost_diagnostic': fixed_cost,
            'marginal_cost_per_baseline_weight': delta / denom if denom else None,
            'marginal_cost_per_added_weight': delta / added_weight if added_weight else None,
            'energy_decomposition_identity': identity,
            'computed_additive_monotonicity': monotone,
            'numerical_comparison_failure': not monotone or not identity or not solver_ok,
            'restart_uncertainty': uncertainty,
            'qualified': monotone and identity and solver_ok and sb['qualified'] and se['qualified'],
            'effort': {'fixed_starts_per_graph': p.starts, 'cross_starts_per_graph': 2,
                       'initial_scout_solves_per_graph': p.starts,
                       'total_minimizations_per_graph': 2 * p.starts + 2},
            'runtime_seconds': time.monotonic() - t0}


def ranking_verdict(comparisons, p=Policy()):
    """Conservative operational gate, not a rigorous interval for global optima."""
    ordered = sorted(comparisons, key=lambda c: (c['total_energy_change'], ms.digest(c['additions'])))
    pairs = []
    for a, b in zip(ordered, ordered[1:]):
        gap = b['total_energy_change'] - a['total_energy_change']
        u = a['restart_uncertainty'] + b['restart_uncertainty']
        pairs.append({'left': a['additions'], 'right': b['additions'], 'gap': gap,
                      'uncertainty_sum': u, 'resolved': gap > u,
                      'numerical_tie': abs(gap) <= p.rank_atol})
    return {'qualified_comparisons': all(c['qualified'] for c in comparisons),
            'adjacent_ranks': pairs,
            'stable_beyond_restart_uncertainty': bool(pairs) and all(x['resolved'] or x['numerical_tie']
                                                                      and x['uncertainty_sum'] <= 4*p.rank_atol for x in pairs),
            'global_ranking_claimed': False}
