"""Cached page-selection comparison and separate known-positive diagnostics.

Predictions never consume support labels. Unjudged pages remain unjudged;
zero hit means only that no saved known positive was selected.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math

import numpy as np

from retrieval_arena.diagnostics import _distances_from_targets
from .phase_navigation import traverse_once, score_trial

VERSION = 'support_access_comparison.v1'
CELLS = ('both_hit', 'graph_only', 'semantic_only', 'neither_hit')
STAGES = ('final_selection', 'traversal_admission', 'beyond_hop_limit', 'directed_unreachable')


def cached_ranking(nodes, score_pairs):
    """Require a finite score for every eligible page; refuse duplicate IDs.

    Scores outside the view are permitted but never selected. A missing score
    is an input error, never an implicit zero or a low-ranked page.
    """
    nodes = list(nodes)
    if not nodes or len(nodes) != len(set(nodes)) or any(not isinstance(n, str) or not n for n in nodes):
        raise ValueError('support-access-view-nodes')
    scores = {}
    for doc, value in score_pairs:
        if doc in scores:
            raise ValueError('support-access-duplicate-score-id')
        if not isinstance(doc, str) or not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError('support-access-nonfinite-score')
        scores[doc] = float(value)
    if set(nodes) - set(scores):
        raise ValueError('support-access-missing-score')
    return sorted(nodes, key=lambda d: (-scores[d], d)), scores


def freeze_prediction(nodes, edges, score_pairs, saved_trial, traversal):
    """Outcome-blind baseline selection and exact historical policy replay."""
    ranked, scores = cached_ranking(nodes, score_pairs)
    if traversal.get('directed') is not True or traversal.get('max_hops') != 1:
        raise ValueError('support-access-requires-directed-one-hop')
    if len(nodes) < traversal['final_top_k']:
        raise ValueError('support-access-view-below-output-budget')
    replay = traverse_once(nodes, edges, scores, ranked[0], **traversal)
    # Check every recorded field, including offers, score values and candidates.
    if replay != saved_trial:
        raise ValueError('support-access-saved-prediction-mismatch')
    m = len(replay['selected_ids'])
    return {'schema_version': 'retrieval_arena.support_access_prediction.v1',
            'B1': ranked[:1], 'B4': ranked[:traversal['final_top_k']], 'Bm': ranked[:m],
            'semantic_ranking': ranked, 'G4': replay,
            'seed_retained': ranked[0] in replay['selected_ids']}


def _adjacency(nodes, edges):
    allowed = set(nodes)
    directed = {n: set() for n in nodes}
    weak = {n: set() for n in nodes}
    for s, t in edges:
        if s not in allowed or t not in allowed:
            raise ValueError('support-access-edge-outside-view')
        directed[s].add(t)
        weak[s].add(t)
        weak[t].add(s)
    return directed, weak


def failure_diagnostic(nodes, edges, trial, targets, traversal):
    """First failure stage in a fixed order; uncapped paths are diagnostics."""
    targets = set(targets)
    if not targets:
        return {'stage': 'unavailable', 'reason': 'no-known-target-in-view'}
    adj, weak = _adjacency(nodes, edges)
    seed = trial['seed_id']
    distances = _distances_from_targets([seed], adj)
    weak_distances = _distances_from_targets([seed], weak)
    finite = sorted(targets & set(distances), key=lambda t: (distances[t], t))
    reachable_one = sorted(t for t in finite if distances[t] <= 1)
    candidate_targets = sorted(targets & set(trial['candidate_ids']))
    selected_targets = sorted(targets & set(trial['selected_ids']))
    if selected_targets:
        stage = 'success'
    elif candidate_targets:
        stage = 'final_selection'
    elif reachable_one:
        stage = 'traversal_admission'
    elif finite:
        stage = 'beyond_hop_limit'
    else:
        stage = 'directed_unreachable'
    # ID-ordered shortest route: take the first predecessor at each distance.
    route = []
    if finite:
        current = finite[0]
        route = [current]
        while current != seed:
            current = min(s for s, ts in adj.items() if current in ts and distances.get(s) == distances[current] - 1)
            route.append(current)
        route.reverse()
    neighbors = sorted(adj[seed] - {seed})
    expected_offers = neighbors[:traversal['neighbor_budget']]
    admission = []
    for t in reachable_one:
        if t in trial['candidate_ids']: continue
        pos = neighbors.index(t) + 1 if t in neighbors else 0
        if t not in expected_offers:
            reason = 'neighbor_budget'
        elif t in trial['offered_ids'] or len(trial['candidate_ids']) >= traversal['candidate_budget']:
            reason = 'candidate_budget'
        else:
            raise ValueError('support-access-admission-trace-inconsistent')
        admission.append({'target_id': t, 'neighbor_position': pos, 'reason': reason,
                          'recorded_offer': t in trial['offered_ids']})
    weak_targets = sorted(targets & set(weak_distances))
    return {'stage': stage, 'minimum_directed_distance': distances[finite[0]] if finite else None,
            'directed_target_distances': {t: distances.get(t) for t in sorted(targets)},
            'weak_target_distances': {t: weak_distances.get(t) for t in sorted(targets)},
            'unreachable_subtype': ('weakly_connected_wrong_direction' if weak_targets else 'different_weak_components')
                if stage == 'directed_unreachable' else None,
            'weakly_reachable_target_count': len(weak_targets),
            'targets_in_different_weak_components': sorted(targets - set(weak_distances)),
            'nearest_target_route': route, 'one_hop_target_ids': reachable_one,
            'candidate_target_ids': candidate_targets, 'selected_target_ids': selected_targets,
            'seed_outdegree': len(adj[seed]), 'admission_exclusions': admission}


def evaluate_prediction(nodes, edges, prediction, known_targets, traversal, semantic_depths):
    """Evaluator only: project positives, score immutable predictions, attribute failure."""
    targets = set(known_targets) & set(nodes)
    ranking = prediction['semantic_ranking']
    if ranking != list(dict.fromkeys(ranking)) or set(ranking) != set(nodes):
        raise ValueError('support-access-ranking-universe')
    if any(k > len(ranking) or k < 1 for k in semantic_depths):
        raise ValueError('support-access-curve-depth-unavailable')
    selected = {p: set(prediction[p]) for p in ('B1', 'B4', 'Bm')}
    selected['G4'] = set(prediction['G4']['selected_ids'])
    support = {p: sorted(ids & targets) for p, ids in selected.items()}
    hits = {p: int(bool(ids)) for p, ids in support.items()}
    b, g = hits['B4'], hits['G4']
    cell = 'both_hit' if b and g else 'semantic_only' if b else 'graph_only' if g else 'neither_hit'
    overlap = sorted(set(support['B4']) & set(support['G4']))
    graph_extra = sorted(set(support['G4']) - set(support['B4']))
    semantic_extra = sorted(set(support['B4']) - set(support['G4']))
    target_ranks = {t: ranking.index(t) + 1 for t in sorted(targets)}
    historical = score_trial(prediction['G4'], targets)
    return {'schema_version': 'retrieval_arena.support_access_evaluation.v1',
            'eligible': bool(targets), 'known_target_ids_in_view': sorted(targets),
            'known_target_count_in_view': len(targets), 'hits': hits, 'cell': cell,
            'G4_minus_B4': g - b, 'G4_minus_B1': g - hits['B1'], 'G4_minus_Bm': g - hits['Bm'],
            'seed_miss_graph_hit': int(not hits['B1'] and g),
            'seed_retained': prediction['seed_retained'],
            'selected_counts': {p: len(ids) for p, ids in selected.items()},
            'known_support_selected': support, 'known_support_overlap': overlap,
            'graph_exclusive_known_support': graph_extra, 'semantic_exclusive_known_support': semantic_extra,
            'both_hit_support_relation': ('identical' if not graph_extra and not semantic_extra else 'different_or_additional') if b and g else None,
            'known_positive_set_coverage': {p: len(ids) / len(targets) if targets else None for p,ids in support.items()},
            'best_known_positive_semantic_rank': min(target_ranks.values()) if target_ranks else None,
            'known_positive_semantic_ranks': target_ranks,
            'semantic_depth_hits': {str(k): int(bool(set(ranking[:k]) & targets)) for k in semantic_depths},
            'historical_expansion_added_hit': historical['expansion_added_hit'],
            'candidate_hit': historical['candidate_hit'], 'candidate_count': historical['candidate_count'],
            'dead_end': historical['dead_end'],
            'failure': failure_diagnostic(nodes, edges, prediction['G4'], targets, traversal)}


def fraction(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'fraction': numerator / denominator if denominator else None}


def aggregate(rows):
    rows = [r for r in rows if r['eligible']]
    n = len(rows)
    if not n: return {'status': 'unavailable', 'reason': 'no-eligible-queries', 'queries': 0}
    cells = Counter(r['cell'] for r in rows)
    hits = {p: fraction(sum(r['hits'][p] for r in rows), n) for p in ('B1', 'B4', 'G4', 'Bm')}
    differences = {m: sum(r[m] for r in rows) / n for m in ('G4_minus_B4', 'G4_minus_B1', 'G4_minus_Bm')}
    assert math.isclose(differences['G4_minus_B4'], (cells['graph_only'] - cells['semantic_only']) / n, abs_tol=1e-15)
    stages = Counter(r['failure']['stage'] for r in rows)
    cross = {cell: dict(Counter(r['failure']['stage'] for r in rows if r['cell'] == cell)) for cell in CELLS}
    rank_strata = {}
    for stage in STAGES:
        subset = [r for r in rows if r['failure']['stage'] == stage]
        rank_strata[stage] = {'queries': len(subset), 'best_rank_values': [r['best_known_positive_semantic_rank'] for r in subset],
                              'B4_misses': sum(not r['hits']['B4'] for r in subset)}
    counts = {p: dict(sorted(Counter(r['selected_counts'][p] for r in rows).items())) for p in hits}
    return {'status': 'qualified', 'queries': n, 'hit_rates': hits, 'paired_cells': {c: cells[c] for c in CELLS},
            'paired_differences': differences, 'net_gain_identity_verified': True,
            'observed_B4_miss_opportunity': fraction(n - hits['B4']['numerator'], n),
            'recovery_fraction': fraction(cells['graph_only'], n - hits['B4']['numerator']),
            'loss_fraction': fraction(cells['semantic_only'], hits['B4']['numerator']),
            'seed_miss_graph_hit': sum(r['seed_miss_graph_hit'] for r in rows),
            'seed_retained_count': sum(r['seed_retained'] for r in rows),
            'historical_expansion_added_hit_count': sum(r['historical_expansion_added_hit'] for r in rows),
            'candidate_hit': fraction(sum(r['candidate_hit'] for r in rows), n),
            'candidate_count_mean': sum(r['candidate_count'] for r in rows) / n,
            'dead_ends': sum(r['dead_end'] for r in rows), 'selected_count_histograms': counts,
            'selected_count_means': {p: sum(r['selected_counts'][p] for r in rows) / n for p in hits},
            'underfilled_G4': sum(r['selected_counts']['G4'] < 4 for r in rows),
            'known_positive_set_coverage_mean': {p: sum(r['known_positive_set_coverage'][p] for r in rows)/n for p in hits},
            'known_positive_selected_count_total': {p: sum(len(r['known_support_selected'][p]) for r in rows) for p in hits},
            'overlap_known_positive_count': sum(len(r['known_support_overlap']) for r in rows),
            'graph_exclusive_known_positive_count': sum(len(r['graph_exclusive_known_support']) for r in rows),
            'semantic_exclusive_known_positive_count': sum(len(r['semantic_exclusive_known_support']) for r in rows),
            'both_hit_support_relations': dict(Counter(r['both_hit_support_relation'] for r in rows if r['cell'] == 'both_hit')),
            'failure_stages': {s: stages[s] for s in STAGES}, 'failure_stage_by_cell': cross,
            'unreachable_subtypes': dict(Counter(r['failure']['unreachable_subtype'] for r in rows if r['failure']['stage'] == 'directed_unreachable')),
            'rank_strata_by_failure_stage': rank_strata,
            'B4_miss_best_rank_values': [r['best_known_positive_semantic_rank'] for r in rows if not r['hits']['B4']],
            'B4_miss_known_positive_counts': [r['known_target_count_in_view'] for r in rows if not r['hits']['B4']],
            'semantic_depth_curve': {k: fraction(sum(r['semantic_depth_hits'][k] for r in rows), n) for k in rows[0]['semantic_depth_hits']}}


def paired_intervals(rows, *, job_ids, draws, seed):
    """One shared group resample carries every view and condition together.

    Duplicated text groups retain all original query rows, so the statistic
    remains query-macro rather than a mean of equally weighted group means.
    """
    if draws < 1: raise ValueError('support-access-bootstrap-draws')
    by_group = defaultdict(list)
    for row in rows: by_group[row['query_text_group']].append(row)
    groups = sorted(by_group)
    if not groups: return {}
    metrics = ('G4_minus_B4', 'G4_minus_B1', 'G4_minus_Bm')
    # Sufficient statistics per group/view preserve all rows while avoiding
    # materializing thousands of repeated dictionaries per bootstrap draw.
    sums = np.zeros((len(groups), len(job_ids), len(metrics)))
    ns = np.zeros((len(groups), len(job_ids)))
    job_index = {j:i for i,j in enumerate(job_ids)}
    for i, group in enumerate(groups):
        for row in by_group[group]:
            if row['eligible']:
                j = job_index[row['job_id']]
                ns[i,j] += 1
                sums[i,j] += [row[m] for m in metrics]
    estimates = np.full((draws, len(job_ids), len(metrics)), np.nan)
    rng = np.random.default_rng(seed)
    for draw in range(draws):
        indices = rng.integers(0, len(groups), size=len(groups))
        denom = ns[indices].sum(axis=0)
        np.divide(sums[indices].sum(axis=0), denom[:, None], out=estimates[draw], where=denom[:,None] > 0)
    out = {}
    for j, job in enumerate(job_ids):
        out[job] = {}
        for k, metric in enumerate(metrics):
            available = estimates[:,j,k][np.isfinite(estimates[:,j,k])]
            n = int(ns[:,j].sum())
            out[job][metric] = {'status': 'qualified' if n and len(available) else 'unavailable',
                'estimate': float(sums[:,j,k].sum()/n) if n else None,
                'interval_95': np.quantile(available, [.025,.975]).tolist() if len(available) else None,
                'queries': n, 'text_groups_in_joint_resample': len(groups), 'draws': draws,
                'available_draws': len(available), 'seed': seed,
                'unit': 'exact-query-text-group', 'estimand': 'query-macro',
                'conditional_on_fixed_graphs_and_saved_labels': True, 'all_views_carried_together': True}
    return out
