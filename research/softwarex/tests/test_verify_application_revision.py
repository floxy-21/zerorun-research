"""Artificial failed bundles exercise provenance rejection; no workload is run."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from research.softwarex import verify_application_revision as audit


class ApplicationRevisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='zerorun-revision-verifier-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.bundle=self.root/'record-only'; self.bundle.mkdir()
        self.driver=b'Artificial test observer, not an executable experiment.\n'
        self.amendment=b'Artificial prospective test amendment.\n'
        self.write('provenance/driver.py',self.driver)
        self.write('provenance/APPLICATION_REVISION_AMENDMENT.md',self.amendment)
        self.write('commands/harness-init.stdout.log',b'')
        self.write('commands/harness-init.stderr.log',b'Artificial transport failure.\n')
        self.started={'label':'harness-init','argv':['git','init','/new/artificial/checkout'],
            'started_utc':'2026-09-07T08:00:00+00:00','timeout_seconds':120,
            'returncode':None,'timed_out':False,'error':None}
        self.row={**self.started,'completed_utc':'2026-09-07T08:00:01+00:00','elapsed_seconds':1.0,
            'returncode':128,'stdout':self.record('commands/harness-init.stdout.log'),
            'stderr':self.record('commands/harness-init.stderr.log')}
        self.dump('commands/harness-init.started.json',self.started)
        self.dump('commands/harness-init.json',self.row)
        self.protocol={'schema':'zerorun.application-revision-driver.v1','harness_commit':audit.HARNESS,
            'engine_commit':audit.ENGINE,'public_remote':audit.PUBLIC,
            'sequence':['main_original_24_existing_image','fresh_image_build','original_two_case_pilot_new_image'],
            'main_budget_seconds':1200,'pilot_budget_seconds':600,'execution_seconds':120,
            'main_outer_timeout_seconds':1560,'pilot_outer_timeout_seconds':960,'registry_port':19519,
            'driver':self.record('provenance/driver.py'),'amendment':self.record('provenance/APPLICATION_REVISION_AMENDMENT.md'),
            'no_concurrent_benchmarks':True,'model_calls':0,'runtime_source_modified':False,
            'git_credentials_used':False,'bit_identical_rebuild_required':False}
        self.completion={'schema':'zerorun.application-revision-completion.v1','commands':[self.row],
            'automatic_retries':0,'old_results_overwritten':False,'workspaces_and_private_keys_exported':False,
            'external_independent_researcher':False,'bit_identical_rebuild_claimed':False,
            'passed':False,'finished_sequence':False,'error':{'type':'RuntimeError','message':'artificial transport failure'},
            'material_correctness_stop':False}
        self.dump('provenance/protocol.json',self.protocol)
        self.dump('completion.json',self.completion)
        self.refresh()

    def write(self,name,raw):
        path=self.bundle/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)

    def dump(self,name,value):
        self.write(name,json.dumps(value,sort_keys=True).encode())

    def record(self,name):
        raw=(self.bundle/name).read_bytes()
        return {'path':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}

    def refresh(self):
        rows=[self.record(p.relative_to(self.bundle).as_posix()) for p in sorted(self.bundle.rglob('*'))
              if p.is_file() and p.name!='RECORD_MANIFEST.json']
        self.dump('RECORD_MANIFEST.json',{'files':rows,'contains_credentials':False})

    def verify(self):
        return audit.verify(self.root,self.bundle,hashlib.sha256(self.driver).hexdigest(),
                            hashlib.sha256(self.amendment).hexdigest())

    def test_authentic_failed_attempt_never_certifies_execution(self):
        result=self.verify()
        self.assertEqual(result['state'],'RECONCILED_FAILED_ATTEMPT')
        self.assertFalse(result['checkout_execution_binding_confirmed'])
        self.assertFalse(result['fresh_real_workload_reproduction_confirmed'])
        self.assertEqual(result['failed_commands'],['harness-init'])

    def test_independent_driver_pin_survives_forged_self_consistent_receipts(self):
        self.write('provenance/driver.py',b'Changed observer.\n')
        self.protocol['driver']=self.record('provenance/driver.py')
        self.dump('provenance/protocol.json',self.protocol);self.refresh()
        with self.assertRaisesRegex(ValueError,'pinned driver'): self.verify()

    def test_independent_amendment_pin_survives_forged_receipts(self):
        self.write('provenance/APPLICATION_REVISION_AMENDMENT.md',b'Changed plan.\n')
        self.protocol['amendment']=self.record('provenance/APPLICATION_REVISION_AMENDMENT.md')
        self.dump('provenance/protocol.json',self.protocol);self.refresh()
        with self.assertRaisesRegex(ValueError,'pinned amendment'): self.verify()

    def test_changed_stream_rejected_even_after_outer_manifest_is_regenerated(self):
        self.write('commands/harness-init.stderr.log',b'Different actual failure.\n');self.refresh()
        with self.assertRaisesRegex(ValueError,'binding mismatch'): self.verify()

    def test_omitted_file_is_not_silently_unbound(self):
        saved=audit.read(self.bundle/'RECORD_MANIFEST.json');saved['files'].pop()
        self.dump('RECORD_MANIFEST.json',saved)
        with self.assertRaisesRegex(ValueError,'exhaustive'): self.verify()

    def test_duplicate_manifest_path_is_rejected(self):
        saved=audit.read(self.bundle/'RECORD_MANIFEST.json');saved['files'].append(copy.deepcopy(saved['files'][0]))
        self.dump('RECORD_MANIFEST.json',saved)
        with self.assertRaisesRegex(ValueError,'duplicate'): self.verify()

    def test_summary_cannot_override_raw_command_exit(self):
        self.completion['commands'][0]['returncode']=0
        self.dump('completion.json',self.completion);self.refresh()
        with self.assertRaisesRegex(ValueError,'raw command'): self.verify()

    def test_start_record_cannot_hide_different_command(self):
        self.started['argv']=['git','other-command'];self.dump('commands/harness-init.started.json',self.started);self.refresh()
        with self.assertRaisesRegex(ValueError,'start/final'): self.verify()

    def test_failed_preflight_cannot_be_promoted_by_pass_flags(self):
        self.completion.update(passed=True,finished_sequence=True,error=None)
        self.dump('completion.json',self.completion);self.refresh()
        with self.assertRaisesRegex(ValueError,'failed command'): self.verify()

    def test_private_file_is_rejected_even_when_hashed(self):
        self.write('unreconciled-attempts/failed/private-cache-authentication-NOT-FOR-PUBLICATION/key.json',b'{}')
        self.refresh()
        with self.assertRaisesRegex(ValueError,'private'): self.verify()


    def test_legacy_aggregate_is_recomputed_from_raw_blocks_without_tolerance(self):
        self.dump('run/completion.json',{'cases':[{'case_id':'case-a','disposition':'COMPLETE'},
                                                 {'case_id':'case-b','disposition':'COMPLETE'}]})
        for ordinal,value in enumerate([1e16,1.0,1.0,1.0]):
            case='case-a' if ordinal<2 else 'case-b'
            self.dump(f'run/cases/{case}/block-{ordinal%2}/summary.json',
                      {'measurements':{'chain_ms':{'fresh':value,'zerorun':value/2}}})
        original={'controlled_handoffs':{'complete_paired_chain_ms':{'fresh':123,'zerorun':456},
                                        'complete_pair_chain_saved_fraction':789,'complete_cases':2}}
        result=audit.wrapper_summary_expected(original,self.bundle,'3.10.12')
        self.assertEqual(result['controlled_handoffs']['complete_paired_chain_ms'],{'fresh':1e16,'zerorun':5e15})
        self.assertEqual(result['controlled_handoffs']['complete_pair_chain_saved_fraction'],0.5)
        self.assertEqual(original['controlled_handoffs']['complete_paired_chain_ms']['fresh'],123)
        self.assertEqual(result['controlled_handoffs']['complete_cases'],2)

    def test_current_producer_aggregate_keeps_canonical_summary(self):
        summary={'controlled_handoffs':{'complete_cases':2}}
        self.assertIs(audit.wrapper_summary_expected(summary,self.bundle,'3.14.0'),summary)


if __name__=='__main__':
    unittest.main()
