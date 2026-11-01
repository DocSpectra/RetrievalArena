"""Offline fixed-view objective study; inputs and study selection are caller-owned."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

import numpy as np

from .embedded_link_audit import portable_inventory, read, write
from .graph import local_objective as lo


def fixtures():
    cycle = lambda n: [[str(i), str((i + 1) % n)] for i in range(n)]
    path = lambda n: [[str(i), str(i + 1)] for i in range(n - 1)]
    return [
        {'id': 'path-blindness', 'nodes': list('0123'), 'edges': path(4), 'additions': [['0', '2'], ['0', '3']]},
        {'id': 'cycle-alias', 'nodes': list('0123'), 'edges': cycle(4), 'additions': [['0', '2'], ['1', '3']]},
        {'id': 'cycle-three', 'nodes': list('012'), 'edges': cycle(3), 'additions': []},
        {'id': 'isolated-disconnected', 'nodes': list('012345'), 'edges': cycle(3) + [['3', '4']], 'additions': [['3', '5']]},
        {'id': 'localized-motif', 'nodes': [str(i) for i in range(16)],
         'edges': cycle(3) + [[str(i), str(i+1)] for i in range(2, 15)], 'additions': [['2', '4'], ['10', '12']]},
        {'id': 'hub-concentration', 'nodes': list('01234567'), 'edges': path(8),
         'sets': [[['0', '2'], ['0', '3']], [['0', '2'], ['4', '6']]]},
        {'id': 'reciprocal-completion', 'nodes': list('012'), 'edges': path(3), 'additions': [['1', '0']]},
        {'id': 'duplicate-noop', 'nodes': list('012'), 'edges': path(3), 'additions': [['0', '1']]},
        {'id': 'duplicate-proposal', 'nodes': list('012'), 'edges': path(3), 'sets': [[['0', '2'], ['0', '2']]]},
        {'id': 'outside-region', 'nodes': list('012'), 'edges': path(3), 'additions': [['0', 'x']]},
        {'id': 'semantic-shortcut', 'nodes': list('0123'), 'edges': path(4), 'additions': [['0', '2']],
         'synthetic_semantic_support': False},
        {'id': 'no-distinction', 'nodes': list('012345'), 'edges': path(6), 'additions': [['0', '2'], ['3', '5']]},
    ]


def run(inp, out):
    import resource  # immutable Linux operator; objective library itself is portable
    started = time.monotonic()
    req = read(inp / 'operator-request.json')
    inv = [r for r in portable_inventory(inp) if r['path'] != 'operator-request.json']
    if inv != req['input_inventory'] or lo.ms.digest(inv) != req['input_inventory_sha256']:
        raise ValueError('input-inventory-mismatch')
    cfg = read(inp / 'policy.json'); p = lo.Policy(**cfg['numerical'])
    if set(x.name for x in out.iterdir()) != {'.retrieval-arena-output-owner'}:
        raise ValueError('output-not-fresh')
    cases = fixtures() if req['stage'] == 'fixtures' else read(inp / 'cases.json')
    results = []
    for case in cases:
        if req['stage'] != 'fixtures' and case.get('assignment') != 'development':
            raise ValueError('reserved-outcome-forbidden')
        nodes, edges = case['nodes'], case['edges']; rows = []
        for q in cfg['charges']:
            base = lo.solve(nodes, edges, q, p)
            spec = lo.spectral(nodes, edges, q, p)
            ordinary = lo.ordinary(nodes, edges)
            qzero = lo.spectral(nodes, edges, 0., p)
            reverse = [[t, s] for s, t in edges]
            rs = lo.spectral(nodes, reverse, q, p)
            # Conjugated saved feasible solution gives exact reversal identity,
            # distinct from whether independent local starts find equal optima.
            _, rt = lo.terms(nodes, reverse, q)
            reverse_energy = lo.energy_gradient(-np.array(base['phases']), rt)[0]
            comparisons = []; rejected = []
            sets = case.get('sets', [[[s, t]] for s, t in case.get('additions', [])])
            for additions in sets:
                try:
                    lo.eligible_additions(nodes, edges, additions)
                    if case.get('synthetic_semantic_support') is False:
                        rejected.append({'additions': additions, 'reason': 'fixture-semantic-support-failed',
                                         'status': 'synthetic policy fixture; not documentation judgment'})
                        continue
                    comparisons.append(lo.compare(nodes, edges, additions, q, p))
                except ValueError as exc:
                    rejected.append({'additions': additions, 'reason': str(exc)})
            # For the cyclic fixture, an analytic optimum is available.
            n = len(nodes); analytic = None
            if case['id'] in ('cycle-three', 'cycle-alias'):
                flux = np.angle(np.exp(2j*np.pi*q*n))
                analytic = n * (1 - np.cos(flux/n))
            rows.append({'q': q, 'baseline': base, 'spectral': spec, 'qzero_spectral': qzero,
                         'ordinary': ordinary, 'reverse_lambda_difference': rs['lambda1'] - spec['lambda1'],
                         'reverse_conjugate_energy_error': reverse_energy - base['energy_upper_bound'],
                         'analytic_phase_optimum': analytic,
                         'analytic_error': None if analytic is None else base['energy_upper_bound']-analytic,
                         'comparisons': comparisons, 'rejections': rejected,
                         'ranking': lo.ranking_verdict(comparisons, p)})
            if time.monotonic() - started > cfg['max_runtime_seconds']:
                raise TimeoutError('offline-resource-ceiling')
        result = {'id': case['id'], 'nodes': len(nodes), 'edge_count': len(edges),
                  'assignment': case.get('assignment', 'fixture'), 'case_sha256': lo.ms.digest(case),
                  'boundary': case.get('boundary', 'fixture; fixed nodes'), 'charges': rows}
        write(out / (case['id'].replace(':', '-') + '.json'), result)
        results.append(result)
        print(json.dumps({'case': case['id'], 'seconds': time.monotonic()-started}), flush=True)
    summary = {'schema_version': lo.VERSION + '.audit', 'stage': req['stage'],
               'cases': [{'id': r['id'], 'nodes': r['nodes'], 'edge_count': r['edge_count'],
                          'charges': [{'q': x['q'], 'energy': x['baseline']['energy_upper_bound'],
                            'restart_spread': x['baseline']['restart_spread'],
                            'all_stationary': x['baseline']['all_stationary'],
                            'lambda1': x['spectral']['lambda1'],
                            'cluster_size': x['spectral']['ground_cluster_size'],
                            'effective_support': x['spectral']['effective_support'],
                            'ranking': x['ranking'], 'rejections': x['rejections']} for x in r['charges']]}
                         for r in results],
               'policy': cfg, 'runtime_seconds': time.monotonic()-started,
               'peak_rss_kib_linux': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               'versions': lo.ms.runtime_identity(),
               'image': {k: os.environ.get(k) for k in ['RA_SOURCE_COMMIT','RA_SOURCE_TREE',
                         'RA_IMAGE_REFERENCE','RA_DEPENDENCY_LOCK_SHA256','RA_PLATFORM']},
               'provider_accounting': {'attempts': 0, 'spend_usd': 0, 'outbound_calls': 0},
               'reserved_outcomes_inspected': False}
    write(out / 'summary.json', summary)
    invout = portable_inventory(out)
    if sum(x['size_bytes'] for x in invout) > cfg['max_output_bytes']:
        raise ValueError('output-budget')
    write(out / 'manifest.json', {'schema_version': lo.VERSION + '.manifest',
        'outputs': invout, 'outputs_sha256': lo.ms.digest(invout),
        'input_inventory_sha256': req['input_inventory_sha256']})
    return summary
