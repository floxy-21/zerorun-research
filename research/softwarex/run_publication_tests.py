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
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def inventory(root):
    from research.softwarex import build_public_release as release
    paths = {root / "research/softwarex/build_public_release.py"}
    for prefix, names in (("research/sqj", release.SQJ_FILES),
                          ("research/sqj/strengthening", release.STRENGTHENING_FILES + release.OPTIONAL_STRENGTHENING),
                          ("research/softwarex", release.PAPER_OPTIONAL_FILES)):
        paths.update(root / prefix / name for name in names if name.endswith(".py") and (root / prefix / name).is_file())
    paths.update(root.glob("research/softwarex/tests/*.py"))
    paths = sorted(paths)
    return [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths
            if "__pycache__" not in p.parts]


def run(output):
    output = output.absolute()
    allowed = (ROOT / "research/softwarex/evidence").resolve()
    if output.parent.resolve() != allowed or output.exists() or output.is_symlink():
        raise ValueError("a new immediate publication-evidence directory is required")
    output.mkdir()
    before = inventory(ROOT)
    temporary = Path(tempfile.mkdtemp(prefix="zerorun-publication-test-"))
    command = [sys.executable, "-B", "-m", "pytest", "research/softwarex/tests",
               "-q", "-p", "no:cacheprovider", "--basetemp=" + str(temporary / "cases"),
               "--junitxml=" + str(output / "tests.xml")]
    started = datetime.now(timezone.utc).isoformat()
    failure = None
    returncode = None
    raw = b""
    counts = None
    testcases = None
    try:
        if any((parent / ".git").exists() for parent in (temporary.resolve(), *temporary.resolve().parents)):
            raise ValueError("test temporary directory has a Git-checkout ancestor")
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=600)
        returncode, raw = result.returncode, result.stdout
        xml = ET.parse(output / "tests.xml").getroot()
        suites = [xml] if xml.tag == "testsuite" else xml.findall(".//testsuite")
        if not suites:
            raise ValueError("JUnit suites missing")
        counts = {key: sum(int(s.attrib[key]) for s in suites)
                  for key in ("tests", "failures", "errors", "skipped")}
        testcases = len(xml.findall(".//testcase"))
        if testcases != counts["tests"] or any(value < 0 for value in counts.values()):
            raise ValueError("JUnit count/structure disagreement")
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
