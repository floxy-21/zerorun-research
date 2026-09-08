from pathlib import Path
from datetime import datetime,timezone
import base64,hashlib,importlib.util,json,os,shutil,sys,time
ROOT=Path('/home/reviewer/zerorun-fresh-v1');OUT=ROOT/'failure-records';SOURCE=ROOT/'source'
HELPER=Path('/mnt/c/ZeroRun-recovery-20260908/workspace/Startup/research/softwarex/quickstart_053_metadata_v2.py')
def ident(p):
    raw=p.read_bytes();return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def save(name,value):
    with (OUT/name).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2,sort_keys=True);f.write('\n')
OUT.mkdir(mode=0o700);(OUT/'driver.py').write_bytes(Path(__file__).read_bytes())
spec=importlib.util.spec_from_file_location('fresh_public_metadata',HELPER);q=importlib.util.module_from_spec(spec);sys.modules[spec.name]=q;spec.loader.exec_module(q)
lifecycle,smoke,diagnostic=q.load_helpers(SOURCE)
ENV={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':str(ROOT/'failure-home'),'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_CONFIG_NOSYSTEM':'1','GIT_TERMINAL_PROMPT':'0','GIT_ASKPASS':'false'}
Path(ENV['HOME']).mkdir(mode=0o700)
commands=[];runner=q.recorded_runner(smoke._default_runner,smoke._encoded_stream,commands)
receipt={'schema':'zerorun.fresh-public-failure-demonstration.v1','started_utc':datetime.now(timezone.utc).isoformat(),'passed':False,'failure':None,'helper':ident(HELPER),'commands':commands,'stages':[],'model_called':False,'real_repository_authorized':False,'scope':'Two actual fresh-failure requests on a separately reviewed built-in fixture data state; no rewrite of the original five-stage success example.'}
try:
    zerorun=ROOT/'wheel-tools/bin/zerorun';py=ROOT/'wheel-tools/bin/python'
    installed=lifecycle._installed_package_identity(runner,zerorun=zerorun,python_command=str(py),source_root=SOURCE,environment=ENV)
    q.validate_core(lifecycle._source_package_identity(SOURCE/'src'),installed)
    receipt['installed_before']=installed
    with lifecycle.private_temporary_directory(Path('/tmp'),prefix='zerorun-reviewed-failure-') as temporary:
        repo=temporary/'repository';trust=temporary/'external-authority';explicit=dict(ENV,ZERORUN_TRUST_ROOT=str(trust))
        original=lifecycle._create_synthetic_repository(runner,root=repo,runtime_image=q.IMAGE,git=shutil.which('git'),environment=ENV)
        assert (repo/'fixture.py').read_bytes()==lifecycle.FIXTURE_PROGRAM.encode('utf-8')
        negative=b'zerorun deliberately unequal synthetic fixture data v1\n'
        assert negative!=lifecycle.FIXTURE_DATA.encode('utf-8')
        qualification={'scope':'Only the original fixed program and two literal data states; no real project or inferred input closure.','command':['python','fixture.py'],'inputs':['fixture.py','fixture.txt'],'environment_forwarded':[],'outputs':[],'network':'none','image':q.IMAGE,'program':ident(repo/'fixture.py'),'original_data_sha256':hashlib.sha256(lifecycle.FIXTURE_DATA.encode('utf-8')).hexdigest(),'negative_data_sha256':hashlib.sha256(negative).hexdigest(),'review_reason':'The inspected program only reads the one UTF-8 data file, compares against one fixed literal and raises SystemExit(73) on inequality. Neither state reads external input, time or random data.','authority_scope':'Explicitly user-authorized synthetic laboratory operation; separately created external exact-manifest authority. This record is an operator review assertion, not automated proof for arbitrary code.'}
        save('qualification.json',qualification)
        (OUT/'fixture.py').write_bytes((repo/'fixture.py').read_bytes());(OUT/'original-fixture.txt').write_bytes((repo/'fixture.txt').read_bytes());(OUT/'negative-fixture.txt').write_bytes(negative)
        (repo/'fixture.txt').write_bytes(negative)
        (OUT/'manifest.json').write_bytes((repo/'.zerorun.json').read_bytes())
        before=lifecycle._authority_tree_identity(trust)
        auth=smoke._run_small(runner,[str(zerorun),'--manifest',str(repo/'.zerorun.json'),'--json','authorize','--manifest-sha256',q.digest(repo/'.zerorun.json')],label='reviewed synthetic negative-state authority',cwd=temporary,environment=explicit)
        after=lifecycle._authority_tree_identity(trust)
        response=q.strict_json(auth.stdout);q.assert_authorization(response,q.digest(repo/'.zerorun.json'),before,after)
        receipt.update(fixture_root=str(repo),manifest_sha256=q.digest(repo/'.zerorun.json'),authorization=response,authority_before=before,authority_after=after,original_fixture_identity=original)
        expected_files={name:ident(repo/name) for name in ('.zerorun.json','fixture.py','fixture.txt')}
        for name,request_kind in (('fresh_failure_verify','verify'),('repeated_failure_default','miss')):
            requests=q.request_plan(request_kind,repo);start=time.monotonic()
            result=runner([str(zerorun),'mcp-server'],cwd=repo,environment=explicit,timeout_seconds=120.0,output_limit_bytes=q.LIMIT,input_bytes=b''.join(q.canonical(row)+b'\n' for row in requests))
            streams=smoke._encoded_stream(result)
            stage={'name':name,'request_kind':request_kind,'requests':requests,'streams':streams,'elapsed_seconds':time.monotonic()-start}
            receipt['stages'].append(stage)
            decoded=q.validate_streams(streams)
            responses=[q.strict_json(line) for line in decoded['stdout'].splitlines() if line.strip()]
            stage['responses']=responses
            assert len(responses)==2 and [r.get('id') for r in responses]==[1,2] and all(r.get('jsonrpc')=='2.0' and 'error' not in r for r in responses)
            assert responses[0]['result']['serverInfo']=={'name':'zerorun','version':'0.5.3'}
            wire=responses[1]['result'];payload=wire['structuredContent']
            assert wire['isError'] is True and len(wire['content'])==1 and wire['content'][0]['type']=='text' and q.strict_json(wire['content'][0]['text'])==payload
            assert payload['status']=='MISS_FAILED' and payload['exit_code']==73 and payload['task']=='synthetic-lifecycle' and payload['restored_outputs']==[]
            assert {n:ident(repo/n) for n in expected_files}==expected_files and lifecycle._authority_tree_identity(trust)==after
        assert receipt['stages'][0]['responses'][1]['result']['structuredContent']['cache_key']==receipt['stages'][1]['responses'][1]['result']['structuredContent']['cache_key']
        receipt['fixture_and_authority_stable']=True
    receipt['temporary_fixture_and_authority_cleaned']=not temporary.exists()
    receipt['installed_after']=lifecycle._installed_package_identity(runner,zerorun=zerorun,python_command=str(py),source_root=SOURCE,environment=ENV)
    assert receipt['installed_after']==installed
    receipt['passed']=True
except Exception as e:receipt['failure']={'type':type(e).__name__,'message':str(e)}
receipt['finished_utc']=datetime.now(timezone.utc).isoformat();save('completion.json',receipt)
save('RECORD_MANIFEST.json',{'schema':'zerorun.fresh-public-records.v1','files':[{'path':p.relative_to(OUT).as_posix(),**ident(p)} for p in sorted(OUT.rglob('*')) if p.is_file()]})
print(json.dumps({'passed':receipt['passed'],'failure':receipt['failure'],'stages':len(receipt['stages'])}),flush=True)
raise SystemExit(0 if receipt['passed'] else 1)
