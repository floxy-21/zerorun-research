"""Frozen paired cost analysis. No target dropping or favorable retry selection."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

ARMS = ('fresh', 'receipt', 'zerorun')
PREFIXES = (0, 1, 2, 4)
SEED = 20261029
DRAWS = 10000
SCENARIOS = ('request_only', 'prequalified_source', 'operational_qualification')
CONTRASTS = (('fresh', 'zerorun'), ('fresh', 'receipt'), ('zerorun', 'receipt'))


def setup_cost(row):
    return row['materialization_seconds'] + row['arm_setup_seconds']


def components(arm, n):
    calls = arm['calls'][:n + 1]
    if len(calls) != n + 1 or not all(x['correctness_passed'] for x in calls):
        raise ValueError('missing or invalid request')
    q = arm['qualification']
    return {'materialization': arm['setup']['materialization_seconds'],
            'authority_setup': arm['setup']['arm_setup_seconds'],
            'qualification': 0.0 if q is None else setup_cost(q['setup']) + q['response']['outer_seconds'],
            'producer': calls[0]['outer_seconds'],
            'consumers': sum(c['outer_seconds'] for c in calls[1:])}


def cost(arm, n, scenario):
    c = components(arm, n)
    value = c['producer'] + c['consumers']
    if scenario != 'request_only':
        value += c['materialization'] + c['authority_setup']
    if scenario == 'operational_qualification':
        value += c['qualification']
    return value


def interval(draws):
    return np.quantile(draws, [0.025, 0.975]).tolist()


def analyze_target(records, target):
    blocks = sorted(records, key=lambda b: (b['session'], b['block']))
    valid = (len(blocks) == 12 and all(b['status'] == 'completed' for b in blocks)
             and [b['session'] for b in blocks] == [0] * 6 + [1] * 6
             and len({(b['session'], b['block']) for b in blocks}) == 12)
    if not valid:
        return {'target': target, 'status': 'incomplete_no_balanced_estimate', 'blocks': blocks}
    rng = np.random.default_rng(SEED)
    # The same paired block draws serve every arm, cost scenario and prefix.
    indices = np.concatenate([rng.integers(0, 6, (DRAWS, 6)),
                              rng.integers(6, 12, (DRAWS, 6))], axis=1)
    result = {'target': target, 'status': 'analyzed', 'planned_blocks': 12, 'scenarios': {},
              'raw_components': [{k: b[k] for k in ('session', 'block', 'order')} | {
                  'arms': {a: {str(n): components(b['arms'][a], n) for n in PREFIXES} for a in ARMS}}
                  for b in blocks]}
    for scenario in SCENARIOS:
        output = result['scenarios'][scenario] = {}
        for n in PREFIXES:
            values = np.array([[cost(b['arms'][a], n, scenario) for a in ARMS] for b in blocks])
            means = values.mean(axis=0)
            bootstrap = values[indices].mean(axis=1)
            row = {'means_seconds': dict(zip(ARMS, means.tolist())),
                   'session_means_seconds': [dict(zip(ARMS, v.mean(axis=0).tolist())) for v in (values[:6], values[6:])],
                   'raw_costs_seconds': values.tolist(), 'contrasts': {}}
            for left, right in CONTRASTS:
                i, j = ARMS.index(left), ARMS.index(right)
                diffs = bootstrap[:, i] - bootstrap[:, j]
                entry = {'difference_seconds': float(means[i] - means[j]), 'ci95_seconds': interval(diffs),
                         'right_over_left_ratio': float(means[j] / means[i]),
                         'ci95_ratio': interval(bootstrap[:, j] / bootstrap[:, i])}
                if left == 'fresh':
                    margins = diffs - 0.05 * bootstrap[:, 0]
                    entry['five_percent_margin_ci95_seconds'] = interval(margins)
                    entry['five_percent_improvement_supported'] = bool(entry['five_percent_margin_ci95_seconds'][0] > 0)
                row['contrasts'][left + '_minus_' + right] = entry
            output[str(n)] = row
    # Session 0 calibration is fixed before evaluating session 1 residuals.
    prediction = result['session0_prediction_session1'] = {}
    for a in ARMS:
        m0 = {k: np.mean([components(b['arms'][a], 4)[k] for b in blocks[:6]])
              for k in components(blocks[0]['arms'][a], 4)}
        prediction[a] = {str(n): {'predicted_seconds': float(m0['materialization'] + m0['authority_setup'] + m0['qualification'] + m0['producer'] + n * m0['consumers'] / 4),
                                 'observed_seconds': float(np.mean([cost(b['arms'][a], n, 'operational_qualification') for b in blocks[6:]]))}
                         for n in PREFIXES}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('sessions', nargs=2, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    summaries = [json.loads((p / 'summary.json').read_text()) for p in args.sessions]
    if sorted(x['session'] for x in summaries) != [0, 1]:
        raise ValueError('exactly the two planned sessions required')
    selection = json.loads(Path(__file__).with_name('MAIN_SELECTION.json').read_text())
    out = {'schema': 'zerorun.q1q2.analysis.v1', 'seed': SEED, 'draws': DRAWS,
           'interval_scope': 'pointwise paired-block repeat variability conditional on two realized sessions',
           'inputs': [{'path': str(p), 'sha256': hashlib.sha256((p / 'summary.json').read_bytes()).hexdigest()} for p in args.sessions],
           'common_preparation': [], 'targets': []}
    for folder, summary in zip(args.sessions, summaries):
        prep = json.loads((folder / 'runtime/completion.json').read_text())
        acquisitions = {p.parent.name: json.loads(p.read_text())['outer_seconds'] for p in folder.glob('*/acquisition.json')}
        out['common_preparation'].append({'session': summary['session'], 'runtime_seconds': prep['outer_seconds'],
            'acquisition_seconds': acquisitions,
            'allocation_rule': 'First use of one selected target charges this full runtime preparation plus that target acquisition equally to each arm; no amortization over measurement blocks.'})
    for target in selection['targets']:
        blocks = [b for summary in summaries for t in summary['targets'] if t['id'] == target['id'] for b in t.get('blocks', [])]
        result = analyze_target(blocks, target['id'])
        result['selected_target_dispositions'] = [{'session': s['session'], 'status': next((t['status'] for t in s['targets'] if t['id'] == target['id']), 'not_started')} for s in summaries]
        out['targets'].append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
