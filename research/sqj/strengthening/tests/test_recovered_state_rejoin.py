"""Offline post-interruption binding tests; no Docker or agent commands run."""
import json

import pytest

from research.sqj.strengthening import run_recovered_state_rejoin as w
from research.sqj.strengthening import analyze_state_rejoin as a
from research.sqj.strengthening.tests import test_bound_state_rejoin as base
from research.sqj.strengthening.tests.test_analyze_state_rejoin import case, bound, write, stamp


def prepared(monkeypatch, tmp_path, **options):
    monkeypatch.setattr(base, "w", w)
    engine, evidence, previous, output, calls = base.prepare(monkeypatch, tmp_path, **options)
    (previous.parent / "campaign-summary.json").unlink()
    receipt = tmp_path / "interruption.json"
    write(receipt, {"schema": "zerorun.vm-interruption.v1",
        "replication_protocol_sha256": w.file_record(previous)["sha256"],
        "observed_utc": "2026-01-01T00:00:00Z", "description": "Fixture VM interruption",
        "recovery_action": "Fixture restart; original summary remains absent"})
    return engine, evidence, previous, output, receipt, calls


def test_missing_original_summary_stays_missing_and_case_is_standalone(monkeypatch, tmp_path):
    engine, evidence, prior, output, receipt, calls = prepared(monkeypatch, tmp_path)
    assert w.run_bound(engine, evidence, prior, output, receipt) == 0
    protocol = json.loads((output / "protocol.json").read_bytes())
    assert not protocol["replication"]["completed"]
    assert not protocol["replication"]["completion_present"]
    assert protocol["replication"]["completion_file"] is None
    assert not (prior.parent / "campaign-summary.json").exists()
    assert not (output / "original-campaign-summary.json").exists()
    assert "replication_completed" not in protocol["checks"]
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("replication_protocol_sha256", "0" * 64),
    ("observed_utc", "2099-01-01T00:00:00Z"), ("description", ""), ("recovery_action", ""),
    ("schema", "unbound")])
def test_bad_interruption_receipt_never_invokes_runner(monkeypatch, tmp_path, field, value):
    engine, evidence, prior, output, receipt, calls = prepared(monkeypatch, tmp_path)
    row = json.loads(receipt.read_bytes())
    row[field] = value
    write(receipt, row)
    assert w.run_bound(engine, evidence, prior, output, receipt) == 1
    assert calls == []
    assert not json.loads((output / "completion.json").read_bytes())["completed"]


@pytest.mark.parametrize("option", ["drift", "fail_runner"])
def test_recovery_does_not_waive_failures_or_core_drift(monkeypatch, tmp_path, option):
    engine, evidence, prior, output, receipt, calls = prepared(monkeypatch, tmp_path, **{option: True})
    assert w.run_bound(engine, evidence, prior, output, receipt) == 1
    assert len(calls) == 1
    assert not json.loads((output / "completion.json").read_bytes())["completed"]


@pytest.fixture
def recovered(bound):
    root, prior = bound
    (prior / "campaign-summary.json").unlink()
    protocol = a.read_json(root / "protocol.json")
    protocol["schema"] = "zerorun.recovered-state-rejoin.protocol.v1"
    protocol["wrapper"] = w.file_record(w.HERE / "run_recovered_state_rejoin.py", label="strengthening/run_recovered_state_rejoin.py")
    protocol["checks"].pop("replication_completed")
    protocol["checks"].update(original_protocol_bound=True, interruption_provenance_bound=True)
    protocol["replication"].update(completion_file=None, completed=False, completion_present=False)
    protocol["replication_status_claim"] = "original campaign not certified complete; standalone case after interruption"
    write(root / "interruption.json", {"schema": "zerorun.vm-interruption.v1", "observed_utc": stamp(15),
        "replication_protocol_sha256": a.digest(prior / "protocol.json"),
        "description": "Fixture interruption", "recovery_action": "Fixture recovery"})
    protocol["interruption_file"] = w.file_record(root / "interruption.json", label="interruption.json")
    write(root / "protocol.json", protocol)
    completion = a.read_json(root / "completion.json")
    completion["schema"] = "zerorun.recovered-state-rejoin.completion.v1"
    completion["protocol_sha256"] = a.digest(root / "protocol.json")
    completion["wrapper_after"] = protocol["wrapper"]
    completion["checks"]["interruption_receipt_unchanged"] = True
    write(root / "completion.json", completion)
    return root, prior


def test_independent_analyzer_accepts_bound_standalone_not_completed_campaign(recovered):
    root, prior = recovered
    result = a.analyze(root, replication_directory=prior)
    assert result["completed"], result["errors"]
    assert result["requests"] == 4 and result["optimized_hits"] == 1
    assert result["provenance"]["execution_context"] == "standalone-after-vm-interruption"
    assert not result["provenance"]["original_campaign_certified_complete_by_case"]


@pytest.mark.parametrize("kind", ["fake-completion", "changed-interruption", "wrong-source", "no-interruption-check"])
def test_independent_analyzer_rejects_manufactured_recovery(recovered, kind):
    root, prior = recovered
    protocol = a.read_json(root / "protocol.json")
    if kind == "fake-completion":
        protocol["replication"]["completed"] = True
    elif kind == "changed-interruption":
        write(root / "interruption.json", {"schema": "unbound"})
    elif kind == "wrong-source":
        protocol["core_protected_before"] = {}
    else:
        protocol["checks"].pop("interruption_provenance_bound")
    write(root / "protocol.json", protocol)
    completion = a.read_json(root / "completion.json")
    completion["protocol_sha256"] = a.digest(root / "protocol.json")
    write(root / "completion.json", completion)
    result = a.analyze(root, replication_directory=prior)
    assert not result["completed"] and result["errors"]
