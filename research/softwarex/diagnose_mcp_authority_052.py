"""Version-specific STDIO exchange validation for the 0.5.2 laboratory.

The historical validator remains unchanged. The exchange validator below is its
exact function with only serverInfo.version changed to 0.5.2; common request and
JSON parsing functions remain imported from the byte-bound historical module.
This module does not execute a diagnostic or grant authority.
"""
import base64
import hashlib

from .diagnose_mcp_authority import LIMIT, STAGES, canonical, request_plan, strict_json


def validate_exchange(stage, root):
    name = stage["name"]
    if stage["requests"] != request_plan(name, root):
        raise ValueError("diagnostic request sequence or arguments differ")
    if stage["explicit_trust_root"] is not (name != "negative_doctor"):
        raise ValueError("trust-path intervention label differs")
    streams = stage["streams"]
    raw_streams = {}
    for stream in ("stdout", "stderr"):
        raw = base64.b64decode(streams[stream + "_base64"], validate=True)
        if (len(raw) > LIMIT or len(raw) != streams[stream + "_bytes"]
                or hashlib.sha256(raw).hexdigest() != streams[stream + "_sha256"]
                or streams[stream + "_truncated"] is not False):
            raise ValueError("diagnostic stream hash, length, or truncation differs")
        raw_streams[stream] = raw
    if type(streams["returncode"]) is not int or streams["returncode"] != 0 or streams["timed_out"] is not False:
        raise ValueError("diagnostic STDIO server failed or timed out")
    responses = [strict_json(line) for line in raw_streams["stdout"].splitlines() if line.strip()]
    if "responses" in stage and canonical(stage["responses"]) != canonical(responses):
        raise ValueError("recorded responses differ from captured STDIO")
    if len(responses) != 2 or any(type(row.get("id")) is not int for row in responses):
        raise ValueError("diagnostic requires exactly two integer response IDs")
    if [row.get("id") for row in responses] != [1, 2] or any(row.get("jsonrpc") != "2.0" for row in responses):
        raise ValueError("diagnostic response IDs or JSON-RPC version differ")
    initialized = responses[0].get("result", {})
    if ("error" in responses[0] or initialized.get("serverInfo") != {"name": "zerorun", "version": "0.5.2"}
            or initialized.get("protocolVersion") != "2025-11-25"):
        raise ValueError("diagnostic initialized wrong server or protocol")
    response = responses[1]
    if "error" in response:
        raise ValueError("diagnostic tool returned a JSON-RPC error")
    result = response.get("result", {})
    payload, content = result.get("structuredContent"), result.get("content")
    if (not isinstance(payload, dict) or type(result.get("isError")) is not bool
            or not isinstance(content, list) or len(content) != 1
            or content[0].get("type") != "text"
            or canonical(strict_json(content[0]["text"])) != canonical(payload)):
        raise ValueError("diagnostic structured/text/error contract differs")
    if name == "negative_doctor":
        tasks = payload.get("tasks", [])
        if (result["isError"] is not True or payload.get("ok") is not False
                or payload.get("manifest_authorized") is not False
                or payload.get("mode") != "observe-only" or payload.get("reuse_ready") is not False
                or payload.get("root") != str(root) or len(tasks) != 1
                or tasks[0].get("task") != "synthetic-lifecycle"
                or tasks[0].get("status") != "UNTRUSTED" or tasks[0].get("cache_key") is not None):
            raise ValueError("missing-variable control did not refuse authority")
        key = None
    elif name == "explicit_doctor":
        tasks = payload.get("tasks", [])
        if (result["isError"] is not False or payload.get("ok") is not True
                or payload.get("manifest_authorized") is not True or payload.get("mode") != "task-reuse"
                or payload.get("reuse_ready") is not True or payload.get("task_reuse_ready") is not True
                or payload.get("manifest_version") != 2 or payload.get("root") != str(root)
                or len(tasks) != 1 or tasks[0].get("task") != "synthetic-lifecycle"
                or tasks[0].get("status") != "CACHEABLE" or tasks[0].get("reason") is not None):
            raise ValueError("explicit trust path did not establish readiness")
        key = tasks[0].get("cache_key")
    else:
        expected = {"miss": "MISS_EXECUTED", "hit": "HIT_REUSED", "verify": "VERIFY_MATCH"}[name]
        if (result["isError"] is not False or payload.get("status") != expected
                or type(payload.get("exit_code")) is not int or payload["exit_code"] != 0
                or payload.get("mode") != "reuse" or payload.get("task") != "synthetic-lifecycle"
                or payload.get("verified") is not (name == "verify") or payload.get("restored_outputs") != []
                or payload.get("stdout_tail") != "" or payload.get("stderr_tail") != ""):
            raise ValueError("diagnostic lifecycle result contradicts fixed expectation")
        key = payload.get("cache_key")
    if key is not None and (not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key)):
        raise ValueError("invalid cache key")
    if name != "negative_doctor" and key is None:
        raise ValueError("missing cache key")
    return {"responses": responses, "payload": payload, "cache_key": key}
