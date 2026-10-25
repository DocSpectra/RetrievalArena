"""Symmetric normalized magnetic spectrum, a versioned graph measurement.

The contract is docs/evaluations/normalized_magnetic_spectrum.md. This module
is separate from magnetic_phase.py, whose unnormalized constructor and
historical receipts stay unchanged. It measures low spectra of declared graph
views plus generic structural descriptors and one hierarchy comparator
(generalized trophic incoherence). It is a descriptor, not a retrieval or
answer score, and no field here is an optimization target.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

import numpy as np

VERSION = 'normalized_magnetic_spectrum.v1'
BUNDLE_SCHEMA = 'retrieval_arena.graph_bundle.v1'
CONFIG_SCHEMA = 'retrieval_arena.graph_spectra_config.v1'
CONTINUITY_Q = 0.25
SENSITIVITY_Q = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25)
NORMALIZATION = 'symmetric: L = diag(d>0) - D^+1/2 (W o exp(2 pi i q (A - A^T))) D^+1/2, D^+1/2 zero on isolates'
PHASE_POLICY = 'theta_ij = 2 pi q (A_ij - A_ji); H_ij = W_ij exp(i theta_ij)'
WEIGHT_POLICY = 'W = (A + A^T)/2 for binary A: 1 reciprocal, 1/2 one-way; q = 0 uses the same W'
ISOLATE_POLICY = 'isolated nodes kept; their row and column are zero (eigenvalue 0); I - H_norm never substituted'
EDGE_POLICY = 'binary simple directed: duplicates removed and counted, self loops removed and counted, unknown endpoints refused'
VIEW_KINDS = ('full', 'lwcc', 'induced', 'induced_lwcc')


@dataclass(frozen=True)
class SpectrumConfig:
    modes: int = 3
    solver: str = 'dense'
    residual_relative: float = 1e-10
    orthogonality_absolute: float = 1e-10
    hermitian_absolute: float = 1e-12
    bound_absolute: float = 1e-10
    near_zero_absolute: float = 1e-9
    sparse_tol: float = 0.0
    sparse_maxiter: int = 100000
    sparse_seed: int = 2207


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def code_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def validate_config(config):
    if not isinstance(config, SpectrumConfig):
        raise ValueError('invalid-spectrum-config')
    if not isinstance(config.modes, int) or config.modes < 1:
        raise ValueError('invalid-modes')
    if config.solver not in ('dense', 'sparse'):
        raise ValueError('invalid-solver')
    for key in ('residual_relative', 'orthogonality_absolute', 'hermitian_absolute', 'bound_absolute', 'near_zero_absolute'):
        value = getattr(config, key)
        if not np.isfinite(value) or value <= 0:
            raise ValueError('invalid-tolerance:' + key)
    return config


# ------------------------------------------------------------------ graph admission and views

def admit_graph(nodes, edges):
    """Declared input policy. Returns sorted nodes, sorted unique arcs and the counts removed."""
    nodes = list(nodes)
    if any(not isinstance(n, str) or not n for n in nodes):
        raise ValueError('malformed-node')
    if len(set(nodes)) != len(nodes):
        raise ValueError('duplicate-node')
    known = set(nodes)
    arcs, self_loops, rows = set(), 0, 0
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2 or not all(isinstance(x, str) for x in edge):
            raise ValueError('malformed-edge')
        s, t = edge
        if s not in known or t not in known:
            raise ValueError('unknown-endpoint')
        rows += 1
        if s == t:
            self_loops += 1
            continue
        arcs.add((s, t))
    return {'nodes': sorted(nodes), 'edges': sorted(arcs), 'input_edge_rows': rows,
            'self_loops_removed': self_loops, 'duplicate_edges_removed': rows - self_loops - len(arcs)}


def graph_sha256(nodes, edges):
    return digest({'nodes': sorted(nodes), 'edges': sorted([list(e) for e in edges])})


def weak_components(nodes, edges):
    """Components ordered by size descending, then by smallest node id (deterministic tie-break)."""
    adj = defaultdict(set)
    for s, t in edges:
        adj[s].add(t)
        adj[t].add(s)
    seen, comps = set(), []
    for start in sorted(nodes):
        if start in seen:
            continue
        comp, queue = {start}, deque([start])
        seen.add(start)
        while queue:
            x = queue.popleft()
            for y in adj[x]:
                if y not in seen:
                    seen.add(y)
                    comp.add(y)
                    queue.append(y)
        comps.append(comp)
    comps.sort(key=lambda c: (-len(c), min(c)))
    return comps


def induced(nodes, edges, keep):
    keep = set(keep)
    return sorted(n for n in nodes if n in keep), [(s, t) for s, t in edges if s in keep and t in keep]


def select_view(nodes, edges, view, node_sets=None):
    """Apply a declared view to an admitted graph. Returns (view nodes, view edges, selection record)."""
    kind = view.get('kind') if isinstance(view, dict) else None
    if kind not in VIEW_KINDS:
        raise ValueError('invalid-view')
    record = {'kind': kind}
    if kind in ('induced', 'induced_lwcc'):
        name = view.get('node_set')
        if not node_sets or name not in node_sets:
            raise ValueError('unknown-node-set')
        chosen = set(node_sets[name])
        if not chosen <= set(nodes):
            raise ValueError('node-set-outside-graph')
        nodes, edges = induced(nodes, edges, chosen)
        record['node_set'] = name
        record['node_set_size'] = len(chosen)
    if kind in ('lwcc', 'induced_lwcc'):
        comps = weak_components(nodes, edges)
        if comps:
            top = len(comps[0])
            record['lwcc_tie_count'] = sum(1 for c in comps if len(c) == top)
            record['lwcc_rule'] = 'largest weak component; ties broken by smallest node id'
            nodes, edges = induced(nodes, edges, comps[0])
        else:
            record['lwcc_tie_count'] = 0
    record.update(nodes=len(nodes), edges=len(edges), view_graph_sha256=graph_sha256(nodes, edges),
                  selected_nodes_sha256=digest(sorted(nodes)), selected_edges_sha256=digest(sorted([list(e) for e in edges])))
    return sorted(nodes), sorted(edges), record


def validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get('schema_version') != BUNDLE_SCHEMA:
        raise ValueError('bundle-schema')
    for key in ('graph_id', 'nodes', 'edges', 'canonical_graph_sha256'):
        if key not in bundle:
            raise ValueError('bundle-missing:' + key)
    admitted = admit_graph(bundle['nodes'], [tuple(e) for e in bundle['edges']])
    if admitted['input_edge_rows'] != len(admitted['edges']):
        raise ValueError('bundle-not-canonical')
    if graph_sha256(admitted['nodes'], admitted['edges']) != bundle['canonical_graph_sha256']:
        raise ValueError('bundle-graph-sha256-mismatch')
    for name, members in (bundle.get('node_sets') or {}).items():
        if len(set(members)) != len(members) or not set(members) <= set(admitted['nodes']):
            raise ValueError('bundle-node-set-invalid:' + name)
    return admitted


# ------------------------------------------------------------------ operators

def _adjacency(nodes, edges):
    idx = {v: i for i, v in enumerate(nodes)}
    a = np.zeros((len(nodes), len(nodes)), dtype=np.float64)
    for s, t in edges:
        a[idx[s], idx[t]] = 1.0
    return a


def normalized_operator_from_weights(w, theta):
    """D^+1/2 (D - W o exp(i theta)) D^+1/2 for symmetric nonnegative w and antisymmetric real theta."""
    w = np.asarray(w, dtype=np.float64)
    theta = np.asarray(theta, dtype=np.float64)
    if w.shape != theta.shape or w.ndim != 2 or w.shape[0] != w.shape[1]:
        raise ValueError('operator-shape')
    if np.any(w < 0) or not np.array_equal(w, w.T) or not np.array_equal(theta, -theta.T):
        raise ValueError('operator-symmetry')
    d = w.sum(axis=1)
    r = np.zeros_like(d)
    r[d > 0] = 1.0 / np.sqrt(d[d > 0])
    h = w * np.exp(1j * theta)
    return np.diag((d > 0).astype(np.float64)).astype(np.complex128) - r[:, None] * h * r[None, :]


def normalized_magnetic_matrix(nodes, edges, q=CONTINUITY_Q):
    if not np.isfinite(q):
        raise ValueError('nonfinite-q')
    a = _adjacency(nodes, edges)
    return normalized_operator_from_weights(0.5 * (a + a.T), 2 * np.pi * q * (a - a.T))


def unnormalized_magnetic_matrix(nodes, edges, q=CONTINUITY_Q):
    """Explicit comparator D - W o exp(2 pi i q (A - A^T)); equals the legacy magnetic_phase constructor."""
    if not np.isfinite(q):
        raise ValueError('nonfinite-q')
    a = _adjacency(nodes, edges)
    w = 0.5 * (a + a.T)
    return np.diag(w.sum(axis=1)).astype(np.complex128) - w * np.exp(2j * np.pi * q * (a - a.T))


def sparse_normalized_magnetic_matrix(nodes, edges, q=CONTINUITY_Q):
    from scipy import sparse
    idx = {v: i for i, v in enumerate(nodes)}
    n = len(nodes)
    es = set(edges)
    weight, phase = {}, {}
    for s, t in es:
        i, j = idx[s], idx[t]
        recip = (t, s) in es
        weight[(i, j)] = weight[(j, i)] = 1.0 if recip else 0.5
        if not recip:
            phase[(i, j)], phase[(j, i)] = 2 * np.pi * q, -2 * np.pi * q
    d = np.zeros(n)
    for (i, _), x in weight.items():
        d[i] += x
    r = np.zeros(n)
    r[d > 0] = 1.0 / np.sqrt(d[d > 0])
    rows = list(range(n)) + [i for i, _ in weight]
    cols = list(range(n)) + [j for _, j in weight]
    vals = [1.0 + 0j if d[i] > 0 else 0j for i in range(n)]
    vals += [-r[i] * x * np.exp(1j * phase.get((i, j), 0.0)) * r[j] for (i, j), x in weight.items()]
    return sparse.csr_matrix((np.array(vals, dtype=np.complex128), (rows, cols)), shape=(n, n))


def legacy_binary_skeleton_normalized_lambda2(nodes, edges):
    """LEGACY comparator: ordinary normalized Laplacian of the binary undirected skeleton (historical
    normalized_lambda2). Its weights differ from W when reciprocal and one-way arcs coexist."""
    if len(nodes) < 2:
        return None
    idx = {x: i for i, x in enumerate(nodes)}
    a = np.zeros((len(nodes), len(nodes)))
    for s, t in edges:
        a[idx[s], idx[t]] = a[idx[t], idx[s]] = 1
    d = a.sum(axis=1)
    inv = np.zeros(len(nodes))
    inv[d > 0] = 1 / np.sqrt(d[d > 0])
    lap = np.diag((d > 0).astype(float)) - inv[:, None] * a * inv[None, :]
    return float(np.linalg.eigvalsh(lap)[1])


# ------------------------------------------------------------------ eigensolvers and qualification

def _dense_modes(matrix):
    e, v = np.linalg.eigh(matrix)
    return e, v


def _sparse_modes(matrix, k, config):
    from scipy.sparse import identity
    from scipy.sparse.linalg import ArpackNoConvergence, eigsh
    n = matrix.shape[0]
    if k >= n - 1:
        return None, None, 'sparse_requires_n_greater_than_modes_plus_1'
    rng = np.random.default_rng(config.sparse_seed)
    v0 = rng.standard_normal(n) + 1j * rng.standard_normal(n)
    shifted = 2.0 * identity(n, dtype=np.complex128, format='csr') - matrix
    try:
        mu, v = eigsh(shifted, k=k, which='LA', v0=v0, tol=config.sparse_tol, maxiter=config.sparse_maxiter)
    except ArpackNoConvergence:
        return None, None, 'sparse_not_converged'
    # ARPACK vectors inside a repeated eigenvalue need not be orthogonal. Rayleigh-Ritz on the returned
    # subspace restores an orthonormal basis without changing the subspace; residuals are checked after.
    basis, _ = np.linalg.qr(v)
    theta, y = np.linalg.eigh(basis.conj().T @ (matrix @ basis))
    return theta, basis @ y, None


def spectrum(nodes, edges, q, config=SpectrumConfig(), *, operator='normalized'):
    """Lowest `modes` eigenvalues with numerical qualification. Values are raw; nothing is clipped or dropped."""
    validate_config(config)
    n = len(nodes)
    rec = {'q': float(q), 'operator': operator, 'solver': config.solver, 'dtype': 'complex128', 'n': n,
           'eigenvalues': [], 'near_zero': [], 'missing_modes': {}, 'status': 'unavailable', 'reasons': []}
    if operator not in ('normalized', 'unnormalized'):
        raise ValueError('invalid-operator')
    if n == 0:
        rec['reasons'].append('empty_view')
        return rec
    k = min(config.modes, n)
    for i in range(k, config.modes):
        rec['missing_modes'][f'lambda{i + 1}'] = 'fewer_nodes_than_mode_index'
    if operator == 'normalized':
        dense = normalized_magnetic_matrix(nodes, edges, q)
        upper = 2.0
    else:
        dense = unnormalized_magnetic_matrix(nodes, edges, q)
        upper = 2.0 * float(np.max(np.real(np.diag(dense)))) if n else 0.0
    hermitian = float(np.max(np.abs(dense - dense.conj().T))) if n else 0.0
    if config.solver == 'dense':
        e, v = _dense_modes(dense)
        rec['full_spectrum'] = True
        rec['spectrum_min'], rec['spectrum_max'] = float(e[0]), float(e[-1])
        rec['numerical_zero_count'] = int(np.sum(np.abs(e) <= config.near_zero_absolute))
        rec['zero_count_method'] = 'dense full spectrum, |lambda| <= near_zero_absolute'
        values, vectors, matrix = e[:k], v[:, :k], dense
    else:
        if operator != 'normalized':
            raise ValueError('sparse-unnormalized-not-implemented')
        matrix = sparse_normalized_magnetic_matrix(nodes, edges, q)
        values, vectors, failure = _sparse_modes(matrix, k, config)
        rec['full_spectrum'] = False
        rec['numerical_zero_count'] = None
        rec['zero_count_method'] = 'not asserted: partial low-mode computation'
        rec['sparse'] = {'method': 'scipy.sparse.linalg.eigsh on 2I - L, which=LA; QR + Rayleigh-Ritz on the returned subspace',
                         'tol': config.sparse_tol,
                         'maxiter': config.sparse_maxiter, 'seed': config.sparse_seed}
        if failure:
            rec['reasons'].append(failure)
            return rec
    residual = float(np.linalg.norm(matrix @ vectors - vectors * values) / max(1.0, float(np.linalg.norm(dense))))
    orth = float(np.max(np.abs(vectors.conj().T @ vectors - np.eye(k))))
    rec.update(eigenvalues=[float(x) for x in values],
               near_zero=[bool(abs(x) <= config.near_zero_absolute) for x in values],
               residual_relative=residual, orthogonality_max_abs=orth, hermitian_max_abs=hermitian,
               bound=[0.0, upper], computed_modes_within_bound=bool(np.all(values >= -config.bound_absolute)
                                                                    and np.all(values <= upper + config.bound_absolute)))
    for i, x in enumerate(values):
        rec[f'lambda{i + 1}'] = float(x)
    for i in range(k, config.modes):
        rec[f'lambda{i + 1}'] = None
    reasons = rec['reasons']
    if residual > config.residual_relative:
        reasons.append('residual')
    if orth > config.orthogonality_absolute:
        reasons.append('orthogonality')
    if hermitian > config.hermitian_absolute:
        reasons.append('hermitian')
    if not rec['computed_modes_within_bound']:
        reasons.append('bound')
    if rec.get('full_spectrum') and operator == 'normalized' and (
            rec['spectrum_min'] < -config.bound_absolute or rec['spectrum_max'] > 2 + config.bound_absolute):
        reasons.append('full_spectrum_bound')
    rec['status'] = 'qualified' if not reasons else 'failed'
    rec['_vectors'] = vectors
    return rec


def public(rec):
    return {k: v for k, v in rec.items() if not k.startswith('_')}


def subspace_distance(u, v):
    """Spectral-norm distance between the projectors onto span(u) and span(v); valid under repeated eigenvalues."""
    pu, pv = u @ u.conj().T, v @ v.conj().T
    return float(np.linalg.norm(pu - pv, ord=2))


# ------------------------------------------------------------------ structural descriptors and comparator

def gini(values):
    xs = sorted(values)
    n, total = len(xs), sum(xs)
    if n == 0 or total == 0:
        return 0.0
    return sum((2 * (i + 1) - n - 1) * x for i, x in enumerate(xs)) / (n * total)


def _largest_scc(nodes, edges):
    if not nodes:
        return 0
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components
    idx = {v: i for i, v in enumerate(nodes)}
    rows = [idx[s] for s, _ in edges]
    cols = [idx[t] for _, t in edges]
    g = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(nodes), len(nodes)))
    _, labels = connected_components(g, directed=True, connection='strong')
    return int(np.bincount(labels).max())


def structural_descriptors(nodes, edges):
    n, m = len(nodes), len(edges)
    out_d, in_d = defaultdict(int), defaultdict(int)
    for s, t in edges:
        out_d[s] += 1
        in_d[t] += 1
    es = set(edges)
    recip = sum(1 for s, t in edges if (t, s) in es)
    w_deg = defaultdict(float)
    for s, t in edges:
        x = 0.5  # each arc contributes W = 1/2 to both endpoints; a reciprocal pair sums to 1
        w_deg[s] += x
        w_deg[t] += x
    wd = np.array([w_deg[v] for v in nodes]) if n else np.array([])
    comps = weak_components(nodes, edges)
    isolates = sum(1 for v in nodes if out_d[v] == 0 and in_d[v] == 0)
    scc = _largest_scc(nodes, edges)
    pos = wd[wd > 0]
    return {'nodes': n, 'arcs': m, 'weak_components': len(comps), 'isolates': isolates,
            'isolate_fraction': isolates / n if n else None,
            'lwcc_nodes': len(comps[0]) if comps else 0, 'lwcc_coverage': len(comps[0]) / n if n else None,
            'largest_scc_nodes': scc, 'largest_scc_fraction': scc / n if n else None,
            'reciprocal_arcs': recip, 'reciprocal_arc_fraction': recip / m if m else None,
            'sources': sum(1 for v in nodes if out_d[v] > 0 and in_d[v] == 0),
            'sinks': sum(1 for v in nodes if in_d[v] > 0 and out_d[v] == 0),
            'source_fraction': sum(1 for v in nodes if out_d[v] > 0 and in_d[v] == 0) / n if n else None,
            'sink_fraction': sum(1 for v in nodes if in_d[v] > 0 and out_d[v] == 0) / n if n else None,
            'fraction_denominator': 'view nodes (including isolates)',
            'outdegree_gini': gini([out_d[v] for v in nodes]),
            'weighted_degree': {'mean': float(wd.mean()) if n else None, 'median': float(np.median(wd)) if n else None,
                                'max': float(wd.max()) if n else None,
                                'min_positive': float(pos.min()) if pos.size else None,
                                'cv': float(wd.std() / wd.mean()) if n and wd.mean() > 0 else None,
                                'definition': 'd_i = sum_j W_ij, W = (A + A^T)/2'}}


def trophic_levels(nodes, edges):
    """Generalized trophic levels per weak component with edges (minimum level 0 in each component).

    Solves diag(in + out) h - (A + A^T) h = in - out, grounding one node per component; the grounded
    system is positive definite for a connected component. Returns (levels, per-component arcs, edgeless
    component count, maximum equation residual).
    """
    comps = weak_components(nodes, edges)
    by_node = {}
    for ci, comp in enumerate(comps):
        for v in comp:
            by_node[v] = ci
    comp_edges = defaultdict(list)
    for s, t in edges:
        comp_edges[by_node[s]].append((s, t))
    levels, groups, edgeless, residual = {}, [], 0, 0.0
    for ci, comp in enumerate(comps):
        ce = comp_edges.get(ci, [])
        if not ce:
            edgeless += 1
            continue
        order = sorted(comp)
        idx = {v: i for i, v in enumerate(order)}
        k = len(order)
        a = np.zeros((k, k))
        for s, t in ce:
            a[idx[s], idx[t]] = 1.0
        win, wout = a.sum(axis=0), a.sum(axis=1)
        lam = np.diag(win + wout) - a - a.T
        rhs = win - wout
        h = np.zeros(k)
        h[1:] = np.linalg.solve(lam[1:, 1:], rhs[1:])
        h -= h.min()
        residual = max(residual, float(np.max(np.abs(lam @ h - rhs))))
        for v in order:
            levels[v] = float(h[idx[v]])
        groups.append(ce)
    return levels, groups, edgeless, residual


def incoherence_from_levels(levels, edges):
    return float(sum((levels[t] - levels[s] - 1.0) ** 2 for s, t in edges))


def trophic_incoherence(nodes, edges):
    """Generalized trophic incoherence F0 (MacKay, Johnson and Sansom 2020) for binary A.

    F0 = sum A_ij (h_j - h_i - 1)^2 / sum A_ij over weak components with edges (edgeless components are
    excluded and counted). A descriptive hierarchy comparator, not an LT label or a documentation-quality
    measure; its scalar cannot distinguish every tree orientation.
    """
    levels, groups, edgeless, residual = trophic_levels(nodes, edges)
    numerator = sum(incoherence_from_levels(levels, ce) for ce in groups)
    count, used = sum(len(ce) for ce in groups), len(groups)
    if count == 0:
        return {'status': 'unavailable', 'value': None, 'reasons': ['edgeless_view'], 'edgeless_components': edgeless,
                'components_with_edges': 0, 'arcs': 0}
    return {'status': 'computed', 'value': numerator / count, 'reasons': [], 'arcs': count,
            'components_with_edges': used, 'edgeless_components': edgeless, 'level_equation_residual_max': residual,
            'level_constant': 'min level 0 in each weak component', 'aggregate': 'edge-weighted over components with edges',
            'reference': 'MacKay, Johnson and Sansom (2020), How directed is a directed network?, R. Soc. Open Sci.'}


# ------------------------------------------------------------------ one measurement job

def runtime_identity():
    import scipy
    return {'python': sys.version.split()[0], 'numpy': np.__version__, 'scipy': scipy.__version__,
            'platform': platform.platform(), 'machine': platform.machine(),
            'threads': {k: os.environ.get(k) for k in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS')}}


def measure_view(bundle, view, normalized_q, unnormalized_q=(), config=SpectrumConfig(), *, legacy=True):
    """One receipt: graph and view identity, structural descriptors, trophic incoherence and spectra."""
    validate_config(config)
    for q in list(normalized_q) + list(unnormalized_q):
        if not isinstance(q, (int, float)) or not np.isfinite(q):
            raise ValueError('invalid-q')
    admitted = validate_bundle(bundle)
    nodes, edges, selection = select_view(admitted['nodes'], admitted['edges'], view, bundle.get('node_sets'))
    spectra = {}
    for q in normalized_q:
        spectra[f'{q:.2f}'] = public(spectrum(nodes, edges, q, config))
    unnormalized = {}
    dense = SpectrumConfig(**dict(asdict(config), solver='dense'))
    for q in unnormalized_q:
        unnormalized[f'{q:.2f}'] = public(spectrum(nodes, edges, q, dense, operator='unnormalized'))
    statuses = [s['status'] for s in spectra.values()] + [s['status'] for s in unnormalized.values()]
    receipt = {
        'metric_version': VERSION, 'normalization': NORMALIZATION, 'phase_policy': PHASE_POLICY,
        'weight_policy': WEIGHT_POLICY, 'isolate_policy': ISOLATE_POLICY, 'edge_policy': EDGE_POLICY,
        'graph_id': bundle['graph_id'], 'canonical_graph_sha256': bundle['canonical_graph_sha256'],
        'graph_nodes': len(admitted['nodes']), 'graph_arcs': len(admitted['edges']),
        'view': dict(view), 'selection': selection,
        'config': asdict(config), 'config_sha256': digest(asdict(config)), 'code_sha256': code_sha256(),
        'normalized': spectra, 'unnormalized': unnormalized,
        'weighted_normalized_lambda2_q0': spectra.get('0.00', {}).get('lambda2'),
        'structure': structural_descriptors(nodes, edges), 'trophic_incoherence': trophic_incoherence(nodes, edges),
        'numerical_status': ('qualified' if statuses and all(s == 'qualified' for s in statuses)
                             else 'unavailable' if not nodes else 'failed'),
        'optimization_admitted': False, 'scientific_utility': 'unvalidated',
    }
    if legacy:
        receipt['legacy_binary_skeleton_normalized_lambda2'] = legacy_binary_skeleton_normalized_lambda2(nodes, edges)
    return receipt


def validate_jobs_config(config):
    if not isinstance(config, dict) or config.get('schema_version') != CONFIG_SCHEMA:
        raise ValueError('graph-spectra-config-schema')
    if set(config) != {'schema_version', 'spectrum_config', 'jobs'}:
        raise ValueError('graph-spectra-config-keys')
    spec = SpectrumConfig(**config['spectrum_config'])
    validate_config(spec)
    ids = set()
    for job in config['jobs']:
        if set(job) - {'job_id', 'bundle', 'view', 'normalized_q', 'unnormalized_q', 'legacy'} or 'job_id' not in job:
            raise ValueError('graph-spectra-job-keys')
        if job['job_id'] in ids:
            raise ValueError('graph-spectra-duplicate-job')
        ids.add(job['job_id'])
        if not isinstance(job['bundle'], str) or job['bundle'].startswith('/') or '..' in Path(job['bundle']).parts:
            raise ValueError('graph-spectra-bundle-path')
    return spec


def run_jobs(config, input_root):
    """Measure every job of a validated config over bundles under input_root (host CLI and operator)."""
    spec = validate_jobs_config(config)
    cache, rows = {}, []
    for job in config['jobs']:
        if job['bundle'] not in cache:
            cache[job['bundle']] = json.loads((Path(input_root) / job['bundle']).read_bytes())
        receipt = measure_view(cache[job['bundle']], job['view'], job.get('normalized_q', []), job.get('unnormalized_q', []),
                               spec, legacy=job.get('legacy', True))
        receipt['job_id'] = job['job_id']
        rows.append(receipt)
    return rows
