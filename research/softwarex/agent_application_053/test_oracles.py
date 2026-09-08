import copy
import subprocess
from types import SimpleNamespace

import pytest

from research.softwarex.agent_application_053 import oracles as audit


def outcome(exit_code, call, *, xfail=False):
    return {'result': {'verdict': {'exit_code': exit_code, 'nodes': {
        'tests/test_case.py::test_regression': {'setup': 'passed', 'call': call, 'teardown': 'passed', 'wasxfail': xfail}}}}}


PRODUCER = {'producer_completed': True, 'boundary_pass': True, 'source_patch_present': True}


def test_real_failed_baseline_and_same_node_success_is_verified():
    result = audit.classify(PRODUCER, outcome(1, 'failed'), outcome(0, 'passed'))
    assert result['completed_verified_fix'] is True
    assert result['classification'] == 'VERIFIED_FIX'


@pytest.mark.parametrize('baseline,final', [(None, outcome(0, 'passed')), (outcome(1, 'failed'), None),
    (outcome(0, 'passed'), outcome(0, 'passed')), (outcome(1, 'failed'), outcome(1, 'failed')),
    (outcome(2, 'failed'), outcome(0, 'passed')), (outcome(1, 'failed', xfail=True), outcome(0, 'passed')),
    (outcome(1, 'failed'), outcome(0, 'skipped')), (outcome(1, 'failed'), outcome(0, 'passed', xfail=True))])
def test_missing_failed_or_xfail_cases_are_not_verified_fixes(baseline, final):
    assert audit.classify(PRODUCER, baseline, final)['completed_verified_fix'] is False


@pytest.mark.parametrize('field', list(PRODUCER))
def test_producer_completion_boundary_and_actual_patch_are_required(field):
    producer = {**PRODUCER, field: False}
    assert audit.classify(producer, outcome(1, 'failed'), outcome(0, 'passed'))['completed_verified_fix'] is False


def test_removing_a_failed_node_cannot_repair_it():
    final = outcome(0, 'passed')
    final['result']['verdict']['nodes'] = {'replacement': next(iter(final['result']['verdict']['nodes'].values()))}
    assert audit.classify(PRODUCER, outcome(1, 'failed'), final)['completed_verified_fix'] is False


def test_empty_final_and_teardown_failure_cannot_be_promoted():
    final = outcome(0, 'passed')
    final['result']['verdict']['nodes'] = {}
    assert audit.classify(PRODUCER, outcome(1, 'failed'), final)['final_fresh_pass'] is False
    final = outcome(0, 'passed')
    next(iter(final['result']['verdict']['nodes'].values()))['teardown'] = 'failed'
    assert audit.classify(PRODUCER, outcome(1, 'failed'), final)['final_fresh_pass'] is False


def test_raw_command_logger_preserves_arguments_return_and_full_streams(tmp_path):
    calls = []
    answer = subprocess.CompletedProcess(['docker', 'start'], 1, stdout='x'*5000, stderr='retained failure')
    def original(root, argv, *, timeout_seconds):
        calls.append((root, argv, timeout_seconds)); return answer
    folder = tmp_path/'records'; invoke = audit.log_plain_calls(SimpleNamespace(_plain_run=original), folder)
    argv = ['docker', 'start', '--attach', 'exact-container']
    assert invoke(tmp_path, argv, timeout_seconds=120) is answer
    assert calls == [(tmp_path, argv, 120)]
    record = audit.read(folder/'000.json')
    assert record['returncode'] == 1 and record['argv'] == argv
    assert (folder/'000.stdout.log').read_text() == 'x'*5000
    assert (folder/'000.stderr.log').read_text() == 'retained failure'


def test_raw_command_logger_preserves_timeout_failure(tmp_path):
    def original(root, argv, *, timeout_seconds):
        raise subprocess.TimeoutExpired(argv, timeout_seconds, output=b'partial output', stderr=b'timeout detail')
    folder = tmp_path/'records'; invoke = audit.log_plain_calls(SimpleNamespace(_plain_run=original), folder)
    with pytest.raises(subprocess.TimeoutExpired):
        invoke(tmp_path, ['docker', 'start'], timeout_seconds=120)
    record = audit.read(folder/'000.json')
    assert record['returncode'] is None and record['error']['type'] == 'TimeoutExpired'
    assert (folder/'000.stdout.log').read_bytes() == b'partial output'
    assert (folder/'000.stderr.log').read_bytes() == b'timeout detail'
