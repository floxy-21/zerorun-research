"""Adversarial checks for the offline compatible-image evidence audit."""
import copy
import json
from pathlib import Path
import shutil

import pytest

from research.softwarex import validate_compatible_image as audit


SOURCE = Path(__file__).resolve().parents[1]/'evidence/compatible-image-repair-v2'


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


def test_actual_image_reconciles_without_claiming_clean_os_or_workload_success():
    result = audit.verify(SOURCE)
    assert result['files'] == 52 and result['wheel_count'] == 16 and result['commands'] == 14
    assert result['image_build_commands_independently_rechecked'] is True
    assert result['wheel_bytes_and_metadata_rechecked'] is True
    assert result['docker_build_cache_disabled'] is True and result['build_network'] == 'none'
    assert result['completion_sha256'] == audit.COMPLETION_SHA
    assert result['image']['requested'] == audit.IMAGE
    assert result['base_pull_reported_up_to_date'] is True
    assert result['publicly_pullable'] is False
    assert result['clean_operating_system'] is False
    assert result['workload_preflight_passed'] is False


@pytest.fixture
def command_copy(tmp_path):
    return Path(shutil.copytree(SOURCE/'commands', tmp_path/'commands'))


@pytest.mark.parametrize('key,value', [('returncode', 1), ('returncode', False),
    ('stdout_truncated', True), ('stderr_truncated', True), ('error', {'message': 'failed'}),
    ('outer_ms', -1), ('outer_ms', float('nan')), ('timeout_seconds', 601)])
def test_failed_partial_or_invalid_commands_never_become_success(command_copy, key, value):
    path = command_copy/'offline-build.json'
    row = audit.read(path); row[key] = value; save(path, row)
    with pytest.raises(ValueError):
        audit.check_commands(command_copy)


def test_recorded_start_cannot_disagree_with_build_argv(command_copy):
    path = command_copy/'offline-build.started.json'
    row = audit.read(path); row['argv'].remove('--no-cache'); save(path, row)
    with pytest.raises(ValueError, match='start/final argv'):
        audit.check_commands(command_copy)


def test_omitted_command_is_rejected(command_copy):
    (command_copy/'pip-check.json').unlink()
    with pytest.raises(ValueError, match='command inventory'):
        audit.check_commands(command_copy)


def test_reordered_or_clock_inconsistent_execution_is_rejected(command_copy):
    path = command_copy/'offline-build.started.json'
    row = audit.read(path); row['started_utc'] = '2026-09-07T14:30:00+00:00'; save(path, row)
    with pytest.raises(ValueError, match='sequence or wall time'):
        audit.check_commands(command_copy)


def test_wheel_closure_cannot_omit_a_retained_wheel():
    locked = copy.deepcopy(audit.read(SOURCE/'locked-inputs.json'))
    locked['packages'].pop()
    with pytest.raises(ValueError, match='wheel closure or metadata'):
        audit.check_wheels(SOURCE/'context', locked)


def test_claimed_wheel_hash_does_not_override_actual_bytes():
    locked = copy.deepcopy(audit.read(SOURCE/'locked-inputs.json'))
    locked['context_files'][-1]['sha256'] = '0'*64
    with pytest.raises(ValueError, match='pre-build hashes'):
        audit.check_wheels(SOURCE/'context', locked)


def test_lock_must_cover_exactly_the_rehashed_wheels(tmp_path):
    context = Path(shutil.copytree(SOURCE/'context', tmp_path/'context'))
    locked = copy.deepcopy(audit.read(SOURCE/'locked-inputs.json'))
    path = context/'requirements.lock'
    path.write_bytes(path.read_bytes().splitlines(keepends=True)[0])
    for row in locked['context_files']:
        if row['path'] == 'requirements.lock':
            row.update(bytes=path.stat().st_size, sha256=audit.h.sha(path.read_bytes()))
    with pytest.raises(ValueError, match='lock omits'):
        audit.check_wheels(context, locked)


@pytest.mark.parametrize('change', ['extra', 'modified'])
def test_pinned_recovery_rejects_extra_or_rewritten_records(tmp_path, change):
    target = Path(shutil.copytree(SOURCE, tmp_path/'records'))
    if change == 'extra':
        (target/'extra.json').write_text('{}')
    else:
        row = audit.read(target/'commands/offline-build.json')
        row['argv'].remove('--no-cache'); save(target/'commands/offline-build.json', row)
    with pytest.raises(ValueError, match='independently pinned recovery'):
        audit.verify(target)
