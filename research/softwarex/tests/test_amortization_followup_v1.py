"""Offline accounting/refusal tests; they do not execute the proposed study."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from research.softwarex import amortization_followup_v1 as a


def op(ms, status="HIT_REUSED", key="same", code=0):
    return {"outer_ms": ms, "result": {"status": status, "cache_key": key, "exit_code": code}}


def arms(count):
    return {
        "fresh": {"producer": op(10), "consumers": [op(10) for _ in range(count)],
                  "setup_outer_ms": 2, "oracle": op(7), "diagnostics": None},
        "zerorun": {"producer": op(30, "MISS_EXECUTED"), "consumers": [op(2) for _ in range(count)],
                    "setup_outer_ms": 3, "oracle": op(8), "diagnostics": op(20, "VERIFY_MATCH")},
    }


def test_chain_accounts_for_all_consumers_and_preserves_slowdown():
    one = a.measurements(arms(1), 1)
    four = a.measurements(arms(4), 4)
    assert one["chain_ms"] == {"fresh": 20, "zerorun": 32}
    assert one["chain_saved_fraction"] < 0
    assert four["chain_ms"] == {"fresh": 50, "zerorun": 38}
    assert four["setup_inclusive_chain_ms"] == {"fresh": 52, "zerorun": 41}
    assert four["oracle_ms"] == {"fresh": 7, "zerorun": 8}
    assert four["fresh_diagnostics_ms"] == 20
    assert four["consumer_ms"]["zerorun"] == [2, 2, 2, 2]


def test_a_consumer_miss_is_counted_without_forcing_a_hit():
    value = arms(2)
    value["zerorun"]["consumers"][1] = op(30, "MISS_EXECUTED")
    result = a.measurements(value, 2)
    assert result["cache_hits"] == 1 and result["chain_ms"]["zerorun"] == 62
    a.check_results(value["zerorun"]["producer"], value["zerorun"]["consumers"],
                    value["zerorun"]["diagnostics"], {"exit_code": 0}, "zerorun")


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True])
def test_nonfinite_missing_or_nonpositive_clock_is_not_zero_imputed(bad):
    value = arms(1); value["zerorun"]["consumers"][0]["outer_ms"] = bad
    with pytest.raises(ValueError): a.measurements(value, 1)


def test_missing_consumer_and_unplanned_count_are_rejected():
    with pytest.raises(ValueError): a.measurements(arms(1), 2)
    with pytest.raises(ValueError): a.measurements(arms(3), 3)


@pytest.mark.parametrize("mutation", ["warm-producer", "wrong-key", "failed-consumer", "cached-diagnostics", "failed-diagnostics", "boolean-exit"])
def test_incorrect_reuse_or_nonfresh_diagnostics_are_rejected(mutation):
    row = arms(2)["zerorun"]
    if mutation == "warm-producer": row["producer"]["result"]["status"] = "HIT_REUSED"
    elif mutation == "wrong-key": row["consumers"][0]["result"]["cache_key"] = "other"
    elif mutation == "failed-consumer": row["consumers"][1]["result"]["exit_code"] = 1
    elif mutation == "cached-diagnostics": row["diagnostics"]["result"]["status"] = "HIT_REUSED"
    elif mutation == "failed-diagnostics": row["diagnostics"]["result"]["exit_code"] = 1
    else: row["consumers"][0]["result"]["exit_code"] = False
    with pytest.raises(ValueError):
        a.check_results(row["producer"], row["consumers"], row["diagnostics"], {"exit_code": 0}, "zerorun")


def test_all_fixed_counts_have_two_counterbalanced_blocks():
    assert a.PLAN == ((1, 0), (1, 1), (2, 0), (2, 1), (4, 0), (4, 1))
    for count in a.COUNTS:
        assert a.block_order(count, 0) == list(reversed(a.block_order(count, 1)))


def test_platform_final_bit_rounding_does_not_hide_real_changes():
    a.same_measurements({"cost": [1234.1234567890001], "n": 4}, {"cost": [1234.123456789], "n": 4})
    for value in ({"cost": [1234.2], "n": 4}, {"cost": [1234.123456789], "n": True}):
        with pytest.raises(ValueError): a.same_measurements(value, {"cost": [1234.123456789], "n": 4})


def test_selection_is_actual_maximum_and_retains_unfavorable_history():
    root = Path(__file__).resolve().parents[3]
    base = root / "research/softwarex/evidence/application-revision-20260907-v6/record-only"
    selected = a.selected_case(base)
    assert selected["outcome_informed"] is True
    assert selected["selected"]["case_id"] == a.CASE
    assert selected["selected"]["cold_snapshot_prepare_ms"] == pytest.approx(13255.157)
    assert len(selected["all_case_scores"]) == 24


def test_export_preserves_failure_records_and_excludes_private_workspaces(tmp_path):
    a.h.save(tmp_path / "failure.json", {"failed": True})
    for directory in ("source", "private-cache-authentication-NOT-FOR-PUBLICATION", "n-1/block-0/zerorun/workspace"):
        (tmp_path / directory).mkdir(parents=True)
        (tmp_path / directory / "private.txt").write_text("not-public", encoding="utf-8")
    a.export(tmp_path)
    bundle = tmp_path / "record-only"
    manifest = a.v.read(bundle / "RECORD_MANIFEST.json")
    assert [row["path"] for row in manifest["files"]] == ["failure.json"]
    for row in manifest["files"]: a.h.bound(bundle, row)
    assert (tmp_path / "transfer.json").is_file()


def test_failure_before_main_protocol_is_exported(tmp_path, monkeypatch):
    out = tmp_path / "new-run"
    def failed(*args):
        out.mkdir(); a.h.save(out / "retained-preparation.json", {"failed": True})
        raise ValueError("preparation refused")
    monkeypatch.setattr(a, "execute", failed)
    monkeypatch.setattr(sys, "argv", ["followup", "--engine", str(tmp_path), "--acquisition", str(tmp_path),
        "--v6-records", str(tmp_path), "--image-completion", str(tmp_path / "image"),
        "--protocol", str(tmp_path / "protocol"), "--output", str(out)])
    with pytest.raises(ValueError, match="preparation refused"): a.main()
    saved = a.v.read(out / "record-only/preflight-failure.json")
    assert len(saved["conditions"]) == 6
    assert all(row["disposition"] == "INCOMPLETE" for row in saved["conditions"])
    assert a.v.read(out / "record-only/retained-preparation.json")["failed"] is True


def test_refusing_existing_output_never_adds_records(tmp_path, monkeypatch):
    out = tmp_path / "retained"; out.mkdir(); (out / "original.txt").write_bytes(b"preserve")
    def refused(*args): raise ValueError("new external output required")
    monkeypatch.setattr(a, "execute", refused)
    monkeypatch.setattr(sys, "argv", ["followup", "--engine", str(tmp_path), "--acquisition", str(tmp_path),
        "--v6-records", str(tmp_path), "--image-completion", str(tmp_path / "image"),
        "--protocol", str(tmp_path / "protocol"), "--output", str(out)])
    with pytest.raises(ValueError, match="new external output"): a.main()
    assert [p.name for p in out.iterdir()] == ["original.txt"]
