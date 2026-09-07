"""Artificial publication-test receipts; no model or subprocess execution."""
from copy import deepcopy
import hashlib
import json

import pytest

from research.softwarex import build_readiness as readiness


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def binding(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def save_receipt(context, *, public=True):
    raw = (json.dumps(context["receipt"], sort_keys=True) + "\n").encode()
    relative = readiness.EXTENSION_TESTS + "/receipt.json"
    put(context["root"] / relative, raw)
    if public:
        put(context["release"] / relative, raw)


@pytest.fixture
def extension(tmp_path, monkeypatch):
    root, release = tmp_path / "source", tmp_path / "public"
    monkeypatch.setattr(readiness, "ROOT", root)
    source = "research/softwarex/tests/test_fixture_only.py"
    code = b"# Artificial source identity, not a real executed test.\n"
    xml = (b'<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
           b'<testcase name="one"/><testcase name="two"/></testsuite></testsuites>')
    outputs = {"tests.xml": xml, "pytest.log": b"Artificial fixture: two cases.\n"}
    for directory in (root, release):
        put(directory / source, code)
        for name, raw in outputs.items():
            put(directory / readiness.EXTENSION_TESTS / name, raw)
    rows = [{"path": source, **binding(code)}]
    receipt = {
        "schema": "zerorun.softwarex-publication-tests.v1",
        "passed": True, "returncode": 0, "failure": None,
        "source_unchanged": True, "source_before": rows,
        "source_after": deepcopy(rows),
        "output_files": {name: binding(raw) for name, raw in outputs.items()},
        "junit_counts": {"tests": 2, "failures": 0, "errors": 0, "skipped": 0},
        "testcases_present": 2, "scope": "Artificial unit fixture only",
    }
    context = {"root": root, "release": release, "source": source,
               "receipt": receipt, "xml": xml}
    save_receipt(context)
    return context


def test_matching_source_outputs_and_receipt_pass(extension):
    result = readiness.check_extension_tests(extension["release"])
    assert result["receipt"] == readiness.EXTENSION_TESTS + "/receipt.json"
    assert result["counts"] == extension["receipt"]["junit_counts"]
    assert result["source_files_bound"] == 1
    assert result["scope"] == "Artificial unit fixture only"


@pytest.mark.parametrize("mode", [
    "local-source", "public-source", "public-receipt", "failed-receipt",
    "output-bytes", "junit-count", "junit-failure-node", "source-after",
])
def test_drift_or_failure_never_becomes_ready(extension, mode):
    receipt = extension["receipt"]
    if mode in {"local-source", "public-source"}:
        directory = extension["root"] if mode == "local-source" else extension["release"]
        put(directory / extension["source"], b"Changed after the artificial test.\n")
    elif mode == "public-receipt":
        receipt["scope"] = "Changed only in the local receipt"
        save_receipt(extension, public=False)
    elif mode == "output-bytes":
        put(extension["root"] / readiness.EXTENSION_TESTS / "pytest.log", b"Changed log\n")
    elif mode == "junit-failure-node":
        raw = extension["xml"].replace(b'<testcase name="one"/>',
                                       b'<testcase name="one"><failure/></testcase>')
        for directory in (extension["root"], extension["release"]):
            put(directory / readiness.EXTENSION_TESTS / "tests.xml", raw)
        receipt["output_files"]["tests.xml"] = binding(raw)
        save_receipt(extension)
    else:
        if mode == "failed-receipt":
            receipt["failure"] = {"type": "TimeoutExpired", "message": "Fixture timeout"}
        elif mode == "junit-count":
            receipt["junit_counts"]["tests"] = 3
        else:
            receipt["source_after"][0]["sha256"] = "0" * 64
        save_receipt(extension)
    with pytest.raises(ValueError):
        readiness.check_extension_tests(extension["release"])
    assert not (extension["root"] / "research/softwarex/generated/final-readiness.json").exists()


@pytest.fixture
def application(tmp_path, monkeypatch):
    """Artificial summaries exercise binding logic, not live experimental claims."""
    root, release = tmp_path / "application-source", tmp_path / "application-public"
    monkeypatch.setattr(readiness, "ROOT", root)
    relative = "research/softwarex/generated/application-evidence-v1.json"
    source = "research/softwarex/artificial_application_fixture.py"
    code = b"# Artificial readiness test input, never a model experiment.\n"
    summary = {
        "schema": "zerorun.softwarex-application-evidence.v1", "completed": True,
        "clean_installation": {"validation": {"passed": True}},
        "account_free_quickstart": {"validation": {"passed": True}},
        "public_guide_installation": {"validation": {"passed": True}},
        "public_guide_quickstart": {"validation": {"passed": True}},
        "model_backed_application": {"functional_lifecycle_pass": False, "recorded_outcome": "retained adverse fixture"},
        "model_backed_application_v3": {"functional_lifecycle_pass": False, "recorded_outcome": "retained adverse fixture"},
        "interpretation": {
            "model_and_quickstart_installations_are_distinct": True, "same_frozen_runtime_required": True,
            "independent_human_users": 0, "autonomous_issue_resolution_study": False,
            "workflow_speedup_established": False, "acceptance_probability_estimated": False,
        },
    }
    rows = [{"path": source, **binding(code)}]
    calls = []
    def independently_build(candidate_root):
        calls.append(candidate_root)
        return deepcopy(summary)
    monkeypatch.setattr(readiness.application_builder, "build", independently_build)
    monkeypatch.setattr(readiness.application_builder, "source_inputs", lambda candidate_root: deepcopy(rows), raising=False)
    for directory in (root, release):
        put(directory / source, code)
        put(directory / relative, json.dumps(summary).encode())
    return {"root": root, "release": release, "relative": relative, "source": source,
            "summary": summary, "paper": {"application": deepcopy(summary)}, "rows": rows, "calls": calls}


def test_application_is_recomputed_and_preserves_adverse_model_outcomes(application):
    result, files = readiness.check_application_evidence(application["release"], application["paper"])
    assert application["calls"] == [application["root"]]
    assert result == application["summary"] and files == 1
    assert result["model_backed_application"]["functional_lifecycle_pass"] is False
    assert result["model_backed_application_v3"]["functional_lifecycle_pass"] is False


@pytest.mark.parametrize("mode", [
    "stale-saved-summary", "stale-paper-summary", "local-source", "public-source", "public-summary",
    "duplicate-source", "empty-sources", "outside-source", "wrong-source-size", "boolean-source-size",
    "wrong-source-hash", "incomplete-summary", "source-list-not-rows",
])
def test_application_drift_or_missing_bindings_cannot_become_ready(application, monkeypatch, mode):
    root, release, summary = application["root"], application["release"], application["summary"]
    if mode == "stale-saved-summary":
        changed = dict(summary, changed_after_validation=True)
        put(root / application["relative"], json.dumps(changed).encode())
    elif mode == "stale-paper-summary":
        application["paper"]["application"]["changed_after_validation"] = True
    elif mode in {"local-source", "public-source"}:
        put((root if mode == "local-source" else release) / application["source"], b"Changed input bytes\n")
    elif mode == "public-summary":
        put(release / application["relative"], b"{}\n")
    elif mode == "duplicate-source":
        application["rows"].append(deepcopy(application["rows"][0]))
    elif mode == "empty-sources":
        application["rows"].clear()
    elif mode == "outside-source":
        application["rows"][0]["path"] = "../outside.py"
    elif mode == "wrong-source-size":
        application["rows"][0]["bytes"] += 1
    elif mode == "boolean-source-size":
        application["rows"][0]["bytes"] = True
    elif mode == "wrong-source-hash":
        application["rows"][0]["sha256"] = "0" * 64
    elif mode == "source-list-not-rows":
        monkeypatch.setattr(readiness.application_builder, "source_inputs", lambda root: [None])
    else:
        summary["completed"] = False
        application["paper"] = {"application": deepcopy(summary)}
        for directory in (root, release):
            put(directory / application["relative"], json.dumps(summary).encode())
    with pytest.raises(ValueError):
        readiness.check_application_evidence(release, application["paper"])
    assert not (root / "research/softwarex/generated/final-readiness.json").exists()


@pytest.mark.parametrize("name", ["clean_installation", "account_free_quickstart",
                                  "public_guide_installation", "public_guide_quickstart"])
def test_application_requires_actual_installation_and_quickstart_validation(application, name):
    application["summary"][name]["validation"]["passed"] = False
    application["paper"]["application"] = deepcopy(application["summary"])
    for directory in (application["root"], application["release"]):
        put(directory / application["relative"], json.dumps(application["summary"]).encode())
    with pytest.raises(ValueError, match="passing installation/quickstart"):
        readiness.check_application_evidence(application["release"], application["paper"])


@pytest.mark.parametrize("field,value", [
    ("model_and_quickstart_installations_are_distinct", False),
    ("same_frozen_runtime_required", False), ("independent_human_users", 5),
    ("independent_human_users", False), ("autonomous_issue_resolution_study", True),
    ("workflow_speedup_established", True), ("acceptance_probability_estimated", True),
])
def test_application_scope_cannot_be_promoted_to_unobserved_claims(application, field, value):
    application["summary"]["interpretation"][field] = value
    application["paper"]["application"] = deepcopy(application["summary"])
    for directory in (application["root"], application["release"]):
        put(directory / application["relative"], json.dumps(application["summary"]).encode())
    with pytest.raises(ValueError, match="exceeds its observed scope"):
        readiness.check_application_evidence(application["release"], application["paper"])


def test_application_validator_failure_is_not_replaced_with_saved_summary(application, monkeypatch):
    def invalid(root):
        raise ValueError("raw application receipt failed independent reconciliation")
    monkeypatch.setattr(readiness.application_builder, "build", invalid)
    with pytest.raises(ValueError, match="raw application receipt failed"):
        readiness.check_application_evidence(application["release"], application["paper"])
