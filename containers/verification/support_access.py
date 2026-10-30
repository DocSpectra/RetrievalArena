"""Offline immutable operator for cached support-access comparisons."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, '/workspace/source')
from retrieval_arena.candidate_retrieval import canonical, regular_inventory, sha256_bytes, sha256_file
from retrieval_arena.graph import magnetic_spectrum as ms
from retrieval_arena.graph import support_access as sa

INPUT = Path('/workspace/input')
OUTPUT = Path('/workspace/output')
COMMAND = 'compare-support-access'


def _rows(path):
    with path.open(encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def _unique(rows, key):
    result = {}
    for row in rows:
        k = key(row)
        if k in result: raise ValueError('support-access-duplicate-row:' + str(k))
        result[k] = row
    return result


def _write(path, value):
    path.write_bytes(canonical(value) + b'\n')


def _jsonl(path, values):
    with path.open('wb') as f:
        for value in values: f.write(canonical(value) + b'\n')


def _safe(root, rel):
    if not isinstance(rel, str): raise ValueError('support-access-path-type')
    path = (root / rel).resolve()
    if root.resolve() not in path.parents: raise ValueError('support-access-path-escape')
    return path


def _excerpt(path, question):
    lines = path.read_text(encoding='utf-8').splitlines()
    terms = set(re.findall(r'[a-z0-9]+', question.lower())) - {'a','an','the','is','do','i','how','what','to','of','and','can','my','it','in'}
    if not lines: return {'line': 1, 'text': ''}
    line = min(range(len(lines)), key=lambda i: (-len(terms & set(re.findall(r'[a-z0-9]+', lines[i].lower()))), i))
    start = max(0, line - 1)
    return {'line': start + 1, 'text': '\n'.join(lines[start:line+5])[:850],
            'selection': 'first maximum query-term-overlap line plus local context; illustrative, not sufficiency evidence'}


def run(input_root, output_root):
    started = time.monotonic()
    request = json.loads((input_root / 'operator-request.json').read_bytes())
    if (set(request) != {'schema_version','command','config_path','input_inventory','input_inventory_sha256'} or
        request['schema_version'] != 'retrieval_arena.support_access_operator_request.v1' or
        request['command'] != COMMAND or request['config_path'] != 'config.json'):
        raise ValueError('support-access-request')
    inventory = [r for r in regular_inventory(input_root) if r['path'] != 'operator-request.json']
    inv_hash = sha256_bytes(canonical(inventory))
    if request['input_inventory'] != inventory or request['input_inventory_sha256'] != inv_hash:
        raise ValueError('support-access-input-inventory')
    config = json.loads((input_root / 'config.json').read_bytes())
    expected = {'schema_version','queries_path','trials_path','targets_path','reference_scores_path','corpus_path',
                'jobs','primary_job','traversal','semantic_depths','bootstrap_draws','bootstrap_seed',
                'max_output_bytes','max_runtime_seconds'}
    if set(config) != expected or config['schema_version'] != 'retrieval_arena.support_access_config.v1':
        raise ValueError('support-access-config')
    if not isinstance(config['max_output_bytes'], int) or not 1 <= config['max_output_bytes'] <= 4*1024**3:
        raise ValueError('support-access-output-budget')
    if not isinstance(config['max_runtime_seconds'], int) or not 1 <= config['max_runtime_seconds'] <= 7200:
        raise ValueError('support-access-runtime-budget')
    def budget():
        if time.monotonic() - started > config['max_runtime_seconds']:
            raise ValueError('support-access-runtime-exceeded')
        if sum(p.stat().st_size for p in output_root.iterdir() if p.is_file()) > config['max_output_bytes']:
            raise ValueError('support-access-output-budget-exceeded')
    corpus = _safe(input_root, config['corpus_path'])
    required = {'config.json'} | {config[k] for k in ('queries_path','trials_path','targets_path','reference_scores_path')}
    required |= {j['bundle'] for j in config['jobs']}
    required |= {p.relative_to(input_root).as_posix() for p in corpus.rglob('*') if p.is_file()}
    if set(r['path'] for r in inventory) != required:
        raise ValueError('support-access-undeclared-inputs')
    queries = _unique(_rows(_safe(input_root, config['queries_path'])), lambda r: r['query_id'])
    for q in queries.values():
        if set(q) != {'query_id','question','scores'} or not isinstance(q['question'],str) or not isinstance(q['scores'],dict):
            raise ValueError('support-access-query-schema')
    trials = _unique(_rows(_safe(input_root, config['trials_path'])), lambda r: (r['job_id'],r['query_id']))
    jobs = _unique(config['jobs'], lambda j: j['job_id'])
    if config['primary_job'] not in jobs: raise ValueError('support-access-primary-job')
    keys = {(j,q) for j in jobs for q in queries}
    if set(trials) != keys: raise ValueError('support-access-trial-join')
    graphs, predictions = {}, []
    for job_id, job in jobs.items():
        if set(job) != {'job_id','family','kind','bundle','node_ids'} or job['kind'] not in {'full_scope','fixed_lwcc'}:
            raise ValueError('support-access-job-schema')
        bundle = json.loads(_safe(input_root, job['bundle']).read_bytes())
        nodes = job['node_ids']
        if set(nodes) - set(bundle['nodes']): raise ValueError('support-access-view-outside-bundle')
        if job['kind'] == 'full_scope' and set(nodes) != set(bundle['nodes']):
            raise ValueError('support-access-full-scope-contract')
        edges = [(s,t) for s,t in bundle['edges'] if s in set(nodes) and t in set(nodes)]
        graphs[job_id] = (nodes,edges)
        for qid, query in queries.items():
            trial = trials[job_id,qid]
            if set(trial) != {'job_id','query_id','question','semantic'} or trial['question'] != query['question']:
                raise ValueError('support-access-trial-query-identity')
            pred = sa.freeze_prediction(nodes,edges,query['scores'].items(),trial['semantic'],config['traversal'])
            predictions.append({'job_id':job_id,'query_id':qid,'query_text_group':query['question'],**pred})
            budget()
    # This durable artifact is completely selected before the evaluator opens targets.
    frozen = output_root / 'frozen_predictions.jsonl'
    _jsonl(frozen, predictions)
    prediction_hash = sha256_file(frozen)
    target_rows = _unique(_rows(_safe(input_root, config['targets_path'])), lambda r:r['query_id'])
    if set(target_rows) != set(queries): raise ValueError('support-access-target-query-join')
    targets = {}
    universe = set().union(*(set(n) for n,e in graphs.values()))
    for qid, row in target_rows.items():
        if set(row) != {'query_id','known_target_ids'} or not isinstance(row['known_target_ids'],list):
            raise ValueError('support-access-target-schema')
        ts = row['known_target_ids']
        if not ts or len(ts) != len(set(ts)) or any(not isinstance(t,str) for t in ts) or set(ts) - universe:
            raise ValueError('support-access-target-outside-universe-or-duplicate')
        targets[qid] = ts
    references = _unique(_rows(_safe(input_root, config['reference_scores_path'])), lambda r:(r['job_id'],r['query_id']))
    if set(references) != keys: raise ValueError('support-access-reference-join')
    evaluated = []
    for prediction in predictions:
        job_id, qid = prediction['job_id'], prediction['query_id']
        nodes,edges = graphs[job_id]
        row = sa.evaluate_prediction(nodes,edges,prediction,targets[qid],config['traversal'],config['semantic_depths'])
        if jobs[job_id]['kind'] == 'full_scope' and not row['eligible']:
            raise ValueError('support-access-full-scope-missing-target')
        reference = references[job_id,qid]
        expected_scores = {'C_semantic':row['hits']['G4'], 'semantic_seed_hit':row['hits']['B1'],
                           'semantic_candidate_hit':row['candidate_hit'],
                           'semantic_expansion_added_access':row['historical_expansion_added_hit'],
                           'semantic_selected_count':row['selected_counts']['G4'],
                           'semantic_candidate_count':row['candidate_count'], 'semantic_dead_end':row['dead_end']}
        if any(reference['scores'][k] != v for k,v in expected_scores.items()) or reference['known_target_count_in_view'] != row['known_target_count_in_view']:
            raise ValueError('support-access-historical-score-mismatch')
        evaluated.append({'job_id':job_id,'query_id':qid,'query_text_group':prediction['query_text_group'],**row})
        budget()
    if sha256_file(frozen) != prediction_hash: raise ValueError('support-access-predictions-changed')
    _jsonl(output_root / 'per_query_comparison.jsonl', evaluated)
    intervals = sa.paired_intervals(evaluated,job_ids=list(jobs),draws=config['bootstrap_draws'],seed=config['bootstrap_seed'])
    summary = {'schema_version':'retrieval_arena.support_access_summary.v1', 'comparison_version':sa.VERSION,
               'primary_job':config['primary_job'], 'prediction_sha256':prediction_hash,
               'prediction_rows':len(predictions), 'historical_replay_rows_verified':len(evaluated), 'views':{}}
    for job_id in jobs:
        subset = [r for r in evaluated if r['job_id'] == job_id]
        summary['views'][job_id] = {**sa.aggregate(subset), 'intervals':intervals[job_id],
            'view_node_count':len(graphs[job_id][0]),'view_graph_sha256':ms.graph_sha256(*graphs[job_id]),
            'excluded_no_known_target_ids':[r['query_id'] for r in subset if not r['eligible']]}
    _write(output_root / 'support_access_summary.json',summary)
    cards = []
    pred_index = {(p['job_id'],p['query_id']):p for p in predictions}
    for cell in sa.CELLS:
        choices = sorted((r for r in evaluated if r['job_id'] == config['primary_job'] and r['cell'] == cell),key=lambda r:r['query_id'])[:2]
        for row in choices:
            pred = pred_index[row['job_id'],row['query_id']]
            docs = sorted(set(row['known_target_ids_in_view']) | set(pred['B4']) | set(pred['G4']['selected_ids']))
            excerpts = {doc:_excerpt(_safe(corpus,doc+'.md'),row['query_text_group']) for doc in docs}
            cards.append({'query_id':row['query_id'],'question':row['query_text_group'],'cell':cell,
                'query_provenance':'Caller must join original query provenance by unchanged query ID; this operator invents no sufficiency labels.',
                'best_semantic_ids':pred['semantic_ranking'][:16], 'B1':pred['B1'],'B4':pred['B4'],
                'G4':pred['G4'],'evaluation':row,'page_excerpts':excerpts,
                'sufficiency_or_composition_assessment':'unknown; excerpts and pooled positives do not establish complete-answer sufficiency or necessary composition'})
    _write(output_root / 'example_cards.json', {'schema_version':'retrieval_arena.support_access_example_cards.v1','cards':cards})
    budget()
    outputs = regular_inventory(output_root)
    outputs = [r for r in outputs if r['path'] != '.retrieval-arena-output-owner']
    manifest = {'schema_version':'retrieval_arena.support_access_operator_manifest.v1','command':COMMAND,
                'outputs':outputs,'input_inventory_sha256':inv_hash,'runtime_seconds':time.monotonic()-started,
                'counts':{'prediction_rows':len(predictions),'evaluated_rows':len(evaluated),'views':len(jobs),'example_cards':len(cards)},
                'code_sha256':{'comparison':sha256_file(Path(sa.__file__)),'operator':sha256_file(Path(__file__))},
                'runtime':{'python':sys.version.split()[0],'numpy':__import__('numpy').__version__,
                    **{k:os.environ.get(v) for k,v in {'source_commit':'RA_SOURCE_COMMIT','source_tree':'RA_SOURCE_TREE',
                       'dependency_lock_sha256':'RA_DEPENDENCY_LOCK_SHA256','image_reference':'RA_IMAGE_REFERENCE','container_platform':'RA_PLATFORM'}.items()}}}
    _write(output_root / 'support_access_manifest.json',manifest)
    receipt = {'schema_version':'retrieval_arena.support_access_operator_receipt.v1','command':COMMAND,'status':'qualified',
               'manifest_sha256':sha256_file(output_root/'support_access_manifest.json'),'input_inventory_sha256':inv_hash,
               'counts':manifest['counts'],'receipt_sha256':'0'*64}
    receipt['receipt_sha256'] = sha256_bytes(canonical(receipt))
    _write(output_root / 'operator_receipt.v1.json',receipt)
    budget()
    return summary


def main():
    if not OUTPUT.is_dir() or {p.name for p in OUTPUT.iterdir()} != {'.retrieval-arena-output-owner'}:
        raise ValueError('verification-operator-output-not-fresh')
    run(INPUT,OUTPUT)
    return 0
