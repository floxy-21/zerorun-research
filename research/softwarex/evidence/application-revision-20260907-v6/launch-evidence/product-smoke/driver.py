import hashlib,json,os,sys
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
sys.dont_write_bytecode=True
ROOT=Path('/home/floxy/zerorun-compatibility-preflight-20260907-v2')
INPUT=Path('/home/floxy/zerorun-v6-preparation-20260907')
out=INPUT/'product-smoke-v6';assert not out.exists();out.mkdir()
bundle=out/'record-only';bundle.mkdir()
private=out/'private-cache-authentication-NOT-FOR-PUBLICATION';private.mkdir(mode=0o700)
sys.path.insert(0,str(ROOT/'harness'))
from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_image_v2.run import image_arm_workspace
bench,api,oci,load_manifest,binding=h.load_engine(ROOT/'engine')
image=json.loads((ROOT/'image-repair-v2/completion.json').read_bytes())['result']['image']
pre=json.loads((INPUT/'preflight-audit/completion.json').read_bytes());assert pre['passed']
index,case=next((i,c) for i,c in enumerate(pre['cases']) if c['case_id']=='terryyin__lizard-191')
source=INPUT/'workspaces'/f'case-{index:02d}-terryyin__lizard-191'/'source'
source_identity=h.identity(source);assert source_identity['sha256']==case['source_sha256']
(bundle/'driver.py').write_bytes(Path(__file__).read_bytes())
(bundle/'amendment.md').write_bytes((INPUT/'PRODUCT_PREFLIGHT_AMENDMENT.md').read_bytes())
h.save(bundle/'protocol.json',{'schema':'zerorun.compatible-image-product-smoke.v1','started_utc':h.utc(),
    'case_id':case['case_id'],'targets':['test/test_languages/testCAndCPP.py'],'image':image,'engine_binding':binding,
    'source_identity':source_identity,'expected_fresh_verdict':case['verdict'],'performance_claims':False,
    'driver':h.record(bundle/'driver.py','driver.py'),'amendment':h.record(bundle/'amendment.md','amendment.md')})
error=None
try:
    with ExitStack() as stack:
        stack.enter_context(patch.object(h,'IMAGE',image['requested']))
        stack.enter_context(patch.object(bench,'_PYTEST_EXECUTION_TIMEOUT_SECONDS',120))
        stack.enter_context(patch.object(oci,'DOCKER_EXECUTION_TIMEOUT_SECONDS',120))
        stack.enter_context(patch.dict(os.environ,{'ZERORUN_TRUST_ROOT':str(private),'GIT_TERMINAL_PROMPT':'0'}))
        work=out/'workspace'
        setup,manifest=image_arm_workspace(bench,source,work,{'provenance':{'image':image,'preflight_only':True}},['test/test_languages/testCAndCPP.py'])
        h.save(bundle/'setup.json',{'setup':setup,'manifest':manifest})
        loaded=load_manifest(work/'.zerorun.json')
        attestation=oci.inspect_runtime(loaded.tasks['handoff-tests'],allow_pull=False,repository_root=ROOT/'engine')
        assert attestation['image_id']==image['image_id'] and attestation['config_sha256']==image['config_sha256']
        h.save(bundle/'runtime-attestation.json',attestation)
        producer=h.operation(bundle,'producer',lambda:h.product(api,loaded))
        consumer=h.operation(bundle,'consumer',lambda:h.product(api,loaded))
        oracle=h.operation(bundle,'oracle',lambda:h.fresh_oracle(bench,work,['test/test_languages/testCAndCPP.py'],bundle/'oracle-capture'))
        diagnostics=h.operation(bundle,'diagnostics',lambda:h.product(api,loaded,verify=True))
        assert producer['result']['status']=='MISS_EXECUTED'
        assert consumer['result']['status']=='HIT_REUSED'
        assert producer['result']['cache_key']==consumer['result']['cache_key']
        assert diagnostics['result']['status']=='VERIFY_MATCH'
        assert producer['result']['exit_code']==consumer['result']['exit_code']==diagnostics['result']['exit_code']==0
        assert oracle['result']['verdict']==case['verdict']
        assert h.identity(work)==source_identity==h.identity(source)
        h.save(bundle/'source-after.json',h.identity(work))
except Exception as caught:
    error={'type':type(caught).__name__,'message':str(caught)}
h.save(bundle/'completion.json',{'schema':'zerorun.compatible-image-product-smoke-completion.v1',
    'completed_utc':h.utc(),'passed':error is None,'error':error,'performance_claims':False,
    'private_cache_not_reused_by_main':True})
h.save(bundle/'RECORD_MANIFEST.json',{'files':[h.record(p,p.relative_to(bundle).as_posix()) for p in sorted(bundle.rglob('*')) if p.is_file()]})
print(json.dumps({'passed':error is None,'error':error}),flush=True)
raise SystemExit(int(error is not None))
