"""Verify model-assigned role references without impersonating human labels."""
import json
from pathlib import Path
from . import page_role_annotation as pa


def load_model_reference(directory, texts, rubric, groups, discovery):
    """Revalidate the frozen first-valid batch and retain unresolved targets.

    No human label or approval is created. The caller declares the exploratory
    use in its study plan; annotation execution still needs its exact approval.
    """
    root = Path(directory)
    def read(name):
        return json.loads((root/name).read_bytes())
    manifest = read('manifest.json'); approval = read('approval.json')
    result = read('result.json'); prompt = (root/'prompt.txt').read_bytes().decode('utf-8')
    pa.require(approval.get('decision') == 'yes' and approval.get('author') and approval.get('record')
               and approval.get('manifest_sha256') == pa.digest(manifest), 'model-reference-authorization')
    facets = rubric.get('facets'); policy = manifest.get('evidence_policy', pa.STRICT_EVIDENCE)
    pa.require(manifest['rubric_sha256'] == pa.digest(rubric) and
               manifest['prompt_sha256'] == pa.raw_hash(prompt) and
               manifest['schema_sha256'] == pa.digest(pa.annotation_schema(rubric['roles'], facets)),
               'model-reference-definition')
    ids = set(manifest['page_hashes'])
    pa.require(ids and ids <= set(texts) and ids <= set(groups), 'model-reference-population')
    pa.require(not {groups[v] for v in ids} & {groups[v] for v in discovery}, 'discovery-evaluation-leakage')
    for v in ids:
        pa.require(pa.raw_hash(texts[v]) == manifest['page_hashes'][v], 'model-reference-content')
    events = [json.loads(line) for line in (root/'attempts.jsonl').read_bytes().splitlines()]
    starts = {}; completed = {}; valid = {}; attempts = {}
    for event in events:
        v = event['doc_id']; aid = event['attempt_id']
        pa.require(v in ids and isinstance(aid, str) and Path(aid).name == aid
                   and '/' not in aid and '\\' not in aid, 'model-reference-event')
        if event['event'] == 'start':
            pa.require(aid not in starts and v not in valid, 'model-reference-preferred-retry')
            starts[aid] = v; attempts[v] = attempts.get(v, 0) + 1
        else:
            pa.require(event['event'] in ('valid', 'invalid') and starts.get(aid) == v
                       and aid not in completed, 'model-reference-journal')
            completed[aid] = event
            if event['event'] == 'valid':
                pa.require(v not in valid, 'model-reference-duplicate')
                reply = read(aid+'.json')
                pa.require(reply['model'] == manifest['settings']['model'] and
                           reply['revision'] == manifest['settings']['revision'], 'model-reference-identity')
                annotation = pa.validate_annotation(json.loads(reply['text']), texts[v], rubric['roles'],
                    rubric['unknown_role'], evidence_policy=policy, facets=facets)
                pa.require(annotation == event['annotation'] and event['cache_identity'] ==
                    pa.cache_identity(texts[v], rubric, prompt, manifest['settings'], rubric['roles'],
                                      evidence_policy=policy, facets=facets), 'model-reference-annotation')
                valid[v] = event
    pa.require(set(starts) == set(completed), 'model-reference-interrupted-attempt')
    pa.require(len(starts) <= manifest['max_attempts'] and len(ids) <= manifest['max_pages'] and
               all(n <= 1+manifest['retries'] for n in attempts.values()), 'model-reference-budget')
    pa.require(result['manifest_sha256'] == pa.digest(manifest) and result['attempts'] == len(starts)
               and result['annotations'] == list(valid.values()) and
               result['unresolved'] == sorted(ids-set(valid)), 'model-reference-result')
    labels = {v: valid[v]['annotation']['role'] if v in valid else None for v in sorted(ids)}
    return labels, {'kind': 'model_assigned', 'manifest_sha256': pa.digest(manifest),
        'batch_result_sha256': pa.digest(result), 'rubric_sha256': pa.digest(rubric),
        'model': manifest['settings']['model'], 'revision': manifest['settings']['revision'],
        'pages': len(ids), 'resolved': len(valid), 'unresolved': result['unresolved'],
        'interpretation': 'Agreement with model-assigned purposes; not human-reference accuracy or annotation reliability.'}
