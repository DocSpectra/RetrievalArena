"""Opt-in individual and selected-set constraints; no scores or model calls.

Historical entry points retain pool-wide reciprocal rejection. V2 permits
conflicting alternatives and checks the actual assignment against original
edges before scoring, patching and independent replay.
"""
from itertools import combinations

from . import embedded_links as e
from . import embedded_ledger_v2 as ledger

VERSION = 'retrieval_arena.embedded_constraints.v2'


def admit_version(version):
    if version not in (None, VERSION):
        raise ValueError('unknown-embedded-constraint-version')
    return version


def pool_conflicts(pool):
    rows = ledger.pool_conflicts(pool)
    for p, q in combinations(sorted(pool, key=lambda p: p['proposal_id']), 2):
        if (p['corpus_id'], p['snapshot_id']) != (q['corpus_id'], q['snapshot_id']):
            continue
        if p['source_id'] == q['source_id']:
            rows.append({'proposal_ids': [p['proposal_id'], q['proposal_id']],
                         'reason': 'same-source-cap'})
            # The one-source cap implies the one-new-link-per-sentence cap.
            # Equal sentence text need not mean the same occurrence.
    return sorted(rows, key=lambda r: (r['proposal_ids'], r['reason']))


def validate_selected(pool, selected, baseline_edges):
    """Fail closed on joint conflicts, including conflicts absent from metadata.

    Accepts full proposals or selector projections; syntax and bytes are checked
    individually by the editor. Baseline must be the frozen original graph.
    """
    ids = [p['proposal_id'] for p in pool]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate-proposal-id')
    if len(selected) != len(set(selected)) or set(selected) - set(ids):
        raise ValueError('selected-pool-binding')
    by_id = {p['proposal_id']: p for p in pool}
    chosen = [by_id[pid] for pid in selected]
    original = set(map(tuple, baseline_edges)); added = set(); sources = set()
    for p in chosen:
        pair = (p['source_id'], p['target_id']); reverse = pair[::-1]
        if pair[0] == pair[1]:
            raise ValueError('selected-self-link')
        if pair in original or reverse in original:
            raise ValueError('selected-pair-present-in-original')
        if pair in added:
            raise ValueError('selected-duplicate-page-pair')
        if reverse in added:
            raise ValueError('selected-reciprocal-additions')
        if pair[0] in sources:
            raise ValueError('selected-same-source-cap')
        if set(p.get('conflicts', [])) & set(selected):
            raise ValueError('selected-declared-conflict')
        added.add(pair); sources.add(pair[0])
    return {'constraint_version': VERSION, 'selected': sorted(selected),
            'added_pairs': sorted(added), 'original_sha256': e.digest(sorted(original)),
            'no_baseline_pair_in_either_direction': True,
            'no_selected_reciprocals': True, 'same_source_cap': 1}


def replay_assignment(pool, selected, baseline_edges, originals, facts):
    """Independent assignment validation plus exact idempotent patch replay."""
    proof = validate_selected(pool, selected, baseline_edges)
    by_id = {p['proposal_id']: p for p in pool}; edited = dict(originals)
    if set(facts) != set(selected):
        raise ValueError('assignment-facts-coverage')
    for pid in selected:
        p = by_id[pid]; fact = facts[pid]
        if fact.get('constraint_version') != VERSION:
            raise ValueError('assignment-constraint-binding')
        if fact['added_edges'] != [[p['source_id'], p['target_id']]]:
            raise ValueError('assignment-pair-binding')
        raw = originals[p['source_id']]; new = e.replay_patch(raw, fact)
        if e.replay_patch(new, fact) != new:
            raise ValueError('assignment-idempotence')
        edited[p['source_id']] = new
    return edited, proof
