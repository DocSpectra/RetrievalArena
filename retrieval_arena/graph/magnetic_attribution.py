"""Invariant attribution of normalized magnetic ground modes, a versioned graph diagnostic.

The contract is docs/evaluations/magnetic_attribution.md. The operator, dense solver and numerical
qualification are those of normalized_magnetic_spectrum.v1 (magnetic_spectrum.py, unchanged and imported).
This module adds:
- deterministic selection of directed cycles on nonreciprocal arcs, from topology alone;
- declared arc-reversal controls with exact per-pair structural assertions;
- complete ground-cluster projectors, node mass and per-edge energy;
- the fixed-mode / relaxation account of a finite ground-eigenvalue change.

Every field is descriptive. Edge energy says where a mode's energy resides, not what caused an eigenvalue
change; no field is a retrieval or answer score or an optimization target.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import hashlib
import io
import json
from pathlib import Path

import numpy as np

from retrieval_arena.graph import magnetic_spectrum as ms

VERSION = 'magnetic_attribution.v1'
CONFIG_SCHEMA = 'retrieval_arena.magnetic_attribution_config.v1'
CONTROLS = ('matched_cycle_reversal', 'global_reversal', 'forest_reorientation')
JOB_KINDS = ('cycle_selection', 'pair', 'single')
SELECTION_RULE = ('simple directed cycles using only nonreciprocal arcs; lengths searched in the declared order; '
                  'start nodes ascending; a cycle is rooted at its smallest id and every other node id is larger '
                  '(canonical rotation, so each cycle is met once); successors ascending; depth-first; one expansion '
                  '= one partial path created, the one-node start path included; stop at max_cycles or at the '
                  'expansion budget. Reads node ids and arcs only.')
ENERGY_POLICY = ('f = U / sqrt(d) (zero on isolates); energy({i,j}) = W_ij sum_r |f_ir - exp(i theta_ij) f_jr|^2 / k '
                 'over unordered edges i < j in view order, theta_ij = 2 pi q (A_ij - A_ji) (the magnetic_spectrum '
                 'phase convention); mass_i = sum_r |U_ir|^2 / k; energies sum to the cluster mean eigenvalue')
CLUSTER_POLICY = ('ground cluster = every eigenvalue within cluster_absolute of lambda1 in the full dense spectrum; '
                  'its boundary is qualified when the cluster is the whole space or the next gap exceeds '
                  'max(cluster_absolute, resolution_factor * cluster residual)')
RESOLUTION_POLICY = ('resolved iff |delta lambda1| > max(resolution_floor, resolution_factor * (r_A + r_B)), r = max '
                     'over the ground cluster of ||L u - lambda u||_2 for unit u from the qualified dense solve')


@dataclass(frozen=True)
class AttributionConfig:
    cluster_absolute: float = 1e-8
    resolution_floor: float = 1e-8
    resolution_factor: float = 10.0
    identity_absolute: float = 1e-10
    trophic_absolute: float = 1e-9
    permutation_eigenvalue_absolute: float = 1e-10
    permutation_field_absolute: float = 1e-8
    report_modes: int = 6
    top_items: int = 10


def validate_config(config):
    if not isinstance(config, AttributionConfig):
        raise ValueError('invalid-attribution-config')
    for key in ('cluster_absolute', 'resolution_floor', 'resolution_factor', 'identity_absolute', 'trophic_absolute',
                'permutation_eigenvalue_absolute', 'permutation_field_absolute'):
        value = getattr(config, key)
        if not np.isfinite(value) or value <= 0:
            raise ValueError('invalid-tolerance:' + key)
    for key in ('report_modes', 'top_items'):
        if not isinstance(getattr(config, key), int) or getattr(config, key) < 1:
            raise ValueError('invalid-count:' + key)
    return config


def code_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


# ------------------------------------------------------------------ cycle selection (topology only)

def nonreciprocal_successors(edges):
    es = set(edges)
    succ = defaultdict(list)
    for s, t in sorted(es):
        if (t, s) not in es:
            succ[s].append(t)
    return succ


def cycle_arcs(cycle):
    return [(cycle[i], cycle[(i + 1) % len(cycle)]) for i in range(len(cycle))]


def canonical_cycle(cycle):
    i = cycle.index(min(cycle))
    return tuple(cycle[i:] + cycle[:i])


def nonreciprocal_cycles(nodes, edges, lengths=(3, 4, 5, 6), max_cycles=4, budget=100000):
    """Deterministic, budgeted enumeration of simple directed cycles on nonreciprocal arcs (SELECTION_RULE)."""
    lengths = list(lengths)
    if not lengths or any(not isinstance(x, int) or x < 3 for x in lengths) or len(set(lengths)) != len(lengths):
        raise ValueError('invalid-cycle-lengths')
    if not isinstance(max_cycles, int) or max_cycles < 1 or not isinstance(budget, int) or budget < 1:
        raise ValueError('invalid-cycle-search-limits')
    succ = nonreciprocal_successors(edges)
    order = sorted(nodes)
    found, seen, expansions = [], set(), 0
    per_length = {}
    status = None
    for length in lengths:
        before = len(found)
        for start in order:
            if expansions >= budget:
                status = 'budget_exhausted'
                break
            expansions += 1
            path, on, stack = [start], {start}, [iter(succ[start])]
            while stack and status is None:
                nxt = next(stack[-1], None)
                if nxt is None:
                    stack.pop()
                    on.discard(path.pop())
                    continue
                if len(path) == length:
                    if nxt == start:
                        cyc = canonical_cycle(path)
                        if cyc not in seen:
                            seen.add(cyc)
                            found.append(list(cyc))
                            if len(found) >= max_cycles:
                                status = 'max_cycles_reached'
                    continue
                if nxt <= start or nxt in on:
                    continue
                if expansions >= budget:
                    status = 'budget_exhausted'
                    break
                expansions += 1
                path.append(nxt)
                on.add(nxt)
                stack.append(iter(succ[nxt]))
            if status:
                break
        per_length[str(length)] = len(found) - before
        if status:
            break
    return {'rule': SELECTION_RULE, 'parameters': {'lengths': lengths, 'max_cycles': max_cycles, 'budget': budget},
            'cycles': found, 'arcs': [[list(a) for a in cycle_arcs(c)] for c in found],
            'found_per_length': per_length, 'expansions': expansions,
            'status': status or 'search_complete',
            'nonreciprocal_arcs': sum(len(v) for v in succ.values()),
            'note': 'a budget stop is not evidence that no further cycles exist'}


def validate_cycle(edges, cycle):
    """Refuse anything but a simple directed cycle (length >= 3) of nonreciprocal arcs present in the graph."""
    cycle = list(cycle)
    if len(cycle) < 3 or len(set(cycle)) != len(cycle):
        raise ValueError('not-a-simple-cycle')
    es = set(edges)
    for s, t in cycle_arcs(cycle):
        if (s, t) not in es:
            raise ValueError('cycle-arc-absent')
        if (t, s) in es:
            raise ValueError('reciprocal-cycle-arc')
    return cycle_arcs(cycle)


def arcs_form_cycle(arcs):
    arcs = [tuple(a) for a in arcs]
    succ = {}
    for s, t in arcs:
        if s in succ:
            return None
        succ[s] = t
    if len(arcs) < 3 or set(succ) != set(succ.values()):
        return None
    start = min(succ)
    cycle, x = [start], succ[start]
    while x != start:
        if x in cycle:
            return None
        cycle.append(x)
        x = succ[x]
    return cycle if len(cycle) == len(arcs) else None


def reverse_arcs(edges, arcs):
    """Reverse the declared one-way arcs; refuses an absent, duplicated or reciprocal arc."""
    es = set(edges)
    arcs = [tuple(a) for a in arcs]
    if len(set(arcs)) != len(arcs):
        raise ValueError('reversal-duplicate-arc')
    for s, t in arcs:
        if (s, t) not in es:
            raise ValueError('reversal-arc-absent')
        if (t, s) in es:
            raise ValueError('reversal-reciprocal-arc')
    return sorted((es - set(arcs)) | {(t, s) for s, t in arcs})


# ------------------------------------------------------------------ structural controls

def _degrees(nodes, edges):
    out_d, in_d = {v: 0 for v in nodes}, {v: 0 for v in nodes}
    for s, t in edges:
        out_d[s] += 1
        in_d[t] += 1
    return out_d, in_d


def weak_partition(nodes, edges):
    return sorted(sorted(c) for c in ms.weak_components(nodes, edges))


def scc_partition(nodes, edges):
    if not nodes:
        return []
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    idx = {v: i for i, v in enumerate(nodes)}
    g = csr_matrix((np.ones(len(edges)), ([idx[s] for s, _ in edges], [idx[t] for _, t in edges])),
                   shape=(len(nodes), len(nodes)))
    _, labels = connected_components(g, directed=True, connection='strong')
    groups = defaultdict(list)
    for v, lab in zip(nodes, labels):
        groups[int(lab)].append(v)
    return sorted(sorted(g) for g in groups.values())


def undirected_weights(nodes, edges):
    a = ms._adjacency(nodes, edges)
    return 0.5 * (a + a.T)


def phases(nodes, edges, q):
    a = ms._adjacency(nodes, edges)
    return 2 * np.pi * q * (a - a.T)


def _flatten(prefix, value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f'{prefix}.{k}' if prefix else k, v, out)
    else:
        out[prefix] = value
    return out


def control_checks(nodes_a, edges_a, nodes_b, edges_b, control, changed_arcs, config=AttributionConfig()):
    """Exact per-pair assertions for a declared control. A failed check quarantines the pair."""
    if control not in CONTROLS:
        raise ValueError('invalid-control')
    changed = [tuple(a) for a in changed_arcs]
    if control == 'global_reversal':
        expected_b = sorted((t, s) for s, t in edges_a)
    else:
        expected_b = reverse_arcs(edges_a, changed)
    out_a, in_a = _degrees(nodes_a, edges_a)
    out_b, in_b = _degrees(nodes_b, edges_b)
    same_nodes = list(nodes_a) == list(nodes_b)
    w_equal = same_nodes and np.array_equal(undirected_weights(nodes_a, edges_a), undirected_weights(nodes_b, edges_b))
    desc_a, desc_b = ms.structural_descriptors(nodes_a, edges_a), ms.structural_descriptors(nodes_b, edges_b)
    troph_a, troph_b = ms.trophic_incoherence(nodes_a, edges_a), ms.trophic_incoherence(nodes_b, edges_b)
    troph_diff = (abs(troph_a['value'] - troph_b['value'])
                  if troph_a['value'] is not None and troph_b['value'] is not None else None)
    q0_equal = same_nodes and np.array_equal(ms.normalized_magnetic_matrix(nodes_a, edges_a, 0.0),
                                             ms.normalized_magnetic_matrix(nodes_b, edges_b, 0.0))
    checks = {
        'declared_change_reproduces_b': list(expected_b) == list(edges_b),
        'distinct_graph_hashes': ms.graph_sha256(nodes_a, edges_a) != ms.graph_sha256(nodes_b, edges_b),
        'ordered_nodes_equal': same_nodes,
        'node_count_equal': len(nodes_a) == len(nodes_b),
        'arc_count_equal': len(edges_a) == len(edges_b),
        'undirected_w_equal': bool(w_equal),
        'q0_normalized_matrix_equal': bool(q0_equal),
        'weak_partition_equal': weak_partition(nodes_a, edges_a) == weak_partition(nodes_b, edges_b),
        'scc_partition_equal': scc_partition(nodes_a, edges_a) == scc_partition(nodes_b, edges_b),
    }
    degree = {
        'in_degree_equal': in_a == in_b,
        'out_degree_equal': out_a == out_b,
        'weighted_degree_equal': bool(w_equal),
        'reciprocal_arc_count_equal': desc_a['reciprocal_arcs'] == desc_b['reciprocal_arcs'],
        'source_count_equal': desc_a['sources'] == desc_b['sources'],
        'sink_count_equal': desc_a['sinks'] == desc_b['sinks'],
        'outdegree_gini_equal': desc_a['outdegree_gini'] == desc_b['outdegree_gini'],
        'trophic_incoherence_within_tolerance': troph_diff is not None and troph_diff <= config.trophic_absolute,
    }
    if control == 'matched_cycle_reversal':
        checks.update(degree)
        cycle = arcs_form_cycle(changed)
        checks['changed_arcs_form_directed_cycle'] = cycle is not None
        checks['changed_arcs_nonreciprocal_in_a'] = all((t, s) not in set(edges_a) for s, t in changed)
    elif control == 'global_reversal':
        checks['in_out_degrees_swapped'] = in_a == out_b and out_a == in_b
    else:
        n_comp = len(ms.weak_components(nodes_a, edges_a))
        undirected = {frozenset(e) for e in edges_a}
        checks['undirected_graph_is_forest'] = (len(undirected) == len(edges_a)
                                                and len(undirected) == len(nodes_a) - n_comp)
    fa, fb = _flatten('', desc_a, {}), _flatten('', desc_b, {})
    changed_descriptors = sorted(k for k in set(fa) | set(fb) if fa.get(k) != fb.get(k))
    if troph_diff is None or troph_diff > config.trophic_absolute:
        changed_descriptors.append('trophic_incoherence')
    return {'control': control, 'checks': checks, 'passed': all(checks.values()),
            'degree_matched': all(degree[k] for k in ('in_degree_equal', 'out_degree_equal')),
            'degree_checks': degree,
            'changed_arcs': [list(a) for a in changed] if control != 'global_reversal' else 'all',
            'trophic_incoherence': {'a': troph_a['value'], 'b': troph_b['value'], 'abs_diff': troph_diff,
                                    'tolerance': config.trophic_absolute,
                                    'invariant': 'W and in - out unchanged, so the level system and its minimized '
                                                 'objective are unchanged' if control == 'matched_cycle_reversal'
                                    else None},
            'descriptors_a': desc_a, 'descriptors_b': desc_b, 'descriptors_that_change': changed_descriptors,
            'quarantined': not all(checks.values())}


# ------------------------------------------------------------------ ground cluster, mass and edge energy

def ground_cluster(values, residual, config=AttributionConfig()):
    """Cluster rank, width and next gap from an ascending full spectrum; boundary qualification."""
    values = np.asarray(values, dtype=np.float64)
    n = values.size
    if n == 0:
        return {'rank': 0, 'boundary_qualified': False, 'reasons': ['empty_view']}
    k = int(np.sum(values - values[0] <= config.cluster_absolute))
    gap = float(values[k] - values[k - 1]) if k < n else None
    threshold = max(config.cluster_absolute, config.resolution_factor * residual)
    qualified = gap is None or gap > threshold
    return {'rank': k, 'width': float(values[k - 1] - values[0]), 'next_gap': gap, 'boundary_threshold': threshold,
            'boundary_qualified': bool(qualified), 'mean_eigenvalue': float(values[:k].mean()),
            'closes_at_spectrum_end': gap is None,
            'reasons': [] if qualified else ['cluster_boundary_unresolved']}


def mode_fields(w, theta, u):
    """Node mass and unordered-edge energy of an orthonormal block u (n x k); invariant to u -> u Q, Q unitary."""
    u = np.asarray(u, dtype=np.complex128)
    if u.ndim == 1:
        u = u[:, None]
    k = u.shape[1]
    d = w.sum(axis=1)
    r = np.zeros_like(d)
    r[d > 0] = 1.0 / np.sqrt(d[d > 0])
    f = u * r[:, None]
    mass = (np.abs(u) ** 2).sum(axis=1) / k
    i, j = np.nonzero(np.triu(w, 1))
    diff = f[i] - np.exp(1j * theta[i, j])[:, None] * f[j]
    energy = w[i, j] * (np.abs(diff) ** 2).sum(axis=1) / k
    return mass, i, j, energy


def effective_support(mass):
    s = float(np.sum(mass ** 2))
    return 1.0 / s if s > 0 else None


def _solve(nodes, edges, q, spec):
    """Full qualified dense eigensystem through magnetic_spectrum.spectrum (modes = n)."""
    rec = ms.spectrum(nodes, edges, q, ms.SpectrumConfig(**dict(asdict(spec), modes=max(1, len(nodes)), solver='dense')))
    return rec


class _Side:
    """One graph view at one q: qualified spectrum, ground cluster, fields and artifacts."""

    def __init__(self, nodes, edges, q, spec, config):
        self.nodes, self.edges, self.q = nodes, edges, q
        self.rec = _solve(nodes, edges, q, spec)
        self.qualified = self.rec['status'] == 'qualified'
        self.values = np.asarray(self.rec['eigenvalues'], dtype=np.float64)
        vectors = self.rec.get('_vectors')
        self.matrix = ms.normalized_magnetic_matrix(nodes, edges, q)
        self.w = undirected_weights(nodes, edges)
        self.theta = phases(nodes, edges, q)
        k0 = int(np.sum(self.values - self.values[0] <= config.cluster_absolute)) if self.values.size else 0
        self.u = vectors[:, :k0] if vectors is not None and k0 else None
        self.residual = (float(max(np.linalg.norm(self.matrix @ self.u[:, r] - self.values[r] * self.u[:, r])
                                   for r in range(k0))) if self.u is not None else None)
        self.cluster = ground_cluster(self.values, self.residual or 0.0, config) if self.values.size else \
            {'rank': 0, 'boundary_qualified': False, 'reasons': ['empty_view']}
        self.available = self.qualified and self.cluster['boundary_qualified']
        if self.u is not None:
            self.mass, self.i, self.j, self.energy = mode_fields(self.w, self.theta, self.u)
        else:
            self.mass = self.energy = None
        self.resolution = max(config.resolution_floor, config.resolution_factor * (self.residual or 0.0))

    def summary(self, config, changed_edges=None, slug=None):
        n = len(self.nodes)
        out = {'numerics': {key: self.rec.get(key) for key in (
                   'status', 'reasons', 'solver', 'dtype', 'n', 'residual_relative', 'orthogonality_max_abs',
                   'hermitian_max_abs', 'spectrum_min', 'spectrum_max', 'numerical_zero_count')},
               'lowest_eigenvalues': [float(x) for x in self.values[:config.report_modes]],
               'near_zero': [bool(abs(x) <= ms.SpectrumConfig().near_zero_absolute)
                             for x in self.values[:config.report_modes]],
               'lambda1': float(self.values[0]) if self.values.size else None,
               'lambda2': float(self.values[1]) if self.values.size > 1 else None,
               'ground_residual_abs': self.residual, 'cluster': self.cluster,
               'attribution_status': 'available' if self.available else 'unavailable',
               'attribution_reasons': ([] if self.available else
                                       (['numerical_qualification_failed'] if not self.qualified else [])
                                       + list(self.cluster.get('reasons', [])))}
        if self.mass is None:
            return out
        d = self.w.sum(axis=1)
        vol = d.sum()
        dmass = d / vol if vol > 0 else np.zeros_like(d)
        total = float(self.energy.sum())
        ident = abs(total - self.cluster['mean_eigenvalue'])
        resolvable = total > self.resolution
        support = effective_support(self.mass)
        ids = self.nodes
        order_e = sorted(range(len(self.energy)), key=lambda e: (-self.energy[e], ids[self.i[e]], ids[self.j[e]]))
        order_m = sorted(range(n), key=lambda x: (-self.mass[x], ids[x]))
        fields = {
            'energy_total': total, 'cluster_mean_eigenvalue': self.cluster['mean_eigenvalue'],
            'energy_sum_residual': ident, 'energy_sum_identity_ok': ident <= config.identity_absolute,
            'energy_total_label': ('cluster mean eigenvalue (k > 1), not lambda1' if self.cluster['rank'] > 1
                                   else 'lambda1 (simple ground mode)'),
            'energy_resolution': self.resolution, 'energy_fractions_available': bool(resolvable),
            'mass_sum': float(self.mass.sum()), 'effective_support': support,
            'effective_support_fraction': support / n if support else None,
            'degree_mass_tv_distance': float(0.5 * np.abs(self.mass - dmass).sum()),
            'degree_mass_effective_support': effective_support(dmass),
            'isolate_mass': float(self.mass[d == 0].sum()),
            'top_mass_nodes': [{'node': ids[x], 'mass': float(self.mass[x]), 'degree_mass': float(dmass[x])}
                               for x in order_m[:config.top_items]],
            'top_energy_edges': [{'edge': [ids[self.i[e]], ids[self.j[e]]], 'energy': float(self.energy[e]),
                                  'fraction': float(self.energy[e] / total) if resolvable else None}
                                 for e in order_e[:config.top_items]],
        }
        if changed_edges is not None:
            key = {frozenset(e) for e in changed_edges}
            on = np.array([frozenset((ids[a], ids[b])) in key for a, b in zip(self.i, self.j)], dtype=bool)
            cnodes = {x for e in changed_edges for x in e}
            nmask = np.array([v in cnodes for v in ids], dtype=bool)
            outside = [e for e in order_e if not on[e]]
            fields['changed_cycle'] = {
                'nodes': len(cnodes), 'edges': int(on.sum()),
                'mass_on_cycle_nodes': float(self.mass[nmask].sum()), 'node_share': float(nmask.sum() / n),
                'degree_volume_share': float(d[nmask].sum() / vol) if vol > 0 else None,
                'energy_on_changed_edges': float(self.energy[on].sum()),
                'energy_fraction_on_changed_edges': float(self.energy[on].sum() / total) if resolvable else None,
                'w_share_of_changed_edges': float(self.w[self.i[on], self.j[on]].sum() / self.w[self.i, self.j].sum()),
                'highest_energy_edge_outside_changed': (
                    {'edge': [ids[self.i[outside[0]]], ids[self.j[outside[0]]]],
                     'energy': float(self.energy[outside[0]]),
                     'fraction': float(self.energy[outside[0]] / total) if resolvable else None}
                    if outside else None)}
        out['fields'] = fields
        if slug:
            out['artifacts'] = {'nodes_csv': f'tables/{slug}.nodes.csv', 'edges_csv': f'tables/{slug}.edges.csv',
                                'cluster_vectors_npy': f'modes/{slug}.npy'}
        return out

    def artifacts(self, slug):
        if self.mass is None:
            return {}
        d = self.w.sum(axis=1)
        vol = d.sum()
        nodes_csv = ['node,weighted_degree,mass,degree_mass']
        nodes_csv += [f'{v},{d[x]!r},{float(self.mass[x])!r},{float(d[x] / vol) if vol > 0 else 0.0!r}'
                      for x, v in enumerate(self.nodes)]
        edges_csv = ['i,j,w,theta_ij,energy']
        edges_csv += [f'{self.nodes[a]},{self.nodes[b]},{self.w[a, b]!r},{float(self.theta[a, b])!r},{float(e)!r}'
                      for a, b, e in zip(self.i, self.j, self.energy)]
        buf = io.BytesIO()
        np.save(buf, np.ascontiguousarray(self.u), allow_pickle=False)
        return {f'tables/{slug}.nodes.csv': ('\n'.join(nodes_csv) + '\n').encode(),
                f'tables/{slug}.edges.csv': ('\n'.join(edges_csv) + '\n').encode(),
                f'modes/{slug}.npy': buf.getvalue()}


def finite_change(a, b, changed_edges, config=AttributionConfig()):
    """delta lambda1 = u_A* (L_B - L_A) u_A + (lambda1_B - u_A* L_B u_A) for simple ground modes on both sides."""
    reasons = []
    if not (a.available and b.available):
        reasons.append('attribution_unavailable_on_a_side')
    if a.cluster.get('rank') != 1 or b.cluster.get('rank') != 1:
        reasons.append('clustered_ground_mode')
    if reasons:
        return {'status': 'unavailable', 'reasons': reasons}
    ua = a.u[:, 0]
    delta = float(b.values[0] - a.values[0])
    fixed = float(np.real(ua.conj() @ ((b.matrix - a.matrix) @ ua)))
    relax = float(b.values[0] - np.real(ua.conj() @ (b.matrix @ ua)))
    _, i, j, e_a = mode_fields(a.w, a.theta, ua)
    _, _, _, e_b = mode_fields(b.w, b.theta, ua)
    per_edge = e_b - e_a
    ids = a.nodes
    key = {frozenset(e) for e in changed_edges}
    on = np.array([frozenset((ids[x], ids[y])) in key for x, y in zip(i, j)], dtype=bool)
    rows = sorted(({'edge': [ids[x], ids[y]], 'fixed_mode_energy_change': float(v)}
                   for x, y, v, c in zip(i, j, per_edge, on) if c), key=lambda r: r['edge'])
    account_residual = abs(fixed + relax - delta)
    return {'status': 'computed', 'delta_lambda1': delta, 'fixed_mode_term': fixed, 'relaxation_term': relax,
            'account_residual': account_residual, 'account_ok': account_residual <= config.identity_absolute,
            'relaxation_nonpositive': relax <= config.identity_absolute,
            'fixed_mode_edge_sum': float(per_edge[on].sum()),
            'fixed_mode_edge_sum_residual': abs(float(per_edge[on].sum()) - fixed),
            'unchanged_edges_fixed_mode_max_abs': float(np.max(np.abs(per_edge[~on]))) if (~on).any() else 0.0,
            'per_changed_edge': rows,
            'meaning': 'Rayleigh-energy decomposition of the declared intervention: the old mode held fixed, then '
                       'its relaxation; not an independent causal effect per edge'}


def permutation_check(nodes, edges, q, spec, config, seed, reference):
    """Recompute one side on a permuted node order; compare spectrum, cluster projector, mass and edge energy."""
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(nodes))
    pnodes = [nodes[x] for x in perm]
    side = _Side(pnodes, edges, q, spec, config)
    out = {'seed': seed, 'q': q, 'eigenvalue_max_abs_diff': float(np.max(np.abs(side.values - reference.values)))}
    if side.u is None or reference.u is None or side.u.shape[1] != reference.u.shape[1]:
        out.update(status='unavailable', reasons=['cluster_rank_differs_or_missing'])
        return out
    back = np.empty_like(side.u)
    back[perm] = side.u
    out['cluster_rank'] = int(side.u.shape[1])
    out['projector_distance'] = ms.subspace_distance(reference.u, back)
    mass_back = np.empty_like(side.mass)
    mass_back[perm] = side.mass
    out['mass_max_abs_diff'] = float(np.max(np.abs(mass_back - reference.mass)))
    ref = {frozenset((nodes[x], nodes[y])): e for x, y, e in zip(reference.i, reference.j, reference.energy)}
    got = {frozenset((pnodes[x], pnodes[y])): e for x, y, e in zip(side.i, side.j, side.energy)}
    out['edge_sets_equal'] = set(ref) == set(got)
    out['energy_max_abs_diff'] = float(max(abs(ref[k] - got[k]) for k in ref)) if out['edge_sets_equal'] and ref else None
    out['passed'] = bool(out['eigenvalue_max_abs_diff'] <= config.permutation_eigenvalue_absolute
                         and out['projector_distance'] <= config.permutation_field_absolute
                         and out['mass_max_abs_diff'] <= config.permutation_field_absolute
                         and out['edge_sets_equal']
                         and (out['energy_max_abs_diff'] or 0.0) <= config.permutation_field_absolute)
    out['status'] = 'computed'
    return out


# ------------------------------------------------------------------ jobs

def validate_jobs_config(config):
    if not isinstance(config, dict) or config.get('schema_version') != CONFIG_SCHEMA:
        raise ValueError('magnetic-attribution-config-schema')
    if set(config) != {'schema_version', 'spectrum_config', 'attribution_config', 'jobs'}:
        raise ValueError('magnetic-attribution-config-keys')
    spec = ms.validate_config(ms.SpectrumConfig(**config['spectrum_config']))
    if spec.solver != 'dense':
        raise ValueError('magnetic-attribution-requires-dense')
    attr = validate_config(AttributionConfig(**config['attribution_config']))
    ids, selections = set(), set()
    allowed = {'cycle_selection': {'job_id', 'kind', 'bundle', 'view', 'lengths', 'max_cycles', 'budget'},
               'pair': {'job_id', 'kind', 'bundle_a', 'bundle_b', 'view', 'q', 'control', 'changed_arcs',
                        'selection_ref', 'permutation_seed'},
               'single': {'job_id', 'kind', 'bundle', 'views', 'q'}}
    for job in config['jobs']:
        kind = job.get('kind')
        if kind not in JOB_KINDS or 'job_id' not in job or set(job) - allowed[kind]:
            raise ValueError('magnetic-attribution-job-keys')
        if job['job_id'] in ids:
            raise ValueError('magnetic-attribution-duplicate-job')
        ids.add(job['job_id'])
        for key in ('bundle', 'bundle_a', 'bundle_b'):
            if key in job and (not isinstance(job[key], str) or job[key].startswith('/')
                               or '..' in Path(job[key]).parts):
                raise ValueError('magnetic-attribution-bundle-path')
        if kind == 'cycle_selection':
            selections.add(job['job_id'])
        if kind == 'pair':
            if job.get('control') not in CONTROLS:
                raise ValueError('magnetic-attribution-control')
            ref = job.get('selection_ref')
            if ref is not None and ref.get('job_id') not in selections:
                raise ValueError('magnetic-attribution-selection-ref')
        for q in job.get('q', []):
            if not isinstance(q, (int, float)) or not np.isfinite(q):
                raise ValueError('invalid-q')
    return spec, attr


def _view_of(bundle, view):
    admitted = ms.validate_bundle(bundle)
    return ms.select_view(admitted['nodes'], admitted['edges'], view, bundle.get('node_sets'))


def _common(spec, attr):
    return {'metric_version': VERSION, 'code_sha256': code_sha256(), 'spectrum_metric_version': ms.VERSION,
            'spectrum_code_sha256': ms.code_sha256(), 'normalization': ms.NORMALIZATION,
            'phase_policy': ms.PHASE_POLICY, 'weight_policy': ms.WEIGHT_POLICY, 'energy_policy': ENERGY_POLICY,
            'cluster_policy': CLUSTER_POLICY, 'resolution_policy': RESOLUTION_POLICY,
            'spectrum_config': asdict(spec), 'attribution_config': asdict(attr),
            'config_sha256': ms.digest({'spectrum': asdict(spec), 'attribution': asdict(attr)}),
            'optimization_admitted': False, 'scientific_utility': 'unvalidated'}


def run_jobs(config, input_root):
    """Run every job of a validated config over bundles under input_root. Returns (records, artifacts)."""
    spec, attr = validate_jobs_config(config)
    bundles, sides, artifacts, selections, rows = {}, {}, {}, {}, []

    def load(path):
        if path not in bundles:
            bundles[path] = json.loads((Path(input_root) / path).read_bytes())
        return bundles[path]

    def side(nodes, edges, record, q):
        key = (record['view_graph_sha256'], float(q))
        if key not in sides:
            s = _Side(nodes, edges, float(q), spec, attr)
            slug = f"{record['view_graph_sha256'][:16]}_q{float(q):.2f}"
            artifacts.update(s.artifacts(slug))
            sides[key] = (s, slug)
        return sides[key]

    for job in config['jobs']:
        base = dict(_common(spec, attr), job_id=job['job_id'], kind=job['kind'])
        if job['kind'] == 'cycle_selection':
            b = load(job['bundle'])
            nodes, edges, record = _view_of(b, job['view'])
            result = nonreciprocal_cycles(nodes, edges, job.get('lengths', (3, 4, 5, 6)), job.get('max_cycles', 4),
                                          job.get('budget', 100000))
            selections[job['job_id']] = (record['view_graph_sha256'], result)
            rows.append(dict(base, graph_id=b['graph_id'], canonical_graph_sha256=b['canonical_graph_sha256'],
                             view=job['view'], selection=record, result=result, spectral_inputs_read=False))
            continue
        if job['kind'] == 'single':
            b = load(job['bundle'])
            views = []
            for view in job['views']:
                nodes, edges, record = _view_of(b, view)
                per_q = {}
                for q in job['q']:
                    s, slug = side(nodes, edges, record, q)
                    per_q[f'{float(q):.2f}'] = s.summary(attr, slug=slug)
                views.append({'view': view, 'selection': record,
                              'structure': ms.structural_descriptors(nodes, edges), 'q': per_q})
            rows.append(dict(base, graph_id=b['graph_id'], canonical_graph_sha256=b['canonical_graph_sha256'],
                             views=views))
            continue
        ba, bb = load(job['bundle_a']), load(job['bundle_b'])
        na, ea, ra = _view_of(ba, job['view'])
        nb, eb, rb = _view_of(bb, job['view'])
        changed = [tuple(a) for a in job.get('changed_arcs', [])]
        if job['control'] == 'matched_cycle_reversal':
            cycle = arcs_form_cycle(changed)
            if cycle is None:
                raise ValueError('changed-arcs-not-a-cycle')
            validate_cycle(ea, cycle)
        ref = job.get('selection_ref')
        if ref is not None:
            view_hash, result = selections[ref['job_id']]
            if view_hash != ra['view_graph_sha256']:
                raise ValueError('selection-view-mismatch')
            if sorted(tuple(a) for a in result['arcs'][ref['index']]) != sorted(changed):
                raise ValueError('selection-arcs-mismatch')
        controls = control_checks(na, ea, nb, eb, job['control'], changed, attr)
        changed_edges = ([sorted(a) for a in changed] if job['control'] != 'global_reversal' else None)
        per_q = {}
        for q in job['q']:
            sa, slug_a = side(na, ea, ra, q)
            sb, slug_b = side(nb, eb, rb, q)
            delta = float(sb.values[0] - sa.values[0])
            resid = (sa.residual or 0.0) + (sb.residual or 0.0)
            tol = max(attr.resolution_floor, attr.resolution_factor * resid)
            both = sa.qualified and sb.qualified
            block = {
                'a': sa.summary(attr, changed_edges, slug_a), 'b': sb.summary(attr, changed_edges, slug_b),
                'delta_lambda1': {'raw': delta, 'tolerance': tol, 'resolved': (abs(delta) > tol) if both else None,
                                  'baseline_lambda1': float(sa.values[0]),
                                  'relative_to_baseline': delta / sa.values[0] if abs(sa.values[0]) > tol else None,
                                  'note': 'numerical resolution is not practical importance'},
                'delta_lambda2': (float(sb.values[1] - sa.values[1]) if sa.values.size > 1 else None),
                'lowest_eigenvalues_max_abs_diff': float(np.max(np.abs(
                    sb.values[:attr.report_modes] - sa.values[:attr.report_modes]))),
                'finite_change': (finite_change(sa, sb, changed_edges, attr) if changed_edges is not None
                                  else {'status': 'unavailable', 'reasons': ['every_edge_changes']}),
            }
            if job.get('permutation_seed') is not None:
                block['permutation_check'] = permutation_check(na, ea, float(q), spec, attr, job['permutation_seed'], sa)
            per_q[f'{float(q):.2f}'] = block
        rows.append(dict(base, control=job['control'], view=job['view'],
                         graph_a={'graph_id': ba['graph_id'], 'canonical_graph_sha256': ba['canonical_graph_sha256'],
                                  'selection': ra},
                         graph_b={'graph_id': bb['graph_id'], 'canonical_graph_sha256': bb['canonical_graph_sha256'],
                                  'selection': rb},
                         selection_ref=ref, controls=controls, q=per_q))
    return rows, artifacts
