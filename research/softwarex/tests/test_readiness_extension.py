"""Artificial publication-test receipts; no model or subprocess execution."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from research.softwarex import build_readiness as readiness


def put(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def binding(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def hosted_ci(tmp_path, monkeypatch):
    root, release = tmp_path / "source", tmp_path / "public"
    monkeypatch.setattr(readiness, "ROOT", root)
    receipt = {"schema": "zerorun.hosted-ci-status.v1",
        "repository": "https://github.com/floxy-21/zerorun-mvp", "commit": "a" * 40,
        "status": "NOT_STARTED_ACCOUNT_BILLING_OR_SPENDING_LIMIT", "run_id": 123,
        "run_url": "https://github.com/floxy-21/zerorun-mvp/actions/runs/123",
        "observed_utc": "2026-09-07T05:42:00+00:00", "observation": "Artificial nonexecution observation.",
        **{key: False for key in ("test_execution_observed", "test_failure_observed", "hosted_ci_green",
                                 "account_settings_changed", "payment_made")}}
    for directory in (root, release):
        put(directory / readiness.HOSTED_CI_RECEIPT, json.dumps(receipt).encode())
    return root, release, receipt


def test_hosted_ci_administrative_block_is_not_passing_test_evidence(hosted_ci):
    _, release, _ = hosted_ci
    result = readiness.check_hosted_ci_observation(release)
    assert result["used_as_passing_test_evidence"] is False
    assert result["observation"]["hosted_ci_green"] is False


@pytest.mark.parametrize("mode", ["execution-claim", "green-claim", "different-run", "public-drift"])
def test_hosted_ci_observation_cannot_become_a_green_claim(hosted_ci, mode):
    root, release, receipt = hosted_ci
    if mode == "execution-claim":
        receipt["test_execution_observed"] = True
    elif mode == "green-claim":
        receipt["hosted_ci_green"] = True
    elif mode == "different-run":
        receipt["run_url"] += "9"
    else:
        receipt["observation"] = "Changed local observation."
    raw = json.dumps(receipt).encode()
    put(root / readiness.HOSTED_CI_RECEIPT, raw)
    if mode != "public-drift":
        put(release / readiness.HOSTED_CI_RECEIPT, raw)
    with pytest.raises(ValueError):
        readiness.check_hosted_ci_observation(release)


@pytest.fixture
def current_quickstart(tmp_path, monkeypatch):
    from research.softwarex import quickstart_052
    root, release = tmp_path / "source", tmp_path / "public"
    monkeypatch.setattr(readiness, "ROOT", root)
    helper = "research/softwarex/quickstart_052.py"
    for directory in (root, release):
        put(directory / helper, Path(quickstart_052.__file__).read_bytes())
        put(directory / "research/softwarex/QUICKSTART_052.md", b"Artificial documented procedure.")
        put(directory / "research/softwarex/diagnose_mcp_authority_052.py", b"# Artificial diagnostic binding.")
        for name in ("install.json", "check.json"):
            put(directory / readiness.CURRENT_QUICKSTART_DIR / name, b"{\"artificial\": true}")
    outcomes = {"install": {"passed": True, "source_commit": "a" * 40},
                "check": {"passed": True, "source_commit": "a" * 40}}
    monkeypatch.setattr(quickstart_052, "validate_receipt_pair", lambda *args: {
        "installation": deepcopy(outcomes["install"]), "check": deepcopy(outcomes["check"]),
        "passed": outcomes["install"]["passed"] and outcomes["check"]["passed"],
        "read_only": True, "model_called": False, "real_repository_authorized": False})
    return root, release, outcomes


def test_current_quickstart_has_separate_version_and_public_bindings(current_quickstart):
    _, release, _ = current_quickstart
    value = readiness.check_current_quickstart(release)
    assert value["version"] == "0.5.2"
    assert len(value["public_files_sha256"]) == 5


@pytest.mark.parametrize("mode", ["failed-install", "failed-workflow", "different-commit", "public-guide-drift"])
def test_current_quickstart_refuses_stale_or_partial_current_evidence(current_quickstart, mode):
    _, release, outcomes = current_quickstart
    if mode == "failed-install":
        outcomes["install"]["passed"] = False
    elif mode == "failed-workflow":
        outcomes["check"]["passed"] = False
    elif mode == "different-commit":
        outcomes["check"]["source_commit"] = "b" * 40
    else:
        (release / "research/softwarex/QUICKSTART_052.md").write_bytes(b"Changed public guide.")
    with pytest.raises(ValueError):
        readiness.check_current_quickstart(release)


def save_receipt(context, *, public=True):
    raw = (json.dumps(context["receipt"], sort_keys=True) + "\n").encode()
    relative = readiness.EXTENSION_TESTS + "/receipt.json"
    put(context["root"] / relative, raw)
    if public:
        put(context["release"] / relative, raw)


@pytest.fixture
def extension(tmp_path, monkeypatch):
    root, release = tmp_path / "source", tmp_path / "public"
    monkeypatch.setattr(readiness, "ROOT", root)
    source = "research/softwarex/tests/test_fixture_only.py"
    code = b"# Artificial source identity, not a real executed test.\n"
    xml = (b'<testsuites><testsuite tests="2" failures="0" errors="0" skipped="0">'
           b'<testcase name="one"/><testcase name="two"/></testsuite></testsuites>')
    outputs = {"tests.xml": xml, "pytest.log": b"Artificial fixture.\n2 passed in 0.01s\n"}
    for directory in (root, release):
        put(directory / source, code)
        for name, raw in outputs.items():
            put(directory / readiness.EXTENSION_TESTS / name, raw)
    rows = [{"path": source, **binding(code)}]
    monkeypatch.setattr(readiness.publication_runner, "inventory", lambda candidate: deepcopy(rows))
    monkeypatch.setattr(readiness.publication_runner, "test_paths", lambda candidate: ["research/softwarex/tests"])
    receipt = {
        "schema": "zerorun.softwarex-publication-tests.v1",
        "passed": True, "returncode": 0, "failure": None,
        "source_unchanged": True, "source_before": rows,
        "source_after": deepcopy(rows),
        "output_files": {name: binding(raw) for name, raw in outputs.items()},
        "junit_counts": {"tests": 2, "failures": 0, "errors": 0, "skipped": 0},
        "junit_accounting": {"counts": {"tests": 2, "failures": 0, "errors": 0, "skipped": 0},
                             "testcase_elements": 2, "primary_passed": 2,
                             "reported_passing_subtests": 0, "passing_subtests_without_testcase": 0},
        "testcases_present": 2, "scope": "Artificial unit fixture only",
        "command": ["python", "-B", "-m", "pytest", "research/softwarex/tests", "-q",
                    "--import-mode=importlib", "-p", "no:cacheprovider", "--basetemp=fixture", "--junitxml=fixture.xml"],
    }
    context = {"root": root, "release": release, "source": source,
               "receipt": receipt, "xml": xml}
    save_receipt(context)
    return context


def test_matching_source_outputs_and_receipt_pass(extension):
    result = readiness.check_extension_tests(extension["release"])
    assert result["receipt"] == readiness.EXTENSION_TESTS + "/receipt.json"
    assert result["counts"] == extension["receipt"]["junit_counts"]
    assert result["source_files_bound"] == 1
    assert result["scope"] == "Artificial unit fixture only"


@pytest.mark.parametrize("mode", [
    "local-source", "public-source", "public-receipt", "failed-receipt",
    "output-bytes", "junit-count", "junit-failure-node", "source-after", "test-omission", "test-filter",
])
def test_drift_or_failure_never_becomes_ready(extension, mode):
    receipt = extension["receipt"]
    if mode in {"local-source", "public-source"}:
        directory = extension["root"] if mode == "local-source" else extension["release"]
        put(directory / extension["source"], b"Changed after the artificial test.\n")
    elif mode == "public-receipt":
        receipt["scope"] = "Changed only in the local receipt"
        save_receipt(extension, public=False)
    elif mode == "output-bytes":
        put(extension["root"] / readiness.EXTENSION_TESTS / "pytest.log", b"Changed log\n")
    elif mode == "junit-failure-node":
        raw = extension["xml"].replace(b'<testcase name="one"/>',
                                       b'<testcase name="one"><failure/></testcase>')
        for directory in (extension["root"], extension["release"]):
            put(directory / readiness.EXTENSION_TESTS / "tests.xml", raw)
        receipt["output_files"]["tests.xml"] = binding(raw)
        save_receipt(extension)
    else:
        if mode == "failed-receipt":
            receipt["failure"] = {"type": "TimeoutExpired", "message": "Fixture timeout"}
        elif mode == "junit-count":
            receipt["junit_counts"]["tests"] = 3
        elif mode == "test-omission":
            receipt["command"][4] = "research/softwarex/tests/one_only.py"
        elif mode == "test-filter":
            receipt["command"] += ["-k", "only_one_case"]
        else:
            receipt["source_after"][0]["sha256"] = "0" * 64
        save_receipt(extension)
    with pytest.raises(ValueError):
        readiness.check_extension_tests(extension["release"])
    assert not (extension["root"] / "research/softwarex/generated/final-readiness.json").exists()


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    root, release = tmp_path / "handoff-source", tmp_path / "handoff-public"
    monkeypatch.setattr(readiness, "ROOT", root)
    relative = readiness.handoff_builder.OUTPUT
    source = "research/softwarex/evidence/agent-producer-pilot-v1/case-00/final-source.tar.gz"
    raw = b"Artificial archive binding; no experiment was executed."
    summary = {"schema": "zerorun.softwarex-handoff-evidence.v1",
               "available_completed_runs_reconciled": True, "acceptance_probability_estimated": False,
               "performance_threshold_imposed": False,
               "agent_evaluation": {"pilot": {"producer_completed": 0}},
               "unavailable_or_incomplete_runs": ["v2_pilot"]}
    rows = [{"path": source, **binding(raw)}]
    calls = []
    def regenerate(candidate):
        calls.append(candidate)
        return deepcopy(summary)
    monkeypatch.setattr(readiness.handoff_builder, "build", regenerate)
    monkeypatch.setattr(readiness.handoff_builder, "source_inputs", lambda candidate: deepcopy(rows))
    for directory in (root, release):
        put(directory / source, raw)
        put(directory / relative, json.dumps(summary).encode())
    return {"root": root, "release": release, "source": source, "relative": relative,
            "summary": summary, "rows": rows, "calls": calls, "paper": {"handoff": deepcopy(summary)}}


def test_handoff_reconciles_private_and_public_inputs_without_requiring_positive_results(handoff):
    result, count = readiness.check_handoff_evidence(handoff["release"], handoff["paper"])
    assert result == handoff["summary"] and count == 1
    assert handoff["calls"] == [handoff["root"], handoff["release"]]
    assert result["unavailable_or_incomplete_runs"] == ["v2_pilot"]


@pytest.mark.parametrize("mode", ["paper", "saved", "public-archive", "missing-archive", "false-scope", "inventory-omission"])
def test_handoff_stale_or_missing_public_evidence_is_not_ready(handoff, mode):
    if mode == "paper":
        handoff["paper"]["handoff"]["unavailable_or_incomplete_runs"] = []
    elif mode == "saved":
        put(handoff["root"] / handoff["relative"], b"{}")
    elif mode == "public-archive":
        put(handoff["release"] / handoff["source"], b"different archive")
    elif mode == "missing-archive":
        (handoff["release"] / handoff["source"]).unlink()
    elif mode == "false-scope":
        handoff["summary"]["acceptance_probability_estimated"] = True
    else:
        handoff["rows"].clear()
    with pytest.raises((ValueError, FileNotFoundError)):
        readiness.check_handoff_evidence(handoff["release"], handoff["paper"])


@pytest.fixture
def application(tmp_path, monkeypatch):
    """Artificial summaries exercise binding logic, not live experimental claims."""
    root, release = tmp_path / "application-source", tmp_path / "application-public"
    monkeypatch.setattr(readiness, "ROOT", root)
    relative = "research/softwarex/generated/application-evidence-v1.json"
    source = "research/softwarex/artificial_application_fixture.py"
    code = b"# Artificial readiness test input, never a model experiment.\n"
    summary = {
        "schema": "zerorun.softwarex-application-evidence.v1", "completed": True,
        "clean_installation": {"validation": {"passed": True}},
        "account_free_quickstart": {"validation": {"passed": True}},
        "public_guide_installation": {"validation": {"passed": True}},
        "public_guide_quickstart": {"validation": {"passed": True}},
        "model_backed_application": {"functional_lifecycle_pass": False, "recorded_outcome": "retained adverse fixture"},
        "model_backed_application_v3": {"functional_lifecycle_pass": False, "recorded_outcome": "retained adverse fixture"},
        "interpretation": {
            "model_and_quickstart_installations_are_distinct": True, "same_frozen_runtime_required": True,
            "independent_human_users": 0, "autonomous_issue_resolution_study": False,
            "workflow_speedup_established": False, "acceptance_probability_estimated": False,
        },
    }
    rows = [{"path": source, **binding(code)}]
    calls = []
    def independently_build(candidate_root):
        calls.append(candidate_root)
        return deepcopy(summary)
    monkeypatch.setattr(readiness.application_builder, "build", independently_build)
    monkeypatch.setattr(readiness.application_builder, "source_inputs", lambda candidate_root: deepcopy(rows), raising=False)
    for directory in (root, release):
        put(directory / source, code)
        put(directory / relative, json.dumps(summary).encode())
    return {"root": root, "release": release, "relative": relative, "source": source,
            "summary": summary, "paper": {"application": deepcopy(summary)}, "rows": rows, "calls": calls}


def test_application_is_recomputed_and_preserves_adverse_model_outcomes(application):
    result, files = readiness.check_application_evidence(application["release"], application["paper"])
    assert application["calls"] == [application["root"]]
    assert result == application["summary"] and files == 1
    assert result["model_backed_application"]["functional_lifecycle_pass"] is False
    assert result["model_backed_application_v3"]["functional_lifecycle_pass"] is False


@pytest.mark.parametrize("mode", [
    "stale-saved-summary", "stale-paper-summary", "local-source", "public-source", "public-summary",
    "duplicate-source", "empty-sources", "outside-source", "wrong-source-size", "boolean-source-size",
    "wrong-source-hash", "incomplete-summary", "source-list-not-rows",
])
def test_application_drift_or_missing_bindings_cannot_become_ready(application, monkeypatch, mode):
    root, release, summary = application["root"], application["release"], application["summary"]
    if mode == "stale-saved-summary":
        changed = dict(summary, changed_after_validation=True)
        put(root / application["relative"], json.dumps(changed).encode())
    elif mode == "stale-paper-summary":
        application["paper"]["application"]["changed_after_validation"] = True
    elif mode in {"local-source", "public-source"}:
        put((root if mode == "local-source" else release) / application["source"], b"Changed input bytes\n")
    elif mode == "public-summary":
        put(release / application["relative"], b"{}\n")
    elif mode == "duplicate-source":
        application["rows"].append(deepcopy(application["rows"][0]))
    elif mode == "empty-sources":
        application["rows"].clear()
    elif mode == "outside-source":
        application["rows"][0]["path"] = "../outside.py"
    elif mode == "wrong-source-size":
        application["rows"][0]["bytes"] += 1
    elif mode == "boolean-source-size":
        application["rows"][0]["bytes"] = True
    elif mode == "wrong-source-hash":
        application["rows"][0]["sha256"] = "0" * 64
    elif mode == "source-list-not-rows":
        monkeypatch.setattr(readiness.application_builder, "source_inputs", lambda root: [None])
    else:
        summary["completed"] = False
        application["paper"] = {"application": deepcopy(summary)}
        for directory in (root, release):
            put(directory / application["relative"], json.dumps(summary).encode())
    with pytest.raises(ValueError):
        readiness.check_application_evidence(release, application["paper"])
    assert not (root / "research/softwarex/generated/final-readiness.json").exists()


@pytest.mark.parametrize("name", ["clean_installation", "account_free_quickstart",
                                  "public_guide_installation", "public_guide_quickstart"])
def test_application_requires_actual_installation_and_quickstart_validation(application, name):
    application["summary"][name]["validation"]["passed"] = False
    application["paper"]["application"] = deepcopy(application["summary"])
    for directory in (application["root"], application["release"]):
        put(directory / application["relative"], json.dumps(application["summary"]).encode())
    with pytest.raises(ValueError, match="passing installation/quickstart"):
        readiness.check_application_evidence(application["release"], application["paper"])


@pytest.mark.parametrize("field,value", [
    ("model_and_quickstart_installations_are_distinct", False),
    ("same_frozen_runtime_required", False), ("independent_human_users", 5),
    ("independent_human_users", False), ("autonomous_issue_resolution_study", True),
    ("workflow_speedup_established", True), ("acceptance_probability_estimated", True),
])
def test_application_scope_cannot_be_promoted_to_unobserved_claims(application, field, value):
    application["summary"]["interpretation"][field] = value
    application["paper"]["application"] = deepcopy(application["summary"])
    for directory in (application["root"], application["release"]):
        put(directory / application["relative"], json.dumps(application["summary"]).encode())
    with pytest.raises(ValueError, match="exceeds its observed scope"):
        readiness.check_application_evidence(application["release"], application["paper"])


def test_application_validator_failure_is_not_replaced_with_saved_summary(application, monkeypatch):
    def invalid(root):
        raise ValueError("raw application receipt failed independent reconciliation")
    monkeypatch.setattr(readiness.application_builder, "build", invalid)
    with pytest.raises(ValueError, match="raw application receipt failed"):
        readiness.check_application_evidence(application["release"], application["paper"])


def save_highlights_review(context, *, public=True):
    raw = json.dumps(context["review"]).encode()
    put(context["root"] / context["relative"], raw)
    if public:
        put(context["release"] / context["relative"], raw)


@pytest.fixture
def word_highlights(tmp_path, monkeypatch):
    """Copy the reviewed artifact into isolated fixtures; never run a renderer."""
    source_root = Path(readiness.__file__).resolve().parents[2]
    root, release = tmp_path / "word-source", tmp_path / "word-public"
    relative = "research/softwarex/generated/highlights-docx-review.json"
    review = json.loads((source_root / relative).read_bytes())
    for directory in (root, release):
        for path in [relative, review["artifact"], review["source"], review["builder"]]:
            put(directory / path, (source_root / path).read_bytes())
    monkeypatch.setattr(readiness, "ROOT", root)
    return {"root": root, "release": release, "relative": relative, "review": review}


def test_word_highlights_rechecks_actual_structure_without_renderer(word_highlights, monkeypatch):
    import subprocess
    def no_execution(*args, **kwargs):
        raise AssertionError("readiness must not create or render a Word document")
    monkeypatch.setattr(subprocess, "run", no_execution)
    monkeypatch.setattr(subprocess, "Popen", no_execution)
    monkeypatch.setattr(readiness.highlights_builder, "create", no_execution)
    original = readiness.highlights_builder.validate_docx
    calls = []
    def actual_check(artifact, source):
        calls.append((artifact, source))
        return original(artifact, source)
    monkeypatch.setattr(readiness.highlights_builder, "validate_docx", actual_check)
    result = readiness.check_word_highlights(word_highlights["release"])
    review, root = word_highlights["review"], word_highlights["root"]
    assert calls == [(root / review["artifact"], root / review["source"])]
    assert result["artifact_sha256"] == review["artifact_sha256"]
    assert result["structural_validation"]["highlight_character_counts"] == [82, 69, 71, 69, 83]
    assert result["renderer_executed_for_readiness"] is False
    assert result["public_files_bound"] == 4
    assert not (root / "tmp").exists()  # Local PNGs/renderers are not prerequisites.
    assert not (root / "research/softwarex/generated/final-readiness.json").exists()


@pytest.mark.parametrize("key", ["artifact", "builder", "source"])
@pytest.mark.parametrize("location", ["local", "public"])
def test_word_highlights_rejects_source_artifact_and_builder_drift(word_highlights, key, location):
    directory = word_highlights["root"] if location == "local" else word_highlights["release"]
    path = directory / word_highlights["review"][key]
    put(path, path.read_bytes() + b"changed bytes")
    with pytest.raises(ValueError):
        readiness.check_word_highlights(word_highlights["release"])


@pytest.mark.parametrize("mode", [
    "schema", "artifact-path", "builder-path", "source-path", "artifact-size", "boolean-size",
    "author", "character-counts", "structure-false", "structure-number", "extra-structure",
    "uninspected", "external-review", "other-reviewer", "page-count", "boolean-page-count",
    "page-size", "missing-review-outcome", "missing-image-hash", "missing-pdf-hash",
    "outside-image", "public-image-claim", "nonlocal-intermediates", "public-review-drift",
])
def test_word_highlights_rejects_incomplete_or_rebound_review(word_highlights, mode):
    review = word_highlights["review"]
    visual = review["visual_review"]
    if mode == "schema":
        review["schema"] = "unreviewed"
    elif mode.endswith("-path"):
        review[mode.removesuffix("-path")] = "../outside"
    elif mode == "artifact-size":
        review["artifact_bytes"] += 1
    elif mode == "boolean-size":
        review["artifact_bytes"] = True
    elif mode == "author":
        review["author"] = "Changed author"
    elif mode == "character-counts":
        review["unchanged_highlight_character_counts"][0] += 1
    elif mode == "structure-false":
        review["structural_validation"]["passed"] = False
    elif mode == "structure-number":
        review["structural_validation"]["true_word_bullets"] = 1
    elif mode == "extra-structure":
        review["structural_validation"]["unsupported_check"] = True
    elif mode == "uninspected":
        visual["all_rendered_pages_inspected"] = False
    elif mode == "external-review":
        visual["independent_human_review"] = True
    elif mode == "other-reviewer":
        visual["performed_by"] = "external_developers"
    elif mode == "page-count":
        visual["page_count"] = 2
    elif mode == "boolean-page-count":
        visual["page_count"] = True
    elif mode == "page-size":
        visual["page_size_points"] = [595, 842]
    elif mode == "missing-review-outcome":
        visual["outcome"] = ""
    elif mode == "missing-image-hash":
        visual.pop("page_png_sha256")
    elif mode == "missing-pdf-hash":
        visual["intermediate_pdf_sha256"] = "not a hash"
    elif mode == "outside-image":
        visual["page_png"] = "../outside/page-1.png"
    elif mode == "public-image-claim":
        visual["page_png"] = "research/softwarex/page-1.png"
    elif mode == "nonlocal-intermediates":
        visual["intermediates_are_local_qa_only"] = False
    else:
        visual["outcome"] = "Changed only in local review"
    save_highlights_review(word_highlights, public=mode != "public-review-drift")
    with pytest.raises(ValueError):
        readiness.check_word_highlights(word_highlights["release"])


@pytest.mark.parametrize("mode", ["paragraph-text", "author", "word-bullets", "title-border"])
def test_word_highlights_does_not_trust_positive_review_flags(word_highlights, mode):
    review = word_highlights["review"]
    original = (word_highlights["root"] / review["artifact"]).read_bytes()
    rewritten = BytesIO()
    with zipfile.ZipFile(BytesIO(original)) as source, zipfile.ZipFile(rewritten, "w") as changed:
        for item in source.infolist():
            raw = source.read(item.filename)
            if mode == "paragraph-text" and item.filename == "word/document.xml":
                raw = raw.replace(b"ZeroRun distinguishes", b"Altered distinguishes")
            elif mode == "author" and item.filename == "docProps/core.xml":
                raw = raw.replace(b"Jishan Kapoor", b"Changed author")
            elif mode == "word-bullets" and item.filename == "word/numbering.xml":
                raw = raw.replace(b'w:numFmt w:val="bullet"', b'w:numFmt w:val="decimal"')
            elif mode == "title-border" and item.filename == "word/styles.xml":
                title_start = raw.index(b'w:styleId="Title"')
                raw = raw[:title_start] + raw[title_start:].replace(
                    b"<w:pPr>", b'<w:pPr><w:pBdr><w:bottom w:val="single"/></w:pBdr>', 1)
            changed.writestr(item, raw)
    raw = rewritten.getvalue()
    assert raw != original
    review["artifact_bytes"] = len(raw)
    review["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
    for directory in (word_highlights["root"], word_highlights["release"]):
        put(directory / review["artifact"], raw)
    save_highlights_review(word_highlights)
    with pytest.raises(ValueError):
        readiness.check_word_highlights(word_highlights["release"])


def test_word_highlights_loaded_validator_must_match_reviewed_builder(word_highlights):
    review = word_highlights["review"]
    raw = (word_highlights["root"] / review["builder"]).read_bytes() + b"\n# drift\n"
    review["builder_sha256"] = hashlib.sha256(raw).hexdigest()
    for directory in (word_highlights["root"], word_highlights["release"]):
        put(directory / review["builder"], raw)
    save_highlights_review(word_highlights)
    with pytest.raises(ValueError, match="loaded Word highlights validator differs"):
        readiness.check_word_highlights(word_highlights["release"])


def test_word_highlights_validator_failure_propagates(word_highlights, monkeypatch):
    def invalid(*args):
        raise ValueError("actual Word artifact validation failed")
    monkeypatch.setattr(readiness.highlights_builder, "validate_docx", invalid)
    with pytest.raises(ValueError, match="actual Word artifact validation failed"):
        readiness.check_word_highlights(word_highlights["release"])
