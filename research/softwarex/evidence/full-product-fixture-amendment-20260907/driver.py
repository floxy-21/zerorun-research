"""Verify a version-only test-fixture correction before committing it."""
from pathlib import Path
import importlib.util
import json
import shutil
import subprocess
import tempfile

root = Path('/home/floxy/zerorun-five-hour-20260907')
spec = importlib.util.spec_from_file_location('full_driver', root / 'full_product_vm-v2.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)
output = root / 'full-product-fixture-amendment-20260907'
assert not output.exists()
output.mkdir()
temporary = Path(tempfile.mkdtemp(prefix='zerorun-fixture-amendment-'))
checkout = temporary / 'checkout'
env = d.environment()
calls = []
record = {'schema': 'zerorun.version-fixture-amendment.v1', 'base_commit': d.COMMIT,
          'started_utc': d.utc(), 'commands': calls, 'passed': False,
          'failure': None, 'test_assertions_changed': False,
          'product_runtime_changed': False, 'fixture_literals_changed': 17,
          'driver_sha256': d.sha(Path(__file__).read_bytes()),
          'support_driver_sha256': d.sha((root / 'full_product_vm-v2.py').read_bytes())}
before = None
try:
    row, raw = d.invoke(output, calls, 'git_clone', ['git', '-c', 'credential.helper=',
        'clone', '--template=', '--depth=1', 'file://' + str(root / 'private-git-transport-0e59813/.git'),
        str(checkout)], temporary, env)
    d.require(row['returncode'] == 0, 'fresh clone failed')
    head = subprocess.check_output(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], env=env).decode().strip()
    d.require(head == d.COMMIT, 'base commit mismatch')
    original = d.inventory(checkout, env)
    d.save(output / 'source-original.json', original)
    target = checkout / 'tests/test_release_prerequisites.py'
    old = target.read_bytes()
    new = (root / 'test_release_prerequisites-0.5.2.py').read_bytes()
    d.require(old.count(b'0.5.1') == 17 and old.replace(b'0.5.1', b'0.5.2') == new,
              'amendment is not exactly the 17 version-literal corrections')
    target.write_bytes(new)
    (output / 'test_fixture_before.py').write_bytes(old)
    (output / 'test_fixture_after.py').write_bytes(new)
    record['amendment'] = {'path': 'tests/test_release_prerequisites.py',
                           'before_sha256': d.sha(old), 'after_sha256': d.sha(new),
                           'reason': '0.5.1 fixture identities conflict with unchanged validator and current 0.5.2 project version'}
    before = d.inventory(checkout, env)
    d.require([a['path'] for a, b in zip(original, before) if a != b] == ['tests/test_release_prerequisites.py'],
              'unexpected source change')
    d.save(output / 'source-before-test.json', before)
    test_env = dict(env, PYTHONPATH=str(checkout), ZERORUN_TRUST_ROOT=str(temporary / 'unit-authority'))
    launcher = "import pathlib,sys;root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root));import zerorun,pytest;assert pathlib.Path(zerorun.__file__).resolve()==root/'zerorun/__init__.py';raise SystemExit(pytest.main(sys.argv[2:]))"
    row, raw = d.invoke(output, calls, 'affected_module', [d.PYTHON, '-B', '-c', launcher,
        str(checkout), 'tests/test_release_prerequisites.py', '-q', '--tb=short', '--durations=10',
        '-p', 'no:cacheprovider', '--basetemp=' + str(temporary / 'pytest'),
        '--junitxml=' + str(output / 'tests.xml')], checkout, test_env, timeout=240)
    d.require(row['returncode'] == 0 and row['error'] is None, 'affected module failed')
    record['junit_counts'] = d.junit_summary((output / 'tests.xml').read_bytes(), raw, ['test_release_prerequisites'])
except Exception as exc:
    record['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
finally:
    if before is not None:
        after = d.inventory(checkout, env)
        d.save(output / 'source-after-test.json', after)
        record['tracked_source_unchanged_during_tests'] = before == after
    record['passed'] = record['failure'] is None and record.get('tracked_source_unchanged_during_tests') is True
    record['completed_utc'] = d.utc()
    d.save(output / 'receipt.json', record)
    print(json.dumps(record))
