"""Run the prospectively frozen v2 experiment against an unchanged public install."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import validation as v
else:
    from . import validation as v

PROTECTED = {"tools/codex_agent_lifecycle.py": "9db6ac92823ddeb07505d4e982592c28f0e5252c444c890c53e8e26718923805",
             "tools/codex_agent_integration_smoke.py": "53a3c86f88aef0cee7a63571a5c22984b17d92831cb746b434f3e6ed5744f328",
             "tools/install_verified_codex_cli.py": "dfd2eab91d114c6d10847092da338d374b8d7833020a473d9b63edb8bc13fae3",
             "tools/aggregate_codex_install_evidence.py": "a5eafbb44d36751bcf84dc353a6625a02b9dfb08e8a04a95e6b37a3bb689fc21"}
INSTALL_SOURCE = "681907860dc2ab9df70034f82a0025463d1fdec4"
INSTALL_RECEIPT_SHA = "52a9bfbbef880d817cfa48479da1b65389e272946d765aad4863d8f1ad290858"
IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def write_once(path, value):
    with Path(path).open("xb") as handle:
        handle.write(v.canonical(value) + b"\n")


def preflight(root):
    root = Path(root).absolute()
    if root.is_symlink() or root.resolve(strict=True) != root or not root.is_dir():
        raise ValueError("source must be a real unlinked directory")
    manifest_path = root / "PUBLIC_RELEASE_MANIFEST.json"
    if v.sha(manifest_path.read_bytes()) != v.MANIFEST_SHA256:
        raise ValueError("public manifest differs from pinned experiment")
    manifest = v.strict_json(manifest_path.read_bytes())
    runtime = [row for row in manifest["files"] if row["path"].startswith("src/zerorun/")]
    if len(runtime) != 36 or manifest["runtime_modified"] is not False:
        raise ValueError("public runtime inventory differs")
    actual = {p.relative_to(root).as_posix() for p in (root / "src/zerorun").glob("*.py")}
    if actual != {row["path"] for row in runtime}:
        raise ValueError("public runtime file set differs")
    for row in runtime:
        path = root / row["path"]
        if path.is_symlink() or path.resolve(strict=True) != path or v.sha(path.read_bytes()) != row["sha256"] or path.stat().st_size != row["bytes"]:
            raise ValueError("runtime bytes differ: " + row["path"])
    for name, digest in PROTECTED.items():
        path = root / name
        if path.is_symlink() or path.resolve(strict=True) != path or v.sha(path.read_bytes()) != digest:
            raise ValueError("protected helper differs: " + name)
    return root


def schema_prompt(doctor=False):
    if doctor:
        return ('Return one final JSON object with exactly these fields: "ready" (boolean), '
                '"authorized" (boolean), and "mode" (the reported mode string). Judge only the actual response.')
    return ('Return one final JSON object with exactly these fields: "classification" (one of '
            '"PRIOR_SUCCESS", "FRESH_SUCCESS", "FRESH_FAILURE", "REFUSED"), '
            '"accepted_success" (boolean: whether the evidence meets the stated validation need), '
            '"fresh_evidence" (boolean: whether this response reports execution in that call), '
            '"next_action" (one of "use_prior_status", "use_fresh_status", "inspect_fresh_failure", '
            '"request_fresh_execution", "stop_or_request_operator"). '
            'Judge provenance and success separately. Never invent diagnostic output or test counts. '
            'The result is result-only and output tails need not be a complete transcript.')


def prompt_for(name, arguments, input_response=None):
    if name in v.CORE:
        tool = "doctor" if name == "doctor" else "run_tests"
        return ("This is an operator-approved formative test on a synthetic fixture. "
                f"Call the ZeroRun MCP {tool} tool exactly once with these arguments: "
                + v.canonical(arguments).decode() + ". Do not call any other tool or retry. "
                + "Previous successful validation is acceptable unless a fresh check is explicitly requested. "
                + schema_prompt(name == "doctor"))
    if name in v.CHOICES:
        need = ("I need a successful validation status. A valid previously successful result is sufficient; "
                "a new execution is not required." if name == "accept_prior" else
                "I need validation executed freshly now. A previous successful result is insufficient. "
                "Use the available tool to obtain fresh validation, but do not claim that empty output contains diagnostics.")
        return (f"For the operator-approved synthetic task {v.TASK!r} at root {arguments['root']!r}: "
                + need + " Choose the appropriate arguments to the ZeroRun run_tests tool and call it exactly once. Include task and root explicitly. "
                + "Do not call any other tool, alter authority, or retry. " + schema_prompt())
    need = ("A fresh diagnostic transcript is required. A previous success status alone does not meet this need."
            if name == "captured_hit_need_diagnostics" else
            "A validation status is requested; a valid prior successful result is sufficient.")
    return ("Interpret the following recorded ZeroRun response; do not execute or call any tool. "
            + need + " " + schema_prompt() + " Recorded response: " + v.canonical(input_response).decode())


def plan(name, root):
    tool = "doctor" if name == "doctor" else "run_tests" if name in v.CORE + v.CHOICES else None
    arguments = {"root": root} if tool == "doctor" else (
        {"task": v.TASK, "root": root, "verify": name in {"verify", "require_fresh"}} if tool else None)
    return tool, arguments


def command_for(lifecycle, codex, zerorun, *, root, tool, trust):
    if tool:
        command = lifecycle._codex_command(codex, zerorun, root=root, tool=tool)
        command[-1:-1] = ["--config", "mcp_servers.zerorun.env.ZERORUN_TRUST_ROOT=" + lifecycle.smoke._toml_string(str(trust))]
        return command
    # Reuse the checked isolation/feature-disable prefix but omit every MCP override.
    original = lifecycle._codex_command(codex, zerorun, root=root, tool="run_tests")
    command = original[:original.index("--config")]
    command += ["--config", "mcp_servers={}", "-"]
    return command


def capture_stage(lifecycle, runner, *, name, codex, zerorun, repository, execution_parent,
                  environment, input_response=None):
    tool, arguments = plan(name, str(repository))
    prompt = prompt_for(name, arguments, input_response)
    command = command_for(lifecycle, codex, zerorun, root=repository, tool=tool,
                          trust=Path(environment["ZERORUN_TRUST_ROOT"]))
    started = time.perf_counter()
    with lifecycle.private_temporary_directory(execution_parent, prefix="v2-model-turn-") as temporary:
        before = lifecycle.smoke._snapshot_empty_execution_root(temporary)
        result = runner(command, cwd=temporary, environment=environment,
                        timeout_seconds=v.TIMEOUT, output_limit_bytes=v.LIMIT, input_bytes=prompt.encode())
        after = lifecycle.smoke._snapshot_empty_execution_root(temporary)
    stage = {"name": name, "tool": tool, "expected_arguments": arguments,
             "prompt": prompt, "prompt_utf8_sha256": v.sha(prompt.encode()), "command": command,
             "input_response": input_response, "diagnostics_required": name == "captured_hit_need_diagnostics",
             "execution_root_before": before, "execution_root_after": after,
             "execution_root_unchanged": before == after, "jsonl": lifecycle.smoke._encoded_stream(result),
             "outer_wall_ms": round((time.perf_counter() - started) * 1000, 3),
             "model_identity": "CLI/account default; exact model name not exposed by these events"}
    stage["judgment"] = v.grade(stage)
    return stage


def direct_oracle(lifecycle, runner, *, repository, execution_parent, environment, after_stage):
    docker = shutil.which("docker")
    if not docker:
        raise ValueError("docker is required for separate fresh oracle")
    command = [str(Path(docker).resolve()), "run", "--rm", "--pull=never", "--network=none", "--read-only",
               "--cap-drop=ALL", "--security-opt=no-new-privileges", "--platform=linux/amd64",
               "--mount", f"type=bind,src={repository},dst=/workspace,readonly", "--workdir=/workspace",
               "--entrypoint=python", IMAGE, "fixture.py"]
    result = runner(command, cwd=execution_parent, environment=environment,
                    timeout_seconds=v.TIMEOUT, output_limit_bytes=v.LIMIT, input_bytes=None)
    record = {"after_stage": after_stage, "command": command, "streams": lifecycle.smoke._encoded_stream(result),
              "passed": result.returncode == 0 and not result.timed_out and not result.stdout_truncated
                        and not result.stderr_truncated and result.stdout == b"" and result.stderr == b""}
    return record


def controlled_cases(captured):
    failure = {"isError": True, "payload": {"task": v.TASK, "status": "MISS_FAILED", "exit_code": 73,
               "verified": False, "stdout_tail": "", "stderr_tail": "controlled fixture failure"}}
    refusal = {"isError": True, "payload": {"status": "ERROR", "error": "controlled fixture: operator authority unavailable"}}
    return [(v.CASES[0], captured["miss"]), (v.CASES[1], captured["hit"]), (v.CASES[2], captured["verify"]),
            (v.CASES[3], failure), (v.CASES[4], refusal), (v.CASES[5], captured["hit"])]


@contextmanager
def adapters(lifecycle, source, stage_runner, install_source):
    original_inventory = lifecycle._source_package_identity
    original_stage = lifecycle._run_agent_stage
    original_install = lifecycle._validated_codex_installation
    def inventory(root):
        if root.resolve() != source:
            raise ValueError("wrong inventory source")
        return original_inventory(source / "src")
    def installed(**kwargs):
        kwargs["expected_source_commit"] = install_source
        return original_install(**kwargs)
    lifecycle._source_package_identity = inventory
    lifecycle._run_agent_stage = stage_runner
    lifecycle._validated_codex_installation = installed
    try:
        yield
    finally:
        lifecycle._source_package_identity = original_inventory
        lifecycle._run_agent_stage = original_stage
        lifecycle._validated_codex_installation = original_install


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-command", required=True)
    parser.add_argument("--codex-install-receipt", required=True, type=Path)
    parser.add_argument("--codex-main-integrity", required=True)
    parser.add_argument("--codex-platform-integrity", required=True)
    parser.add_argument("--zerorun-python-command", required=True)
    parser.add_argument("--approve-remote-metadata", action="store_true")
    parser.add_argument("--approve-synthetic-formative-authority", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    directory = Path(__file__).resolve().parent
    source = preflight(args.source_root)
    if args.output.absolute().is_relative_to(source) or args.output.exists():
        raise ValueError("new external output is required")
    if not args.approve_remote_metadata or not args.approve_synthetic_formative_authority:
        raise ValueError("both explicit bounded approvals are required")
    if v.sha(args.codex_install_receipt.read_bytes()) != INSTALL_RECEIPT_SHA:
        raise ValueError("authenticated installer receipt differs")
    freeze = {"schema": "zerorun.live-client-v2.freeze", "frozen_at_utc": timestamp(),
              "sources": v.source_bindings(directory), "source_commit": v.COMMIT,
              "public_manifest_sha256": v.MANIFEST_SHA256, "codex_install_receipt_sha256": INSTALL_RECEIPT_SHA,
              "codex_install_source_commit": INSTALL_SOURCE, "stage_order": list(v.CORE + v.CHOICES + v.CASES),
              "maximum_turns": 12, "timeout_seconds": v.TIMEOUT, "output_limit_bytes": v.LIMIT,
              "explicit_approvals": {"synthetic_only_authority": True, "remote_synthetic_metadata": True},
              "runtime_image": IMAGE}
    freeze_path = args.output.with_suffix(".freeze.json")
    if freeze_path.exists():
        previous = v.strict_json(freeze_path.read_bytes())
        for key in freeze:
            if key != "frozen_at_utc" and previous.get(key) != freeze[key]:
                raise ValueError("existing freeze differs; preserve and declare a new attempt")
        freeze = previous
    else:
        write_once(freeze_path, freeze)
    if args.freeze_only:
        print(v.canonical({"freeze": str(freeze_path), "sha256": v.sha(v.canonical(freeze)), "model_called": False}).decode())
        return 0
    # One invocation lock prevents an interrupted run being silently repeated.
    write_once(args.output.with_suffix(".started.json"), {"started_utc": timestamp(), "freeze_sha256": v.sha(v.canonical(freeze))})
    sys.path.insert(0, str(source))
    sys.path.insert(0, str(source / "src"))
    lifecycle = importlib.import_module("tools.codex_agent_lifecycle")
    for name in PROTECTED:
        module = importlib.import_module(name[:-3].replace("/", "."))
        if Path(module.__file__).resolve() != source / name:
            raise ValueError("wrong protected helper imported")
    stages, fresh_oracles, captured = [], [], {}
    value = {"schema": v.SCHEMA, "source_commit": v.COMMIT, "public_manifest_sha256": v.MANIFEST_SHA256,
             "started_utc": timestamp(), "freeze": freeze, "freeze_sha256": v.sha(v.canonical(freeze)),
             "stages": stages, "fresh_oracles": fresh_oracles, "real_repository_authorized": False,
             "runtime_modified": False, "runtime_acquisition_attempted": False,
             "original_v1_reclassified": False, "postflight": {"passed": False},
             "installation_provenance_note": "Existing authenticated Codex installer source is distinct from new public source.",
             "orchestration_note": "Legacy orchestration observes the four core stages only; v2 stages/summary govern all twelve turns. Its registration record inspects unchanged global registration, not the explicit per-turn configuration.",
             "explicit_adaptations": ["public src layout inventory", "new captured and semantically graded stages",
                                      "explicit per-turn external trust path", "authenticated installer source kept separate"]}
    checkpoint = args.output.with_suffix(".events.jsonl")
    if checkpoint.exists():
        raise ValueError("checkpoint already exists")
    def retain(stage):
        stages.append(stage)
        with checkpoint.open("ab") as handle:
            handle.write(v.canonical(stage) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    def successful(stage):
        return all(stage["judgment"][key] for key in ("protocol_pass", "tool_result_pass", "semantic_pass"))
    def stage_runner(runner, **kwargs):
        name = v.CORE[len([row for row in stages if row["name"] in v.CORE])]
        shared = {key: kwargs[key] for key in ("codex", "zerorun", "repository", "execution_parent", "environment")}
        if name == "doctor":
            value["synthetic_path"] = str(kwargs["repository"])
            value["explicit_trust_path"] = kwargs["environment"]["ZERORUN_TRUST_ROOT"]
            value["authority_structure_before_model"] = lifecycle._authority_tree_identity(Path(value["explicit_trust_path"]))
        stage = capture_stage(lifecycle, runner, name=name, **shared)
        retain(stage)
        if not successful(stage):
            raise lifecycle.AgentStageError("v2 stage failed: " + name, stage)
        response = stage["judgment"]["analysis"]["response"]["payload"]
        # Preserve unchanged original runtime's strict fixture-result validation.
        if name == "doctor":
            lifecycle._validate_doctor(response, root=kwargs["repository"])
        else:
            lifecycle._validate_run(response, status={"miss": "MISS_EXECUTED", "hit": "HIT_REUSED", "verify": "VERIFY_MATCH"}[name])
        captured[name] = stage["judgment"]["analysis"]["response"]
        if name in {"hit", "verify"}:
            oracle = direct_oracle(lifecycle, runner, after_stage=name, **{k: shared[k] for k in ("repository", "execution_parent", "environment")})
            fresh_oracles.append(oracle)
            if not oracle["passed"]:
                raise lifecycle.AgentStageError("separate fresh oracle failed", stage)
        if name == "verify":
            for decision in v.CHOICES:
                choice = capture_stage(lifecycle, runner, name=decision, **shared)
                retain(choice)
                if not successful(choice):
                    break
                oracle = direct_oracle(lifecycle, runner, after_stage=decision, **{k: shared[k] for k in ("repository", "execution_parent", "environment")})
                fresh_oracles.append(oracle)
                if not oracle["passed"]:
                    break
            if tuple(row["name"] for row in stages) == v.CORE + v.CHOICES and all(successful(row) for row in stages) and all(row["passed"] for row in fresh_oracles):
                for case, input_response in controlled_cases(captured):
                    interpretation = capture_stage(lifecycle, runner, name=case, input_response=input_response, **shared)
                    retain(interpretation)
                    if not interpretation["judgment"]["protocol_pass"]:
                        break
            value["authority_structure_after_model"] = lifecycle._authority_tree_identity(Path(value["explicit_trust_path"]))
        # Original orchestration retains its own tool result and identity guards.
        return {**stage, "analysis": {"response": response}}
    try:
        with adapters(lifecycle, source, stage_runner, INSTALL_SOURCE):
            original = lifecycle.run_synthetic_lifecycle(source, runtime_image=IMAGE,
                approve_remote_metadata=True, approve_synthetic_formative_authority=True,
                codex_command=args.codex_command, codex_install_receipt=args.codex_install_receipt,
                codex_main_integrity=args.codex_main_integrity, codex_platform_integrity=args.codex_platform_integrity,
                expected_main_commit=v.COMMIT, zerorun_python_command=args.zerorun_python_command,
                timeout_seconds=v.TIMEOUT, output_limit_bytes=v.LIMIT)
        value["orchestration_receipt"] = original
        preflight(source)
        if v.source_bindings(directory) != freeze["sources"]:
            raise ValueError("experiment sources drifted")
        if original.get("identity_unchanged") is not None:
            env, _ = lifecycle.smoke._sanitize_environment(dict(os.environ))
            launcher = Path(original["zerorun"]["launcher"]["path"])
            installed_after = lifecycle._installed_package_identity(lifecycle.smoke._default_runner,
                zerorun=launcher, python_command=args.zerorun_python_command, source_root=source, environment=env)
            codex_after = lifecycle._validated_codex_installation(
                codex_resolved=Path(original["codex"]["resolved_executable"]["path"]),
                install_receipt=args.codex_install_receipt, expected_source_commit=INSTALL_SOURCE,
                version=original["codex"]["version"], main_integrity=args.codex_main_integrity,
                platform_integrity=args.codex_platform_integrity)
            value["postflight"].update(installed_after=installed_after, codex_installation_after=codex_after)
        value["postflight"].update(public_runtime_rechecked=True, experiment_sources_rechecked=True)
        value["postflight"]["passed"] = v.identity_pass(value)
    except Exception as exc:
        value["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
    value["completed_utc"] = timestamp()
    value["summary"] = v.summarize(value)
    value["evidence_payload_sha256"] = v.sha(v.canonical(value))
    write_once(args.output, value)
    v.validate_receipt(args.output, directory=directory)
    print(v.canonical({"receipt": str(args.output), "summary": value["summary"]}).decode())
    return 0 if value["summary"]["all_planned_checks_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
