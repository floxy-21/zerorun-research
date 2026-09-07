"""Offline checks only; these fixtures are not empirical model outcomes."""
from __future__ import annotations

from copy import deepcopy
import importlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

PACKAGE = "_zerorun_guided_v1_tests"
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(PACKAGE, Path(__file__).with_name("__init__.py"), submodule_search_locations=[str(Path(__file__).parent)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)
v = importlib.import_module(PACKAGE + ".validation")
runner = importlib.import_module(PACKAGE + ".run")
base, old = v.load_v3()
test_spec = importlib.util.spec_from_file_location("_guided_offline_frozen_test_helpers", Path(base.__file__).with_name("test_validation.py"))
fixtures = importlib.util.module_from_spec(test_spec)
test_spec.loader.exec_module(fixtures)
ROOT, TRUST = fixtures.ROOT, fixtures.TRUST


def setup():
    root_schema = {"type": "string", "description": "Repository path"}
    tools = [{"name": "run_tests", "inputSchema": {"type": "object", "properties": {"task": {"type": "string"},
        "root": root_schema, "verify": {"type": "boolean", "default": False}}, "required": ["task"], "additionalProperties": False}}]
    def result(p):
        return {"isError": p["isError"], "structuredContent": p["payload"], "content": [{"type": "text", "text": v.canonical(p["payload"]).decode()}]}
    replies = [{"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "zerorun", "version": "0.5.1"}}},
               {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}},
               {"jsonrpc": "2.0", "id": 3, "result": result(fixtures.doctor())},
               {"jsonrpc": "2.0", "id": 4, "result": result(fixtures.response("MISS_EXECUTED"))}]
    row = {"model_called": False, "command": ["/zerorun", "mcp-server"], "requests": v.setup_requests(ROOT),
           "streams": fixtures.encoded(b"".join(v.canonical(reply) + b"\n" for reply in replies))}
    row["analysis"] = v.setup_analysis(row, ROOT, old)
    return row


def guided_stage(name, *, answer=None):
    row = fixtures.stage(name, answer=answer)
    card = (Path(__file__).parent.parent / "CLIENT_API_CARD.md").read_text(encoding="utf-8")
    row["prompt"] = v.guided_prompt(base, card, name, row["expected_arguments"], row["input_response"])
    row["prompt_utf8_sha256"] = v.sha(row["prompt"].encode())
    return row


def data(stages):
    freeze = {"schema": "zerorun.guided-client.freeze.v1", "sources": v.source_bindings(Path(__file__).parent),
              "source_commit": old.COMMIT, "public_manifest_sha256": old.MANIFEST_SHA256,
              "frozen_v3_sources": v.FROZEN_V3, "prior_receipts": dict(base.PRIOR_TRIALS, v3=v.PRIOR_V3),
              "scoped_tool_preauthorization": base.SCOPED_CONSENT, "stage_order": list(old.CHOICES + old.CASES),
              "maximum_model_turns": 8, "timeout_seconds": old.TIMEOUT, "output_limit_bytes": old.LIMIT}
    value = {"schema": v.SCHEMA, "freeze": freeze, "freeze_sha256": v.sha(v.canonical(freeze)), "stages": stages,
             "synthetic_path": ROOT, "explicit_trust_path": TRUST, "setup": setup(), "identity": {}, "fresh_oracles": [],
             "prior_trials_reclassified": False, "runtime_modified": False, "real_repository_authorized": False}
    value["summary"] = v.summarize(value, old)
    return value


def save(path, value):
    value["freeze_sha256"] = v.sha(v.canonical(value["freeze"]))
    value["evidence_payload_sha256"] = v.sha(v.canonical({k: val for k, val in value.items() if k != "evidence_payload_sha256"}))
    path.write_bytes(v.canonical(value))
    return path


def test_setup_is_two_non_model_tool_calls():
    result = v.setup_analysis(setup(), ROOT, old)
    assert result["non_model_tool_calls"] == 2 and result["model_calls"] == 0
    assert result["seed"]["payload"]["status"] == "MISS_EXECUTED"


@pytest.mark.parametrize("mutation", ["extra_property", "no_default", "wrong_status", "wrong_key", "duplicate_reply", "bad_return", "wrong_plan"])
def test_setup_mutations_fail(mutation):
    row = setup()
    replies = [v.strict_json(line) for line in old.streams(row["streams"]).splitlines()]
    schema = replies[1]["result"]["tools"][0]["inputSchema"]
    if mutation == "extra_property":
        schema["properties"]["invented"] = {"type": "boolean"}
    elif mutation == "no_default":
        del schema["properties"]["verify"]["default"]
    elif mutation in {"wrong_status", "wrong_key"}:
        p = replies[3]["result"]["structuredContent"]
        p["status" if mutation == "wrong_status" else "cache_key"] = "HIT_REUSED" if mutation == "wrong_status" else "b" * 64
        replies[3]["result"]["content"][0]["text"] = v.canonical(p).decode()
    elif mutation == "duplicate_reply":
        replies.append(deepcopy(replies[-1]))
    elif mutation == "wrong_plan":
        row["requests"][-1]["params"]["arguments"]["verify"] = True
    row["streams"] = fixtures.encoded(b"".join(v.canonical(reply) + b"\n" for reply in replies), returncode=1 if mutation == "bad_return" else 0)
    with pytest.raises(ValueError):
        v.setup_analysis(row, ROOT, old)


@pytest.mark.parametrize("name", old.CHOICES + old.CASES)
def test_guided_card_keeps_grader_thresholds(name):
    result = old.grade(guided_stage(name))
    assert result["protocol_pass"] and result["tool_result_pass"] and result["semantic_pass"]


def test_neutral_api_card_has_no_case_answers():
    card = (Path(__file__).parent.parent / "CLIENT_API_CARD.md").read_text(encoding="utf-8")
    assert "`task`" in card and "`root`" in card and "`verify`" in card
    assert "default `false`" in card and "When true" in card
    assert not any(text in card for text in ("accept_prior", "require_fresh", "HIT_REUSED", "VERIFY_MATCH", "reuse_prior_success", "PRIOR_SUCCESS"))
    assert "fresh evidence of failure, not success" in card


def test_both_decisions_receive_identical_card():
    first = guided_stage("accept_prior")["prompt"].split("Frozen input-interface reference", 1)[1]
    second = guided_stage("require_fresh")["prompt"].split("Frozen input-interface reference", 1)[1]
    assert first == second


def test_partial_failed_decision_is_valid_not_success(tmp_path):
    value = data([guided_stage("accept_prior", answer={})])
    summary = v.validate_receipt(save(tmp_path / "receipt.json", value))
    assert summary["live_decision_turns_completed"] == 1 and summary["live_decision_turns_passed"] == 0
    assert not summary["all_planned_checks_pass"]


@pytest.mark.parametrize("mutation", ["card_removed", "old_core_added", "prior_hash", "scope", "semantic_regraded", "continued_after_failure", "continued_without_oracle"])
def test_guided_receipt_tampering_rejected(tmp_path, mutation):
    value = data([guided_stage("accept_prior", answer={})])
    if mutation == "card_removed":
        value["stages"][0]["prompt"] = "ignore documentation"
        value["stages"][0]["prompt_utf8_sha256"] = v.sha(b"ignore documentation")
    elif mutation == "old_core_added":
        value["stages"].insert(0, fixtures.stage("doctor"))
    elif mutation == "prior_hash":
        value["freeze"]["prior_receipts"]["v3"] = "0" * 64
    elif mutation == "scope":
        value["runtime_modified"] = True
    elif mutation == "semantic_regraded":
        value["stages"][0]["judgment"]["semantic_pass"] = True
    elif mutation == "continued_after_failure":
        value["stages"].append(guided_stage("require_fresh"))
    elif mutation == "continued_without_oracle":
        value["stages"] = [guided_stage("accept_prior"), guided_stage("require_fresh")]
    with pytest.raises(ValueError):
        v.validate_receipt(save(tmp_path / "receipt.json", value))


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}'])
def test_strict_json(raw):
    with pytest.raises(ValueError):
        v.strict_json(raw)


def test_guided_does_not_count_non_model_seed_as_model_miss():
    summary = v.summarize(data([]), old)
    assert summary["non_model_setup_tool_calls"] == 2 and summary["live_decision_turns_completed"] == 0
    assert summary["interpretation_cases_completed"] == 0


def test_empty_identity_not_success():
    assert not v.identity_pass({"identity": {}})
