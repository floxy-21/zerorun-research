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


@pytest.mark.parametrize("mode", ["exact", "changed-preserved", "missing-preserved", "unexpected-test"])
def test_historical_receipt_binds_only_the_exact_preserved_named_test(install_fixture, mode):
    context = install_fixture
    release = context["release"]
    prefix = readiness.HISTORICAL_RUNTIME_PREFIX
    relative = "tests/test_codex_integration.py" if mode != "unexpected-test" else "tests/test_unrelated.py"
    old = b"# artificial historical integration test\n"
    current = b"# distinct current test; not certified by historical receipt\n"
    put(release / prefix / "src/zerorun/core.py", (release / "src/zerorun/core.py").read_bytes())
    put(release / relative, current)
    preserved = release / prefix / relative
    if mode != "missing-preserved":
        put(preserved, old if mode != "changed-preserved" else old + b"# altered\n")
    path = context["here"] / "generated/public-release-tests.json"
    receipt = readiness.read(path)
    receipt["tested_code"].append({"path": relative, "bytes": len(old), "sha256": artifacts.digest(old)})
    put_json(path, receipt)
    put_json(release / "research/softwarex/generated/public-release-tests.json", receipt)
    if mode == "exact":
        _, checked = readiness.check_test_and_install_bindings(release, historical_runtime_prefix=prefix)
        assert checked["preserved_historical_tests"] == [{"tested_path": relative,
            "preserved_path": prefix + "/" + relative, "bytes": len(old), "sha256": artifacts.digest(old)}]
        assert checked["targeted_test_amendments"] == []
        assert (release / relative).read_bytes() == current
        with pytest.raises(ValueError):
            readiness.check_test_and_install_bindings(release)
    else:
        with pytest.raises((ValueError, FileNotFoundError)):
            readiness.check_test_and_install_bindings(release, historical_runtime_prefix=prefix)


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


@pytest.fixture
def targeted_amendment(fixture, monkeypatch):
    monkeypatch.setattr(readiness, "ROOT", fixture["root"])
    relative = "research/sqj/strengthening/tests/test_analyze_replication.py"
    old, current = b"# original fixture\n", b"# corrected exact timeout fixture\n"
    junit_path = "research/softwarex/evidence/public-test-amendment-v1.xml"
    junit = b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="fixture"/></testsuite></testsuites>'
    receipt = {"schema": "zerorun.softwarex-public-test-amendment.v1", "completed": True,
               "path": relative, "original_sha256": artifacts.digest(old), "current_sha256": artifacts.digest(current),
               "current_bytes": len(current), "reason": "Exact source timeout fixture correction; runtime unchanged",
               "junit": {"path": junit_path, "sha256": artifacts.digest(junit), "tests": 1, "failures": 0, "errors": 0, "skipped": 0}}
    fixture["public"].update({relative: current, junit_path: junit})
    put(fixture["root"] / junit_path, junit)
    receipt_path = "research/softwarex/evidence/public-test-amendment-v1.json"
    put_json(fixture["root"] / receipt_path, receipt)
    fixture["public"][receipt_path] = (fixture["root"] / receipt_path).read_bytes()
    refresh_release(fixture["release"], fixture["public"])
    return fixture, {"path": relative, "sha256": artifacts.digest(old), "bytes": len(old)}, receipt


def test_exact_named_research_test_amendment_passes(targeted_amendment):
    context, original, expected = targeted_amendment
    assert readiness.check_targeted_test_amendment(context["release"], original) == expected


@pytest.mark.parametrize("mode", ["runtime-path", "other-test", "stale-original", "stale-current", "failed-xml", "wrong-count"])
def test_targeted_amendment_never_waives_other_source_or_failure(targeted_amendment, mode):
    context, original, receipt = targeted_amendment
    if mode == "runtime-path":
        original["path"] = "src/zerorun/core.py"
    elif mode == "other-test":
        original["path"] = "tests/test_hermetic.py"
    elif mode == "stale-original":
        original["sha256"] = "0" * 64
    elif mode == "stale-current":
        put(context["release"] / original["path"], b"Further untested source change")
    else:
        if mode == "failed-xml":
            raw = b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="fixture"><failure/></testcase></testsuite></testsuites>'
            receipt["junit"]["sha256"] = artifacts.digest(raw)
            put(context["root"] / receipt["junit"]["path"], raw)
            put(context["release"] / receipt["junit"]["path"], raw)
        else:
            receipt["junit"]["tests"] = 2
        name = "research/softwarex/evidence/public-test-amendment-v1.json"
        put_json(context["root"] / name, receipt)
        put_json(context["release"] / name, receipt)
    with pytest.raises(ValueError):
        readiness.check_targeted_test_amendment(context["release"], original)
