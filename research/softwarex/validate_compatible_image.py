"""Offline audit of the retained Python 3.10 compatibility image build.

This checks recorded execution and exact input bytes; it does not execute Docker,
claim a clean operating system, or imply public availability of a loopback image.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import email
import io
import json
import math
from pathlib import Path
import re
import zipfile

from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_image_v2 import build_image as b

ARCHIVE_SHA = 'fe380e2da0a0c9af73033c09f7e92abc40480e3ba79904e820e3534d4fbe1b55'
INVENTORY_SHA = '14983e2184a95da8b81bc3d7500a829c13653b381ed6e5e5acb32ee427f12910'
COMPLETION_SHA = 'ffce2613a7b7305df307b3b811c6b8ed6c8f818af7cf0addbd4534bd34f75045'
DRIVER_SHA = '79336878af3e8dde198faf55a1de9dbed6a3cbd96e0663c37344a090384f2f80'
AMENDMENT_SHA = 'ba46f5278dfffe5b7441a8c1c22519d1286b3c0ffecf14786a55ecc5999a448e'
BASE = 'python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a'
IMAGE = '127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465'
LABELS = ('base-pull', 'base-tag-inspect', 'base-pinned-inspect', 'download-wheels',
          'offline-build', 'import-probe', 'pip-check', 'registry-inspect',
          'registry-create', 'registry-start', 'push', 'pull-digest',
          'derived-inspect', 'registry-remove')
LIMIT = 12 * 1024 * 1024


def read(path):
    return h.strict(h.ordinary(path, LIMIT))


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    h.require(result.tzinfo is not None, 'timestamp lacks timezone')
    return result


def exact_inventory(directory):
    directory = Path(directory)
    h.require(directory.is_dir() and not directory.is_symlink(), 'ordinary image record directory required')
    rows = []
    for path in directory.rglob('*'):
        h.require(not path.is_symlink() and not getattr(path.lstat(), 'st_file_attributes', 0) & 0x400,
                  'linked or reparse image member refused')
        if path.is_file():
            raw = h.ordinary(path, LIMIT)
            rows.append({'path': path.relative_to(directory).as_posix(), 'bytes': len(raw), 'sha256': h.sha(raw)})
    rows.sort(key=lambda row: row['path'])
    identity = h.sha(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode())
    h.require(len(rows) == 52 and identity == INVENTORY_SHA, 'compatible image inventory differs from independently pinned recovery')
    return rows


def check_commands(directory):
    """Bind each actual command to its start record and reject partial streams."""
    directory = Path(directory)
    expected = {label + suffix for label in LABELS for suffix in ('.json', '.started.json')}
    h.require({p.name for p in directory.iterdir()} == expected, 'image command inventory differs')
    rows, previous = {}, None
    for label in LABELS:
        row, start = read(directory/(label+'.json')), read(directory/(label+'.started.json'))
        h.require(set(start) == {'argv', 'started_utc'} and start['argv'] == row['argv'],
                  'image command start/final argv differ')
        h.require(type(row['returncode']) is int and row['returncode'] == 0 and row['error'] is None,
                  'image command did not succeed')
        h.require(row['stdout_truncated'] is False and row['stderr_truncated'] is False
                  and isinstance(row['stdout'], str) and isinstance(row['stderr'], str), 'incomplete image command streams')
        h.require(type(row['outer_ms']) in (int, float) and math.isfinite(row['outer_ms']) and row['outer_ms'] >= 0,
                  'invalid image command elapsed time')
        h.require(type(row['timeout_seconds']) is int and 0 < row['timeout_seconds'] <= 600,
                  'image command timeout differs')
        began, ended = instant(start['started_utc']), instant(row['completed_utc'])
        h.require(began <= ended and (previous is None or previous <= began), 'image command sequence or wall time differs')
        h.require(abs((ended-began).total_seconds() - row['outer_ms']/1000) <= 2,
                  'image command wall/monotonic duration differs')
        previous = ended
        rows[label] = {**row, 'started_utc': start['started_utc']}
    return rows


def check_wheels(context, locked):
    """Rehash all wheels, read their metadata, and reconstruct the entire lock."""
    context = Path(context)
    rows = []
    for path in sorted(context.rglob('*'), key=lambda item: item.relative_to(context).as_posix()):
        h.require(not path.is_symlink(), 'linked context member refused')
        if path.is_file():
            raw = h.ordinary(path, LIMIT)
            rows.append({'path': path.relative_to(context).as_posix(), 'bytes': len(raw), 'sha256': h.sha(raw)})
    h.require(rows == locked['context_files'], 'context no longer agrees with pre-build hashes')
    packages = []
    for path in sorted((context/'wheelhouse').iterdir(), key=lambda item: item.name):
        h.require(path.is_file() and path.suffix == '.whl', 'non-wheel dependency input')
        raw = h.ordinary(path, LIMIT)
        with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
            names = [name for name in wheel.namelist() if name.endswith('.dist-info/METADATA')]
            h.require(len(names) == 1 and wheel.getinfo(names[0]).file_size <= 1024*1024, 'ambiguous or oversized wheel metadata')
            metadata = email.message_from_bytes(wheel.read(names[0]))
        packages.append({'name': metadata['Name'], 'version': metadata['Version'],
                         'wheel': path.name, 'bytes': len(raw), 'sha256': h.sha(raw)})
    normalized = [re.sub('[-_.]+', '-', item['name']).lower() for item in packages]
    h.require(len(packages) == len(set(normalized)) == 16 and packages == locked['packages'],
              'wheel closure or metadata differs')
    expected_lock = ''.join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in packages)
    h.require(h.ordinary(context/'requirements.lock').decode() == expected_lock, 'lock omits, duplicates or changes a wheel')
    return packages


def verify(directory):
    directory = Path(directory)
    inventory = exact_inventory(directory)
    protocol, completion, locked = (read(directory/name) for name in ('protocol.json', 'completion.json', 'locked-inputs.json'))
    h.require(h.sha(h.ordinary(directory/'completion.json')) == COMPLETION_SHA, 'image completion identity differs')
    h.require(protocol['schema'] == 'zerorun.compatibility-image-candidate.v2'
              and completion['schema'] == 'zerorun.compatibility-image-candidate-completion.v2', 'unknown compatible-image schema')
    h.require(h.bound(directory, completion['protocol']) == h.ordinary(directory/'protocol.json'), 'completion protocol binding differs')
    for name, identity in [('driver', DRIVER_SHA), ('amendment', AMENDMENT_SHA)]:
        h.require(h.sha(h.bound(directory, protocol[name])) == identity, 'image source/amendment pin differs')
    h.require(protocol['build_network'] == 'none' and protocol['docker_build_cache'] is False
              and protocol['download_network'] is True and protocol['historical_environment_reproduced'] is False
              and protocol['source_or_assertion_changes'] is False, 'image preparation scope differs')
    h.require(completion['passed'] is True and completion['error'] is None
              and completion['registry_cleanup'] is True and completion['workload_preflight_passed'] is False,
              'image completion hides a failure or claims workload success')
    commands = check_commands(directory/'commands')
    packages = check_wheels(directory/'context', locked)
    build = commands['offline-build']; argv = build['argv']; tag = argv[6]
    expected_context = '/home/floxy/zerorun-compatibility-preflight-20260907-v2/image-repair-v2/context'
    h.require(re.fullmatch(r'127\.0\.0\.1:19559/zerorun-compatible-v2:[0-9a-f]{32}', tag)
              and argv == ['docker', 'build', '--no-cache', '--pull=false', '--network=none', '--tag', tag, expected_context],
              'actual image build flags or context differ')
    h.require('Using cache' not in build['stdout'] and 'Successfully built 78039048251e' in build['stdout'],
              'raw build does not confirm the recorded uncached image')
    h.require(instant(locked['recorded_before_build']) <= instant(build['started_utc']), 'wheel lock was not recorded before build')
    dockerfile = (f'FROM {BASE}\nCOPY wheelhouse/ /opt/zerorun-wheelhouse/\n'
        'COPY requirements.lock /opt/zerorun-requirements.lock\n'
        'RUN python -m pip install --disable-pip-version-check --no-cache-dir --no-index --find-links=/opt/zerorun-wheelhouse --require-hashes -r /opt/zerorun-requirements.lock\n'
        'ENV PYTHONPATH=/workspace/src:/workspace\nLABEL org.opencontainers.image.title=ZeroRun-compatible-handoff-dependencies\n')
    h.require(h.ordinary(directory/'context/Dockerfile').decode() == dockerfile, 'offline hash-locked Dockerfile differs')
    h.require(h.ordinary(directory/'context/requirements.in').decode() == protocol['requirements_in'], 'declared direct dependencies differ')
    base = b.image_metadata(commands['base-pinned-inspect']['stdout'].encode(), BASE)
    image = b.image_metadata(commands['derived-inspect']['stdout'].encode(), IMAGE)
    result = completion['result']
    h.require(base == locked['base'] == result['base'] and image == result['image'], 'image/base identity differs from raw inspection')
    h.require(commands['base-pull']['argv'] == ['docker', 'pull', '--platform', 'linux/amd64', protocol['base_acquisition_tag']]
              and commands['base-pinned-inspect']['argv'] == ['docker', 'image', 'inspect', BASE]
              and commands['derived-inspect']['argv'] == ['docker', 'image', 'inspect', IMAGE], 'image inspection or acquisition target differs')
    h.require(commands['push']['argv'] == ['docker', 'push', tag]
              and ('digest: '+IMAGE.split('@')[1]) in commands['push']['stdout']
              and commands['pull-digest']['argv'] == ['docker', 'pull', '--platform', 'linux/amd64', IMAGE], 'push/pull digest binding differs')
    probe = commands['import-probe']
    probe_code = "import importlib.metadata as m,json,sys; print(json.dumps({'python':sys.version,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))"
    h.require(probe['argv'] == ['docker', 'run', '--rm', '--pull=never', '--read-only', '--network=none',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', tag, 'python', '-B', '-c', probe_code], 'runtime probe target or isolation differs')
    runtime = h.strict(probe['stdout'].encode())
    h.require(runtime == result['runtime_probe'] and runtime['python'].startswith('3.10.21 '), 'runtime probe identity differs')
    h.require(all(runtime['packages'].get(p['name']) == p['version'] for p in packages)
              and packages == result['packages'], 'installed package versions differ from locked wheels')
    pip = commands['pip-check']
    h.require(pip['argv'] == ['docker', 'run', '--rm', '--pull=never', '--read-only', '--network=none', tag, 'python', '-B', '-m', 'pip', 'check']
              and pip['stdout'].strip() == 'No broken requirements found.', 'recorded dependency check differs')
    registry_id = commands['registry-create']['stdout'].strip()
    h.require(re.fullmatch('[0-9a-f]{64}', registry_id)
              and commands['registry-remove']['argv'] == ['docker', 'container', 'rm', '--force', registry_id], 'registry cleanup target differs')
    h.require(all(result[key] is True for key in ('context_unchanged', 'offline_hash_locked_build', 'build_cache_disabled'))
              and result['image_publicly_pullable'] is False, 'image scope summary differs')
    setup = completion['setup_outer_seconds']
    h.require(type(setup) in (int, float) and math.isfinite(setup) and setup >= sum(row['outer_ms'] for row in commands.values())/1000,
              'outer setup excludes recorded command time')
    wall = (instant(completion['completed_utc']) - instant(protocol['started_utc'])).total_seconds()
    h.require(abs(wall-setup) <= 2, 'setup wall/monotonic duration differs')
    return {'schema': 'zerorun.compatible-image-reconciliation.v1', 'state': 'RECONCILED_RECORDED_IMAGE_BUILD',
        'image_build_commands_independently_rechecked': True, 'completion_sha256': COMPLETION_SHA,
        'recovered_archive_sha256': ARCHIVE_SHA, 'inventory_sha256': INVENTORY_SHA,
        'files': len(inventory), 'bound_bytes': sum(row['bytes'] for row in inventory),
        'commands': len(commands), 'wheel_count': len(packages), 'packages': packages, 'image': image, 'base': base,
        'actual_build_argv': argv, 'docker_build_cache_disabled': True, 'build_network': 'none',
        'wheel_acquisition_used_network': True, 'wheel_bytes_and_metadata_rechecked': True,
        'runtime_probe': runtime, 'runtime_probe_kind': 'distribution metadata/version enumeration; not imports of each application module',
        'pip_check_passed': True, 'setup_outer_seconds': setup, 'offline_build_seconds': build['outer_ms']/1000,
        'base_pull_reported_up_to_date': 'Image is up to date' in commands['base-pull']['stdout'],
        'publicly_pullable': False, 'clean_operating_system': False, 'independent_human_reproduction': False,
        'workload_preflight_passed': False,
        'scope': 'Retained author-run image preparation in an existing VM and Docker daemon. Base pull reported an existing image up to date; wheel acquisition used network. The build itself disabled cache and network and used the complete retained 16-wheel hash lock. The digest belongs to a loopback registry and is not a public image distribution. This audit does not certify workload outcomes or uninterrupted benchmark timing.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2, sort_keys=True, allow_nan=False))


if __name__ == '__main__':
    main()
