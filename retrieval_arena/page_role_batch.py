"""Authorized annotation entry point inside a source/image-bound container.

The offline verification operator never enables network. This separate entry
point reuses RA's model_providers transport without changing its restrictions.
"""
import asyncio
import json
import os
from pathlib import Path
from . import page_role_annotation as pa
from .model_providers import transport_for, validate_settings


def main():
    inp, out = Path('/workspace/input'), Path('/workspace/output')
    def read(name): return json.loads((inp/name).read_bytes())
    manifest = read('manifest.json'); approval = read('approval.json')
    pa.require(approval.get('decision') == 'yes' and approval.get('author') and approval.get('record')
               and approval.get('manifest_sha256') == pa.digest(manifest), 'annotation-not-authorized')
    pa.require(Path('/.dockerenv').exists(), 'annotation-container-required')
    execution = manifest['execution']
    pa.require(execution['source_commit'] == os.environ.get('RA_SOURCE_COMMIT') and
               execution['image_reference'] == os.environ.get('RA_IMAGE_REFERENCE'), 'annotation-runtime-identity')
    pa.require(manifest['settings']['provider'] == 'ollama' and os.environ.get('RA_RAGAS_NETWORK') == 'host_model_alias', 'annotation-network')
    validate_settings(manifest['settings'])
    rubric = read('rubric.json'); prompt = (inp/'prompt.txt').read_bytes().decode('utf-8')
    texts = {}
    for v in manifest['page_hashes']:
        pa.require(Path(v).name == v and '/' not in v and '\\' not in v, 'annotation-document-id')
        texts[v] = (inp/'corpus'/(v+'.md')).read_bytes().decode('utf-8')
    transport = transport_for(manifest['settings'], 'host_model_alias', out)
    result = asyncio.run(pa.run_batch(texts, rubric, prompt, manifest, approval, out, transport))
    (out/'result.json').write_bytes(pa.canonical(result)+b'\n')
    return 0 if not result['unresolved'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
