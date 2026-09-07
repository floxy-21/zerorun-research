"""Read-only reconciliation of descriptive, scripted-client, and live evidence.

Final mode requires a recorded live outcome, not a passing outcome. A missing
live record is allowed only in explicitly marked preview output. No execution,
authority creation, model call, or performance experiment occurs here.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re

from research.softwarex import analyze_operating_region as op

ROOT = Path(__file__).resolve().parents[2]
HERE = "research/softwarex"
CLIENT_BASE = HERE + "/evidence/client-conformance-v1"
CLIENT_ATTEMPT = CLIENT_BASE + "/amendment-3/attempt-20260906T235617052057Z"
# The final receipt is fixed independently of its prospective producer binding.
CLIENT_RECEIPT_SHA = "05d01dcac41d8c4959755a5aae2239e8b5bdec5311853060cc20ca67f3d1a923"
PRIOR_CLIENT_RECEIPTS = {
    CLIENT_BASE + "/amendment-1/attempt-20260906T235321276129Z/receipt.json": "39d9c8c2ce7d4fd17272ce21fee323f18bde593383b3e0cd6f3b40db2fd37385",
    CLIENT_BASE + "/amendment-2/attempt-20260906T235427829622Z/receipt.json": "391b46e6413f86b7d7a240652475750c737d460d3cb973a08d7475d7eb7586cf",
}
LIVE_BASE = HERE + "/evidence/live-client-v1"
OUTPUT = HERE + "/generated/extension-evidence-v1.json"
CORE_COMMIT = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
LIVE_PUBLIC_COMMIT = "681907860dc2ab9df70034f82a0025463d1fdec4"
LIVE_MANIFEST_SHA = "76bded3e8312517594c3977fa311c1cc4e3060bd7c7a390f34b5055f2cc632dc"
LIVE_PROTOCOL_SHA = "da254c66e5fc60fd03a2ce3c5125a5850de5aedf69941e3ac982f2e45d3591a5"
DIAGNOSTIC_PROTOCOL_SHA = "53996ac78f6da52058ad82247b92e16a72b6cd67b721ed13ecb006569480d22a"
DIAGNOSTIC_PATH = LIVE_BASE + "/non-model-diagnostic.json"
TOOLS = {"doctor", "list_tasks", "stats", "explain", "prepare_pytest", "run_tests", "run_pytest"}
FIXTURE_CHECKS = {"no_authority_or_cache_created", "fixture_inputs_unchanged", "no_reviewed_manifest_created"}
PROTECTED_HELPERS = {
    "tools/codex_agent_lifecycle.py": ("9db6ac92823ddeb07505d4e982592c28f0e5252c444c890c53e8e26718923805", "979809e79d4b106d968980ef36c9fa71346a5950c15c02c7fabd11aa543a61d6"),
    "tools/codex_agent_integration_smoke.py": ("53a3c86f88aef0cee7a63571a5c22984b17d92831cb746b434f3e6ed5744f328", "df83a264863e25606fff8c63fc70d4dc407174df181173ede909b438cff6496d"),
    "tools/install_verified_codex_cli.py": ("dfd2eab91d114c6d10847092da338d374b8d7833020a473d9b63edb8bc13fae3", "0f9b4d2ebc4a08b156bd89260388fba018d1e9e5af01096f60a2d35e39d994a8"),
}
MARKERS = ("ZERORUN_SYNTHETIC_DOCTOR_COMPLETE", "ZERORUN_SYNTHETIC_MISS_COMPLETE", "ZERORUN_SYNTHETIC_HIT_COMPLETE", "ZERORUN_SYNTHETIC_VERIFY_COMPLETE")
STATUSES = (None, "MISS_EXECUTED", "HIT_REUSED", "VERIFY_MATCH")
SUPPORT_AMENDMENT = {
    "schema": "zerorun.public-lifecycle-support-amendment.v1",
    "amendment_file": "LIVE_CLIENT_AMENDMENT_1.md",
    "amendment_sha256": "e5c844c1d03d9eb531af24517ae9e70c2ebbbf12ec4de6e8b6409bb8c93db7d6",
    "helper_path": "support/tools/aggregate_codex_install_evidence.py",
    "helper_sha256": "a5eafbb44d36751bcf84dc353a6625a02b9dfb08e8a04a95e6b37a3bb689fc21",
    "helper_original_path": "tools/aggregate_codex_install_evidence.py",
    "helper_source_commit": "f67cac4a9067bc7dc6debc86f31d5e62e9aedf78",
    "source_checkout_modified": False,
}


require, same = op.require, op.same


def read(root, name):
    return op.strict_json(op.read_regular(root, name))


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def record(root, name):
    return op.file_record(root, name)


def canonical_hash(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())


def self_hash(value, label):
    require(isinstance(value, dict) and value.get("evidence_payload_sha256") == canonical_hash(
        {key: item for key, item in value.items() if key != "evidence_payload_sha256"}), label + " self-hash mismatch")


def exact_module(root, name, relative):
    module = importlib.import_module(name)
    require(Path(module.__file__).resolve() == (root / relative).resolve(), "imported evidence helper from another checkout")
    return module


def expected_core(root):
    receipt = read(root, HERE + "/generated/public-release-tests.json")
    require(receipt["core_commit"] == CORE_COMMIT, "unexpected frozen runtime commit")
    rows = [{"path": row["path"][len("src/zerorun/"):], "bytes": row["bytes"], "sha256": row["sha256"]}
            for row in receipt["tested_code"] if row["path"].startswith("src/zerorun/")
            and row["origin"].startswith("git:" + CORE_COMMIT + ":zerorun/")]
    require(len(rows) == len({row["path"] for row in rows}) == 36, "incomplete exact core inventory")
    return sorted(rows, key=lambda row: row["path"])


def retained_paths(root, relative):
    """Order inventories by case-sensitive relative POSIX labels on every OS."""
    return sorted((root / relative).rglob("*"), key=lambda path: path.relative_to(root).as_posix())


def output_bindings(root, attempt, receipt):
    paths = {path.name for path in (root / attempt).iterdir() if path.is_file()}
    require(paths == set(receipt["output_files"]) | {"receipt.json"}, "unlisted or missing client attempt output")
    for name, expected in receipt["output_files"].items():
        require(op.safe_name(name).name == name, "nested client output name")
        actual = record(root, attempt + "/" + name)
        same({key: actual[key] for key in ("bytes", "sha256")}, expected, "client raw-output binding differs")


def validate_client_transport(module, transport, live, plan):
    require(type(transport["returncode"]) is int and transport["returncode"] == 0, "client process did not complete")
    for name in ("stdout", "stderr"):
        raw = transport[name].encode("utf-8")
        require(len(raw) <= module.LIMIT and sha(raw) == transport[name + "_sha256"], "client transport bytes/hash differ")
    responses = [op.strict_json(line) for line in transport["stdout"].splitlines() if line.strip()]
    same(responses, live["responses"], "live analysis differs from raw stdout")
    require(len(responses) == 8 and all(type(row.get("id")) is int and row.get("jsonrpc") == "2.0" for row in responses), "invalid live response envelopes")
    same([row["id"] for row in responses], list(range(1, 9)), "response IDs differ")
    requests = plan["requests"]
    require(len(requests) == 8 and all(type(row["id"]) is int and row["jsonrpc"] == "2.0" for row in requests), "invalid request envelopes")
    same([row["id"] for row in requests], list(range(1, 9)), "request IDs differ")
    same([row["method"] for row in requests], ["initialize", "tools/list"] + ["tools/call"] * 5 + ["conformance/unknown"], "fixed request sequence changed")
    same([row["params"]["name"] for row in requests[2:7]], ["doctor", "list_tasks", "stats", "run_tests", "stats"], "fixed tool sequence changed")
    require(requests[5]["params"]["arguments"]["task"] == "tests", "unconfigured execution target changed")
    roots = [row["params"]["arguments"]["root"] for row in requests[2:7]]
    require(all(isinstance(value, str) and value for value in roots) and len(set(roots[:4])) == 1 and roots[-1] != roots[0], "repository-bound request fixture changed")
    by_id = {row["id"]: row for row in responses}
    checks = {"initialized_installed_server": by_id[1].get("result", {}).get("serverInfo") == {"name": "zerorun", "version": "0.5.1"}}
    tools = by_id[2].get("result", {}).get("tools", [])
    names = [tool["name"] for tool in tools]
    checks["exact_seven_tools"] = len(names) == 7 and set(names) == TOOLS
    require(all(tool.get("inputSchema", {}).get("type") == "object" and isinstance(tool.get("annotations"), dict) for tool in tools), "tool schema/annotations absent")
    for ident, label in ((3, "doctor"), (4, "list_tasks"), (5, "stats")):
        payload, error = module.response_payload(by_id[ident], expected_id=ident)
        require(error is False, "diagnostic response incorrectly marked tool error")
        checks[label + "_observe_only"] = payload.get("mode") == "observe-only" and payload.get("reuse_ready") is False
    refusal, escape = module.consume(by_id[6], expected_id=6), module.consume(by_id[7], expected_id=7)
    checks["unconfigured_execution_refused"] = refusal["classification"] == "REFUSED" and "no .zerorun.json found" in refusal["reason"]
    checks["other_repository_refused"] = escape["classification"] == "REFUSED" and "refusing access" in escape["reason"]
    checks["unknown_method_protocol_error"] = module.consume(by_id[8], expected_id=8)["classification"] == "PROTOCOL_ERROR"
    require(set(live["checks"]) == set(checks) | FIXTURE_CHECKS, "unexpected client check set")
    same({key: live["checks"][key] for key in checks}, checks, "client raw-response checks disagree")
    require(all(type(value) is bool for value in live["checks"].values()), "nonboolean client outcome")
    return checks


def validate_client(root):
    receipt_path = CLIENT_ATTEMPT + "/receipt.json"
    require(record(root, receipt_path)["sha256"] == CLIENT_RECEIPT_SHA, "fixed client receipt changed")
    receipt = read(root, receipt_path)
    require(receipt["schema"] == "zerorun.softwarex-client-conformance.v1", "wrong client receipt schema")
    protocol_path = CLIENT_BASE + "/amendment-3/protocol.json"
    protocol = read(root, protocol_path)
    require(protocol["schema"] == receipt["schema"] + ".protocol" and receipt["protocol_sha256"] == record(root, protocol_path)["sha256"], "client protocol binding differs")
    for name, bound in receipt["source_bindings"].items():
        current = record(root, name)
        same({key: current[key] for key in ("bytes", "sha256")}, bound, "client producer/source changed")
    module = exact_module(root, "research.softwarex.client_conformance", HERE + "/client_conformance.py")
    same(receipt["source_bindings"], protocol["source_bindings"], "client source/protocol differ")
    same(receipt["source_bindings"], module.source_records(root), "client source inventory incomplete")
    same(protocol["categories"], list(module.CATEGORIES), "fixed client categories changed")
    output_bindings(root, CLIENT_ATTEMPT, receipt)
    expected_cases = module.scripted_cases(root)
    for case in expected_cases:
        case["pass"] = case["decision"]["classification"] == case["expected_classification"] and case["decision"]["accepted_success"] is case["expected_accepted_success"]
    observed = read(root, CLIENT_ATTEMPT + "/scripted-fixtures.json")
    same(observed["cases"], expected_cases, "scripted client cases/decisions differ")
    require(len(expected_cases) == 14, "scripted scenario count changed")
    installed = read(root, CLIENT_ATTEMPT + "/installed-identity.json")
    expected = {row["path"]: row["sha256"] for row in expected_core(root)}
    same(installed["expected"], expected, "installed comparison reference changed")
    same(installed["actual"]["files"], expected, "installed runtime differs from exact core")
    require(installed["actual"]["version"] == "0.5.1" and installed["reference_sha256"] == record(root, HERE + "/generated/public-release-tests.json")["sha256"], "installed core reference/version differs")
    live = read(root, CLIENT_ATTEMPT + "/live-analysis.json")
    plan = read(root, CLIENT_ATTEMPT + "/live-request-plan.json")
    same(plan["installed_runtime"], installed["actual"], "transport used another installed runtime")
    transport = read(root, CLIENT_ATTEMPT + "/live-transport.json")
    checks = validate_client_transport(module, transport, live, plan)
    require(all(live[key] is False for key in ("positive_execution_tested", "operator_authority_created", "model_called")), "scripted conformance scope broadened")
    passed = sum(case["pass"] for case in expected_cases)
    same([receipt["scripted_cases"], receipt["scripted_passed"], receipt["live_passed"]], [len(expected_cases), passed, sum(live["checks"].values())], "client pass counts differ")
    same(receipt["live_checks"], live["checks"], "client check receipt differs")
    require(receipt["source_unchanged"] is True and receipt["completed"] == (passed == 14 and all(live["checks"].values())), "client completion claim differs")
    prior = []
    files = []
    for path in retained_paths(root, CLIENT_BASE):
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            files.append(record(root, relative))
            if path.name == "receipt.json" and relative != receipt_path:
                old = read(root, relative)
                require(PRIOR_CLIENT_RECEIPTS.get(relative) == files[-1]["sha256"], "prior failed client receipt changed")
                require(old["schema"] == receipt["schema"] and old["completed"] is False, "unexpected extra completed client attempt")
                output_bindings(root, path.parent.relative_to(root).as_posix(), old)
                prior.append({"path": relative, "sha256": files[-1]["sha256"], "completed": False,
                              "failure": old.get("failure"), "scripted_passed": old.get("scripted_passed"), "live_passed": old.get("live_passed")})
    require({row["path"] for row in prior} == set(PRIOR_CLIENT_RECEIPTS), "prior failed client attempts missing/expanded")
    return {"recorded": True, "completed": receipt["completed"], "receipt": record(root, receipt_path),
            "scripted_cases": 14, "scripted_passed": passed, "installed_stdio_requests": 8,
            "scenario_category_count": len(module.CATEGORIES), "scenario_categories": list(module.CATEGORIES),
            "live_checks": len(live["checks"]), "live_checks_passed": receipt["live_passed"],
            "raw_response_checks_recomputed": len(checks), "producer_attested_fixture_checks": len(FIXTURE_CHECKS),
            "installed_runtime_files": 36, "positive_mcp_execution_tested": False, "model_called": False,
            "scope": "Scripted MCP-shaped preserved API outputs plus real installed-server diagnostics/refusals; not autonomous-agent evaluation",
            "prior_failed_attempts": prior, "retained_history_files": files}


def helper_bindings(root):
    values = {}
    for name, (published, _) in PROTECTED_HELPERS.items():
        retained = record(root, LIVE_BASE + "/protected-source/" + name)
        require(retained["sha256"] == published, "protected lifecycle helper changed")
        values[name] = retained
    return values


def decoded_stream(value):
    result = {}
    for name in ("stdout", "stderr"):
        raw = base64.b64decode(value[name + "_base64"], validate=True)
        require(type(value[name + "_bytes"]) is int and len(raw) <= 2 * 1024 * 1024 and len(raw) == value[name + "_bytes"] and sha(raw) == value[name + "_sha256"], "live raw stream hash/size differs")
        result[name] = raw
    require(type(value["returncode"]) is int and all(type(value[key]) is bool for key in ("timed_out", "stdout_truncated", "stderr_truncated")), "invalid live process metadata")
    return result


def parse_live_events(raw):
    require(raw.endswith(b"\n"), "live JSONL lacks final newline")
    lines = raw.splitlines()
    require(0 < len(lines) <= 1024 and all(0 < len(line) <= 256 * 1024 for line in lines), "excessive or empty live JSONL")
    events = [op.strict_json(line) for line in lines]
    allowed = {"thread.started", "turn.started", "turn.completed", "turn.failed", "item.started", "item.updated", "item.completed", "error"}
    require(all(isinstance(event, dict) and event.get("type") in allowed for event in events), "unknown live event type")
    return events


def analyze_stage(stage, index, root_path):
    require(index in range(4), "extra live stage")
    streams = decoded_stream(stage["jsonl"])
    require(stage["jsonl"]["returncode"] == 0 and all(stage["jsonl"][key] is False for key in ("timed_out", "stdout_truncated", "stderr_truncated")), "incomplete live stage")
    require(stage["execution_root_unchanged"] is True, "live stage changed isolated execution root")
    require(sha(stage["prompt"].encode()) == stage["prompt_utf8_sha256"], "live stage prompt differs")
    tool = "doctor" if index == 0 else "run_tests"
    args = {"root": root_path} if index == 0 else {"task": "synthetic-lifecycle", "root": root_path, "verify": index == 3}
    same([stage["tool"], stage["verify"]], [tool, None if index == 0 else index == 3], "live stage selection differs")
    events = parse_live_events(streams["stdout"])
    counts, calls, messages = {}, {}, []
    for event in events:
        event_type = event["type"]
        counts[event_type] = counts.get(event_type, 0) + 1
        require(event_type not in {"error", "turn.failed"}, "live error event")
        if not event_type.startswith("item."):
            continue
        item = event["item"]
        require(isinstance(item.get("id"), str) and item["id"], "invalid live item ID")
        if item["type"] == "mcp_tool_call":
            calls.setdefault(item["id"], []).append((event_type, item))
        else:
            require(item["type"] in {"reasoning", "agent_message"} and event_type == "item.completed", "forbidden live tool/item")
            if item["type"] == "agent_message":
                messages.append(item["text"])
    require(all(counts.get(key) == 1 for key in ("thread.started", "turn.started", "turn.completed")) and len(calls) == 1, "not one completed single-call turn")
    same(messages, [MARKERS[index]], "live completion marker differs")
    call_id, phases = next(iter(calls.items()))
    same([name for name, _ in phases], ["item.started", "item.completed"], "live tool lifecycle differs")
    for _, call in phases:
        same([call["server"], call["tool"], call["arguments"]], ["zerorun", tool, args], "unapproved live call or arguments")
    require(phases[0][1]["status"] == "in_progress" and phases[1][1]["status"] == "completed" and phases[1][1].get("error") is None, "live call did not complete")
    result = phases[1][1]["result"]
    payload = result["structured_content"]
    require(isinstance(payload, dict) and len(result["content"]) == 1 and result["content"][0]["type"] == "text", "invalid live MCP payload")
    same(op.strict_json(result["content"][0]["text"]), payload, "live structured/text result mismatch")
    require(result.get("is_error", False) is False and result.get("isError", False) is False, "live tool result reports error")
    expected = {"event_counts": dict(sorted(counts.items())), "mcp_call_count": 1, "call_id": call_id,
                "tool": tool, "arguments": args, "response": payload, "response_sha256": canonical_hash(payload),
                "command_execution_count": 0, "file_change_count": 0, "retry_count": 0, "final_message": MARKERS[index]}
    same(stage["analysis"], expected, "live saved event analysis differs")
    if index == 0:
        require(all(payload.get(key) is True for key in ("ok", "manifest_present", "manifest_authorized", "reuse_ready", "task_reuse_ready")), "doctor did not attest reviewed fixture readiness")
        require(payload.get("manifest_version") == 2 and payload.get("mode") == "task-reuse" and payload.get("root") == root_path, "doctor root/mode differs")
        tasks = payload["tasks"]
        require(len(tasks) == 1 and tasks[0]["task"] == "synthetic-lifecycle" and tasks[0]["status"] == "CACHEABLE" and tasks[0].get("reason") is None, "doctor task differs")
        key = tasks[0]["cache_key"]
    else:
        same([payload.get("task"), payload.get("status"), payload.get("mode")], ["synthetic-lifecycle", STATUSES[index], "reuse"], "live execution status differs")
        require(type(payload.get("exit_code")) is int and payload["exit_code"] == 0 and payload.get("verified") is (index == 3), "live status/exit/verification conflict")
        same([payload.get("restored_outputs"), payload.get("stdout_tail"), payload.get("stderr_tail")], [[], "", ""], "result-only live task produced outputs")
        key = payload["cache_key"]
    require(isinstance(key, str) and re.fullmatch("[0-9a-f]{64}", key), "invalid live cache key")
    return {"index": index, "tool": tool, "status": "READY" if index == 0 else STATUSES[index], "cache_key": key, "mcp_calls": 1}


def validate_tree(tree, expected):
    same(tree["files"], expected, "live package differs from exact installed core")
    require(tree["file_count"] == 36 and tree["bytes"] == sum(row["bytes"] for row in expected)
            and tree["identity_sha256"] == canonical_hash(expected), "live package inventory totals/hash differ")


def describe_adverse_stage(stage, index, root_path):
    """Extract bounded adverse diagnostics without accepting the failed stage.

    This is a separate descriptive path, never a replacement success criterion.
    A malformed or incomplete trace receives no particular refusal classification.
    """
    streams = decoded_stream(stage["jsonl"])
    result = {"classification": "unclassified_failed_stage", "counts_reconciled": False,
              "raw_stdout_sha256": sha(streams["stdout"]), "raw_stderr_sha256": sha(streams["stderr"])}
    try:
        require(stage["jsonl"]["returncode"] == 0 and not any(stage["jsonl"][key] for key in ("timed_out", "stdout_truncated", "stderr_truncated")), "incomplete process")
        require(stage["execution_root_unchanged"] is True and sha(stage["prompt"].encode()) == stage["prompt_utf8_sha256"], "stage inputs changed")
        events = parse_live_events(streams["stdout"])
        require(all(sum(event.get("type") == name for event in events) == 1 for name in ("thread.started", "turn.started", "turn.completed")), "incomplete turn")
        require(not any(event.get("type") in {"error", "turn.failed"} for event in events), "failed turn")
        items = [(event["type"], event["item"]) for event in events if event["type"].startswith("item.")]
        require(all(item.get("type") in {"mcp_tool_call", "reasoning", "agent_message"} for _, item in items), "unapproved item")
        require(all(kind == "item.completed" for kind, item in items if item["type"] != "mcp_tool_call"), "nonterminal non-tool item")
        calls = [(kind, item) for kind, item in items if item["type"] == "mcp_tool_call"]
        same([kind for kind, _ in calls], ["item.started", "item.completed"], "not one call")
        started, ended = calls[0][1], calls[1][1]
        require(started["id"] == ended["id"] and started["status"] == "in_progress" and ended["status"] in {"completed", "failed"}, "call lifecycle differs")
        tool = "doctor" if index == 0 else "run_tests"
        args = {"root": root_path} if index == 0 else {"task": "synthetic-lifecycle", "root": root_path, "verify": index == 3}
        for call in (started, ended):
            same([call["server"], call["tool"], call["arguments"]], ["zerorun", tool, args], "unexpected call")
        messages = [item["text"] for kind, item in items if item["type"] == "agent_message" and kind == "item.completed"]
        result.update(counts_reconciled=True, mcp_calls=1, run_tests_calls=int(index != 0), tool=tool,
                      call_status=ended["status"], agent_message_count=len(messages), exact_marker_only=messages == [MARKERS[index]])
        payload = ended["result"]["structured_content"]
        content = ended["result"]["content"]
        require(isinstance(payload, dict) and len(content) == 1 and content[0]["type"] == "text", "no unambiguous payload")
        same(op.strict_json(content[0]["text"]), payload, "payload disagreement")
        result["response_sha256"] = canonical_hash(payload)
        if index == 0:
            tasks = payload.get("tasks", [])
            refusal = (payload.get("root") == root_path and payload.get("mode") == "observe-only"
                       and payload.get("manifest_present") is True and payload.get("manifest_version") == 2
                       and all(payload.get(key) is False for key in ("ok", "manifest_authorized", "reuse_ready", "task_reuse_ready"))
                       and len(tasks) == 1 and tasks[0].get("task") == "synthetic-lifecycle"
                       and tasks[0].get("status") == "UNTRUSTED" and tasks[0].get("cache_key") is None
                       and isinstance(tasks[0].get("reason"), str) and re.fullmatch(
                           r"manifest has no matching external per-user authority for exact SHA-256 [0-9a-f]{64}", tasks[0]["reason"]))
            if refusal:
                result.update(classification="untrusted_doctor_readiness_refusal", manifest_authorized=False,
                              reuse_ready=False, mode="observe-only", task_status="UNTRUSTED", reason=tasks[0]["reason"])
    except (ValueError, KeyError, TypeError, IndexError) as error:
        result["diagnostic_limitation"] = str(error)
    return result


def validate_live_envelope(envelope, *, expected, adapter_sha, protocol_sha, manifest_sha):
    self_hash(envelope, "outer lifecycle")
    require(envelope["schema"] == "zerorun.softwarex-public-lifecycle.v1", "wrong live envelope schema")
    original = envelope["original_receipt"]
    self_hash(original, "original lifecycle")
    require(original["schema"] == "zerorun.codex-agent-synthetic-lifecycle/v1", "wrong original lifecycle schema")
    metadata = envelope["research_public_layout_adapter"]
    same([metadata["schema"], metadata["adapter_sha256"], metadata["protocol_sha256"], metadata["pinned_public_manifest_sha256"]],
         ["zerorun.public-lifecycle-layout-adapter.v1", adapter_sha, protocol_sha, manifest_sha], "live adapter/protocol/manifest binding differs")
    same(metadata["protected_helpers_sha256"], {name: pair[0] for name, pair in PROTECTED_HELPERS.items()}, "live helper binding differs")
    same(metadata["pre_model_support_amendment"], SUPPORT_AMENDMENT, "live support amendment/helper binding differs")
    require(metadata["only_adaptation"] == "source-package inventory uses root/src/zerorun instead of root/zerorun", "unrecorded live layout adaptation")
    require(metadata["runtime_modified"] is False and metadata["original_validation_and_authority_unchanged"] is True, "adapter broadened runtime/authority scope")
    require(original["real_repository_authorized"] is False and original["runtime_acquisition_attempted"] is False, "live authority/runtime acquisition outside scope")
    require(original["design"]["agent_retry_allowed"] is False and original["design"]["one_mcp_call_per_agent_turn"] is True, "live retry/call policy differs")
    stages = original["agent_stages"]
    require(isinstance(stages, list) and len(stages) <= 4, "extra live stages")
    declared_pass = original["functional_lifecycle_pass"]
    require(type(declared_pass) is bool and type(envelope["source_recheck"]["passed"]) is bool, "nonboolean live outcome")
    require(envelope["functional_lifecycle_pass"] is (declared_pass and envelope["source_recheck"]["passed"]), "outer lifecycle pass disagrees")
    require(envelope["evidence_valid"] is (original["evidence_valid"] is True and envelope["source_recheck"]["passed"]), "outer lifecycle evidence validity disagrees")
    installed_core_validated = False
    if stages or declared_pass:
        validate_tree(original["source"]["package"], expected)
        validate_tree(original["zerorun"]["installed_package"]["module"], expected)
        require(original["source"]["commit"] == LIVE_PUBLIC_COMMIT and original["source"]["git"]["head"] == LIVE_PUBLIC_COMMIT, "live checkout commit differs")
        require(original["zerorun"]["installed_matches_source_package"] is True and original["source"]["harness"]["sha256"] == PROTECTED_HELPERS["tools/codex_agent_lifecycle.py"][0], "live installed source/helper differs")
        installed_core_validated = True
    verified, adverse = [], []
    for index, stage in enumerate(stages):
        # All raw streams are checked even when the turn failed or was truncated.
        decoded_stream(stage["jsonl"])
        try:
            verified.append(analyze_stage(stage, index, original["synthetic_repository"]["path"]))
        except (ValueError, KeyError, TypeError) as error:
            require(not declared_pass, "passing lifecycle has invalid raw stage")
            adverse.append({"index": index, "validation_error": str(error),
                            "raw_diagnostics": describe_adverse_stage(stage, index, original["synthetic_repository"]["path"])})
    if declared_pass:
        require(original["status"] == "pass" and original["evidence_valid"] is True and "failure" not in original and len(verified) == 4, "live pass lacks complete stages")
        validate_tree(original["source"]["package"], expected)
        validate_tree(original["zerorun"]["installed_package"]["module"], expected)
        require(original["zerorun"]["installed_matches_source_package"] is True and original["source"]["harness"]["sha256"] == PROTECTED_HELPERS["tools/codex_agent_lifecycle.py"][0], "live installed source/helper differs")
        same(original["identity_unchanged"], {key: True for key in ("source_worktree", "codex_executable", "zerorun_launcher",
            "zerorun_installed_package", "synthetic_reviewed_source", "codex_authenticated_installation")}, "live source/installation changed")
        require(original["source"]["commit"] == LIVE_PUBLIC_COMMIT and original["source"]["git"]["head"] == LIVE_PUBLIC_COMMIT, "live checkout commit differs")
        same(original["source"]["git"], original["source"]["git_after"], "live checkout changed during experiment")
        require(original["synthetic_repository"]["unchanged_reviewed_source"] is True and original["external_authority"]["synthetic_only"] is True
                and original["external_authority"]["outside_synthetic_repository"] is True and original["external_authority"]["secret_values_recorded"] is False, "live fixture authority/source scope differs")
        require(len({row["cache_key"] for row in verified}) == 1, "live stages use different keys")
        same(original["lifecycle"], {"statuses": list(STATUSES[1:]), "cache_key": verified[0]["cache_key"], "exact_expected_sequence": True,
                                    "total_agent_turns": 4, "total_mcp_calls": 4, "total_retries": 0}, "live lifecycle summary differs")
    else:
        require(original["status"] == "fail_closed" and original["evidence_valid"] is False and isinstance(original.get("failure"), dict), "adverse outcome lacks explicit failure record")
    return {"recorded": True, "functional_lifecycle_pass": envelope["functional_lifecycle_pass"],
            "original_status": original["status"], "agent_stages_recorded": len(stages), "independently_validated_stages": verified,
            "stage_adverse_outcomes": adverse, "failure": original.get("failure"), "source_recheck": envelope["source_recheck"],
            "installed_core_inventory_validated": installed_core_validated,
            "recorded_codex_version": original.get("codex", {}).get("version"),
            "recorded_host_system": original.get("environment", {}).get("system"),
            "new_runtime_or_real_repository_authority": False, "scope": "one constrained synthetic functional lifecycle; no performance or autonomous issue-resolution inference"}


def validate_live(root, *, preview=False):
    path = LIVE_BASE + "/receipt.json"
    if not (root / path).exists():
        require(preview, "final extension requires a recorded live receipt, whether passing or adverse")
        return {"recorded": False, "functional_lifecycle_pass": False, "scope": "preview only; live outcome absent"}
    envelope = read(root, path)
    manifest_path = LIVE_BASE + "/public-experiment-manifest.json"
    manifest = read(root, manifest_path)
    require(record(root, manifest_path)["sha256"] == LIVE_MANIFEST_SHA, "frozen public experiment manifest changed")
    expected = expected_core(root)
    core_rows = [{"path": row["path"][len("src/zerorun/"):], "bytes": row["bytes"], "sha256": row["sha256"]}
                 for row in manifest["files"] if row["path"].startswith("src/zerorun/")]
    same(sorted(core_rows, key=lambda row: row["path"]), expected, "live public manifest core differs")
    for name, (published, _) in PROTECTED_HELPERS.items():
        rows = [row for row in manifest["files"] if row["path"] == name]
        require(len(rows) == 1 and rows[0]["sha256"] == published, "manifest omits exact live helper")
    helpers = helper_bindings(root)
    adapter = record(root, HERE + "/run_public_lifecycle.py")
    protocol = record(root, HERE + "/LIVE_CLIENT_PROTOCOL.md")
    require(protocol["sha256"] == LIVE_PROTOCOL_SHA, "frozen live protocol changed")
    amendment = record(root, HERE + "/" + SUPPORT_AMENDMENT["amendment_file"])
    support = record(root, HERE + "/" + SUPPORT_AMENDMENT["helper_path"])
    require(amendment["sha256"] == SUPPORT_AMENDMENT["amendment_sha256"] and support["sha256"] == SUPPORT_AMENDMENT["helper_sha256"], "retained support amendment/helper changed")
    result = validate_live_envelope(envelope, expected=expected, adapter_sha=adapter["sha256"], protocol_sha=protocol["sha256"], manifest_sha=record(root, manifest_path)["sha256"])
    files = [record(root, item.relative_to(root).as_posix()) for item in retained_paths(root, LIVE_BASE) if item.is_file()]
    return {**result, "receipt": record(root, path), "adapter": adapter, "protocol": protocol, "support_amendment": amendment, "support_helper": support,
            "helper_bindings": helpers, "retained_files": files}


def validate_non_model(root, *, preview=False):
    if not (root / DIAGNOSTIC_PATH).exists():
        require(preview, "final extension requires the separately planned non-model diagnostic outcome")
        return {"recorded": False, "passed": False, "model_called": False, "codex_correction_tested": False}
    before = record(root, DIAGNOSTIC_PATH)
    receipt = read(root, DIAGNOSTIC_PATH)
    self_hash(receipt, "non-model diagnostic")
    producer = record(root, HERE + "/diagnose_mcp_authority.py")
    protocol = record(root, HERE + "/NON_MODEL_DIAGNOSTIC_PROTOCOL.md")
    require(receipt["helper_sha256"] == producer["sha256"] and receipt["protocol_sha256"] == protocol["sha256"] == DIAGNOSTIC_PROTOCOL_SHA, "diagnostic producer/protocol binding differs")
    require(type(receipt["passed"]) is bool and receipt["model_called"] is False and receipt["codex_correction_tested"] is False
            and receipt["real_repository_authorized"] is False, "diagnostic scope or outcome differs")
    module = exact_module(root, "research.softwarex.diagnose_mcp_authority", HERE + "/diagnose_mcp_authority.py")
    require(Path(module.adapter.__file__).resolve() == (root / HERE / "run_public_lifecycle.py").resolve(), "wrong diagnostic adapter imported")
    verified = module.validate_saved_receipt(root, root / DIAGNOSTIC_PATH)
    expected = expected_core(root)
    stages = receipt["stages"]
    if stages or receipt["passed"]:
        validate_tree(receipt["source_package"], expected)
        validate_tree(receipt["installed_before"]["module"], expected)
        require(receipt["source_before"]["head"] == LIVE_PUBLIC_COMMIT, "diagnostic source commit differs")
    outcomes = []
    for index, stage in enumerate(stages):
        decoded_stream(stage["streams"])
        outcome = {"name": stage["name"], "validation_passed": False, "explicit_trust_root": stage["explicit_trust_root"],
                   "stdout_sha256": stage["streams"]["stdout_sha256"], "stderr_sha256": stage["streams"]["stderr_sha256"]}
        try:
            analysis = module.validate_exchange(stage, receipt["synthetic"]["path"])
            if "responses" in stage:
                same(stage["responses"], analysis["responses"], "diagnostic saved responses differ from raw stdout")
            outcome.update(validation_passed=True, responses=len(analysis["responses"]), cache_key=analysis["cache_key"],
                           task_status=analysis["payload"]["tasks"][0]["status"] if index < 2 else analysis["payload"]["status"])
        except (ValueError, KeyError, TypeError) as error:
            require(not receipt["passed"], "passing diagnostic contains an invalid exchange")
            outcome["validation_error"] = str(error)
        outcomes.append(outcome)
    if receipt["passed"]:
        validate_tree(receipt["installed_after"]["module"], expected)
        require(receipt["temporary_fixture_and_authority_cleaned"] is True and receipt["synthetic"]["unchanged_inputs_and_authority"] is True and receipt["synthetic"]["synthetic_only"] is True,
                "diagnostic cleanup or fixture/authority unchanged check failed")
        synthetic = receipt["synthetic"]
        original = read(root, LIVE_BASE + "/receipt.json")["original_receipt"]["synthetic_repository"]
        same([synthetic["fixture_identity"], synthetic["manifest_sha256"], synthetic["runtime_image"]],
             [original["fixture_identity"], original["manifest_sha256"], original["runtime_image"]], "diagnostic changed the sealed built-in fixture/image")
        paths = [PurePosixPath(synthetic[key]) for key in ("path", "external_authority_path", "isolated_process_home")]
        require(all(path.is_absolute() and ".." not in path.parts for path in paths) and len(set(paths)) == 3 and len({path.parent for path in paths}) == 1,
                "diagnostic authority/home/fixture isolation differs")
        for index, stage in enumerate(stages):
            same(stage["recorded_environment_intervention"], {"HOME": str(paths[2]), "USERPROFILE": str(paths[2]),
                "XDG_STATE_HOME": None, "LOCALAPPDATA": None, "ZERORUN_TRUST_ROOT": None if index == 0 else str(paths[1])},
                "diagnostic recorded environment intervention differs")
            require(stage["command"] == stages[0]["command"] and len(stage["command"]) == 2
                    and stage["command"][1] == "mcp-server" and PurePosixPath(stage["command"][0]).name == "zerorun",
                    "diagnostic command was not one unchanged installed STDIO server")
        authorization = synthetic["authorization"]
        authorization_streams = decoded_stream(synthetic["authorization_streams"])
        require(synthetic["authorization_streams"]["returncode"] == 0 and not any(synthetic["authorization_streams"][key]
            for key in ("timed_out", "stdout_truncated", "stderr_truncated")), "diagnostic authorization process failed")
        same(op.strict_json(authorization_streams["stdout"]), authorization, "diagnostic authorization raw response differs")
        same(authorization, {"status": "AUTHORIZED", "manifest_sha256": receipt["synthetic"]["manifest_sha256"],
                             "pytest_profile_sha256": None, "authority_location": "external-per-user"}, "diagnostic authority response differs")
        require(receipt["synthetic"]["authority_before"]["present"] is False and receipt["synthetic"]["authority_after"]["file_count"] == 2,
                "diagnostic authority was not isolated")
    same(record(root, DIAGNOSTIC_PATH), before, "diagnostic receipt changed while reading")
    return {"recorded": True, "passed": receipt["passed"], "receipt": before, "producer": producer, "protocol": protocol,
            "stages_recorded": len(stages), "stages_validated": sum(row["validation_passed"] for row in outcomes),
            "negative_control_passed": bool(outcomes) and outcomes[0]["validation_passed"], "stages": outcomes,
            "original_live_receipt_sha256": receipt["original_live_receipt_sha256"], "failure": receipt.get("failure"),
            "producer_saved_reconciliation": verified, "model_called": False, "codex_correction_tested": False,
            "scope": "Separate fresh-fixture STDIO missing-variable control and explicit external-trust-path intervention; not a rerun or correction test of the failed model trial."}


def build(root=ROOT, *, preview=False):
    root = Path(root).resolve()
    operating = op.analyze(root)
    same(operating, read(root, op.OUTPUT), "saved operating-region analysis differs")
    client = validate_client(root)
    live = validate_live(root, preview=preview)
    diagnostic = validate_non_model(root, preview=preview)
    return {"schema": "zerorun.softwarex-extension-evidence.v1", "completed": not preview and live["recorded"] and diagnostic["recorded"], "preview": preview,
            "operating_region": {"completed": True, "receipt": record(root, op.OUTPUT), "counts": operating["counts"],
                "constructed_hit_fraction": operating["constructed_hit_fraction"],
                "subjects": [{key: row[key] for key in ("workload", "crossover", "block_crossover_range", "category_mean_ms", "observed_mixture_direct_to_fast_ratio", "hit_cost_attribution", "direct_cost_attribution", "interrupted_attempt_sensitivity")}
                             for row in operating["subjects"]]},
            "scripted_client_conformance": client, "bounded_live_client": live, "explicit_trust_path_diagnostic": diagnostic,
            "builder_sha256": record(root, HERE + "/build_extension_evidence.py")["sha256"],
            "interpretation": "Independent reconciliation of stated evidence classes, not acceptance odds, autonomous performance, or commercial qualification."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    require(not args.preview or args.output is not None, "preview requires an explicit separate output path")
    target = args.output or ROOT / OUTPUT
    require(not args.preview or target.resolve() != (ROOT / OUTPUT).resolve(), "preview cannot use the final output path")
    result = build(preview=args.preview)
    raw = op.encoded(result)
    if args.check:
        require(target.read_bytes() == raw, "saved extension evidence differs")
    else:
        with target.open("xb") as stream:
            stream.write(raw)
    print(json.dumps({"completed": result["completed"], "preview": result["preview"], "live_recorded": result["bounded_live_client"]["recorded"], "live_pass": result["bounded_live_client"]["functional_lifecycle_pass"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
