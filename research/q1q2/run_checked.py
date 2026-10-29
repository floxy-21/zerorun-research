"""Validated entry point for future B1/B2 reproduction.

The frozen run_main/run_pilot files remain byte-for-byte available. This entry
point adds the C4 response check before the frozen runner can inspect a payload.
Use audit_responses.py and audit_evidence.py on historical and new outputs.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from research.q1q2 import run_main


def retain_completed(row):
    if row.get('disposition') != 'completed' or row.get('returncode') != 0:
        if 'payload' in row:
            row['rejected_payload'] = row.pop('payload')
        row['validation_rejection'] = 'response did not complete successfully; diagnostic payload is not admissible'
    return row


def main():
    original = run_main.invoke

    def checked(*args, **kwargs):
        return retain_completed(original(*args, **kwargs))

    run_main.invoke = checked
    return run_main.main()


if __name__ == '__main__':
    raise SystemExit(main())
