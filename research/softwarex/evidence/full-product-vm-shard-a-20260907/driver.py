"""Fresh exact-commit source regression on a Linux laboratory Python.

This substitutes for one hosted regression job, not the full Python matrix,
model transport experiments, or commercial performance gates. All outputs and
temporary test state are external to the fresh checkout. No model or production
reuse authority is created by this driver; upstream unit fixtures remain intact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

CONFIRMATORY = "tests/test_research_confirmatory.py"
REPOSITORY = "https://github.com/floxy-21/zerorun-mvp.git"
EXPECTED_TEST_MODULES = 91
PYTHON = "/home/floxy/.zerorun-final-venv-d7c9764efe3c/bin/python"


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def environment():
    result = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        result.pop(name, None)
    result.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", GIT_TERMINAL_PROMPT="0",
                  GCM_INTERACTIVE="Never", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
    return result


def invoke(output, calls, label, argv, cwd, env, timeout=300):
    row = {"label": label, "command": list(argv), "cwd": str(cwd), "started_utc": utc(),
           "returncode": None, "error": None, "timeout_seconds": timeout}
    calls.append(row)
    save(output / (label + ".started.json"), row)
    print(json.dumps({"stage": label, "state": "started"}), flush=True)
    start = time.perf_counter()
    stdout = stderr = b""
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            row["returncode"] = process.returncode
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            row["returncode"] = process.returncode
            row["error"] = {"type": "TimeoutExpired", "message": label + " exceeded its fixed timeout"}
    finally:
        row.update(completed_utc=utc(), outer_seconds=time.perf_counter() - start)
        for stream, raw in (("stdout", stdout), ("stderr", stderr)):
            name = label + "." + stream + ".txt"
            (output / name).write_bytes(raw)
            row[stream] = {"path": name, "bytes": len(raw), "sha256": sha(raw)}
        save(output / (label + ".json"), row)
    print(json.dumps({"stage": label, "returncode": row["returncode"], "error": row["error"]}), flush=True)
    return row, stdout


def inventory(checkout, git_env):
    result = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=checkout, env=git_env,
                            check=True, capture_output=True)
    rows = []
    for entry in result.stdout.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.decode().split("\t", 1)
        mode, blob, stage = metadata.split()
        require(stage == "0" and mode in {"100644", "100755"}, "unexpected tracked file type or conflict")
        path = checkout / name
        require(path.is_file() and not path.is_symlink(), "tracked source is not an ordinary file")
        raw = path.read_bytes()
        rows.append({"path": name, "git_mode": mode, "git_blob": blob, "bytes": len(raw), "sha256": sha(raw)})
    return sorted(rows, key=lambda row: row["path"])


def junit_summary(raw, stdout, expected_modules):
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    require(suites, "JUnit suites absent")
    counts = {name: sum(int(s.attrib[name]) for s in suites) for name in ("tests", "failures", "errors", "skipped")}
    cases = root.findall(".//testcase")
    require(all(n >= 0 for n in counts.values()) and counts["failures"] == counts["errors"] == 0
            and not root.findall(".//failure") and not root.findall(".//error"), "failing or invalid JUnit")
    require(len(root.findall(".//skipped")) == counts["skipped"], "JUnit skips disagree")
    lines = [line.strip().strip("=").strip() for line in stdout.decode().splitlines() if line.strip()]
    match = re.fullmatch(r"(.+) in [0-9]+(?:\.[0-9]+)?s(?: \([0-9:]+\))?", lines[-1] if lines else "")
    require(match is not None, "pytest final summary absent")
    fields = {}
    for part in match.group(1).split(", "):
        field = re.fullmatch(r"([0-9]+) (passed|skipped|subtests passed|warnings?)", part)
        require(field is not None and field.group(2) not in fields, "unsupported or repeated pytest outcome")
        fields[field.group(2)] = int(field.group(1))
    passed, skipped, subtests = fields.get("passed", 0), fields.get("skipped", 0), fields.get("subtests passed", 0)
    require(passed > 0 and skipped == counts["skipped"] and passed + skipped + subtests == counts["tests"]
            and len(cases) in {passed + skipped, counts["tests"]}, "pytest/JUnit primary or subtest counts disagree")
    classes = {case.get("classname", "") for case in cases}
    represented = {stem for stem in expected_modules if any(stem in classname.split(".") for classname in classes)}
    require(represented == set(expected_modules), "one or more full-suite modules missing from JUnit")
    return dict(counts, primary_passed=passed, passing_subtests=subtests, testcase_elements=len(cases),
                module_count=len(represented), represented_modules=sorted(represented))


NODE_PLUGIN = r'''"""External observer: records pytest collection and reports; never selects tests."""
import json
from pathlib import Path
OUTPUT = Path(__OUTPUT_JSON__)
DESELECTED = []

def emit(name, value):
    with (OUTPUT / name).open("a", encoding="utf8") as stream:
        stream.write(json.dumps(value, sort_keys=True, allow_nan=False) + "\n")

def pytest_deselected(items):
    DESELECTED.extend(item.nodeid for item in items)

def pytest_collection_finish(session):
    for index, item in enumerate(session.items):
        emit("collected-nodes.jsonl", {"index": index, "nodeid": item.nodeid, "module": item.nodeid.split("::", 1)[0]})
    value = {"collected_count": len(session.items), "deselected_count": len(DESELECTED),
             "deselected_nodeids": DESELECTED, "config_args": list(session.config.args),
             "keyword": session.config.option.keyword, "markexpr": session.config.option.markexpr}
    (OUTPUT / "collection-summary.json").write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf8")

def pytest_runtest_logreport(report):
    emit("test-reports.jsonl", {"nodeid": report.nodeid, "phase": report.when,
         "outcome": report.outcome, "duration_seconds": report.duration,
         "expected_failure": str(report.wasxfail) if hasattr(report, "wasxfail") else None})
'''


def run(output, python, git_source, expected_commit, shard):
    require(sys.platform == "linux" and os.geteuid() != 0, "non-root Linux laboratory required")
    require(re.fullmatch(r"[0-9a-f]{40}", expected_commit) is not None, "explicit exact commit required")
    require(git_source.startswith("file:///") and not any(x in git_source for x in ("?", "#", "\n", "\r")),
            "credential-free absolute local file:// Git transport required")
    require(shard in {"confirmatory", "other90"}, "unknown shard")
    output = output.resolve()
    require(not output.exists() and output.parent.is_dir(), "new external evidence directory required")
    output.mkdir()
    (output / "driver.py").write_bytes(Path(__file__).read_bytes())
    temporary = Path(tempfile.mkdtemp(prefix="zerorun-full-product-"))
    checkout = temporary / "checkout"
    checkout.mkdir()
    git_env = environment()
    record = {"schema": "zerorun.full-product-vm-regression-shard.v1", "started_utc": utc(), "commit": expected_commit,
              "repository": REPOSITORY, "driver": {"bytes": Path(__file__).stat().st_size,
                  "sha256": sha(Path(__file__).read_bytes())}, "python": python, "checkout": str(checkout),
              "temporary_directory": str(temporary), "commands": [], "passed": False, "failure": None,
              "scope": "One declared disjoint shard of the full repository pytest suite, for one existing CPython environment; not independently the full suite, hosted matrix, model experiments, or commercial gates",
              "test_selection": "all tests in the selected shard modules; no -k/-m or node exclusions; both shard reports are required for full-suite coverage",
              "runtime_import": "exact fresh commit source precedes installed packages; PYTHONPATH is propagated to subprocess children",
              "transport": {"git_source": git_source, "original_repository_pointer": REPOSITORY,
                            "source_kind": "private local Git object transport" if git_source.startswith("file://") else "original HTTPS repository",
                            "repository_credentials_forwarded": False},
              "new_model_calls": 0, "production_authority_created": False,
              "commercial_performance_gate_claimed": False, "shard": shard, "both_shards_required": True}
    before = None
    try:
        commands = [
            ("git_init", ["git", "init", "--template=", "--initial-branch=main", str(checkout)], temporary),
            ("git_remote", ["git", "remote", "add", "origin", REPOSITORY], checkout),
            ("git_fetch", ["git", "-c", "credential.helper=", "fetch", "--depth=1", git_source, expected_commit], checkout),
            ("git_checkout", ["git", "checkout", "--detach", "FETCH_HEAD"], checkout),
            ("git_head", ["git", "rev-parse", "HEAD"], checkout),
        ]
        for label, argv, cwd in commands:
            row, raw = invoke(output, record["commands"], label, argv, cwd, git_env)
            require(row["returncode"] == 0 and row["error"] is None, label + " failed")
        require(raw.decode().strip() == expected_commit, "checkout is not the exact expected commit")
        before = inventory(checkout, git_env)
        save(output / "source-before.json", before)
        all_modules = sorted(row["path"] for row in before
                             if row["path"].startswith("tests/test_") and row["path"].endswith(".py"))
        require(len(all_modules) == EXPECTED_TEST_MODULES and CONFIRMATORY in all_modules,
                "full product test-module denominator or confirmatory module changed")
        partitions = {"confirmatory": [CONFIRMATORY], "other90": [x for x in all_modules if x != CONFIRMATORY]}
        require(len(partitions["other90"]) == 90 and not set(partitions["confirmatory"]) & set(partitions["other90"])
                and sorted(partitions["confirmatory"] + partitions["other90"]) == all_modules,
                "shard plan is not exhaustive and disjoint")
        plan = {"schema": "zerorun.full-product-vm-shard-plan.v1", "commit": expected_commit,
                "all_modules": all_modules, "shards": partitions, "exhaustive": True, "disjoint": True,
                "node_filters": [], "both_shards_required_for_full_coverage": True}
        save(output / "shard-plan.json", plan)
        record["shard_plan_sha256"] = sha((output / "shard-plan.json").read_bytes())
        selected_modules = partitions[shard]
        modules = [Path(x).stem for x in selected_modules]
        record["expected_test_modules"] = modules
        record["full_suite_module_count"] = len(all_modules)
        record["selected_module_paths"] = selected_modules
        inspect = """import importlib.metadata as m,json,pathlib,platform,re,sys
from packaging.requirements import Requirement
raw=pathlib.Path(sys.argv[1]).read_text()
packages={};mismatches=[]
for line in raw.splitlines():
 line=line.strip()
 if not line or line.startswith(('#','--')): continue
 text=line.split('--hash=',1)[0].strip().rstrip(chr(92)).strip()
 requirement=Requirement(text)
 if requirement.marker is not None and not requirement.marker.evaluate(): continue
 try: actual=m.version(requirement.name)
 except m.PackageNotFoundError: actual=None
 packages[requirement.name]=actual
 if actual is None or not requirement.specifier.contains(actual): mismatches.append({'requirement':str(requirement),'actual':actual})
print(json.dumps({'python':sys.version,'executable':sys.executable,'platform':platform.platform(),'test_dependency_versions':packages,'test_lock_mismatches':mismatches}))
"""
        row, raw = invoke(output, record["commands"], "python_environment", [python, "-B", "-c", inspect,
                          str(checkout / "ci/release-test-requirements.txt")], temporary, git_env)
        require(row["returncode"] == 0, "required regression dependencies unavailable")
        record["environment"] = json.loads(raw)
        require(not record["environment"]["test_lock_mismatches"], "existing test environment differs from exact CI test dependency versions")
        test_env = dict(git_env, PYTHONPATH=str(checkout), ZERORUN_TRUST_ROOT=str(temporary / "unit-fixture-authority"))
        plugin = temporary / "shard_node_recorder.py"
        plugin.write_text(NODE_PLUGIN.replace("__OUTPUT_JSON__", repr(str(output))), encoding="utf8")
        record["collection_plugin"] = {"sha256": sha(plugin.read_bytes()), "bytes": plugin.stat().st_size}
        launcher = "import importlib.util,pathlib,sys;root=pathlib.Path(sys.argv[1]).resolve();sys.path.insert(0,str(root));import zerorun,pytest;assert pathlib.Path(zerorun.__file__).resolve()==root/'zerorun/__init__.py';spec=importlib.util.spec_from_file_location('shard_node_recorder',sys.argv[2]);plugin=importlib.util.module_from_spec(spec);spec.loader.exec_module(plugin);raise SystemExit(pytest.main(sys.argv[3:],plugins=[plugin]))"
        args = [python, "-B", "-c", launcher, str(checkout), str(plugin), *selected_modules,
                "-q", "-p", "no:cacheprovider", "--basetemp=" + str(temporary / "pytest"),
                "--junitxml=" + str(output / "tests.xml")]
        row, raw = invoke(output, record["commands"], "shard_pytest", args, checkout, test_env, timeout=2100)
        if (output / "collected-nodes.jsonl").is_file():
            nodes = [json.loads(line) for line in (output / "collected-nodes.jsonl").read_text().splitlines()]
            record["collected_node_count"] = len(nodes)
            record["collected_node_sha256"] = sha((output / "collected-nodes.jsonl").read_bytes())
            require(nodes and len({x["nodeid"] for x in nodes}) == len(nodes), "empty or duplicate collected nodes")
            require({x["module"] for x in nodes} == set(selected_modules), "collected nodes do not exactly cover the selected modules")
        else:
            raise ValueError("collected-node inventory missing")
        collection = json.loads((output / "collection-summary.json").read_bytes())
        record["collection_summary"] = collection
        require(collection["deselected_count"] == 0 and not collection["keyword"] and not collection["markexpr"],
                "one or more tests excluded within selected modules")
        require(collection["config_args"] == selected_modules and collection["collected_count"] == len(nodes),
                "collection selection differs from exhaustive shard plan")
        require(row["returncode"] == 0 and row["error"] is None, "selected full-product regression shard failed")
        record["junit_counts"] = junit_summary((output / "tests.xml").read_bytes(), raw, modules)
    except Exception as exc:
        record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if before is not None:
            try:
                after = inventory(checkout, git_env)
                save(output / "source-after.json", after)
                record["tracked_source_unchanged"] = before == after
                require(record["tracked_source_unchanged"], "tracked source changed during full regression")
            except Exception as exc:
                record["failure"] = record["failure"] or {"type": type(exc).__name__, "message": str(exc)}
        record["passed"] = record["failure"] is None and record.get("tracked_source_unchanged") is True
        record["completed_utc"] = utc()
        record["output_files"] = [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
                                  for p in sorted(output.iterdir()) if p.is_file()]
        save(output / "receipt.json", record)
        print(json.dumps({k: record.get(k) for k in ("passed", "failure", "commit", "junit_counts")}), flush=True)
    return int(not record["passed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--python", default=PYTHON)
    parser.add_argument("--git-source", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--shard", required=True, choices=("confirmatory", "other90"))
    args = parser.parse_args()
    raise SystemExit(run(args.output, args.python, args.git_source, args.expected_commit, args.shard))
