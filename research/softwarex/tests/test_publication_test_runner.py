"""Receipt runner failure preservation, without launching child tests."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from research.softwarex import run_publication_tests as runner


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
        return SimpleNamespace(returncode=1 if kind == "failed" else 0, stdout=b"recorded output")
    monkeypatch.setattr(runner.subprocess, "run", invoke)
    assert runner.run(output) == (0 if kind == "pass" else 1)
    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["passed"] is (kind == "pass")
    assert (output / "pytest.log").read_bytes() == (b"partial output" if kind == "timeout" else b"recorded output")
    with pytest.raises(ValueError, match="new immediate"):
        runner.run(output)


def test_git_ancestor_stops_before_test_execution(prepared, monkeypatch):
    output, external = prepared
    (external / ".git").mkdir()
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not run"))
    assert runner.run(output) == 1
    assert "Git-checkout" in json.loads((output / "receipt.json").read_text())["failure"]["message"]
