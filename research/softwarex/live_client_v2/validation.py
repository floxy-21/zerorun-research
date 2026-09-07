"""Strict, read-only transcript and receipt validation for experiment v2."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

SCHEMA = "zerorun.softwarex-live-client.v2"
COMMIT = "ebf2884df12573d63f45813200e0675288d12096"
MANIFEST_SHA256 = "f7a01e6a16f32022ce686a4a213e073c83a8f77de82df0c9df357c9c31ab6564"
TASK = "synthetic-lifecycle"
LIMIT = 2 * 1024 * 1024
TIMEOUT = 120.0
CORE = ("doctor", "miss", "hit", "verify")
CHOICES = ("accept_prior", "require_fresh")
CASES = ("captured_fresh", "captured_hit", "captured_verify", "controlled_failure", "controlled_refusal", "captured_hit_need_diagnostics")
FIELDS = {"classification", "accepted_success", "fresh_evidence", "next_action"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def strict_json(raw):
    def pairs(rows):
        out = {}
        for key, value in rows:
            if key in out:
                raise ValueError("duplicate JSON key")
            out[key] = value
        return out
    def reject(value):
        raise ValueError("non-finite JSON value")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
    canonical(value)
    return value


def streams(record):
    for name in ("stdout", "stderr"):
        raw = base64.b64decode(record[name + "_base64"], validate=True)
        if len(raw) != record[name + "_bytes"] or sha(raw) != record[name + "_sha256"] or len(raw) > LIMIT:
            raise ValueError("raw stream binding differs")
        if type(record[name + "_truncated"]) is not bool:
            raise ValueError("invalid truncation flag")
    if type(record["timed_out"]) is not bool or type(record["returncode"]) is not int:
        raise ValueError("invalid process outcome")
    return base64.b64decode(record["stdout_base64"], validate=True)


def payload_from_call(item):
    result = item.get("result")
    if not isinstance(result, dict):
        raise ValueError("MCP call has no result")
    payload, content = result.get("structured_content"), result.get("content")
    if not isinstance(payload, dict) or not isinstance(content, list) or len(content) != 1:
        raise ValueError("MCP result representations absent")
    block = content[0]
    if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
        raise ValueError("invalid text representation")
    if canonical(strict_json(block["text"])) != canonical(payload):
        raise ValueError("MCP text and structured results differ")
    errors = [result[k] for k in ("isError", "is_error") if k in result]
    if any(type(value) is not bool for value in errors) or len(set(errors)) > 1:
        raise ValueError("contradictory MCP error metadata")
    error = bool(errors and errors[0])
    if item.get("error") is not None or item.get("status") not in {"completed", "failed"}:
        raise ValueError("MCP call did not terminate normally")
    if item["status"] == "failed":
        error = True
    return {"payload": payload, "isError": error}


def analyze_events(raw, *, tool=None, arguments=None, allow_default_false=False):
    if not raw or not raw.endswith(b"\n") or len(raw) > LIMIT:
        raise ValueError("incomplete JSONL")
    lines = raw.splitlines()
    if len(lines) > 10000 or any(not line or len(line) > 1024 * 1024 for line in lines):
        raise ValueError("invalid JSONL bounds")
    events = [strict_json(line) for line in lines]
    counts, calls, messages, item_ids = {}, {}, [], set()
    allowed = {"thread.started", "turn.started", "turn.completed", "item.started", "item.completed"}
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("type") not in allowed:
            raise ValueError("failed, unknown, or updated event")
        kind = event["type"]
        counts[kind] = counts.get(kind, 0) + 1
        if not kind.startswith("item."):
            continue
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise ValueError("invalid event item")
        item_type = item.get("type")
        if item_type == "mcp_tool_call":
            calls.setdefault(item["id"], []).append((index, kind, item))
        elif item_type in {"agent_message", "reasoning"}:
            if kind != "item.completed" or item["id"] in item_ids:
                raise ValueError("duplicate or incomplete non-tool item")
            item_ids.add(item["id"])
            if item_type == "agent_message":
                if not isinstance(item.get("text"), str):
                    raise ValueError("invalid message text")
                messages.append((index, item["text"]))
        else:
            raise ValueError("forbidden tool or file operation")
    if any(counts.get(name) != 1 for name in ("thread.started", "turn.started", "turn.completed")):
        raise ValueError("expected exactly one complete thread/turn")
    if [event["type"] for event in events[:2]] != ["thread.started", "turn.started"] or events[-1]["type"] != "turn.completed":
        raise ValueError("invalid lifecycle event order")
    if set(calls) & item_ids or not messages:
        raise ValueError("colliding item IDs or missing final message")
    response, completion = None, -1
    if tool is None:
        if calls:
            raise ValueError("no-tool interpretation made a tool call")
    else:
        if len(calls) != 1:
            raise ValueError("expected exactly one MCP call")
        rows = next(iter(calls.values()))
        if len(rows) != 2 or [row[1] for row in rows] != ["item.started", "item.completed"]:
            raise ValueError("MCP start/completion mismatch")
        for _, _, item in rows:
            actual_arguments = item.get("arguments")
            if allow_default_false and isinstance(actual_arguments, dict) and "verify" not in actual_arguments:
                actual_arguments = dict(actual_arguments, verify=False)
            if item.get("server") != "zerorun" or item.get("tool") != tool or canonical(actual_arguments) != canonical(arguments):
                raise ValueError("wrong tool or arguments")
        if rows[0][2].get("status") != "in_progress":
            raise ValueError("MCP start was not in progress")
        completion = rows[1][0]
        if sum(index > completion for index, _ in messages) != 1:
            raise ValueError("expected one final message after MCP completion")
        response = payload_from_call(rows[1][2])
    final = messages[-1][1]
    if messages[-1][0] <= completion:
        raise ValueError("final answer preceded result")
    try:
        final_json = strict_json(final)
        final_parse_error = None
    except (ValueError, TypeError):
        final_json = None
        final_parse_error = "final response is not strict JSON"
    return {"event_counts": counts, "mcp_call_count": len(calls), "response": response,
            "commentary": [text for _, text in messages[:-1]], "final_message": final,
            "final_json": final_json, "final_parse_error": final_parse_error,
            "usage": events[-1].get("usage"), "thread_id": events[0].get("thread_id")}


def oracle(response, *, doctor=False, diagnostics=False):
    p, error = response["payload"], response["isError"]
    if not isinstance(p, dict) or type(error) is not bool:
        raise ValueError("invalid explicit response envelope")
    if doctor:
        if error or p.get("ok") is not True or p.get("manifest_authorized") is not True or p.get("mode") != "task-reuse" or p.get("reuse_ready") is not True:
            raise ValueError("doctor did not establish authorized readiness")
        return {"ready": True, "authorized": True, "mode": "task-reuse"}
    status, code = p.get("status"), p.get("exit_code")
    answer = {"classification": "REFUSED", "accepted_success": False, "fresh_evidence": False,
              "next_action": "stop_or_request_operator"}
    if status == "ERROR":
        if error is not True or not isinstance(p.get("error"), str):
            raise ValueError("inconsistent refusal")
        return answer
    if type(code) is not int or code < 0 or error != (code != 0):
        raise ValueError("contradictory error or exit metadata")
    if status == "MISS_FAILED":
        if code == 0:
            raise ValueError("failed status reports success")
        return dict(answer, classification="FRESH_FAILURE", fresh_evidence=True, next_action="inspect_fresh_failure")
    if status not in {"MISS_EXECUTED", "HIT_REUSED", "VERIFY_MATCH"} or code != 0:
        raise ValueError("unrecognized or contradictory success")
    if (p.get("task") != TASK or not re.fullmatch("[0-9a-f]{64}", str(p.get("cache_key")))
            or p.get("restored_outputs") != [] or type(p.get("verified")) is not bool
            or p["verified"] != (status == "VERIFY_MATCH")):
        raise ValueError("invalid result-only provenance")
    if status == "HIT_REUSED":
        if p.get("stdout_tail") != "" or p.get("stderr_tail") != "":
            raise ValueError("result-only reuse has replayed diagnostics")
        return dict(answer, classification="PRIOR_SUCCESS", accepted_success=not diagnostics,
                    next_action="request_fresh_execution" if diagnostics else "use_prior_status")
    return dict(answer, classification="FRESH_SUCCESS", accepted_success=True, fresh_evidence=True, next_action="use_fresh_status")


def grade(stage):
    raw = streams(stage["jsonl"])
    result = {"protocol_pass": False, "tool_result_pass": False, "semantic_pass": False}
    try:
        if stage["jsonl"]["returncode"] != 0 or any(stage["jsonl"][key] for key in ("timed_out", "stdout_truncated", "stderr_truncated")):
            raise ValueError("incomplete or failed process")
        before, after = stage["execution_root_before"], stage["execution_root_after"]
        if before.get("entries") != [] or before != after or stage["execution_root_unchanged"] is not True:
            raise ValueError("execution directory mutated")
        analyzed = analyze_events(raw, tool=stage["tool"], arguments=stage["expected_arguments"],
                                  allow_default_false=stage["name"] == "accept_prior")
        result.update(protocol_pass=True, analysis=analyzed)
        response = analyzed["response"] if stage["tool"] else stage["input_response"]
        expected = oracle(response, doctor=stage["tool"] == "doctor", diagnostics=stage["diagnostics_required"])
        if stage["tool"]:
            payload = response["payload"]
            if stage["name"] == "doctor":
                tasks = payload.get("tasks")
                if (payload.get("root") != stage["expected_arguments"]["root"] or payload.get("manifest_version") != 2
                        or payload.get("task_reuse_ready") is not True or not isinstance(tasks, list) or len(tasks) != 1
                        or tasks[0].get("task") != TASK or tasks[0].get("status") != "CACHEABLE"
                        or tasks[0].get("reason") is not None or not re.fullmatch("[0-9a-f]{64}", str(tasks[0].get("cache_key")))):
                    raise ValueError("doctor fixture/task binding differs")
            else:
                expected_status = {"miss": "MISS_EXECUTED", "hit": "HIT_REUSED", "verify": "VERIFY_MATCH",
                                   "accept_prior": "HIT_REUSED", "require_fresh": "VERIFY_MATCH"}[stage["name"]]
                if payload.get("status") != expected_status or payload.get("mode") != "reuse":
                    raise ValueError("unexpected live lifecycle outcome")
        result.update(tool_result_pass=True, expected_semantics=expected)
        actual = analyzed["final_json"]
        result["semantic_pass"] = isinstance(actual, dict) and canonical(actual) == canonical(expected)
        if not result["semantic_pass"]:
            result["semantic_error"] = "final JSON differs from the independent response oracle"
    except (ValueError, TypeError, KeyError) as exc:
        result["error"] = str(exc)
    return result


def source_bindings(directory):
    names = ("PROTOCOL.md", "__init__.py", "run.py", "validation.py", "test_validation.py")
    return [{"path": name, "sha256": sha((directory / name).read_bytes()), "bytes": (directory / name).stat().st_size} for name in names]


def validate_receipt(receipt_path, *, directory=None):
    """Recompute saved judgments; does not execute code, models, or tools."""
    directory = directory or Path(__file__).resolve().parent
    value = strict_json(Path(receipt_path).read_bytes())
    if value.get("schema") != SCHEMA or value.get("source_commit") != COMMIT or value.get("public_manifest_sha256") != MANIFEST_SHA256:
        raise ValueError("wrong experiment/source binding")
    if value.get("evidence_payload_sha256") != sha(canonical({k: v for k, v in value.items() if k != "evidence_payload_sha256"})):
        raise ValueError("receipt seal mismatch")
    freeze = value["freeze"]
    if freeze["sources"] != source_bindings(directory) or freeze["schema"] != "zerorun.live-client-v2.freeze":
        raise ValueError("frozen source bytes differ")
    if value["freeze_sha256"] != sha(canonical(freeze)):
        raise ValueError("freeze seal mismatch")
    try:
        from . import run as runner
    except ImportError:
        import run as runner
    fixed_freeze = {"source_commit": COMMIT, "public_manifest_sha256": MANIFEST_SHA256,
                    "codex_install_receipt_sha256": runner.INSTALL_RECEIPT_SHA,
                    "codex_install_source_commit": runner.INSTALL_SOURCE,
                    "stage_order": list(CORE + CHOICES + CASES), "maximum_turns": 12,
                    "timeout_seconds": TIMEOUT, "output_limit_bytes": LIMIT,
                    "explicit_approvals": {"synthetic_only_authority": True, "remote_synthetic_metadata": True},
                    "runtime_image": runner.IMAGE}
    if any(canonical(freeze.get(key)) != canonical(expected) for key, expected in fixed_freeze.items()):
        raise ValueError("prospective design bounds differ")
    for key in ("real_repository_authorized", "runtime_modified", "runtime_acquisition_attempted", "original_v1_reclassified"):
        if value.get(key) is not False:
            raise ValueError("experiment scope flags differ")
    stages = value["stages"]
    allowed = CORE + CHOICES + CASES
    names = tuple(stage["name"] for stage in stages)
    if names != allowed[:len(names)] or len(names) > 12:
        raise ValueError("unexpected stage order, retry, or denominator")
    captured = {}
    stopped = False
    for stage in stages:
        if stopped:
            raise ValueError("model work continued beyond a frozen stopping rule")
        tool, arguments = runner.plan(stage["name"], value["synthetic_path"])
        if stage["tool"] != tool or canonical(stage["expected_arguments"]) != canonical(arguments):
            raise ValueError("stage differs from immutable request plan")
        if stage["diagnostics_required"] is not (stage["name"] == "captured_hit_need_diagnostics"):
            raise ValueError("diagnostic requirement differs from plan")
        if stage["name"] in CASES:
            input_response = dict(runner.controlled_cases(captured))[stage["name"]]
        else:
            input_response = None
        if canonical(input_response) != canonical(stage["input_response"]):
            raise ValueError("interpretation input is not exact captured/fixed evidence")
        if stage["prompt"] != runner.prompt_for(stage["name"], arguments, input_response):
            raise ValueError("prompt differs from immutable task plan")
        if stage["prompt_utf8_sha256"] != sha(stage["prompt"].encode()):
            raise ValueError("prompt binding differs")
        actual = grade(stage)
        if canonical(actual) != canonical(stage["judgment"]):
            raise ValueError("stored judgment differs from raw transcript")
        stopped = (not all(actual[key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass"))
                   if stage["name"] in CORE + CHOICES else not actual["protocol_pass"])
        if stage["name"] in CORE and actual.get("analysis", {}).get("response") is not None:
            captured[stage["name"]] = actual["analysis"]["response"]
        command = stage["command"]
        if not isinstance(command, list) or command[-1] != "-" or "--ignore-user-config" not in command or "--strict-config" not in command:
            raise ValueError("recorded command lacks isolated configuration")
        env_setting = "mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT=" + json.dumps(value["explicit_trust_path"])
        if tool and command.count(env_setting) != 1:
            raise ValueError("explicit operator-managed trust forwarding absent")
        if not tool and ("mcp_servers={}" not in command or any("ZERORUN_TRUST_ROOT=" in part for part in command)):
            raise ValueError("interpretation command exposes tool configuration")
    original = value.get("orchestration_receipt")
    if original:
        if original["evidence_payload_sha256"] != sha(canonical({k: val for k, val in original.items() if k != "evidence_payload_sha256"})):
            raise ValueError("inherited orchestration receipt seal differs")
        if original.get("source", {}).get("commit") not in {None, COMMIT}:
            raise ValueError("orchestration code source differs")
        if original.get("functional_lifecycle_pass") is True:
            recorded_core = original.get("agent_stages")
            expected_core = [stage for stage in stages if stage["name"] in CORE]
            if not isinstance(recorded_core, list) or len(recorded_core) != 4 or len(expected_core) != 4:
                raise ValueError("successful original orchestration lacks exact four core records")
            for recorded, actual in zip(recorded_core, expected_core):
                if {key: val for key, val in recorded.items() if key != "analysis"} != actual:
                    raise ValueError("core orchestration stage differs from v2 raw stage")
    expected_oracle_stages = [stage["name"] for stage in stages if stage["name"] in {"hit", "verify", "accept_prior", "require_fresh"}
                             and all(stage["judgment"][key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass"))]
    oracles = value["fresh_oracles"]
    if [row["after_stage"] for row in oracles] != expected_oracle_stages[:len(oracles)]:
        raise ValueError("fresh oracle order differs")
    for row in oracles:
        computed_pass = fresh_oracle_pass(row, value["synthetic_path"])
        if row["passed"] is not computed_pass:
            raise ValueError("fresh oracle outcome differs from raw process evidence")
    if value["postflight"]["passed"] is not identity_pass(value):
        raise ValueError("identity summary is not re-computable")
    computed = summarize(value)
    if value["summary"] != computed:
        raise ValueError("summary is not reproducible")
    return computed


def fresh_oracle_pass(row, root):
    raw = streams(row["streams"])
    expected_suffix = ["run", "--rm", "--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                       "--security-opt=no-new-privileges", "--platform=linux/amd64", "--mount",
                       f"type=bind,src={root},dst=/workspace,readonly", "--workdir=/workspace", "--entrypoint=python",
                       "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef", "fixture.py"]
    if row["command"][1:] != expected_suffix or not row["command"][0].endswith("/docker"):
        raise ValueError("fresh oracle command differs")
    s = row["streams"]
    return s["returncode"] == 0 and not any(s[k] for k in ("timed_out", "stdout_truncated", "stderr_truncated")) and raw == b"" and s["stderr_bytes"] == 0


def identity_pass(value):
    original = value.get("orchestration_receipt", {})
    post = value.get("postflight", {})
    try:
        source = original["source"]
        installed = original["zerorun"]["installed_package"]
        codex = original["codex"]["authenticated_installation"]
        identities = original["identity_unchanged"]
        if set(identities) != {"source_worktree", "codex_executable", "zerorun_launcher", "zerorun_installed_package",
                               "synthetic_reviewed_source", "codex_authenticated_installation"} or any(v is not True for v in identities.values()):
            return False
        return (source["commit"] == COMMIT and source["git"] == source["git_after"]
                and source["package"]["file_count"] == 36
                and source["package"]["identity_sha256"] == "0bb214d1c9b33a2e3b7a3aa81e8f2469024cb6ddaab46c3fa41a9272502a57d5"
                and source["package"]["files"] == installed["module"]["files"]
                and sha(canonical(installed["module"]["files"])) == source["package"]["identity_sha256"]
                and installed == post["installed_after"]
                and codex == post["codex_installation_after"]
                and codex["receipt_sha256"] == "52a9bfbbef880d817cfa48479da1b65389e272946d765aad4863d8f1ad290858"
                and original["synthetic_repository"]["unchanged_reviewed_source"] is True
                and value["authority_structure_before_model"] == value["authority_structure_after_model"]
                and value["authority_structure_before_model"]["file_count"] == 2
                and post["public_runtime_rechecked"] is True and post["experiment_sources_rechecked"] is True)
    except KeyError:
        return False


def summarize(value):
    rows = value["stages"]
    def passed(stage):
        return all(stage["judgment"][key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass"))
    live = [stage for stage in rows if stage["name"] in CORE + CHOICES]
    interp = [stage for stage in rows if stage["name"] in CASES]
    core = [stage for stage in live if stage["name"] in CORE]
    choices = [stage for stage in live if stage["name"] in CHOICES]
    identity = identity_pass(value)
    inherited = value.get("orchestration_receipt", {}).get("functional_lifecycle_pass") is True
    oracles = value.get("fresh_oracles", [])
    oracle_pass = len(oracles) == 4 and all(fresh_oracle_pass(row, value["synthetic_path"]) for row in oracles)
    keys = []
    for stage in live:
        response = stage["judgment"].get("analysis", {}).get("response")
        if response and stage["judgment"]["tool_result_pass"]:
            keys.append(response["payload"]["tasks"][0]["cache_key"] if stage["name"] == "doctor" else response["payload"]["cache_key"])
    common_key = len(keys) == 6 and len(set(keys)) == 1
    return {"live_turns_completed": len(live), "live_turns_passed": sum(passed(stage) for stage in live),
            "core_lifecycle_pass": len(core) == 4 and all(passed(stage) for stage in core) and inherited and identity,
            "decision_turns_passed": sum(passed(stage) for stage in choices), "common_six_turn_cache_key": common_key,
            "interpretation_cases_completed": len(interp), "interpretation_cases_passed": sum(passed(stage) for stage in interp),
            "fresh_oracle_calls": len(oracles), "fresh_oracles_pass": oracle_pass,
            "all_planned_checks_pass": len(rows) == 12 and all(passed(stage) for stage in rows) and inherited and identity and oracle_pass and common_key,
            "population_accuracy_claim": False, "autonomous_workflow_speedup_claim": False}
