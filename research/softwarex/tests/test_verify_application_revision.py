"""Artificial failed bundles exercise provenance rejection; no workload is run."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pytest

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


def dump_record(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


def file_binding(root, name):
    raw = (root/name).read_bytes()
    return {'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def clean_failed_bundle(tmp_path, monkeypatch):
    """Only the new wrapper boundary is artificial; no workload executes."""
    legacy = ApplicationRevisionAuditTests()
    legacy.setUp()
    try:
        bundle = legacy.bundle
        (bundle/'provenance/APPLICATION_REVISION_AMENDMENT.md').unlink()
        legacy.write('provenance/APPLICATION_REVISION_V3_AMENDMENT.md', legacy.amendment)
        legacy.write('provenance/build_image_no_cache_v3.py', b'Artificial no-cache adapter.\n')
        legacy.write('provenance/OUTCOME_CLASSIFICATION_V3.md', b'Artificial outcome classification.\n')
        monkeypatch.setattr(audit, 'CLEAN_ADAPTER_SHA', legacy.record('provenance/build_image_no_cache_v3.py')['sha256'])
        monkeypatch.setattr(audit, 'CLEAN_CLASSIFICATION_SHA', legacy.record('provenance/OUTCOME_CLASSIFICATION_V3.md')['sha256'])
        protocol = legacy.protocol
        protocol.update(schema='zerorun.application-revision-driver.v3',
            sequence=['fresh_image_build_no_cache', 'main_original_24_new_image'],
            image_build_outer_timeout_seconds=960, registry_port=19529,
            docker_build_cache_disabled=True, preexisting_base_layers_permitted=True,
            amendment=legacy.record('provenance/APPLICATION_REVISION_V3_AMENDMENT.md'),
            no_cache_adapter=legacy.record('provenance/build_image_no_cache_v3.py'),
            outcome_clarification=legacy.record('provenance/OUTCOME_CLASSIFICATION_V3.md'))
        for key in ('pilot_budget_seconds', 'pilot_outer_timeout_seconds'):
            protocol.pop(key)
        legacy.completion['schema'] = 'zerorun.application-revision-completion.v3'
        legacy.dump('provenance/protocol.json', protocol)
        legacy.dump('completion.json', legacy.completion)
        legacy.refresh()
        yield legacy
    finally:
        legacy.doCleanups()


def verify_clean_fixture(fixture):
    return audit.verify_clean_v3(fixture.root, fixture.bundle,
        hashlib.sha256(fixture.driver).hexdigest(), hashlib.sha256(fixture.amendment).hexdigest())


def test_clean_failed_preflight_does_not_certify_new_image_or_timing(clean_failed_bundle):
    result = verify_clean_fixture(clean_failed_bundle)
    assert result['state'] == 'RECONCILED_FAILED_ATTEMPT'
    assert result['fresh_image_main_reproduction_confirmed'] is False
    assert result['uninterrupted_timing_certified'] is False
    assert result['pooled_with_previous_attempts'] is False


@pytest.mark.parametrize('key,value', [('main_budget_seconds', 1080), ('execution_seconds', 121),
    ('registry_port', 19519), ('docker_build_cache_disabled', False),
    ('sequence', ['main_original_24_new_image', 'fresh_image_build_no_cache'])])
def test_clean_wrapper_rejects_changed_prospective_scope(clean_failed_bundle, key, value):
    fixture = clean_failed_bundle
    fixture.protocol[key] = value
    fixture.dump('provenance/protocol.json', fixture.protocol); fixture.refresh()
    with pytest.raises(ValueError):
        verify_clean_fixture(fixture)


def test_clean_self_consistent_adapter_replacement_is_rejected(clean_failed_bundle):
    fixture = clean_failed_bundle
    fixture.write('provenance/build_image_no_cache_v3.py', b'Changed artificial adapter.\n')
    fixture.protocol['no_cache_adapter'] = fixture.record('provenance/build_image_no_cache_v3.py')
    fixture.dump('provenance/protocol.json', fixture.protocol); fixture.refresh()
    with pytest.raises(ValueError, match='pinned clean no_cache_adapter'):
        verify_clean_fixture(fixture)


def test_clean_wrapper_cannot_be_sent_to_historical_verifier(clean_failed_bundle):
    with pytest.raises(ValueError, match='unknown wrapper schema'):
        clean_failed_bundle.verify()


@pytest.fixture
def no_cache_fixture(tmp_path):
    requested = ['/usr/bin/docker', 'build', '--pull=false', '--network=none', '--build-arg',
                 'SOURCE_DATE_EPOCH=0', '--tag', '127.0.0.1:19529/zerorun-handoff-v2:artificial',
                 '/new/artificial/image/context']
    actual = requested[:2] + ['--no-cache'] + requested[2:]
    adapter = {'schema': 'zerorun.docker-no-cache-invocation.v3', 'requested_argv': requested,
        'actual_argv': actual, 'adapter_file': 'provenance/build_image_no_cache_v3.py',
        'adapter_sha256': audit.CLEAN_ADAPTER_SHA, 'frozen_builder_sha256': audit.FROZEN_IMAGE_BUILDER_SHA,
        'only_added_argument': '--no-cache', 'runtime_source_modified': False}
    command = {'argv': actual, 'returncode': 0, 'error': None, 'stdout_truncated': False,
               'stderr_truncated': False, 'outer_ms': 1.0}
    dump_record(tmp_path/'protocol.json', {'port': 19529})
    dump_record(tmp_path/'commands/no-cache-adapter.json', adapter)
    dump_record(tmp_path/'commands/build.started.json', {'argv': actual})
    dump_record(tmp_path/'commands/build.json', command)
    return tmp_path, adapter, command


def test_no_cache_proof_uses_actual_command_and_does_not_require_old_digest(no_cache_fixture):
    root, _, _ = no_cache_fixture
    result = audit.no_cache_binding(root)
    assert result['actual_docker_build_no_cache'] is True
    assert result['empty_daemon_claimed'] is False
    assert result['uncached_dependency_acquisition_claimed'] is False


@pytest.mark.parametrize('mode', ['missing-flag', 'extra-argument', 'another-command', 'changed-builder', 'failed-build'])
def test_no_cache_label_cannot_hide_wrong_actual_build(no_cache_fixture, mode):
    root, adapter, command = no_cache_fixture
    if mode == 'missing-flag':
        adapter['actual_argv'] = adapter['requested_argv']
    elif mode == 'extra-argument':
        adapter['actual_argv'] = [*adapter['actual_argv'], '--network=host']
    elif mode == 'another-command':
        command['argv'] = adapter['requested_argv']
        dump_record(root/'commands/build.started.json', {'argv': command['argv']})
    elif mode == 'changed-builder':
        adapter['frozen_builder_sha256'] = '0'*64
    else:
        command['returncode'] = 1
    dump_record(root/'commands/no-cache-adapter.json', adapter)
    dump_record(root/'commands/build.json', command)
    with pytest.raises(ValueError):
        audit.no_cache_binding(root)


@pytest.fixture
def continuity_fixture(tmp_path):
    host, guest = tmp_path/'host-observation', tmp_path/'record-only'
    host.mkdir(); guest.mkdir()
    (host/'observer.py').write_bytes(b'Artificial host observer fixture.\n')
    (guest/'RECORD_MANIFEST.json').write_bytes(b'{"artificial": "guest record manifest"}')
    observer_sha = file_binding(host, 'observer.py')['sha256']
    driver_sha, amendment_sha = 'a'*64, 'b'*64
    protocol = {'schema': 'zerorun.host-continuity-protocol.v3', 'observer': file_binding(host, 'observer.py'),
        'sample_interval_seconds': 10, 'maximum_sample_start_gap_seconds': 45, 'elapsed_bracket_slack_seconds': 2,
        'expected_vm_state': 'running', 'expected_delta_uuid': 'a32b0595-4e14-4fd2-89af-d6ec6094b9a1',
        'endpoint': '127.0.0.1:2222', 'expected_host_key': 'SHA256:5rUVqDOVpFiVuEmQuKFaavxhvu1BWFt7PKtGgs9YNfY',
        'stable_guest_boot_id_required': True, 'guest_command_span_must_be_covered': True,
        'monitoring_is_part_of_recorded_environment': True, 'sample_errors_qualify_as_uninterrupted': False,
        'source_edits': False, 'other_benchmarks_permitted': False,
        'input_files': [{'path': name, 'bytes': 1, 'sha256': digest} for name, digest in {
            'revision_application_vm_v3.py': driver_sha, 'build_image_no_cache_v3.py': audit.CLEAN_ADAPTER_SHA,
            'APPLICATION_REVISION_V3_AMENDMENT.md': amendment_sha,
            'OUTCOME_CLASSIFICATION_V3.md': audit.CLEAN_CLASSIFICATION_SHA}.items()]}
    dump_record(host/'protocol.json', protocol)
    prefix = '/home/floxy/zerorun-v3-inputs-20260907/'
    dump_record(host/'launch.json', {'argv': ['python3', '-B', prefix+'revision_application_vm_v3.py',
        '--amendment', prefix+'APPLICATION_REVISION_V3_AMENDMENT.md', '--no-cache-wrapper',
        prefix+'build_image_no_cache_v3.py'], 'host_key_verified': True, 'credentials_logged': False})
    transfer = {'path': '/artificial/record-only.tar.gz', 'bytes': 1, 'sha256': 'c'*64}
    (host/'driver.stdout.log').write_text(json.dumps(transfer)+'\n', encoding='utf-8')
    (host/'driver.stderr.log').write_bytes(b'')
    origin = datetime(2000, 1, 1, tzinfo=timezone.utc)
    instant = lambda offset: (origin+timedelta(seconds=offset)).isoformat()
    command = {'started_utc': instant(5), 'completed_utc': instant(25), 'elapsed_seconds': 20}
    samples = []
    for index in range(4):
        elapsed = index*10
        samples.append({'sample_index': index, 'host_utc_start': instant(elapsed),
            'host_utc_end': instant(elapsed+.1), 'host_monotonic_start': 1000+elapsed,
            'host_monotonic_end': 1000+elapsed+.1, 'error': None, 'vm_state': 'running',
            'delta_uuid': protocol['expected_delta_uuid'], 'vm_state_change_time': instant(-100),
            'host_c_free_bytes': 1000000, 'host_d_free_bytes': 1000000,
            'guest': {'utc': instant(elapsed), 'monotonic_seconds': 2000+elapsed, 'uptime_seconds': 2000+elapsed,
                'boot_id': '11111111-1111-1111-1111-111111111111', 'image_started': True,
                'main_started': index>0, 'main_started_utc': command['started_utc'] if index>0 else None,
                'main_finished': index==3}})
    completion = {'schema': 'zerorun.host-continuity-completion.v3', 'error': None,
        'uninterrupted_claim_requires_independent_reconciliation': True, 'vm_stop_or_savestate_requested': False,
        'guest_record_manifest': file_binding(tmp_path, 'record-only/RECORD_MANIFEST.json'),
        'archive_downloaded_and_verified': True, 'host_key_verified': True, 'guest_transfer': transfer,
        'driver_exit_code': 0}
    def refresh():
        (host/'continuity.log').write_text(''.join(json.dumps(row)+'\n' for row in samples), encoding='utf-8')
        completion.update(sample_count=len(samples), sample_errors=sum(row['error'] is not None for row in samples),
            files=[file_binding(host, path.name) for path in sorted(host.iterdir()) if path.name!='completion.json'])
        dump_record(host/'completion.json', completion)
    refresh()
    def verify():
        return audit.host_continuity(host, guest, observer_sha, driver_sha, amendment_sha, command)
    return host, guest, protocol, samples, completion, refresh, verify


def test_host_continuity_requires_observations_and_binds_guest_manifest(continuity_fixture):
    _, _, _, _, _, _, verify = continuity_fixture
    result = verify()
    assert result['sampled_continuity_checks_passed'] is True
    assert result['main_command_span_covered'] is True
    assert result['sample_count'] == 4


@pytest.mark.parametrize('mode', ['pause-state', 'state-transition', 'reboot', 'guest-clock-jump',
    'guest-monotonic-pause', 'sample-error', 'missing-end-coverage', 'wrong-delta', 'long-gap',
    'missing-state-marker', 'accumulated-pause', 'accumulated-host-clock-drift'])
def test_honest_host_adversity_is_retained_without_uninterrupted_claim(continuity_fixture, mode):
    _, _, _, samples, _, refresh, verify = continuity_fixture
    if mode == 'pause-state':
        samples[1]['vm_state'] = 'paused'
    elif mode == 'state-transition':
        samples[-1]['vm_state_change_time'] = '2000-01-01T00:00:19+00:00'
    elif mode == 'reboot':
        samples[-1]['guest']['boot_id'] = '22222222-2222-2222-2222-222222222222'
    elif mode == 'guest-clock-jump':
        samples[1]['guest']['utc'] = '2000-01-01T00:00:18+00:00'
    elif mode == 'guest-monotonic-pause':
        samples[1]['guest']['monotonic_seconds'] -= 5
    elif mode == 'sample-error':
        samples[1]['error'] = {'type': 'TimeoutError', 'message': 'Artificial probe timeout.'}
    elif mode == 'missing-end-coverage':
        samples[-1]['guest']['main_finished'] = False
    elif mode == 'wrong-delta':
        samples[1]['delta_uuid'] = '33333333-3333-3333-3333-333333333333'
    elif mode == 'missing-state-marker':
        samples[1]['vm_state_change_time'] = ''
    elif mode == 'accumulated-pause':
        for index, row in enumerate(samples):
            row['guest']['monotonic_seconds'] -= index
            row['guest']['uptime_seconds'] -= index
            current = datetime.fromisoformat(row['guest']['utc'])
            row['guest']['utc'] = (current-timedelta(seconds=index)).isoformat()
    elif mode == 'accumulated-host-clock-drift':
        for index, row in enumerate(samples):
            for key in ('host_utc_start', 'host_utc_end'):
                row[key] = (datetime.fromisoformat(row[key])-timedelta(seconds=index)).isoformat()
    else:
        samples[-1]['host_monotonic_start'] += 50
        samples[-1]['host_monotonic_end'] += 50
    refresh()
    result = verify()
    assert result['state'] == 'RECONCILED_HOST_OBSERVATIONS'
    assert result['sampled_continuity_checks_passed'] is False
    assert result['uninterrupted_timing_certified'] is False
    assert result['qualification_reasons']


@pytest.mark.parametrize('mode', ['other-bundle', 'threshold-drift', 'duplicate-index', 'observer-replacement'])
def test_host_observation_identity_drift_is_rejected(continuity_fixture, mode):
    host, guest, protocol, samples, _, refresh, verify = continuity_fixture
    if mode == 'other-bundle':
        (guest/'RECORD_MANIFEST.json').write_bytes(b'Changed artificial guest manifest')
    elif mode == 'threshold-drift':
        protocol['maximum_sample_start_gap_seconds'] = 9999
        dump_record(host/'protocol.json', protocol)
    elif mode == 'duplicate-index':
        samples[1]['sample_index'] = 0
    else:
        (host/'observer.py').write_bytes(b'Changed artificial observer')
        protocol['observer'] = file_binding(host, 'observer.py')
        dump_record(host/'protocol.json', protocol)
    refresh()
    with pytest.raises(ValueError):
        verify()


@pytest.mark.parametrize('mode', ['valid', 'invalid-memory', 'reversed-time'])
def test_supplemental_memory_record_is_bound_but_never_qualifies_continuity(continuity_fixture, mode):
    host, _, _, _, _, refresh, verify = continuity_fixture
    observation = {'schema': 'zerorun-host-memory-observation-v1',
        'origin': 'direct Win32_OperatingSystem observation',
        'observed_started_utc': '2000-01-01T00:00:10+00:00',
        'observed_completed_utc': '2000-01-01T00:00:11+00:00',
        'free_physical_kib': 100, 'free_virtual_kib': 50, 'total_virtual_kib': 200}
    if mode == 'invalid-memory':
        observation['free_virtual_kib'] = 201
    elif mode == 'reversed-time':
        observation['observed_completed_utc'] = '2000-01-01T00:00:09+00:00'
    dump_record(host/'host-memory-observation.json', observation)
    refresh()
    if mode != 'valid':
        with pytest.raises(ValueError):
            verify()
    else:
        result = verify()
        assert result['sampled_continuity_checks_passed'] is True
        memory = result['supplemental_memory_observation']
        assert memory['record'] == observation
        assert memory['used_to_qualify_continuity'] is False
        assert memory['continuous_memory_monitoring'] is False
        assert memory['record_sha256'] == file_binding(host, 'host-memory-observation.json')['sha256']


def artificial_full_cohort_counts():
    return {'selected_cases': 24, 'dispositions': {'COMPLETE': 18, 'INCOMPLETE_OR_UNSUPPORTED': 6,
        'MATERIAL_CORRECTNESS_STOP': 0, 'NOT_RUN_BUDGET': 0, 'NOT_RUN_CAMPAIGN_FAILURE': 0,
        'NOT_RUN_CORRECTNESS_STOP': 0, 'UNAVAILABLE_ACQUISITION': 0}}


def test_full_attempt_gate_keeps_six_artificial_failures_as_failures():
    result = audit.full_attempt_gate(artificial_full_cohort_counts())
    assert result['all_selected_cases_attempted'] is True
    assert result['attempted_cases'] == 24
    assert result['complete_cases'] == 18
    assert result['incomplete_or_unsupported_cases'] == 6
    assert result['all_selected_cases_completed'] is False


@pytest.mark.parametrize('disposition', ['NOT_RUN_BUDGET', 'NOT_RUN_CAMPAIGN_FAILURE',
                                       'NOT_RUN_CORRECTNESS_STOP', 'UNAVAILABLE_ACQUISITION'])
def test_any_unattempted_case_prevents_full_cohort_gate(disposition):
    control = artificial_full_cohort_counts()
    control['dispositions']['COMPLETE'] -= 1
    control['dispositions'][disposition] += 1
    result = audit.full_attempt_gate(control)
    assert result['all_selected_cases_attempted'] is False
    assert result['attempted_cases'] == 23


@pytest.mark.parametrize('mode', ['missing-case', 'duplicate-case', 'changed-selection', 'boolean-count'])
def test_full_cohort_gate_rejects_inconsistent_denominator(mode):
    control = artificial_full_cohort_counts()
    if mode == 'missing-case':
        control['dispositions']['COMPLETE'] -= 1
    elif mode == 'duplicate-case':
        control['dispositions']['COMPLETE'] += 1
    elif mode == 'changed-selection':
        control['selected_cases'] = 23
    else:
        control['dispositions']['NOT_RUN_BUDGET'] = False
    with pytest.raises(ValueError, match='disposition denominator'):
        audit.full_attempt_gate(control)


@pytest.fixture
def full_failed_bundle(clean_failed_bundle, monkeypatch):
    fixture = clean_failed_bundle
    fixture.write('provenance/APPLICATION_REVISION_V4_AMENDMENT.md', fixture.amendment)
    fixture.write('provenance/prior-v3-protocol.json', b'{"artificial": "prior image build protocol"}')
    monkeypatch.setattr(audit, 'PRIOR_V3_PROTOCOL_SHA', fixture.record('provenance/prior-v3-protocol.json')['sha256'])
    protocol = fixture.protocol
    protocol.update(schema='zerorun.application-revision-driver.v4',
        sequence=['reconcile_existing_v3_no_cache_image', 'main_all_24_same_image'],
        main_budget_seconds=7200, main_outer_timeout_seconds=7560,
        completion_requires_all_selected_attempted=True, expected_image_digest=audit.PRIOR_V3_IMAGE_DIGEST,
        prior_build_docker_cache_disabled=True, docker_build_performed_in_this_attempt=False,
        amendment=fixture.record('provenance/APPLICATION_REVISION_V4_AMENDMENT.md'),
        prior_no_cache_adapter=fixture.record('provenance/build_image_no_cache_v3.py'),
        prior_v3_protocol=fixture.record('provenance/prior-v3-protocol.json'))
    fixture.completion.update(schema='zerorun.application-revision-completion.v4',
                              all_selected_cases_attempted=False, attempt_gate=None)
    fixture.dump('provenance/protocol.json', protocol)
    fixture.dump('completion.json', fixture.completion)
    fixture.refresh()
    return fixture


def verify_full_fixture(fixture):
    return audit.verify_full_v4(fixture.root, fixture.bundle,
        hashlib.sha256(fixture.driver).hexdigest(), hashlib.sha256(fixture.amendment).hexdigest())


def test_failed_full_cohort_does_not_certify_all_cases_or_new_image(full_failed_bundle):
    result = verify_full_fixture(full_failed_bundle)
    assert result['state'] == 'RECONCILED_FAILED_ATTEMPT'
    assert result['all_selected_cases_attempted'] is False
    assert result['full_cohort_reproduction_confirmed'] is False
    assert result['new_image_built_in_this_attempt'] is False
    assert result['pooled_with_previous_attempts'] is False


@pytest.mark.parametrize('key,value', [('main_budget_seconds', 1200), ('execution_seconds', 121),
    ('main_outer_timeout_seconds', 1560), ('completion_requires_all_selected_attempted', False),
    ('expected_image_digest', 'sha256:'+'0'*64), ('docker_build_performed_in_this_attempt', True)])
def test_full_cohort_wrapper_rejects_budget_gate_or_image_substitution(full_failed_bundle, key, value):
    fixture = full_failed_bundle
    fixture.protocol[key] = value
    fixture.dump('provenance/protocol.json', fixture.protocol); fixture.refresh()
    with pytest.raises(ValueError):
        verify_full_fixture(fixture)


def test_self_consistent_replacement_of_prior_image_protocol_is_rejected(full_failed_bundle):
    fixture = full_failed_bundle
    fixture.write('provenance/prior-v3-protocol.json', b'{"artificial": "replacement image protocol"}')
    fixture.protocol['prior_v3_protocol'] = fixture.record('provenance/prior-v3-protocol.json')
    fixture.dump('provenance/protocol.json', fixture.protocol); fixture.refresh()
    with pytest.raises(ValueError, match='pinned full-cohort prior_v3_protocol'):
        verify_full_fixture(fixture)


def test_full_cohort_cannot_be_promoted_by_pass_flags_over_failed_command(full_failed_bundle):
    fixture = full_failed_bundle
    fixture.completion.update(passed=True, finished_sequence=True, error=None, all_selected_cases_attempted=True)
    fixture.dump('completion.json', fixture.completion); fixture.refresh()
    with pytest.raises(ValueError, match='failed execution command'):
        verify_full_fixture(fixture)


def test_longer_full_cohort_timeout_exception_does_not_relax_historical_commands(full_failed_bundle):
    fixture = full_failed_bundle
    fixture.started['timeout_seconds'] = 7560
    fixture.row['timeout_seconds'] = 7560
    fixture.dump('commands/harness-init.started.json', fixture.started)
    fixture.dump('commands/harness-init.json', fixture.row)
    fixture.dump('completion.json', fixture.completion); fixture.refresh()
    with pytest.raises(ValueError, match='timeout outside protocol'):
        verify_full_fixture(fixture)


def test_full_host_observations_use_v4_source_and_launch_without_claiming_image_rebuild(continuity_fixture):
    host, guest, protocol, _, completion, refresh, _ = continuity_fixture
    prior_inputs = {row['path']: row for row in protocol['input_files']}
    driver_sha = prior_inputs['revision_application_vm_v3.py']['sha256']
    amendment_sha = prior_inputs['APPLICATION_REVISION_V3_AMENDMENT.md']['sha256']
    protocol.update(schema='zerorun.host-continuity-protocol.v4', input_files=[
        {'path': 'revision_application_vm_v4.py', 'bytes': 1, 'sha256': driver_sha},
        {'path': 'APPLICATION_REVISION_V4_AMENDMENT.md', 'bytes': 1, 'sha256': amendment_sha}])
    completion['schema'] = 'zerorun.host-continuity-completion.v4'
    dump_record(host/'protocol.json', protocol)
    prefix = '/home/floxy/zerorun-v4-inputs-20260907/'
    dump_record(host/'launch.json', {'argv': ['python3', '-B', prefix+'revision_application_vm_v4.py',
        '--amendment', prefix+'APPLICATION_REVISION_V4_AMENDMENT.md', '--existing-image',
        '/home/floxy/zerorun-application-revision-20260907-v3/handoff-image-build-no-cache-v1'],
        'host_key_verified': True, 'credentials_logged': False})
    refresh()
    command = {'started_utc': '2000-01-01T00:00:05+00:00',
               'completed_utc': '2000-01-01T00:00:25+00:00', 'elapsed_seconds': 20}
    observer_sha = protocol['observer']['sha256']
    result = audit.host_continuity(host, guest, observer_sha, driver_sha, amendment_sha, command, version=4)
    assert result['sampled_continuity_checks_passed'] is True
    with pytest.raises(ValueError, match='host observation schema differs'):
        audit.host_continuity(host, guest, observer_sha, driver_sha, amendment_sha, command)
