"""B3: one standardized replay per frozen workflow-run snapshot."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from research.q1q2.run_pilot import acquire, invoke, extract_outcomes, save, utc, monitor
from research.q1q2.run_main import oracle
from research.q1q2.prepare_runtime import prepare
from research.q1q2 import qualification_gate as gate
from zerorun import trust
from zerorun.manifest import load_manifest


def renew(source, root, authority, image, arm, command, qualified):
    started = time.perf_counter()
    root.mkdir(exist_ok=True)
    # Stable root path and stores permit restoration and cross-record reuse.
    for child in root.iterdir():
        if child.name in {'.git', '.zerorun'}:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in source.iterdir():
        if child.name in gate.EXCLUDED_ROOT:
            continue
        if child.is_dir():
            shutil.copytree(child, root / child.name)
        else:
            shutil.copy2(child, root / child.name)
    root.chmod(source.stat().st_mode & 0o777)
    if not (root / '.git').exists():
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / '.zerorun').mkdir(exist_ok=True)
    inputs = sorted(p.name for p in root.iterdir() if p.name not in gate.EXCLUDED_ROOT)
    task = {'command': command, 'inputs': inputs, 'outputs': [], 'env': [],
            'cacheable': True, 'unsafe_effects': [], 'cache_streams': False,
            'image': image, 'platform': 'linux/amd64', 'result_only': True, 'closure_reviewed': True}
    save(root / '.zerorun.json', {'version': 2, 'tasks': {'tests': task}})
    materialization = time.perf_counter() - started
    started = time.perf_counter()
    authority.mkdir(mode=0o700, exist_ok=True)
    os.environ['ZERORUN_TRUST_ROOT'] = str(authority / 'native')
    if arm == 'zerorun':
        manifest = load_manifest(root / '.zerorun.json')
        trust.authorize_manifest(manifest, expected_manifest_sha256=trust.manifest_sha256(manifest))
    if not (authority / 'receipt.key').exists() and arm == 'receipt':
        fd = os.open(authority / 'receipt.key', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(secrets.token_bytes(32))
    if qualified:
        gate.require(root, root / '.zerorun.json', authority / 'scope.json')
    else:
        key, _ = gate.identity(root, root / '.zerorun.json')
        gate.qualify(root, root / '.zerorun.json', authority / 'scope.json', expected_identity=key,
                     rationale='Candidate standardized replay under conditional outcome determinism. Admission also requires full-tree/staged outcome equality; see B3_PROTOCOL.md. Not human certification.')
    return {'materialization_seconds': materialization, 'authority_seconds': time.perf_counter() - started,
            'existing_qualified_state': qualified, 'manifest': task}


def replay_target(target, requests, image, folder):
    folder.mkdir()
    record = {'id': target['id'], 'repo': target['repo'], 'selected_records': len(requests),
              'rows': [], 'status': 'begun', 'qualified_states': {}, 'rejected_states': {}}
    qualified, rejected = record['qualified_states'], record['rejected_states']
    for index, request in enumerate(requests):
        row = {**request, 'record_index': index, 'disposition': 'begun', 'started_utc': utc()}
        record['rows'].append(row)
        item = folder / str(request['id'])
        item.mkdir()
        try:
            source = item / 'acquired'
            if not acquire({**target, 'sha': request['head_sha']}, source, item):
                acquisition = json.loads((item / 'acquisition.json').read_text())
                row.update(disposition='source_unavailable_or_unsupported', reason=acquisition.get('error'),
                           source_archive_sha256=acquisition.get('archive_sha256'))
                continue
            descriptor = {'inventory': gate.inventory(source), 'command': target['command'], 'image': image,
                          'policy': 'ordinary whole-tree, source modes, staged timestamps zero, result-only'}
            row['content_identity'] = identity = hashlib.sha256(gate.canonical(descriptor)).hexdigest()
            row['prior_qualified_state'] = identity in qualified
            full = oracle(source, image, item, target['command'])
            row['fresh'] = full
            expected = full['summary']
            if not full['full_verified'] or full['response'].get('returncode') != 0:
                row['disposition'] = 'fresh_evidence_missing_or_inconsistent'
                continue
            if expected['exit_code'] != 0:
                row['disposition'] = 'fresh_test_failure'
                if identity in qualified:
                    rejected[identity] = {'reason': 'fresh failed at previously successful state', 'record': request['id']}
                    row['contradicts_prior_success'] = True
                continue
            if identity in rejected:
                row.update(disposition='prior_qualification_refused', refusal=rejected[identity])
                continue
            if identity in qualified and qualified[identity]['outcomes'] != expected:
                rejected[identity] = {'reason': 'fresh node/phase digest changed for equal identity', 'record': request['id']}
                row['disposition'] = 'fresh_outcome_instability'
                continue
            arms = row['arms'] = {}
            for arm in ('receipt', 'zerorun'):
                root, authority = folder / (arm + '-root'), folder / (arm + '-authority')
                setup = renew(source, root, authority, image, arm, target['command'], identity in qualified)
                response = invoke(root, arm, authority)
                arms[arm] = {'setup': setup, 'response': response,
                             'summary': extract_outcomes(response.get('payload', {}))}
            wanted = 'HIT_REUSED' if identity in qualified else 'MISS_EXECUTED'
            valid = all(a['response'].get('payload', {}).get('status') == wanted
                        and a['response'].get('disposition') == 'completed'
                        and a['response'].get('returncode') == 0
                        and a['response'].get('payload', {}).get('exit_code') == 0
                        and (wanted == 'HIT_REUSED' or a['summary'] == expected) for a in arms.values())
            if not valid:
                rejected[identity] = {'reason': 'staged outcome/status disagreed with fresh oracle', 'record': request['id']}
                row['disposition'] = 'qualification_or_reuse_mismatch'
                # The outer deployment records the refusal and never serves a
                # subsequent hit from this candidate state, even if a store exists.
                continue
            if identity not in qualified:
                qualified[identity] = {'record': request['id'], 'outcomes': expected}
                row['disposition'] = 'successful_qualified_miss'
            else:
                row['disposition'] = 'eligible_actual_hit'
                row['producer_record'] = qualified[identity]['record']
        except Exception as error:
            row.update(disposition='technical_failure', error=repr(error), traceback=traceback.format_exc())
        finally:
            row['completed_utc'] = utc()
            save(item / 'record.json', row)
            save(folder / 'summary.json', record)
            print(json.dumps({'project': target['id'], 'record': request['id'], 'disposition': row['disposition']}), flush=True)
    record['status'] = 'completed'
    save(folder / 'summary.json', record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    report = {'schema': 'zerorun.q1q2.B3.v1', 'started_utc': utc(), 'status': 'begun', 'targets': [],
              'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'host': platform.uname()._asdict(),
              'sampling_unit': 'latest snapshot per workflow-run ID; imposed record-creation order; intermediate attempts unknown'}
    here = Path(__file__).parent
    freeze = json.loads((here / 'B3_FREEZE.json').read_text())
    for name, digest in freeze['sha256'].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise ValueError('B3 frozen input changed: ' + name)
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(output / 'host.jsonl', stop), daemon=True)
    thread.start()
    try:
        image = prepare(output / 'runtime', main=True)
        report['image'] = image
        requests = {r['repo']: r for r in json.loads((here / 'MAIN_REQUESTS.json').read_text())}
        for target in json.loads((here / 'MAIN_SELECTION.json').read_text())['targets']:
            records = requests[target['repo']]['requests']
            assert records == sorted(records, key=lambda r: (r['created_at'], r['id']))
            report['targets'].append(replay_target(target, records, image, output / target['id']))
            save(output / 'summary.json', report)
        report['status'] = 'completed'
    except Exception as error:
        report.update(status='failed', error=repr(error), traceback=traceback.format_exc())
    finally:
        stop.set()
        thread.join(timeout=5)
        report.update(completed_utc=utc(), total_outer_seconds=time.perf_counter() - started)
        save(output / 'summary.json', report)
    return int(report['status'] != 'completed')


if __name__ == '__main__':
    raise SystemExit(main())
