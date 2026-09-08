from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,signal,subprocess,sys,time
ROOT=Path('/home/reviewer/zerorun-fresh-v1');OUT=ROOT/'corrected-records'
HELPER=Path('/mnt/c/ZeroRun-recovery-20260908/workspace/Startup/research/softwarex/quickstart_053_metadata_v2.py')
COMMIT='13a3ceae84913a1ddbed412bdad9cd6fbf7e2e54'
def stamp():return datetime.now(timezone.utc).isoformat()
def ident(p):
    raw=p.read_bytes();return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(n,v):
    with (OUT/n).open('x',encoding='utf-8') as f:json.dump(v,f,indent=2,sort_keys=True);f.write('\n')
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
(OUT/'driver.py').write_bytes(Path(__file__).read_bytes())
(OUT/'quickstart_053_metadata_v2.py').write_bytes(HELPER.read_bytes())
(OUT/'QUICKSTART_053_METADATA_V2.md').write_bytes(HELPER.with_name('QUICKSTART_053_METADATA_V2.md').read_bytes())
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(ROOT/'git-home'),'USER':'reviewer','LOGNAME':'reviewer','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'false','SSH_ASKPASS':'false','GCM_INTERACTIVE':'never','PIP_CONFIG_FILE':'/dev/null','PIP_NO_CACHE_DIR':'1'}
source=ROOT/'source'
receipt={'schema':'zerorun.fresh-public-corrected-demonstration.v2','started_utc':stamp(),'passed':False,'failure':None,'source_commit':COMMIT,'helper':ident(HELPER),'guide':ident(HELPER.with_name('QUICKSTART_053_METADATA_V2.md')),'prior_attempt_completion':ident(ROOT/'records/completion.json'),'prior_attempt_manifest':ident(ROOT/'records/RECORD_MANIFEST.json'),'intervention':'Use separately versioned external helper for8MiB full-source Git metadata;2MiB MCP streams and runtime/authority unchanged. Originalr1cloneandfailedattemptretained.','independent_human_replication':False,'runtime_changed':False,'model_called':False}
save('protocol.json',{k:v for k,v in receipt.items() if k not in ('passed','failure')})
try:
    assert invoke('source-head-before',['git','-C',source,'rev-parse','HEAD']).decode().strip()==COMMIT
    assert not invoke('source-status-before',['git','-C',source,'status','--porcelain=v2','--untracked-files=all']).strip()
    invoke('corrected-source-install-and-mcp',[sys.executable,'-I','-B',HELPER,'--source-root',source,'--create-tools-env',ROOT/'corrected-source-tools','--installation-output',OUT/'install.json','--output',OUT/'check.json','--approve-synthetic-formative-authority','--intervention',receipt['intervention']],900)
    receipt['source_validation']=json.loads(invoke('source-receipt-validation',[sys.executable,'-I','-B',HELPER,'--source-root',source,'--check',OUT/'check.json','--installation-receipt',OUT/'install.json'],120))
    assert receipt['source_validation']['passed'] is True
    invoke('wheel-venv-create',[sys.executable,'-I','-B','-m','venv',ROOT/'wheel-tools'])
    wp=ROOT/'wheel-tools/bin/python';wz=ROOT/'wheel-tools/bin/zerorun'
    wheel=source/'output/packages/zerorun-softwarex/zerorun-0.5.3-py3-none-any.whl'
    assert ident(wheel)['sha256']=='dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9'
    receipt['wheel']=ident(wheel)
    invoke('public-wheel-install',[wp,'-I','-B','-m','pip','--isolated','install','--no-cache-dir','--no-deps',wheel])
    invoke('public-wheel-mcp',[wp,'-I','-B',HELPER,'--source-root',source,'--zerorun-command',wz,'--zerorun-python-command',wp,'--output',OUT/'wheel-check.json','--approve-synthetic-formative-authority','--intervention',receipt['intervention']],900)
    code="import importlib.util,json,pathlib,sys; p=pathlib.Path("+repr(str(HELPER))+"); s=importlib.util.spec_from_file_location('fresh_public_quickstart',p); m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); print(json.dumps(m.validate_saved_receipt(pathlib.Path("+repr(str(source))+"),pathlib.Path("+repr(str(OUT/'wheel-check.json'))+")),sort_keys=True))"
    receipt['wheel_validation']=json.loads(invoke('wheel-receipt-validation',[wp,'-I','-B','-c',code],120))
    assert receipt['wheel_validation']['passed'] is True
    assert invoke('source-head-after',['git','-C',source,'rev-parse','HEAD']).decode().strip()==COMMIT
    assert not invoke('source-status-after',['git','-C',source,'status','--porcelain=v2','--untracked-files=all']).strip()
    assert ident(HELPER)==receipt['helper'] and ident(HELPER.with_name('QUICKSTART_053_METADATA_V2.md'))==receipt['guide']
    receipt.update(source_clean_before=True,source_clean_after=True,passed=True)
except Exception as e:receipt['failure']={'type':type(e).__name__,'message':str(e)}
receipt['finished_utc']=stamp();save('completion.json',receipt)
save('RECORD_MANIFEST.json',{'schema':'zerorun.fresh-public-records.v1','files':[{'path':p.relative_to(OUT).as_posix(),**ident(p)} for p in sorted(OUT.rglob('*')) if p.is_file()]})
print(json.dumps(receipt),flush=True)
raise SystemExit(0 if receipt['passed'] else 1)
