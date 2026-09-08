"""One full original-24 handoff campaign after independently verified preflight."""
import argparse
from contextlib import contextmanager,ExitStack
import hashlib,json,os,shutil,subprocess,sys,tarfile,time
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode=True
SOURCE=Path('/home/floxy/zerorun-compatibility-preflight-20260907-v2')
OUTPUT=Path('/home/floxy/zerorun-application-revision-20260907-v5')
HARNESS='0528905a52b74df78aa4e5a09219df34620282dd'
ENGINE='ebf2884df12573d63f45813200e0675288d12096'
LEDGER='4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997'

parser=argparse.ArgumentParser();parser.add_argument('--amendment',type=Path,required=True)
parser.add_argument('--gate',type=Path,required=True);args=parser.parse_args()
assert os.name=='posix' and os.getuid()!=0 and not OUTPUT.exists()
gate=json.loads(args.gate.read_bytes());assert gate['approved_for_timed_launch'] is True
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==gate['guest_boot_id']
assert os.cpu_count()==gate['guest_cpus']==5
preflight=SOURCE/'preflight-compatible-image-v2/record-only/completion.json'
assert hashlib.sha256(preflight.read_bytes()).hexdigest()==gate['preflight_completion_sha256']
pre=json.loads(preflight.read_bytes());assert pre['preflight_cases_attempted']==24 and pre['public_sources_unchanged']
assert all(c['source_unchanged'] and c['execution_error'] is None and c['verdict_error'] is None for c in pre['cases'])
failed=[c['case_id'] for c in pre['cases'] if c['runner']['exit_code']!=0]
assert failed==gate['allowed_unsupported_cases']
image_build_path=SOURCE/'image-repair-v2/completion.json'
assert hashlib.sha256(image_build_path.read_bytes()).hexdigest()==gate['image_build_completion_sha256']
image_build=json.loads(image_build_path.read_bytes());assert image_build['passed'] is True
image=image_build['result']['image'];assert image['requested']==gate['image']
harness,engine=SOURCE/'harness',SOURCE/'engine'
def clean_binding(path,commit):
    head=subprocess.run(['git','-C',str(path),'rev-parse','HEAD'],capture_output=True,check=True).stdout.decode().strip()
    status=subprocess.run(['git','-C',str(path),'status','--porcelain=v1','--untracked-files=all'],capture_output=True,check=True).stdout
    assert head==commit and not status
    return {'commit':head,'clean':True}
before={'harness':clean_binding(harness,HARNESS),'engine':clean_binding(engine,ENGINE)}
ledger=harness/'research/softwarex/evidence/handoff-acquisition-recovery-v1/main.json'
assert hashlib.sha256(ledger.read_bytes()).hexdigest()==LEDGER
selected=json.loads(ledger.read_bytes())['cases'];assert len(selected)==24
assert [c['case_id'] for c in selected]==[c['case_id'] for c in pre['cases']]
sys.path.insert(0,str(harness))
from research.softwarex.handoff_v1 import run as h,validate as v
from research.softwarex.handoff_image_v2.run import image_arm_workspace
components=h.load_engine(engine);bench,_,oci,load_manifest=components[:4]
OUTPUT.mkdir();bundle=OUTPUT/'record-only';bundle.mkdir()
provenance=bundle/'provenance';provenance.mkdir()
for path,name in [(Path(__file__),'driver.py'),(args.amendment,'APPLICATION_REVISION_V5_AMENDMENT.md'),
                  (args.gate,'launch-gate.json'),(preflight,'preflight-completion.json'),
                  (image_build_path,'image-build-completion.json')]:
    (provenance/name).write_bytes(path.read_bytes())
inspection=OUTPUT/'runtime-inspection';inspection.mkdir()
(inspection/'.zerorun.json').write_bytes(bench._frozen_pytest_manifest_bytes(runtime_image=image['requested']))
task=load_manifest(inspection/'.zerorun.json').tasks['pytest-generalization']
attestation=oci.inspect_runtime(task,allow_pull=False,repository_root=engine)
assert attestation['image_id']==image['image_id'] and attestation['config_sha256']==image['config_sha256']
h.save(provenance/'protocol.json',{'schema':'zerorun.full-cohort-compatible-image.v5','started_utc':h.utc(),
    'selected_cases':24,'ledger_sha256':LEDGER,'public_source_before':before,
    'actual_vm_memory_mib':1536,'guest_cpus':5,'guest_boot_id':gate['guest_boot_id'],
    'image':image,'runtime_attestation_before':attestation,'engine_binding':components[-1],
    'execution_seconds':120,'operational_guard_seconds':14400,'former_20_minute_cutoff_used':False,
    'common_image_setup_seconds':image_build['setup_outer_seconds'],'image_setup_in_chain_ratio':False,
    'adapter_substitutions':['h.IMAGE: candidate RepoDigest','h.arm_workspace: existing image-contained preparation',
      'h.load_engine: same checked components','bench._frozen_dependency_layer: already built immutable image provenance'],
    'unchanged':'All ZeroRun runtime files, admission, authorization, fingerprinting, original cases, targets, assertions, arm order, operations, fresh oracles and accounting',
    'preflight_not_a_paired_campaign':True,'all_prior_failures_preserved':True,
    'bindings':[h.record(p,p.name) for p in sorted(provenance.iterdir()) if p.is_file()]})

@contextmanager
def image_layer(root,*,runtime_image,extra_requirements):
    assert runtime_image==image['requested'] and tuple(extra_requirements)==('pretend',)
    yield {'provenance':{'schema':'zerorun.compatible-image-deployment.v5','image':image,
        'locked_packages':image_build['result']['packages'],'build_completion_sha256':gate['image_build_completion_sha256'],
        'preparation_precedes_run':True,'common_build_once_seconds':image_build['setup_outer_seconds']}}

error=None;result=None;summary=None;main_exit=None;started=time.monotonic()
h.save(provenance/'main-full.started.json',{'started_utc':h.utc(),'monotonic_seconds':started})
try:
    with ExitStack() as stack:
        stack.enter_context(patch.object(h,'IMAGE',image['requested']))
        stack.enter_context(patch.object(h,'arm_workspace',image_arm_workspace))
        stack.enter_context(patch.object(h,'load_engine',lambda selected_engine: components if selected_engine==engine else (_ for _ in ()).throw(ValueError('engine mismatch'))))
        stack.enter_context(patch.object(bench,'_frozen_dependency_layer',image_layer))
        result=h.run(ledger,engine,OUTPUT/'run',execution_seconds=120,budget_seconds=14400)
    main_exit=0 if result['campaign_error'] is None and result['material_correctness_stop'] is False else 1
    assert result['selected_cases']==24 and len(result['cases'])==24
    assert [c['case_id'] for c in result['cases']]==[c['case_id'] for c in selected]
    assert not any(c['disposition'].startswith('NOT_RUN') or c['disposition']=='UNAVAILABLE_ACQUISITION' for c in result['cases'])
    assert main_exit==0
    summary=v.validate_saved(OUTPUT/'run',ledger.parent)
    h.save(provenance/'independent-guest-reconciliation.json',summary)
except Exception as caught:
    error={'type':type(caught).__name__,'message':str(caught)}
finally:
    h.save(provenance/'main-full.json',{'completed_utc':h.utc(),'elapsed_seconds':time.monotonic()-started,
        'error':error,'main_exit_code':main_exit})
    after={'harness':clean_binding(harness,HARNESS),'engine':clean_binding(engine,ENGINE)}
    after_attestation=oci.inspect_runtime(task,allow_pull=False,repository_root=engine)
    assert before==after and h.encoded(attestation)==h.encoded(after_attestation)
    complete=result is not None and len(result['cases'])==24 and not any(c['disposition'].startswith('NOT_RUN') or c['disposition']=='UNAVAILABLE_ACQUISITION' for c in result['cases'])
    completion={'schema':'zerorun.full-cohort-compatible-image-completion.v5','completed_utc':h.utc(),
        'error':error,'all_24_attempted':complete,'complete_paired_cases':result['complete_cases'] if result else None,
        'material_correctness_stop':result['material_correctness_stop'] if result else None,
        'source_unchanged':before==after,'runtime_attestation_after':after_attestation,
        'guest_reconciliation_passed':summary is not None,'host_continuity_requires_independent_validation':True,
        'cases':[{'case_id':c['case_id'],'disposition':c['disposition'],'error':c.get('error')} for c in result['cases']] if result else None}
    h.save(provenance/'completion.json',completion)
    excluded={'source','workspace','preflight-workspace','dependency-starter','private-cache-authentication-NOT-FOR-PUBLICATION','.git','.zerorun','.zerorun-env','__pycache__'}
    run=OUTPUT/'run'
    if run.exists():
        for folder,dirs,files in os.walk(run,followlinks=False):
            dirs[:]=[d for d in dirs if d not in excluded]
            for name in files:
                path=Path(folder)/name;assert not path.is_symlink()
                destination=bundle/'run'/path.relative_to(run)
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,destination)
    rows=[h.record(p,p.relative_to(bundle).as_posix()) for p in sorted(bundle.rglob('*')) if p.is_file()]
    h.save(bundle/'RECORD_MANIFEST.json',{'files':rows})
    archive=OUTPUT/'record-only.tar.gz'
    with tarfile.open(archive,'x:gz') as tar:
        for p in sorted(bundle.rglob('*')):
            if p.is_file():tar.add(p,arcname=p.relative_to(OUTPUT).as_posix(),recursive=False)
    digest=hashlib.sha256()
    with archive.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    h.save(OUTPUT/'transfer.json',{'path':str(archive),'bytes':archive.stat().st_size,'sha256':digest.hexdigest()})
    print(json.dumps(completion),flush=True)
raise SystemExit(int(error is not None))
