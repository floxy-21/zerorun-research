"""Adversarial offline version-binding fixtures, not actual installation claims."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import sys
import zipfile

import pytest

from research.softwarex import build_public_release as public
from research.softwarex import build_readiness as readiness
from research.softwarex.five_hour_review import current_runtime as current
from research.softwarex.tests.test_readiness_bindings import install_fixture
from research.softwarex.tests.test_submission_artifacts import fixture, put, put_json


OLD_MCP = b'''def _tools():
    root_property = {"type": "string", "description": "root"}
    return [{"name": "run_tests", "description": "old", "inputSchema": {"properties": {"root": root_property, "verify": {"type": "boolean", "default": False}}, "required": ["task"], "additionalProperties": False}}, {"name": "doctor", "description": "unchanged"}]

def execute(authorized):
    return authorized
'''
NEW_MCP = OLD_MCP.replace(b'"description": "old"', b'"description": "new neutral semantics"').replace(
    b'"root": root_property', b'"root": {**root_property, "description": "repository bound"}').replace(
    b'"type": "boolean", "default": False', b'"type": "boolean", "default": False, "description": "false allows reuse; true fresh"')


def sources():
    old = {"__init__.py": b'__version__ = "0.5.1"\n', "mcp.py": OLD_MCP}
    old.update({f"module_{n:02}.py": b"# unchanged fixture\n" for n in range(34)})
    new = {**old, "__init__.py": b'__version__ = "0.5.2"\n', "mcp.py": NEW_MCP}
    return old, new


def test_exact_version_and_neutral_discovery_change_accepted():
    old, new = sources()
    assert current.validate_metadata_only(old, new) == ["__init__.py", "mcp.py"]
    assert old["mcp.py"] == OLD_MCP and new["mcp.py"] == NEW_MCP


@pytest.mark.parametrize("mutation", ["extra-runtime", "version-code", "execution", "default", "authority", "argument", "other-tool", "removed-file", "new-file"])
def test_only_description_change_never_waives_engine_or_schema(mutation):
    old, new = sources()
    if mutation == "extra-runtime":
        new["module_00.py"] = b"# changed\n"
    elif mutation == "version-code":
        new["__init__.py"] += b"print('unexpected side effect')\n"
    elif mutation == "execution":
        new["mcp.py"] = new["mcp.py"].replace(b"return authorized", b"return True")
    elif mutation == "default":
        new["mcp.py"] = new["mcp.py"].replace(b'"default": False', b'"default": True')
    elif mutation == "authority":
        new["mcp.py"] = new["mcp.py"].replace(b'def execute(authorized)', b'def execute(authorized=True)')
    elif mutation == "argument":
        new["mcp.py"] = new["mcp.py"].replace(b'"additionalProperties": False', b'"additionalProperties": True')
    elif mutation == "other-tool":
        new["mcp.py"] = new["mcp.py"].replace(b'"description": "unchanged"', b'"description": "changed"')
    elif mutation == "removed-file":
        new.pop("module_00.py")
    else:
        new["extra.py"] = b"# extra\n"
    with pytest.raises(ValueError):
        current.validate_metadata_only(old, new)


def test_historical_checker_reads_exact_preserved_bytes_not_current_runtime(install_fixture):
    release = install_fixture["release"]
    relative = "src/zerorun/core.py"
    old = (release / relative).read_bytes()
    put(release / current.HISTORICAL_PREFIX / relative, old)
    put(release / relative, b"# distinct current version\n")
    _, tests = readiness.check_test_and_install_bindings(release, historical_runtime_prefix=current.HISTORICAL_PREFIX)
    assert tests["pytest_passed"] == 3
    with pytest.raises(ValueError):
        readiness.check_test_and_install_bindings(release)
    put(release / current.HISTORICAL_PREFIX / relative, b"# changed historical code\n")
    with pytest.raises(ValueError, match="historical runtime differs"):
        readiness.check_test_and_install_bindings(release, historical_runtime_prefix=current.HISTORICAL_PREFIX)


def test_current_packaging_version_is_explicit_and_legacy_default_preserved():
    assert b'version = "0.5.1"' in public.pyproject()
    assert b'version = "0.5.2"' in public.pyproject("0.5.2")
    with pytest.raises(ValueError):
        public.pyproject("9.9.9")


@pytest.fixture
def current_release(tmp_path):
    release = tmp_path / "release"
    old, new = sources()
    indexed = []
    for prefix, files, commit in (("src/zerorun", new, public.CURRENT_CORE),
                                  (current.HISTORICAL_PREFIX + "/src/zerorun", old, current.HISTORICAL_CORE)):
        for name, raw in files.items():
            relative = prefix + "/" + name
            put(release / relative, raw)
            indexed.append({"path": relative, "bytes": len(raw), "sha256": current.digest(raw),
                            "origin": "git:" + commit + ":zerorun/" + name})
    for path in current.REQUIRED_TESTS:
        put(release / path, b"# artificial regression fixture; not executed\n")
    put(release / current.SELF, Path(current.__file__).read_bytes())
    put(release / "pyproject.toml", public.pyproject("0.5.2"))
    manifest = {"current_core_commit": public.CURRENT_CORE, "historical_core_commit": current.HISTORICAL_CORE,
                "current_version": "0.5.2", "files": indexed}
    put_json(release / "PUBLIC_RELEASE_MANIFEST.json", manifest)
    wheel = BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in new.items():
            archive.writestr("zerorun/" + name, raw)
        archive.writestr("zerorun-0.5.2.dist-info/METADATA", "Name: zerorun\nVersion: 0.5.2\nLicense-Expression: MIT\n")
    put(release / current.WHEEL, wheel.getvalue())
    return release


def test_snapshot_preserves_all_72_runtime_files(current_release):
    result = current.snapshot(current_release)
    assert result["version"] == "0.5.2" and len(result["runtime_files"]) == 72
    assert result["changed_runtime_modules"] == ["__init__.py", "mcp.py"]


@pytest.mark.parametrize("mode", ["origin", "hash", "version", "missing-test", "missing-historical", "changed-current"])
def test_current_source_inventory_cannot_borrow_historical_success(current_release, mode):
    path = current_release / "PUBLIC_RELEASE_MANIFEST.json"
    manifest = json.loads(path.read_bytes())
    if mode == "origin":
        manifest["files"][0]["origin"] = "git:" + current.HISTORICAL_CORE + ":zerorun/__init__.py"
    elif mode == "hash":
        manifest["files"][0]["sha256"] = "0" * 64
    elif mode == "version":
        manifest["current_version"] = "0.5.1"
    elif mode == "missing-test":
        (current_release / "tests/test_mcp_discovery_semantics.py").unlink()
    elif mode == "missing-historical":
        (current_release / current.HISTORICAL_PREFIX / "src/zerorun/mcp.py").unlink()
    else:
        put(current_release / "src/zerorun/module_00.py", b"# altered engine\n")
    put_json(path, manifest)
    with pytest.raises(ValueError):
        current.snapshot(current_release)


def test_wheel_current_files_are_exact(current_release):
    assert current.wheel_check(current_release)["path"] == current.WHEEL


@pytest.mark.parametrize("mode", ["old-version", "changed-module", "startup-payload"])
def test_wheel_identity_or_unexpected_payload_is_rejected(current_release, mode):
    path = current_release / current.WHEEL
    changed = BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            raw = source.read(name)
            if mode == "old-version" and name.endswith("METADATA"):
                raw = raw.replace(b"Version: 0.5.2", b"Version: 0.5.1")
            if mode == "changed-module" and name == "zerorun/mcp.py":
                raw += b"# unexpected edit\n"
            target.writestr(name, raw)
        if mode == "startup-payload":
            target.writestr("startup.pth", b"import something\n")
    put(path, changed.getvalue())
    with pytest.raises(ValueError):
        current.wheel_check(current_release)


XML = b'<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"><testcase classname="tests.test_mcp" name="fixture"/><testcase classname="tests.test_codex_integration" name="fixture"/><testcase classname="tests.test_mcp_discovery_semantics" name="fixture"/></testsuite></testsuites>'


@pytest.mark.parametrize("mode", ["failure-node", "count", "skips", "missing-new-tests"])
def test_junit_does_not_trust_green_summary(mode):
    raw = XML
    if mode == "failure-node":
        raw = raw.replace(b'name="fixture"/>', b'name="fixture"><failure/></testcase>', 1)
    elif mode == "count":
        raw = raw.replace(b'tests="3"', b'tests="30"')
    elif mode == "skips":
        raw = raw.replace(b'skipped="0"', b'skipped="1"')
    else:
        raw = raw.replace(b'tests.test_mcp_discovery_semantics', b'tests.other')
    with pytest.raises(ValueError):
        current.junit_counts(raw)


@pytest.mark.parametrize("represented", [False, True])
def test_junit_passing_subtests_are_reconciled_against_retained_summary(represented):
    # Pytest 9's unittest subtests can increment tests without XML testcase
    # elements. This models the observed structure, with two primary skips.
    raw = XML.replace(b'tests="3"', b'tests="13"').replace(b'skipped="0"', b'skipped="2"')
    added = b'<testcase classname="tests.test_platform" name="skip-one"><skipped/></testcase><testcase classname="tests.test_platform" name="skip-two"><skipped/></testcase>'
    if represented:
        added += b''.join(b'<testcase classname="tests.test_subtests" name="sub-' + str(i).encode() + b'"/>' for i in range(8))
    raw = raw.replace(b'</testsuite>', added + b'</testsuite>')
    result = current.junit_counts(raw, pytest_stdout=b'...\n3 passed, 2 skipped, 8 subtests passed in 0.20s\n')
    assert result == {"tests": 13, "failures": 0, "errors": 0, "skipped": 2,
                      "testcase_elements": 13 if represented else 5,
                      "reported_passing_subtests": 8,
                      "passing_subtests_without_testcase": 0 if represented else 8}
    if not represented:
        with pytest.raises(ValueError, match="requires a retained pytest summary"):
            current.junit_counts(raw)


@pytest.mark.parametrize("mode", ["wrong-subtests", "wrong-primary", "wrong-skips", "failure-node",
                                 "error-node", "failed-subtests", "missing-summary", "duplicate-count",
                                 "missing-required-class", "unexplained-testcase", "partial-subtest-elements"])
def test_junit_subtest_accounting_cannot_hide_missing_or_failed_tests(mode):
    raw = XML.replace(b'tests="3"', b'tests="11"')
    summary = b'3 passed, 8 subtests passed in 0.20s\n'
    if mode == "wrong-subtests":
        summary = b'3 passed, 7 subtests passed in 0.20s\n'
    elif mode == "wrong-primary":
        summary = b'2 passed, 9 subtests passed in 0.20s\n'
    elif mode == "wrong-skips":
        summary = b'2 passed, 1 skipped, 8 subtests passed in 0.20s\n'
    elif mode in {"failure-node", "error-node"}:
        tag = b'failure' if mode == "failure-node" else b'error'
        raw = raw.replace(b'name="fixture"/>', b'name="fixture"><' + tag + b'/></testcase>', 1)
    elif mode == "failed-subtests":
        summary = b'3 passed, 8 subtests failed in 0.20s\n'
    elif mode == "missing-summary":
        summary = b'...\n'
    elif mode == "duplicate-count":
        summary = b'3 passed, 8 subtests passed, 8 subtests passed in 0.20s\n'
    elif mode == "missing-required-class":
        raw = raw.replace(b'tests.test_mcp_discovery_semantics', b'tests.other')
    elif mode == "unexplained-testcase":
        raw = raw.replace(b'<testcase classname="tests.test_mcp" name="fixture"/>', b'')
    else:
        raw = raw.replace(b'</testsuite>', b'<testcase classname="tests.test_subtests" name="one-subtest"/></testsuite>')
    with pytest.raises(ValueError):
        current.junit_counts(raw, pytest_stdout=summary)


@pytest.mark.parametrize("raw", [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'])
def test_duplicate_or_nonfinite_receipt_json_refused(raw):
    with pytest.raises(ValueError):
        current.strict_json(raw)


def receipt_fixture(release, platform="linux"):
    """Artificial streams only: this unit fixture is never execution evidence."""
    paths = PureWindowsPath if platform == "win32" else PurePosixPath
    prefix = "C:/lab" if platform == "win32" else "/lab"
    recorded_release = paths(prefix) / "release"
    external = paths(prefix) / "temporary-environment"
    venv = external / "venv"
    output = paths(prefix) / "evidence"
    executable = str(venv / ("Scripts/python.exe" if platform == "win32" else "bin/python"))
    host = "C:/Python/python.exe" if platform == "win32" else "/usr/bin/python3"
    wheel = str(recorded_release / current.WHEEL)
    commands = [
        [host, "-m", "venv", str(venv)],
        [executable, "-I", "-B", "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", wheel],
        [executable, "-I", "-B", "-c", current.SMOKE],
        [executable, "-I", "-B", "-m", "zerorun", "--help"],
        [host, "-B", "-c", current.TEST_SCRIPT, str(recorded_release), "tests", "-q", "--import-mode=importlib", "-p", "no:cacheprovider", "--basetemp=" + str(external / "cases"), "--junitxml=" + str(output / "tests.xml")],
    ]
    files = {p.name: current.digest(p.read_bytes()) for p in (release / "src/zerorun").glob("*.py")}
    smoke = json.dumps({"version": "0.5.2", "module": str(venv / "lib/zerorun/__init__.py"), "files": files}).encode()
    calls = []
    labels = ["create_venv", "install_wheel", "installed_smoke", "installed_help", "public_regression"]
    for index, command in enumerate(commands):
        stdout = (smoke if index == 2 else b"mcp-server authorize" if index == 3
                  else b"3 passed in 0.01s\n" if index == 4 else b"artificial offline fixture")
        calls.append({"label": labels[index], "command": command,
                      "cwd": str(recorded_release if index == 4 else external), "returncode": 0,
                      "error": None, "stdout": current.stream(stdout), "stderr": current.stream(b"")})
    snapshot = current.snapshot(release)
    record = {"schema": current.SCHEMA, "completed": True, "failure": None, "passed": True,
              "platform": platform, "external_venv": str(venv), "tested_release_absolute": str(recorded_release),
              "output_directory_absolute": str(output), "wheel_input_absolute": wheel,
              "source_before": snapshot, "source_after": deepcopy(snapshot), "wheel": current.wheel_check(release),
              "commands": calls, "junit": {"path": "tests.xml", "bytes": len(XML), "sha256": current.digest(XML)},
              "junit_counts": current.junit_counts(XML), "model_called": False, "docker_called": False,
              "authority_changed": False, "historical_evidence_reclassified": False}
    path = release.parent / "offline-evidence" / "receipt.json"
    put_json(path, record)
    put(path.parent / "tests.xml", XML)
    return path, record


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_complete_offline_receipt_validation_is_cross_platform(current_release, platform):
    path, _ = receipt_fixture(current_release, platform)
    result = current.validate(current_release, path)
    assert result["passed"] is True and result["version"] == "0.5.2"
    assert result["junit_counts"]["tests"] == 3
    assert result["current_core_commit"] == public.CURRENT_CORE


@pytest.mark.parametrize("mode", [
    "failure", "source-after", "source-omission", "old-smoke", "smoke-byte", "smoke-outside",
    "smoke-program", "stream-hash", "return-bool", "extra-command", "missing-command", "subset-tests",
    "selection-flag", "junit-path", "junit-bytes", "junit-bool", "wheel-path", "online-install",
    "different-venv", "source-install", "relative-path", "wrong-cwd", "nonisolated-pip", "forced-install",
    "model", "docker", "authority", "reclassified",
])
def test_full_offline_receipt_cannot_misstate_install_or_test_scope(current_release, mode):
    path, record = receipt_fixture(current_release)
    calls = record["commands"]
    if mode == "failure":
        record["failure"] = {"message": "failed but claimed passed"}
    elif mode == "source-after":
        record["source_after"]["runtime_files"][0]["sha256"] = "0" * 64
    elif mode == "source-omission":
        record["source_before"]["test_and_helper_files"].pop()
        record["source_after"] = deepcopy(record["source_before"])
    elif mode in {"old-smoke", "smoke-byte", "smoke-outside"}:
        smoke = current.strict_json(current.decoded(calls[2]["stdout"]))
        if mode == "old-smoke":
            smoke["version"] = "0.5.1"
        elif mode == "smoke-byte":
            smoke["files"]["mcp.py"] = "0" * 64
        else:
            smoke["module"] = "/lab/release/src/zerorun/__init__.py"
        calls[2]["stdout"] = current.stream(json.dumps(smoke).encode())
    elif mode == "smoke-program":
        calls[2]["command"][-1] = "print('claimed without import')"
    elif mode == "stream-hash":
        calls[0]["stdout"]["sha256"] = "0" * 64
    elif mode == "return-bool":
        calls[0]["returncode"] = False
    elif mode == "extra-command":
        calls.append(deepcopy(calls[-1]))
    elif mode == "missing-command":
        calls.pop()
    elif mode == "subset-tests":
        calls[4]["command"][5] = "tests/test_mcp.py"
    elif mode == "selection-flag":
        calls[4]["command"] += ["-k", "convenient"]
    elif mode == "junit-path":
        calls[4]["command"][-1] = "--junitxml=/other/evidence.xml"
    elif mode == "junit-bytes":
        put(path.parent / "tests.xml", XML + b"\n")
    elif mode == "junit-bool":
        record["junit_counts"]["errors"] = False
    elif mode == "wheel-path":
        record["wheel_input_absolute"] = "/lab/other/wheel.whl"
        calls[1]["command"][-1] = record["wheel_input_absolute"]
    elif mode == "online-install":
        calls[1]["command"].remove("--no-index")
    elif mode == "different-venv":
        calls[2]["command"][0] = "/usr/bin/python3"
    elif mode == "source-install":
        record["external_venv"] = "/lab/release/venv"
    elif mode == "relative-path":
        record["external_venv"] = "relative/venv"
    elif mode == "wrong-cwd":
        calls[1]["cwd"] = "/other"
    elif mode == "nonisolated-pip":
        calls[1]["command"].remove("-I")
    elif mode == "forced-install":
        calls[1]["command"].insert(-1, "--force-reinstall")
    else:
        key = {"model": "model_called", "docker": "docker_called", "authority": "authority_changed", "reclassified": "historical_evidence_reclassified"}[mode]
        record[key] = True
    put_json(path, record)
    with pytest.raises(ValueError):
        current.validate(current_release, path)


def test_installation_environment_does_not_expose_source_metadata(monkeypatch):
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        monkeypatch.setenv(name, "/source/with/zerorun.egg-info")
    monkeypatch.setenv("TASK_UNRELATED_SENTINEL", "preserve")
    environment = current.execution_environment()
    assert not set(environment) & {"PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
    assert environment["TASK_UNRELATED_SENTINEL"] == "preserve"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert current.os.environ["PYTHONPATH"] == "/source/with/zerorun.egg-info"


def test_regression_launcher_propagates_only_exact_source_to_children(tmp_path):
    source = tmp_path / "regression-source"
    put(source / "src/zerorun/__init__.py", b'__version__ = "offline-child-fixture"\n')
    put(source / "src/pytest.py", b'''import json,os,subprocess,sys
def main(arguments):
    child = subprocess.run([sys.executable, '-B', '-c', 'import zerorun; print(zerorun.__version__)'], capture_output=True, text=True)
    print(json.dumps({'child_returncode': child.returncode, 'child_output': child.stdout.strip(), 'source_path': os.environ.get('PYTHONPATH')}))
    return child.returncode
''')
    environment = current.execution_environment()
    assert "PYTHONPATH" not in environment
    result = subprocess.run([sys.executable, "-B", "-c", current.TEST_SCRIPT, str(source), "tests"],
                            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record == {"child_returncode": 0, "child_output": "offline-child-fixture",
                      "source_path": str((source / "src").resolve())}
    assert "PYTHONPATH" not in environment
