"""Bounded offline adversarial recovery fixtures; no VM or cache authorization."""
from datetime import datetime, timedelta
from pathlib import Path
import shutil

import pytest

from research.sqj.strengthening import analyze_replication as base
from research.sqj.strengthening import analyze_recovered_replication as analysis
from research.sqj.strengthening import run_replication_recovery as producer
from research.sqj.strengthening.tests.test_analyze_replication import make_protocol, make_subject, write, stamp, ARCHIVE


def remove(root, relative):
    target = root / relative
    assert target.resolve().is_relative_to(root.resolve())
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def shift(value):
    if isinstance(value, dict):
        return {key: ((datetime.fromisoformat(item) + timedelta(seconds=100000)).isoformat()
                      if key in {"started_utc", "ended_utc", "utc"} and isinstance(item, str) else shift(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [shift(item) for item in value]
    return value


@pytest.fixture
def recovered(tmp_path):
    original = tmp_path / "original"
    recovery = tmp_path / "recovery"
    original.mkdir()
    recovery.mkdir()
    protocol = make_protocol(original)
    for name in base.COHORT:
        make_subject(original, protocol, name)
    _, _, recovered_raw = make_subject(recovery, protocol, "packaging")
    for number in (1, 2, 3):
        remove(recovery, f"packaging/trajectory-{number}")
    recovered_raw["rows"] = [r for r in recovered_raw["rows"] if r["trajectory"] >= 4]
    recovered_raw["blocks"] = [b for b in recovered_raw["blocks"] if b["trajectory"] >= 4]
    recovered_raw["completed"] = False  # frozen helper's six-block workload flag
    write(recovery / "packaging/summary.json", recovered_raw)
    for file in recovery.rglob("*.json"):
        write(file, shift(base.read_json(file)))
    for relative in ("packaging/summary.json", "packaging/trajectory-5", "packaging/trajectory-6",
                     "packaging/trajectory-4/block-summary.json", "packaging/trajectory-4/restoration.json"):
        remove(original, relative)
    for number in (3, 4, 5, 6):
        remove(original, f"packaging/trajectory-4/request-{number}")
    for file in (original / "packaging/trajectory-4/request-2").iterdir():
        if file.name != "invocation.json":
            file.unlink()
    planned = producer.plan_recovery(original, protocol)
    (recovery / "external-incident.txt").write_text("Synthetic test fixture, not a real crash receipt.\n")
    p = {"schema": "zerorun.short-replication-recovery-protocol.v1", "frozen_utc": stamp(10000),
        "frozen_driver_sha256": base.DRIVER_SHA256,
        "producer_sha256": base.digest(Path(producer.__file__)),
        "original_protocol_sha256": base.digest(original / "protocol.json"),
        "original_evidence_inventory": producer.evidence_inventory(original),
        "incident_sha256": base.digest(recovery / "external-incident.txt"),
        "selection_uses_performance": False, "operator_authority_allowed": False,
        "protected_source_before": protocol["protected_source_identity"], "engine_identity_before": protocol["engine_identity"], **planned}
    write(recovery / "recovery-protocol.json", p)
    c = {"schema": "zerorun.short-replication-recovery-completion.v1",
        "recovery_protocol_sha256": base.digest(recovery / "recovery-protocol.json"),
        "protected_source_after": protocol["protected_source_identity"], "engine_identity_after": protocol["engine_identity"],
        "original_evidence_unchanged": True, "operator_authority_receipts_created": False,
        "uninterrupted_original_campaign": False, "completed": True,
        "results": [{"workload": "packaging", "completed": True, "requests": 21}]}
    write(recovery / "recovery-completion.json", c)
    return original, recovery, protocol


def test_recovery_retains_partial_attempt_and_never_fakes_setup(recovered):
    original, recovery, _ = recovered
    result = analysis.analyze(original, recovery, ARCHIVE)
    assert result["completed"]
    assert not result["uninterrupted_original_campaign"]
    assert result["counts"]["complete_blocks"] == 24
    assert result["counts"]["observed_complete_requests"] == 168
    assert result["counts"]["fresh_agreements"] == 168
    assert result["counts"]["optimized_hits"] == 96
    assert result["additional_interrupted_complete_requests"] == 2
    assert result["additional_incomplete_requests"] == 1
    packaging = next(r for r in result["subjects"] if r["workload"] == "packaging")
    assert packaging["setup_inclusive_direct_to_fast_ratio"] is None
    assert packaging["additional_interrupted_attempt_measured_totals_ms"]["direct"] == 600
    assert not (original / "campaign-summary.json").exists()


def test_original_mismatch_cannot_be_reclassified(recovered):
    original, _, protocol = recovered
    path = original / "packaging/trajectory-4/request-1/observation.json"
    row = base.read_json(path)
    row["whole_task_exit_agreement"] = False
    write(path, row)
    with pytest.raises(ValueError, match="mismatch"):
        producer.plan_recovery(original, protocol)


@pytest.mark.parametrize("kind", ["drop-original", "change-incident", "different-schedule", "lie-uninterrupted", "missing-new-block"])
def test_recovery_tampering_rejected(recovered, kind):
    original, recovery, _ = recovered
    if kind == "drop-original":
        remove(original, "packaging/trajectory-4/request-2/invocation.json")
    elif kind == "change-incident":
        (recovery / "external-incident.txt").write_text("replacement")
    elif kind == "different-schedule":
        path = recovery / "recovery-protocol.json"
        value = base.read_json(path)
        value["recovery_schedule"][0]["trajectories"].pop()
        write(path, value)
    elif kind == "lie-uninterrupted":
        path = recovery / "recovery-completion.json"
        value = base.read_json(path)
        value["uninterrupted_original_campaign"] = True
        write(path, value)
    else:
        remove(recovery, "packaging/trajectory-6/block-summary.json")
    with pytest.raises((ValueError, KeyError, OSError)):
        analysis.analyze(original, recovery, ARCHIVE)


def test_failed_block_does_not_authorize_retry(recovered):
    original, _, protocol = recovered
    write(original / "packaging/trajectory-4/block-summary.json", {"completed": False, "requests": 2})
    with pytest.raises(ValueError, match="failed block"):
        producer.plan_recovery(original, protocol)


def test_inventory_omits_private_authentication(recovered):
    original, _, _ = recovered
    private = original / "private-cache-authentication-NOT-FOR-PUBLICATION/secret.json"
    write(private, {"secret": "not-a-real-secret-test-value"})
    assert not any("secret" in r["path"] for r in producer.evidence_inventory(original))
    assert producer.evidence_inventory(original) == analysis.inventory(original)


def rebind_original(original, recovery):
    path = recovery / "recovery-protocol.json"
    p = base.read_json(path)
    p["original_evidence_inventory"] = producer.evidence_inventory(original)
    write(path, p)
    path = recovery / "recovery-completion.json"
    c = base.read_json(path)
    c["recovery_protocol_sha256"] = base.digest(recovery / "recovery-protocol.json")
    write(path, c)


def test_real_crash_shape_retains_three_zero_byte_receipts(recovered):
    original, recovery, protocol = recovered
    for relative in ("packaging/trajectory-4/request-1/observation.json", "packaging/trajectory-4/request-1/fresh.json",
                     "packaging/trajectory-4/request-2/invocation.json"):
        (original / relative).write_bytes(b"")
    planned = producer.plan_recovery(original, protocol)
    assert [b["trajectory"] for b in planned["recovery_schedule"][0]["trajectories"]] == [4, 5, 6]
    rebind_original(original, recovery)
    result = analysis.analyze(original, recovery, ARCHIVE)
    assert result["completed"] and result["counts"]["observed_complete_requests"] == 168
    assert result["additional_interrupted_complete_requests"] == 1
    assert result["additional_incomplete_requests"] == 1
    assert len(result["unparseable_zero_byte_receipts"]) == 3
    assert result["unflushed_invocation_only_directories"] == ["packaging/trajectory-4/request-2"]
    partial = result["interrupted_attempts"][0]
    assert len(partial["partial_arm_receipts"]) == 3
    assert partial["partial_request_outer_totals_ms"]["oracle"] == 0
    assert partial["partial_request_outer_totals_ms"]["direct"] == 300


def test_nonempty_malformed_receipt_is_not_silently_filtered(recovered):
    original, recovery, _ = recovered
    (original / "packaging/trajectory-4/request-1/observation.json").write_bytes(b'{"broken":')
    rebind_original(original, recovery)
    with pytest.raises(ValueError):
        analysis.analyze(original, recovery, ARCHIVE)


def test_finished_campaign_cannot_be_recovered(recovered):
    original, _, protocol = recovered
    write(original / "campaign-summary.json", {"completed": True})
    with pytest.raises(ValueError, match="interrupted"):
        producer.plan_recovery(original, protocol)
