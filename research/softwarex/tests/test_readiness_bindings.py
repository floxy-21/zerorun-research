"""Offline readiness-binding checks using deliberately artificial fixtures."""
import json

import pytest

from research.softwarex import build_readiness as readiness
from research.softwarex import build_submission_artifacts as artifacts
from research.softwarex.tests.test_submission_artifacts import fixture, put, put_json, refresh_release


@pytest.fixture
def built(fixture, monkeypatch):
    monkeypatch.setattr(readiness, "ROOT", fixture["root"])
    monkeypatch.setattr(readiness, "HERE", fixture["here"])
    receipt = artifacts.build(fixture["release"])
    for relative in ("output/submission/SoftwareX_source.zip", "output/submission/ZeroRun_SoftwareX_reviewer.zip",
                     "research/softwarex/generated/artifact-build.json", "research/softwarex/generated/paper-evidence.json",
                     "research/softwarex/generated/pdf-review.json"):
        fixture["public"][relative] = (fixture["root"] / relative).read_bytes()
    refresh_release(fixture["release"], fixture["public"])
    fixture["artifacts"] = receipt
    return fixture


def test_current_artifacts_and_six_source_bindings_pass(built):
    evidence = readiness.read(built["here"] / "generated/paper-evidence.json")
    receipt, _ = readiness.check_artifacts(built["release"], evidence)
    assert receipt == built["artifacts"]


@pytest.mark.parametrize("mode", ["old-evidence", "old-qa", "old-source", "false-completed",
                                  "false-verified", "outside-path", "old-public-source", "old-public-archive"])
def test_stale_or_invalid_artifacts_cannot_become_ready(built, mode):
    receipt_path = built["here"] / "generated/artifact-build.json"
    if mode == "old-evidence":
        path = built["here"] / "generated/paper-evidence.json"
        value = readiness.read(path)
        value["changed"] = True
        put_json(path, value)
    elif mode == "old-qa":
        path = built["here"] / "generated/pdf-review.json"
        value = readiness.read(path)
        value["changed"] = True
        put_json(path, value)
    elif mode == "old-source":
        put(built["here"] / "paper/main.bbl", b"Changed after PDF review")
    elif mode == "old-public-source":
        put(built["release"] / "research/softwarex/paper/main.bbl", b"Old public bibliography")
    elif mode == "old-public-archive":
        put(built["release"] / "output/submission/SoftwareX_source.zip", b"Old public archive")
    else:
        value = readiness.read(receipt_path)
        if mode == "false-completed":
            value["completed"] = False
        elif mode == "false-verified":
            value["source_archive"]["verified"] = False
        else:
            value["source_archive"]["path"] = "../outside.zip"
        put_json(receipt_path, value)
    evidence = readiness.read(built["here"] / "generated/paper-evidence.json")
    with pytest.raises(ValueError):
        readiness.check_artifacts(built["release"], evidence)
    assert not (built["here"] / "generated/final-readiness.json").exists()


@pytest.fixture
def install_fixture(fixture, monkeypatch):
    monkeypatch.setattr(readiness, "ROOT", fixture["root"])
    monkeypatch.setattr(readiness, "HERE", fixture["here"])
    wheel, code = b"Fixture wheel bytes, not installable", b"# fixture code\n"
    junit = b'<testsuites><testsuite tests="4" failures="0" errors="0" skipped="1"/></testsuites>'
    install = {"schema": "zerorun.softwarex-public-install-smoke.v1", "status": "PASS",
               "installed_runtime_files_exact": 36, "wheel_runtime_files_exact": 36,
               "wheel_bytes": len(wheel), "wheel_sha256": artifacts.digest(wheel), "core_commit": "a" * 40}
    tests = {"schema": "zerorun.softwarex-public-release-tests.v1", "status": "PASS_WITH_PLATFORM_SKIPS",
             "core_commit": "a" * 40, "tested_code": [{"path": "src/zerorun/core.py", "bytes": len(code), "sha256": artifacts.digest(code)}],
             "runs": [{"junit": "research/softwarex/evidence/tests.xml", "sha256": artifacts.digest(junit),
                       "junit_cases_including_subtests": 4, "pytest_failed": 0, "errors": 0,
                       "pytest_passed": 3, "pytest_skipped": 1, "subtests_passed": 0}]}
    for name, value in (("public-install-smoke.json", install), ("public-release-tests.json", tests)):
        put_json(fixture["here"] / "generated" / name, value)
        fixture["public"]["research/softwarex/generated/" + name] = (fixture["here"] / "generated" / name).read_bytes()
    fixture["public"].update({"src/zerorun/core.py": code, "research/softwarex/evidence/tests.xml": junit,
                              "output/packages/zerorun-softwarex/zerorun-0.5.1-py3-none-any.whl": wheel})
    refresh_release(fixture["release"], fixture["public"])
    return fixture


def test_current_wheel_source_and_junit_bindings_pass(install_fixture):
    install, tests = readiness.check_test_and_install_bindings(install_fixture["release"])
    assert install["status"] == "PASS" and tests["pytest_passed"] == 3


@pytest.mark.parametrize("relative", ["src/zerorun/core.py", "research/softwarex/evidence/tests.xml",
                                       "output/packages/zerorun-softwarex/zerorun-0.5.1-py3-none-any.whl"])
def test_changed_tested_payload_cannot_become_ready(install_fixture, relative):
    put(install_fixture["release"] / relative, b"Changed after recorded testing")
    with pytest.raises(ValueError):
        readiness.check_test_and_install_bindings(install_fixture["release"])


def test_junit_count_disagreement_is_not_a_passing_receipt(install_fixture):
    path = install_fixture["here"] / "generated/public-release-tests.json"
    value = readiness.read(path)
    value["runs"][0]["pytest_passed"] = 30
    put_json(path, value)
    with pytest.raises(ValueError, match="JUnit/receipt counts"):
        readiness.check_test_and_install_bindings(install_fixture["release"])
