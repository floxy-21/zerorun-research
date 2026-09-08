import json,sys
from pathlib import Path
sys.dont_write_bytecode=True
ROOT=Path('/home/floxy/zerorun-v6-preparation-20260907')
SOURCE=Path('/home/floxy/zerorun-compatibility-preflight-20260907-v2')
sys.path.insert(0,str(SOURCE/'harness'))
from research.softwarex.handoff_v1 import run as h
bench,*_=h.load_engine(SOURCE/'engine')
records=ROOT/'preflight-records';prior=h.strict((records/'completion.json').read_bytes())
assert len(prior['cases'])==24 and prior['first_case']['passed'] and prior['first_case']['nodes']==102
assert prior['public_sources_unchanged'] and prior['runtime_attestation_unchanged']
assert prior['guest_boot_id']==Path('/proc/sys/kernel/random/boot_id').read_text().strip()
for row in h.strict((records/'RECORD_MANIFEST.json').read_bytes())['files']:h.bound(records,row)
out=ROOT/'preflight-audit';out.mkdir()
(out/'driver.py').write_bytes(Path(__file__).read_bytes())
(out/'amendment.md').write_bytes((ROOT/'PREFLIGHT_AUDIT_AMENDMENT.md').read_bytes())
ledger=h.strict((ROOT/'corrected-acquisition/main.json').read_bytes())
old_skip=h.strict((ROOT/'original-v5-lizard174-capture.json').read_bytes())
skipped=lambda c:[n for n,o in zip(c['nodeids'],c['outcomes']) if o['call']=='skipped']
expected=['test/testNestedStructures.py::TestCppNestedStructures::test_struct_inside_declaration','test/testNestedStructures.py::TestCppNestedStructures::test_struct_inside_definition']
assert skipped(old_skip)==expected and old_skip['exit_code']==0
rows=[]
for i,case in enumerate(ledger['cases']):
    label=f'case-{i:02d}-{case["case_id"]}'
    capture=bench._validate_shadow_evidence(h.strict((records/label/'raw-outcomes.json').read_bytes()))
    runner=h.strict((records/label/'runner.json').read_bytes())
    assert runner['exit_code']==capture['exit_code']==0 and capture['all_nodes_non_failing'] and capture['complete_per_node_outcomes']
    assert skipped(capture)==(expected if case['case_id']=='terryyin__lizard-174' else [])
    assert all(o['call'] in ('passed','skipped') and not o['wasxfail'] for o in capture['outcomes'])
    identity=h.strict((records/label/'source-preparation.json').read_bytes())['identity']
    assert h.identity(ROOT/'workspaces'/label/'source')==identity==h.identity(ROOT/'workspaces'/label/'workspace')
    preliminary=prior['cases'][i];assert preliminary['case_id']==case['case_id']
    if not preliminary['passed']:
        assert case['case_id']=='terryyin__lizard-174' and preliminary['error']=={'type':'AssertionError','message':''}
    row={'case_id':case['case_id'],'passed':True,'nodes':len(capture['nodeids']),'skipped_nodeids':skipped(capture),
         'source_unchanged':True,'source_sha256':identity['sha256'],'verdict':h.outcome(capture),
         'capture_sha256':h.sha((records/label/'raw-outcomes.json').read_bytes()),'preliminary_driver_passed':preliminary['passed']}
    rows.append(row)
completion={'schema':'zerorun.original-semantics-preflight-audit.v6','passed':True,'completed_utc':h.utc(),
    'first_case':prior['first_case'],'cases':rows,'original_completion_sha256':h.sha((records/'completion.json').read_bytes()),
    'original_preliminary_failure_preserved':True,'expected_historical_skips_retained':expected,
    'guest_boot_id':prior['guest_boot_id'],'original_validator_unchanged':True}
h.save(out/'completion.json',completion)
h.save(out/'RECORD_MANIFEST.json',{'files':[h.record(p,p.name) for p in sorted(out.iterdir()) if p.is_file()]})
print(json.dumps({'passed':True,'cases':len(rows),'first_case_nodes':prior['first_case']['nodes'],'retained_skips':2}))
