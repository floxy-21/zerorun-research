"""Small new-workload interface; separate from the frozen scientific campaigns."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from research.q1q2 import qualification_gate as gate, run_main
from research.q1q2.run_checked import retain_completed
from research.q1q2.prepare_runtime import prepare
from research.q1q2.run_pilot import save, utc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image', help='Existing immutable Docker image reference; omit to build the locked runtime')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    original = run_main.invoke
    run_main.invoke = lambda *a, **kw: retain_completed(original(*a, **kw))
    report = {'schema': 'zerorun.q1q2.cases.v1', 'started_utc': utc(), 'status': 'begun', 'cases': [],
              'case_ledger_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
              'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'scope': 'New-workload functional demonstration; one fixed-order block is not inferential timing evidence.'}
    try:
        image = args.image or prepare(output / 'runtime', main=True)
        if '@sha256:' not in image:
            raise ValueError('immutable image digest required')
        report['image'] = image
        for case in json.loads(args.cases.read_text())['cases']:
            name = case['id']
            if Path(name).name != name or name in ('.', '..'):
                raise ValueError('invalid case id')
            folder = output / name
            folder.mkdir()
            row = {'id': name, 'status': 'begun', 'command': case['command']}
            report['cases'].append(row)
            try:
                begin = time.perf_counter()
                source = (args.cases.resolve().parent / case['source']).resolve()
                inventory = gate.inventory(source)
                row['source_identity'] = hashlib.sha256(gate.canonical(inventory)).hexdigest()
                row['inventory'] = inventory
                target = folder / 'source'
                shutil.copytree(source, target)
                row['source_preparation_seconds'] = time.perf_counter() - begin
                pf = run_main.preflight(case, target, image, folder)
                row['preflight'] = pf
                if not pf['equal_nonempty_success']:
                    row['status'] = 'preflight_refused'
                    continue
                blockdir = folder / 'block-0'
                blockdir.mkdir()
                row['block'] = run_main.timed_block(case, target, image, blockdir,
                    {'target': name, 'block': 0, 'session': 0, 'order': ['fresh', 'receipt', 'zerorun']})
                row['status'] = row['block']['status']
                save(blockdir / 'record.json', row['block'])
            except gate.QualificationRequired as error:
                row.update(status='source_refused', error=str(error))
            except Exception as error:
                row.update(status='technical_failure', error=repr(error), traceback=traceback.format_exc())
            finally:
                save(folder / 'case.json', row)
                save(output / 'summary.json', report)
        report['status'] = 'completed' if all(r['status'] == 'completed' for r in report['cases']) else 'completed_with_refusals_or_failures'
    except Exception as error:
        report.update(status='failed', error=repr(error), traceback=traceback.format_exc())
    finally:
        report.update(completed_utc=utc(), outer_seconds=time.perf_counter() - started)
        save(output / 'summary.json', report)
    return int(report['status'] != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
