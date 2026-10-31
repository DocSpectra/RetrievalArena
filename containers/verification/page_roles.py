"""Offline role discovery/sample/review/evaluation operator; never calls a model."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
sys.path.insert(0, '/workspace/source')
from retrieval_arena import page_role_annotation as pa
from retrieval_arena import page_role_evaluation as pe
from retrieval_arena import page_role_reference as pref
from retrieval_arena.candidate_retrieval import regular_inventory, sha256_file
from retrieval_arena.graph import page_representations as pr


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_bytes(pa.canonical(value)+b'\n')


def run(inp, out):
    started = time.monotonic()
    pa.require(not any(p.name != '.retrieval-arena-output-owner' for p in out.iterdir()), 'roles-output-not-fresh')
    inventory = [v for v in regular_inventory(inp) if v['path'] != 'operator-request.json']
    req = read(inp/'operator-request.json')
    pa.require(req == {'schema_version': 'retrieval_arena.page_roles_request.v1', 'command': 'page-roles',
        'input_inventory': inventory, 'input_inventory_sha256': pa.digest(inventory)}, 'roles-input-inventory')
    cfg = read(inp/'config.json'); stage = cfg['stage']
    pa.require(cfg['schema_version'] == 'retrieval_arena.page_roles_config.v1' and stage in ('prepare', 'sample', 'import', 'evaluate', 'evaluate-model'), 'roles-stage')
    pa.require(0 < cfg['max_seconds'] <= 14400 and 0 < cfg['max_output_bytes'] <= 6*1024**3, 'roles-budget')
    pages = read(inp/'pages.json'); ids = [p['doc_id'] for p in pages]
    pa.require(ids == sorted(set(ids)) and all(Path(v).name == v and '/' not in v and '\\' not in v for v in ids), 'roles-page-order')
    texts = {v: (inp/'corpus'/f'{v}.md').read_bytes().decode('utf-8') for v in ids}
    for p in pages:
        pa.require(pa.raw_hash(texts[p['doc_id']]) == p['raw_sha256'], 'roles-content-hash')
    rubric = read(inp/'rubric.json'); rubric_hash = pa.digest(rubric)
    pa.require(len(set(rubric['roles'])) == len(rubric['roles']) and rubric['unknown_role'] in rubric['roles'], 'roles-rubric')
    discovery = read(inp/'discovery.json')
    common = {'config.json', 'pages.json', 'rubric.json', 'discovery.json', *('corpus/'+v+'.md' for v in ids)}
    allowed = set(common)
    if stage in ('prepare', 'sample'):
        groups = pa.leakage_groups(pages, texts, **cfg['grouping'])
        write(out/'leakage-groups.json', groups)
        excluded_groups = {groups['groups'][v] for v in discovery}
        excluded_ids = [v for v in ids if groups['groups'][v] in excluded_groups]
        summary = {'population': len(ids), 'discovery_pages': len(discovery), 'groups': len(set(groups['groups'].values())),
            'discovery_excluded_pages': len(excluded_ids), 'remaining_population': len(ids)-len(excluded_ids),
            'rubric_sha256': rubric_hash, 'stage': stage, 'calls': 0}
        if stage == 'prepare':
            packet, key, form = pa.export_blind(discovery, texts, groups['groups'], rubric_hash, cfg['packet_salt'])
            write(out/'discovery-packet.json', packet); write(out/'discovery-private-key.json', key)
            write(out/'discovery-human-form.json', form)
            # Each page is fenced as text: corpus instructions are never executed.
            chunks = ['# Discovery review: unrendered source prose\n\nNo evaluation pages have been sampled. Start a timer for each reviewed page.\n']
            for card in packet['cards']:
                fence = '`'*(max([len(x) for x in __import__('re').findall(r'`+', card['text'])]+[2])+1)
                chunks.append('\n## '+card['card_id']+'\n\n'+fence+'text\n'+card['text']+'\n'+fence+'\n')
            (out/'discovery-review.md').write_text('\n'.join(chunks), encoding='utf-8', newline='\n')
            write(out/'evaluation-form-template.json', {'status': 'not sampled; workload/rubric approval pending',
                'proposed_count': cfg['proposed_count'], 'seed': cfg['sample_seed'], 'rows': []})
        else:
            allowed.add('sampling-approval.json'); approval = read(inp/'sampling-approval.json')
            pa.require(approval['decision'] == 'yes' and approval['author'] and approval['record'] and
                approval['rubric_sha256'] == rubric_hash and approval['count'] == cfg['count'] and
                approval['population_sha256'] == pa.digest(pages) and approval['group_policy_sha256'] == pa.digest(cfg['grouping']), 'sampling-approval')
            sample = pa.sample_pages(pages, groups['groups'], discovery, cfg['count'], cfg['sample_seed'])
            write(out/'sample.json', sample)
            packet, key, form = pa.export_blind([r['doc_id'] for r in sample['rows']], texts, groups['groups'], rubric_hash, cfg['packet_salt'])
            write(out/'blind-packet.json', packet); write(out/'private-key.json', key); write(out/'initial-human-form.json', form)
            summary['sampled'] = len(sample['rows'])
    elif stage == 'import':
        allowed |= {'blind-packet.json', 'private-key.json', 'initial-human-form.json'}
        imported = pa.import_human(read(inp/'initial-human-form.json'), read(inp/'blind-packet.json'),
            read(inp/'private-key.json'), rubric['roles'], texts)
        write(out/'initial-human-labels.json', imported)
        summary = {'stage': stage, 'human_rows': len(imported['rows']), 'resolved': imported['resolved'], 'calls': 0}
    else:
        reference_meta = None
        if stage == 'evaluate-model':
            allowed |= {'groups.json', 'evaluation.json', 'simple.json'}
            allowed |= {'model-reference/'+p.name for p in (inp/'model-reference').iterdir() if p.is_file()}
            groups = read(inp/'groups.json')
            labels, reference_meta = pref.load_model_reference(inp/'model-reference', texts, rubric, groups, discovery)
        else:
            allowed |= {'reference.json', 'groups.json', 'evaluation.json', 'simple.json', 'reference-approval.json',
                        'sample.json', 'blind-packet.json', 'private-key.json', 'initial-human-form.json', 'initial-human-labels.json'}
            reference = read(inp/'reference.json'); approval = read(inp/'reference-approval.json')
            pa.require(approval['decision'] == 'yes' and approval['author'] and approval['record'] and
                       approval['reference_sha256'] == pa.digest(reference) and approval['rubric_sha256'] == rubric_hash,
                       'human-reference-not-approved')
            pa.require(reference['kind'] in ('initial_human', 'adjudicated_human') and reference['rubric_sha256'] == rubric_hash
                       and reference['provenance_record'], 'reference-kind')
            initial = pa.import_human(read(inp/'initial-human-form.json'), read(inp/'blind-packet.json'),
                read(inp/'private-key.json'), rubric['roles'], texts)
            pa.require(initial == read(inp/'initial-human-labels.json') and
                       reference['initial_human_sha256'] == pa.digest(initial), 'reference-initial-stream')
            sample = read(inp/'sample.json')
            initial_by_id = {r['doc_id']: r for r in initial['rows']}
            pa.require(set(initial_by_id) == {r['doc_id'] for r in sample['rows']}, 'sample-reference-join')
            labels = {}
            for row in reference['rows']:
                v = row['doc_id']; pa.require(v in texts and v not in labels and row['raw_sha256'] == pa.raw_hash(texts[v]), 'reference-join')
                pa.require(row['role'] is None or row['role'] in rubric['roles'], 'reference-role')
                pa.require(v in initial_by_id, 'reference-unsampled-page')
                if reference['kind'] == 'initial_human':
                    pa.require(row['role'] == initial_by_id[v]['role'], 'reference-changed-initial-label')
                elif row['role'] != initial_by_id[v]['role']:
                    pa.require(isinstance(row.get('adjudication_rationale'), str) and row['adjudication_rationale'].strip(), 'adjudication-rationale')
                labels[v] = row['role']
            pa.require(set(labels) == set(initial_by_id), 'reference-coverage-join')
            groups = read(inp/'groups.json')
            pa.require(all(groups[r['doc_id']] == r['group'] for r in initial['rows']) and
                       all(groups[r['doc_id']] == r['group'] for r in sample['rows']), 'reference-group-join')
        pa.require(not {groups[v] for v in labels} & {groups[v] for v in discovery}, 'discovery-evaluation-leakage')
        simple_rows = read(inp/'simple.json')['rows']; simple = {}
        fields = cfg['simple_fields']
        for row in simple_rows:
            pa.require(row['doc_id'] not in simple, 'simple-duplicate-id')
            simple[row['doc_id']] = [float(row[f]) if row[f] is not None else float('nan') for f in fields]
        kernels = {}
        for name, spec in cfg['kernels'].items():
            relative = Path(spec['path'])
            pa.require(not relative.is_absolute() and '..' not in relative.parts, 'kernel-path')
            directory = inp/relative
            allowed |= {relative.as_posix()+'/'+p.name for p in directory.iterdir() if p.is_file()}
            # Expected identity recomputed from the admitted exact corpus pages.
            content = pa.digest([(v, pa.raw_hash(texts[v])) for v in ids])
            manifest, kernel, rows = pr.load_bundle(directory, content_identity=content,
                graph_sha256=spec['graph_sha256'], q=spec['q'], t=spec['t'])
            pa.require(manifest['nodes'] == ids, 'kernel-node-join')
            kernels[name] = {v: rows[i] for i, v in enumerate(ids)}
        result = pe.evaluate(labels, groups, texts, simple, kernels, read(inp/'evaluation.json'),
            freeze=lambda plan: write(out/'frozen-evaluation-plan.json', plan))
        if reference_meta is not None:
            result['reference'] = reference_meta
        write(out/'evaluation-results.json', result); summary = {'stage': stage, 'status': result['status'], 'calls': 0}
    pa.require({r['path'] for r in inventory} == allowed, 'roles-undeclared-input')
    pa.require(time.monotonic()-started <= cfg['max_seconds'], 'roles-runtime-budget')
    pa.require(sum(p.stat().st_size for p in out.iterdir() if p.is_file()) <= cfg['max_output_bytes'], 'roles-output-budget')
    summary['runtime_seconds'] = time.monotonic()-started
    write(out/'summary.json', summary)
    manifest = {'schema_version': 'retrieval_arena.page_roles_operator_manifest.v1', 'input_inventory_sha256': pa.digest(inventory),
        'code_sha256': sha256_file(Path(pa.__file__)), 'evaluation_code_sha256': sha256_file(Path(pe.__file__)),
        'operator_sha256': sha256_file(Path(__file__)), 'reference_code_sha256': sha256_file(Path(pref.__file__)), 'outputs': regular_inventory(out),
        'runtime': {k: os.environ.get(k) for k in ('RA_SOURCE_COMMIT', 'RA_SOURCE_TREE', 'RA_IMAGE_REFERENCE', 'RA_DEPENDENCY_LOCK_SHA256', 'RA_PLATFORM')}}
    write(out/'page_roles_manifest.json', manifest)
    write(out/'operator_receipt.v1.json', {'status': 'complete', 'manifest_sha256': pa.digest(manifest)})
    return summary


def main():
    run(Path('/workspace/input'), Path('/workspace/output'))
    return 0
