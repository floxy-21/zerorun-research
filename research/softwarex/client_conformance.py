"""Bounded non-model client conformance; never grants operator authority.

Positive fixtures wrap preserved Python-API results in explicitly synthetic
MCP envelopes. Live installed-server checks cover diagnostics/refusals only.
Neither evidence class is an autonomous agent experiment.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "zerorun.softwarex-client-conformance.v1"
LIMIT = 1024 * 1024
CATEGORIES = (
    "preserved_fresh_success", "preserved_reused_success",
    "reused_success_requires_fresh_transcript", "preserved_fresh_failure",
    "explicit_tool_refusal", "top_level_protocol_error",
    "structured_text_disagreement", "contradictory_status_exit_or_error",
    "mode_only_or_unknown_status", "actual_installed_stdio_readiness_and_refusal",
)
SOURCE_RECORDS = (
    "seed-26/product.json", "changed-27-counterfactual-oracle/product.json",
    "restored-collection-29/product.json", "restored-repeat-30/product.json",
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if len(raw) > LIMIT:
        raise ValueError("JSON response exceeds 1 MiB")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def invalid(_value):
        raise ValueError("non-finite JSON constant")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        canonical(value)
    except (UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError("invalid or excessive JSON") from exc
    return value


def decision(classification, *, success=False, fresh=False, action="stop_or_request_operator", reason=""):
    return {"classification": classification, "accepted_success": success,
            "fresh_evidence": fresh, "next_action": action, "reason": reason}


def response_payload(response, *, expected_id):
    if (not isinstance(response, dict) or response.get("jsonrpc") != "2.0"
            or type(response.get("id")) is not type(expected_id)
            or response.get("id") != expected_id or isinstance(expected_id, bool)
            or not isinstance(expected_id, (str, int))):
        raise ValueError("invalid response envelope or mismatched request ID")
    if "error" in response:
        if "result" in response:
            raise ValueError("response has both result and error")
        error = response["error"]
        if (not isinstance(error, dict) or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)):
            raise ValueError("invalid JSON-RPC error")
        raise ValueError("JSON-RPC error: " + error["message"])
    result = response.get("result")
    if not isinstance(result, dict) or type(result.get("isError")) is not bool:
        raise ValueError("missing explicit boolean isError")
    payload, content = result.get("structuredContent"), result.get("content")
    if (not isinstance(payload, dict) or not isinstance(content, list)
            or len(content) != 1 or not isinstance(content[0], dict)
            or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str)):
        raise ValueError("expected one JSON text block and structured result")
    if canonical(strict_json(content[0]["text"])) != canonical(payload):
        raise ValueError("structured and text representations disagree")
    return payload, result["isError"]


def consume(response, *, expected_id=1, fresh_transcript_required=False):
    """Return a conservative decision; never execute a fallback or authorize reuse.

    The caller must trust the actual MCP connection. This consumer validates
    response semantics, not cache cryptography or source-closure completeness.
    A fresh response may contain bounded output tails, not a complete transcript.
    """
    try:
        payload, error = response_payload(response, expected_id=expected_id)
        status, code = payload.get("status"), payload.get("exit_code")
        if not isinstance(status, str):
            raise ValueError("missing result status; mode is not a verdict")
        if status == "ERROR":
            if not error or not isinstance(payload.get("error"), str):
                raise ValueError("inconsistent tool refusal")
            return decision("REFUSED", reason=payload["error"])
        recognized = {"HIT_REUSED", "MISS_EXECUTED", "MISS_FAILED", "VERIFY_MATCH"}
        if status not in recognized:
            return decision("REFUSED", reason="unknown or unsupported status: " + status)
        if type(code) is not int or code < 0:
            raise ValueError("exit_code must be a nonnegative integer")
        if error != (code != 0):
            raise ValueError("isError contradicts exit_code")
        if status == "MISS_FAILED":
            if code == 0:
                raise ValueError("failed status cannot report exit zero")
            return decision("FRESH_FAILURE", fresh=True, action="inspect_fresh_failure",
                            reason="fresh failure is not a successful reusable result")
        if code != 0:
            raise ValueError("success status cannot report a failing exit")
        if (not isinstance(payload.get("cache_key"), str)
                or not re.fullmatch("[0-9a-f]{64}", payload["cache_key"])
                or not isinstance(payload.get("task"), str) or not payload["task"]
                or payload.get("restored_outputs") != []
                or type(payload.get("verified")) is not bool):
            raise ValueError("missing or incompatible result-only provenance fields")
        if status == "VERIFY_MATCH" and payload["verified"] is not True:
            raise ValueError("verification result is not verified")
        if status == "HIT_REUSED":
            if payload["verified"] is not False:
                raise ValueError("plain reuse cannot claim fresh verification")
            if payload.get("stdout_tail", "") or payload.get("stderr_tail", ""):
                raise ValueError("result-only hit cannot replay diagnostic output")
            return decision("PRIOR_SUCCESS", success=not fresh_transcript_required,
                            action="request_fresh_execution" if fresh_transcript_required else "use_prior_status",
                            reason="identified earlier success; no fresh execution or transcript")
        return decision("FRESH_SUCCESS", success=True, fresh=True, action="use_fresh_status",
                        reason="fresh execution; output fields are bounded tails")
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        return decision("PROTOCOL_ERROR", reason=str(exc))


def wrap_api_fixture(record):
    """MCP-shaped fixture only: this does NOT claim an observed MCP response."""
    payload = copy.deepcopy(record)
    payload["mode"] = "reuse"
    payload["stdout_tail"] = payload.pop("stdout", "")[-4000:]
    payload["stderr_tail"] = payload.pop("stderr", "")[-4000:]
    return {"jsonrpc": "2.0", "id": 1, "result": {
        "isError": payload["exit_code"] != 0, "structuredContent": payload,
        "content": [{"type": "text", "text": canonical(payload).decode()}]}}


def envelope(payload, *, error=False):
    return {"jsonrpc": "2.0", "id": 1, "result": {
        "isError": error, "structuredContent": payload,
        "content": [{"type": "text", "text": canonical(payload).decode()}]}}


def write_once(path, value):
    raw = json.dumps(value, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    with path.open("xb") as handle:
        handle.write(raw)


def file_record(path):
    raw = path.read_bytes()
    return {"sha256": sha(raw), "bytes": len(raw)}


def source_records(root):
    paths = ["research/softwarex/client_conformance.py", "research/softwarex/tests/test_client_conformance.py",
             "research/softwarex/generated/public-release-tests.json"]
    case = "research/sqj/strengthening/evidence/agent-state-rejoin-v3/case/"
    paths.extend(case + name for name in SOURCE_RECORDS)
    return {name: file_record(root / name) for name in paths}


def freeze(root, output):
    output.mkdir(parents=True, exist_ok=True)
    protocol = {"schema": SCHEMA + ".protocol", "frozen_utc": datetime.now(timezone.utc).isoformat(),
                "categories": list(CATEGORIES), "source_bindings": source_records(root),
                "fixed_positive_request_records": list(SOURCE_RECORDS),
                "selection": "all four previously published bounded case requests, no outcome-based replacement",
                "planned_live_requests": ["initialize", "tools/list", "doctor", "list_tasks", "stats",
                                          "run_tests_without_authority", "other_repository_refusal", "unknown_method"],
                "limits": "Non-model client conformance only. No authority creation, model calls, Docker execution, speed or autonomous-agent effectiveness claim.",
                "adverse_outcomes": "Every attempt has a new numbered directory; no overwrite or automatic retry."}
    write_once(output / "protocol.json", protocol)
    return protocol


def scripted_cases(root):
    case = root / "research/sqj/strengthening/evidence/agent-state-rejoin-v3/case"
    records = [strict_json((case / name).read_bytes()) for name in SOURCE_RECORDS]
    fresh, changed, failed, hit = [wrap_api_fixture(record) for record in records]
    cases = [("fresh_seed", CATEGORIES[0], fresh, False, "FRESH_SUCCESS", True),
             ("fresh_changed", CATEGORIES[0], changed, False, "FRESH_SUCCESS", True),
             ("prior_success", CATEGORIES[1], hit, False, "PRIOR_SUCCESS", True),
             ("prior_requires_transcript", CATEGORIES[2], hit, True, "PRIOR_SUCCESS", False),
             ("fresh_failure", CATEGORIES[3], failed, False, "FRESH_FAILURE", False),
             ("refusal", CATEGORIES[4], envelope({"status": "ERROR", "error": "untrusted configuration"}, error=True), False, "REFUSED", False),
             ("rpc_error", CATEGORIES[5], {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "unknown method"}}, False, "PROTOCOL_ERROR", False)]
    mismatch = copy.deepcopy(hit)
    mismatch["result"]["content"][0]["text"] = "{}"
    cases.append(("text_disagreement", CATEGORIES[6], mismatch, False, "PROTOCOL_ERROR", False))
    for label, field, value in (("bad_exit", "exit_code", 7), ("boolean_exit", "exit_code", False),
                                ("bad_verified", "verified", True)):
        payload = copy.deepcopy(hit["result"]["structuredContent"])
        payload[field] = value
        cases.append((label, CATEGORIES[7], envelope(payload), False, "PROTOCOL_ERROR", False))
    false_error = copy.deepcopy(hit)
    false_error["result"]["isError"] = True
    cases.append(("error_with_success", CATEGORIES[7], false_error, False, "PROTOCOL_ERROR", False))
    cases.append(("mode_only", CATEGORIES[8], envelope({"mode": "reuse", "exit_code": 0}), False, "PROTOCOL_ERROR", False))
    cases.append(("unknown_status", CATEGORIES[8], envelope({"mode": "reuse", "status": "FUTURE_SUCCESS", "exit_code": 0}), False, "REFUSED", False))
    return [{"name": name, "category": category, "input": response,
             "fresh_transcript_required": transcript, "expected_classification": expected,
             "expected_accepted_success": success,
             "decision": consume(response, fresh_transcript_required=transcript)}
            for name, category, response, transcript, expected, success in cases]


def safe_environment(trust_root):
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC", "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({"ZERORUN_TRUST_ROOT": str(trust_root), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never",
                "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def live_checks(python, root, attempt):
    # The exact fixture directory is retained on failure for inspection. It is
    # outside all source checkouts, contains no credentials, and is never authorized.
    fixture = Path(tempfile.mkdtemp(prefix="zerorun-client-conformance-"))
    repository = fixture / "repository"
    repository.mkdir()
    trust = fixture / "absent-authority"
    env = safe_environment(trust)
    git = shutil.which("git")
    if git is None:
        raise ValueError("Git is required for a genuine temporary repository")
    initialized = subprocess.run([git, "init", "--template=", "--initial-branch=main", str(repository)],
                                 env=env, capture_output=True, timeout=20, check=False)
    if initialized.returncode:
        raise ValueError("temporary Git fixture initialization failed")
    other = fixture / "other-repository"
    other.mkdir()
    initialized_other = subprocess.run([git, "init", "--template=", "--initial-branch=main", str(other)],
                                       env=env, capture_output=True, timeout=20, check=False)
    if initialized_other.returncode:
        raise ValueError("second temporary Git fixture initialization failed")
    # A genuinely unconfigured first-use checkout: do not assert closure review,
    # write an activated manifest, or create external authority for this model.
    (repository / "input.txt").write_bytes(b"unauthorized conformance fixture; no test execution\n")
    before = {"input.txt": file_record(repository / "input.txt")}
    probe = "import hashlib,json,pathlib,zerorun; p=pathlib.Path(zerorun.__file__).parent; print(json.dumps({'version':zerorun.__version__,'path':str(p),'files':{str(f.relative_to(p)).replace(chr(92),'/'):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(p.rglob('*.py'))}}))"
    identified = subprocess.run([str(python), "-I", "-c", probe], cwd=fixture,
                                env=env, capture_output=True, timeout=20, check=False)
    if identified.returncode:
        raise ValueError("external installed ZeroRun identification failed")
    installed = strict_json(identified.stdout)
    package = Path(installed["path"]).resolve()
    if package.is_relative_to(root.resolve()) or not package.is_relative_to(python.parent.parent.resolve()):
        raise ValueError("expected an external environment installation, not checkout import")
    # The Windows source archive checkout has CRLF conversions; compare exact
    # wheel bytes with the preserved Git-blob-bound public-layout test inventory,
    # not with newline-normalized or platform-converted working-copy bytes.
    tested = strict_json((root / "research/softwarex/generated/public-release-tests.json").read_bytes())
    prefix = "src/zerorun/"
    source_commit = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
    expected = {row["path"][len(prefix):]: row["sha256"] for row in tested["tested_code"]
                if row["path"].startswith(prefix)
                and row["origin"].startswith("git:" + source_commit + ":zerorun/")}
    if tested.get("core_commit") != source_commit or len(expected) != 36:
        raise ValueError("frozen exact-byte installation reference is incomplete")
    write_once(attempt / "installed-identity.json", {"actual": installed, "expected": expected,
               "reference": "research/softwarex/generated/public-release-tests.json",
               "reference_sha256": sha((root / "research/softwarex/generated/public-release-tests.json").read_bytes())})
    if installed["files"] != expected:
        raise ValueError("installed runtime does not match frozen core bytes")
    requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "zerorun-scripted-conformance", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}]
    for ident, name, args in ((3, "doctor", {}), (4, "list_tasks", {}), (5, "stats", {}),
                              (6, "run_tests", {"task": "tests"}),
                              (7, "stats", {"root": str(other)})):
        requests.append({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": {
            "name": name, "arguments": {"root": str(repository), **args}}})
    requests.append({"jsonrpc": "2.0", "id": 8, "method": "conformance/unknown", "params": {}})
    raw = b"".join(canonical(request) + b"\n" for request in requests)
    write_once(attempt / "live-request-plan.json", {"requests": requests, "installed_runtime": installed,
               "python": {"path": str(python), **file_record(python)}, "fixture": str(fixture)})
    completed = subprocess.run([str(python), "-I", "-m", "zerorun", "mcp-server"], cwd=repository,
                               env=env, input=raw, capture_output=True, timeout=30, check=False)
    write_once(attempt / "live-transport.json", {"returncode": completed.returncode,
               "stdout": completed.stdout.decode("utf-8", errors="replace"),
               "stderr": completed.stderr.decode("utf-8", errors="replace"),
               "stdout_sha256": sha(completed.stdout), "stderr_sha256": sha(completed.stderr)})
    if completed.returncode or len(completed.stdout) > LIMIT or len(completed.stderr) > LIMIT:
        raise ValueError("installed MCP process failed or exceeded output limit")
    responses = [strict_json(line) for line in completed.stdout.splitlines() if line.strip()]
    if [response.get("id") for response in responses] != list(range(1, 9)):
        raise ValueError("live response IDs are missing, repeated, or reordered")
    by_id = {response["id"]: response for response in responses}
    checks = {}
    checks["initialized_installed_server"] = by_id[1].get("result", {}).get("serverInfo") == {"name": "zerorun", "version": "0.5.1"}
    names = [tool["name"] for tool in by_id[2].get("result", {}).get("tools", [])]
    checks["exact_seven_tools"] = len(names) == 7 and set(names) == {"doctor", "list_tasks", "stats", "explain", "prepare_pytest", "run_tests", "run_pytest"}
    for ident, label in ((3, "doctor"), (4, "list_tasks"), (5, "stats")):
        payload, _error = response_payload(by_id[ident], expected_id=ident)
        checks[label + "_observe_only"] = payload.get("mode") == "observe-only" and payload.get("reuse_ready") is False
    refusal = consume(by_id[6], expected_id=6)
    escape = consume(by_id[7], expected_id=7)
    checks["unconfigured_execution_refused"] = refusal["classification"] == "REFUSED" and "no .zerorun.json found" in refusal["reason"]
    checks["other_repository_refused"] = escape["classification"] == "REFUSED" and "refusing access" in escape["reason"]
    checks["unknown_method_protocol_error"] = consume(by_id[8], expected_id=8)["classification"] == "PROTOCOL_ERROR"
    checks["no_authority_or_cache_created"] = not trust.exists() and not (repository / ".zerorun").exists()
    checks["fixture_inputs_unchanged"] = before == {name: file_record(repository / name) for name in before}
    checks["no_reviewed_manifest_created"] = not (repository / ".zerorun.json").exists()
    return {"checks": checks, "responses": responses, "fixture_path": str(fixture),
            "positive_execution_tested": False, "operator_authority_created": False,
            "model_called": False, "scope": "real installed stdio server, diagnostics and refusals only"}


def run(root, output, python):
    protocol = strict_json((output / "protocol.json").read_bytes())
    if protocol.get("categories") != list(CATEGORIES) or protocol.get("source_bindings") != source_records(root):
        raise ValueError("frozen protocol or source binding mismatch; create an explicit amendment, not a silent retry")
    attempt = output / ("attempt-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    attempt.mkdir()
    receipt = {"schema": SCHEMA, "started_utc": datetime.now(timezone.utc).isoformat(),
               "protocol_sha256": sha((output / "protocol.json").read_bytes()),
               "source_bindings": source_records(root), "platform": platform.platform(),
               "python": sys.version, "completed": False,
               "limitations": protocol["limits"], "attempt_directory": attempt.name}
    try:
        cases = scripted_cases(root)
        for case in cases:
            case["pass"] = (case["decision"]["classification"] == case["expected_classification"]
                            and case["decision"]["accepted_success"] is case["expected_accepted_success"])
        write_once(attempt / "scripted-fixtures.json", {"origin": "preserved API outputs in explicitly scripted MCP-shaped fixtures; adverse mutations synthetic", "cases": cases})
        live = live_checks(python, root, attempt)
        write_once(attempt / "live-analysis.json", live)
        receipt.update({"scripted_cases": len(cases), "scripted_passed": sum(case["pass"] for case in cases),
                        "live_checks": live["checks"], "live_passed": sum(live["checks"].values()),
                        "completed": all(case["pass"] for case in cases) and all(live["checks"].values())})
    except Exception as exc:
        receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    receipt["source_unchanged"] = receipt["source_bindings"] == source_records(root)
    receipt["completed"] = receipt["completed"] and receipt["source_unchanged"]
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["output_files"] = {path.name: file_record(path) for path in sorted(attempt.iterdir())}
    write_once(attempt / "receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "run"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--installed-python", type=Path)
    args = parser.parse_args(argv)
    if args.action == "freeze":
        result = freeze(args.root.resolve(), args.output.resolve())
    else:
        if args.installed_python is None:
            parser.error("run requires --installed-python in an external environment")
        result = run(args.root.resolve(), args.output.resolve(), args.installed_python.resolve())
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if args.action == "freeze" or result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
