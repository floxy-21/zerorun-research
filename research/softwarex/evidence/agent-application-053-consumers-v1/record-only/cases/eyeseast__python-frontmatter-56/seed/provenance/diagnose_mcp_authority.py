"""Separate, bounded, synthetic-only STDIO authority diagnostic; never calls a model."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

if __package__:
    from . import run_public_lifecycle as adapter
else:
    import run_public_lifecycle as adapter

SCHEMA = "zerorun.softwarex-non-model-authority-diagnostic.v1"
PROTOCOL_SHA256 = "53996ac78f6da52058ad82247b92e16a72b6cd67b721ed13ecb006569480d22a"
ADAPTER_SHA256 = "f79ad5910223213fdf8a3acd4e1117f8a1ca31d7f1ed066cdd846bac7fc914c2"
LIVE_SHA256 = "d1afbce8b54a2e6b65ebb10631a6e21bfc956a2096b77396d7e1a608de221879"
COMMIT = "681907860dc2ab9df70034f82a0025463d1fdec4"
IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
LIMIT = 2 * 1024 * 1024
STAGES = ("negative_doctor", "explicit_doctor", "miss", "hit", "verify")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def strict_json(raw):
    if len(raw) > LIMIT:
        raise ValueError("JSON exceeds diagnostic bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite JSON: " + value)
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    canonical(result)
    return result


def request_plan(name, root):
    if name not in STAGES:
        raise ValueError("unknown fixed diagnostic stage")
    tool = "doctor" if name.endswith("doctor") else "run_tests"
    arguments = {"root": str(root)}
    if tool == "run_tests":
        arguments.update(task="synthetic-lifecycle", verify=name == "verify")
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "zerorun-non-model-authority-diagnostic", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": tool, "arguments": arguments}},
    ]


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
    if ("error" in responses[0] or initialized.get("serverInfo") != {"name": "zerorun", "version": "0.5.1"}
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


def bindings(directory, live):
    adapter.checked_regular_file(directory / "NON_MODEL_DIAGNOSTIC_PROTOCOL.md", PROTOCOL_SHA256, "diagnostic protocol")
    adapter.checked_regular_file(directory / "run_public_lifecycle.py", ADAPTER_SHA256, "frozen public adapter")
    adapter.checked_regular_file(directory / "LIVE_CLIENT_PROTOCOL.md", adapter.PROTOCOL_SHA256, "original live protocol")
    adapter.checked_regular_file(directory / "LIVE_CLIENT_AMENDMENT_1.md", adapter.AMENDMENT_SHA256, "original support amendment")
    adapter.checked_regular_file(directory / "support/tools" / adapter.SUPPORT_HELPER, adapter.SUPPORT_HELPER_SHA256, "original support helper")
    adapter.checked_regular_file(live, LIVE_SHA256, "adverse live receipt")
    return {"public_adapter_sha256": ADAPTER_SHA256, "original_protocol_sha256": adapter.PROTOCOL_SHA256,
            "public_manifest_sha256": adapter.PINNED_MANIFEST, "amendment_sha256": adapter.AMENDMENT_SHA256,
            "support_helper_sha256": adapter.SUPPORT_HELPER_SHA256, "protected_helpers_sha256": adapter.PROTECTED,
            "source_commit": COMMIT}


def validate_saved_receipt(root, receipt_path):
    """Read-only validation; no subprocess, authority, model, or imported runtime."""
    directory = Path(root) / "research/softwarex"
    if not directory.is_dir():
        directory = Path(root)
    receipt = strict_json(Path(receipt_path).read_bytes())
    expected = adapter.canonical_hash({k: v for k, v in receipt.items() if k != "evidence_payload_sha256"})
    if receipt.get("evidence_payload_sha256") != expected or receipt.get("schema") != SCHEMA:
        raise ValueError("diagnostic envelope schema or hash differs")
    if (receipt.get("model_called") is not False or receipt.get("codex_correction_tested") is not False
            or receipt.get("real_repository_authorized") is not False
            or receipt.get("original_live_receipt_sha256") != LIVE_SHA256
            or receipt.get("protocol_sha256") != PROTOCOL_SHA256
            or receipt.get("helper_sha256") != adapter.digest(directory / Path(__file__).name)):
        raise ValueError("diagnostic scope or producer bindings differ")
    expected_bindings = bindings(directory, directory / "evidence/live-client-v1/receipt.json")
    if receipt.get("source_bindings") != expected_bindings:
        raise ValueError("diagnostic source bindings differ")
    stages = receipt.get("stages", [])
    if [row["name"] for row in stages] != list(STAGES[:len(stages)]) or len(stages) > 5:
        raise ValueError("diagnostic stage order differs")
    validated, failures, keys = 0, [], []
    negative_passed = False
    for stage in stages:
        try:
            result = validate_exchange(stage, receipt["synthetic"]["path"])
            validated += 1
            if stage["name"] == "negative_doctor":
                negative_passed = True
            if result["cache_key"] is not None:
                keys.append(result["cache_key"])
        except (ValueError, KeyError, TypeError) as exc:
            failures.append(str(exc))
    if receipt.get("passed") is True:
        if (validated != 5 or failures or len(set(keys)) != 1
                or receipt.get("postflight", {}).get("passed") is not True):
            raise ValueError("diagnostic pass is unsupported by exchanges or postflight")
        if (receipt["installed_before"] != receipt["installed_after"]
                or receipt["source_before"] != receipt["source_after"]
                or receipt["installed_before"]["module"]["files"] != receipt["source_package"]["files"]
                or receipt["installed_before"]["module"]["file_count"] != 36):
            raise ValueError("diagnostic pass has inconsistent source/installed identities")
    elif not isinstance(receipt.get("failure"), dict):
        raise ValueError("adverse diagnostic has no recorded failure")
    return {"passed": receipt["passed"], "stages_recorded": len(stages), "stages_validated": validated,
            "negative_control_passed": negative_passed,
            "model_called": False, "codex_correction_tested": False, "validation_failures": failures}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--live-receipt", required=True, type=Path)
    parser.add_argument("--zerorun-command", required=True)
    parser.add_argument("--zerorun-python-command", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approve-synthetic-formative-authority", action="store_true")
    args = parser.parse_args(argv)
    if not args.approve_synthetic_formative_authority:
        raise ValueError("explicit synthetic-only authority acknowledgement is required")
    root = adapter.preflight(args.source_root)
    directory = Path(__file__).parent.resolve()
    source_bindings = bindings(directory, args.live_receipt)
    support_tools, helper = adapter.support_preflight(root, directory)
    sys.path.insert(0, str(root))
    support = adapter.install_support_namespace(root, support_tools, helper)
    lifecycle = importlib.import_module("tools.codex_agent_lifecycle")
    smoke = importlib.import_module("tools.codex_agent_integration_smoke")
    installer = importlib.import_module("tools.install_verified_codex_cli")
    adapter.validate_imports(root, {"tools/codex_agent_lifecycle.py": lifecycle,
        "tools/codex_agent_integration_smoke.py": smoke, "tools/install_verified_codex_cli.py": installer})
    if lifecycle.codex_install_evidence is not support:
        raise ValueError("wrong original support module")
    output, _ = smoke._validate_output_destination(args.output)
    if output.exists():
        raise ValueError("diagnostic output already exists; no retry or overwrite")
    if output.is_relative_to(root):
        raise ValueError("diagnostic evidence must stay outside the pinned checkout")
    runner = smoke._default_runner
    environment, _ = smoke._sanitize_environment(dict(os.environ))
    environment.pop("ZERORUN_TRUST_ROOT", None)
    git = shutil.which("git")
    if not git:
        raise ValueError("git is required")
    _, zerorun = smoke._resolve_executable(args.zerorun_command, name="installed ZeroRun", repository_root=root)
    receipt = {"schema": SCHEMA, "started_utc": datetime.now(timezone.utc).isoformat(),
        "model_called": False, "codex_correction_tested": False, "real_repository_authorized": False,
        "passed": False, "original_live_receipt_sha256": LIVE_SHA256, "protocol_sha256": PROTOCOL_SHA256,
        "helper_sha256": adapter.digest(Path(__file__)), "source_bindings": source_bindings, "stages": []}
    try:
        source_before = smoke._git_snapshot(runner, git=git, root=root, environment=environment)
        if source_before["head"] != COMMIT or source_before["branch"] != "main" or source_before["status"]["bytes"]:
            raise ValueError("diagnostic source must be the clean pinned public main")
        receipt["source_before"] = source_before
        installed = lifecycle._installed_package_identity(runner, zerorun=zerorun,
            python_command=args.zerorun_python_command, source_root=root, environment=environment)
        package = lifecycle._source_package_identity(root / "src")
        receipt.update(installed_before=installed, source_package=package)
        if installed["module"]["files"] != package["files"] or package["file_count"] != 36:
            raise ValueError("installed 36-file runtime differs from frozen public source")
        with lifecycle.private_temporary_directory(Path(tempfile.gettempdir()), prefix="zerorun-non-model-authority-") as temporary:
            temporary = adapter.real_directory(temporary)
            repository, trust = temporary / "repository", temporary / "external-authority"
            home = temporary / "empty-process-home"
            home.mkdir()
            env = dict(environment, HOME=str(home), USERPROFILE=str(home))
            for name in ("ZERORUN_TRUST_ROOT", "XDG_STATE_HOME", "LOCALAPPDATA"):
                env.pop(name, None)
            fixture = lifecycle._create_synthetic_repository(runner, root=repository, runtime_image=IMAGE, git=git, environment=env)
            manifest_sha = adapter.digest(repository / ".zerorun.json")
            before = lifecycle._authority_tree_identity(trust)
            explicit = dict(env, ZERORUN_TRUST_ROOT=str(trust))
            synthetic = {"path": str(repository), "fixture_identity": fixture, "manifest_sha256": manifest_sha,
                         "runtime_image": IMAGE, "authority_before": before, "synthetic_only": True,
                         "external_authority_path": str(trust), "isolated_process_home": str(home)}
            receipt["synthetic"] = synthetic
            authorization = smoke._run_small(runner, [str(zerorun), "--manifest", str(repository / ".zerorun.json"),
                "--json", "authorize", "--manifest-sha256", manifest_sha], label="diagnostic synthetic exact-hash authorization",
                cwd=temporary, environment=explicit)
            synthetic["authorization_streams"] = smoke._encoded_stream(authorization)
            response = smoke._load_object(authorization.stdout, label="diagnostic authority response")
            after = lifecycle._authority_tree_identity(trust)
            synthetic.update(authorization=response, authority_after=after)
            if (response != {"status": "AUTHORIZED", "manifest_sha256": manifest_sha,
                            "pytest_profile_sha256": None, "authority_location": "external-per-user"}
                    or before["present"] or after["file_count"] != 2):
                raise ValueError("diagnostic synthetic authority scope differs")
            keys = []
            for name in STAGES:
                requests = request_plan(name, repository)
                command = [str(zerorun), "mcp-server"]
                result = runner(command, cwd=repository, environment=env if name == "negative_doctor" else explicit,
                    timeout_seconds=120.0, output_limit_bytes=LIMIT,
                    input_bytes=b"".join(canonical(row) + b"\n" for row in requests))
                stage = {"name": name, "requests": requests, "command": command,
                         "explicit_trust_root": name != "negative_doctor", "streams": smoke._encoded_stream(result),
                         "recorded_environment_intervention": {
                             "HOME": str(home), "USERPROFILE": str(home),
                             "ZERORUN_TRUST_ROOT": None if name == "negative_doctor" else str(trust),
                             "XDG_STATE_HOME": None, "LOCALAPPDATA": None},
                         "other_environment_values_recorded": False}
                receipt["stages"].append(stage)
                analyzed = validate_exchange(stage, repository)
                stage["responses"] = analyzed["responses"]
                if name == "negative_doctor" and (repository / ".zerorun").exists():
                    raise ValueError("negative doctor unexpectedly created cache state")
                if analyzed["cache_key"] is not None:
                    keys.append(analyzed["cache_key"])
                if lifecycle._synthetic_source_identity(runner, git=git, root=repository, environment=env, runtime_image=IMAGE) != fixture:
                    raise ValueError("synthetic inputs drifted during diagnostic")
            if len(set(keys)) != 1 or lifecycle._authority_tree_identity(trust) != after:
                raise ValueError("diagnostic cache keys or authority changed")
            synthetic["unchanged_inputs_and_authority"] = True
        receipt["temporary_fixture_and_authority_cleaned"] = not temporary.exists()
        receipt["source_after"] = smoke._git_snapshot(runner, git=git, root=root, environment=environment)
        receipt["installed_after"] = lifecycle._installed_package_identity(runner, zerorun=zerorun,
            python_command=args.zerorun_python_command, source_root=root, environment=environment)
        adapter.preflight(root)
        adapter.support_preflight(root, directory)
        if (bindings(directory, args.live_receipt) != source_bindings or receipt["source_after"] != source_before
                or receipt["installed_after"] != installed or not receipt["temporary_fixture_and_authority_cleaned"]):
            raise ValueError("diagnostic postflight identity or cleanup failed")
        receipt["postflight"] = {"passed": True}
        receipt["passed"] = True
    except Exception as exc:
        receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        receipt["postflight"] = {"passed": False}
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["evidence_payload_sha256"] = adapter.canonical_hash(receipt)
    with output.open("xb") as handle:
        handle.write(canonical(receipt) + b"\n")
    print(json.dumps({"receipt": str(output), "passed": receipt["passed"], "model_called": False,
                      "stages": len(receipt["stages"])}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
