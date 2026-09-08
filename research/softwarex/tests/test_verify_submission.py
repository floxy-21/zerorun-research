"""Offline verifier tests with tiny invented receipts, never a real study."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from research.softwarex import verify_submission as verifier
from research.softwarex import analysis_reproduction as analysis
from research.softwarex import build_submission_artifacts as artifacts


def save(root, path, value):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(json.dumps(value, sort_keys=True).encode())
    return target


@pytest.fixture
def artifact_fixture(tmp_path, monkeypatch):
    prefix = "research/softwarex/"
    sources = {}
    for name in artifacts.SOURCE_FILES:
        target = tmp_path / prefix / "paper" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(("fixture-" + name).encode())
        sources[name] = artifacts.digest(target.read_bytes())
    builder = tmp_path / prefix / "build_submission_artifacts.py"
    builder.write_bytes(b"fixture builder")
    pdf = tmp_path / "output/pdf/zerorun-softwarex.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"tiny non-PDF test fixture, never claimed rendered")
    evidence = save(tmp_path, prefix + "generated/paper-evidence.json", {"main_tex_sha256": sources["main.tex"], "bibliography_sha256": sources["references.bib"]})
    qa = save(tmp_path, prefix + "generated/pdf-review.json", {"pdf_sha256": artifacts.digest(pdf.read_bytes()),
        "all_pages_visually_reviewed": True, "word_limit_pass": True, "unresolved_references": False, "compilation_source_sha256": sources})
    archives = {name: {"path": relative, "sha256": "a" * 64, "bytes": 1, "members": 7, "verified": True}
        for name, relative in (("source_archive", "output/submission/SoftwareX_source.zip"),
                               ("reviewer_archive", "output/submission/ZeroRun_SoftwareX_reviewer.zip"))}
    lookup = {r["path"]: copy.deepcopy(r) for r in archives.values()}
    monkeypatch.setattr(artifacts, "verify", lambda path: dict(lookup[path.relative_to(tmp_path).as_posix()]))
    record = {"schema": "zerorun.softwarex-artifact-build.v1", "completed": True,
        "builder_sha256": artifacts.digest(builder.read_bytes()), "paper_evidence_sha256": artifacts.digest(evidence.read_bytes()),
        "pdf_review_sha256": artifacts.digest(qa.read_bytes()), "pdf_sha256": artifacts.digest(pdf.read_bytes()),
        "compilation_source_sha256": sources, "journal_submitted": False, "payment_made": False,
        "private_history_included": False, "private_authority_included": False, **archives}
    save(tmp_path, prefix + "generated/artifact-build.json", record)
    return tmp_path, record


def test_artifact_bindings_use_current_receipts(artifact_fixture):
    root, record = artifact_fixture
    result = verifier.artifact_check(root)
    assert result["pdf_sha256"] == record["pdf_sha256"]
    assert set(result["archives"]) == {"source_archive", "reviewer_archive"}


@pytest.mark.parametrize("relative", ["output/pdf/zerorun-softwarex.pdf", "research/softwarex/paper/main.tex",
    "research/softwarex/build_submission_artifacts.py", "research/softwarex/generated/paper-evidence.json"])
def test_changed_current_artifact_refused(artifact_fixture, relative):
    root, _ = artifact_fixture
    with (root / relative).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError):
        verifier.artifact_check(root)


def test_stale_archive_summary_refused(artifact_fixture):
    root, record = artifact_fixture
    record["source_archive"]["sha256"] = "f" * 64
    save(root, "research/softwarex/generated/artifact-build.json", record)
    with pytest.raises(ValueError, match="archive"):
        verifier.artifact_check(root)


def prepare_verifier(monkeypatch):
    monkeypatch.setattr(analysis, "require_canonical_python", lambda: {"implementation": "cpython", "major_minor": [3, 12]})
    monkeypatch.setattr(verifier, "manifest_check", lambda root: {"manifest_sha256": "a" * 64})
    monkeypatch.setattr(verifier, "artifact_check", lambda root: {"synthetic": True})


def test_all_fourteen_saved_evidence_routes_are_invoked(tmp_path, monkeypatch):
    prepare_verifier(monkeypatch)
    called = []
    monkeypatch.setattr(verifier, "check_command", lambda root, module, args: called.append((module, args)) or {"returncode": 0})
    result = verifier.verify(tmp_path)
    assert result["passed"] is True
    assert called == [(module, args) for _, module, args in verifier.commands()]
    assert len(called) == 14
    assert all("--check" in args for _, args in called)
    assert not any("build_readiness" in module for module, _ in called)
    assert "research.softwarex.build_handoff_evidence" in {module for module, _ in called}
    assert "research.softwarex.quickstart_053" in {module for module, _ in called}
    assert "research.softwarex.build_agent_application_evidence" in {module for module, _ in called}
    assert "research.softwarex.build_consumer_lizard_audit" in {module for module, _ in called}
    assert "research.softwarex.verify_fresh_public_053" in {module for module, _ in called}


def test_current_routes_require_053_receipts_without_relabeling_052():
    selected = {name: (module, args) for name, module, args in verifier.commands()}
    assert selected["current_runtime"] == (
        "research.softwarex.five_hour_review.current_runtime_053",
        ["--release", ".", "--check", "research/softwarex/evidence/current-runtime-0.5.3-v1/receipt.json"])
    assert selected["current_quickstart"] == (
        "research.softwarex.quickstart_053",
        ["--source-root", ".", "--check", "research/softwarex/evidence/quickstart-public-053-v1/check.json",
         "--installation-receipt", "research/softwarex/evidence/quickstart-public-053-v1/install.json"])
    assert all("--output" not in selected[name][1] and "--create-tools-env" not in selected[name][1]
               for name in ("current_runtime", "current_quickstart"))


@pytest.mark.parametrize("name,legacy", [
    ("current_runtime", "research/softwarex/five_hour_review/current_runtime.py"),
    ("current_quickstart", "research/softwarex/quickstart_052.py"),
])
def test_missing_053_checker_does_not_fall_back_to_historical_helper(tmp_path, monkeypatch, name, legacy):
    old = tmp_path / legacy
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"# retained historical checker fixture")
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **kw: pytest.fail("must not execute a historical fallback"))
    module, args = next((module, args) for label, module, args in verifier.commands() if label == name)
    with pytest.raises(ValueError, match="required offline checker is missing"):
        verifier.check_command(tmp_path, module, args)


def test_current_manifest_requires_external_asset_before_other_checks(tmp_path, monkeypatch):
    from research.softwarex import build_public_release as release
    save(tmp_path, release.MANIFEST, {"current_version": "0.5.3", "files": [{"path": "fixture"}]})
    monkeypatch.setattr(release, "external_artifacts", lambda saved: [])
    monkeypatch.setattr(release, "inspect", lambda *a, **kw: pytest.fail("incomplete current release cannot pass"))
    with pytest.raises(ValueError, match="complete 0.5.3 submission requires"):
        verifier.manifest_check(tmp_path)


def test_one_failed_checker_is_nonpassing_but_other_checks_retained(tmp_path, monkeypatch):
    prepare_verifier(monkeypatch)
    def check(root, module, args):
        if module.endswith("build_handoff_evidence"):
            raise FileNotFoundError("missing final handoff evidence")
        return {"returncode": 0}
    monkeypatch.setattr(verifier, "check_command", check)
    result = verifier.verify(tmp_path)
    assert result["passed"] is False
    assert len(result["checks"]) == 18
    assert next(r for r in result["checks"] if r["check"] == "handoff")["error"]["type"] == "FileNotFoundError"


def test_manifest_drift_between_checks_fails(tmp_path, monkeypatch):
    prepare_verifier(monkeypatch)
    states = iter([{"manifest_sha256": "before"}, {"manifest_sha256": "after"}])
    monkeypatch.setattr(verifier, "manifest_check", lambda root: next(states))
    monkeypatch.setattr(verifier, "check_command", lambda *a: {})
    result = verifier.verify(tmp_path)
    assert result["passed"] is False
    assert "changed between" in result["checks"][-1]["error"]["message"]


def test_unsupported_python_stops_before_any_file_or_subprocess_check(tmp_path, monkeypatch):
    monkeypatch.setattr(analysis, "_runtime_identity", lambda: ("cpython", (3, 10)))
    monkeypatch.setattr(verifier, "manifest_check", lambda root: pytest.fail("must not inspect under unsupported analysis Python"))
    result = verifier.verify(tmp_path)
    assert result["passed"] is False
    assert len(result["checks"]) == 1
    assert "3.12--3.14" in result["checks"][0]["error"]["message"]


def test_command_is_exact_no_install_no_shell_no_local_bytecode(tmp_path, monkeypatch):
    module, args = "research.softwarex.build_paper", ["--check"]
    path = tmp_path / "research/softwarex/build_paper.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# fixture only")
    def invoke(argv, **kw):
        assert argv == [verifier.sys.executable, "-B", "-s", "-m", module, "--check"]
        assert kw["cwd"] == tmp_path and "shell" not in kw
        assert kw["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        assert kw["timeout"] == 120
        return SimpleNamespace(returncode=0, stdout=b"checked", stderr=b"")
    monkeypatch.setattr(verifier.subprocess, "run", invoke)
    assert verifier.check_command(tmp_path, module, args)["returncode"] == 0


def test_missing_checker_or_noncheck_mode_never_executes(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier.subprocess, "run", lambda *a, **kw: pytest.fail("must not execute"))
    with pytest.raises(ValueError, match="missing"):
        verifier.check_command(tmp_path, "research.softwarex.build_paper", ["--check"])
    with pytest.raises(ValueError, match="read-only"):
        verifier.check_command(tmp_path, "research.softwarex.build_paper", [])


@pytest.mark.parametrize("kind", ["failure", "timeout"])
def test_checker_failure_and_timeout_cannot_pass(tmp_path, monkeypatch, kind):
    path = tmp_path / "research/softwarex/build_paper.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# fixture only")
    def invoke(argv, **kw):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, 120)
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"stale input")
    monkeypatch.setattr(verifier.subprocess, "run", invoke)
    with pytest.raises(ValueError, match="failed|exceeded"):
        verifier.check_command(tmp_path, "research.softwarex.build_paper", ["--check"])


def test_no_passing_exit_for_failed_report(monkeypatch, capsys):
    monkeypatch.setattr(verifier, "verify", lambda: {"passed": False})
    assert verifier.main() == 1
    assert json.loads(capsys.readouterr().out) == {"passed": False}
