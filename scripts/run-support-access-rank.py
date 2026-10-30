"""Portable thin Docker shim for immutable offline complete-score generation."""
import argparse
import json
from pathlib import Path
import subprocess
import time

def capture(*args): return subprocess.check_output(args,text=True).strip()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model',type=Path,required=True); p.add_argument('--provenance',type=Path,required=True)
    p.add_argument('--base-image',required=True); p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args(); repo=Path(__file__).resolve().parents[1]
    if capture('git','-C',str(repo),'status','--porcelain'): raise ValueError('ranking-source-not-clean')
    if not a.base_image.startswith('sha256:') or len(a.base_image)!=71: raise ValueError('ranking-base-mutable')
    if capture('docker','image','inspect','--format','{{.Id}}',a.base_image)!=a.base_image: raise ValueError('ranking-base-unavailable')
    commit=capture('git','-C',str(repo),'rev-parse','HEAD'); tree=capture('git','-C',str(repo),'rev-parse','HEAD^{tree}')
    tag='retrieval-arena/support-access-rank:'+commit[:12]
    # A local, ID-verified tag prevents a digest-shaped FROM from pulling.
    base_tag='retrieval-arena/support-access-rank-base:'+a.base_image[7:19]
    subprocess.run(['docker','tag',a.base_image,base_tag],check=True)
    subprocess.run(['docker','build','--network=none','--pull=false','--file',str(repo/'containers/verification/Dockerfile.support-access-rank'),
        '--build-arg','BASE_IMAGE='+base_tag,'--build-arg','SOURCE_COMMIT='+commit,'--build-arg','SOURCE_TREE='+tree,'--tag',tag,str(repo)],check=True)
    image=capture('docker','image','inspect','--format','{{.Id}}',tag)
    if a.output.exists(): raise ValueError('ranking-output-exists')
    a.output.mkdir(parents=True)
    args=['docker','run','--rm','--network','none','--cpus','4','--memory','8g','--read-only','--security-opt','no-new-privileges','--cap-drop','ALL',
        '--pids-limit','512','--tmpfs','/tmp:rw,nosuid,nodev,size=512m']
    for host,dest,ro in [(a.input,'/input',True),(a.output,'/output',False),(a.model,'/model',True),(a.provenance,'/provenance',True)]:
        args+=['--mount','type=bind,src='+str(host.resolve())+',dst='+dest+(',readonly' if ro else '')]
    args+=[image]
    receipt={'source_commit':commit,'source_tree':tree,'image':image,'base_image':a.base_image,'command':args,
        'network':'none','cpus':4,'memory_gib':8,'platform_validated':'linux/amd64 only'}
    a.receipt.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf8')
    started=time.monotonic()
    completed=subprocess.run(args,timeout=7200)
    receipt.update(returncode=completed.returncode,runtime_seconds=time.monotonic()-started)
    a.receipt.write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n',encoding='utf8')
    completed.check_returncode()

if __name__=='__main__': main()
