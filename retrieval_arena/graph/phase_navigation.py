"""Normalized magnetic phase descriptors and matched-entry graph traversal.

The descriptors are measurements, never navigation or quality objectives. The
navigation helper deliberately accepts only cached scores and graph structure;
support labels belong to the separate scoring stage.
"""
from __future__ import annotations

from collections import deque
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

VERSION = "phase_navigation.v1"
PHASE_VERSION = "normalized_phase_dispersion.v1"
POLARIZATION_VERSION = "normalized_magnetic_subspace_edge_polarization.v2"


def code_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _seed(master_seed, family, view, query_id):
    payload = f"{master_seed}\0{family}\0{view}\0{query_id}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def phase_dispersion(vector, *, denominator_tolerance=1e-14):
    """Amplitude-squared weighted circular dispersion, invariant to global phase."""
    v = np.asarray(vector, dtype=np.complex128)
    if v.ndim != 1 or not np.all(np.isfinite(v)):
        raise ValueError("phase-vector-invalid")
    denom = float(np.vdot(v, v).real)
    if denom <= denominator_tolerance:
        return {"status": "unavailable", "value": None, "reason": "zero-amplitude"}
    # |v_i| v_i = |v_i|^2 exp(i theta_i); no per-node division or anchor.
    value = 1.0 - abs(np.sum(np.abs(v) * v)) / denom
    value = float(value)
    if value < -1e-12 or value > 1 + 1e-12:
        return {"status": "failed", "value": value, "reason": "dispersion-bound"}
    return {"status": "qualified", "value": min(1.0, max(0.0, value)),
            "denominator": denom, "effective_support": denom**2 / float(np.sum(np.abs(v)**4))}


def qualify_phase_modes(matrix, values, vectors, *, residual_relative=1e-10,
                         orthogonality_absolute=1e-10, hermitian_absolute=1e-12,
                         bound_absolute=1e-10, gap_relative=1e-8):
    """Qualify V1/V2 only when both neighboring spectral boundaries are resolved."""
    l = np.asarray(matrix, dtype=np.complex128)
    e = np.asarray(values, dtype=np.float64)
    v = np.asarray(vectors, dtype=np.complex128)
    if l.ndim != 2 or l.shape[0] != l.shape[1] or e.ndim != 1 or v.shape != (l.shape[0], len(e)):
        raise ValueError("phase-spectrum-shape")
    if len(e) < 3:
        return {"status": "unavailable", "reason": "adjacent-modes-missing", "V1": None, "V2": None}
    herm = float(np.max(np.abs(l - l.conj().T)))
    residuals = np.linalg.norm(l @ v - v * e[None, :], axis=0)
    orth = float(np.max(np.abs(v.conj().T @ v - np.eye(len(e)))))
    residual = float(np.linalg.norm(residuals) / max(1.0, np.linalg.norm(l)))
    op_norm = float(np.linalg.norm(l, ord=np.inf))
    gaps = np.diff(e)
    threshold = max(gap_relative * max(1.0, op_norm),
                    10.0 * float(residuals[0] + residuals[1] + residuals[2]))
    checks = {"residual_relative": residual, "orthogonality_max_abs": orth,
              "hermitian_max_abs": herm, "operator_infinity_norm": op_norm,
              "adjacent_gaps": [float(gaps[0]), float(gaps[1])], "gap_threshold": threshold,
              "bound": [float(e.min()), float(e.max())]}
    reasons = []
    if residual > residual_relative: reasons.append("residual")
    if orth > orthogonality_absolute: reasons.append("orthogonality")
    if herm > hermitian_absolute: reasons.append("hermitian")
    if e[0] < -bound_absolute or e[-1] > 2 + bound_absolute: reasons.append("bound")
    out = {"status": "qualified" if not reasons else "failed", "reasons": reasons,
           "checks": checks, "V1": None, "V2": None}
    if reasons:
        return out
    for ix, name in ((0, "V1"), (1, "V2")):
        boundaries = [gaps[ix]] if ix == 0 else [gaps[ix - 1], gaps[ix]]
        if any(gap <= threshold for gap in boundaries):
            out[name] = {"status": "unavailable", "value": None, "reason": "unresolved-mode"}
        else:
            out[name] = phase_dispersion(v[:, ix])
    return out


def qualify_subspace_polarization(nodes, edges, matrix, values, vectors, *, requested_rank=10,
                                  residual_relative=1e-10, orthogonality_absolute=1e-10,
                                  hermitian_absolute=1e-12, bound_absolute=1e-10,
                                  gap_relative=1e-8, diagonal_tolerance=1e-10):
    """Basis-invariant low-subspace polarization; expand a near-degenerate boundary cluster."""
    l = np.asarray(matrix, dtype=np.complex128)
    e = np.asarray(values, dtype=np.float64)
    v = np.asarray(vectors, dtype=np.complex128)
    n = len(nodes)
    if len(e) != n or v.shape != (n, n):
        return {"status": "unavailable", "reason": "full-spectrum-required"}
    if n == 0:
        return {"status": "unavailable", "reason": "empty-view"}
    op_norm = float(np.linalg.norm(l, ord=np.inf))
    scale = max(1.0, op_norm)
    residuals = np.linalg.norm(l @ v - v * e[None, :], axis=0)
    residual = float(np.linalg.norm(residuals) / max(1.0, np.linalg.norm(l)))
    orth = float(np.max(np.abs(v.conj().T @ v - np.eye(n))))
    herm = float(np.max(np.abs(l - l.conj().T)))
    if residual > residual_relative or orth > orthogonality_absolute or herm > hermitian_absolute:
        return {"status": "failed", "reason": "spectral-qualification", "residual_relative": residual,
                "orthogonality_max_abs": orth, "hermitian_max_abs": herm}
    if e[0] < -bound_absolute or e[-1] > 2 + bound_absolute:
        return {"status": "failed", "reason": "spectrum-bound"}
    threshold = max(gap_relative * scale, 10.0 * float(np.max(residuals)))
    rank = min(requested_rank, n)
    while rank < n and e[rank] - e[rank - 1] <= threshold:
        rank += 1
    ambiguous = rank < n and abs((e[rank] - e[rank - 1]) - threshold) <= max(residual_relative, 100*np.finfo(float).eps)*scale
    if ambiguous:
        return {"status": "unavailable", "reason": "ambiguous-cluster-boundary", "requested_rank": requested_rank,
                "actual_rank": rank, "boundary_gap": float(e[rank]-e[rank-1]), "gap_threshold": threshold}
    u = v[:, :rank]
    p = u @ u.conj().T
    diag = np.real(np.diag(p))
    diag_imag = float(np.max(np.abs(np.imag(np.diag(p)))))
    if diag_imag > diagonal_tolerance or float(np.min(diag)) < -diagonal_tolerance:
        return {"status": "failed", "reason": "invalid-projector-diagonal", "diagonal_imag_max": diag_imag,
                "diagonal_min": float(np.min(diag))}
    diag = np.maximum(diag, 0.0)
    ix = {node: i for i, node in enumerate(nodes)}
    numerator_terms, denominator_terms = [], []
    for s, t in edges:
        i, j = ix[s], ix[t]
        numerator_terms.append(float(np.imag(p[j, i])))
        denominator_terms.append(math.sqrt(float(diag[i] * diag[j])))
    denom = math.fsum(denominator_terms)
    if denom <= residual_relative:
        return {"status": "unavailable", "reason": "empty-denominator", "requested_rank": requested_rank,
                "actual_rank": rank, "denominator": denom}
    val = abs(math.fsum(numerator_terms)) / denom
    boundary = None if rank == n else float(e[rank] - e[rank-1])
    return {"status": "qualified", "value": float(val), "requested_rank": requested_rank,
            "actual_rank": rank, "boundary_gap": boundary, "gap_threshold": threshold,
            "denominator": denom, "effective_mass": float(np.sum(diag)),
            "effective_support": float(np.sum(diag)**2 / np.sum(diag**2)),
            "full_spectrum": rank == n, "full_spectrum_zero_degeneracy": rank == n and abs(val) <= residual_relative,
            "diagonal_imag_max": diag_imag}


def sample_random_entries(nodes, *, master_seed, family, view, query_id, count=128):
    """Uniform distinct start pages with stable id-based RNG independent of input order."""
    ordered = sorted(nodes)
    if not ordered:
        return []
    rng = np.random.default_rng(_seed(master_seed, family, view, query_id))
    size = min(count, len(ordered))
    return [ordered[int(i)] for i in rng.choice(len(ordered), size=size, replace=False)]


def traverse_once(nodes, edges, scores, seed, *, max_hops=1, neighbor_budget=20, candidate_budget=40,
                  final_top_k=4, distance_penalty=0.02, directed=True):
    """One shared outgoing-link traversal. `scores` are cached cosine values only."""
    node_set = set(nodes)
    if seed not in node_set:
        raise ValueError("seed-outside-view")
    adj = {n: [] for n in nodes}
    for s, t in edges:
        if s in node_set and t in node_set:
            adj[s].append(t)
            if not directed:
                adj[t].append(s)
    for n in adj:
        adj[n] = sorted(set(adj[n]))
    distances = {seed: 0}
    queue = deque([seed])
    offered = []
    while queue and len(distances) < candidate_budget:
        current = queue.popleft()
        if distances[current] >= max_hops:
            continue
        neighbors = [x for x in adj[current] if x not in distances][:neighbor_budget]
        offered.extend(neighbors)
        for x in neighbors:
            if len(distances) >= candidate_budget:
                break
            distances[x] = distances[current] + 1
            queue.append(x)
    ranked = sorted(distances, key=lambda x: (-(float(scores.get(x, 0.0)) - distance_penalty * distances[x]),
                                               distances[x], x))[:final_top_k]
    return {"seed_id": seed, "offered_ids": offered, "candidate_ids": list(distances),
            "distances": distances, "selected_ids": ranked,
            "selected_scores": {x: float(scores.get(x, 0.0)) for x in ranked},
            "dead_end": len(distances) == 1, "candidate_count": len(distances)}


def matched_trials(nodes, edges, scores, query_id, *, family, view, master_seed=29061029,
                  random_count=128, **traversal):
    """Return semantic singleton and all random singleton trials through same traversal."""
    semantic_seed = min(nodes, key=lambda n: (-float(scores.get(n, 0.0)), n))
    random_seeds = sample_random_entries(nodes, master_seed=master_seed, family=family,
                                         view=view, query_id=query_id, count=random_count)
    return ({"policy": "semantic", "trials": [traverse_once(nodes, edges, scores, semantic_seed, **traversal)]},
            {"policy": "random", "trials": [traverse_once(nodes, edges, scores, seed, **traversal)
                                               for seed in random_seeds],
             "seed_ids": random_seeds, "first_half": random_seeds[:64], "last_half": random_seeds[64:]})


def score_trial(trial, known_targets):
    """Separate evaluator: mark access to known positives without relabeling other pages negative."""
    targets = set(known_targets)
    candidates = set(trial["candidate_ids"])
    selected = set(trial["selected_ids"])
    seed = trial["seed_id"]
    return {"hit": int(bool(selected & targets)), "candidate_hit": int(bool(candidates & targets)),
            "seed_hit": int(seed in targets),
            "expansion_added_hit": int(bool((selected - {seed}) & targets)),
            "selected_count": len(selected), "candidate_count": trial["candidate_count"],
            "dead_end": bool(trial["dead_end"]),
            "known_target_distances": {t: trial["distances"][t] for t in sorted(targets & set(trial["distances"]))}}


def query_macro(semantic_trials, random_trials, known_targets):
    """Score only after retrieval; average random starts within query before query macro mean."""
    sem = [score_trial(row, known_targets) for row in semantic_trials]
    rnd = [score_trial(row, known_targets) for row in random_trials]
    if len(sem) != 1 or not rnd:
        raise ValueError("navigation-trials-incomplete")
    mean = lambda rows, key: float(np.mean([x[key] for x in rows]))
    c_sem = float(sem[0]["hit"])
    c_rand = mean(rnd, "hit")
    return {"C_semantic": c_sem, "C_random": c_rand, "D": c_sem - c_rand,
            "semantic_candidate_hit": float(sem[0]["candidate_hit"]),
            "random_candidate_hit": mean(rnd, "candidate_hit"),
            "semantic_seed_hit": float(sem[0]["seed_hit"]),
            "random_seed_hit": mean(rnd, "seed_hit"),
            "semantic_expansion_added_access": float(sem[0]["expansion_added_hit"]),
            "random_expansion_added_access": mean(rnd, "expansion_added_hit"),
            "semantic_selected_count": sem[0]["selected_count"],
            "random_selected_count": mean(rnd, "selected_count"),
            "semantic_candidate_count": sem[0]["candidate_count"],
            "random_candidate_count": mean(rnd, "candidate_count"),
            "semantic_dead_end": sem[0]["dead_end"],
            "random_dead_end_rate": mean(rnd, "dead_end"),
            "semantic_target_distances": sem[0]["known_target_distances"],
            "random_target_distance_counts": _distance_counts(rnd)}


def _distance_counts(scored_trials):
    counts = {str(i): 0 for i in (1, 2, 3)}
    for row in scored_trials:
        distances = row["known_target_distances"].values()
        for limit in (1, 2, 3):
            if any(d <= limit for d in distances):
                counts[str(limit)] += 1
    return counts


def spearman_association(rows, feature, outcome):
    eligible = [(float(r[feature]), float(r[outcome])) for r in rows
                if r.get(feature) is not None and r.get(outcome) is not None
                and np.isfinite(float(r[feature])) and np.isfinite(float(r[outcome]))]
    if len(eligible) < 2 or len({x for x, _ in eligible}) < 2 or len({y for _, y in eligible}) < 2:
        return {"status": "unavailable", "reason": "insufficient-variation", "n": len(eligible), "rho": None}
    rho = float(spearmanr([x for x, _ in eligible], [y for _, y in eligible]).statistic)
    return {"status": "qualified", "n": len(eligible), "rho": rho,
            "feature_values": [x for x, _ in eligible], "outcome_values": [y for _, y in eligible]}


def ridge_transfer(rows, feature_names, outcome, *, train_kinds, test_kinds, minimum_train=15, minimum_test=6):
    """Frozen train-only standardization and ridge penalty=1; no tuning or held-out refit."""
    train = [r for r in rows if r.get("kind") in train_kinds]
    test = [r for r in rows if r.get("kind") in test_kinds]
    if len(train) < minimum_train or len(test) < minimum_test:
        return {"status": "unavailable", "reason": "minimum-variant-count", "n_train": len(train), "n_test": len(test)}
    usable = [name for name in feature_names if all(r.get(name) is not None for r in train + test)]
    if not usable:
        return {"status": "unavailable", "reason": "no-complete-features"}
    xtr = np.asarray([[float(r[name]) for name in usable] for r in train])
    xte = np.asarray([[float(r[name]) for name in usable] for r in test])
    ytr = np.asarray([float(r[outcome]) for r in train]); yte = np.asarray([float(r[outcome]) for r in test])
    means, scales = xtr.mean(axis=0), xtr.std(axis=0)
    zero_variance = [name for name, scale in zip(usable, scales) if scale == 0]
    safe = np.where(scales == 0, 1.0, scales)
    ztr, zte = (xtr-means)/safe, (xte-means)/safe
    design = np.column_stack([np.ones(len(ztr)), ztr])
    penalty = np.diag([0.0] + [1.0] * len(usable))
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ ytr)
    pred = np.column_stack([np.ones(len(zte)), zte]) @ beta
    baseline = np.full(len(yte), ytr.mean())
    return {"status": "qualified", "features": usable, "zero_variance_columns": zero_variance,
            "n_train": len(train), "n_test": len(test), "standardization": "training rows only",
            "ridge_penalty": 1.0, "intercept_penalized": False,
            "test_mae": float(np.mean(np.abs(yte-pred))),
            "test_rmse": float(np.sqrt(np.mean((yte-pred)**2))),
            "training_mean_mae": float(np.mean(np.abs(yte-baseline))),
            "training_mean_rmse": float(np.sqrt(np.mean((yte-baseline)**2))),
            "predictions": pred.tolist(), "observed": yte.tolist()}


def paired_query_bootstrap(rows_by_query_text, *, statistic, draws=2000, seed=29061029):
    """Cluster duplicate texts while resampling original query rows with their fixed weights."""
    groups = sorted(rows_by_query_text)
    if not groups:
        return {"status": "unavailable", "reason": "empty-query-panel"}
    rng = np.random.default_rng(seed)
    estimates = []
    for _ in range(draws):
        sampled = rng.choice(groups, size=len(groups), replace=True)
        sample = [row for group in sampled for row in rows_by_query_text[group]]
        estimates.append(float(statistic(sample)))
    observed = float(statistic([row for group in groups for row in rows_by_query_text[group]]))
    lo, hi = np.quantile(estimates, [0.025, 0.975])
    return {"status": "qualified", "unit": "query-text-group", "draws": draws,
            "estimate": observed, "interval_95": [float(lo), float(hi)],
            "seed": seed, "conditional_on_fixed_graphs": True}
