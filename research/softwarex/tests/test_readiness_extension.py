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
