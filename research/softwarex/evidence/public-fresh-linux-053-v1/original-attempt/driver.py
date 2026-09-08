from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,signal,subprocess,sys,time
ROOT=Path('/home/reviewer/zerorun-fresh-v1')
OUT=ROOT/'records'
COMMIT='13a3ceae84913a1ddbed412bdad9cd6fbf7e2e54'
MANIFEST='2e9d3960d252d6ee3d27f85662dc3cd6a67cbbfcaa705021d2c059001deb7651'
IMAGE='docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef'
WHEEL='dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9'
def stamp():return datetime.now(timezone.utc).isoformat()
def ident(p):
    raw=p.read_bytes();return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(name,v):
    with (OUT/name).open('x',encoding='utf-8') as f:json.dump(v,f,indent=2,sort_keys=True);f.write('\n')
def invoke(label,argv,timeout=300):
    argv=list(map(str,argv));start=time.monotonic();row={'argv':argv,'started_utc':stamp(),'returncode':None,'timed_out':False}
    print(json.dumps({'starting':label}),flush=True)
    with (OUT/(label+'.stdout.txt')).open('xb') as stdout,(OUT/(label+'.stderr.txt')).open('xb') as stderr:
        p=subprocess.Popen(argv,env=ENV,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
        row['pid']=p.pid
        try:row['returncode']=p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            row['timed_out']=True;os.killpg(p.pid,signal.SIGKILL);row['returncode']=p.wait(timeout=10)
    row.update(finished_utc=stamp(),elapsed_seconds=time.monotonic()-start)
    for kind in ('stdout','stderr'):
        name=label+'.'+kind+'.txt';row[kind]={'path':name,**ident(OUT/name)}
    save(label+'.json',row)
    if row['returncode']!=0:raise RuntimeError('preserved command failed: '+label)
    return (OUT/(label+'.stdout.txt')).read_bytes()
OUT.mkdir(mode=0o700)
save('protocol.json',{'schema':'zerorun.fresh-public-linux-demonstration.protocol.v1','source_commit':COMMIT,'public_manifest_sha256':MANIFEST,'wheel_sha256':WHEEL,'image':IMAGE,'scope':'New official Alpine root filesystem in a dedicated C-backed WSL2 distro; no prior Docker images, config, home or caches. Original account-free synthetic fixture only. Existing Windows hardware and WSL kernel remain shared; no independent human or performance benefit claim.','source_install':'Unchanged published quickstart_053.py; fresh external venv and byte-bound source build.','wheel_install':'Separate new external venv; exact public wheel, no dependencies; same unchanged published MCP checker.','failure_scope':'Original quickstart includes actual missing-authority refusal; an additional failed-fixture supplement, if performed, is separate.','runtime_or_trust_changes':False,'credentials_supplied':False,'model_calls':0,'started_utc':stamp()})
(OUT/'driver.py').write_bytes(Path(__file__).read_bytes())
git_home=ROOT/'git-home';git_home.mkdir(mode=0o700)
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(git_home),'USER':'reviewer','LOGNAME':'reviewer','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'false','SSH_ASKPASS':'false','GCM_INTERACTIVE':'never','PIP_CONFIG_FILE':'/dev/null','PIP_NO_CACHE_DIR':'1'}
git=['git','-c','credential.helper=','-c','core.askPass=','-c','http.extraHeader=']
source=ROOT/'source';receipt={'schema':'zerorun.fresh-public-linux-demonstration.v1','started_utc':stamp(),'passed':False,'failure':None,'source_commit':COMMIT,'image':IMAGE,'independent_human_replication':False,'old_virtualbox_vm_used':False,'runtime_changed':False,'model_called':False}
try:
    invoke('nonroot-identity',['id'])
    before=invoke('empty-image-inventory',['docker','image','ls','--quiet'])
    if before.strip():raise ValueError('new Docker image store is not empty')
    invoke('public-base-pull',['docker','pull','--platform','linux/amd64',IMAGE],600)
    image=json.loads(invoke('pulled-image-identity',['docker','image','inspect',IMAGE,'--format','[{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}]']))
    assert len(image)==1 and image[0]['Os']=='linux' and image[0]['Architecture']=='amd64' and any(x.endswith('@'+IMAGE.split('@')[1]) for x in image[0]['RepoDigests'])
    receipt['public_image_identity']=image[0]
    invoke('anonymous-git-init',git+['init',source])
    invoke('anonymous-origin',git+['-C',source,'remote','add','origin','https://github.com/floxy-21/zerorun-research.git'])
    invoke('anonymous-public-fetch',git+['-C',source,'fetch','--depth','1','origin',COMMIT],600)
    invoke('pinned-checkout',git+['-C',source,'checkout','--detach',COMMIT],180)
    head=invoke('source-head-before',git+['-C',source,'rev-parse','HEAD']).decode().strip()
    assert head==COMMIT and not invoke('source-status-before',git+['-C',source,'status','--porcelain=v2','--untracked-files=all']).strip()
    assert ident(source/'PUBLIC_RELEASE_MANIFEST.json')['sha256']==MANIFEST
    helper=source/'research/softwarex/quickstart_053.py'
    wheel=source/'output/packages/zerorun-softwarex/zerorun-0.5.3-py3-none-any.whl'
    assert ident(wheel)['sha256']==WHEEL
    receipt['public_wheel']=ident(wheel)
    invoke('documented-source-install-and-mcp',[sys.executable,'-I','-B',helper,'--source-root',source,'--create-tools-env',ROOT/'source-tools','--installation-output',OUT/'source-install.json','--output',OUT/'source-mcp.json','--approve-synthetic-formative-authority','--intervention','New official Alpine WSL2 userland and Docker daemon; prior shell-background daemon did not persist, so a hidden persistent WSL client was used.'],900)
    receipt['source_receipt_validation']=json.loads(invoke('source-receipt-validation',[sys.executable,'-I','-B',helper,'--source-root',source,'--check',OUT/'source-mcp.json','--installation-receipt',OUT/'source-install.json'],120))
    assert receipt['source_receipt_validation']['passed'] is True
    invoke('wheel-venv-create',[sys.executable,'-I','-B','-m','venv',ROOT/'wheel-tools'])
    wp=ROOT/'wheel-tools/bin/python';wz=ROOT/'wheel-tools/bin/zerorun'
    invoke('public-wheel-install',[wp,'-I','-B','-m','pip','--isolated','install','--no-cache-dir','--no-deps',wheel])
    invoke('public-wheel-mcp',[wp,'-I','-B',helper,'--source-root',source,'--zerorun-command',wz,'--zerorun-python-command',wp,'--output',OUT/'wheel-mcp.json','--approve-synthetic-formative-authority'],900)
    # The current public checker exposes a strict read-only single-check receipt API.
    code="import importlib.util,json,pathlib; p=pathlib.Path("+repr(str(helper))+"); s=importlib.util.spec_from_file_location('fresh_public_quickstart',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m.validate_saved_receipt(pathlib.Path("+repr(str(source))+"),pathlib.Path("+repr(str(OUT/'wheel-mcp.json'))+")),sort_keys=True))"
    receipt['wheel_receipt_validation']=json.loads(invoke('wheel-receipt-validation',[wp,'-I','-B','-c',code],120))
    assert receipt['wheel_receipt_validation']['passed'] is True
    assert invoke('source-head-after',git+['-C',source,'rev-parse','HEAD']).decode().strip()==COMMIT
    assert not invoke('source-status-after',git+['-C',source,'status','--porcelain=v2','--untracked-files=all']).strip()
    receipt.update(source_clean_before=True,source_clean_after=True,passed=True)
except Exception as e:receipt['failure']={'type':type(e).__name__,'message':str(e)}
receipt['finished_utc']=stamp();save('completion.json',receipt)
manifest=[{'path':p.relative_to(OUT).as_posix(),**ident(p)} for p in sorted(OUT.rglob('*')) if p.is_file()]
save('RECORD_MANIFEST.json',{'schema':'zerorun.fresh-public-records.v1','files':manifest})
print(json.dumps(receipt),flush=True)
raise SystemExit(0 if receipt['passed'] else 1)
