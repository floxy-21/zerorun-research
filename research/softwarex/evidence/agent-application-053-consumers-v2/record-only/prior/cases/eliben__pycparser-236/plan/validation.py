"""Read-only source qualification and raw-event analysis for real consumers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

SCHEMA = "zerorun.real-agent-qualification-0.5.3.v1"
STAGES = ("available", "fresh", "restored")
CASES = ("eliben__pycparser-236", "joke2k__django-environ-174", "tobymao__sqlglot-3182",
         "terryyin__lizard-241", "eyeseast__python-frontmatter-56", "joshtemple__lkml-87")
LIMIT = 8 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result
    def nonfinite(value):
        raise ValueError("nonfinite JSON")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    canonical(value)
    return value


def real(path, directory=False):
    path = Path(os.path.abspath(path))
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400
            and path.resolve(strict=True) == path, "linked or noncanonical path refused")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            "ordinary directory/file required")
    return path


def safe_relative(name):
    require(isinstance(name, str) and name and "\\" not in name and ":" not in name
            and "\x00" not in name, "invalid relative path")
    parsed = PurePosixPath(name)
    require(not parsed.is_absolute() and ".." not in parsed.parts and parsed.as_posix() == name,
            "unsafe relative path")
    return name


def inventory(root, *, exclude_control=True):
    """All ordinary files; never follow links, read credentials, or create state."""
    root = real(root, directory=True)
    if exclude_control:
        real(root / ".git", directory=True)
    rows, total = [], 0
    for current, dirs, files in os.walk(root, followlinks=False):
        current = Path(current)
        if current == root and exclude_control:
            dirs[:] = [name for name in dirs if name not in {".git", ".zerorun"}]
            require(not ({".git", ".zerorun"} & set(files)), "control directory is a file")
        for name in dirs:
            real(current / name, directory=True)
        dirs.sort()
        for name in sorted(files):
            path = real(current / name)
            require(path.stat().st_size <= 32 * 1024 * 1024, "source file exceeds bound")
            raw = path.read_bytes()
            total += len(raw)
            require(total <= 256 * 1024 * 1024 and len(rows) < 20000, "source inventory exceeds bound")
            rows.append({"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": sha(raw)})
    return sorted(rows, key=lambda row: row["path"])


def identity(rows):
    return sha(canonical(rows))


def checked_rows(rows):
    require(isinstance(rows, list) and 0 < len(rows) <= 20000, "bounded file inventory required")
    indexed = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}, "invalid file record")
        name = safe_relative(row["path"])
        require(name.split("/")[0] not in {".git", ".zerorun"} and name not in indexed,
                "control or duplicate inventory path")
        require(type(row["bytes"]) is int and 0 <= row["bytes"] <= 32 * 1024 * 1024
                and isinstance(row["sha256"], str) and HEX.fullmatch(row["sha256"]), "invalid file bytes/hash")
        indexed[name] = row
    require([row["path"] for row in rows] == sorted(indexed), "inventory is not path-sorted")
    require(sum(row["bytes"] for row in rows) <= 256 * 1024 * 1024, "inventory exceeds byte bound")
    return indexed


def validate_qualification(value):
    require(value.get("schema") == SCHEMA and value.get("case_id") in CASES, "qualification schema or selection differs")
    root = value.get("root")
    require(isinstance(root, str) and root.startswith("/") and str(PurePosixPath(root)) == root
            and ".." not in PurePosixPath(root).parts, "canonical Linux repository root required")
    require(isinstance(value.get("task"), str) and value["task"] and len(value["task"]) <= 128, "task absent")
    require(isinstance(value.get("manifest_sha256"), str) and HEX.fullmatch(value["manifest_sha256"]), "manifest hash absent")
    require(isinstance(value.get("command"), list) and value["command"]
            and all(isinstance(arg, str) and arg for arg in value["command"]), "reviewed command absent")
    require(isinstance(value.get("runtime_image"), str)
            and re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", value["runtime_image"]), "digest-bound runtime absent")
    require(set(value.get("states", {})) == {"final", "restored"}, "two exact reviewed states required")
    indexed = {}
    for name in ("final", "restored"):
        state = value["states"][name]
        indexed[name] = checked_rows(state.get("files"))
        require(state.get("eligible") is True and state.get("identity_sha256") == identity(state["files"]),
                "state is not exactly qualified")
        require(indexed[name].get(".zerorun.json", {}).get("sha256") == value["manifest_sha256"],
                "state manifest differs")
    tests = value.get("protected_test_paths")
    require(isinstance(tests, list) and tests and tests == sorted(set(tests)), "explicit protected tests required")
    for name in tests:
        safe_relative(name)
        require(name in indexed["final"] and indexed["final"][name] == indexed["restored"].get(name),
                "supplied test changed during restoration")
    changes = sorted(name for name in indexed["final"].keys() | indexed["restored"].keys()
                     if indexed["final"].get(name) != indexed["restored"].get(name))
    restoration = value.get("restoration", {})
    require(changes and restoration.get("changed_paths") == changes
            and isinstance(restoration.get("description"), str) and restoration["description"].strip(),
            "restoration inventory or explanation differs")
    for name in changes:
        require(name.endswith(".py") and name not in tests and name != ".zerorun.json"
                and not any(part.startswith(".") or part.lower() in {"test", "tests", "testing", "docs", "doc", "ci"}
                            for part in PurePosixPath(name).parts)
                and PurePosixPath(name).name not in {"conftest.py", "setup.py", "noxfile.py", "toxfile.py", "conf.py"}
                and not PurePosixPath(name).name.startswith("test_") and not PurePosixPath(name).name.endswith("_test.py"),
                "restoration exceeds production-source scope")
    inspection = value.get("inspection", {})
    require(inspection.get("method") == "AI-assisted inspection under user-authorized laboratory scope"
            and inspection.get("human_review_seconds") is None
            and isinstance(inspection.get("basis"), str) and inspection["basis"].strip(), "inspection scope differs")
    authorization = value.get("authorization", {})
    require(authorization.get("scope") == "selected laboratory case and the two exact recorded states only"
            and isinstance(authorization.get("user_authorization_reference"), str)
            and authorization["user_authorization_reference"].strip(), "laboratory authorization reference absent")
    return {"case_id": value["case_id"], "task": value["task"], "states": {
        name: value["states"][name]["identity_sha256"] for name in ("final", "restored")},
        "human_review_time_claimed": False, "arbitrary_future_edits_qualified": False}


def payload_from_call(item):
    result = item.get("result")
    require(isinstance(result, dict), "completed MCP call has no result")
    payload = result.get("structured_content", result.get("structuredContent"))
    require(isinstance(payload, dict), "MCP structured result absent")
    if "structured_content" in result and "structuredContent" in result:
        require(result["structured_content"] == result["structuredContent"], "contradictory structured results")
    content = result.get("content")
    require(isinstance(content, list) and len(content) == 1 and content[0].get("type") == "text"
            and strict(content[0].get("text", "")) == payload, "MCP text/structured results differ")
    flags = [result[key] for key in ("isError", "is_error") if key in result]
    require(all(type(flag) is bool for flag in flags) and len(set(flags)) <= 1, "MCP error flags differ")
    return payload, bool(flags and flags[0])


def classify_result(payload, error, task):
    status, code = payload.get("status"), payload.get("exit_code")
    if status == "ERROR":
        require(error and isinstance(payload.get("error"), str), "contradictory refusal")
        return "REFUSED"
    require(payload.get("task") == task and type(code) is int and type(payload.get("verified")) is bool,
            "result task/exit/verification metadata absent")
    require(payload.get("restored_outputs") == [], "result-only task unexpectedly restored outputs")
    require(error == (code != 0), "exit status contradicts MCP error flag")
    if status == "HIT_REUSED":
        require(code == 0 and payload["verified"] is False and payload.get("stdout_tail") == ""
                and payload.get("stderr_tail") == "" and HEX.fullmatch(str(payload.get("cache_key"))),
                "reuse lacks exact successful result-only provenance")
        return "PRIOR_SUCCESS"
    if status in {"MISS_EXECUTED", "VERIFY_MATCH"}:
        require(code == 0 and (status != "VERIFY_MATCH" or payload["verified"] is True), "fresh status contradicts outcome")
        return "FRESH_SUCCESS"
    if status == "MISS_FAILED":
        require(code != 0, "fresh failure reports zero exit")
        return "FRESH_FAILURE"
    if status in {"VERIFY_MISMATCH", "FORCE_CONFLICT"}:
        return "CORRECTNESS_MISMATCH"
    return "UNCLASSIFIED_RESULT"


def analyze_events(raw, *, root, task, stage):
    """Retain uncertain/adverse observations; requests and prose are never hits."""
    require(stage in STAGES, "unknown consumer stage")
    result = {"schema": "zerorun.real-consumer-event-analysis.0.5.3.v1", "boundary_pass": True,
              "model_turn_completed": False, "completed_mcp_results": [], "requested_mcp_calls": 0,
              "requested_model": "gpt-6-astra", "observed_model_identifiers": [], "errors": [],
              "final_message": None, "interpretation": "UNCERTAIN", "material_interpretation_failure": False,
              "fresh_request_satisfied": False, "reuse_observed": False}
    try:
        require(raw and len(raw) <= LIMIT and raw.endswith(b"\n"), "empty, truncated, or oversized JSONL")
        lines = raw.splitlines()
        require(len(lines) <= 10000 and all(line and len(line) <= 1024 * 1024 for line in lines), "JSONL bounds differ")
        events = [strict(line) for line in lines]
        calls, messages, counts, completed_ids = {}, [], {}, set()
        allowed = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error",
                   "item.started", "item.updated", "item.completed"}
        for index, event in enumerate(events):
            require(isinstance(event, dict) and event.get("type") in allowed, "unknown event")
            kind = event["type"]
            counts[kind] = counts.get(kind, 0) + 1
            for key in ("model", "model_id", "model_slug"):
                if isinstance(event.get(key), str) and event[key] not in result["observed_model_identifiers"]:
                    result["observed_model_identifiers"].append(event[key])
            if kind in {"error", "turn.failed"}:
                result["errors"].append({"event": kind, "detail": event.get("message", event.get("error"))})
            if not kind.startswith("item."):
                continue
            item = event.get("item")
            require(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"], "invalid item identity")
            item_id, item_type = item["id"], item.get("type")
            if item_type == "mcp_tool_call":
                require(item.get("server") == "zerorun" and item.get("tool") in {"list_tasks", "run_tests"},
                        "unrelated MCP tool observed")
                arguments = item.get("arguments")
                require(isinstance(arguments, dict) and arguments.get("root", root) in (None, root), "MCP root differs")
                if item["tool"] == "run_tests":
                    require(arguments.get("task") == task and set(arguments) <= {"root", "task", "verify"}
                            and type(arguments.get("verify", False)) is bool, "MCP task or options differ")
                else:
                    require(set(arguments) <= {"root"}, "list_tasks options differ")
                sequence = calls.setdefault(item_id, [])
                if sequence:
                    require(all(item.get(k) == sequence[0][2].get(k) for k in ("server", "tool", "arguments")),
                            "MCP request identity changed")
                require(not sequence or sequence[-1][1] != "item.completed", "event after MCP completion")
                sequence.append((index, kind, item))
                if kind == "item.completed":
                    require(item_id not in completed_ids and len([r for r in sequence if r[1] == "item.started"]) == 1,
                            "duplicate or unstarted MCP completion")
                    completed_ids.add(item_id)
                    if item.get("status") == "completed" and item.get("error") is None:
                        payload, error = payload_from_call(item)
                        if item["tool"] == "run_tests":
                            classification = classify_result(payload, error, task)
                            result["completed_mcp_results"].append({"call_id": item_id, "event_index": index,
                                "arguments": arguments, "payload": payload, "is_error": error,
                                "classification": classification})
                    else:
                        result["errors"].append({"event": "mcp_call_failed", "call_id": item_id,
                                                  "status": item.get("status"), "error": item.get("error")})
            elif item_type in {"reasoning", "agent_message"}:
                if kind == "item.completed" and item_type == "agent_message":
                    require(isinstance(item.get("text"), str), "invalid agent message")
                    messages.append((index, item["text"]))
            else:
                raise ValueError("forbidden execution, file, plan, or other tool event")
        result["event_counts"] = counts
        result["requested_mcp_calls"] = len(calls)
        for call_id, sequence in calls.items():
            if sequence[0][1] != "item.started" or sequence[-1][1] != "item.completed":
                result["errors"].append({"event": "incomplete_mcp_call", "call_id": call_id})
        result["model_turn_completed"] = (counts.get("thread.started") == 1 and counts.get("turn.started") == 1
            and counts.get("turn.completed") == 1 and not counts.get("turn.failed")
            and events[-1]["type"] == "turn.completed")
        results = result["completed_mcp_results"]
        result["reuse_observed"] = any(row["classification"] == "PRIOR_SUCCESS" for row in results)
        result["fresh_request_satisfied"] = any(row["arguments"].get("verify") is True
            and row["classification"] in {"FRESH_SUCCESS", "FRESH_FAILURE"} for row in results)
        result["validation_result_count"] = len(results)
        result["validation_calls_beyond_first"] = max(0, len(results) - 1)
        if not results:
            result["errors"].append({"event": "no_completed_validation_result"})
        if messages:
            result["final_message"] = messages[-1][1]
            try:
                final = strict(messages[-1][1])
            except (ValueError, TypeError):
                final = None
            result["final_structured_interpretation"] = final
            if results and isinstance(final, dict) and type(final.get("execution_in_this_call")) is bool:
                last = results[-1]
                if messages[-1][0] > last["event_index"]:
                    evidence = last["classification"]
                    classifications = {item["classification"] for item in results}
                    false_fresh = final["execution_in_this_call"] and classifications == {"PRIOR_SUCCESS"}
                    false_success = (final.get("validation_status") == "success"
                        and classifications <= {"FRESH_FAILURE", "REFUSED", "CORRECTNESS_MISMATCH"})
                    if false_fresh or false_success:
                        result.update(interpretation="MATERIAL_FAILURE", material_interpretation_failure=True)
                    else:
                        result["interpretation"] = "STRUCTURED_CLAIM_RECORDED_REVIEW_REQUIRED"
        # Natural-language diagnostics and limitations still need separate review.
        result["natural_language_review_required"] = True
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        result["boundary_pass"] = False
        result["errors"].append({"event": "parse_or_boundary_error", "message": str(exc)})
    return result
