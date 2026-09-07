"""Read-only validation for the single prospective API-guided demonstration."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys

SCHEMA = "zerorun.softwarex-guided-client.v1"
PRIOR_V3 = "642bb00b576f878ea474a9caa972a77087b5163e3734d77caea892a5dc826b94"
FROZEN_V3 = {
    "PROTOCOL.md": "7227f042f74dd558342611366f773412cbd2e9de36ca8ff78ec627f85594f68f",
    "__init__.py": "ab1dd147c40dcbbfddc5aa122dcef9569765d9b8b7dd90ff32856f7275c190b7",
    "run.py": "7431c8727df2920d5e3ded194ef7ffe324bfa2bf594434c372547969f2476519",
    "validation.py": "39a03d2b0166e69697137ec4a4ca6830d1b00fac1b5ff5cac25f4d87b107a733",
    "test_validation.py": "d4354e62a8e09d4ed111175117512e7b1937dff88d419fac6075b855edce4e2f",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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


def load_v3(directory=None):
    directory = (directory or Path(__file__).resolve().parent.parent / "live_client_v3").absolute()
    if directory.resolve(strict=True) != directory:
        raise ValueError("frozen v3 directory is linked")
    for name, expected in FROZEN_V3.items():
        path = directory / name
        if path.is_symlink() or sha(path.read_bytes()) != expected:
            raise ValueError("frozen v3 source differs: " + name)
    package_name = "_zerorun_guided_frozen_v3_" + sha(str(directory).encode())[:12]
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(package_name, directory / "__init__.py", submodule_search_locations=[str(directory)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(package_name + ".run"), importlib.import_module(package_name + ".validation")


def source_bindings(directory):
    names = ("PROTOCOL.md", "__init__.py", "run.py", "validation.py", "test_validation.py", "../CLIENT_API_CARD.md")
    return [{"path": name, "sha256": sha((directory / name).read_bytes()), "bytes": (directory / name).stat().st_size} for name in names]


def setup_requests(root):
    return [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "zerorun-guided-setup", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "doctor", "arguments": {"root": root}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "run_tests", "arguments": {"task": "synthetic-lifecycle", "root": root, "verify": False}}}]


def setup_analysis(setup, root, v3):
    raw = v3.streams(setup["streams"])
    streams = setup["streams"]
    if streams["returncode"] != 0 or any(streams[name] for name in ("timed_out", "stdout_truncated", "stderr_truncated")):
        raise ValueError("non-model setup process failed")
    if setup["requests"] != setup_requests(root) or not raw.endswith(b"\n"):
        raise ValueError("non-model setup request plan or output incomplete")
    replies = [strict_json(line) for line in raw.splitlines()]
    if len(replies) != 4 or [row.get("id") for row in replies] != [1, 2, 3, 4]:
        raise ValueError("non-model setup response count or IDs differ")
    if any(row.get("jsonrpc") != "2.0" or "error" in row for row in replies):
        raise ValueError("non-model setup protocol failure")
    initialized = replies[0]["result"]
    if initialized.get("serverInfo") != {"name": "zerorun", "version": "0.5.1"}:
        raise ValueError("wrong installed server identity")
    tools = replies[1]["result"]["tools"]
    run_tools = [tool for tool in tools if tool.get("name") == "run_tests"]
    if len(run_tools) != 1:
        raise ValueError("run_tests discovery absent or duplicated")
    schema = run_tools[0]["inputSchema"]
    if (schema.get("type") != "object" or schema.get("required") != ["task"] or schema.get("additionalProperties") is not False
            or set(schema.get("properties", {})) != {"root", "task", "verify"}
            or schema["properties"]["task"] != {"type": "string"}
            or schema["properties"]["root"].get("type") != "string"
            or schema["properties"]["verify"] != {"type": "boolean", "default": False}):
        raise ValueError("captured API schema differs from frozen interface")
    def response(row):
        result = row["result"]
        if type(result.get("isError")) is not bool:
            raise ValueError("setup result requires explicit error flag")
        item = {"status": "failed" if result["isError"] else "completed", "error": None,
                "result": {"structured_content": result.get("structuredContent"), "content": result.get("content"), "isError": result["isError"]}}
        return v3.payload_from_call(item)
    doctor, seed = response(replies[2]), response(replies[3])
    v3.oracle(doctor, doctor=True)
    v3.oracle(seed)
    tasks = doctor["payload"].get("tasks")
    if (doctor["payload"].get("root") != root or not isinstance(tasks, list) or len(tasks) != 1
            or tasks[0].get("task") != v3.TASK or tasks[0].get("status") != "CACHEABLE"
            or seed["payload"].get("status") != "MISS_EXECUTED"
            or seed["payload"].get("cache_key") != tasks[0].get("cache_key")):
        raise ValueError("non-model readiness/seed outcome or key differs")
    return {"discovered_tools": tools, "run_tests_schema": schema, "doctor": doctor, "seed": seed,
            "non_model_tool_calls": 2, "model_calls": 0, "cache_key": seed["payload"]["cache_key"]}


def guided_prompt(base, card, name, arguments, input_response):
    return base.prompt_for(name, arguments, input_response) + "\n\nFrozen input-interface reference (not an expected result):\n" + card


def identity_pass(value):
    try:
        p = value["identity"]
        return (p["source_before"] == p["source_after"] and p["installed_before"] == p["installed_after"]
                and p["codex_before"] == p["codex_after"] and p["fixture_before"] == p["fixture_after"]
                and p["authority_before"] == p["authority_after"] and p["authority_before"]["file_count"] == 2
                and p["source_before"]["head"] == "ebf2884df12573d63f45813200e0675288d12096"
                and p["source_before"]["branch"] == "main" and p["source_before"]["status"]["bytes"] == 0
                and p["source_before"]["diff_from_head"]["bytes"] == 0
                and p["installed_before"]["module"]["file_count"] == 36
                and p["installed_before"]["module"]["identity_sha256"] == "0bb214d1c9b33a2e3b7a3aa81e8f2469024cb6ddaab46c3fa41a9272502a57d5"
                and sha(canonical(p["installed_before"]["module"]["files"])) == p["installed_before"]["module"]["identity_sha256"]
                and p["codex_before"]["receipt_sha256"] == "52a9bfbbef880d817cfa48479da1b65389e272946d765aad4863d8f1ad290858"
                and p["codex_before"]["source_commit"] == "681907860dc2ab9df70034f82a0025463d1fdec4"
                and p["codex_before"]["codex_version"] == "0.153.3"
                and p["codex_before"]["native_sha256"] == "f9d4eab23d0e0726340e084ed22d668885c1dcabeb29ec508b8962e5e29b8dc6"
                and p["codex_before"]["node_sha256"] == "1abce2374a485bddae3c27b17a3e3143e2780232026e627c4fe74ddde3f380a1"
                and p["temporary_fixture_cleaned"] is True and p["public_runtime_rechecked"] is True
                and p["sources_and_prior_trials_rechecked"] is True)
    except KeyError:
        return False


def summarize(value, v3):
    stages = value["stages"]
    passed = lambda row: all(row["judgment"][key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass"))
    choices = [row for row in stages if row["name"] in v3.CHOICES]
    cases = [row for row in stages if row["name"] in v3.CASES]
    try:
        setup = setup_analysis(value["setup"], value["synthetic_path"], v3)
        setup_pass = True
    except (KeyError, ValueError, TypeError):
        setup, setup_pass = None, False
    oracles = value["fresh_oracles"]
    fresh_pass = len(oracles) == 2 and all(v3.fresh_oracle_pass(row, value["synthetic_path"]) for row in oracles)
    keys = [setup["cache_key"]] if setup else []
    for row in choices:
        result = row["judgment"].get("analysis", {}).get("response")
        if row["judgment"]["tool_result_pass"] and result:
            keys.append(result["payload"]["cache_key"])
    common = len(keys) == 3 and len(set(keys)) == 1
    return {"non_model_setup_pass": setup_pass, "non_model_setup_tool_calls": 2 if setup_pass else 0,
            "live_decision_turns_completed": len(choices), "live_decision_turns_passed": sum(passed(row) for row in choices),
            "interpretation_cases_completed": len(cases), "interpretation_cases_passed": sum(passed(row) for row in cases),
            "fresh_oracle_calls": len(oracles), "fresh_oracles_pass": fresh_pass, "common_seed_and_decision_key": common,
            "all_planned_checks_pass": len(stages) == 8 and all(passed(row) for row in stages) and setup_pass and fresh_pass and common and identity_pass(value),
            "causal_documentation_improvement_claim": False, "autonomous_workflow_speedup_claim": False,
            "population_accuracy_claim": False}


def validate_receipt(path, *, directory=None, frozen_v3_directory=None):
    directory = directory or Path(__file__).resolve().parent
    base, v3 = load_v3(frozen_v3_directory or directory.parent / "live_client_v3")
    value = strict_json(Path(path).read_bytes())
    if value.get("schema") != SCHEMA or value.get("evidence_payload_sha256") != sha(canonical({k: v for k, v in value.items() if k != "evidence_payload_sha256"})):
        raise ValueError("guided receipt schema/seal differs")
    freeze = value["freeze"]
    if freeze["sources"] != source_bindings(directory) or value["freeze_sha256"] != sha(canonical(freeze)):
        raise ValueError("guided frozen source differs")
    expected = {"source_commit": v3.COMMIT, "public_manifest_sha256": v3.MANIFEST_SHA256,
                "frozen_v3_sources": FROZEN_V3, "prior_receipts": dict(base.PRIOR_TRIALS, v3=PRIOR_V3),
                "scoped_tool_preauthorization": base.SCOPED_CONSENT, "stage_order": list(v3.CHOICES + v3.CASES),
                "maximum_model_turns": 8, "timeout_seconds": v3.TIMEOUT, "output_limit_bytes": v3.LIMIT}
    if any(canonical(freeze.get(key)) != canonical(val) for key, val in expected.items()):
        raise ValueError("guided prospective bounds differ")
    if value.get("prior_trials_reclassified") is not False or value.get("runtime_modified") is not False or value.get("real_repository_authorized") is not False:
        raise ValueError("guided scope differs")
    stages = value["stages"]
    order = v3.CHOICES + v3.CASES
    if tuple(row["name"] for row in stages) != order[:len(stages)] or len(stages) > 8:
        raise ValueError("guided sequence differs")
    if stages:
        setup = setup_analysis(value["setup"], value["synthetic_path"], v3)
        if value["setup"]["analysis"] != setup:
            raise ValueError("setup analysis differs from actual streams")
        captured = {"miss": setup["seed"]}
        card = (directory.parent / "CLIENT_API_CARD.md").read_text(encoding="utf-8")
        stopped = False
        for index, row in enumerate(stages):
            if stopped:
                raise ValueError("guided turn occurred after frozen stopping rule")
            name = row["name"]
            tool, arguments = base.plan(name, value["synthetic_path"])
            input_response = dict(base.controlled_cases(captured))[name] if name in v3.CASES else None
            if row["tool"] != tool or canonical(row["expected_arguments"]) != canonical(arguments) or canonical(row["input_response"]) != canonical(input_response):
                raise ValueError("guided plan or captured-input binding differs")
            if row["diagnostics_required"] is not (name == v3.CASES[-1]):
                raise ValueError("guided diagnostic need differs")
            if row["prompt"] != guided_prompt(base, card, name, arguments, input_response) or row["prompt_utf8_sha256"] != sha(row["prompt"].encode()):
                raise ValueError("guided API card/prompt differs")
            result = v3.grade(row)
            if canonical(row["judgment"]) != canonical(result):
                raise ValueError("guided judgment differs from raw transcript")
            command = row["command"]
            if ("--sandbox" not in command or command[command.index("--sandbox") + 1] != "read-only"
                    or "--ignore-user-config" not in command or "--strict-config" not in command):
                raise ValueError("guided isolation differs")
            if tool and command.count('mcp_servers.zerorun.tools.run_tests.approval_mode="approve"') != 1:
                raise ValueError("guided scoped preauthorization absent")
            if not tool and ("mcp_servers={}" not in command or any('approval_mode="approve"' in part for part in command)):
                raise ValueError("guided interpretation exposed execution tools")
            if tool and command.count("mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT=" + json.dumps(value["explicit_trust_path"])) != 1:
                raise ValueError("guided trust forwarding differs")
            stopped = not all(result[key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass")) if tool else not result["protocol_pass"]
            if tool and not stopped and index + 1 < len(stages):
                matching = [oracle for oracle in value["fresh_oracles"] if oracle["after_stage"] == name]
                if len(matching) != 1 or not v3.fresh_oracle_pass(matching[0], value["synthetic_path"]):
                    raise ValueError("guided model work continued after missing or failed fresh oracle")
            if tool and result.get("analysis", {}).get("response"):
                captured["hit" if name == "accept_prior" else "verify"] = result["analysis"]["response"]
    expected_oracles = [row["name"] for row in stages if row["name"] in v3.CHOICES and all(row["judgment"][k] for k in ("protocol_pass", "tool_result_pass", "semantic_pass"))]
    oracles = value["fresh_oracles"]
    if [row["after_stage"] for row in oracles] != expected_oracles[:len(oracles)]:
        raise ValueError("guided fresh-oracle association differs")
    for row in oracles:
        if row["passed"] is not v3.fresh_oracle_pass(row, value["synthetic_path"]):
            raise ValueError("guided fresh-oracle outcome differs")
    actual = summarize(value, v3)
    if value["summary"] != actual:
        raise ValueError("guided summary differs")
    return actual
