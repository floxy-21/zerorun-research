"""Post-execution evidence audit, independent of experiment admission decisions.

This checks retained bytes and dispositions, not semantic determinism. It does
not alter frozen timing estimates or discard unfavorable records.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

HERE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_text())


def response(row, status, exit_code=0):
    p = row['payload']
    assert row['disposition'] == 'completed' and row['returncode'] == 0
    assert p['status'] == status and p['exit_code'] == exit_code
    if 'container_state' in p:
        s = p['container_state']
        assert s['Status'] == 'exited' and not s['Running'] and not s['OOMKilled']
        assert not s['Error'] and s['ExitCode'] == exit_code


def summary(text, full=False):
    rows = [x.split('=', 1)[1] for x in text.splitlines() if x.startswith('Q1Q2_SUMMARY=')]
    assert len(rows) == 1
    result = json.loads(rows[0])
    if full:
        payloads = [x.split('=', 1)[1] for x in text.splitlines() if x.startswith('Q1Q2_OUTCOMES=')]
        assert len(payloads) == 1 and digest(payloads[0].encode()) == result['sha256']
        payload = json.loads(payloads[0])
        assert payload['exit_code'] == result['exit_code']
        assert len(payload['collected']) == result['collected_count'] > 0
        assert len(payload['reports']) == result['reports_count']
    return result


def oracle(row, path, success=True):
    s = summary(path.read_text(), full=True)
    assert row['summary'] == s and row['full_verified']
    assert summary(row['response']['payload']['stdout_tail']) == s
    response(row['response'], 'FRESH_EXECUTED', 0 if success else s['exit_code'])
    return s


def archive(path):
    a = read(path / 'acquisition.json')
    data = (path / 'source.tar.gz').read_bytes()
    assert digest(data) == a['archive_sha256']
    with tarfile.open(path / 'source.tar.gz') as tar:
        entries = {}
        links = []
        for t in tar:
            name = str(PurePosixPath(t.name))
            assert not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts
            assert name not in entries and name != '.git' and not name.startswith('.git/')
            e = {'path': name, 'mode': t.mode & 0o777}
            if t.isfile():
                e.update(type='file', bytes=t.size, sha256=digest(tar.extractfile(t).read()))
            elif t.isdir():
                e['type'] = 'directory'
            else:
                links.append({'path': name, 'link': t.linkname, 'type': t.type.decode()})
            entries[name] = e
    if a['status'] == 'acquired':
        expected = {x['path']: x for x in a['inventory'] if x['path'] != '.'}
        assert not links and entries == expected
    return {'sha': a['sha'], 'archive_sha256': a['archive_sha256'],
            'status': a['status'], 'links': links, 'error': a.get('error')}


def host(path):
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    gaps = [b['monotonic'] - a['monotonic'] for a, b in zip(rows, rows[1:])]
    assert gaps and min(gaps) > 0
    return {'samples': len(rows), 'max_interval_seconds': max(gaps),
            'intervals_above_2s': sum(g > 2 for g in gaps),
            'max_load1': max(r['loadavg'][0] for r in rows)}


def replay(folder):
    data = read(folder / 'summary.json')
    requests = {r['repo']: r['requests'] for r in read(HERE / 'MAIN_REQUESTS.json')}
    result = {'kind': 'B3', 'execution_commit': data['commit'], 'targets': [], 'host': host(folder / 'host.jsonl')}
    assert data['status'] == 'completed' and len(data['targets']) == 6
    for t in data['targets']:
        seen = {}
        rows = t['rows']
        assert len(rows) == len(requests[t['repo']])
        out = {'id': t['id'], 'counts': dict(Counter(r['disposition'] for r in rows)), 'hits': [], 'failures': [], 'refusals': []}
        assert t == read(folder / t['id'] / 'summary.json')
        for r, selected in zip(rows, requests[t['repo']]):
            assert all(r[k] == v for k, v in selected.items())
            p = folder / t['id'] / str(r['id'])
            assert read(p / 'record.json') == r
            a = archive(p)
            assert a['sha'] == r['head_sha']
            disposition = r['disposition']
            if disposition == 'source_unavailable_or_unsupported':
                assert a['status'] != 'acquired'
                out['refusals'].append({'id': r['id'], 'reason': a['error'], 'links': a['links']})
                continue
            acquisition = read(p / 'acquisition.json')
            descriptor = {'inventory': acquisition['inventory'], 'command': acquisition['command'],
                          'image': data['image'], 'policy': 'ordinary whole-tree, source modes, staged timestamps zero, result-only'}
            key = digest(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode())
            assert key == r['content_identity']
            expected = oracle(r['fresh'], p / 'oracle-full-stdout.txt', success=False)
            if disposition == 'fresh_test_failure':
                assert expected['exit_code'] != 0 and 'arms' not in r
                out['failures'].append({'id': r['id'], 'summary': expected})
                continue
            assert expected['exit_code'] == 0
            assert disposition in ('successful_qualified_miss', 'eligible_actual_hit'), disposition
            is_hit = disposition == 'eligible_actual_hit'
            assert is_hit == (key in seen) == r['prior_qualified_state']
            for name in ('receipt', 'zerorun'):
                arm = r['arms'][name]
                response(arm['response'], 'HIT_REUSED' if is_hit else 'MISS_EXECUTED')
                if not is_hit:
                    assert arm['summary'] == expected == summary(arm['response']['payload']['stdout_tail'])
            if is_hit:
                prior = seen[key]
                assert prior['id'] == r['producer_record'] and prior['summary'] == expected
                out['hits'].append({'id': r['id'], 'producer': prior['id'], 'head_sha': r['head_sha'],
                    'producer_sha': prior['sha'], 'same_commit': prior['sha'] == r['head_sha'],
                    'original_conclusion': r['conclusion'], 'run_attempt': r['run_attempt']})
            else:
                seen[key] = {'id': r['id'], 'sha': r['head_sha'], 'summary': expected}
        result['targets'].append(out)
    result['total_counts'] = dict(sum((Counter(t['counts']) for t in result['targets']), Counter()))
    result['passed'] = True
    return result


def session(folder):
    data = read(folder / 'summary.json')
    schedule = read(HERE / 'MAIN_SCHEDULE.json')['schedule']
    eligible = read(HERE / 'MAIN_ELIGIBILITY.json')['eligible_ids']
    assert data['status'] == 'completed' and len(data['targets']) == 6
    result = {'kind': 'B2', 'session': data['session'], 'execution_commit': data['commit'],
              'targets': [], 'host': host(folder / 'host.jsonl'), 'requests_checked': 0}
    freeze = read(HERE / 'MAIN_FREEZE.json')['sha256']
    for p, h in freeze.items():
        if Path(p).parent == Path('research/q1q2'):
            assert data['sources'][Path(p).name] == h
    for t in data['targets']:
        if t['id'] not in eligible:
            assert t['status'] == 'preflight_refused' and not t['blocks']
            continue
        assert t['status'] == 'completed' and len(t['blocks']) == 6
        p = folder / t['id']
        a = archive(p)
        assert a['sha'] == t['sha']
        wanted = [s for s in schedule if s['session'] == data['session'] and s['target'] == t['id']]
        digests = []
        for b, sch in zip(t['blocks'], wanted):
            assert all(b[k] == v for k, v in sch.items()) and b['status'] == 'completed'
            bp = p / ('block-' + str(b['block']))
            assert read(bp / 'record.json') == b
            expected = []
            for name in ('fresh', 'receipt', 'zerorun'):
                arm = b['arms'][name]
                e = arm['expected_outcomes']
                expected.append(e)
                if name != 'fresh':
                    assert oracle(arm['qualification'], bp / name / 'oracle-full-stdout.txt') == e
                    result['requests_checked'] += 1
                assert len(arm['calls']) == 5
                for i, r in enumerate(arm['calls']):
                    assert r == read(bp / name / ('request-' + str(i) + '.json'))
                    status = 'FRESH_EXECUTED' if name == 'fresh' else 'MISS_EXECUTED' if i == 0 else 'HIT_REUSED'
                    response(r, status)
                    assert r['correctness_passed'] and r['expected_status'] == status
                    if status != 'HIT_REUSED':
                        assert summary(r['payload']['stdout_tail']) == e
                    result['requests_checked'] += 1
            assert expected[0] == expected[1] == expected[2]
            digests.append(expected[0])
        assert all(d == digests[0] for d in digests)
        result['targets'].append({'id': t['id'], 'blocks': len(t['blocks']), 'outcomes': digests[0]})
    result['passed'] = True
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=('B2', 'B3'))
    parser.add_argument('folder', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = session(args.folder) if args.kind == 'B2' else replay(args.folder)
    out['input_summary_sha256'] = digest((args.folder / 'summary.json').read_bytes())
    out['audit_script_sha256'] = digest(Path(__file__).read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps({'passed': out['passed'], 'output': str(args.output)}))
