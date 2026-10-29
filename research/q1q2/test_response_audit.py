from research.q1q2.audit_responses import accepted
from research.q1q2.run_checked import retain_completed


def test_discarded_valid_looking_payload_never_counts():
    row = {'disposition': 'completed', 'returncode': 0, 'payload': {'status': 'HIT_REUSED', 'exit_code': 0}}
    assert accepted(row, 'HIT_REUSED')
    row['disposition'] = 'request_error'
    row['error'] = 'QualificationRequired: post-request source changed'
    assert not accepted(row, 'HIT_REUSED')
    row['disposition'] = 'completed'
    row['returncode'] = 1
    assert not accepted(row, 'HIT_REUSED')


def test_incoherent_completion_is_not_success():
    row = {'disposition': 'completed', 'returncode': 0, 'payload': {'status': 'FRESH_EXECUTED', 'exit_code': 0,
        'container_state': {'Status': 'exited', 'Running': False, 'Error': '', 'OOMKilled': False, 'ExitCode': 3}}}
    assert not accepted(row, 'FRESH_EXECUTED')


def test_failed_gate_payload_cannot_reach_frozen_validator():
    payload = {'status': 'HIT_REUSED', 'exit_code': 0}
    row = retain_completed({'disposition': 'request_error', 'returncode': 0, 'payload': payload})
    assert 'payload' not in row
    assert row['rejected_payload'] == payload

