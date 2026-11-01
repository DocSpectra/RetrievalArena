"""Explicit authored-fixture provenance while retaining v1's host edit/replay.

No fixture assertion is a provider response. V2 is a tagged extension; a checked
internal projection lets the unchanged v1 state machine enforce transitions.
The public v2 log hashes and trusted tip are always computed over v2 bytes.
"""
import copy
from . import embedded_links as e

VERSION = 'retrieval_arena.embedded_links.v2'
PROPOSAL_SCHEMA = copy.deepcopy(e.PROPOSAL_SCHEMA)
PROPOSAL_SCHEMA['properties']['schema_version'] = {'const': VERSION+'.proposal'}
FIXTURE_PROVENANCE = e._obj({'kind': {'const': 'authored_fixture'},
    'manifest_sha256': e.H, 'candidate_key': e.S, 'timestamp': e.S})
MODEL_PROVENANCE = copy.deepcopy(e.PROPOSAL_SCHEMA['properties']['provenance'])
MODEL_PROVENANCE['properties']['kind'] = {'const': 'model_response'}
MODEL_PROVENANCE['required'].append('kind')
MODEL_PROVENANCE['properties']['status'] = {'const':'model-response'}
# Conditional provenance schemas are emitted and enforced by validate_proposal.
PROPOSAL_SCHEMA['properties']['provenance'] = {}
PROPOSAL_SCHEMA['allOf'] = [{'if': {'properties': {'provenance': {'properties': {'kind': {'const': kind}}}}},
    'then': {'properties': {'provenance': schema}}} for kind, schema in
    [('authored_fixture', FIXTURE_PROVENANCE), ('model_response', MODEL_PROVENANCE)]]


def public_schemas():
    """Full JSON Schema unions; the dependency-free validator uses tagged checks."""
    proposal=copy.deepcopy(PROPOSAL_SCHEMA); proposal.pop('allOf')
    proposal['properties']['provenance']={'oneOf':[FIXTURE_PROVENANCE, MODEL_PROVENANCE]}
    event_schema=copy.deepcopy(e.EVENT_SCHEMA)
    event_schema['properties']['schema_version']={'const':VERSION+'.event'}
    event_schema['properties']['authority']={'enum':['model','reviewer','host','fixture_author']}
    fixture_review=e._obj({'kind':{'const':'authored_fixture'},'manifest_sha256':e.H,
        'semantic_score':{'const':6},'relation':{'enum':list(e.RELATIONS)},'decision':{'const':'eligible'}})
    payloads=dict(e.EVENT_PAYLOADS,proposed=proposal,
                  reviewed={'oneOf':[e.EVENT_PAYLOADS['reviewed'],fixture_review]})
    event_schema['allOf']=[{'if':{'properties':{'type':{'const':typ}}},
        'then':{'properties':{'payload':schema}}} for typ,schema in payloads.items()]
    return proposal,event_schema


def validate_proposal(p):
    e.validate_schema(p, PROPOSAL_SCHEMA)
    provenance = p['provenance']; kind = provenance.get('kind')
    schema = {'authored_fixture': FIXTURE_PROVENANCE, 'model_response': MODEL_PROVENANCE}.get(kind)
    if schema is None: raise ValueError('v2-provenance-kind')
    e.validate_schema(provenance, schema)
    return p


def _legacy_proposal(p):
    """Private validation projection, never persisted as scientific provenance."""
    validate_proposal(p)
    row = copy.deepcopy(p); row['schema_version'] = e.VERSION+'.proposal'
    if p['provenance']['kind'] == 'authored_fixture':
        row['provenance'] = {'provider': 'internal-validation-projection', 'model': 'internal-validation-projection',
            'prompt_version': 'internal', 'schema_version': 'internal', 'attempt_id': 'internal',
            'timestamp': p['provenance']['timestamp'], 'response_sha256': p['provenance']['manifest_sha256'],
            'source_access': [p['source_id'], p['target_id']], 'retry_of': None, 'status': 'mock-model'}
    else: row['provenance'].pop('kind')
    return row


def apply_edit(raw, target_raw, proposal, pages, edges, region, limits, proposed_pool=()):
    return e.apply_edit(raw, target_raw, _legacy_proposal(proposal), pages, edges, region, limits,
                        proposed_pool=[_legacy_proposal(p) for p in proposed_pool])


def pool_conflicts(pool):
    return e.pool_conflicts([_legacy_proposal(p) for p in pool])


def event(log, proposal, typ, payload, authority='host'):
    row = {'schema_version': VERSION+'.event', 'previous_sha256': e.digest(log[-1]) if log else '0'*64,
           **{k: proposal[k] for k in ('proposal_id', 'run_id', 'arm_id', 'region_id')},
           'type': typ, 'authority': authority, 'timestamp': proposal['provenance']['timestamp'],
           'supersedes_event_id': None, 'payload': payload}
    row['event_id'] = e.digest(row)
    replay([*log, row])
    return row


def replay(log, trusted_tip=None):
    legacy = []; seen = {}; previous = '0'*64; proposals = {}; mapping = {}
    for row in log:
        if row['event_id'] in seen:
            if seen[row['event_id']] != row: raise ValueError('duplicate-event-tamper')
            continue
        if row['schema_version'] != VERSION+'.event': raise ValueError('v2-event-version')
        if e.digest({k:v for k,v in row.items() if k!='event_id'}) != row['event_id'] or row['previous_sha256'] != previous:
            raise ValueError('ledger-chain')
        r = copy.deepcopy(row); r['schema_version'] = e.VERSION+'.event'
        r['previous_sha256'] = e.digest(legacy[-1]) if legacy else '0'*64
        pid = r['proposal_id']
        if r['type'] == 'proposed':
            validate_proposal(r['payload']); proposals[pid] = r['payload']
            fixture = r['payload']['provenance']['kind'] == 'authored_fixture'
            if r['authority'] != ('fixture_author' if fixture else 'model'): raise ValueError('provenance-authority')
            r['payload'] = _legacy_proposal(r['payload']); r['authority'] = 'model'
        elif r['type'] == 'schema_validated':
            if r['payload']['proposal_sha256'] != e.digest(proposals[pid]): raise ValueError('schema-binding')
            r['payload']['proposal_sha256'] = e.digest(_legacy_proposal(proposals[pid]))
        elif r['type'] == 'reviewed' and proposals[pid]['provenance']['kind'] == 'authored_fixture':
            expected = e._obj({'kind': {'const':'authored_fixture'}, 'manifest_sha256':e.H,
                              'semantic_score':{'const':6}, 'relation':{'enum':list(e.RELATIONS)},
                              'decision':{'const':'eligible'}})
            e.validate_schema(r['payload'], expected)
            if r['authority'] != 'fixture_author' or r['payload']['manifest_sha256'] != proposals[pid]['provenance']['manifest_sha256']:
                raise ValueError('fixture-review-binding')
            r['authority'] = 'reviewer'
            r['payload'] = {'reviewer_provider':'internal-validation-projection','reviewer_model':'internal',
                'rubric_version':'internal','decision':'eligible','evidence':proposals[pid]['target_evidence'],
                'uncertainty':'authored assertion','response_sha256':row['payload']['manifest_sha256'],
                'blinded_input_sha256':e.digest({}),'status':'mock-model'}
        if r['supersedes_event_id'] is not None:
            r['supersedes_event_id'] = mapping[r['supersedes_event_id']]
        r['event_id'] = e.digest({k:v for k,v in r.items() if k!='event_id'})
        legacy.append(r)
        seen[row['event_id']] = row; previous = e.digest(row); mapping[row['event_id']] = r['event_id']
    if trusted_tip is not None and previous != trusted_tip: raise ValueError('untrusted-ledger-tip')
    result = e.replay(legacy)
    return dict(result, tip_sha256=previous, schema_version=VERSION+'.replay')


def append_event(path, row, trusted_tip=None):
    import json
    import os
    old = [json.loads(x) for x in path.read_bytes().splitlines()] if path.exists() else []
    if trusted_tip is not None: replay(old, trusted_tip)
    result = replay([*old, row])
    if not any(r['event_id'] == row['event_id'] for r in old):
        with path.open('ab') as stream:
            stream.write(e.canonical(row)+b'\n'); stream.flush(); os.fsync(stream.fileno())
    return result
