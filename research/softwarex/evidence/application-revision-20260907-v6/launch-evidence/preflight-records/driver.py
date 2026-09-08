"""Corrected Lizard first, followed by original-order all-case preparation."""
import hashlib,json,os,shutil,subprocess,sys,tarfile,time
from pathlib import Path,PurePosixPath
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path('/home/floxy/zerorun-v6-preparation-20260907')
SOURCE=Path('/home/floxy/zerorun-compatibility-preflight-20260907-v2')
LEDGER='e6453b256f80e0eb79d280acc6bb8a143c006e56854f1702300cdc6550ab9ee2'
ARCHIVE='9834824bbe1c5ad5b9868b55ed069b2baa02d49c730bbb304a59081ec0d154d8'
IMAGE='127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465'
assert os.name=='posix' and os.getuid()!=0
sys.path.insert(0,str(SOURCE/'harness'))
from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_image_v2.run import image_arm_workspace
def binding(path,expected):
    actual=subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],check=True,capture_output=True).stdout.decode().strip()
    status=subprocess.run(['git','-C',str(path),'status','--porcelain=v1','--untracked-files=all'],check=True,capture_output=True).stdout
    assert actual==expected and not status
    return actual
before={'harness':binding(SOURCE/'harness','0528905a52b74df78aa4e5a09219df34620282dd'),
        'engine':binding(SOURCE/'engine','ebf2884df12573d63f45813200e0675288d12096')}
archive=ROOT/'corrected-acquisition.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==ARCHIVE
acquisition=ROOT/'corrected-acquisition';assert not acquisition.exists();acquisition.mkdir()
with tarfile.open(archive,'r:gz') as tar:
    seen=set()
    for member in tar:
        path=PurePosixPath(member.name)
        assert member.isfile() and not path.is_absolute() and '..' not in path.parts and member.name not in seen
        assert member.size<=h.MAX_ARCHIVE_BYTES
        destination=acquisition.joinpath(*path.parts);destination.parent.mkdir(parents=True,exist_ok=True)
        with destination.open('xb') as out:out.write(tar.extractfile(member).read())
        seen.add(member.name)
manifest=h.strict((acquisition/'RECORD_MANIFEST.json').read_bytes())
assert seen=={r['path'] for r in manifest['files']}|{'RECORD_MANIFEST.json'}
for row in manifest['files']:h.bound(acquisition,row,h.MAX_ARCHIVE_BYTES)
ledger=acquisition/'main.json';assert h.sha(ledger.read_bytes())==LEDGER
cases=h.validate_ledger(h.strict(ledger.read_bytes()));assert len(cases)==24
components=h.load_engine(SOURCE/'engine');bench,_,oci,load_manifest=components[:4]
image_build=SOURCE/'image-repair-v2/completion.json';build=h.strict(image_build.read_bytes());assert build['passed']
image=build['result']['image'];assert image['requested']==IMAGE
inspection=ROOT/'image-inspection';inspection.mkdir()
(inspection/'.zerorun.json').write_bytes(bench._frozen_pytest_manifest_bytes(runtime_image=IMAGE))
task=load_manifest(inspection/'.zerorun.json').tasks['pytest-generalization']
attestation=oci.inspect_runtime(task,allow_pull=False,repository_root=SOURCE/'engine')
assert attestation['image_id']==image['image_id'] and attestation['config_sha256']==image['config_sha256']
records=ROOT/'preflight-records';records.mkdir()
(records/'driver.py').write_bytes(Path(__file__).read_bytes())
h.save(records/'protocol.json',{'schema':'zerorun.corrected-fixture-preflight.v6','started_utc':h.utc(),
    'ledger_sha256':LEDGER,'archive_sha256':ARCHIVE,'driver':h.record(records/'driver.py'),
    'public_sources_before':before,'image':image,'runtime_attestation':attestation,'engine_binding':components[-1],
    'guest_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'cpus':os.cpu_count(),
    'first_check':'terryyin__lizard-191: full target plus existing complexity specification (102 nodes)',
    'then':[c['case_id'] for c in cases],'per_command_seconds':120,'paired_performance_claim':False,
    'historical_records_modified':False,'fixture_correction':h.strict((acquisition/'FIXTURE_CORRECTION.json').read_bytes())})
original=bench._plain_run
def check(case,label,targets,expected_nodes=None):
    output=records/label;output.mkdir()
    work=ROOT/'workspaces'/label;work.mkdir(parents=True)
    row={'case_id':case['case_id'],'started_utc':h.utc(),'error':None,'passed':False}
    counter=[0]
    def observed(root,argv,*,timeout_seconds):
        name=f'command-{counter[0]:03d}';counter[0]+=1
        entry={'argv':list(argv),'cwd':str(root),'started_utc':h.utc(),'timeout_seconds':timeout_seconds}
        h.save(output/(name+'.started.json'),entry)
        result=original(root,argv,timeout_seconds=timeout_seconds)
        for key in ('stdout','stderr'):
            raw=getattr(result,key) or b''
            if isinstance(raw,str):raw=raw.encode()
            path=output/(name+'.'+key+'.txt');path.write_bytes(raw);entry[key]=h.record(path)
        entry.update(returncode=result.returncode,completed_utc=h.utc());h.save(output/(name+'.json'),entry)
        return result
    try:
        source=h.make_state(case,acquisition,work)
        source_before=h.identity(source)
        (output/'source-preparation.json').write_bytes((work/'source-preparation.json').read_bytes())
        workspace=work/'workspace'
        with patch.object(h,'IMAGE',IMAGE):
            setup,_=image_arm_workspace(bench,source,workspace,{'provenance':{'preflight_only':True,'image':IMAGE}},targets)
        h.save(output/'setup.json',setup)
        support=output/'support';support.mkdir()
        (support/'benchmark_shadow_plugin.py').write_text(bench._SHADOW_PLUGIN,encoding='utf-8')
        raw=output/'raw-outcomes.json';raw.touch();raw.chmod(0o666)
        with patch.object(bench,'_PYTEST_EXECUTION_TIMEOUT_SECONDS',120),patch.object(bench,'_plain_run',observed):
            runner=bench._plain_pytest_container(workspace,targets=targets,runtime_image=IMAGE,support=support,shadow_output=raw)
        h.save(output/'runner.json',runner)
        capture=bench._validate_shadow_evidence(h.strict(raw.read_bytes()))
        h.save(output/'capture.json',capture)
        assert capture['exit_code']==runner['exit_code']==0
        assert capture['all_nodes_non_failing'] and capture['complete_per_node_outcomes']
        assert all(o['call']=='passed' and not o['wasxfail'] for o in capture['outcomes'])
        if expected_nodes is not None:assert len(capture['nodeids'])==expected_nodes
        after=h.identity(workspace);assert after==source_before==h.identity(source)
        h.save(output/'source-after.json',after)
        row.update(passed=True,nodes=len(capture['nodeids']),source_unchanged=True,source_sha256=source_before['sha256'],exit_code=0)
    except Exception as error:
        row['error']={'type':type(error).__name__,'message':str(error)}
    row['completed_utc']=h.utc();h.save(output/'completion.json',row)
    print(json.dumps(row),flush=True)
    return row
results=[];first=None
try:
    case=next(c for c in cases if c['case_id']=='terryyin__lizard-191')
    first=check(case,'lizard-first',[*case['targets'],'test/testCyclomaticComplexity.py'],102)
    assert first['passed'],'Corrected Lizard FIRST check failed; all-case launch held'
    for index,case in enumerate(cases):
        result=check(case,f'case-{index:02d}-{case["case_id"]}',case['targets']);results.append(result)
    assert len(results)==24 and all(r['passed'] for r in results),'One or more all-case preparations failed'
finally:
    after={'harness':binding(SOURCE/'harness',before['harness']),'engine':binding(SOURCE/'engine',before['engine'])}
    final=oci.inspect_runtime(task,allow_pull=False,repository_root=SOURCE/'engine')
    assert after==before and h.encoded(final)==h.encoded(attestation)
    completion={'schema':'zerorun.corrected-fixture-preflight-completion.v6','completed_utc':h.utc(),
        'passed':bool(first and first['passed'] and len(results)==24 and all(r['passed'] for r in results)),
        'first_case':first,'cases':results,'public_sources_unchanged':after==before,'runtime_attestation_unchanged':True,
        'ledger_sha256':LEDGER,'guest_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    h.save(records/'completion.json',completion)
    h.save(records/'RECORD_MANIFEST.json',{'files':[h.record(p,p.relative_to(records).as_posix()) for p in sorted(records.rglob('*')) if p.is_file()]})
    with tarfile.open(ROOT/'preflight-records.tar.gz','x:gz') as tar:
        for p in sorted(records.rglob('*')):
            if p.is_file():tar.add(p,arcname=p.relative_to(records).as_posix(),recursive=False)
    print(json.dumps({'preflight_completion':completion}),flush=True)
