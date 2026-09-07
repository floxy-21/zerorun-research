"""Offline diagnostic-contract fixtures; no authority, runtime, or model calls."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from research.softwarex import diagnose_mcp_authority as diagnostic


ROOT = "/tmp/synthetic-diagnostic/repository"
KEY = "a" * 64


def make_stage(name):
    if name.endswith("doctor"):
        positive = name == "explicit_doctor"
        payload = {"root": ROOT, "ok": positive, "manifest_authorized": positive,
            "mode": "task-reuse" if positive else "observe-only", "reuse_ready": positive,
            "task_reuse_ready": positive, "manifest_version": 2,
            "tasks": [{"task": "synthetic-lifecycle", "status": "CACHEABLE" if positive else "UNTRUSTED",
                       "cache_key": KEY if positive else None, "reason": None if positive else "no authority"}]}
        error = not positive
    else:
        payload = {"task": "synthetic-lifecycle", "status": {"miss": "MISS_EXECUTED", "hit": "HIT_REUSED", "verify": "VERIFY_MATCH"}[name],
                   "mode": "reuse", "exit_code": 0, "verified": name == "verify", "restored_outputs": [],
                   "stdout_tail": "", "stderr_tail": "", "cache_key": KEY}
        error = False
    result = {"isError": error, "structuredContent": payload,
              "content": [{"type": "text", "text": diagnostic.canonical(payload).decode()}]}
    responses = [{"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "zerorun", "version": "0.5.1"},
                  "protocolVersion": "2025-11-25"}}, {"jsonrpc": "2.0", "id": 2, "result": result}]
    stage = {"name": name, "requests": diagnostic.request_plan(name, ROOT), "explicit_trust_root": name != "negative_doctor",
             "responses": responses, "streams": {"returncode": 0, "timed_out": False}}
    refresh_streams(stage)
    return stage


def refresh_streams(stage):
    raw = b"".join(diagnostic.canonical(row) + b"\n" for row in stage["responses"])
    for name, value in (("stdout", raw), ("stderr", b"")):
        stage["streams"].update({name + "_base64": base64.b64encode(value).decode(),
            name + "_sha256": hashlib.sha256(value).hexdigest(), name + "_bytes": len(value), name + "_truncated": False})


@pytest.mark.parametrize("name", diagnostic.STAGES)
def test_fixed_stage_accepts_consistent_offline_fixture(name):
    result = diagnostic.validate_exchange(make_stage(name), ROOT)
    assert result["cache_key"] == (None if name == "negative_doctor" else KEY)


@pytest.mark.parametrize("mutation", ["return_bool", "timeout", "hash", "truncated", "requests", "intervention",
    "responses", "error", "exit_bool", "text", "verified", "status", "key", "tail", "server", "id_bool"])
def test_adverse_contracts_are_refused(mutation):
    stage = make_stage("hit")
    if mutation == "return_bool":
        stage["streams"]["returncode"] = False
    elif mutation == "timeout":
        stage["streams"]["timed_out"] = True
    elif mutation == "hash":
        stage["streams"]["stdout_sha256"] = "0" * 64
    elif mutation == "truncated":
        stage["streams"]["stdout_truncated"] = True
    elif mutation == "requests":
        stage["requests"][-1]["params"]["arguments"]["root"] = "/other"
    elif mutation == "intervention":
        stage["explicit_trust_root"] = False
    elif mutation == "responses":
        stage["responses"][-1]["result"]["isError"] = True
    else:
        result = stage["responses"][-1]["result"]
        payload = result["structuredContent"]
        if mutation == "error": result["isError"] = True
        elif mutation == "exit_bool": payload["exit_code"] = False
        elif mutation == "text": result["content"][0]["text"] = "{}"
        elif mutation == "verified": payload["verified"] = True
        elif mutation == "status": payload["status"] = "MISS_EXECUTED"
        elif mutation == "key": payload["cache_key"] = "not-a-key"
        elif mutation == "tail": payload["stdout_tail"] = "fresh output on a hit"
        elif mutation == "server": stage["responses"][0]["result"]["serverInfo"]["version"] = "different"
        elif mutation == "id_bool": stage["responses"][0]["id"] = True
        if mutation != "text": result["content"][0]["text"] = diagnostic.canonical(payload).decode()
        refresh_streams(stage)
    with pytest.raises(ValueError):
        diagnostic.validate_exchange(stage, ROOT)


def test_control_must_be_untrusted_not_any_error():
    stage = make_stage("negative_doctor")
    result = stage["responses"][-1]["result"]
    result["structuredContent"]["tasks"][0]["status"] = "UNSUPPORTED"
    result["content"][0]["text"] = diagnostic.canonical(result["structuredContent"]).decode()
    refresh_streams(stage)
    with pytest.raises(ValueError, match="control"):
        diagnostic.validate_exchange(stage, ROOT)


def test_original_evidence_and_frozen_protocol_bindings_are_real():
    directory = Path(diagnostic.__file__).parent
    result = diagnostic.bindings(directory, directory / "evidence/live-client-v1/receipt.json")
    assert result["source_commit"] == diagnostic.COMMIT


def test_requires_explicit_synthetic_authority_before_source_access(tmp_path):
    with pytest.raises(ValueError, match="acknowledgement"):
        diagnostic.main(["--source-root", str(tmp_path / "missing"), "--live-receipt", "missing.json",
                         "--zerorun-command", "never", "--zerorun-python-command", "never", "--output", "never.json"])


def test_duplicate_members_are_refused():
    with pytest.raises(ValueError, match="duplicate"):
        diagnostic.strict_json(b'{"id":1,"id":1}')


def test_saved_adverse_keeps_successful_negative_control(tmp_path, monkeypatch):
    stages = [make_stage("negative_doctor"), make_stage("explicit_doctor")]
    stages[1]["streams"]["timed_out"] = True
    receipt = {"schema": diagnostic.SCHEMA, "model_called": False, "codex_correction_tested": False,
        "real_repository_authorized": False, "original_live_receipt_sha256": diagnostic.LIVE_SHA256,
        "protocol_sha256": diagnostic.PROTOCOL_SHA256, "helper_sha256": "fake", "source_bindings": {},
        "synthetic": {"path": ROOT}, "stages": stages, "passed": False, "failure": {"message": "timeout"}}
    receipt["evidence_payload_sha256"] = diagnostic.adapter.canonical_hash(receipt)
    path = tmp_path / "receipt.json"
    path.write_bytes(diagnostic.canonical(receipt))
    monkeypatch.setattr(diagnostic, "bindings", lambda *_: {})
    monkeypatch.setattr(diagnostic.adapter, "digest", lambda *_: "fake")
    result = diagnostic.validate_saved_receipt(tmp_path, path)
    assert result["negative_control_passed"] is True
    assert result["passed"] is False
    assert result["stages_validated"] == 1
