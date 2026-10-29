"""Clean wheel installation with an explicit elapsed-cost and byte ledger."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    report = {'schema': 'zerorun.q1q2.installation.v1', 'steps': [],
              'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'scope': 'Separate clean-install check; not substituted for the source-deployed main timing sessions.'}

    def run(name, argv, cwd=ROOT):
        began = time.perf_counter()
        p = subprocess.run(list(map(str, argv)), cwd=cwd, capture_output=True, text=True, timeout=180)
        report['steps'].append({'name': name, 'argv': list(map(str, argv)), 'outer_seconds': time.perf_counter()-began,
                                'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        (out / 'installation.json').write_text(json.dumps(report, indent=2))
        p.check_returncode()
        return p.stdout

    try:
        spec = json.loads((HERE / 'INSTALL_DEPENDENCY.json').read_text())
        begin = time.perf_counter()
        data = urllib.request.urlopen(spec['url'], timeout=120).read()
        assert hashlib.sha256(data).hexdigest() == spec['digests']['sha256'] and len(data) == spec['size']
        wheelhouse = out / 'wheels'
        wheelhouse.mkdir()
        backend = wheelhouse / spec['filename']
        backend.write_bytes(data)
        report['build_backend_acquisition_seconds'] = time.perf_counter()-begin
        report['build_backend'] = spec
        env = out / 'venv'
        run('create-clean-environment', [sys.executable, '-m', 'venv', env])
        python = env / 'bin/python'
        run('install-build-backend', [python, '-m', 'pip', 'install', '--no-index', '--no-deps', backend])
        run('build-project-wheel', [python, '-c', f'import setuptools.build_meta as b; b.build_wheel({str(wheelhouse)!r})'])
        wheel, = wheelhouse.glob('zerorun-*.whl')
        report['project_wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        run('install-project-wheel', [python, '-m', 'pip', 'install', '--no-index', '--no-deps', wheel])
        code = "import hashlib,json,pathlib,zerorun; p=pathlib.Path(zerorun.__file__).parent; print(json.dumps({'path':str(p),'sha256':{x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in p.glob('*.py')}}))"
        installed = json.loads(run('verify-installed-bytes', [python, '-I', '-c', code], cwd=out))
        expected = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'src/zerorun').glob('*.py')}
        assert installed['sha256'] == expected and str(env) in installed['path']
        report['installed'] = installed
        run('installed-cli-help', [python, '-I', '-m', 'zerorun', '--help'], cwd=out)
        report['passed'] = True
    except Exception as error:
        report.update(passed=False, error=repr(error))
        raise
    finally:
        report['outer_seconds'] = time.perf_counter()-start
        (out / 'installation.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'passed': report['passed'], 'python': str(python), 'outer_seconds': report['outer_seconds']}))


if __name__ == '__main__':
    main()
