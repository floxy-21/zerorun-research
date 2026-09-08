"""Offline tamper fixtures only; these tests are not installation evidence."""
from copy import deepcopy
from io import BytesIO
import ast
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
import zipfile
import pytest

from research.softwarex.five_hour_review import current_runtime_053 as current
from research.softwarex.five_hour_review import current_runtime as historical


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def put_json(path, value):
    put(path, json.dumps(value, sort_keys=True).encode())


def _bindings(files):
    return {name: {"bytes": len(raw), "sha256": current.digest(raw)} for name, raw in files.items()}


def _sources():
    old = {name: b"# unchanged offline fixture\n" for name in current.HISTORICAL_FILES}
    old["__init__.py"] = b'__version__ = "0.5.1"\n'
    old["codex.py"] = b'_SKILL = "old policy\\n"\n'
    old["model.py"] = b'class RunResult:\n    wall_ms = 0.0\n    def as_dict(self):\n        return {"wall_ms": self.wall_ms}\n'
    old["mcp.py"] = b'def _tools():\n    root_property = {"type": "string", "description": "root"}\n    return [{"name": "run_tests", "description": "old", "inputSchema": {"properties": {"root": root_property, "verify": {"type": "boolean", "default": False}}, "required": ["task"], "additionalProperties": False}}, {"name": "doctor", "description": "unchanged"}]\n\ndef execute(authorized):\n    return authorized\n'
    new = dict(old)
    new["__init__.py"] = old["__init__.py"].replace(b"0.5.1", b"0.5.3")
    new["mcp.py"] = old["mcp.py"].replace(b'"description": "old"', b'"description": "fresh or reuse"')
    new["codex.py"] = b'_PREVIOUS_SKILL = "old policy\\n"\n_SKILL = "whole-task policy\\n"\n'
    new["model.py"] = old["model.py"].replace(b'class RunResult:\n', b'class RunResult:\n    """Current status and historical duration documented."""\n')
    return old, new


@pytest.fixture
def release_fixture(tmp_path, monkeypatch):
    # Pin a clearly synthetic test inventory before mutations. Production pin
    # constants are never replaced by the producer and are checked separately.
    old, new = _sources()
    monkeypatch.setattr(current, "HISTORICAL_FILES", _bindings(old))
    monkeypatch.setattr(current, "CURRENT_FILES", _bindings(new))
    support = {name: b"# artificial regression fixture; never execution evidence\n" for name in current.REQUIRED_TESTS}
    support["docs/zerorun-SKILL.md"] = b"whole-task policy\n"
    monkeypatch.setattr(current, "REQUIRED_SUPPORT", _bindings(support))
    release = tmp_path / "release"
    indexed = []
    for prefix, files, commit in (("src/zerorun", new, current.CURRENT_CORE),
        (current.HISTORICAL_PREFIX + "/src/zerorun", old, current.HISTORICAL_CORE)):
        for name, raw in files.items():
            relative = prefix + "/" + name
            put(release / relative, raw)
            indexed.append({"path": relative, **_bindings({name: raw})[name],
                            "origin": "git:" + commit + ":zerorun/" + name})
    for relative, raw in support.items():
        put(release / relative, raw)
        indexed.append({"path": relative, **_bindings({relative: raw})[relative],
                        "origin": current.SUPPORT_ORIGINS[relative]})
    put(release / current.SELF, Path(current.__file__).read_bytes())
    put(release / historical.SELF, Path(historical.__file__).read_bytes())
    put(release / "pyproject.toml", b'[project]\nname = "zerorun"\nversion = "0.5.3"\n')
    put_json(release / "PUBLIC_RELEASE_MANIFEST.json", {"current_core_commit": current.CURRENT_CORE,
        "historical_core_commit": current.HISTORICAL_CORE, "current_version": "0.5.3", "files": indexed})
    wheel = BytesIO()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in new.items(): archive.writestr("zerorun/" + name, raw)
        archive.writestr("zerorun-0.5.3.dist-info/METADATA", "Name: zerorun\nVersion: 0.5.3\nLicense-Expression: MIT\n")
    put(release / current.WHEEL, wheel.getvalue())
    return release


XML = b'<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"><testcase classname="tests.test_mcp" name="fixture"/><testcase classname="tests.test_codex_integration" name="fixture"/><testcase classname="tests.test_mcp_discovery_semantics" name="fixture"/></testsuite></testsuites>'

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
    smoke = json.dumps({"version": "0.5.3", "module": str(venv / "lib/zerorun/__init__.py"), "files": files}).encode()
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

def test_historical_checker_is_still_strictly_052():
    assert historical.VERSION == "0.5.2"
    assert historical.ALLOWED_CHANGED == {"__init__.py", "mcp.py"}
    assert current.SCHEMA != historical.SCHEMA


def test_runtime_denominator_and_exact_accepted_changes(release_fixture):
    result = current.snapshot(release_fixture)
    assert len(result["runtime_files"]) == 72
    assert result["unchanged_computational_modules"] == 32
    assert result["changed_runtime_modules"] == ["__init__.py", "codex.py", "mcp.py", "model.py"]
    assert current.wheel_check(release_fixture)["path"] == current.WHEEL


@pytest.mark.parametrize("mutation", ["codex", "model", "compute", "old", "removed", "added", "nested", "support", "origin", "commit", "version", "duplicate"])
def test_source_manifest_cannot_relabel_or_hide_changes(release_fixture, mutation):
    release = release_fixture
    manifest_path = release / "PUBLIC_RELEASE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_bytes())
    relative = {"codex": "src/zerorun/codex.py", "model": "src/zerorun/model.py",
                "compute": "src/zerorun/hermetic.py", "old": current.HISTORICAL_PREFIX + "/src/zerorun/hermetic.py",
                "support": "tests/test_codex_integration.py"}.get(mutation)
    if relative:
        put(release / relative, (release / relative).read_bytes() + b"# changed\n")
        # Even a refreshed manifest cannot override the fixed source pins.
        for row in manifest["files"]:
            if row["path"] == relative:
                row.update(_bindings({relative: (release / relative).read_bytes()})[relative])
    elif mutation == "removed": (release / "src/zerorun/hermetic.py").unlink()
    elif mutation == "added": put(release / "src/zerorun/new.py", b"# new\n")
    elif mutation == "nested": put(release / "src/zerorun/extra/__init__.py", b"# hidden module\n")
    elif mutation == "origin": manifest["files"][0]["origin"] = "git:" + current.HISTORICAL_CORE + ":zerorun/__init__.py"
    elif mutation == "commit": manifest["current_core_commit"] = "0" * 40
    elif mutation == "version": manifest["current_version"] = "0.5.2"
    elif mutation == "duplicate": manifest["files"].append(deepcopy(manifest["files"][0]))
    put_json(manifest_path, manifest)
    with pytest.raises((ValueError, OSError)):
        current.snapshot(release)


@pytest.mark.parametrize("mutation", ["field", "method", "new-class", "missing-doc", "function-doc"])
def test_model_docstring_exception_cannot_change_computation(mutation):
    old, new = _sources()
    raw = new["model.py"]
    if mutation == "field": raw = raw.replace(b"wall_ms = 0.0", b"wall_ms = 1.0")
    elif mutation == "method": raw = raw.replace(b"self.wall_ms", b"0.0")
    elif mutation == "new-class": raw += b"class Other: pass\n"
    elif mutation == "missing-doc": raw = old["model.py"]
    else: raw = raw.replace(b"def as_dict(self):\n", b'def as_dict(self):\n        """Unapproved other docstring."""\n')
    with pytest.raises(ValueError): current.validate_model_documentation(old["model.py"], raw)


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_complete_receipt_is_cross_platform_and_separate_from_history(release_fixture, platform):
    path, _ = receipt_fixture(release_fixture, platform)
    result = current.validate(release_fixture, path)
    assert result["version"] == "0.5.3" and result["passed"]
    assert result["installed_runtime_files"] == 36
    assert result["unchanged_computational_modules"] == 32
    with pytest.raises(ValueError): historical.validate(release_fixture, path)


@pytest.mark.parametrize("mutation", ["schema", "smoke-file", "smoke-version", "subset", "online", "source-drift", "stream", "scope", "return-bool", "outside-install"])
def test_receipt_does_not_borrow_results_or_relax_installation(release_fixture, mutation):
    path, record = receipt_fixture(release_fixture)
    if mutation == "schema": record["schema"] = historical.SCHEMA
    elif mutation.startswith("smoke"):
        smoke = json.loads(current.decoded(record["commands"][2]["stdout"]))
        if mutation == "smoke-file": smoke["files"]["codex.py"] = "0" * 64
        else: smoke["version"] = "0.5.2"
        record["commands"][2]["stdout"] = current.stream(json.dumps(smoke).encode())
    elif mutation == "subset": record["commands"][4]["command"][5] = "tests/test_mcp.py"
    elif mutation == "online": record["commands"][1]["command"].remove("--no-index")
    elif mutation == "source-drift": record["source_after"]["version"] = "0.5.2"
    elif mutation == "stream": record["commands"][2]["stdout"]["sha256"] = "0" * 64
    elif mutation == "scope": record["authority_changed"] = True
    elif mutation == "return-bool": record["commands"][0]["returncode"] = False
    elif mutation == "outside-install": record["external_venv"] = "/lab/release/venv"
    put_json(path, record)
    with pytest.raises(ValueError): current.validate(release_fixture, path)


@pytest.mark.parametrize("mutation", ["module", "version", "payload"])
def test_wheel_requires_exact_installed_runtime_and_no_extra_payload(release_fixture, mutation):
    path = release_fixture / current.WHEEL
    buffer = BytesIO()
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(buffer, "w") as target:
        for name in source.namelist():
            raw = source.read(name)
            if mutation == "module" and name == "zerorun/codex.py": raw += b"# changed\n"
            if mutation == "version" and name.endswith("METADATA"): raw = raw.replace(b"0.5.3", b"0.5.2")
            target.writestr(name, raw)
        if mutation == "payload": target.writestr("startup.pth", b"import unexpected\n")
    path.write_bytes(buffer.getvalue())
    with pytest.raises(ValueError): current.wheel_check(release_fixture)


def test_previous_skill_matches_the_frozen_utf8_payload_not_its_own_constant():
    root = Path(__file__).resolve().parents[3]
    path = root / "zerorun/codex.py"
    if not path.is_file(): path = root / "src/zerorun/codex.py"
    previous = current._literal_assignment(path.read_bytes(), "_PREVIOUS_SKILL")
    assert current.digest(previous.encode("utf-8")) == "564a9804d2587d8164a509a864e31e9ca5320f8d2af18648be4094eff9ae9b96"


def test_direct_checker_cli_uses_its_sibling_helper(tmp_path):
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(current.__file__).resolve()), "--help"],
        cwd=tmp_path, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert b"--release" in result.stdout and b"--check" in result.stdout
