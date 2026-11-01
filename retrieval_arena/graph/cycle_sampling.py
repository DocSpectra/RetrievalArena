"""Outcome-independent, budgeted hash-priority sampling of nonreciprocal cycles.

Contract: docs/evaluations/magnetic_attribution.md. No numerical dependencies.
"""
from __future__ import annotations

import hashlib
import json

VERSION = 'nonreciprocal_cycle_sample.v2'


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def priority(domain, seed, corpus, length, ids):
    return hashlib.sha256(canonical_bytes([VERSION, domain, seed, corpus, length, list(ids)])).hexdigest()


def sample_cycles(nodes, edges, *, corpus, seed, lengths=(3, 4, 5, 6), quota=2, budget=250000):
    """Return (compact result, canonical encounter ledger bytes), without reading outcomes.

    Each created partial path, including a root, costs one expansion. Closing
    an arc creates no new partial path. A cap is recorded only if an otherwise
    eligible next partial path cannot be created. Search continues past quota.
    """
    if (not isinstance(seed, int) or not isinstance(corpus, str) or not corpus
            or not isinstance(quota, int) or quota < 1 or not isinstance(budget, int) or budget < 1
            or not lengths or len(set(lengths)) != len(lengths)
            or any(not isinstance(n, int) or n < 3 for n in lengths)):
        raise ValueError('invalid-cycle-sampling-parameters')
    nodes = list(nodes)
    if len(set(nodes)) != len(nodes) or any(not isinstance(v, str) for v in nodes):
        raise ValueError('invalid-cycle-sampling-nodes')
    es = {tuple(e) for e in edges}
    universe = set(nodes)
    if any(s == t or s not in universe or t not in universe for s, t in es):
        raise ValueError('invalid-cycle-sampling-arcs')
    successors = {v: [] for v in nodes}
    for s, t in es:
        if (t, s) not in es:
            successors[s].append(t)
    by_length, ledger, selected = {}, [], []
    for length in lengths:
        order = sorted(nodes, key=lambda v: (priority('root', seed, corpus, length, [v]), v))
        succ = {v: sorted(successors[v], key=lambda t: (
            priority('successor', seed, corpus, length, [v, t]), t)) for v in nodes}
        expansions, capped, seen, candidates = 0, False, set(), []
        stream = hashlib.sha256()
        for start in order:
            if expansions == budget:
                capped = True
                break
            expansions += 1
            path, on, stack = [start], {start}, [iter(succ[start])]
            while stack:
                nxt = next(stack[-1], None)
                if nxt is None:
                    stack.pop()
                    on.remove(path.pop())
                    continue
                if len(path) == length:
                    if nxt == start:
                        cycle = tuple(path)  # root is the lexical minimum
                        if cycle not in seen:
                            seen.add(cycle)
                            h = priority('selection', seed, corpus, length, cycle)
                            candidate = {'length': length, 'cycle': list(cycle), 'selection_hash': h}
                            line = canonical_bytes(candidate) + b'\n'
                            stream.update(line)
                            ledger.append(line)
                            candidates.append(candidate)
                    continue
                if nxt <= start or nxt in on:
                    continue
                if expansions == budget:
                    capped = True
                    break
                expansions += 1
                path.append(nxt)
                on.add(nxt)
                stack.append(iter(succ[nxt]))
            if capped:
                break
        chosen = sorted(candidates, key=lambda r: (r['selection_hash'], r['cycle']))[:quota]
        selected.extend(chosen)
        by_length[str(length)] = {
            'expansions': expansions, 'candidates': len(candidates),
            'status': 'capped' if capped else 'exhaustive',
            'empty_interpretation': (None if candidates else
                                    'none_found_within_search_budget' if capped else 'no_eligible_cycles'),
            'candidate_stream_sha256': stream.hexdigest(), 'selected': chosen}
    return {'selection_contract': VERSION,
            'parameters': {'corpus': corpus, 'seed': seed, 'lengths': list(lengths), 'quota': quota, 'budget': budget},
            'by_length': by_length, 'selected': selected,
            'cycles': [r['cycle'] for r in selected],
            'arcs': [[[c[i], c[(i + 1) % len(c)]] for i in range(len(c))]
                     for c in [r['cycle'] for r in selected]],
            'ledger_sha256': hashlib.sha256(b''.join(ledger)).hexdigest(),
            'nonreciprocal_arcs': sum(map(len, successors.values())),
            'limitation': 'hash sample of encountered candidates; capped traversal is not uniform over all cycles'}, b''.join(ledger)
