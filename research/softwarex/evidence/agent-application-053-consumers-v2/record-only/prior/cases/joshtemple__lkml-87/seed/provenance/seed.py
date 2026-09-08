"""One real installed-MCP seed for an already authorized, exactly reviewed lab.

The run route creates no authority and makes no model call. The check route is
offline: it reconciles retained raw STDIO against source, manifest and installed
runtime bindings. A cache hit, a request alone, or an arbitrary JSON receipt is
never accepted as a fresh successful seed.
"""
from __future__ import annotations

import argparse
import ast
import base64
import inspect
import math
import os
from pathlib import Path, PurePosixPath

from research.softwarex import quickstart_053 as quickstart
from research.softwarex import diagnose_mcp_authority as diagnostic
from research.softwarex.agent_application_053 import consumer as c
from research.softwarex.agent_application_053 import validation as v

SCHEMA = "zerorun.real-agent-seed.0.5.3.v1"
SECONDS = 180


def requests(root, task):
    return [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "zerorun-real-agent-seed", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "run_tests", "arguments": {"root": root, "task": task, "verify": False}}},
    ]


def parse_exchange(stage, root, task):
    """Reuse the unchanged 0.5.3 wire parser for a real task's fresh result.

    Parameterize its fixed fixture name and replace only the two empty-tail
    assertions with the actual MCP bound: up to 4,000 decoded characters. Every
    status, error, version, key, structured/text and request check remains.
    Actual raw requests and responses are never rewritten or normalized.
    """
    v.require(stage.get("name") == "miss" and stage.get("requests") == requests(root, task),
              "seed requires the exact single fresh-request sequence")
    tree = ast.parse(inspect.getsource(quickstart.validate_exchange))
    class Parameters(ast.NodeTransformer):
        task_names = 0
        tails = set()

        def visit_Constant(self, node):
            if node.value == "synthetic-lifecycle":
                self.task_names += 1
                return ast.copy_location(ast.Constant(task), node)
            return node

        def visit_Compare(self, node):
            left = node.left
            if (len(node.ops) == 1 and isinstance(node.ops[0], ast.NotEq)
                    and len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Constant)
                    and node.comparators[0].value == "" and isinstance(left, ast.Call)
                    and isinstance(left.func, ast.Attribute) and isinstance(left.func.value, ast.Name)
                    and left.func.value.id == "payload" and left.func.attr == "get"
                    and len(left.args) == 1 and isinstance(left.args[0], ast.Constant)
                    and left.args[0].value in {"stdout_tail", "stderr_tail"}):
                v.require(left.args[0].value not in self.tails, "duplicate quickstart tail assertion")
                self.tails.add(left.args[0].value)
                return ast.copy_location(ast.UnaryOp(ast.Not(), ast.Call(ast.Name("_valid_fresh_tail", ast.Load()), [left], [])), node)
            return self.generic_visit(node)

    parameters = Parameters()
    tree = ast.fix_missing_locations(parameters.visit(tree))
    v.require(parameters.task_names == 3 and parameters.tails == {"stdout_tail", "stderr_tail"},
              "quickstart parameterization no longer has its reviewed shape")
    namespace = dict(quickstart.__dict__)
    namespace["request_plan"] = lambda name, supplied_root: requests(str(supplied_root), task)
    namespace["_valid_fresh_tail"] = lambda value: isinstance(value, str) and len(value) <= 4000
    exec(compile(tree, quickstart.__file__, "exec"), namespace)
    return namespace["validate_exchange"](stage, root)


def sources():
    paths = {"seed.py": Path(__file__), "consumer.py": Path(c.__file__),
             "validation.py": Path(v.__file__), "quickstart_053.py": Path(quickstart.__file__),
             "diagnose_mcp_authority.py": Path(diagnostic.__file__),
             "run_public_lifecycle.py": Path(diagnostic.adapter.__file__),
             "current_runtime_053.py": c.CURRENT_PIN_SOURCE}
    return {name: path for name, path in sorted(paths.items())}


def bound(directory, row):
    v.require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}, "invalid seed file binding")
    name = v.safe_relative(row["path"])
    path = v.real(Path(directory) / name)
    v.require(path.stat().st_size <= v.LIMIT and c.file_row(path, directory) == row, "seed file bytes differ")
    return path.read_bytes()


def stream_envelope(directory, command):
    result = {"returncode": command["returncode"], "timed_out": command["timed_out"]}
    for name in ("stdout", "stderr"):
        raw = bound(directory, command[name])
        result.update({name + "_base64": base64.b64encode(raw).decode(), name + "_bytes": len(raw),
                       name + "_sha256": v.sha(raw), name + "_truncated": False})
    return result


def validate_command(directory, label, argv, cwd, timeout):
    row = v.strict(v.real(directory / (label + ".json")).read_bytes())
    start = v.strict(v.real(directory / (label + ".started.json")).read_bytes())
    v.require(row["command"] == start["command"] == argv and row["cwd"] == start["cwd"] == cwd
              and row["timeout_seconds"] == start["timeout_seconds"] == timeout
              and row["started_utc"] == start["started_utc"] and c.successful(row),
              "seed command did not succeed with its frozen arguments")
    elapsed = row.get("elapsed_seconds")
    v.require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= timeout + 20,
              "invalid seed command duration")
    for name in ("stdout", "stderr"):
        v.require(row[name]["path"] == label + "." + name + ".log", "seed stream path differs")
        bound(directory, row[name])
    return row


def validate(directory):
    """Reconcile immutable files and raw responses without executing anything."""
    directory = v.real(directory, directory=True)
    manifest = v.strict(v.real(directory / "RECORD_MANIFEST.json").read_bytes())
    names = []
    for row in manifest["files"]:
        bound(directory, row); names.append(row["path"])
    actual = [c.file_row(path, directory)["path"] for path in directory.rglob("*")
              if path.is_file() and path != directory / "RECORD_MANIFEST.json"]
    v.require(len(names) == len(set(names)) and sorted(names) == sorted(actual), "seed record inventory is not exhaustive")
    protocol = v.strict(v.real(directory / "protocol.json").read_bytes())
    receipt = v.strict(v.real(directory / "receipt.json").read_bytes())
    qualification = v.strict(v.real(directory / "qualification.json").read_bytes())
    v.validate_qualification(qualification)
    v.require(protocol["schema"] == SCHEMA and receipt["schema"] == SCHEMA
              and receipt["passed"] is True and receipt["failure"] is None
              and receipt["protocol_sha256"] == v.sha((directory / "protocol.json").read_bytes()), "seed attempt did not complete")
    v.require(protocol["qualification_sha256"] == v.sha((directory / "qualification.json").read_bytes())
              and protocol["core_commit"] == c.CORE and protocol["runtime_identity"] == c.CORE_IDENTITY
              and protocol["seconds"] == SECONDS and protocol["automatic_retries"] == 0
              and protocol["model_called"] is False and protocol["authority_created"] is False,
              "seed scope or installed runtime pin differs")
    expected_sources = [dict(c.file_row(path), path="provenance/" + name) for name, path in sources().items()]
    v.require(protocol["sources"] == expected_sources, "seed execution/helper source differs")
    for row in expected_sources: bound(directory, row)
    root, task = qualification["root"], qualification["task"]
    v.require(protocol["root"] == root and protocol["task"] == task
              and protocol["runtime_image"] == qualification["runtime_image"]
              and protocol["manifest_sha256"] == qualification["manifest_sha256"], "seed qualification binding differs")
    source = qualification["states"]["final"]["files"]
    v.require(receipt["source_before"] == receipt["source_after"] == source
              and receipt["source_identity"] == v.identity(source), "seed source changed or was not the reviewed final state")
    v.require(receipt["git_before"] == receipt["git_after"], "seed changed Git metadata")
    manifest_raw = (directory / "manifest.json").read_bytes()
    v.require(v.sha(manifest_raw) == qualification["manifest_sha256"], "seed manifest differs")
    # Reconcile the saved manifest contract without reading the live repository.
    supplied = v.strict(manifest_raw)
    target = supplied.get("tasks", {}).get(task, {})
    expected_inputs = sorted({row["path"].split("/")[0] for state in qualification["states"].values()
                              for row in state["files"] if row["path"] != ".zerorun.json"})
    v.require(supplied.get("version") == 2 and set(supplied.get("tasks", {})) == {task}
              and target.get("command") == qualification["command"] and target.get("image") == qualification["runtime_image"]
              and target.get("inputs") == expected_inputs and target.get("platform") == "linux/amd64"
              and all(target.get(name) is True for name in ("cacheable", "result_only", "closure_reviewed"))
              and target.get("cache_streams") is False and all(target.get(name) == [] for name in ("outputs", "env", "unsafe_effects")),
              "seed manifest contract differs")
    client = protocol["client"]
    server, python = client["zerorun"]["invoked"], client["python"]["invoked"]
    v.require(PurePosixPath(server).is_absolute() and PurePosixPath(python).is_absolute()
              and PurePosixPath(server).parent == PurePosixPath(python).parent
              and not PurePosixPath(server).is_relative_to(root)
              and not PurePosixPath(python).is_relative_to(root), "external installation paths differ")
    trust = PurePosixPath(protocol["trust_root"])
    v.require(trust.is_absolute() and not trust.is_relative_to(root) and not PurePosixPath(root).is_relative_to(trust)
              and protocol["environment"]["ZERORUN_TRUST_ROOT"] == str(trust), "seed external authority binding differs")
    v.require(receipt["client_after"] == client, "installed executable identity changed")
    v.require(receipt["commands"] == ["runtime-before", "seed", "runtime-after"], "seed command count or order differs")
    installed = []
    for label in ("runtime-before", "runtime-after"):
        row = validate_command(directory, label, [python, "-I", "-B", "-c", c.PROBE], protocol["probe_cwd"], 20)
        value = v.strict(bound(directory, row["stdout"]))
        v.checked_rows(value["files"])
        package = PurePosixPath(value["module"])
        v.require(value["version"] == "0.5.3" and len(value["files"]) == 36
                  and v.identity(value["files"]) == c.CORE_IDENTITY
                  and package.is_relative_to(PurePosixPath(python).parent.parent)
                  and not package.is_relative_to(root), "seed installed runtime differs")
        installed.append(value)
    v.require(installed[0] == installed[1] == receipt["installed_runtime"], "installed runtime changed during seed")
    row = validate_command(directory, "seed", [server, "mcp-server"], root, SECONDS)
    expected_requests = requests(root, task)
    v.require(protocol["requests"] == expected_requests
              and (directory / "requests.log").read_bytes() == b"".join(v.canonical(value) + b"\n" for value in expected_requests),
              "raw seed requests differ")
    stage = {"name": "miss", "requests": expected_requests, "explicit_trust_root": True,
             "streams": stream_envelope(directory, row)}
    result = parse_exchange(stage, root, task)
    v.require(receipt["result"] == result and receipt["cache_key"] == result["cache_key"], "seed result summary differs from raw MCP")
    return {"schema": SCHEMA, "reconciled": True, "seeded": True, "case_id": qualification["case_id"],
            "root": root, "task": task, "status": "MISS_EXECUTED", "exit_code": 0,
            "cache_key": result["cache_key"], "source_identity": v.identity(source),
            "manifest_sha256": qualification["manifest_sha256"], "runtime_image": qualification["runtime_image"],
            "runtime_identity": c.CORE_IDENTITY, "model_called": False, "authority_created": False,
            "receipt_sha256": v.sha((directory / "receipt.json").read_bytes())}


def run(args):
    v.require(os.name == "posix" and os.getuid() != 0, "non-root Linux laboratory required")
    qualification_raw = v.real(args.qualification).read_bytes()
    qualification = v.strict(qualification_raw); v.validate_qualification(qualification)
    root = v.real(qualification["root"], directory=True)
    output = Path(os.path.abspath(args.output)); v.real(output.parent, directory=True)
    v.require(not output.exists() and not output.is_relative_to(root) and not root.is_relative_to(output), "new external seed directory required")
    trust = v.real(args.trust_root, directory=True)
    v.require(not trust.is_relative_to(root) and not root.is_relative_to(trust)
              and not output.is_relative_to(trust) and not trust.is_relative_to(output), "external disjoint trust root required")
    client = {"zerorun": c.executable(args.zerorun_command, root), "python": c.executable(args.zerorun_python_command, root)}
    launcher, python = (Path(client[name]["invoked"]) for name in ("zerorun", "python"))
    first = launcher.read_bytes().splitlines()[0].decode()
    v.require(first.startswith("#!") and Path(first[2:]).parent == python.parent
              and Path(first[2:]).resolve(strict=True) == python.resolve(strict=True)
              and launcher.parent == python.parent, "server launcher does not use the probed environment")
    c.manifest_check(root, qualification)
    v.require(v.inventory(root) == qualification["states"]["final"]["files"], "current source is not the reviewed final state")
    output.mkdir(); (output / "provenance").mkdir(); (output / "empty-workspace").mkdir()
    c.write(output / "qualification.json", qualification_raw)
    c.write(output / "manifest.json", (root / ".zerorun.json").read_bytes())
    for name, path in sources().items(): c.write(output / "provenance" / name, path.read_bytes())
    env = {name: os.environ[name] for name in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL") if name in os.environ}
    env.update(PATH=os.pathsep.join([str(python.parent), "/usr/local/bin", "/usr/bin", "/bin"]),
               PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", GCM_INTERACTIVE="Never", ZERORUN_TRUST_ROOT=str(trust))
    plan = requests(str(root), qualification["task"])
    protocol = {"schema": SCHEMA, "started_utc": c.utc(), "root": str(root), "task": qualification["task"],
                "qualification_sha256": v.sha(qualification_raw), "manifest_sha256": qualification["manifest_sha256"],
                "runtime_image": qualification["runtime_image"], "core_commit": c.CORE, "runtime_identity": c.CORE_IDENTITY,
                "client": client, "trust_root": str(trust), "environment": env, "requests": plan,
                "sources": [dict(c.file_row(path), path="provenance/" + name) for name, path in sources().items()],
                "seconds": SECONDS, "probe_cwd": str(output / "empty-workspace"),
                "automatic_retries": 0, "authority_created": False, "model_called": False}
    c.save(output / "protocol.json", protocol)
    input_bytes = b"".join(v.canonical(value) + b"\n" for value in plan)
    c.write(output / "requests.log", input_bytes)
    receipt = {"schema": SCHEMA, "protocol_sha256": v.sha((output / "protocol.json").read_bytes()),
               "passed": False, "failure": None, "commands": []}
    try:
        receipt["source_before"] = v.inventory(root)
        v.require(receipt["source_before"] == qualification["states"]["final"]["files"],
                  "source changed since reviewed preflight")
        receipt["source_identity"] = v.identity(receipt["source_before"])
        receipt["git_before"] = v.inventory(root / ".git", exclude_control=False)
        for label, argv, cwd, timeout, data in (
            ("runtime-before", [str(python), "-I", "-B", "-c", c.PROBE], output / "empty-workspace", 20, None),
            ("seed", [str(launcher), "mcp-server"], root, SECONDS, input_bytes),
            ("runtime-after", [str(python), "-I", "-B", "-c", c.PROBE], output / "empty-workspace", 20, None)):
            row = c.invoke(argv, cwd, env, output, label, timeout=timeout, input_bytes=data)
            receipt["commands"].append(label)
            v.require(c.successful(row), "seed command failed: " + label)
            if label == "runtime-before":
                installed = v.strict(bound(output, row["stdout"]))
                v.require(installed["version"] == "0.5.3" and len(installed["files"]) == 36
                          and v.identity(installed["files"]) == c.CORE_IDENTITY
                          and Path(installed["module"]).is_relative_to(python.parent.parent), "installed runtime does not match exact053")
                receipt["installed_runtime"] = installed
                c.installed_runtime_binding(receipt)
            elif label == "seed":
                result = parse_exchange({"name": "miss", "requests": plan, "explicit_trust_root": True,
                                         "streams": stream_envelope(output, row)}, str(root), qualification["task"])
                receipt.update(result=result, cache_key=result["cache_key"])
            else:
                v.require(v.strict(bound(output, row["stdout"])) == receipt["installed_runtime"], "installed runtime changed")
                c.installed_runtime_binding(receipt)
        receipt["source_after"] = v.inventory(root)
        receipt["git_after"] = v.inventory(root / ".git", exclude_control=False)
        receipt["client_after"] = {"zerorun": c.executable(launcher, root), "python": c.executable(python, root)}
        v.require(receipt["source_before"] == receipt["source_after"] and receipt["git_before"] == receipt["git_after"]
                  and receipt["client_after"] == client, "source or installation changed during seed")
        receipt["passed"] = True
    except Exception as error:
        receipt["failure"] = {"type": type(error).__name__, "message": str(error)}
    receipt["completed_utc"] = c.utc()
    c.save(output / "receipt.json", receipt)
    c.save(output / "RECORD_MANIFEST.json", {"files": [c.file_row(path, output) for path in sorted(output.rglob("*")) if path.is_file()]})
    result = validate(output) if receipt["passed"] else {"seeded": False, "failure": receipt["failure"]}
    print(v.canonical(result).decode())
    return 0 if receipt["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    execute = sub.add_parser("run")
    execute.add_argument("--qualification", required=True, type=Path)
    execute.add_argument("--zerorun-command", required=True, type=Path)
    execute.add_argument("--zerorun-python-command", required=True, type=Path)
    execute.add_argument("--trust-root", required=True, type=Path)
    execute.add_argument("--output", required=True, type=Path)
    check = sub.add_parser("check"); check.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "check": print(v.canonical(validate(args.directory)).decode()); return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
