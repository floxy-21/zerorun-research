"""Run direct publication tests with exact source bindings and retained output.

This is a research-artifact check, not a runtime, model or performance benchmark.
The destination must be new; failed attempts are never overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
NESTED_TEST_MODULES = (
    "live_client_v2/test_validation.py", "live_client_v3/test_validation.py",
    "guided_client_v1/test_validation.py", "handoff_acquisition_v1/test_collect.py",
    "handoff_acquisition_recovery_v1/test_recover.py", "handoff_v1/test_run.py",
    "handoff_v1/test_validate.py", "agent_handoff_v1/test_agent.py", "handoff_image_v2/test_image.py",
    "agent_handoff_evaluation_v1/test_evaluation.py",
)


def test_paths(root):
    """Fail closed on selectively omitted nested tests; never execute studies."""
    from research.softwarex import build_public_release as release
    base = root / "research/softwarex"
    expected = set(NESTED_TEST_MODULES)
    if len(expected) != len(NESTED_TEST_MODULES) or not expected.issubset(set(release.PAPER_OPTIONAL_FILES)):
        raise ValueError("nested offline test module missing from explicit public allowlist")
    declared = {p for p in release.PAPER_OPTIONAL_FILES if "/" in p and p.endswith(".py")
                and Path(p).name.startswith("test_")}
    if declared != expected:
        raise ValueError("explicit publication test invocation omits a public nested test module")
    selected = []
    for directory in sorted({str(Path(p).parent).replace("\\", "/") for p in expected}):
        folder = base / directory
        if not folder.exists():
            # Older artifact layouts without that optional study remain usable.
            continue
        actual = {p.relative_to(base).as_posix() for p in folder.rglob("test_*.py") if "__pycache__" not in p.parts}
        wanted = {p for p in expected if str(Path(p).parent).replace("\\", "/") == directory}
        if actual != wanted:
            raise ValueError("nested offline tests added/omitted without explicit publication selection: " + directory)
        selected.extend("research/softwarex/" + p for p in sorted(wanted))
    return ["research/softwarex/tests", *selected]


def inventory(root):
    from research.softwarex import build_public_release as release
    paths = {root / "research/softwarex/build_public_release.py"}
    for prefix, names in (("research/sqj", release.SQJ_FILES),
                          ("research/sqj/strengthening", release.STRENGTHENING_FILES + release.OPTIONAL_STRENGTHENING),
                          ("research/softwarex", release.PAPER_OPTIONAL_FILES)):
        paths.update(root / prefix / name for name in names if name.endswith(".py") and (root / prefix / name).is_file())
    paths.update(root.glob("research/softwarex/tests/*.py"))
    paths = sorted(paths, key=lambda p: p.relative_to(root).as_posix())
    return [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths
            if "__pycache__" not in p.parts]


def junit_accounting(raw, stdout):
    """Require XML and the retained terminal summary to explain every passing subtest."""
    xml = ET.fromstring(raw)
    suites = [xml] if xml.tag == "testsuite" else xml.findall(".//testsuite")
    if not suites:
        raise ValueError("JUnit suites missing")
    counts = {key: sum(int(s.attrib[key]) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    cases = len(xml.findall(".//testcase"))
    if (any(value < 0 for value in counts.values()) or counts["tests"] <= counts["skipped"]
            or counts["failures"] or counts["errors"] or xml.findall(".//failure") or xml.findall(".//error")
            or len(xml.findall(".//skipped")) != counts["skipped"]):
        raise ValueError("JUnit contains failing or incomplete outcomes")
    lines = [line.strip().strip("=").strip() for line in stdout.decode("utf-8").splitlines() if line.strip()]
    summary = re.fullmatch(r"(.+) in [0-9]+(?:\.[0-9]+)?s(?: \([0-9:]+\))?", lines[-1] if lines else "")
    if summary is None:
        raise ValueError("retained pytest terminal summary missing")
    fields = {}
    for field in summary.group(1).split(", "):
        match = re.fullmatch(r"([0-9]+) (passed|skipped|subtests passed|warnings?)", field)
        if match is None:
            raise ValueError("pytest terminal summary contains unsupported or failing outcomes")
        label = "warnings" if match.group(2) == "warning" else match.group(2)
        if label in fields:
            raise ValueError("duplicate pytest terminal summary count")
        fields[label] = int(match.group(1))
    passed, skipped, subtests = (fields.get(name, 0) for name in ("passed", "skipped", "subtests passed"))
    if (passed <= 0 or skipped != counts["skipped"] or passed + skipped + subtests != counts["tests"]
            or cases not in {passed + skipped, counts["tests"]}):
        raise ValueError("JUnit and terminal test/subtest counts differ")
    return {"counts": counts, "testcase_elements": cases, "primary_passed": passed,
            "reported_passing_subtests": subtests, "passing_subtests_without_testcase": counts["tests"] - cases}


def run(output):
    output = output.absolute()
    allowed = (ROOT / "research/softwarex/evidence").resolve()
    if output.parent.resolve() != allowed or output.exists() or output.is_symlink():
        raise ValueError("a new immediate publication-evidence directory is required")
    output.mkdir()
    before = inventory(ROOT)
    temporary = Path(tempfile.mkdtemp(prefix="zerorun-publication-test-"))
    command = [sys.executable, "-B", "-m", "pytest",
               "-q", "--import-mode=importlib", "-p", "no:cacheprovider", "--basetemp=" + str(temporary / "cases"),
               "--junitxml=" + str(output / "tests.xml")]
    started = datetime.now(timezone.utc).isoformat()
    failure = None
    returncode = None
    raw = b""
    counts = None
    testcases = None
    accounting = None
    try:
        selected = test_paths(ROOT)
        command[4:4] = selected
        if any((parent / ".git").exists() for parent in (temporary.resolve(), *temporary.resolve().parents)):
            raise ValueError("test temporary directory has a Git-checkout ancestor")
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=600)
        returncode, raw = result.returncode, result.stdout
        accounting = junit_accounting((output / "tests.xml").read_bytes(), raw)
        counts, testcases = accounting["counts"], accounting["testcase_elements"]
    except Exception as error:
        if isinstance(error, subprocess.TimeoutExpired):
            raw = error.output or b""
        failure = {"type": type(error).__name__, "message": str(error)}
    (output / "pytest.log").write_bytes(raw)
    after = inventory(ROOT)
    files = {name: {"bytes": (output / name).stat().st_size,
                   "sha256": hashlib.sha256((output / name).read_bytes()).hexdigest()}
             for name in ("tests.xml", "pytest.log") if (output / name).is_file()}
    receipt = {"schema": "zerorun.softwarex-publication-tests.v1", "started_utc": started,
               "finished_utc": datetime.now(timezone.utc).isoformat(), "command": command,
               "returncode": returncode, "junit_counts": counts, "failure": failure,
               "junit_accounting": accounting,
               "python": sys.version, "platform": platform.platform(), "testcases_present": testcases,
               "source_before": before, "source_after": after, "source_unchanged": before == after,
               "output_files": files, "temporary_directory_retained": str(temporary),
               "passed": failure is None and returncode == 0 and counts is not None and counts["failures"] == counts["errors"] == 0 and before == after,
               "scope": "direct publication-extension tests only; no live model or runtime speed claims"}
    (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n",
                                         encoding="utf-8", newline="\n")
    print(json.dumps({key: receipt[key] for key in ("passed", "returncode", "junit_counts", "source_unchanged")}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(run(parser.parse_args().output))
