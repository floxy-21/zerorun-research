"""Designed live boundary challenges, separate from selected-target inference."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from research.q1q2 import reference_service as ref, qualification_gate as gate
from research.q1q2.run_pilot import initialize, invoke, save, utc
from zerorun.manifest import load_manifest
from zerorun import trust

NORMAL = '''from pathlib import Path
import json, os, stat
assert Path('input.txt').read_text() == 'A'
assert list(Path('.git').iterdir()) == []
assert list(Path('.zerorun').iterdir()) == []
print(json.dumps({'file_mtime':Path('input.txt').stat().st_mtime_ns,
 'root_mtime':Path('.').stat().st_mtime_ns,
 'root_mode':stat.S_IMODE(Path('.').stat().st_mode),
 'file_xattrs':os.listxattr('input.txt'), 'root_xattrs':os.listxattr('.')}))
'''


def run(image, output):
    output.mkdir(parents=True, exist_ok=False)
    result = {'started_utc': utc(), 'image': image, 'rows': [], 'fixtures': {'normal': NORMAL}}
    def checked(name, operation):
        row = {'name': name, 'started_utc': utc()}
        result['rows'].append(row)
        try:
            row['evidence'] = operation()
            row['passed'] = True
        except Exception as error:
            row.update(passed=False, error=repr(error), traceback=traceback.format_exc())
        save(output / 'matrix.json', result)

    with tempfile.TemporaryDirectory(prefix='q1q2-matrix-') as temporary:
        base = Path(temporary)
        counter = 0
        def setup(arm, code=NORMAL):
            nonlocal counter
            counter += 1
            source = base / f'fixture-{counter}'
            source.mkdir()
            (source / 'check.py').write_text(code)
            (source / 'input.txt').write_text('A')
            os.setxattr(source / 'input.txt', 'user.q1q2', b'file-metadata')
            os.setxattr(source, 'user.q1q2', b'root-metadata')
            root, authority = base / f'root-{counter}', base / f'authority-{counter}'
            initialize(source, root, image, arm, authority, ['python', '-B', 'check.py'])
            return root, authority

        def metadata():
            records, observations = {}, {}
            for arm in ('fresh', 'receipt', 'zerorun'):
                root, authority = setup(arm)
                response = invoke(root, arm, authority)
                records[arm] = response
                p = response['payload']
                assert p['exit_code'] == 0, response
                observations[arm] = json.loads(p['stdout_tail'].strip())
            for arm in ('receipt', 'zerorun'):
                assert observations[arm]['file_mtime'] == 0 and observations[arm]['root_mtime'] == 0
                assert observations[arm]['file_xattrs'] == [] and observations[arm]['root_xattrs'] == []
            assert observations['receipt'] == observations['zerorun']
            assert observations['fresh']['file_mtime'] > 0
            return {'responses': records, 'observations': observations,
                    'interpretation': 'Staged metadata matches; direct metadata differs, so metadata-dependent tasks need qualification.'}
        checked('normalized_metadata_and_empty_control_masks', metadata)

        def transitions():
            records = {}
            for arm in ('receipt', 'zerorun'):
                root, authority = setup(arm)
                rows = [invoke(root, arm, authority), invoke(root, arm, authority)]
                assert [r['payload']['status'] for r in rows] == ['MISS_EXECUTED', 'HIT_REUSED']
                assert rows[1]['payload'].get('stdout_tail', '') == ''
                original = (root / 'input.txt').stat()
                (root / 'input.txt').write_text('B')
                os.utime(root / 'input.txt', ns=(original.st_atime_ns, original.st_mtime_ns))
                rows.append(invoke(root, arm, authority))
                assert rows[-1]['disposition'] == 'request_error' and 'QualificationRequired' in rows[-1]['error']
                (root / 'input.txt').write_text('A')
                rows.append(invoke(root, arm, authority))
                assert rows[-1]['payload']['status'] == 'HIT_REUSED'
                mode = root.stat().st_mode & 0o777
                root.chmod(0o700)
                rows.append(invoke(root, arm, authority))
                assert rows[-1]['disposition'] == 'request_error'
                root.chmod(mode)
                rows.append(invoke(root, arm, authority, verify=True))
                expected = 'FRESH_EXECUTED' if arm == 'receipt' else 'VERIFY_MATCH'
                assert rows[-1]['payload']['status'] == expected and rows[-1]['payload']['exit_code'] == 0
                records[arm] = rows
            return records
        checked('change_restoration_root_mode_and_distinct_bypass_semantics', transitions)

        def failure():
            records = {}
            for arm in ('fresh', 'receipt', 'zerorun'):
                root, authority = setup(arm, "print('failed task executed'); raise SystemExit(3)\n")
                rows = [invoke(root, arm, authority), invoke(root, arm, authority)]
                assert all(r['payload']['exit_code'] == 3 and r['payload']['status'] != 'HIT_REUSED' for r in rows)
                records[arm] = rows
            return records
        checked('nonzero_outcomes_execute_again_and_never_become_success', failure)

        def tamper():
            root, authority = setup('receipt')
            first = invoke(root, 'receipt', authority)
            assert first['payload']['status'] == 'MISS_EXECUTED'
            receipt, = (root / '.zerorun/reference').glob('*.json')
            data = json.loads(receipt.read_text())
            data['hmac'] = '0' * 64
            receipt.write_text(json.dumps(data))
            after = invoke(root, 'receipt', authority)
            assert after['payload']['status'] == 'MISS_EXECUTED' and after['payload']['exit_code'] == 0
            return {'first': first, 'tampered_receipt_response': after}
        checked('tampered_receipt_requires_execution', tamper)

        def metadata_counterexample():
            rows = {}
            for arm in ('fresh', 'receipt', 'zerorun'):
                root, authority = setup(arm, "from pathlib import Path\nassert Path('input.txt').stat().st_mtime_ns > 0\n")
                rows[arm] = invoke(root, arm, authority)
            assert rows['fresh']['payload']['exit_code'] == 0
            assert rows['receipt']['payload']['exit_code'] != 0 and rows['zerorun']['payload']['exit_code'] != 0
            return {'responses': rows, 'qualification_disposition': 'refused: direct/staged outcomes differ'}
        checked('timestamp_sensitive_task_is_a_qualification_counterexample', metadata_counterexample)

        def omitted_input():
            code = "from pathlib import Path\nassert Path('input.txt').read_text() == 'A'\n"
            root, authority = setup('zerorun', code)
            direct = ref.fresh(root, json.loads((root / '.zerorun.json').read_text())['tasks']['tests'])
            manifest = json.loads((root / '.zerorun.json').read_text())
            manifest['tasks']['tests']['inputs'] = ['check.py']
            save(root / '.zerorun.json', manifest)
            os.environ['ZERORUN_TRUST_ROOT'] = str(authority / 'native')
            parsed = load_manifest(root / '.zerorun.json')
            trust.authorize_manifest(parsed, expected_manifest_sha256=trust.manifest_sha256(parsed))
            key, _ = gate.identity(root, root / '.zerorun.json')
            gate.qualify(root, root / '.zerorun.json', authority / 'scope.json', expected_identity=key,
                         rationale='Designed negative closure challenge; no production qualification inferred.')
            staged = invoke(root, 'zerorun', authority)
            assert direct['exit_code'] == 0 and staged['payload']['exit_code'] != 0
            return {'direct': direct, 'staged': staged, 'qualification_disposition': 'refused: omitted dependency changes outcome'}
        checked('omitted_dependency_is_detected_by_full_tree_oracle', omitted_input)

        def infrastructure():
            records = []
            real_docker = shutil.which('docker')
            original_path = os.environ['PATH']
            for mode in ('allocated_create_error', 'false_attach_success', 'timeout'):
                root, authority = setup('receipt', 'import time\ntime.sleep(20)\n' if mode == 'timeout' else "print('normal')\n")
                task = json.loads((root / '.zerorun.json').read_text())['tasks']['tests']
                before = subprocess.check_output([real_docker, 'ps', '-aq', '--filter', 'name=q1q2-reference-'], text=True).split()
                wrapper = base / ('wrapper-' + mode)
                wrapper.mkdir()
                program = '#!' + sys.executable + '\nimport subprocess,sys\n'
                if mode == 'false_attach_success':
                    program += "if sys.argv[1] == 'start': sys.exit(0)\n"
                program += 'p=subprocess.run([' + repr(real_docker) + ',*sys.argv[1:]])\n'
                if mode == 'allocated_create_error':
                    program += "if sys.argv[1] == 'create' and p.returncode == 0: sys.exit(77)\n"
                program += 'sys.exit(p.returncode)\n'
                (wrapper / 'docker').write_text(program)
                (wrapper / 'docker').chmod(0o755)
                error = None
                try:
                    os.environ['PATH'] = str(wrapper) + os.pathsep + original_path
                    ref.fresh(root, task, timeout=0.2 if mode == 'timeout' else 30)
                except Exception as e:
                    error = repr(e)
                finally:
                    os.environ['PATH'] = original_path
                after = subprocess.check_output([real_docker, 'ps', '-aq', '--filter', 'name=q1q2-reference-'], text=True).split()
                assert error and before == after, (mode, error, before, after)
                assert not list((root / '.zerorun').rglob('*.json'))
                records.append({'mode': mode, 'error': error, 'containers_before': before, 'containers_after': after})
            return records
        checked('uncertain_creation_false_attach_and_timeout_are_cleaned', infrastructure)

        def bounded_output():
            root, authority = setup('receipt', "import sys\nsys.stdout.write('x'*2000000)\nsys.stderr.write('y'*2000000)\n")
            response = invoke(root, 'receipt', authority)
            p = response['payload']
            assert p['exit_code'] == 0 and p['output_truncated']
            assert p['output_bytes'] == {'stdout': 2000000, 'stderr': 2000000}
            assert len(p['stdout_tail']) == 4000 and len(p['stderr_tail']) == 4000
            return response
        checked('bounded_streams_preserve_completed_exit', bounded_output)
    result['passed'] = all(row['passed'] for row in result['rows'])
    result['completed_utc'] = utc()
    save(output / 'matrix.json', result)
    return result


if __name__ == '__main__':
    raise SystemExit(not run(sys.argv[1], Path(sys.argv[2]))['passed'])
