"""Receipt runner failure preservation, without launching child tests."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from research.softwarex import run_publication_tests as runner
from research.softwarex import build_public_release as release


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "research/softwarex/evidence").mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setattr(runner, "inventory", lambda *_: [{"stable": True}])
    monkeypatch.setattr(runner.tempfile, "mkdtemp", lambda **_: str(external))
    return root / "research/softwarex/evidence/new", external


@pytest.mark.parametrize("kind", ["pass", "failed", "timeout", "missing", "malformed", "counts"])
def test_attempt_always_preserved(prepared, monkeypatch, kind):
    output, _ = prepared
    def invoke(command, **kwargs):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(command, 600, output=b"partial output")
        if kind != "missing":
            content = ('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
                       '<testcase name="one"/></testsuite></testsuites>')
            if kind == "malformed":
                content = "<broken"
            if kind == "counts":
                content = content.replace('tests="1"', 'tests="2"')
            (output / "tests.xml").write_text(content)
        return SimpleNamespace(returncode=1 if kind == "failed" else 0, stdout=b"1 passed in 0.01s\n")
    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run(output) == (0 if kind == "pass" else 1)
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["passed"] is (kind == "pass")
    assert (output / "pytest.log").read_bytes() == (b"partial output" if kind == "timeout" else b"1 passed in 0.01s\n")
    with pytest.raises(ValueError, match="new immediate"):
        runner.run(output)


def test_git_ancestor_stops_before_test_execution(prepared, monkeypatch):
    output, external = prepared
    (external / ".git").mkdir()
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run"))
    assert runner.run(output) == 1
    assert "Git-checkout" in json.loads((output / "receipt.json").read_text())["failure"]["message"]


def test_all_new_nested_test_modules_selected_and_source_bound():
    paths = runner.test_paths(runner.ROOT)
    bound = {row["path"] for row in runner.inventory(runner.ROOT)}
    for relative in runner.NESTED_TEST_MODULES:
        path = "research/softwarex/" + relative
        assert path in paths
        assert path in bound
    assert "FIVE_HOUR_FINALIZATION_PLAN.md" in release.PAPER_OPTIONAL_FILES
    assert len(paths) == len(set(paths)) == 1 + len(runner.NESTED_TEST_MODULES)


def test_omission_from_public_allowlist_refused(monkeypatch):
    missing = "handoff_image_v2/test_image.py"
    monkeypatch.setattr(release, "PAPER_OPTIONAL_FILES", tuple(p for p in release.PAPER_OPTIONAL_FILES if p != missing))
    with pytest.raises(ValueError, match="allowlist"):
        runner.test_paths(runner.ROOT)


def test_omission_from_invocation_refused(monkeypatch):
    monkeypatch.setattr(runner, "NESTED_TEST_MODULES", tuple(p for p in runner.NESTED_TEST_MODULES if p != "handoff_v1/test_run.py"))
    with pytest.raises(ValueError, match="invocation"):
        runner.test_paths(runner.ROOT)


def test_selectively_missing_test_file_refused(tmp_path):
    folder = tmp_path / "research/softwarex/handoff_v1"
    folder.mkdir(parents=True)
    (folder / "test_run.py").write_bytes(b"# offline fixture")
    with pytest.raises(ValueError, match="added/omitted"):
        runner.test_paths(tmp_path)


def test_unlisted_nested_test_refused(tmp_path):
    folder = tmp_path / "research/softwarex/agent_handoff_v1"
    folder.mkdir(parents=True)
    (folder / "test_agent.py").write_bytes(b"# offline fixture")
    (folder / "test_unlisted.py").write_bytes(b"# offline fixture")
    with pytest.raises(ValueError, match="added/omitted"):
        runner.test_paths(tmp_path)


def test_new_study_sources_explicitly_allowlisted():
    folders = ("handoff_acquisition_v1", "handoff_acquisition_recovery_v1", "handoff_v1", "agent_handoff_v1", "handoff_image_v2", "agent_handoff_evaluation_v1", "agent_application_053")
    for name in folders:
        for source in (runner.ROOT / "research/softwarex" / name).iterdir():
            if source.is_file() and source.suffix in {".py", ".md"}:
                assert name + "/" + source.name in release.PAPER_OPTIONAL_FILES


def test_current_application_selection_is_exact_and_offline():
    selected = {path for path in runner.NESTED_TEST_MODULES if path.startswith("agent_application_053/")}
    assert selected == {
        "agent_application_053/test_consumer.py", "agent_application_053/test_oracles.py",
        "agent_application_053/test_oracles_v2.py", "agent_application_053/test_seed.py",
        "agent_application_053/test_prepare_consumers.py", "agent_application_053/test_consumer_campaign.py",
        "agent_application_053/test_consumer_v2.py", "agent_application_053/test_consumer_continuation_v2.py",
        "agent_application_053/test_consumer_event_audit_v1.py",
    }
    assert not any(Path(path).name in {"consumer.py", "producer_campaign.py", "oracles.py", "oracles_v2.py"}
                   for path in runner.NESTED_TEST_MODULES)


@pytest.mark.parametrize("mutation", ["missing-known", "unlisted-added"])
def test_current_application_cannot_silently_drop_or_add_nested_tests(tmp_path, mutation):
    folder = tmp_path / "research/softwarex/agent_application_053"
    folder.mkdir(parents=True)
    for name in ("test_consumer.py", "test_oracles.py", "test_oracles_v2.py", "test_seed.py",
                 "test_prepare_consumers.py", "test_consumer_campaign.py", "test_consumer_v2.py",
                 "test_consumer_continuation_v2.py", "test_consumer_event_audit_v1.py"):
        if mutation == "missing-known" and name == "test_oracles_v2.py":
            continue
        (folder / name).write_bytes(b"# artificial offline test inventory")
    if mutation == "unlisted-added":
        (folder / "test_unreviewed.py").write_bytes(b"# never implicitly selected")
    with pytest.raises(ValueError, match="added/omitted"):
        runner.test_paths(tmp_path)


def test_selection_failure_preserved_before_any_subprocess(prepared, monkeypatch):
    output, _ = prepared
    def fail(_):
        raise ValueError("offline test selection rejected")
    monkeypatch.setattr(runner, "test_paths", fail)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **kw: pytest.fail("must not invoke pytest"))
    assert runner.run(output) == 1
    receipt = json.loads((output / "receipt.json").read_text())
    assert "selection rejected" in receipt["failure"]["message"]
    assert receipt["returncode"] is None


def test_selected_nested_modules_reach_actual_pytest_command(prepared, monkeypatch):
    output, _ = prepared
    selected = ["research/softwarex/tests", *("research/softwarex/" + p for p in runner.NESTED_TEST_MODULES)]
    monkeypatch.setattr(runner, "test_paths", lambda root: selected)
    def invoke(command, **kwargs):
        assert command[4:4 + len(selected)] == selected
        assert command[3] == "pytest"
        (output / "tests.xml").write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase name="one"/></testsuite></testsuites>')
        return SimpleNamespace(returncode=0, stdout=b"1 passed in 0.01s\n")
    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run(output) == 0
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["command"][4:4 + len(selected)] == selected


@pytest.mark.parametrize("represented", [False, True])
def test_passing_subtests_require_exact_retained_terminal_accounting(represented):
    cases = b'<testcase name="primary"/>'
    if represented:
        cases += b'<testcase name="sub-one"/><testcase name="sub-two"/>'
    xml = b'<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0">' + cases + b'</testsuite></testsuites>'
    value = runner.junit_accounting(xml, b'1 passed, 2 subtests passed in 0.02s\n')
    assert value["primary_passed"] == 1 and value["reported_passing_subtests"] == 2
    assert value["passing_subtests_without_testcase"] == (0 if represented else 2)


@pytest.mark.parametrize("mode", ["wrong-total", "wrong-primary", "duplicate-summary", "missing-summary",
                                 "failed-subtest", "failure-element", "unexplained-missing-element"])
def test_subtest_accounting_refuses_false_or_missing_success(mode):
    xml = b'<testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"><testcase name="primary"/></testsuite></testsuites>'
    summary = b'1 passed, 2 subtests passed in 0.02s\n'
    if mode == "wrong-total":
        summary = b'1 passed, 1 subtests passed in 0.02s\n'
    elif mode == "wrong-primary":
        summary = b'2 passed, 1 subtests passed in 0.02s\n'
    elif mode == "duplicate-summary":
        summary = b'1 passed, 2 subtests passed, 2 subtests passed in 0.02s\n'
    elif mode == "missing-summary":
        summary = b'partial run output'
    elif mode == "failed-subtest":
        summary = b'1 passed, 2 subtests failed in 0.02s\n'
    elif mode == "failure-element":
        xml = xml.replace(b'<testcase name="primary"/>', b'<testcase name="primary"><failure/></testcase>')
    else:
        xml = xml.replace(b'<testcase name="primary"/>', b'')
    with pytest.raises(ValueError):
        runner.junit_accounting(xml, summary)
