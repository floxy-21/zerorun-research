"""Additive C4 validity audit; does not alter measured timing values."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def accepted(row, expected_status=None):
    payload = row.get('payload', {})
    if row.get('disposition') != 'completed' or row.get('returncode') != 0:
        return False
    if not isinstance(payload.get('exit_code'), int) or payload.get('exit_code') != 0:
        return False
    if expected_status is not None and payload.get('status') != expected_status:
        return False
    state = payload.get('container_state')
    if state is not None and (state.get('Status') != 'exited' or state.get('Running') or state.get('Error') or state.get('OOMKilled') or state.get('ExitCode') != payload['exit_code']):
        return False
    return True


def audit(folder):
    summary = json.loads((folder / 'summary.json').read_text())
    out = {'path': str(folder), 'execution_commit': summary.get('commit'), 'checked': 0, 'defects': []}
    for target in summary.get('targets', []):
        for block in target.get('blocks', []):
            for name, arm in block.get('arms', {}).items():
                q = arm.get('qualification')
                if q:
                    out['checked'] += 1
                    if not accepted(q['response'], 'FRESH_EXECUTED') or not q.get('full_verified'):
                        out['defects'].append([target['id'], block['block'], name, 'qualification'])
                for i, row in enumerate(arm.get('calls', [])):
                    out['checked'] += 1
                    wanted = 'FRESH_EXECUTED' if name == 'fresh' else 'MISS_EXECUTED' if i == 0 else 'HIT_REUSED'
                    if not accepted(row, wanted):
                        out['defects'].append([target['id'], block['block'], name, i])
    out['passed'] = not out['defects'] and out['checked'] > 0
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folders', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = [audit(p) for p in args.folders]
    args.output.write_text(json.dumps(rows, indent=2))
    raise SystemExit(not all(r['passed'] for r in rows))
