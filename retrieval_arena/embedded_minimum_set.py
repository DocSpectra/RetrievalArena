"""Explicit k=1 finite-estimate set selector; historical v2 remains unchanged."""
import copy
import math
from dataclasses import asdict, replace

import numpy as np

from . import embedded_selection_v2 as v2
from .embedded_links import digest
from .graph import block_phase as bp

VERSION = 'retrieval_arena.embedded_selection.minimum_set.v1'


class _ExistingSolves(dict):
    def __contains__(self, key):
        if not super().__contains__(key):
            raise ValueError('score-provenance-missing-solve')
        return True


def classify(rows, atol=1e-6):
    """Rows are in frozen semantic order. No transitive overlap clustering."""
    detail = {'ranges': [], 'members': [], 'within_set_spread': None,
              'outsider_gap': None, 'fallback_reason': None}
    if not rows:
        return 'empty_pool', None, detail
    if any(r['status'] == 'resource_limit' for r in rows):
        detail['fallback_reason'] = 'unavailable-highest-tier-resource-limit'
        return 'runtime_exhaustion', None, detail
    valid = all(r['status'] == 'finite_estimate' and
                len(r.get('schedule_costs', [])) == 2 and
                all(math.isfinite(x) for x in r['schedule_costs']) and
                min(r['schedule_costs']) >= -1e-9 for r in rows)
    if not valid:
        detail['fallback_reason'] = 'unavailable-highest-tier-alternative'
        return 'unscorable_candidate', None, detail
    low = [max(0., min(r['schedule_costs'])) for r in rows]
    high = [max(0., max(r['schedule_costs'])) for r in rows]
    detail['ranges'] = [[l, u] for l, u in zip(low, high)]
    if len(rows) == 1:
        detail.update(members=[0], within_set_spread=high[0]-low[0])
        return 'single_option', 0, detail
    if max(high)-min(low) <= atol:
        detail.update(members=list(range(len(rows))), within_set_spread=max(high)-min(low),
                      fallback_reason='all-candidate-tie')
        return 'all_candidate_tie', 0, detail
    winner = min(range(len(rows)), key=lambda i: high[i])
    if all(high[winner]+atol < low[j] for j in range(len(rows)) if j != winner):
        detail.update(members=[winner], within_set_spread=high[winner]-low[winner],
                      outsider_gap=min(low[j]-high[winner] for j in range(len(rows)) if j != winner))
        return 'resolved_finite_order', winner, detail
    u = min(high)
    members = [i for i in range(len(rows)) if low[i] <= u+atol]
    spread = max(high[i] for i in members)-min(low[i] for i in members)
    outsiders = [i for i in range(len(rows)) if i not in members]
    gap = min((low[j]-max(high[i] for i in members) for j in outsiders), default=None)
    detail.update(members=members, within_set_spread=spread, outsider_gap=gap)
    if len(members) >= 2 and spread <= atol and all(
            low[j] > max(high[i] for i in members)+atol for j in outsiders):
        return 'separated_minimum_set', members[0], detail
    detail['fallback_reason'] = ('minimum-envelope-too-wide' if spread > atol else
                                 'outsider-not-strictly-separated')
    return 'unresolved_ranges', None, detail


def select_minimum_set(pool, group_graph, policy=None, score_cache=None, *, arm='M', scorer=None):
    cfg = dict(v2.DEFAULT_POLICY, **(policy or {}))
    if cfg['maximum'] != 1:
        raise ValueError('minimum-set-k1-only')
    # Reuse exact admission, whole-tier scoring, S/Z and old cache provenance.
    old = v2.select_embedded_v2(pool, group_graph, cfg, score_cache, arm=arm, scorer=scorer)
    result = copy.deepcopy(old)
    result.update(schema_version=VERSION, score_provenance={
        'schema_version': v2.VERSION, 'binding_sha256': old['binding_sha256'],
        'graph_sha256': old['graph_sha256'], 'pool_sha256': old['pool_sha256'],
        'policy': cfg}, binding_sha256=digest([VERSION, old['binding_sha256']]))
    if result['trace'] and arm == 'M':
        step = result['trace'][0]
        status, index, detail = classify(step['raw_scores'], cfg['rank_atol'])
        tier = step['tier']; selected = tier[0 if index is None else index]
        ids = detail.pop('members')
        detail.update(member_ids=[tier[i] for i in ids],
                      range_by_id=dict(zip(tier, detail.pop('ranges'))))
        p = next(p for p in pool if p['proposal_id'] == selected)
        step.update(status=status, selected=selected, minimum_set=detail,
                    selected_tie_id=p.get('tie_id', selected),
                    semantic_fallback=index is None or status == 'all_candidate_tie',
                    magnetic_changed_semantic_choice=selected != tier[0] and status in (
                        'separated_minimum_set', 'resolved_finite_order'))
        result['selected'] = [selected]
        result['magnetic_changed_steps'] = int(step['magnetic_changed_semantic_choice'])
    return result


def replay_scores(pool, graph, policy, historical, audit):
    """Admit v2 scores only after checking bindings, keys, starts and arithmetic.

    Missing solve keys fail before bp.compare can invoke any optimizer. Score
    version is deliberately v2; the new selection version is never substituted.
    """
    cfg = dict(v2.DEFAULT_POLICY, **policy)
    if cfg != historical['policy'] or historical['schema_version'] != v2.VERSION or historical['arm'] != 'M':
        raise ValueError('score-provenance-policy')
    if digest([v2.VERSION, graph, pool, cfg]) != historical['binding_sha256']:
        raise ValueError('score-provenance-binding')
    admitted = bp.lo.ms.admit_graph(graph['nodes'], graph['edges'])
    if digest(admitted) != historical['graph_sha256'] or digest(pool) != historical['pool_sha256']:
        raise ValueError('score-provenance-graph-pool')
    cache = audit['solves']; count = 0
    for key, fit in cache.items():
        nodes, edges = fit['support_nodes'], fit['support_edges']
        policies = [replace(bp.lo.Policy(**cfg['numerical']), seed=s) for s in cfg['seeds']]
        matched = [p for p in policies if key == bp.lo.ms.digest([bp.VERSION, nodes, edges, cfg['q'], asdict(p)])]
        if len(matched) != 1:
            raise ValueError('score-provenance-solve-key')
        p = matched[0]; _, terms = bp.lo.terms(nodes, edges, cfg['q'])
        initials = bp.lo.starts(nodes, p)
        if len(fit['starts']) != p.starts:
            raise ValueError('score-provenance-starts')
        errors = []
        for i, start in enumerate(fit['starts']):
            initial = initials[i].copy()
            for component in bp.lo.ms.weak_components(nodes, edges):
                indices = [nodes.index(n) for n in component]
                initial[indices] -= initial[nodes.index(min(component))]
            if start['start'] != i or start['initial_sha256'] != bp.lo.ms.digest(initial.tolist()) or start['initial_phases'] != initial.tolist():
                raise ValueError('score-provenance-seed-start')
            energy, gradient = bp.lo.energy_gradient(np.array(start['final_phases']), terms)
            grad = float(np.max(np.abs(gradient), initial=0.))
            errors.append(abs(energy-start['energy']))
            if errors[-1] > p.identity_atol or abs(grad-start['gradient_inf']) > p.identity_atol or start['stationary'] != (grad <= p.stationarity):
                raise ValueError('score-provenance-energy-gradient')
            count += 1
        valid = [i for i, s in enumerate(fit['starts']) if s['stationary']]
        best = min(valid or range(p.starts), key=lambda i: (fit['starts'][i]['energy'], i))
        if best != fit['best_start'] or fit['energy_upper_bound'] != fit['starts'][best]['energy'] or fit['phases'] != fit['starts'][best]['final_phases']:
            raise ValueError('score-provenance-best-start')
        if fit['has_stationary_solution'] != bool(valid) or fit['identity_error'] != max(errors) or fit['selected_gradient_inf'] != fit['starts'][best]['gradient_inf']:
            raise ValueError('score-provenance-fit-metadata')
    costs = audit['candidate_costs']
    if set(costs) != {p['proposal_id'] for p in pool}:
        raise ValueError('score-provenance-candidate-coverage')
    for candidate in pool:
        pid = candidate['proposal_id']; row = costs[pid]
        if row['status'] in ('resource_limit', 'unscorable'):
            # Unavailable alternatives stay unavailable, never re-solved or removed.
            continue
        schedules = []
        checked_cache = _ExistingSolves(cache)
        for seed in cfg['seeds']:
            schedules.append(bp.compare(graph['nodes'], graph['edges'],
                [[candidate['source_id'], candidate['target_id']]], cfg['q'], 'block',
                replace(bp.lo.Policy(**cfg['numerical']), seed=seed), checked_cache))
        rebuilt = {'status': next((r['status'] for r in schedules if r['status'] != 'finite_estimate'), 'finite_estimate'),
                   'schedule_costs': [r['cost'] for r in schedules], 'schedules': schedules}
        if row != rebuilt:
            raise ValueError('score-provenance-comparison')
    replay = v2.select_embedded_v2(pool, graph, cfg, {}, scorer=lambda prefix: costs[prefix[0]])
    for name in ('selected', 'trace', 'binding_sha256'):
        if replay[name] != historical[name]:
            raise ValueError('score-provenance-old-v2-replay')
    return {'fits': len(cache), 'starts': count, 'candidates': len(costs), 'v2_replay_equal': True}
