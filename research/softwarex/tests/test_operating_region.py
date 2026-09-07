"""Arithmetic, provenance, and timing-boundary checks; no Docker experiments."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from research.softwarex import analyze_operating_region as a


def hit(outer=2):
    return {"status": "HIT_REUSED", "completed": True, "request_wall_ms": outer,
            "phase_ms": {**{name: outer / 10 for name in a.HIT_COMPONENTS},
                "readonly_hit_path": outer * 0.99, "snapshot_prepare": 0, "snapshot_cleanup": 0}}


def direct(outer=10):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    phases = []
    for index, name in enumerate(a.PHASE_ORDER):
        phases.append({"phase": name, "wall_ms": outer / 8,
                       "started_utc": (start + timedelta(milliseconds=index * outer / 8)).isoformat(),
                       "ended_utc": (start + timedelta(milliseconds=(index + 1) * outer / 8)).isoformat()})
    return {"completed": True, "request_wall_ms": outer, "docker_subprocess_phases": phases,
            "started_utc": start.isoformat(), "ended_utc": (start + timedelta(milliseconds=outer)).isoformat()}


def block_rows():
    result = []
    for index, label in enumerate(a.LABELS):
        fast = hit() if index in a.HITS else {"status": "MISS_EXECUTED" if index == 0 else "MISS_FAILED", "completed": True, "request_wall_ms": 20}
        result.append({"index": index, "label": label, "trajectory": 1,
                       "whole_task_exit_agreement": True, "cache_behavior_expected": True,
                       "arms": {"direct": direct(), "fast": fast}})
    return result


def test_two_direct_means_are_not_replaced_by_pooled_direct_mean():
    result = a.crossover(12, 2, 8, 20)
    assert result["equality_hit_fraction"] == pytest.approx(12 / 22)
    p = result["equality_hit_fraction"]
    assert p * 12 + (1 - p) * 8 == pytest.approx(p * 2 + (1 - p) * 20)
    assert result["regime"] == "reuse_faster_above_equality"


@pytest.mark.parametrize("values,regime", [
    ((3, 3, 4, 4), "equal_everywhere"),
    ((1, 3, 3, 5), "reuse_slower_everywhere"),
    ((3, 1, 3, 1), "reuse_faster_everywhere"),
])
def test_zero_denominator_is_explicit_not_divided_or_clamped(values, regime):
    result = a.crossover(*values)
    assert result["equality_hit_fraction"] is None
    assert result["regime"] == regime and not result["interior_crossover"]


@pytest.mark.parametrize("values,expected", [((10, 2, 10, 8), -1 / 3), ((2, 6, 4, 2), 1 / 3), ((2, 6, 4, 6), -1)])
def test_negative_slope_or_outside_crossing_is_preserved(values, expected):
    result = a.crossover(*values)
    assert result["equality_hit_fraction"] == pytest.approx(expected)
    assert result["equality_in_unit_interval"] == (0 <= expected <= 1)
    if result["denominator_ms"] < 0:
        assert result["regime"] == "reuse_faster_below_equality"


def test_symbolic_extra_cost_changes_intercept_not_slope():
    original = a.crossover(10, 2, 10, 20)
    added = a.crossover(10, 2, 10, 20, added_cost_per_request=5)
    assert added["equality_hit_fraction"] == pytest.approx(15 / 18)
    assert added["denominator_ms"] == original["denominator_ms"]
    assert a.crossover(10, 2, 10, 20, added_cost_per_request=-10)["equality_hit_fraction"] == 0


@pytest.mark.parametrize("value", [True, False, -1, float("nan"), float("inf"), "12", None])
def test_invalid_cost_is_not_a_measurement(value):
    with pytest.raises(ValueError):
        a.crossover(value, 2, 10, 20)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'])
def test_ambiguous_or_nonfinite_json_is_refused(raw):
    with pytest.raises(ValueError):
        a.strict_json(raw)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a//b", "a/./b", "a\\b", "C:stream", ""])
def test_input_names_are_canonical(name):
    with pytest.raises(ValueError):
        a.safe_name(name)


def test_hit_partition_excludes_inclusive_readonly_aggregate():
    result = a.hit_components(hit(10))
    assert result["two_fingerprints_ms"] == 2
    assert result["unattributed_outer_residual_ms"] == 3
    assert result["inclusive_readonly_aggregate_not_added_ms"] == 9.9
    combined = a.attribution([result, result], hit=True)
    assert sum(combined["fractions_of_outer"].values()) == pytest.approx(1)


@pytest.mark.parametrize("mode", ["nonhit", "extra-phase", "missing-phase", "staging", "execution", "negative", "overflow", "inclusive-overflow"])
def test_invalid_hit_partition_is_rejected(mode):
    value = hit()
    if mode == "nonhit":
        value["status"] = "MISS_EXECUTED"
    elif mode == "extra-phase":
        value["phase_ms"]["unknown_nested_phase"] = 1
    elif mode == "missing-phase":
        value["phase_ms"].pop("cache_lookup")
    elif mode == "staging":
        value["phase_ms"]["snapshot_prepare"] = 1
    elif mode == "execution":
        value["phase_ms"]["docker_execution"] = 1
    elif mode == "negative":
        value["phase_ms"]["fingerprint_initial"] = -1
    elif mode == "overflow":
        value["phase_ms"]["fingerprint_initial"] = 20
    else:
        value["phase_ms"]["readonly_hit_path"] = 20
    with pytest.raises(ValueError):
        a.hit_components(value)


def test_direct_partition_has_named_control_start_and_residual():
    value = a.direct_components(direct(8))
    assert value == {"outer_ms": 8, "docker_start_including_attached_execution_ms": 1,
                     "other_docker_control_ms": 6, "unattributed_outer_residual_ms": 1}


@pytest.mark.parametrize("mode", ["missing-phase", "overlap", "outside", "overflow", "negative", "no-timezone"])
def test_invalid_direct_partition_is_rejected(mode):
    value = direct()
    if mode == "missing-phase":
        value["docker_subprocess_phases"].pop()
    elif mode == "overlap":
        value["docker_subprocess_phases"][1]["started_utc"] = value["started_utc"]
    elif mode == "outside":
        value["ended_utc"] = value["started_utc"]
    elif mode == "overflow":
        value["docker_subprocess_phases"][0]["wall_ms"] = 100
    elif mode == "negative":
        value["docker_subprocess_phases"][0]["wall_ms"] = -1
    else:
        value["started_utc"] = "2026-01-01T00:00:00"
    with pytest.raises(ValueError):
        a.direct_components(value)


def test_full_mixture_reconstructs_unfavorable_sequence_without_filtering():
    result = a.summarize_rows(block_rows())
    assert result["requests"] == 7 and result["hit_category_requests"] == 4 and result["nonhit_category_requests"] == 3
    assert result["completed_request_totals_ms"] == {"direct": 70, "fast": 68}
    assert result["crossover"]["equality_hit_fraction"] == pytest.approx(10 / 18)
    assert result["observed_mixture_direct_to_fast_ratio"] == pytest.approx(70 / 68)


@pytest.mark.parametrize("mode", ["missing-row", "duplicate-row", "wrong-label", "wrong-status", "false-agreement", "false-behavior", "incomplete-arm"])
def test_missing_or_ineligible_request_never_enters_estimator(mode):
    rows = block_rows()
    if mode == "missing-row":
        rows.pop()
    elif mode == "duplicate-row":
        rows[-1] = deepcopy(rows[0])
    elif mode == "wrong-label":
        rows[0]["label"] = "repeat-1"
    elif mode == "wrong-status":
        rows[0]["arms"]["fast"]["status"] = "HIT_REUSED"
    elif mode == "false-agreement":
        rows[0]["whole_task_exit_agreement"] = False
    elif mode == "false-behavior":
        rows[0]["cache_behavior_expected"] = False
    else:
        rows[0]["arms"]["direct"]["completed"] = False
    with pytest.raises(ValueError):
        a.summarize_rows(rows)


def test_frozen_anchor_refuses_changed_bytes(tmp_path, monkeypatch):
    path = tmp_path / "anchor.json"
    path.write_bytes(b"original")
    monkeypatch.setattr(a, "PINNED", {"anchor.json": hashlib.sha256(b"original").hexdigest()})
    assert a.anchored_inputs(tmp_path)[0]["bytes"] == 8
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="anchor changed"):
        a.anchored_inputs(tmp_path)


def test_directory_is_not_a_regular_input(tmp_path):
    (tmp_path / "directory").mkdir()
    with pytest.raises(ValueError, match="nonordinary"):
        a.read_regular(tmp_path, "directory")


@pytest.fixture(scope="module")
def real_analysis():
    return a.analyze()


def test_real_raw_data_reconcile_all_denominators_and_descriptive_results(real_analysis):
    assert real_analysis["completed"]
    assert real_analysis["counts"] == {"subjects": 4, "blocks": 24, "requests": 168, "hit_category": 96, "nonhit_category": 72}
    expected = {"click": .5424652542497497, "pycparser": .6358317595411732,
                "colorama": .5630341472853936, "packaging": .5505705188819535}
    for subject in real_analysis["subjects"]:
        assert len(subject["blocks"]) == 6
        assert subject["crossover"]["equality_hit_fraction"] == pytest.approx(expected[subject["workload"]])
        assert subject["constructed_hit_fraction"] == pytest.approx(4 / 7)
        assert not subject["block_crossover_range_is_confidence_interval"]
        assert subject["hit_cost_attribution"]["fractions_of_outer"]["two_fingerprints"] > .7
    assert real_analysis["subjects"][1]["observed_mixture_direct_to_fast_ratio"] < 1
    packaging = real_analysis["subjects"][-1]
    assert packaging["setup_inclusive_direct_to_fast_ratio"] is None
    assert [b["study"] for b in packaging["blocks"]] == ["short-randomized-replication-v1"] * 3 + ["short-randomized-recovery-v1"] * 3
    assert real_analysis["interruption"]["additional_interrupted_complete_requests"] == 1
    assert real_analysis["interruption"]["additional_incomplete_requests"] == 1
    assert not real_analysis["real_agent_hit_frequency_estimated"]


def test_check_mode_preserves_bytes_and_refuses_stale_result(tmp_path, monkeypatch, real_analysis):
    output = tmp_path / "result.json"
    output.write_bytes(a.encoded(real_analysis))
    original = output.read_bytes()
    monkeypatch.setattr(a, "analyze", lambda: deepcopy(real_analysis))
    assert a.main(["--check", "--output", str(output)]) == 0
    assert output.read_bytes() == original
    output.write_bytes(original + b" ")
    with pytest.raises(ValueError, match="saved operating-region analysis differs"):
        a.main(["--check", "--output", str(output)])
    assert output.read_bytes() == original + b" "


def test_generation_never_overwrites_existing_result(tmp_path, monkeypatch, real_analysis):
    output = tmp_path / "result.json"
    output.write_bytes(b"existing")
    monkeypatch.setattr(a, "analyze", lambda: deepcopy(real_analysis))
    with pytest.raises(FileExistsError):
        a.main(["--output", str(output)])
    assert output.read_bytes() == b"existing"


def test_raw_request_changed_after_reconciliation_is_rejected(monkeypatch):
    original = a.read_regular
    target = a.ORIGINAL + "/click/trajectory-1/request-1/observation.json"

    def changed(root, name):
        raw = original(root, name)
        if name == target:
            row = a.strict_json(raw)
            row["arms"]["fast"]["request_wall_ms"] += 1000
            return a.encoded(row)
        return raw

    monkeypatch.setattr(a, "read_regular", changed)
    with pytest.raises(ValueError, match="block total disagrees"):
        a.analyze()


def test_actually_imported_shadow_helper_must_match_frozen_source(monkeypatch):
    original = a.read_regular

    def changed(root, name):
        raw = original(root, name)
        return raw + b"\n# changed live validator\n" if name == "tools/product_generalization_benchmark.py" else raw

    monkeypatch.setattr(a, "read_regular", changed)
    with pytest.raises(ValueError, match="shadow-validation helper differs"):
        a.analyze()


def test_post_derivation_inventory_drift_is_rejected(monkeypatch):
    original = a.inventory
    calls = []

    def changed(root, reconciled):
        records = original(root, reconciled)
        calls.append(1)
        if len(calls) == 2:
            records[-1]["sha256"] = "0" * 64
        return records

    monkeypatch.setattr(a, "inventory", changed)
    with pytest.raises(ValueError, match="input changed during"):
        a.analyze()
