"""Artificial reconciliation fixtures, never model or installation evidence."""
from copy import deepcopy
import hashlib
import json

import pytest

from research.softwarex import build_application_evidence as application
from research.softwarex import quickstart_check
from research.softwarex.live_client_v2 import validation


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def sample(tmp_path, monkeypatch):
    original = tmp_path / application.ORIGINAL
    original.parent.mkdir(parents=True)
    original.write_bytes(b"Artificial prior adverse record; not an experiment.\n")
    monkeypatch.setattr(application, "ORIGINAL_SHA256", hashlib.sha256(original.read_bytes()).hexdigest())
    live = {"freeze": {"artificial": True}, "real_repository_authorized": False,
            "runtime_modified": False, "original_v1_reclassified": False}
    live_path = tmp_path / application.LIVE
    write(live_path, live)
    monkeypatch.setattr(application, "LIVE_V2_SHA256", hashlib.sha256(live_path.read_bytes()).hexdigest())
    write(live_path.with_suffix(".freeze.json"), live["freeze"])
    live_v3_path = tmp_path / application.LIVE_V3
    write(live_v3_path, {**live, "original_v2_reclassified": False})
    write(live_v3_path.with_suffix(".freeze.json"), live["freeze"])
    write(tmp_path / application.INSTALL, {"artificial": "installation"})
    write(tmp_path / application.QUICKSTART, {"artificial": "quickstart"})
    write(tmp_path / application.PUBLIC_INSTALL, {"artificial": "public installation"})
    write(tmp_path / application.PUBLIC_QUICKSTART, {"artificial": "public quickstart"})
    summary = {"all_planned_checks_pass": False, "live_turns_completed": 1,
               "live_turns_passed": 0}
    monkeypatch.setattr(validation, "validate_receipt", lambda *a, **kw: deepcopy(summary))
    actual_import = application.importlib.import_module
    monkeypatch.setattr(application.importlib, "import_module", lambda name: validation
                        if name == "research.softwarex.live_client_v3.validation" else actual_import(name))
    monkeypatch.setattr(quickstart_check, "validate_installation_receipt", lambda *a: {"passed": True, "source_commit": "a" * 40}, raising=False)
    monkeypatch.setattr(quickstart_check, "validate_saved_receipt", lambda *a: {"passed": True, "source_commit": "a" * 40}, raising=False)
    monkeypatch.setattr(application, "source_inputs", lambda *a: [{"path": "artificial", "bytes": 0, "sha256": "a" * 64}])
    return tmp_path, live_path, live, summary


def test_reconciled_failure_is_not_relabelled_success(sample):
    root, _, _, _ = sample
    result = application.build(root)
    assert result["completed"] is True
    assert result["model_backed_application"]["all_planned_checks_pass"] is False
    assert result["model_backed_application"]["live_turns_passed"] == 0
    assert result["model_backed_application_v3"]["all_planned_checks_pass"] is False
    assert result["original_live_trial"]["preserved_without_reclassification"] is True
    assert result["interpretation"]["model_and_quickstart_installations_are_distinct"] is True
    assert result["interpretation"]["independent_human_users"] == 0
    assert result["interpretation"]["acceptance_probability_estimated"] is False
    assert result["public_guide_quickstart"]["validation"]["passed"] is True


@pytest.mark.parametrize("mode", ["original", "freeze", "authority", "runtime", "reclassification"])
def test_changed_original_or_scope_cannot_reconcile(sample, mode):
    root, live_path, live, _ = sample
    if mode == "original":
        (root / application.ORIGINAL).write_bytes(b"Changed previous result")
    elif mode == "freeze":
        write(live_path.with_suffix(".freeze.json"), {"changed": True})
    else:
        field = {"authority": "real_repository_authorized", "runtime": "runtime_modified",
                 "reclassification": "original_v1_reclassified"}[mode]
        live[field] = True
        write(live_path, live)
    with pytest.raises(ValueError):
        application.build(root)


@pytest.mark.parametrize("field", ["real_repository_authorized", "runtime_modified", "original_v1_reclassified", "original_v2_reclassified"])
def test_v3_cannot_expand_scope_or_reclassify_adverse_records(sample, field):
    root, _, live, _ = sample
    write(root / application.LIVE_V3, {**live, "original_v2_reclassified": False, field: True})
    with pytest.raises(ValueError):
        application.build(root)


def test_public_quickstart_must_have_matching_installation(sample, monkeypatch):
    root, _, _, _ = sample
    monkeypatch.setattr(quickstart_check, "validate_saved_receipt", lambda *a: {"passed": True, "source_commit": "b" * 40})
    with pytest.raises(ValueError, match="commits differ"):
        application.build(root)


def test_failed_installation_cannot_be_called_completed(sample, monkeypatch):
    root, _, _, _ = sample
    monkeypatch.setattr(quickstart_check, "validate_installation_receipt", lambda *a: {"passed": False, "source_commit": "a" * 40})
    with pytest.raises(ValueError, match="passing installation"):
        application.build(root)


@pytest.mark.parametrize("which", ["live", "installation", "quickstart"])
def test_validator_failure_is_not_silently_omitted(sample, monkeypatch, which):
    root, _, _, _ = sample
    def fail(*args, **kwargs):
        raise ValueError("artificial validation failure")
    owner, name = {"live": (validation, "validate_receipt"),
                   "installation": (quickstart_check, "validate_installation_receipt"),
                   "quickstart": (quickstart_check, "validate_saved_receipt")}[which]
    monkeypatch.setattr(owner, name, fail)
    with pytest.raises(ValueError, match="artificial validation"):
        application.build(root)
