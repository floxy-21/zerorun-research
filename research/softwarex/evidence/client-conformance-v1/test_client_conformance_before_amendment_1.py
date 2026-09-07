from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research.softwarex import client_conformance as client


def payload(status="HIT_REUSED", code=0):
    return {"status": status, "exit_code": code, "cache_key": "a" * 64,
            "task": "tests", "verified": status == "VERIFY_MATCH",
            "restored_outputs": [], "mode": "reuse", "stdout_tail": "", "stderr_tail": ""}


@pytest.mark.parametrize("status,classification,fresh", [
    ("HIT_REUSED", "PRIOR_SUCCESS", False),
    ("MISS_EXECUTED", "FRESH_SUCCESS", True),
    ("VERIFY_MATCH", "FRESH_SUCCESS", True),
])
def test_success_classes_preserve_actual_evidence_meaning(status, classification, fresh):
    result = client.consume(client.envelope(payload(status)))
    assert result["classification"] == classification
    assert result["accepted_success"] is True
    assert result["fresh_evidence"] is fresh


def test_reused_result_cannot_satisfy_fresh_transcript_request():
    result = client.consume(client.envelope(payload()), fresh_transcript_required=True)
    assert result["classification"] == "PRIOR_SUCCESS"
    assert result["accepted_success"] is False
    assert result["fresh_evidence"] is False
    assert result["next_action"] == "request_fresh_execution"


def test_fresh_failure_is_not_success_despite_reuse_mode():
    result = client.consume(client.envelope(payload("MISS_FAILED", 5), error=True))
    assert result["classification"] == "FRESH_FAILURE"
    assert result["fresh_evidence"] is True
    assert result["accepted_success"] is False
    assert result["next_action"] == "inspect_fresh_failure"


@pytest.mark.parametrize("field,value", [
    ("exit_code", 5), ("exit_code", False), ("exit_code", -1),
    ("exit_code", "0"), ("exit_code", 0.0), ("verified", True),
    ("verified", 0), ("cache_key", "abcd"), ("task", ""),
    ("restored_outputs", ["generated.txt"]), ("stdout_tail", "old transcript"),
    ("stderr_tail", "old error"),
])
def test_contradictory_prior_success_never_passes(field, value):
    record = payload()
    record[field] = value
    result = client.consume(client.envelope(record))
    assert result["classification"] == "PROTOCOL_ERROR"
    assert result["accepted_success"] is False


@pytest.mark.parametrize("record,error", [
    (payload("MISS_FAILED", 0), False),
    (payload("MISS_FAILED", 5), False),
    (payload("MISS_EXECUTED", 3), True),
    (payload(), True),
    ({"status": "ERROR", "error": "refused"}, False),
])
def test_inconsistent_failure_success_error_flags(record, error):
    assert client.consume(client.envelope(record, error=error))["accepted_success"] is False


def test_missing_verification_flag_rejected():
    record = payload("VERIFY_MATCH")
    record["verified"] = False
    assert client.consume(client.envelope(record))["classification"] == "PROTOCOL_ERROR"


@pytest.mark.parametrize("status", ["BYPASS", "UNTRUSTED", "UNKNOWN", "PYTEST_INCREMENTAL_PASS", "FUTURE_SUCCESS"])
def test_unsupported_status_does_not_become_success(status):
    result = client.consume(client.envelope(payload(status)))
    assert result["classification"] == "REFUSED"
    assert result["accepted_success"] is False


def test_mode_only_is_not_a_verdict():
    result = client.consume(client.envelope({"mode": "reuse", "exit_code": 0}))
    assert result["classification"] == "PROTOCOL_ERROR"
    assert result["accepted_success"] is False


def test_explicit_refusal_is_distinct_from_malformed_protocol():
    result = client.consume(client.envelope({"status": "ERROR", "error": "authority absent"}, error=True))
    assert result["classification"] == "REFUSED"
    assert result["next_action"] == "stop_or_request_operator"


@pytest.mark.parametrize("mutation", ["mismatch", "duplicate_text", "missing_text", "extra_text", "wrong_id", "boolean_id", "wrong_version", "missing_error_flag", "string_error_flag", "both_result_error"])
def test_ambiguous_envelopes_fail_closed(mutation):
    response = client.envelope(payload())
    if mutation == "mismatch":
        response["result"]["content"][0]["text"] = "{}"
    elif mutation == "duplicate_text":
        response["result"]["content"][0]["text"] = '{"status":"HIT_REUSED","status":"MISS_FAILED"}'
    elif mutation == "missing_text":
        response["result"]["content"] = []
    elif mutation == "extra_text":
        response["result"]["content"] *= 2
    elif mutation == "wrong_id":
        response["id"] = 2
    elif mutation == "boolean_id":
        response["id"] = True
    elif mutation == "wrong_version":
        response["jsonrpc"] = "1.0"
    elif mutation == "missing_error_flag":
        del response["result"]["isError"]
    elif mutation == "string_error_flag":
        response["result"]["isError"] = "false"
    else:
        response["error"] = {"code": -1, "message": "failure"}
    result = client.consume(response)
    assert result["classification"] == "PROTOCOL_ERROR"
    assert result["accepted_success"] is False


def test_top_level_rpc_error_never_satisfies_test_request():
    result = client.consume({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unknown method"}})
    assert result["classification"] == "PROTOCOL_ERROR"
    assert result["accepted_success"] is False


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}', b'\xff', b'x' * (client.LIMIT + 1)])
def test_json_loader_rejects_ambiguous_nonfinite_or_unbounded_input(raw):
    with pytest.raises(ValueError):
        client.strict_json(raw)


def test_structured_boolean_is_not_equal_to_integer_text():
    response = client.envelope(payload())
    altered = copy.deepcopy(response["result"]["structuredContent"])
    altered["exit_code"] = False
    response["result"]["content"][0]["text"] = json.dumps(altered)
    assert client.consume(response)["classification"] == "PROTOCOL_ERROR"


def test_wrapping_real_api_record_is_explicit_and_nonmutating():
    source = {**payload(), "stdout": "", "stderr": ""}
    original = copy.deepcopy(source)
    wrapped = client.wrap_api_fixture(source)
    assert source == original
    assert wrapped["result"]["structuredContent"]["stdout_tail"] == ""
    assert client.consume(wrapped)["classification"] == "PRIOR_SUCCESS"


def test_all_preserved_case_inputs_are_included_without_replacement():
    root = Path(__file__).resolve().parents[3]
    cases = client.scripted_cases(root)
    assert len(cases) == 14
    assert len(client.SOURCE_RECORDS) == 4
    assert {case["category"] for case in cases} == set(client.CATEGORIES[:-1])
    for case in cases:
        assert case["decision"]["classification"] == case["expected_classification"]
        assert case["decision"]["accepted_success"] is case["expected_accepted_success"]


def test_write_once_preserves_existing_evidence(tmp_path):
    path = tmp_path / "record.json"
    client.write_once(path, {"first": True})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        client.write_once(path, {"replacement": True})
    assert path.read_bytes() == before


def test_environment_contains_no_ambient_api_keys_or_authority(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("GITHUB_TOKEN", "must-not-leak")
    monkeypatch.setenv("PYTHONPATH", "must-not-use")
    monkeypatch.setenv("ZERORUN_TRUST_ROOT", "must-not-use")
    root = tmp_path / "new-absent-authority"
    result = client.safe_environment(root)
    assert "OPENAI_API_KEY" not in result
    assert "GITHUB_TOKEN" not in result
    assert "PYTHONPATH" not in result
    assert result["ZERORUN_TRUST_ROOT"] == str(root)
    assert not root.exists()


def test_protocol_source_change_refuses_before_any_attempt(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "source_records", lambda _root: {"source": "first"})
    client.freeze(tmp_path, tmp_path)
    monkeypatch.setattr(client, "source_records", lambda _root: {"source": "changed"})
    with pytest.raises(ValueError, match="source binding mismatch"):
        client.run(tmp_path, tmp_path, Path("unused-python"))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["protocol.json"]


def test_freeze_never_overwrites_protocol(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "source_records", lambda _root: {})
    client.freeze(tmp_path, tmp_path)
    original = (tmp_path / "protocol.json").read_bytes()
    with pytest.raises(FileExistsError):
        client.freeze(tmp_path, tmp_path)
    assert (tmp_path / "protocol.json").read_bytes() == original
