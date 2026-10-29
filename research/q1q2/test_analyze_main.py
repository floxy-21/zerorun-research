"""Artificial cost identities and paired uncertainty, without main outcomes."""
from copy import deepcopy
import pytest
from research.q1q2.analyze_main import cost, analyze_target


def fixture():
    out = []
    for i in range(12):
        arms = {}
        for a, producer, consumer in [('fresh', 10, 10), ('receipt', 12, 1), ('zerorun', 13, 2)]:
            arms[a] = {'setup': {'materialization_seconds': 2, 'arm_setup_seconds': 3},
                       'qualification': None if a == 'fresh' else {'setup': {'materialization_seconds': 2, 'arm_setup_seconds': 1}, 'response': {'outer_seconds': 10}},
                       'calls': [{'outer_seconds': v, 'correctness_passed': True} for v in [producer] + [consumer] * 4]}
        out.append({'session': i // 6, 'block': i % 6, 'order': list(arms), 'status': 'completed', 'arms': arms})
    return out


def test_full_qualification_is_charged_once_with_its_setup():
    b = fixture()[0]['arms']
    assert cost(b['fresh'], 1, 'operational_qualification') == 25
    assert cost(b['receipt'], 1, 'operational_qualification') == 31
    assert cost(b['receipt'], 4, 'operational_qualification') == 34
    assert cost(b['receipt'], 1, 'prequalified_source') == 18
    assert cost(b['receipt'], 1, 'request_only') == 13


def test_constant_paired_budget_and_margin_have_exact_intervals():
    r = analyze_target(fixture(), 'artificial')['scenarios']['operational_qualification']
    assert r['1']['contrasts']['fresh_minus_receipt']['ci95_seconds'] == [-6, -6]
    assert not r['1']['contrasts']['fresh_minus_receipt']['five_percent_improvement_supported']
    assert r['4']['contrasts']['fresh_minus_receipt']['ci95_seconds'] == [21, 21]
    assert r['4']['contrasts']['fresh_minus_receipt']['five_percent_margin_ci95_seconds'] == [18.25, 18.25]


def test_incomplete_or_invalid_blocks_do_not_become_complete_cases():
    x = fixture()
    assert analyze_target(x[:-1], 'artificial')['status'] == 'incomplete_no_balanced_estimate'
    x[0]['status'] = 'failed'
    assert analyze_target(x, 'artificial')['status'] == 'incomplete_no_balanced_estimate'
    x = fixture()
    x[0]['arms']['fresh']['calls'][0]['correctness_passed'] = False
    with pytest.raises(ValueError):
        analyze_target(x, 'artificial')
