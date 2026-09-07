"""Prepare and execute one separately authorized coding-agent producer attempt."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as c


def helpers(path):
    spec = importlib.util.spec_from_file_location("agent_bound_handoff_helpers", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binding(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": c.sha(raw)}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")


def write_bytes(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
    return {"path": path.name, "bytes": len(raw), "sha256": c.sha(raw)}


def inventory(root):
    rows, total = [], 0
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if name.split("/")[0] == ".git":
            continue
        info = path.lstat()
        c.require(not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400, "source link/reparse refused")
        if stat.S_ISDIR(info.st_mode):
            rows.append({"path": name, "kind": "directory"})
        else:
            c.require(stat.S_ISREG(info.st_mode) and info.st_size <= 32 * 1024 * 1024, "ordinary bounded source file required")
            raw = path.read_bytes()
            total += len(raw)
            c.require(total <= 160 * 1024 * 1024 and len(rows) < 20000, "source inventory bound")
            rows.append({"path": name, "kind": "file", "bytes": len(raw), "sha256": c.sha(raw)})
    return rows


def git_guard(root):
    c.require((root / ".git").is_dir() and not (root / ".git").is_symlink(), "ordinary Git directory required")
    return inventory(root / ".git")


def clean_environment(codex, node, test_python, codex_home=None):
    env = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM") if key in os.environ}
    env["PATH"] = os.pathsep.join([str(node.parent), str(codex.parent), str(test_python.parent), "/usr/local/bin", "/usr/bin", "/bin"])
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    if codex_home is not None:
        env["CODEX_HOME"] = str(codex_home)
    return env


def client_identity(codex, node, test_python):
    codex = codex.resolve(strict=True)
    node = node.resolve(strict=True)
    native = codex.parent.parent / "vendor/x86_64-unknown-linux-musl/bin/codex"
    result = {"launcher": binding(codex), "node": binding(node), "native": binding(native),
              "test_python": binding(test_python.absolute())}
    c.require(result["node"]["sha256"] == c.NODE_SHA and result["native"]["sha256"] == c.NATIVE_SHA
              and result["launcher"]["sha256"] == c.LAUNCHER_SHA, "authenticated client identities differ")
    return result


def prepare(args):
    c.require(os.name == "posix", "Linux laboratory required for preparation")
    ledger_path = args.ledger.resolve(strict=True)
    helper = args.handoff_helper.resolve(strict=True)
    h = helpers(helper)
    ledger_raw = h.ordinary(ledger_path)
    ledger = h.strict(ledger_raw)
    h.validate_ledger(ledger)
    cases = c.selected(ledger)
    if ledger["phase"] == "main":
        pilot = h.strict(h.ordinary(ledger_path.parent / "pilot.json"))
        c.require(pilot.get("phase") == "pilot" and not ({case["case_id"] for case in cases} & {case["case_id"] for case in pilot["cases"]}), "pilot/main overlap")
    output = args.output.resolve()
    c.require(not output.exists() and output.parent.is_dir(), "new external output directory required")
    output.mkdir()
    client = client_identity(args.codex, args.node, args.test_python)
    frozen = {"schema": "zerorun.agent-producer-freeze.v1", "created_utc": h.utc(), "phase": ledger["phase"], "cases": cases,
              "ledger": ledger, "ledger_binding": binding(ledger_path), "helper": binding(helper),
              "engine": str(args.engine.resolve(strict=True)), "client": client,
              "model_requested": c.MODEL, "reasoning_effort": c.EFFORT,
              "timeout_seconds": c.SECONDS, "output_limit_bytes": c.OUTPUT_LIMIT,
              "study_sources": {name: binding(HERE / name) for name in ("common.py", "run.py", "validate.py", "PROTOCOL.md")},
              "reference_patch_supplied_to_model": False, "mcp_authority_created": False}
    save(output / "selection.json", frozen)
    frozen["preparations"] = []
    for index, case in enumerate(cases):
        case_dir = output / f"case-{index:02d}"
        case_dir.mkdir()
        prep = {"case_id": case["case_id"], "repo": case["repo"], "ready": False, "error": None}
        try:
            c.require(case.get("disposition") != "UNAVAILABLE_ACQUISITION", "selected source acquisition unavailable")
            metadata_raw = h.bound(ledger_path.parent, case["metadata"])
            row = h.metadata_row(metadata_raw, case)
            prep["metadata"] = write_bytes(case_dir / "metadata-hf.json", metadata_raw)
            task = row.get("problem_statement")
            test_paths = h.patch_paths(row["test_patch"])
            c.require(set(case["targets"]) <= set(test_paths), "targets outside supplied test patch")
            raw = h.bound(ledger_path.parent, case["source_archive"], h.MAX_ARCHIVE_BYTES)
            prep["source_archive"] = write_bytes(case_dir / "base-source.tar.gz", raw)
            root = case_dir / "workspace"
            prep["extraction"] = h.extract_source(raw, root, case["base_commit"])
            ops = [h.git(root, ["init", "--template=", "--initial-branch=main", "."])]
            raw_patch = row["test_patch"].encode()
            ops += [h.git(root, ["apply", "--check", "--whitespace=nowarn", "-"], raw_patch),
                    h.git(root, ["apply", "--whitespace=nowarn", "-"], raw_patch)]
            c.require(all((root / target).is_file() for target in case["targets"]), "provided target missing")
            ops += [h.git(root, ["add", "--all"]), h.git(root, ["-c", "user.name=Study preparation", "-c", "user.email=study@example.invalid", "commit", "-m", "Prepared base with public test patch"])]
            prep.update(workspace=str(root), public_issue=task, test_paths=test_paths, targets=case["targets"], git_operations=ops,
                        reference_patch_sha256=c.sha(row["patch"].encode()), reference_patch_applied=False,
                        test_patch=write_bytes(case_dir / "provided-tests.patch", raw_patch),
                        before=inventory(root), git_before=git_guard(root))
            prompt = c.prompt(task, case["targets"], args.test_python.absolute())
            prep["prompt"] = write_bytes(case_dir / "prompt.txt", prompt.encode())
            prep["command"] = c.command(args.codex.resolve(strict=True), root, args.test_python)
            prep["ready"] = True
        except Exception as exc:
            prep["error"] = f"{type(exc).__name__}: {exc}"
        save(case_dir / "preparation.json", prep)
        frozen["preparations"].append(binding(case_dir / "preparation.json"))
    save(output / "freeze.json", frozen)
    return {"prepared": str(output), "selected_cases": len(cases), "model_called": False}


def run_case(args):
    c.require(os.name == "posix", "Linux model execution required")
    c.require(args.approve_model_sessions is True, "explicit model-session approval required")
    output = args.prepared.resolve(strict=True)
    freeze_raw = (output / "freeze.json").read_bytes()
    c.require(c.sha(freeze_raw) == args.freeze_sha256, "prospective freeze differs")
    frozen = c.strict(freeze_raw)
    for name, record in frozen["study_sources"].items():
        c.require(binding(HERE / name)["sha256"] == record["sha256"], "study source drift")
    hpath = Path(frozen["helper"]["path"])
    c.require(binding(hpath) == frozen["helper"], "handoff helper drift")
    h = helpers(hpath)
    c.require(0 <= args.case_index < len(frozen["cases"]), "case index outside selection")
    for index in range(args.case_index):
        prior = c.strict((output / f"case-{index:02d}" / "session.json").read_bytes())
        c.require(prior.get("boundary_pass") is True, "earlier producer boundary violation stops campaign")
    case_dir = output / f"case-{args.case_index:02d}"
    root = case_dir / "workspace"
    c.require(binding(case_dir / "preparation.json") == frozen["preparations"][args.case_index], "prepared receipt drift")
    prep = c.strict((case_dir / "preparation.json").read_bytes())
    started = {"case_index": args.case_index, "case_id": prep["case_id"], "freeze_sha256": args.freeze_sha256,
               "started_utc": h.utc()}
    save(case_dir / "started.json", started)  # One attempt, including pre-call failures.
    session = {**started, "model_invocation_attempted": False, "boundary_pass": True, "error": None}
    try:
        c.require(prep["ready"] is True, "selected case was not preparation-ready")
        c.require(inventory(root) == prep["before"] and git_guard(root) == prep["git_before"], "prepared source drift")
        cli = frozen["client"]
        codex, node, test_python = (Path(cli[key]["path"]) for key in ("launcher", "node", "test_python"))
        c.require(client_identity(codex, node, test_python) == cli, "client identity changed")
        codex_home = args.codex_home.resolve(strict=True)
        c.require(codex_home.is_dir() and not codex_home.is_symlink() and not codex_home.is_relative_to(root), "external operator authentication directory required")
        c.require(not (codex_home / "config.toml").exists(), "isolated CODEX_HOME must not contain user config")
        env = clean_environment(codex, node, test_python, codex_home)
        _, _, oci, _, engine_id = h.load_engine(Path(frozen["engine"]))
        version, timeout = oci._run_bounded_process([str(codex), "--version"], cwd=case_dir, environment=env,
            timeout_seconds=20, output_limit_bytes=65536)
        session["client_version"] = version.stdout.decode(errors="replace").strip()
        c.require(not timeout and version.returncode == 0 and session["client_version"] == "codex-cli 0.153.3", "CLI version differs")
        prompt = c.prompt(prep["public_issue"], prep["targets"], test_python).encode()
        c.require(prompt == (case_dir / "prompt.txt").read_bytes(), "prompt drift")
        command = c.command(codex, root, test_python)
        c.require(command == prep["command"], "command drift")
        session.update(command=command, prompt_sha256=c.sha(prompt), engine=engine_id,
                       model_requested=c.MODEL, reasoning_effort=c.EFFORT, model_invocation_attempted=True)
        begin = time.perf_counter()
        result, timed_out = oci._run_bounded_process(command, cwd=root, environment=env,
            timeout_seconds=c.SECONDS, output_limit_bytes=c.OUTPUT_LIMIT, input_bytes=prompt)
        session["outer_seconds"] = time.perf_counter() - begin
        session["stdout"] = write_bytes(case_dir / "events.log", result.stdout)
        session["stderr"] = write_bytes(case_dir / "stderr.log", result.stderr)
        truncated = result.stdout.startswith(oci._TRUNCATION_MARKER) or result.stderr.startswith(oci._TRUNCATION_MARKER)
        session.update(returncode=result.returncode, timed_out=timed_out, truncated=truncated)
        session["analysis"] = c.analyze_events(result.stdout, result.returncode, timed_out, truncated)
        session["after"] = inventory(root)
        session["git_after"] = git_guard(root)
        session["scope"] = c.changes(prep["before"], session["after"], prep["test_paths"])
        session["boundary_pass"] = session["analysis"]["boundary_pass"] and session["scope"]["source_scope_pass"] and session["git_after"] == prep["git_before"]
        if session["git_after"] == prep["git_before"]:
            patch = h.git(root, ["diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--"])
            session["tracked_patch"] = write_bytes(case_dir / "proposed-tracked.patch", patch["stdout"].encode())
        else:
            session["tracked_patch_unavailable"] = "Protected Git metadata changed; no post-model Git command executed."
        # Full final source also preserves untracked proposed production files.
        final = case_dir / "final-source.tar.gz"
        with final.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for row in session["after"]:
                archive.add(root / row["path"], arcname="agent-final/" + row["path"], recursive=False)
        session["final_source"] = {**binding(final), "path": final.name}
        session["client_after"] = client_identity(codex, node, test_python)
        c.require(session["client_after"] == cli, "client identity drift after model")
    except Exception as exc:
        session["error"] = f"{type(exc).__name__}: {exc}"
        if session["model_invocation_attempted"]:
            session["boundary_pass"] = False
    session["completed_utc"] = h.utc()
    save(case_dir / "session.json", session)
    return {"case_id": prep["case_id"], "model_invocation_attempted": session["model_invocation_attempted"],
            "boundary_pass": session["boundary_pass"], "error": session["error"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    setup = commands.add_parser("prepare")
    for name in ("ledger", "handoff-helper", "engine", "codex", "node", "test-python", "output"):
        setup.add_argument("--" + name, required=True, type=Path)
    run = commands.add_parser("run-case")
    run.add_argument("--prepared", type=Path, required=True)
    run.add_argument("--freeze-sha256", required=True)
    run.add_argument("--case-index", type=int, required=True)
    run.add_argument("--codex-home", type=Path, required=True)
    run.add_argument("--approve-model-sessions", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args) if args.mode == "prepare" else run_case(args), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
