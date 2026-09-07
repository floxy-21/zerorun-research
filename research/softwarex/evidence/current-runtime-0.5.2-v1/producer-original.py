"""Install and test a new public runtime without rewriting historical evidence.

The producer uses a new external virtual environment for the wheel smoke check
and runs the published test directory against the exact current public sources.
The validator is read-only. Neither path invokes a model, Docker, or authority.
"""
from __future__ import annotations

import argparse
import ast
import base64
from copy import deepcopy
from datetime import datetime, timezone
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

HISTORICAL_CORE = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
HISTORICAL_PREFIX = "research/softwarex/historical_runtime_0_5_1"
VERSION = "0.5.2"
SCHEMA = "zerorun.softwarex-current-runtime.v1"
SELF = "research/softwarex/five_hour_review/current_runtime.py"
WHEEL = "output/packages/zerorun-softwarex/zerorun-0.5.2-py3-none-any.whl"
REQUIRED_TESTS = {"tests/test_mcp.py", "tests/test_codex_integration.py", "tests/test_mcp_discovery_semantics.py"}
ALLOWED_CHANGED = {"__init__.py", "mcp.py"}
LIMIT = 8 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite JSON value")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def regular(path):
    path = Path(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink()
            and not getattr(before, "st_file_attributes", 0) & 0x400, "nonregular input")
    require(before.st_size <= LIMIT, "oversized input")
    raw = path.read_bytes()
    after = path.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)
            and len(raw) == before.st_size, "input changed during read")
    return raw


def _tool_tree(raw):
    tree = ast.parse(raw.decode("utf-8"))
    tools = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_tools"]
    require(len(tools) == 1, "single tools-discovery function required")
    function = tools[0]
    require(len(function.body) == 2 and isinstance(function.body[0], ast.Assign)
            and len(function.body[0].targets) == 1
            and isinstance(function.body[0].targets[0], ast.Name)
            and function.body[0].targets[0].id == "root_property"
            and isinstance(function.body[1], ast.Return), "unexpected discovery structure")
    root_property = function.body[0].value
    class InlineRoot(ast.NodeTransformer):
        def visit_Name(self, node):
            return deepcopy(root_property) if node.id == "root_property" and isinstance(node.ctx, ast.Load) else node
        def visit_Dict(self, node):
            node = self.generic_visit(node)
            pairs = []
            for key, value in zip(node.keys, node.values):
                if key is None:
                    require(isinstance(value, ast.Dict) and all(k is not None for k in value.keys),
                            "nonliteral discovery dictionary expansion")
                    pairs.extend(zip(value.keys, value.values))
                else:
                    pairs.append((key, value))
            node.keys = [key for key, _ in pairs]
            node.values = [value for _, value in pairs]
            return node
    expanded = InlineRoot().visit(deepcopy(function.body[1].value))
    catalog = ast.literal_eval(expanded)
    require(isinstance(catalog, list) and all(isinstance(row, dict) for row in catalog), "invalid discovery catalog")
    # Normalize only descriptions in this exact literal discovery function.
    class DropDescriptions(ast.NodeTransformer):
        def visit_Dict(self, node):
            pairs = []
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "description":
                    require(isinstance(value, ast.Constant) and isinstance(value.value, str), "dynamic description refused")
                else:
                    pairs.append((key, value))
            node.keys = [key for key, _ in pairs]
            node.values = [value for _, value in pairs]
            return self.generic_visit(node)
    function.body = [ast.Return(value=DropDescriptions().visit(expanded))]
    return ast.dump(tree, include_attributes=False), catalog


def validate_metadata_only(old, current):
    require(set(old) == set(current) and len(old) == 36, "runtime denominator or filenames differ")
    changed = {name for name in old if old[name] != current[name]}
    require(changed == ALLOWED_CHANGED, "runtime changes exceed the two versioned modules")
    old_init = old["__init__.py"]
    require(old_init.count(b'"0.5.1"') == 1
            and current["__init__.py"] == old_init.replace(b'"0.5.1"', b'"0.5.2"'), "version module changed beyond version string")
    old_tree, old_tools = _tool_tree(old["mcp.py"])
    current_tree, current_tools = _tool_tree(current["mcp.py"])
    require(old_tree == current_tree, "MCP executable structure or non-description schema changed")
    left = {row["name"]: row for row in old_tools}
    right = {row["name"]: row for row in current_tools}
    require(len(left) == len(old_tools) == len(right) == len(current_tools)
            and set(left) == set(right) and "run_tests" in left, "tool catalog membership changed")
    require(all(left[name] == right[name] for name in left if name != "run_tests"), "unrelated tool metadata changed")
    return sorted(changed)


def rows(root, paths):
    result = []
    for relative in sorted(set(paths)):
        raw = regular(root / relative)
        result.append({"path": relative, "bytes": len(raw), "sha256": digest(raw)})
    return result


def snapshot(release):
    release = Path(release)
    manifest = strict_json(regular(release / "PUBLIC_RELEASE_MANIFEST.json"))
    commit = manifest.get("current_core_commit")
    require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
            and commit != HISTORICAL_CORE and manifest.get("historical_core_commit") == HISTORICAL_CORE
            and manifest.get("current_version") == VERSION, "current/historical release identity absent")
    current_paths = sorted((release / "src/zerorun").glob("*.py"))
    old_paths = sorted((release / HISTORICAL_PREFIX / "src/zerorun").glob("*.py"))
    current = {p.name: regular(p) for p in current_paths}
    old = {p.name: regular(p) for p in old_paths}
    changed = validate_metadata_only(old, current)
    manifest_rows = manifest.get("files", [])
    indexed = {r["path"]: r for r in manifest_rows}
    require(len(indexed) == len(manifest_rows), "duplicate public manifest path")
    runtime_paths = [p.relative_to(release).as_posix() for p in current_paths + old_paths]
    for relative in runtime_paths:
        raw = regular(release / relative)
        row = indexed.get(relative, {})
        historical = relative.startswith(HISTORICAL_PREFIX + "/")
        expected_commit = HISTORICAL_CORE if historical else commit
        source_name = "zerorun/" + Path(relative).name
        require(row.get("sha256") == digest(raw) and type(row.get("bytes")) is int
                and row["bytes"] == len(raw) and row.get("origin") == "git:" + expected_commit + ":" + source_name,
                "runtime manifest origin or bytes differ")
    test_paths = [p.relative_to(release).as_posix() for p in (release / "tests").rglob("*.py")]
    require(REQUIRED_TESTS <= set(test_paths), "required current MCP tests missing")
    tool_paths = [p.relative_to(release).as_posix() for p in (release / "tools").rglob("*.py")]
    return {"current_core_commit": commit, "historical_core_commit": HISTORICAL_CORE,
            "version": VERSION, "changed_runtime_modules": changed,
            "runtime_files": rows(release, runtime_paths),
            "test_and_helper_files": rows(release, test_paths + tool_paths + [SELF, "pyproject.toml"])}


def wheel_check(release):
    raw = regular(release / WHEEL)
    with zipfile.ZipFile(release / WHEEL) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)) and archive.testzip() is None, "invalid wheel archive")
        expected = {"zerorun/" + p.name for p in (release / "src/zerorun").glob("*.py")}
        actual = {name for name in names if name.startswith("zerorun/") and name.endswith(".py")}
        require(expected == actual and len(actual) == 36, "wheel runtime inventory differs")
        dist_info = "zerorun-0.5.2.dist-info/"
        allowed_metadata = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt",
                            "licenses/LICENSE.txt", "licenses/Licence.txt", "licenses/THIRD_PARTY_NOTICES.md"}
        require(all(name in expected or (name.startswith(dist_info)
                    and name.removeprefix(dist_info) in allowed_metadata) for name in names),
                "unexpected executable or extra wheel payload")
        for name in actual:
            require(archive.read(name) == regular(release / "src" / name), "wheel runtime bytes differ")
        metadata = Parser().parsestr(archive.read("zerorun-0.5.2.dist-info/METADATA").decode())
        require(metadata.get("Version") == VERSION and metadata.get("Name") == "zerorun"
                and metadata.get("License-Expression") == "MIT" and not metadata.get_all("Requires-Dist"), "wheel metadata differs")
    return {"path": WHEEL, "bytes": len(raw), "sha256": digest(raw)}


def stream(raw):
    return {"bytes": len(raw), "sha256": digest(raw), "base64": base64.b64encode(raw).decode()}


def decoded(value):
    raw = base64.b64decode(value["base64"], validate=True)
    require(type(value.get("bytes")) is int and value["bytes"] == len(raw)
            and value.get("sha256") == digest(raw), "captured stream binding differs")
    return raw


def invoke(label, command, *, cwd, env=None, timeout=600):
    record = {"label": label, "command": [str(x) for x in command], "cwd": str(cwd)}
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = subprocess.run(record["command"], cwd=cwd, env=env, capture_output=True, timeout=timeout)
        record.update(returncode=result.returncode, stdout=stream(result.stdout), stderr=stream(result.stderr), error=None)
    except subprocess.TimeoutExpired as exc:
        record.update(returncode=None, stdout=stream(exc.output or b""), stderr=stream(exc.stderr or b""), error="timeout")
    record.update(started_utc=started, finished_utc=datetime.now(timezone.utc).isoformat())
    return record


def junit_counts(raw):
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    require(suites, "JUnit suites absent")
    counts = {key: sum(int(s.attrib[key]) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    require(all(v >= 0 for v in counts.values()) and counts["tests"] > counts["skipped"]
            and len(root.findall(".//testcase")) == counts["tests"]
            and counts["failures"] == counts["errors"] == 0
            and not root.findall(".//failure") and not root.findall(".//error"), "incomplete or failing JUnit")
    require(len(root.findall(".//skipped")) == counts["skipped"], "JUnit skipped count differs")
    classes = {case.attrib.get("classname", "") for case in root.findall(".//testcase")}
    require(all(any(name == stem or name.endswith("." + stem) for name in classes)
                for stem in ("test_mcp", "test_codex_integration", "test_mcp_discovery_semantics")),
            "required MCP regression classes absent")
    return counts


SMOKE = "import json,pathlib,hashlib,zerorun; print(json.dumps({'version':zerorun.__version__,'module':str(pathlib.Path(zerorun.__file__).resolve()),'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in pathlib.Path(zerorun.__file__).parent.glob('*.py')}}))"
TEST_SCRIPT = "import pathlib,sys; source=pathlib.Path(sys.argv[1]).resolve(); sys.path.insert(0,str(source/'src')); import zerorun,pytest; assert pathlib.Path(zerorun.__file__).resolve()==source/'src/zerorun/__init__.py'; raise SystemExit(pytest.main(sys.argv[2:]))"


def validate(release, receipt_path):
    release, receipt_path = Path(release), Path(receipt_path)
    record = strict_json(regular(receipt_path))
    require(record.get("schema") == SCHEMA and record.get("completed") is True
            and record.get("failure") is None, "current runtime attempt incomplete")
    actual = snapshot(release)
    require(canonical(record.get("source_before")) == canonical(record.get("source_after")) == canonical(actual),
            "current runtime/test source drift")
    require(canonical(record.get("wheel")) == canonical(wheel_check(release)), "tested wheel differs")
    calls = record.get("commands")
    require(isinstance(calls, list) and [r.get("label") for r in calls] == ["create_venv", "install_wheel", "installed_smoke", "installed_help", "public_regression"], "command sequence incomplete")
    for row in calls:
        require(type(row.get("returncode")) is int and row["returncode"] == 0 and row.get("error") is None, "current runtime command failed")
        decoded(row["stdout"]); decoded(row["stderr"])
    expected = {p.name: digest(regular(p)) for p in (release / "src/zerorun").glob("*.py")}
    installed = strict_json(decoded(calls[2]["stdout"]))
    require(installed.get("version") == VERSION and installed.get("files") == expected, "installed runtime differs")
    require("mcp-server" in decoded(calls[3]["stdout"]).decode()
            and "authorize" in decoded(calls[3]["stdout"]).decode(), "installed CLI incomplete")
    commands = [row["command"] for row in calls]
    require(record.get("platform") in {"win32", "linux", "darwin"}, "recorded host platform absent")
    path_type = PureWindowsPath if record["platform"] == "win32" else PurePosixPath
    venv = path_type(record["external_venv"])
    tested_release = path_type(record["tested_release_absolute"])
    output = path_type(record["output_directory_absolute"])
    require(all(path.is_absolute() and ".." not in path.parts for path in (venv, tested_release, output)),
            "recorded execution paths must be absolute and normalized")
    require(not venv.is_relative_to(tested_release), "installation is inside source checkout")
    require(path_type(installed["module"]).is_relative_to(venv), "smoke imported outside new venv")
    require(path_type(record["wheel_input_absolute"]) == tested_release / WHEEL,
            "installed wheel path differs from tested release")
    require(all(path_type(row["cwd"]) == venv.parent for row in calls[:4]),
            "installation or smoke working directory differs")
    installed_executable = str(venv / ("Scripts/python.exe" if record["platform"] == "win32" else "bin/python"))
    # Recorded paths may come from another OS; normalize separators for comparison.
    normalize = lambda value: value.replace("\\", "/")
    require(all(normalize(commands[i][0]) == normalize(installed_executable) for i in (1, 2, 3)),
            "installation/smoke did not use the same new environment")
    require(len(commands[0]) == 4 and commands[0][1:] == ["-m", "venv", record["external_venv"]]
            and len(commands[1]) == 8 and commands[1][1:] == ["-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", str(record["wheel_input_absolute"])]
            and len(commands[2]) == 5 and commands[2][1:] == ["-I", "-B", "-c", SMOKE]
            and len(commands[3]) == 6 and commands[3][1:] == ["-I", "-B", "-m", "zerorun", "--help"], "install/smoke command differs")
    require(len(commands[4]) == 12
            and commands[4][1:5] == ["-B", "-c", TEST_SCRIPT, str(record["tested_release_absolute"])]
            and commands[4][5:10] == ["tests", "-q", "--import-mode=importlib", "-p", "no:cacheprovider"]
            and normalize(commands[4][10]) == "--basetemp=" + normalize(str(venv.parent / "cases"))
            and normalize(commands[4][11]) == "--junitxml=" + normalize(record["output_directory_absolute"]) + "/tests.xml",
            "published regression command differs")
    require(record["tested_release_absolute"] == calls[4]["cwd"], "regression working directory differs")
    xml_path = receipt_path.parent / "tests.xml"
    raw = regular(xml_path)
    require(record.get("junit") == {"path": "tests.xml", "bytes": len(raw), "sha256": digest(raw)}, "JUnit bytes differ")
    counts = junit_counts(raw)
    require(canonical(record.get("junit_counts")) == canonical(counts) and record.get("passed") is True,
            "JUnit/receipt result differs")
    require(record.get("model_called") is False and record.get("docker_called") is False
            and record.get("authority_changed") is False and record.get("historical_evidence_reclassified") is False, "current runtime scope differs")
    return {"schema": SCHEMA, "passed": True, "version": VERSION,
            "current_core_commit": actual["current_core_commit"], "historical_core_commit": HISTORICAL_CORE,
            "changed_runtime_modules": actual["changed_runtime_modules"], "installed_runtime_files": 36,
            "junit_counts": counts, "receipt_sha256": digest(regular(receipt_path)), "wheel": record["wheel"]}


def run(release, output):
    release, output = Path(release).resolve(), Path(output).absolute()
    require(not output.exists() and output.parent.is_dir(), "new output directory required")
    output.mkdir()
    external = Path(tempfile.mkdtemp(prefix="zerorun-current-runtime-"))
    require(not any((p / ".git").exists() for p in (external, *external.parents)), "temporary environment has Git ancestor")
    venv = external / "venv"
    record = {"schema": SCHEMA, "started_utc": datetime.now(timezone.utc).isoformat(),
              "completed": False, "failure": None, "commands": [], "passed": False,
              "external_venv": str(venv), "tested_release_absolute": str(release),
              "output_directory_absolute": str(output), "platform": sys.platform,
              "wheel_input_absolute": str(release / WHEEL), "model_called": False,
              "docker_called": False, "authority_changed": False, "historical_evidence_reclassified": False}
    try:
        require(regular(Path(__file__)) == regular(release / SELF), "executed producer differs from release")
        record["source_before"] = snapshot(release)
        record["wheel"] = wheel_check(release)
        executable = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        commands = [
            ("create_venv", [sys.executable, "-m", "venv", str(venv)], external),
            ("install_wheel", [str(executable), "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", str(release / WHEEL)], external),
            ("installed_smoke", [str(executable), "-I", "-B", "-c", SMOKE], external),
            ("installed_help", [str(executable), "-I", "-B", "-m", "zerorun", "--help"], external),
            ("public_regression", [sys.executable, "-B", "-c", TEST_SCRIPT, str(release), "tests", "-q", "--import-mode=importlib", "-p", "no:cacheprovider", "--basetemp=" + str(external / "cases"), "--junitxml=" + str(output / "tests.xml")], release),
        ]
        environment = os.environ.copy()
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTEST_ADDOPTS", None)
        environment.pop("PYTEST_PLUGINS", None)
        environment["PYTHONPATH"] = str(release / "src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for label, command, cwd in commands:
            row = invoke(label, command, cwd=cwd, env=environment)
            record["commands"].append(row)
            require(row["returncode"] == 0 and row["error"] is None, "command failed: " + label)
        xml = regular(output / "tests.xml")
        record["junit"] = {"path": "tests.xml", "bytes": len(xml), "sha256": digest(xml)}
        record["junit_counts"] = junit_counts(xml)
        record["source_after"] = snapshot(release)
        record["completed"] = True
        record["passed"] = record["source_before"] == record["source_after"]
    except Exception as exc:
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()
    path = output / "receipt.json"
    path.write_bytes(json.dumps(record, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n")
    if record["passed"]:
        result = validate(release, path)
        print(json.dumps(result, sort_keys=True))
        return 0
    print(json.dumps({"passed": False, "failure": record["failure"], "receipt": str(path)}))
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--check", type=Path)
    args = parser.parse_args()
    if args.check:
        print(json.dumps(validate(args.release, args.check), sort_keys=True))
        return 0
    return run(args.release, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
