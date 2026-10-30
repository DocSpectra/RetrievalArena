"""Thin local-model container launcher. Explicit matching approval is mandatory."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from retrieval_arena.page_role_annotation import digest, require


def launch(inp, out, receipt_path):
    inp, out = Path(inp).resolve(), Path(out).resolve()
    manifest = json.loads((inp/'manifest.json').read_bytes())
    approval = json.loads((inp/'approval.json').read_bytes())
    require(approval.get('decision') == 'yes' and approval.get('author') and approval.get('record')
            and approval.get('manifest_sha256') == digest(manifest), 'annotation-not-authorized')
    require(inp != out and not inp.is_relative_to(out) and not out.is_relative_to(inp), 'annotation-root-overlap')
    raw = Path(receipt_path).read_bytes(); receipt = json.loads(raw)
    zeroed = re.sub(rb'"receipt_sha256"\s*:\s*"[0-9a-f]{64}"', b'"receipt_sha256":"'+b'0'*64+b'"', raw)
    require(receipt['schema_version'] == 'retrieval_arena.verification_image_receipt.v2' and
            hashlib.sha256(zeroed).hexdigest() == receipt['receipt_sha256'], 'annotation-image-receipt')
    def git(*args): return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()
    require(not git('status', '--porcelain'), 'annotation-source-not-clean')
    require(git('rev-parse', 'HEAD') == receipt['source_commit'] and git('rev-parse', 'HEAD^{tree}') == receipt['source_tree'], 'annotation-source-identity')
    require(hashlib.sha256((ROOT/'containers/verification/requirements.lock').read_bytes()).hexdigest() == receipt['dependency_lock_sha256'], 'annotation-lock-identity')
    image = receipt['image']['reference']
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', image) is not None, 'annotation-mutable-image')
    require(manifest['execution'] == {'source_commit': receipt['source_commit'], 'image_reference': image}, 'annotation-manifest-execution')
    require(receipt['platform'] == 'linux/amd64', 'annotation-platform-unregistered')
    require(manifest['settings']['provider'] == 'ollama' and manifest['resources']['client_cpus'] <= 4 and
            manifest['resources']['client_ram_gib'] <= 8 and manifest['max_seconds'] <= 14400, 'annotation-runtime-policy')
    info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    labels = info['Config']['Labels']
    require(labels['org.opencontainers.image.revision'] == receipt['source_commit'] and
            labels['io.retrieval-arena.source.tree'] == receipt['source_tree'] and
            labels['io.retrieval-arena.verification.dependency-lock-sha256'] == receipt['dependency_lock_sha256'], 'annotation-image-labels')
    out.mkdir(parents=True, exist_ok=True)
    name = 'ra-role-'+uuid.uuid4().hex
    command = ['docker', 'run', '--rm', '--name', name, '--platform', receipt['platform'], '--read-only',
        '--user', '65532:65532', '--security-opt', 'no-new-privileges', '--cap-drop', 'ALL', '--pids-limit', '512',
        '--memory', str(manifest['resources']['client_ram_gib'])+'g', '--cpus', str(manifest['resources']['client_cpus']),
        '--add-host', 'host.docker.internal:host-gateway', '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=256m,uid=65532,gid=65532',
        '--mount', f'type=bind,src={ROOT},dst=/workspace/source,readonly',
        '--mount', f'type=bind,src={inp},dst=/workspace/input,readonly',
        '--mount', f'type=bind,src={out},dst=/workspace/output',
        '--env', 'RA_RAGAS_NETWORK=host_model_alias', '--env', 'RA_SOURCE_COMMIT='+receipt['source_commit'],
        '--env', 'RA_IMAGE_REFERENCE='+image, '--env', 'HOME=/tmp', '--env', 'TMPDIR=/tmp',
        '--entrypoint', 'python', image, '-I', '-B', '-c',
        "import sys;sys.path.insert(0,'/workspace/source');from retrieval_arena.page_role_batch import main;raise SystemExit(main())"]
    try:
        return subprocess.run(command, timeout=manifest['max_seconds']+30).returncode
    except subprocess.TimeoutExpired:
        subprocess.run(['docker', 'stop', '--time', '0', name], check=False)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True); parser.add_argument('--output', required=True); parser.add_argument('--receipt', required=True)
    args = parser.parse_args()
    raise SystemExit(launch(args.input, args.output, args.receipt))
