"""Offline fixtures, not model runs or empirical application outcomes."""
from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validation as v
import run as runner

ROOT = "/tmp/prospective-fixture/repository"
TRUST = "/tmp/prospective-fixture/external-authority"
KEY = "a" * 64


def response(status="HIT_REUSED"):
    return {"isError": False, "payload": {"task": v.TASK, "status": status, "exit_code": 0,
        "cache_key": KEY, "restored_outputs": [], "verified": status == "VERIFY_MATCH",
        "stdout_tail": "", "stderr_tail": "", "mode": "reuse"}}


def doctor():
    return {"isError": False, "payload": {"root": ROOT, "ok": True, "manifest_authorized": True,
        "manifest_version": 2, "mode": "task-reuse", "reuse_ready": True, "task_reuse_ready": True,
        "tasks": [{"task": v.TASK, "status": "CACHEABLE", "reason": None, "cache_key": KEY}]}}


def encoded(raw, *, returncode=0, stderr=b"", **kwargs):
    output = {"returncode": returncode, "timed_out": False, "stdout_truncated": False, "stderr_truncated": False}
    for name, data in (("stdout", raw), ("stderr", stderr)):
        output.update({name + "_base64": base64.b64encode(data).decode(), name + "_bytes": len(data), name + "_sha256": v.sha(data)})
    return dict(output, **kwargs)


def events(payload, *, tool="run_tests", arguments=None, answer=None, commentary=True):
    arguments = arguments or {"task": v.TASK, "root": ROOT, "verify": False}
    result = [{"type": "thread.started", "thread_id": "test"}, {"type": "turn.started"}]
    if commentary:
        result.append({"type": "item.completed", "item": {"id": "pre", "type": "agent_message", "text": "I will inspect the response."}})
    if tool:
        call = {"id": "call", "type": "mcp_tool_call", "server": "zerorun", "tool": tool, "arguments": arguments,
                "status": "in_progress", "error": None, "result": None}
        result.append({"type": "item.started", "item": deepcopy(call)})
        call.update(status="completed", result={"structured_content": payload["payload"],
            "content": [{"type": "text", "text": v.canonical(payload["payload"]).decode()}], "isError": payload["isError"]})
        result.append({"type": "item.completed", "item": call})
    if answer is None:
        answer = v.oracle(payload, doctor=tool == "doctor")
    result.append({"type": "item.completed", "item": {"id": "final", "type": "agent_message", "text": v.canonical(answer).decode()}})
    result.append({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}})
    return result


def raw_events(rows):
    return b"".join(v.canonical(row) + b"\n" for row in rows)


def stage(name="hit", *, answer=None):
    tool, args = runner.plan(name, ROOT)
    if name == "doctor":
        p = doctor()
    elif name in v.CORE + v.CHOICES:
        p = response({"miss": "MISS_EXECUTED", "hit": "HIT_REUSED", "verify": "VERIFY_MATCH", "accept_prior": "HIT_REUSED", "require_fresh": "VERIFY_MATCH"}[name])
    else:
        p = dict(runner.controlled_cases({"miss": response("MISS_EXECUTED"), "hit": response(), "verify": response("VERIFY_MATCH")}))[name]
    diagnostic = name == v.CASES[-1]
    if answer is None:
        answer = v.oracle(p, doctor=name == "doctor", diagnostics=diagnostic)
    prompt = runner.prompt_for(name, args, None if tool else p)
    item = {"name": name, "tool": tool, "expected_arguments": args, "diagnostics_required": diagnostic,
            "input_response": None if tool else p, "prompt": prompt, "prompt_utf8_sha256": v.sha(prompt.encode()),
            "execution_root_before": {"entries": []}, "execution_root_after": {"entries": []}, "execution_root_unchanged": True,
            "jsonl": encoded(raw_events(events(p, tool=tool, arguments=args, answer=answer))),
            "command": ["/codex", "--ignore-user-config", "--strict-config", "--config",
                        "mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT=" + json.dumps(TRUST) if tool else "mcp_servers={}", "-"]}
    item["judgment"] = v.grade(item)
    return item


@pytest.mark.parametrize("name", v.CORE + v.CHOICES + v.CASES)
def test_fixed_positive_offline_cases(name):
    judgment = stage(name)["judgment"]
    assert judgment["protocol_pass"] and judgment["tool_result_pass"] and judgment["semantic_pass"]


def test_pre_call_commentary_is_not_final_failure():
    judgment = stage()["judgment"]
    assert judgment["analysis"]["commentary"] == ["I will inspect the response."]
    assert judgment["semantic_pass"]


def test_wrong_semantics_are_separate_from_protocol_and_tool():
    judgment = stage(answer=v.oracle(response("MISS_EXECUTED")))["judgment"]
    assert judgment["protocol_pass"] and judgment["tool_result_pass"] and not judgment["semantic_pass"]


@pytest.mark.parametrize("mutation", ["missing_newline", "duplicate_call", "wrong_tool", "wrong_args", "extra_tool", "after_commentary", "wrong_order", "text_mismatch", "error_mismatch", "failed_turn"])
def test_bad_transcripts_rejected(mutation):
    item = stage()
    rows = events(response())
    if mutation == "missing_newline":
        raw = raw_events(rows)[:-1]
    else:
        if mutation == "duplicate_call":
            rows[4:4] = deepcopy(rows[3:5])
        elif mutation == "wrong_tool":
            rows[3]["item"]["tool"] = "doctor"
        elif mutation == "wrong_args":
            rows[3]["item"]["arguments"]["verify"] = True
        elif mutation == "extra_tool":
            rows.insert(5, {"type": "item.completed", "item": {"id": "shell", "type": "command_execution"}})
        elif mutation == "after_commentary":
            rows.insert(5, {"type": "item.completed", "item": {"id": "after", "type": "agent_message", "text": "extra after result"}})
        elif mutation == "wrong_order":
            rows[0], rows[1] = rows[1], rows[0]
        elif mutation == "text_mismatch":
            rows[4]["item"]["result"]["content"][0]["text"] = "{}"
        elif mutation == "error_mismatch":
            rows[4]["item"]["result"]["is_error"] = True
        elif mutation == "failed_turn":
            rows[-1]["type"] = "turn.failed"
        raw = raw_events(rows)
    item["jsonl"] = encoded(raw)
    assert not v.grade(item)["protocol_pass"]


@pytest.mark.parametrize("flag", ["timed_out", "stdout_truncated", "stderr_truncated"])
def test_capture_incompleteness_fails(flag):
    item = stage()
    item["jsonl"][flag] = True
    assert not v.grade(item)["protocol_pass"]


def test_nonzero_process_fails():
    item = stage()
    item["jsonl"]["returncode"] = 1
    assert not v.grade(item)["protocol_pass"]


def test_directory_boolean_cannot_hide_mutation():
    item = stage()
    item["execution_root_after"] = {"entries": ["unexpected"]}
    assert not v.grade(item)["protocol_pass"]


def test_nonempty_initial_directory_fails():
    item = stage()
    item["execution_root_before"] = item["execution_root_after"] = {"entries": ["existing"]}
    assert not v.grade(item)["protocol_pass"]


@pytest.mark.parametrize("value", [0, 1, "false", None])
def test_explicit_error_must_be_boolean(value):
    p = response()
    p["isError"] = value
    with pytest.raises(ValueError):
        v.oracle(p)


@pytest.mark.parametrize("field,value", [("exit_code", True), ("verified", 0), ("verified", True), ("stdout_tail", "old output"), ("cache_key", "bad"), ("status", "MISS_FAILED")])
def test_contradictory_success_rejected(field, value):
    p = response()
    p["payload"][field] = value
    with pytest.raises(ValueError):
        v.oracle(p)


def test_fresh_diagnostics_rejects_prior_success():
    expected = v.oracle(response(), diagnostics=True)
    assert expected == {"classification": "PRIOR_SUCCESS", "accepted_success": False,
                        "fresh_evidence": False, "next_action": "request_fresh_execution"}


def test_decision_optional_verify_false_is_allowed_only_there():
    item = stage("accept_prior")
    args = {"task": v.TASK, "root": ROOT}
    item["jsonl"] = encoded(raw_events(events(response(), arguments=args)))
    assert v.grade(item)["semantic_pass"]
    item["name"] = "hit"
    assert not v.grade(item)["protocol_pass"]


def test_cli_omitted_error_field_uses_termination_status():
    rows = events(response())
    del rows[4]["item"]["result"]["isError"]
    analyzed = v.analyze_events(raw_events(rows), tool="run_tests", arguments=runner.plan("hit", ROOT)[1])
    assert analyzed["response"]["isError"] is False
    rows[4]["item"]["status"] = "failed"
    analyzed = v.analyze_events(raw_events(rows), tool="run_tests", arguments=runner.plan("hit", ROOT)[1])
    with pytest.raises(ValueError):
        v.oracle(analyzed["response"])


def test_no_tool_case_rejects_actual_tool():
    item = stage(v.CASES[1])
    item["jsonl"] = encoded(raw_events(events(response())))
    assert not v.grade(item)["protocol_pass"]


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'])
def test_strict_json_rejects_ambiguity(raw):
    with pytest.raises(ValueError):
        v.strict_json(raw)


def sealed_partial(tmp_path, stages):
    freeze = {"schema": "zerorun.live-client-v2.freeze", "sources": v.source_bindings(Path(__file__).parent),
              "source_commit": v.COMMIT, "public_manifest_sha256": v.MANIFEST_SHA256,
              "codex_install_receipt_sha256": runner.INSTALL_RECEIPT_SHA, "codex_install_source_commit": runner.INSTALL_SOURCE,
              "stage_order": list(v.CORE + v.CHOICES + v.CASES), "maximum_turns": 12,
              "timeout_seconds": v.TIMEOUT, "output_limit_bytes": v.LIMIT,
              "explicit_approvals": {"synthetic_only_authority": True, "remote_synthetic_metadata": True}, "runtime_image": runner.IMAGE}
    data = {"schema": v.SCHEMA, "source_commit": v.COMMIT, "public_manifest_sha256": v.MANIFEST_SHA256,
            "freeze": freeze, "freeze_sha256": v.sha(v.canonical(freeze)), "stages": stages,
            "synthetic_path": ROOT, "explicit_trust_path": TRUST, "fresh_oracles": [], "postflight": {"passed": False},
            "real_repository_authorized": False, "runtime_modified": False, "runtime_acquisition_attempted": False, "original_v1_reclassified": False}
    data["summary"] = v.summarize(data)
    return data


def save_receipt(tmp_path, data):
    data["evidence_payload_sha256"] = v.sha(v.canonical({key: value for key, value in data.items() if key != "evidence_payload_sha256"}))
    path = tmp_path / "receipt.json"
    path.write_bytes(v.canonical(data))
    return path


def test_partial_failed_receipt_is_valid_not_passed(tmp_path):
    data = sealed_partial(tmp_path, [stage("doctor", answer={})])
    summary = v.validate_receipt(save_receipt(tmp_path, data))
    assert summary["live_turns_completed"] == 1 and not summary["all_planned_checks_pass"]


@pytest.mark.parametrize("mutation", ["reorder", "bad_prompt", "wrong_root", "diagnostics", "raw_hash", "stored_judgment", "continuation", "source_binding", "false_summary"])
def test_sealed_receipt_tampering_rejected(tmp_path, mutation):
    data = sealed_partial(tmp_path, [stage("doctor", answer={})])
    item = data["stages"][0]
    if mutation == "reorder":
        item["name"] = "hit"
    elif mutation == "bad_prompt":
        item["prompt"] = "gold answer"
        item["prompt_utf8_sha256"] = v.sha(item["prompt"].encode())
    elif mutation == "wrong_root":
        item["expected_arguments"]["root"] = "/elsewhere"
    elif mutation == "diagnostics":
        item["diagnostics_required"] = True
    elif mutation == "raw_hash":
        item["jsonl"]["stdout_sha256"] = "0" * 64
    elif mutation == "stored_judgment":
        item["judgment"]["semantic_pass"] = True
    elif mutation == "continuation":
        data["stages"].append(stage("miss"))
    elif mutation == "source_binding":
        data["freeze"]["sources"][0]["sha256"] = "0" * 64
    elif mutation == "false_summary":
        data["summary"]["all_planned_checks_pass"] = True
    with pytest.raises(ValueError):
        v.validate_receipt(save_receipt(tmp_path, data))


def test_fresh_oracle_recomputed_from_streams():
    suffix = ["run", "--rm", "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
              "--platform=linux/amd64", "--mount", f"type=bind,src={ROOT},dst=/workspace,readonly", "--workdir=/workspace",
              "--entrypoint=python", runner.IMAGE, "fixture.py"]
    record = {"command": ["/usr/bin/docker"] + suffix, "streams": encoded(b""), "passed": True}
    assert v.fresh_oracle_pass(record, ROOT)
    record["streams"] = encoded(b"", returncode=73)
    assert not v.fresh_oracle_pass(record, ROOT)
    record["command"][4] = "--network=host"
    with pytest.raises(ValueError):
        v.fresh_oracle_pass(record, ROOT)


def test_all_true_empty_identity_dictionary_is_not_pass():
    assert v.identity_pass({"orchestration_receipt": {"identity_unchanged": {}}, "postflight": {"passed": True}}) is False


def test_fixed_interpretation_inputs_bind_captured_values():
    captured = {"miss": response("MISS_EXECUTED"), "hit": response(), "verify": response("VERIFY_MATCH")}
    cases = dict(runner.controlled_cases(captured))
    assert cases["captured_hit"] is captured["hit"]
    assert cases["captured_hit_need_diagnostics"] is captured["hit"]
    assert cases["controlled_failure"]["payload"]["status"] == "MISS_FAILED"


def test_decision_prompts_do_not_supply_gold_verify_or_status():
    for name in v.CHOICES:
        prompt = runner.prompt_for(name, runner.plan(name, ROOT)[1])
        assert "verify=" not in prompt and "HIT_REUSED" not in prompt and "VERIFY_MATCH" not in prompt


def test_explicit_trust_config_and_no_tool_config():
    fake = SimpleNamespace(_codex_command=lambda *a, **kw: ["codex", "exec", "--ignore-user-config", "--config", "mcp_servers.zerorun.command=tool", "-"],
                           smoke=SimpleNamespace(_toml_string=json.dumps))
    live = runner.command_for(fake, Path("/codex"), Path("/zerorun"), root=Path(ROOT), tool="doctor", trust=Path(TRUST))
    assert "mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT=" + json.dumps(str(Path(TRUST))) in live
    no_tool = runner.command_for(fake, Path("/codex"), Path("/zerorun"), root=Path(ROOT), tool=None, trust=Path(TRUST))
    assert "mcp_servers={}" in no_tool and not any("ZERORUN_TRUST_ROOT" in x for x in no_tool)
