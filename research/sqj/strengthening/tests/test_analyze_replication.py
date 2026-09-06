"""Adversarial offline fixtures for the independent replication reconciler."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.sqj.strengthening import analyze_replication as analysis
from research.sqj.strengthening import randomized_replication as producer
from research.sqj.run_frozen_campaign import WORKLOADS, IMAGE
from tools.product_generalization_benchmark import _validate_shadow_evidence


SQJ = Path(__file__).resolve().parents[2]
ARCHIVE = SQJ / "source-final"
ORIGINAL = SQJ / "evidence/comparison-final-1/pycparser/summary.json"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def stamp(offset):
    return (datetime(2026, 9, 6, tzinfo=timezone.utc) + timedelta(seconds=offset)).isoformat()


def arm_receipt(arm, index, position, *, workload=0, trajectory=1):
    code = int(index in (3, 4))
    start = 10000 * workload + 1000 * trajectory + 10 * index + position
    elapsed = 300.0 if arm in ("direct", "oracle") else 100.0 if arm == "fast" and index in (1, 2, 5, 6) else 350.0
    row = {"completed": True, "exit_code": code, "wall_ms": elapsed - 10,
           "request_wall_ms": elapsed, "started_utc": stamp(start), "ended_utc": stamp(start + .5),
           "host_before": {"utc": stamp(start)}, "host_after": {"utc": stamp(start + .5)},
           "docker_subprocess_phases": []}
    if arm in ("direct", "oracle"):
        row["docker_subprocess_phases"] = [{"phase": name, "started_utc": stamp(start + .01 + i * .02),
            "ended_utc": stamp(start + .02 + i * .02), "wall_ms": 10.0,
            "timeout_seconds": 900 if name == "start" else 60,
            "exit_code": code if name == "start" else 1 if i == 6 else 0}
            for i, name in enumerate(analysis.PHASE_ORDER)]
    else:
        row.update(status=analysis.expected_state(index)["expected_cache_status"], phase_ms={
            "snapshot_prepare": 0.0 if arm == "fast" and index in (1, 2, 5, 6) else 100.0,
            "readonly_hit_path": elapsed - 20})
    return row


def oracle_capture(target, index):
    nodes = [target + "::test_existing"]
    outcomes = [{"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}]
    if index in (3, 4):
        nodes.append(target + "::test_zerorun_controlled_added_failure")
        outcomes.append({"setup": "passed", "call": "failed", "teardown": "passed", "wasxfail": False})
    return {"schema": "zerorun.benchmark-independent-pytest-shadow.v1", "exit_code": int(index in (3, 4)),
        "nodeids": nodes, "outcomes": outcomes,
        "nodeid_sha256": hashlib.sha256(analysis.canonical({"nodeids": nodes}).encode()).hexdigest()}


def make_protocol(path):
    source = analysis.read_json(ORIGINAL)["source_before"]
    bench = SimpleNamespace(_PYTEST_EXECUTION_TIMEOUT_SECONDS=900, _PLAIN_DOCKER_RESOURCE_ARGS=analysis.RESOURCE_ARGS,
                            _capture_source_identity=lambda: source)
    oci = SimpleNamespace(DOCKER_EXECUTION_TIMEOUT_SECONDS=900, DOCKER_RESOURCE_ARGS=analysis.RESOURCE_ARGS)
    result = producer.freeze_protocol(path, ARCHIVE, bench, oci, WORKLOADS, IMAGE)
    result["frozen_utc"] = stamp(0)
    write(path / "protocol.json", result)
    return result


def make_subject(path, protocol, name="packaging", *, number_of_blocks=6):
    item = next(item for item in WORKLOADS if item[0] == name)
    scheduled = next(item for item in analysis.expected_schedule() if item["workload"] == name)
    output = path / name
    rows, blocks = [], []
    for block in scheduled["trajectories"][:number_of_blocks]:
        trajectory = block["trajectory"]
        folder = output / f"trajectory-{trajectory}"
        write(folder / "block-plan.json", block)
        task = {"image": IMAGE, "cacheable": True, "result_only": True, "outputs": [], "cache_streams": False,
            "env": [], "unsafe_effects": [], "closure_reviewed": True, "platform": "linux/amd64",
            "inputs": [".zerorun-env", "tests"],
            "command": ["sh", ".zerorun-env/bin/python", "-m", "pytest", "-p", "no:cacheprovider", *item[4]]}
        original = analysis.read_json(SQJ / f"evidence/comparison-final-1/{name}/trajectory-1/manifest.json")
        task["inputs"] = original["tasks"]["pytest-fast"]["inputs"]
        write(folder / "manifest.json", {"version": 2, "tasks": {f"replication-{name}-{trajectory}-{arm}": task for arm in analysis.ARMS[1:]}})
        write(folder / "setup.json", {"isolated_result_cache": True, "shared_hardlinks": False,
            "private_byte_copy": True, "read_only_mode_and_content_verified": True, "environment_bootstrap_ms": 10.0})
        for index in range(7):
            request = folder / f"request-{index}"
            row = {"workload": name, "trajectory": trajectory, "index": index, "label": analysis.LABELS[index],
                "source_file_sha256": "b" * 64 if index in (3, 4) else "a" * 64, "order": block["order"], "arms": {}}
            write(request / "invocation.json", {**row, "frozen_sha": item[2], "targets": item[4], "expected": analysis.expected_state(index)})
            for position, arm in enumerate(block["order"]):
                row["arms"][arm] = arm_receipt(arm, index, position, trajectory=trajectory)
                write(request / (arm + ".json"), row["arms"][arm])
            capture = oracle_capture(item[4][0], index)
            row["oracle"] = {**arm_receipt("oracle", index, 3, trajectory=trajectory), "capture": _validate_shadow_evidence(capture)}
            write(request / "fresh.json", row["oracle"])
            write(request / "fresh-outcomes.json", capture)
            row.update(whole_task_exit_agreement=True, cache_behavior_expected=True)
            write(request / "observation.json", row)
            rows.append(row)
        receipt = {**block, "completed": True, "requests": 7}
        write(folder / "block-summary.json", receipt)
        write(folder / "restoration.json", {"restored": True, "source_sha256": "a" * 64, "original_sha256": "a" * 64})
        blocks.append(receipt)
    raw = {"workload": name, "upstream": item[1], "commit": item[2], "targets": item[4], "rows": rows, "blocks": blocks,
        "source_before": protocol["engine_identity"], "source_after": protocol["engine_identity"],
        "source_stable": True, "operator_review_claimed": False, "completed": number_of_blocks == 6,
        "environment": {"build_ms": 100.0, "provenance": {"runtime_image": IMAGE, "extra_requirements": item[5]}}}
    write(output / "summary.json", raw)
    return item, scheduled, raw


@pytest.fixture
def subject(tmp_path):
    protocol = make_protocol(tmp_path)
    item, scheduled, raw = make_subject(tmp_path, protocol)
    return tmp_path, protocol, item, scheduled, raw


def test_independent_schedule_and_state_reconciliation():
    assert analysis.expected_schedule() == producer.schedule()
    assert [analysis.expected_state(i) for i in range(7)] == [producer.expected_request(i) for i in range(7)]


def test_real_source_archives_bind_frozen_protocol(subject):
    path, protocol, *_ = subject
    analysis.validate_protocol(protocol, ARCHIVE)
    wrong = deepcopy(protocol)
    wrong["protected_source_identity"]["files"].pop()
    with pytest.raises(ValueError, match="digest"):
        analysis.validate_protocol(wrong, ARCHIVE)
    wrong = deepcopy(protocol)
    wrong["protected_source_identity"]["files"][0]["sha256"] = "0" * 64
    wrong["protected_source_identity"]["sha256"] = hashlib.sha256(analysis.canonical(wrong["protected_source_identity"]["files"]).encode()).hexdigest()
    with pytest.raises(ValueError, match="bytes"):
        analysis.validate_protocol(wrong, ARCHIVE)


@pytest.mark.parametrize("field,value", [("expected_requests", 167), ("operator_review_claimed", True),
    ("expected_trajectories", 23), ("execution_timeout_seconds", 600), ("testmon_in_this_replication", True),
    ("warmups_removed", 1), ("producer_sha256", "0" * 64), ("expected_fresh_oracles", True)])
def test_protocol_tampering_rejected(subject, field, value):
    _, protocol, *_ = subject
    wrong = deepcopy(protocol)
    wrong[field] = value
    with pytest.raises(ValueError):
        analysis.validate_protocol(wrong, ARCHIVE)


def test_full_subject_reports_all_blocks_and_influence_without_dropping(subject):
    path, protocol, item, scheduled, raw = subject
    result = analysis.inspect_workload(path / item[0], item, scheduled, protocol)
    assert result["completed"]
    assert result["complete_blocks"] == 6
    assert result["observed_complete_requests"] == result["fresh_agreements"] == 42
    assert result["optimized_hits"] == 24
    assert result["headline_performance"]["all_six_blocks_included"]
    assert result["headline_performance"]["block_direct_to_fast_spread"]["n"] == 6
    assert len(result["headline_performance"]["leave_one_block_out_influence"]) == 6
    assert result["headline_performance"]["observations_removed"] == 0
    assert result["headline_performance"]["warm_hit_median_ms"]["fast"] == 100
    assert result["headline_performance"]["common_environment_setup_ms"] == 160
    assert result["blocks"][0]["subprocess_phase_totals_ms"]["direct"]["start"] == 70


@pytest.mark.parametrize("kind", ["missing-row", "duplicate-row", "wrong-order", "wrong-raw-duration", "false-agreement", "source-restore", "namespace"])
def test_summary_or_raw_tampering_rejected(subject, kind):
    path, protocol, item, scheduled, raw = subject
    target = path / item[0]
    if kind == "missing-row":
        raw["rows"].pop()
    elif kind == "duplicate-row":
        raw["rows"][-1] = raw["rows"][0]
    elif kind == "wrong-order":
        raw["rows"][0]["order"].reverse()
    elif kind == "wrong-raw-duration":
        record = analysis.read_json(target / "trajectory-1/request-0/direct.json")
        record["request_wall_ms"] /= 2
        write(target / "trajectory-1/request-0/direct.json", record)
    elif kind == "false-agreement":
        record = analysis.read_json(target / "trajectory-1/request-0/observation.json")
        record["whole_task_exit_agreement"] = False
        write(target / "trajectory-1/request-0/observation.json", record)
    elif kind == "source-restore":
        record = analysis.read_json(target / "trajectory-1/restoration.json")
        record["source_sha256"] = "c" * 64
        write(target / "trajectory-1/restoration.json", record)
    else:
        record = analysis.read_json(target / "trajectory-1/manifest.json")
        record["tasks"]["replication-packaging-1-fast"]["cacheable"] = False
        write(target / "trajectory-1/manifest.json", record)
    write(target / "summary.json", raw)
    with pytest.raises(ValueError):
        analysis.inspect_workload(target, item, scheduled, protocol)


@pytest.mark.parametrize("kind", ["negative", "bool", "phase-sum", "phase-timeout", "phase-order", "phase-interval", "helper-over-outer"])
def test_invalid_clock_or_lifecycle_cannot_be_used(kind):
    receipt = arm_receipt("direct", 0, 0)
    if kind == "negative":
        receipt["request_wall_ms"] = -1
    elif kind == "bool":
        receipt["request_wall_ms"] = True
    elif kind == "phase-sum":
        receipt["docker_subprocess_phases"][3]["wall_ms"] = 301
    elif kind == "phase-timeout":
        receipt["docker_subprocess_phases"][3]["timeout_seconds"] = 600
    elif kind == "phase-order":
        receipt["docker_subprocess_phases"].reverse()
    elif kind == "phase-interval":
        receipt["docker_subprocess_phases"][0]["ended_utc"] = stamp(2000)
    else:
        receipt["wall_ms"] = 301
    with pytest.raises(ValueError):
        analysis.validate_timing(receipt, "direct")


def test_incomplete_subject_retains_completed_blocks_but_has_no_headline(tmp_path):
    protocol = make_protocol(tmp_path)
    item, scheduled, raw = make_subject(tmp_path, protocol, number_of_blocks=2)
    result = analysis.inspect_workload(tmp_path / item[0], item, scheduled, protocol)
    assert not result["completed"]
    assert result["complete_blocks"] == 2
    assert result["observed_complete_requests"] == 14
    assert result["headline_performance"] is None
    assert "trajectory-3" in result["missing"]


def test_claimed_completion_on_incomplete_subject_rejected(tmp_path):
    protocol = make_protocol(tmp_path)
    item, scheduled, raw = make_subject(tmp_path, protocol, number_of_blocks=2)
    raw["completed"] = True
    write(tmp_path / item[0] / "summary.json", raw)
    with pytest.raises(ValueError, match="completion"):
        analysis.inspect_workload(tmp_path / item[0], item, scheduled, protocol)


def test_full_campaign_and_missing_campaign_receipt(tmp_path):
    protocol = make_protocol(tmp_path)
    for name in analysis.COHORT:
        make_subject(tmp_path, protocol, name)
    campaign = {"schema": "zerorun.randomized-short-replication-summary.v1", "completed": True,
        "protected_source_unchanged": True, "operator_authority_receipts_created": False,
        "protected_source_after": protocol["protected_source_identity"],
        "results": [{"workload": row["workload"], "completed": True, "requests": 42} for row in analysis.expected_schedule()]}
    write(tmp_path / "campaign-summary.json", campaign)
    result = analysis.analyze(tmp_path, ARCHIVE)
    assert result["completed"]
    assert result["counts"]["observed_complete_requests"] == 168
    assert result["counts"]["fresh_agreements"] == 168
    assert result["counts"]["optimized_hits"] == 96
    (tmp_path / "campaign-summary.json").unlink()
    missing = analysis.analyze(tmp_path, ARCHIVE)
    assert not missing["completed"] and missing["missing_campaign_summary"]


def test_original_outlier_diagnostics_match_observed_numbers():
    report = analysis.original_outlier_diagnostics()
    assert report["original_observations_removed"] == 0
    assert report["colorama"]["aggregate_direct_to_fast"] == pytest.approx(1.5623559143475587)
    assert report["colorama"]["single_direct_observation_fraction"] == pytest.approx(.439592502731253)
    assert report["colorama"]["leave_one_paired_request_out_diagnostic_only"] == pytest.approx(.8921124564047616)
    assert report["more_itertools"]["single_request_fraction_of_gap"] == pytest.approx(.9544118652395914)
    assert report["observations"][0]["pytest_reported_seconds"] == .47
    assert report["observations"][1]["pytest_reported_seconds"] == .50
    assert report["observations"][2]["pytest_reported_seconds"] == .93


def test_failed_cleanup_block_retains_costs_and_error_without_headline(subject):
    path, protocol, item, scheduled, raw = subject
    failure = {**raw["blocks"][-1], "completed": False, "error_type": "RuntimeError",
               "error": "mock cleanup failure", "retry_attempted": False}
    raw["blocks"][-1] = failure
    raw["completed"] = False
    write(path / item[0] / "trajectory-6/block-summary.json", failure)
    write(path / item[0] / "summary.json", raw)
    result = analysis.inspect_workload(path / item[0], item, scheduled, protocol)
    assert not result["completed"]
    assert result["complete_blocks"] == 5
    assert result["observed_complete_requests"] == 42
    assert result["headline_performance"] is None
    assert result["blocks"][-1]["observed_totals_ms"]["direct"] > 0
    assert result["blocks"][-1]["direct_to_fast_ratio"] is None
    assert result["errors"][0]["receipt"]["error"] == "mock cleanup failure"


def test_matching_rows_cannot_hide_reordered_execution_times(subject):
    path, protocol, item, scheduled, raw = subject
    request = path / item[0] / "trajectory-1/request-0"
    row = analysis.read_json(request / "observation.json")
    first, second = scheduled["trajectories"][0]["order"][:2]
    row["arms"][first]["started_utc"] = row["arms"][second]["started_utc"]
    write(request / (first + ".json"), row["arms"][first])
    write(request / "observation.json", row)
    raw["rows"][0] = row
    write(path / item[0] / "summary.json", raw)
    with pytest.raises(ValueError):
        analysis.inspect_workload(path / item[0], item, scheduled, protocol)


def test_equal_summary_and_receipt_cannot_invent_fresh_outcomes(subject):
    path, protocol, item, scheduled, raw = subject
    request = path / item[0] / "trajectory-1/request-1"
    capture = analysis.read_json(request / "fresh-outcomes.json")
    capture["nodeid_sha256"] = "0" * 64
    write(request / "fresh-outcomes.json", capture)
    with pytest.raises(ValueError, match="digest"):
        analysis.inspect_workload(path / item[0], item, scheduled, protocol)


def test_partial_request_costs_are_preserved_not_turned_into_zero(subject):
    path, protocol, item, scheduled, raw = subject
    request = path / item[0] / "trajectory-6/request-6"
    (request / "observation.json").unlink()
    raw["rows"].pop()
    failure = {**raw["blocks"][-1], "completed": False, "requests": 6,
        "error_type": "OSError", "error": "mock receipt-write failure", "retry_attempted": False}
    raw["blocks"][-1] = failure
    raw["completed"] = False
    write(path / item[0] / "trajectory-6/block-summary.json", failure)
    write(path / item[0] / "summary.json", raw)
    result = analysis.inspect_workload(path / item[0], item, scheduled, protocol)
    assert not result["completed"] and result["headline_performance"] is None
    partial = result["blocks"][-1]
    assert len(partial["partial_arm_receipts"]) == 4
    assert partial["partial_request_outer_totals_ms"] == {"direct": 300, "snapshot": 350, "fast": 100, "oracle": 300}


def test_duplicate_json_keys_and_nonfinite_measurements_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"completed":true,"completed":false}')
    with pytest.raises(ValueError, match="duplicate"):
        analysis.read_json(path)
    path.write_text('{"time":NaN}')
    with pytest.raises(ValueError, match="non-finite"):
        analysis.read_json(path)
