"""Two-part structural target diagnostic; standalone, not an operator.

The contract is docs/evaluations/structural_target.md. Three pure, versioned
functions: an admission rule (connectivity constraint C), an anchored forward
reachability score (T) and a link guard. None of them edits a corpus, ranks
documents or claims answer utility. NumPy is imported only for lambda2.
"""
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
import hashlib
import json

CONSTRAINT_VERSION = 'structural_connectivity_constraint.v1'
REACHABILITY_VERSION = 'anchored_forward_reachability.v1'
GUARD_VERSION = 'structural_link_guard.v1'


@dataclass(frozen=True)
class StructuralConfig:
    depth: int = 3
    profile_depths: tuple = (1, 2, 3, 4, 5, 6)
    lambda2_tolerance: float = .10
    use_lambda2: bool = True
    edge_ratio_max: float = 1.20
    outdegree_floor: int = 30


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def config_digest(config=StructuralConfig()):
    return digest({k: list(v) if isinstance(v, tuple) else v for k, v in asdict(config).items()})


def canonical_nodes(nodes):
    nodes = list(nodes)
    if len(set(nodes)) != len(nodes):
        raise ValueError('duplicate-node')
    return sorted(nodes)


def canonical_edges(nodes, edges):
    """Binary simple directed edges: duplicates, loops and unknown endpoints dropped."""
    known = set(nodes)
    return sorted({(s, t) for s, t in edges if s != t and s in known and t in known})


def graph_digest(nodes, edges):
    ns = canonical_nodes(nodes)
    return digest({'nodes': ns, 'edges': [list(e) for e in canonical_edges(ns, edges)]})


def main_component(nodes, edges):
    """Largest weak component; ties broken by the smallest sorted member id."""
    ns = canonical_nodes(nodes)
    adj = defaultdict(set)
    for s, t in canonical_edges(ns, edges):
        adj[s].add(t)
        adj[t].add(s)
    seen, best = set(), frozenset()
    for start in ns:
        if start in seen:
            continue
        comp, queue = {start}, deque([start])
        seen.add(start)
        while queue:
            for y in adj[queue.popleft()]:
                if y not in seen:
                    seen.add(y)
                    comp.add(y)
                    queue.append(y)
        if len(comp) > len(best):  # ns is sorted, so the first maximum has the smallest member
            best = frozenset(comp)
    return best


def forward_distances(nodes, edges, sources):
    """Multi-source forward BFS hop counts from every source present in nodes."""
    ns = canonical_nodes(nodes)
    out = defaultdict(list)
    for s, t in canonical_edges(ns, edges):
        out[s].append(t)
    known = set(ns)
    dist = {a: 0 for a in sorted(set(sources)) if a in known}
    queue = deque(sorted(dist))
    while queue:
        x = queue.popleft()
        for y in out[x]:
            if y not in dist:
                dist[y] = dist[x] + 1
                queue.append(y)
    return dist


def anchored_reachability(nodes, edges, anchors, depth=3, *, config=StructuralConfig()):
    """T: fraction of the main weak component within `depth` forward hops of any anchor."""
    ns = canonical_nodes(nodes)
    m = main_component(ns, edges)
    dist = forward_distances(ns, edges, anchors)
    in_m = {v: d for v, d in dist.items() if v in m}
    count = sum(d <= depth for d in in_m.values())
    profile = {str(k): (sum(d <= k for d in in_m.values()) / len(m) if m else None) for k in config.profile_depths}
    profile['inf'] = len(in_m) / len(m) if m else None
    present = sorted(a for a in set(anchors) if a in set(ns))
    return {'version': REACHABILITY_VERSION, 'depth': depth,
            'value': count / len(m) if m else None, 'reached_count': count,
            'main_component_size': len(m), 'reached_count_all_pages': sum(d <= depth for d in dist.values()),
            'nodes': len(ns), 'profile': profile,
            'mean_forward_distance': (sum(in_m.values()) / len(in_m)) if in_m else None,
            'anchors_present': present, 'anchors_missing': sorted(set(anchors) - set(present)),
            'anchors_in_main_component': sorted(a for a in present if a in m),
            'graph_sha256': graph_digest(ns, edges), 'config_sha256': config_digest(config)}


def normalized_lambda2(nodes, edges):
    """Second-smallest eigenvalue of D^-1/2 (D-A) D^-1/2, undirected binary; 0 if disconnected."""
    import numpy as np
    ns = canonical_nodes(nodes)
    if len(ns) < 2:
        return 0.
    if len(main_component(ns, edges)) != len(ns):
        return 0.
    idx = {v: i for i, v in enumerate(ns)}
    a = np.zeros((len(ns), len(ns)))
    for s, t in canonical_edges(ns, edges):
        a[idx[s], idx[t]] = a[idx[t], idx[s]] = 1.
    d = a.sum(axis=1)
    inv = 1 / np.sqrt(d)
    lap = np.eye(len(ns)) - inv[:, None] * a * inv[None, :]
    return float(np.linalg.eigvalsh(lap)[1])


def outdegrees(edges):
    out = defaultdict(int)
    for s, _ in edges:
        out[s] += 1
    return out


def baseline_summary(nodes, edges, anchors, *, config=StructuralConfig()):
    """Frozen reference for C and the guard; computed once on the baseline graph."""
    ns = canonical_nodes(nodes)
    es = canonical_edges(ns, edges)
    m = main_component(ns, es)
    od = outdegrees(es)
    return {'nodes': ns, 'main_component': sorted(m), 'edges': len(es),
            'max_outdegree': max(od.values(), default=0), 'anchors': sorted(set(anchors)),
            'lambda2': normalized_lambda2(sorted(m), [e for e in es if e[0] in m and e[1] in m]) if config.use_lambda2 else None,
            'graph_sha256': graph_digest(ns, es), 'config_sha256': config_digest(config)}


def connectivity_admission(nodes, edges, baseline, *, mapping=None, config=StructuralConfig()):
    """C: pass/fail with reasons. `mapping` sends merged-away baseline ids to survivors."""
    mapping = mapping or {}
    ns = canonical_nodes(nodes)
    known = set(ns)
    es = canonical_edges(ns, edges)
    m = main_component(ns, es)
    base_nodes = set(baseline['nodes'])
    image = lambda v: mapping.get(v, v)
    reasons = []
    missing_anchors = sorted(a for a in baseline['anchors'] if image(a) not in known)
    if missing_anchors:
        reasons.append('C0-anchor-missing')
    left = sorted(v for v in baseline['main_component'] if image(v) not in m)
    if left:
        reasons.append('C1-baseline-page-left-main-component')
    new = sorted(known - base_nodes)
    new_outside = sorted(v for v in new if v not in m)
    if new_outside:
        reasons.append('C1-new-page-outside-main-component')
    has_in = {t for s, t in es}
    orphans = sorted(v for v in new if v not in has_in)
    if orphans:
        reasons.append('C1b-new-page-without-in-link')
    lam = threshold = None
    if config.use_lambda2:
        evaluation = sorted(({image(v) for v in baseline['main_component']} | set(new)) & known)
        ev = set(evaluation)
        lam = normalized_lambda2(evaluation, [e for e in es if e[0] in ev and e[1] in ev])
        threshold = (1 - config.lambda2_tolerance) * baseline['lambda2']
        if lam < threshold:
            reasons.append('C2-lambda2-below-tolerance')
    return {'version': CONSTRAINT_VERSION, 'passed': not reasons, 'reasons': reasons,
            'anchors_missing': missing_anchors, 'baseline_pages_left_main_component': len(left),
            'baseline_pages_left_sample': left[:20], 'new_pages': len(new),
            'new_pages_outside_main_component': new_outside[:20], 'orphan_new_pages': orphans[:20],
            'main_component_size': len(m), 'lambda2': lam, 'lambda2_threshold': threshold,
            'baseline_lambda2': baseline.get('lambda2'), 'graph_sha256': graph_digest(ns, es),
            'baseline_graph_sha256': baseline['graph_sha256'], 'config_sha256': config_digest(config)}


def link_guard(edges, baseline, *, nodes=None, config=StructuralConfig()):
    """Guard: total directed edges within the ratio and a per-page out-degree cap."""
    es = canonical_edges(nodes, edges) if nodes is not None else sorted({(s, t) for s, t in edges if s != t})
    od = outdegrees(es)
    edge_limit = int(config.edge_ratio_max * baseline['edges'] + 1e-9)
    cap = max(baseline['max_outdegree'], config.outdegree_floor)
    over = sorted(v for v, k in od.items() if k > cap)
    reasons = []
    if len(es) > edge_limit:
        reasons.append('G1-edge-budget-exceeded')
    if over:
        reasons.append('G2-outdegree-cap-exceeded')
    return {'version': GUARD_VERSION, 'passed': not reasons, 'reasons': reasons, 'edges': len(es),
            'edge_limit': edge_limit, 'baseline_edges': baseline['edges'], 'max_outdegree': max(od.values(), default=0),
            'outdegree_cap': cap, 'pages_over_cap': over[:20], 'config_sha256': config_digest(config)}
