"""Matched semantic/magnetic selection with whole-prefix scores and abstention.

The pool is independently validated before entry. Schedule ranges describe a
finite algorithm, never confidence or global optimization error bounds.
"""
from dataclasses import asdict, replace
import json
import math
import multiprocessing as mp
from pathlib import Path
import tempfile
import time

from .embedded_link_selection import feasible_sets
from .embedded_links import digest
from .graph import block_phase as bp

VERSION = 'retrieval_arena.embedded_selection.v2'
DEFAULT_POLICY = {'maximum': 4, 'q': .25, 'seeds': [3501, 3502],
                  'rank_atol': 1e-6, 'wall_seconds': 600,
                  'max_output_bytes': 100 * 1024**2,
                  'numerical': asdict(bp.lo.Policy())}


def _worker(path, graph, additions, q, policy, seeds, cache, maximum_bytes):
    """Only the child may enter an unbounded native optimizer call."""
    try:
        rows = [bp.compare(graph['nodes'], graph['edges'], additions, q, 'block',
                           replace(bp.lo.Policy(**policy), seed=seed), cache)
                for seed in seeds]
        result = {'rows': rows, 'cache': cache}
        raw = json.dumps(result, sort_keys=True, allow_nan=False).encode()
        if len(raw) > maximum_bytes:
            result = {'failure': 'resource_limit', 'reason': 'numerical-output-limit'}
    except Exception as exc:
        result = {'failure': 'unscorable', 'reason': type(exc).__name__ + ':' + str(exc)}
    Path(path).write_text(json.dumps(result, sort_keys=True, allow_nan=False), encoding='utf-8')


class BoundedBlockScorer:
    """Persistent solve cache; watchdog terminates the process at the wall cap.

    Spawn is supported on all Python platforms. The container still supplies
    CPU/RAM/thread limits. A killed solve retains its attempted request, elapsed
    time, previously completed cache, and explicit unavailable result.
    """
    def __init__(self, graph, pool, policy, cache=None, worker=_worker):
        self.graph = graph
        self.pairs = {p['proposal_id']: [p['source_id'], p['target_id']] for p in pool}
        self.policy = policy
        self.cache = {} if cache is None else cache
        self.deadline = time.monotonic() + policy['wall_seconds']
        self.attempts = []
        self.worker = worker

    def __call__(self, prefix):
        additions = [self.pairs[p] for p in prefix]
        attempt = {'prefix': list(prefix), 'additions': additions,
                   'cache_before': sorted(self.cache), 'status': 'attempted'}
        self.attempts.append(attempt)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            attempt['status'] = 'resource_limit'
            return {'status': 'resource_limit', 'reason': 'group-wall-exhausted'}
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'result.json')
            proc = mp.get_context('spawn').Process(target=self.worker, args=(
                path, self.graph, additions, self.policy['q'], self.policy['numerical'],
                self.policy['seeds'], self.cache, self.policy['max_output_bytes']))
            proc.start()
            proc.join(max(0, self.deadline - time.monotonic()))
            if proc.is_alive():
                proc.terminate(); proc.join(1)
                if proc.is_alive(): proc.kill(); proc.join()
                result = {'status': 'resource_limit', 'reason': 'process-wall-watchdog'}
            elif proc.exitcode != 0 or not Path(path).exists():
                result = {'status': 'unscorable', 'reason': 'numerical-worker-failed'}
            else:
                data = json.loads(Path(path).read_bytes())
                if 'failure' in data:
                    result = {'status': data['failure'], 'reason': data['reason']}
                else:
                    self.cache.update(data['cache'])
                    rows = data['rows']
                    result = {'status': next((r['status'] for r in rows
                                             if r['status'] != 'finite_estimate'), 'finite_estimate'),
                              'schedule_costs': [r['cost'] for r in rows], 'schedules': rows}
        attempt.update(status=result['status'], elapsed_seconds=time.monotonic()-started,
                       cache_after=sorted(self.cache))
        return result


def classify(rows, atol):
    """Never remove a failed alternative from the current semantic tier."""
    if any(r['status'] == 'resource_limit' for r in rows): return 'runtime_exhaustion', None
    if any(r['status'] != 'finite_estimate' for r in rows): return 'unscorable_candidate', None
    if len(rows) == 1: return 'single_option', 0
    low = [max(0., min(r['schedule_costs'])) for r in rows]
    high = [max(0., max(r['schedule_costs'])) for r in rows]
    if max(high)-min(low) <= atol: return 'all_candidate_tie', 0
    winner = min(range(len(rows)), key=lambda i: high[i])
    if all(high[winner]+atol < low[i] for i in range(len(rows)) if i != winner):
        return 'resolved_finite_order', winner
    minima = [i for i in range(len(rows)) if max(high[i], high[winner])-min(low[i], low[winner]) <= atol]
    if len(minima) > 1: return 'partial_minimum_tie', None
    return 'unresolved_ranges', None


def select_embedded_v2(pool, group_graph, policy=None, score_cache=None, *, arm='M', scorer=None):
    cfg = dict(DEFAULT_POLICY, **(policy or {}))
    if arm not in ('S', 'Z', 'M') or cfg['q'] != .25 or cfg['seeds'] != [3501, 3502] or cfg['rank_atol'] != 1e-6:
        raise ValueError('v2-frozen-policy')
    if cfg['wall_seconds'] <= 0 or cfg['max_output_bytes'] <= 0:
        raise ValueError('v2-resource-policy')
    if cfg['numerical'] != asdict(bp.lo.Policy()): raise ValueError('v2-numerical-policy')
    graph = bp.lo.ms.admit_graph(group_graph['nodes'], group_graph['edges'])
    for p in pool:
        if type(p['semantic_score']) is not int or p['semantic_score'] not in (5, 6):
            raise ValueError('v2-semantic-score-tier')
        bp.lo.eligible_additions(graph['nodes'], graph['edges'], [[p['source_id'], p['target_id']]])
    feasible = feasible_sets(pool, cfg['maximum'], constraint_version=cfg.get('constraint_version'))
    budget = max(map(len, feasible), default=0)
    completions = [c for c in feasible if len(c) == budget]
    order = sorted(pool, key=lambda p: (-p['semantic_score'], p.get('tie_id', p['proposal_id']), p['proposal_id']))
    cache = {} if score_cache is None else score_cache
    if scorer is None and arm == 'M': scorer = BoundedBlockScorer(graph, pool, cfg)
    binding = digest([VERSION, group_graph, pool, cfg])
    weight = float(sum(bp.lo.terms(graph['nodes'], graph['edges'], cfg['q'])[1][2]))
    chosen = []; trace = []
    while len(chosen) < budget:
        available = [p for p in order if p['proposal_id'] not in chosen and
                     any(set(chosen) | {p['proposal_id']} <= c for c in completions)]
        tier = [p for p in available if p['semantic_score'] == available[0]['semantic_score']]
        scores = []; cache_ids = []; prefixes = []
        for p in tier:
            prefix = tuple(chosen + [p['proposal_id']]); prefixes.append(list(prefix))
            if cfg.get('constraint_version'):
                from .embedded_constraints import validate_selected
                validate_selected(pool, prefix, graph['edges'])
            key = digest([binding, arm if arm != 'M' else 'q=.25', sorted(prefix)])
            cache_ids.append(key)
            if arm == 'M':
                if key not in cache:
                    try: cache[key] = scorer(prefix)
                    except Exception as exc:
                        cache[key] = {'status':'unscorable','reason':'callback-failed:'+type(exc).__name__}
                r = cache[key]
                if r['status'] == 'finite_estimate':
                    costs = r.get('schedule_costs', [])
                    if len(costs) != 2 or not all(math.isfinite(x) for x in costs):
                        r = {'status': 'identity_failure', 'reason': 'invalid-schedule-costs'}
                    elif min(costs) < -1e-9: r = dict(r, status='negative_marginal')
                scores.append(r)
            elif arm == 'Z':
                scores.append({'status': 'finite_estimate', 'q': 0,
                               'schedule_costs': [0., 0.], 'analytic_zero': True})
        if arm == 'S': status, index = ('semantic_only', 0)
        else: status, index = classify(scores, cfg['rank_atol'])
        fallback = index is None or status == 'all_candidate_tie'
        pick = tier[0 if index is None else index]
        changed = arm == 'M' and status == 'resolved_finite_order' and pick['proposal_id'] != tier[0]['proposal_id']
        trace.append({'step': len(chosen)+1, 'completion_preserving_alternatives': [p['proposal_id'] for p in available],
                      'semantic_tier': tier[0]['semantic_score'], 'tier': [p['proposal_id'] for p in tier],
                      'tentative_prefixes': prefixes, 'raw_scores': scores, 'score_cache_ids': cache_ids,
                      'frozen_denominator': weight, 'status': status, 'semantic_fallback': fallback,
                      'semantic_choice_at_prefix': tier[0]['proposal_id'], 'selected': pick['proposal_id'],
                      'magnetic_changed_semantic_choice': changed,
                      'requested_budget': cfg['maximum'], 'feasible_budget': budget,
                      'achieved_budget_after_step': len(chosen)+1})
        chosen.append(pick['proposal_id'])
    return {'schema_version': VERSION, 'binding_sha256': binding, 'graph_sha256': digest(graph),
            'pool_sha256': digest(pool), 'policy': cfg, 'arm': arm, 'requested_budget': cfg['maximum'],
            'feasible_budget': budget, 'achieved_budget': len(chosen), 'selected': chosen, 'trace': trace,
            'empty_pool': not pool, 'magnetic_changed_steps': sum(t['magnetic_changed_semantic_choice'] for t in trace),
            'numerical_attempts': getattr(scorer, 'attempts', []), 'global_optimality_claimed': False}
