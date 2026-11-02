"""Thin portable Docker shim for the explicitly authorized token-count stage."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

REPO=Path(__file__).resolve().parents[1]

def call(*args): return subprocess.check_output(list(args),cwd=REPO).decode().strip()
def digest(x): return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def identity():
    if call('git','status','--porcelain'): raise ValueError('embedded-api-source-not-clean')
    return call('git','rev-parse','HEAD'),call('git','rev-parse','HEAD^{tree}')

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['build','smoke','count','model','study'])
    p.add_argument('--receipt',required=True);p.add_argument('--base-image');p.add_argument('--input-root');p.add_argument('--output-root');p.add_argument('--authorization');p.add_argument('--runtime-correction')
    a=p.parse_args();receipt=Path(a.receipt).resolve();commit,tree=identity()
    lock=hashlib.sha256((REPO/'containers/ragas/requirements.lock').read_bytes()).hexdigest()
    if a.command=='build':
        if not a.base_image or not a.base_image.startswith('sha256:'): raise ValueError('immutable-base-required')
        info=json.loads(call('docker','image','inspect',a.base_image))[0]
        if info['Config']['Labels'].get('io.retrieval-arena.ragas.dependency-lock-sha256')!=lock:
            raise ValueError('sdk-dependency-lock-mismatch')
        tag='retrieval-arena/embedded-api:'+commit[:12]
        base='retrieval-arena/embedded-api-base:'+a.base_image[7:]
        subprocess.check_call(['docker','tag',a.base_image,base])
        subprocess.check_call(['docker','build','--network=none','--pull=false','--platform','linux/amd64',
            '-f',str(REPO/'containers/embedded_api/Dockerfile'),'--build-arg','BASE_IMAGE='+base,
            '--build-arg','SOURCE_COMMIT='+commit,'--build-arg','SOURCE_TREE='+tree,
            '--build-arg','DEPENDENCY_LOCK_SHA256='+lock,'-t',tag,str(REPO)])
        image=call('docker','image','inspect','--format','{{.Id}}',tag)
        value={'schema_version':'retrieval_arena.embedded_api_receipt.v1','source_commit':commit,
            'source_tree':tree,'dependency_lock_sha256':lock,'base_image':a.base_image,'image':image,'platform':'linux/amd64'}
        value['receipt_sha256']=digest(value);receipt.parent.mkdir(parents=True,exist_ok=True)
        receipt.write_text(json.dumps(value,sort_keys=True)+'\n',encoding='utf-8');print(receipt);return
    value=json.loads(receipt.read_bytes());signature=value.pop('receipt_sha256')
    if digest(value)!=signature or (value['source_commit'],value['source_tree'],value['dependency_lock_sha256'])!=(commit,tree,lock):
        raise ValueError('embedded-api-receipt-identity')
    info=json.loads(call('docker','image','inspect',value['image']))[0];labels=info['Config']['Labels']
    if info['Id']!=value['image'] or labels.get('org.opencontainers.image.revision')!=commit or labels.get('io.retrieval-arena.source.tree')!=tree or labels.get('io.retrieval-arena.embedded-api.lock')!=lock:
        raise ValueError('embedded-api-image-identity')
    args=['docker','run','--rm','--platform','linux/amd64','--read-only','--cpus','2','--memory','2g',
          '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','512','--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
          '--env','RA_SOURCE_COMMIT='+commit,'--env','RA_SOURCE_TREE='+tree,
          '--env','RA_IMAGE_REFERENCE='+value['image'],'--env','RA_DEPENDENCY_LOCK_SHA256='+lock,'--env','RA_PLATFORM=linux/amd64']
    if a.command=='smoke':
        subprocess.check_call([*args,'--network','none',value['image'],'smoke']);return
    if not all([a.input_root,a.output_root,a.authorization]): raise ValueError('count-paths-required')
    auth_path=Path(a.authorization).resolve();authorization=json.loads(auth_path.read_bytes())
    inp=Path(a.input_root).resolve();out=Path(a.output_root).resolve()
    scope={'count':'provider_token_count_only','model':'bounded_embedded_refinement_v2','study':'bounded_embedded_refinement_v3'}[a.command]
    if authorization.get('approved') is not True or authorization.get('scope')!=scope:
        raise ValueError('explicit-'+scope+'-authorization-required')
    req=json.loads((inp/'operator-request.json').read_bytes())
    if a.command=='count' and authorization.get('input_inventory_sha256')!=req['input_inventory_sha256']: raise ValueError('authorization-input-binding')
    if inp==out or inp in out.parents or out in inp.parents: raise ValueError('count-root-overlap')
    credential='OPENAI_API_KEY'
    if a.command=='study':
        study=json.loads((inp/'study-manifest.json').read_bytes())
        if authorization.get('study_manifest_sha256')!=digest(study) or authorization.get('input_inventory_sha256')!=req['input_inventory_sha256']:
            raise ValueError('combined-authorization-binding')
        expected={k:v for k,v in study['runtime_receipts']['sdk'].items() if k!='receipt_sha256'}
        if expected!=value:
            if not a.runtime_correction:raise ValueError('combined-approved-runtime-binding')
            correction=json.loads(Path(a.runtime_correction).read_bytes())
            if (correction.get('scope')!='semantics_preserving_runtime_correction'
                or correction.get('study_manifest_sha256')!=digest(study)
                or correction.get('authorization_sha256')!=digest(authorization)
                or correction.get('original_runtime')!=expected or correction.get('corrected_runtime')!=value
                or correction.get('input_inventory_sha256')!=req['input_inventory_sha256']
                or any(correction.get(k) is not True for k in
                    ['host_schemas_unchanged','prompts_unchanged','source_access_unchanged','caps_unchanged','preserve_clock_and_used_capacity'])):
                raise ValueError('runtime-correction-binding')
    if a.command=='model':
        cfg=json.loads((inp/'config.json').read_bytes());study=json.loads((inp/'study-manifest.json').read_bytes())
        if authorization.get('study_manifest_sha256')!=digest(study):raise ValueError('authorization-manifest-binding')
        credential={'openai':'OPENAI_API_KEY','anthropic':'ANTHROPIC_API_KEY'}[cfg['settings']['provider']]
    if a.command in ('model','study'):args+=['--entrypoint','python']
    tail=(['count','/input','/output','/authorization.json'] if a.command=='count' else
          ['-B','-m','retrieval_arena.'+('embedded_study' if a.command=='study' else 'embedded_model_run'),'/input','/output','/authorization.json'])
    credentials=['--env','OPENAI_API_KEY','--env','ANTHROPIC_API_KEY'] if a.command=='study' else ['--env',credential]
    subprocess.check_call([*args,'--network','bridge',*credentials,
        '--mount',f'type=bind,src={inp},dst=/input,readonly', '--mount',f'type=bind,src={out},dst=/output',
        '--mount',f'type=bind,src={auth_path},dst=/authorization.json,readonly',value['image'],
        *tail])

if __name__=='__main__':main()
