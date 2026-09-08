"""Record completed package checks, without claiming submission or acceptance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from research.softwarex.build_paper import build as build_paper
from research.softwarex.build_public_release import inspect, HISTORICAL_RUNTIME_PREFIX, CURRENT_CORE, CURRENT_VERSION
from research.softwarex.build_public_release import REVIEWER_ASSET, external_archive_identity, external_artifacts
from research.softwarex import build_submission_artifacts as artifacts_builder
from research.softwarex import build_application_evidence as application_builder
from research.softwarex import build_highlights as highlights_builder
from research.softwarex import build_handoff_evidence as handoff_builder
from research.softwarex import run_publication_tests as publication_runner
from research.softwarex.build_submission_artifacts import verify, strict_json, read_regular, member_name, SOURCE_FILES

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "research/softwarex"
EXTENSION_TESTS = "research/softwarex/evidence/publication-final-053-20260908-v2"
CURRENT_RUNTIME_RECEIPT = "research/softwarex/evidence/current-runtime-0.5.3-v1/receipt.json"
CURRENT_QUICKSTART_DIR = "research/softwarex/evidence/quickstart-public-053-v1"
HOSTED_CI_RECEIPT = "research/softwarex/evidence/hosted-ci-20260907/receipt.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return strict_json(read_regular(path))


def sha(path):
    return hashlib.sha256(read_regular(path)).hexdigest()


def matching_public_file(release, relative, local):
    member_name(relative)
    if relative == REVIEWER_ASSET:
        require(external_archive_identity(release / relative) == external_archive_identity(local),
                "public release reviewer archive differs")
        return
    require(read_regular(release / relative) == read_regular(local), "public release file differs: " + relative)


def check_hosted_ci_observation(release):
    """Bind the retained administrative observation without treating it as test evidence."""
    path = ROOT / HOSTED_CI_RECEIPT
    if not path.exists():
        return None
    receipt = read(path)
    require(receipt["schema"] == "zerorun.hosted-ci-status.v1"
            and receipt["repository"] == "https://github.com/floxy-21/zerorun-mvp"
            and receipt["status"] == "NOT_STARTED_ACCOUNT_BILLING_OR_SPENDING_LIMIT",
            "hosted CI administrative observation differs")
    commit = receipt["commit"]
    require(isinstance(commit, str) and len(commit) == 40
            and all(value in "0123456789abcdef" for value in commit), "hosted CI commit absent")
    run_id = receipt["run_id"]
    require(type(run_id) is int and run_id > 0
            and receipt["run_url"] == receipt["repository"] + "/actions/runs/" + str(run_id),
            "hosted CI run identity differs")
    require(all(receipt[key] is False for key in (
        "test_execution_observed", "test_failure_observed", "hosted_ci_green",
        "account_settings_changed", "payment_made")), "hosted CI nonexecution scope differs")
    require(isinstance(receipt["observed_utc"], str) and isinstance(receipt["observation"], str)
            and receipt["observation"].strip(), "hosted CI observation absent")
    matching_public_file(release, HOSTED_CI_RECEIPT, path)
    return {"receipt": HOSTED_CI_RECEIPT, "receipt_sha256": sha(path), "observation": receipt,
            "scope": "Retained operator observation of jobs blocked before execution; no new GitHub query, test result, or green hosted CI attestation.",
            "used_as_passing_test_evidence": False}


def check_artifacts(release, evidence):
    artifacts_path = HERE / "generated/artifact-build.json"
    artifacts = read(artifacts_path)
    require(artifacts["schema"] == "zerorun.softwarex-artifact-build.v1" and artifacts["completed"] is True,
            "completed artifact build required")
    require(artifacts["paper_evidence_sha256"] == sha(HERE / "generated/paper-evidence.json"), "artifact evidence binding is stale")
    require(artifacts["pdf_review_sha256"] == sha(HERE / "generated/pdf-review.json"), "artifact PDF review binding is stale")
    require(artifacts["builder_sha256"] == sha(Path(artifacts_builder.__file__)), "artifact builder changed")
    require(artifacts["journal_submitted"] is False and artifacts["payment_made"] is False
            and artifacts["private_history_included"] is False and artifacts["private_authority_included"] is False,
            "artifact provenance declaration differs")
    for key, relative in (("source_archive", "output/submission/SoftwareX_source.zip"),
                          ("reviewer_archive", "output/submission/ZeroRun_SoftwareX_reviewer.zip")):
        require(artifacts[key]["path"] == relative and artifacts[key]["verified"] is True, "unexpected artifact path or verification")
        actual = verify(ROOT / relative)
        require(actual == artifacts[key], "archive no longer matches verified build")
        matching_public_file(release, relative, ROOT / relative)
        if relative == REVIEWER_ASSET:
            declared = external_artifacts(read(release / "PUBLIC_RELEASE_MANIFEST.json"))
            if declared:  # Historical pre-externalization fixtures remain readable.
                require({name: declared[0][name] for name in ("path", "bytes", "sha256")} ==
                        {name: actual[name] for name in ("path", "bytes", "sha256")},
                        "external reviewer declaration differs from fully verified archive")
    pdf = ROOT / "output/pdf/zerorun-softwarex.pdf"
    qa = read(HERE / "generated/pdf-review.json")
    require(qa["pdf_sha256"] == sha(pdf) == artifacts["pdf_sha256"], "reviewed PDF changed")
    require(qa["all_pages_visually_reviewed"] is True and qa["word_limit_pass"] is True
            and qa["unresolved_references"] is False, "PDF checks incomplete")
    current = {name: sha(HERE / "paper" / name) for name in SOURCE_FILES}
    require(current == qa["compilation_source_sha256"] == artifacts["compilation_source_sha256"],
            "PDF/source/archive binding changed")
    require(current["main.tex"] == evidence["main_tex_sha256"]
            and current["references.bib"] == evidence["bibliography_sha256"], "current manuscript differs from regenerated evidence")
    for name in SOURCE_FILES:
        matching_public_file(release, "research/softwarex/paper/" + name, HERE / "paper" / name)
    for relative in ("output/pdf/zerorun-softwarex.pdf", "research/softwarex/generated/paper-evidence.json",
                     "research/softwarex/generated/pdf-review.json", "research/softwarex/generated/artifact-build.json"):
        matching_public_file(release, relative, ROOT / relative)
    return artifacts, pdf


def check_targeted_test_amendment(release, original):
    """Accept one independently retested harness correction, never runtime drift."""
    allowed = "research/sqj/strengthening/tests/test_analyze_replication.py"
    require(original["path"] == allowed, "only the explicitly named research test can be amended")
    relative = "research/softwarex/evidence/public-test-amendment-v1.json"
    receipt = read(ROOT / relative)
    matching_public_file(release, relative, ROOT / relative)
    require(receipt["schema"] == "zerorun.softwarex-public-test-amendment.v1"
            and receipt["completed"] is True and receipt["path"] == allowed,
            "unexpected targeted-test amendment")
    require(isinstance(receipt.get("reason"), str) and receipt["reason"].strip(), "amendment explanation absent")
    require(receipt["original_sha256"] == original["sha256"], "amendment does not bind the historical tested source")
    current = read_regular(release / allowed)
    require(type(receipt["current_bytes"]) is int and receipt["current_bytes"] == len(current)
            and receipt["current_sha256"] == hashlib.sha256(current).hexdigest()
            and receipt["current_sha256"] != original["sha256"], "amended source hash/size mismatch")
    junit = receipt["junit"]
    require(junit["path"] == "research/softwarex/evidence/public-test-amendment-v1.xml", "unexpected amendment JUnit path")
    require(all(type(junit[key]) is int for key in ("tests", "failures", "errors", "skipped"))
            and junit["tests"] > 0 and junit["failures"] == junit["errors"] == junit["skipped"] == 0,
            "targeted amendment is not a complete passing test run")
    raw = read_regular(release / junit["path"])
    matching_public_file(release, junit["path"], ROOT / junit["path"])
    require(hashlib.sha256(raw).hexdigest() == junit["sha256"], "amendment JUnit hash differs")
    root = ET.fromstring(raw)
    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    require(suites, "amendment JUnit suites absent")
    counts = {key: sum(int(suite.attrib[key]) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
    require(counts == {key: junit[key] for key in counts}, "amendment JUnit counts differ")
    require(sum(len(suite.findall("testcase")) for suite in suites) == counts["tests"]
            and not root.findall(".//failure") and not root.findall(".//error") and not root.findall(".//skipped"),
            "amendment JUnit test cases are incomplete or failing")
    return receipt


def check_test_and_install_bindings(release, *, historical_runtime_prefix=None):
    install = read(HERE / "generated/public-install-smoke.json")
    tests = read(HERE / "generated/public-release-tests.json")
    require(install["schema"] == "zerorun.softwarex-public-install-smoke.v1" and install["status"] == "PASS"
            and install["installed_runtime_files_exact"] == 36 and install["wheel_runtime_files_exact"] == 36,
            "installed package not verified")
    require(tests["schema"] == "zerorun.softwarex-public-release-tests.v1"
            and tests["status"] == "PASS_WITH_PLATFORM_SKIPS" and tests["core_commit"] == install["core_commit"],
            "test/install source identity differs")
    wheel = release / "output/packages/zerorun-softwarex/zerorun-0.5.1-py3-none-any.whl"
    raw = read_regular(wheel)
    require(len(raw) == install["wheel_bytes"] and hashlib.sha256(raw).hexdigest() == install["wheel_sha256"],
            "current public wheel differs from installed wheel")
    checked = tests["tested_code"]
    require(isinstance(checked, list) and checked and len({row["path"] for row in checked}) == len(checked),
            "missing or duplicate tested-code inventory")
    amendments = []
    for row in checked:
        member_name(row["path"])
        relative = row["path"]
        if historical_runtime_prefix is not None and relative.startswith("src/zerorun/"):
            require(historical_runtime_prefix == HISTORICAL_RUNTIME_PREFIX,
                    "unexpected preserved historical runtime prefix")
            relative = historical_runtime_prefix + "/" + relative
        raw = read_regular(release / relative)
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            # A changed historical runtime is never an allowed test amendment.
            require(relative == row["path"], "preserved historical runtime differs from its tested bytes")
            amendments.append(check_targeted_test_amendment(release, row))
    final_tests = tests["runs"][-1]
    require(final_tests["pytest_failed"] == 0 and final_tests["errors"] == 0
            and final_tests["pytest_passed"] > 0, "public-layout tests not passing")
    for run in tests["runs"]:
        member_name(run["junit"])
        raw = read_regular(release / run["junit"])
        require(hashlib.sha256(raw).hexdigest() == run["sha256"], "retained JUnit changed")
        root = ET.fromstring(raw)
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        require(suites, "JUnit test suites absent")
        counts = {key: sum(int(suite.attrib[key]) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
        require(all(value >= 0 for value in counts.values()), "invalid JUnit counts")
        require(counts == {"tests": run["junit_cases_including_subtests"], "failures": run["pytest_failed"],
                           "errors": run["errors"], "skipped": run["pytest_skipped"]}
                and counts["tests"] - counts["failures"] - counts["errors"] - counts["skipped"]
                == run["pytest_passed"] + run["subtests_passed"], "JUnit/receipt counts differ")
    for name in ("public-install-smoke.json", "public-release-tests.json"):
        matching_public_file(release, "research/softwarex/generated/" + name, HERE / "generated" / name)
    return install, dict(final_tests, targeted_test_amendments=amendments)


def check_current_runtime_bindings(release):
    """Require separate 0.5.3 checks; historical receipts never certify this runtime."""
    from research.softwarex.five_hour_review import current_runtime_053 as current_runtime
    relative = CURRENT_RUNTIME_RECEIPT
    matching_public_file(release, relative, ROOT / relative)
    matching_public_file(release, current_runtime.SELF, ROOT / current_runtime.SELF)
    require(sha(Path(current_runtime.__file__)) == sha(ROOT / current_runtime.SELF),
            "loaded current-runtime validator differs")
    summary = current_runtime.validate(release, ROOT / relative)
    require(summary["current_core_commit"] == CURRENT_CORE and summary["version"] == CURRENT_VERSION,
            "current runtime is not the reviewed release commit/version")
    xml_relative = str(Path(relative).parent / "tests.xml").replace("\\", "/")
    matching_public_file(release, xml_relative, ROOT / xml_relative)
    return {"receipt": relative, **summary,
            "scope": "Fresh wheel installation and direct current public-package regression tests; no model, Docker performance or historical-result reclassification."}


def check_current_quickstart(release):
    """Require the documented 0.5.3 installation and five actual STDIO stages."""
    from research.softwarex import quickstart_053
    helper = "research/softwarex/quickstart_053.py"
    guide = "research/softwarex/QUICKSTART_053.md"
    install_relative, check_relative = (CURRENT_QUICKSTART_DIR + "/" + name
                                        for name in ("install.json", "check.json"))
    require(sha(Path(quickstart_053.__file__)) == sha(ROOT / helper),
            "loaded current quickstart validator differs")
    paths = (helper, guide, "research/softwarex/diagnose_mcp_authority_052.py", install_relative, check_relative)
    for relative in paths:
        matching_public_file(release, relative, ROOT / relative)
    pair = quickstart_053.validate_receipt_pair(ROOT, ROOT / install_relative, ROOT / check_relative)
    install, check = pair["installation"], pair["check"]
    require(pair["passed"] is True and pair["read_only"] is True
            and pair["model_called"] is False and pair["real_repository_authorized"] is False
            and install["passed"] is True and check["passed"] is True
            and install["source_commit"] == check["source_commit"],
            "current public guide installation and five-stage workflow must pass on one source commit")
    return {"version": CURRENT_VERSION, "installation": install, "workflow": check,
            "public_files_sha256": {relative: sha(ROOT / relative) for relative in paths},
            "scope": "Fresh 0.5.3 public-source installation and five account-free synthetic STDIO stages; retained raw receipts rechecked, no independent human-user or agent usefulness claim."}


def check_extension_tests(release):
    relative = EXTENSION_TESTS + "/receipt.json"
    receipt = read(ROOT / relative)
    matching_public_file(release, relative, ROOT / relative)
    require(receipt["schema"] == "zerorun.softwarex-publication-tests.v1"
            and receipt["passed"] is True and receipt["returncode"] == 0 and receipt["failure"] is None,
            "complete passing publication-extension tests required")
    require(receipt["source_unchanged"] is True and receipt["source_before"] == receipt["source_after"],
            "publication source changed during tests")
    rows = receipt["source_before"]
    require(rows and len({row["path"] for row in rows}) == len(rows), "test-source inventory absent or duplicated")
    require(rows == publication_runner.inventory(ROOT), "publication test inventory omits or changes current source")
    selected = publication_runner.test_paths(ROOT)
    command = receipt.get("command")
    require(isinstance(command, list) and command[1:4] == ["-B", "-m", "pytest"]
            and command[4:4 + len(selected)] == selected,
            "publication test command omits selected offline modules")
    options = command[4 + len(selected):]
    require(len(options) == 6 and options[:4] == ["-q", "--import-mode=importlib", "-p", "no:cacheprovider"]
            and isinstance(options[4], str) and options[4].startswith("--basetemp=")
            and isinstance(options[5], str) and options[5].startswith("--junitxml="),
            "publication test command options differ from full direct invocation")
    for row in rows:
        member_name(row["path"])
        raw = read_regular(ROOT / row["path"])
        require(len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"],
                "publication source differs from tested bytes: " + row["path"])
        matching_public_file(release, row["path"], ROOT / row["path"])
    for name, row in receipt["output_files"].items():
        require(name in {"tests.xml", "pytest.log"}, "unexpected test-output name")
        path = ROOT / EXTENSION_TESTS / name
        require(path.stat().st_size == row["bytes"] and sha(path) == row["sha256"], "test-output bytes differ")
        matching_public_file(release, EXTENSION_TESTS + "/" + name, path)
    require(set(receipt["output_files"]) == {"tests.xml", "pytest.log"}, "test output missing")
    accounting = publication_runner.junit_accounting(
        read_regular(ROOT / EXTENSION_TESTS / "tests.xml"), read_regular(ROOT / EXTENSION_TESTS / "pytest.log"))
    counts = accounting["counts"]
    require(accounting == receipt["junit_accounting"] and counts == receipt["junit_counts"]
            and accounting["testcase_elements"] == receipt["testcases_present"], "publication JUnit disagrees")
    return {"receipt": relative, "sha256": sha(ROOT / relative), "counts": counts,
            "junit_accounting": accounting,
            "source_files_bound": len(rows), "scope": receipt["scope"]}


def check_application_evidence(release, paper_evidence):
    """Reconcile actual outcomes and all public inputs; never require a live pass."""
    regenerated = application_builder.build(ROOT)
    relative = "research/softwarex/generated/application-evidence-v1.json"
    saved = read(ROOT / relative)
    require(saved == regenerated, "application evidence is stale")
    require(paper_evidence.get("application") == regenerated,
            "manuscript application evidence differs from independently reconciled outcomes")
    require(regenerated.get("schema") == "zerorun.softwarex-application-evidence.v1"
            and regenerated.get("completed") is True,
            "recorded application outcomes are not completely reconciled")
    # These are installation/conformance prerequisites, not model success gates.
    # Adverse v1/v2/v3 model outcomes remain present exactly as regenerated.
    for name in ("clean_installation", "account_free_quickstart",
                 "public_guide_installation", "public_guide_quickstart"):
        require(regenerated.get(name, {}).get("validation", {}).get("passed") is True,
                "passing installation/quickstart evidence required: " + name)
    interpretation = regenerated.get("interpretation", {})
    require(interpretation.get("model_and_quickstart_installations_are_distinct") is True
            and interpretation.get("same_frozen_runtime_required") is True
            and type(interpretation.get("independent_human_users")) is int
            and interpretation["independent_human_users"] == 0
            and interpretation.get("autonomous_issue_resolution_study") is False
            and interpretation.get("workflow_speedup_established") is False
            and interpretation.get("acceptance_probability_estimated") is False,
            "application evidence exceeds its observed scope")
    rows = application_builder.source_inputs(ROOT)
    require(isinstance(rows, list) and rows
            and all(isinstance(row, dict) and isinstance(row.get("path"), str) for row in rows)
            and len({row["path"] for row in rows}) == len(rows),
            "application source/evidence inventory absent or duplicated")
    for row in rows:
        member_name(row["path"])
        raw = read_regular(ROOT / row["path"])
        require(type(row.get("bytes")) is int and row["bytes"] == len(raw)
                and row.get("sha256") == hashlib.sha256(raw).hexdigest(),
                "application input bytes differ: " + row["path"])
        matching_public_file(release, row["path"], ROOT / row["path"])
    matching_public_file(release, relative, ROOT / relative)
    return regenerated, len(rows)


def check_handoff_evidence(release, paper_evidence):
    """Require the exact reconciled outcomes and all public inputs, including failures."""
    relative = handoff_builder.OUTPUT
    regenerated = handoff_builder.build(ROOT)
    require(read(ROOT / relative) == regenerated, "handoff evidence is stale")
    require(paper_evidence.get("handoff") == regenerated,
            "manuscript handoff evidence differs from reconciled outcomes")
    require(regenerated.get("schema") == "zerorun.softwarex-handoff-evidence.v1"
            and regenerated.get("available_completed_runs_reconciled") is True
            and regenerated.get("acceptance_probability_estimated") is False
            and regenerated.get("performance_threshold_imposed") is False,
            "handoff reconciliation or claim scope differs")
    rows = handoff_builder.source_inputs(ROOT)
    require(isinstance(rows, list) and rows and len({row["path"] for row in rows}) == len(rows),
            "handoff input inventory missing or duplicated")
    for row in rows:
        member_name(row["path"])
        raw = read_regular(ROOT / row["path"])
        require(type(row.get("bytes")) is int and row["bytes"] == len(raw)
                and row.get("sha256") == hashlib.sha256(raw).hexdigest(),
                "handoff input binding differs: " + row["path"])
        matching_public_file(release, row["path"], ROOT / row["path"])
    matching_public_file(release, relative, ROOT / relative)
    require(handoff_builder.build(release) == regenerated,
            "public handoff inputs do not reproduce the private reconciliation")
    return regenerated, len(rows)


def check_word_highlights(release):
    """Recheck Word structure and bind the retained visual-review attestation.

    This read-only step does not invoke Word/LibreOffice, recreate the document,
    or claim that a recorded visual inspection has been repeated automatically.
    """
    relative = "research/softwarex/generated/highlights-docx-review.json"
    review = read(ROOT / relative)
    require(review.get("schema") == "zerorun-highlights-docx-review-v1",
            "unexpected Word highlights review schema")
    expected = {"artifact": "research/softwarex/HIGHLIGHTS.docx",
                "builder": "research/softwarex/build_highlights.py",
                "source": "research/softwarex/HIGHLIGHTS.txt"}
    for key, path in expected.items():
        require(review.get(key) == path, "unexpected Word highlights " + key + " path")
        require(review.get(key + "_sha256") == sha(ROOT / path),
                "reviewed Word highlights " + key + " bytes changed")
        matching_public_file(release, path, ROOT / path)
    require(sha(ROOT / expected["builder"]) == sha(Path(highlights_builder.__file__)),
            "loaded Word highlights validator differs from reviewed builder")
    raw = read_regular(ROOT / expected["artifact"])
    require(type(review.get("artifact_bytes")) is int and review["artifact_bytes"] == len(raw),
            "reviewed Word highlights size differs")
    # This is the same structural validator called by build_highlights --check.
    actual = highlights_builder.validate_docx(ROOT / expected["artifact"], ROOT / expected["source"])
    require(actual.get("structural_checks_passed") is True,
            "actual Word highlights structure did not pass")
    require(review.get("author") == actual["author"]
            and review.get("unchanged_highlight_character_counts") == actual["highlight_character_counts"],
            "reviewed Word author or highlight counts differ")
    expected_structure = {
        "passed": actual["structural_checks_passed"], "zip_integrity": True,
        "exact_title_and_five_source_paragraphs": actual["exact_source_text"],
        "true_word_bullets": actual["true_word_bullets"], "title_style": True,
        "black_11pt_times_new_roman_no_title_border": actual["black_11pt_text_no_borders"],
        "letter_portrait": actual["letter_portrait"],
        "no_headers_footers_comments_or_tracked_changes": actual["header_footer_absent"],
    }
    recorded_structure = review.get("structural_validation", {})
    require(isinstance(recorded_structure, dict) and set(recorded_structure) == set(expected_structure)
            and all(recorded_structure[key] is value is True for key, value in expected_structure.items()),
            "saved Word structural checks differ from actual validation")
    visual = review.get("visual_review", {})
    require(isinstance(visual, dict) and visual.get("performed_by") == "internal_document_agent"
            and visual.get("independent_human_review") is False
            and visual.get("all_rendered_pages_inspected") is True
            and type(visual.get("page_count")) is int and visual["page_count"] == 1
            and visual.get("page_size_points") == [612, 792]
            and all(type(size) is int for size in visual["page_size_points"])
            and isinstance(visual.get("outcome"), str) and visual["outcome"].strip()
            and visual.get("intermediates_are_local_qa_only") is True,
            "completed, scoped one-page Word visual review required")
    require(isinstance(visual.get("page_png"), str), "Word visual-review PNG reference absent")
    member_name(visual["page_png"])
    require(visual["page_png"].startswith("tmp/") and visual["page_png"].endswith("/page-1.png"),
            "Word visual-review intermediate must remain local QA")
    for key in ("page_png_sha256", "intermediate_pdf_sha256"):
        digest = visual.get(key)
        require(isinstance(digest, str) and len(digest) == 64
                and all(character in "0123456789abcdef" for character in digest),
                "Word visual-review image/PDF hash absent")
    matching_public_file(release, relative, ROOT / relative)
    return {"artifact": expected["artifact"], "artifact_bytes": len(raw),
            "artifact_sha256": actual["docx_sha256"],
            "source_sha256": actual["source_text_sha256"], "builder_sha256": review["builder_sha256"],
            "review_receipt": relative, "review_receipt_sha256": sha(ROOT / relative),
            "structural_validation": actual, "saved_visual_review": visual,
            "visual_review_scope": "Retained internal visual-review attestation bound to exact DOCX, source, and builder bytes; readiness rechecks structure but does not re-render or reperform visual inspection.",
            "renderer_executed_for_readiness": False, "public_files_bound": 4}


def build(release):
    release_manifest = inspect(release)
    _, _, regenerated = build_paper()
    evidence = read(HERE / "generated/paper-evidence.json")
    require(evidence == regenerated and evidence["preview"] is False, "article evidence is stale")
    require(evidence["replication"]["completed"] is True and evidence["state_rejoin"]["independent_analysis"]["completed"] is True,
            "completed independently reconciled examples required")
    application, application_files_bound = check_application_evidence(release, evidence)
    handoff, handoff_files_bound = check_handoff_evidence(release, evidence)
    artifacts, pdf = check_artifacts(release, evidence)
    files = {}
    for name in ("COVER_LETTER.txt", "HIGHLIGHTS.txt", "UPLOAD_GUIDE.md", "SUBMISSION_CHECKLIST.md", "REPRODUCIBILITY.md"):
        path = HERE / name
        require(path.is_file() and path.stat().st_size > 0, "missing author upload item")
        files[path.relative_to(ROOT).as_posix()] = sha(path)
        matching_public_file(release, path.relative_to(ROOT).as_posix(), path)
    highlights = (HERE / "HIGHLIGHTS.txt").read_text(encoding="utf-8").splitlines()
    require(3 <= len(highlights) <= 5 and all(0 < len(line) <= 85 for line in highlights), "highlight limits not met")
    word_highlights = check_word_highlights(release)
    has_current_runtime = release_manifest.get("current_version") == CURRENT_VERSION
    require(not has_current_runtime or len(external_artifacts(release_manifest)) == 1,
            "current complete package requires its hash-bound external reviewer ZIP declaration")
    install, final_tests = check_test_and_install_bindings(
        release, historical_runtime_prefix=HISTORICAL_RUNTIME_PREFIX if has_current_runtime else None)
    current_runtime = check_current_runtime_bindings(release) if has_current_runtime else None
    current_quickstart = check_current_quickstart(release) if has_current_runtime else None
    extension_tests = check_extension_tests(release)
    hosted_ci = check_hosted_ci_observation(release)
    extension = evidence["extension"]
    require(extension["completed"] is True and extension["preview"] is False,
            "recorded extension outcomes required")
    matching_public_file(release, "research/softwarex/generated/extension-evidence-v1.json",
                         HERE / "generated/extension-evidence-v1.json")
    replication, state = evidence["replication"], evidence["state_rejoin"]["independent_analysis"]
    planned = replication.get("planned", replication.get("original_analysis", {}).get("planned"))
    require(isinstance(planned, dict) and type(planned.get("requests")) is int, "planned request denominator missing")
    record = {
        "schema": "zerorun.softwarex-final-readiness.v1",
        "status": "COMPLETE_PACKAGE_REQUIRES_AUTHOR_APPROVAL_AND_PORTAL_UPLOAD",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "author": "Jishan Kapoor",
        "title": "ZeroRun: Reproducible test-result reuse for AI coding tools",
        "journal": "SoftwareX",
        "article_type": "Original Software Publication",
        "public_code_and_evidence": evidence["public_release"],
        "runtime_core_commit": current_runtime["current_core_commit"] if current_runtime else install["core_commit"],
        "historical_runtime_core_commit": install["core_commit"],
        "current_runtime_validation": current_runtime,
        "current_public_quickstart": current_quickstart,
        "validated_pre_readiness_manifest_sha256": sha(release / "PUBLIC_RELEASE_MANIFEST.json"),
        "manifest_scope": "Checked enclosing stage before adding this readiness file; the later public manifest includes this receipt, avoiding a self-hash cycle.",
        "paper_evidence_sha256": sha(HERE / "generated/paper-evidence.json"),
        "pdf": {"path": pdf.relative_to(ROOT).as_posix(), "sha256": sha(pdf), "review_receipt_sha256": sha(HERE / "generated/pdf-review.json")},
        "archives": {key: artifacts[key] for key in ("source_archive", "reviewer_archive")},
        "external_artifacts": external_artifacts(release_manifest),
        "author_upload_texts_sha256": files,
        "word_highlights": word_highlights,
        "selected_public_tests": {"version": "0.5.1", "scope": "Historical selected public-package tests, bound to preserved historical runtime bytes.",
                                  "passed": final_tests["pytest_passed"], "skipped": final_tests["pytest_skipped"], "additional_passing_subtests": final_tests["subtests_passed"]},
        "targeted_public_test_amendments": final_tests.get("targeted_test_amendments", []),
        "publication_extension_tests": extension_tests,
        "hosted_ci_observation": hosted_ci,
        "application_evidence": application,
        "application_evidence_sha256": sha(HERE / "generated/application-evidence-v1.json"),
        "application_files_bound": application_files_bound,
        "handoff_evidence": handoff,
        "handoff_evidence_sha256": sha(ROOT / handoff_builder.OUTPUT),
        "handoff_files_bound": handoff_files_bound,
        "client_extension": {"scripted_cases": extension["scripted_client_conformance"]["scripted_cases"],
                             "live_model_lifecycle_pass": extension["bounded_live_client"]["functional_lifecycle_pass"],
                             "recorded_model_turns": extension["bounded_live_client"]["agent_stages_recorded"],
                             "evidence_sha256": sha(HERE / "generated/extension-evidence-v1.json")},
        "replication": {"completed": True, "planned_requests": planned["requests"],
                        "fresh_agreements": replication["counts"]["fresh_agreements"], "optimized_hits": replication["counts"]["optimized_hits"]},
        "state_case": {"completed": True, "requests": state["requests"], "optimized_hits": state["optimized_hits"], "autonomous_agent_evaluation": False},
        "author_actions_remaining": ["Complete final journal-file upload and inspect the publisher-generated review PDF before submission"],
        "author_confirmations_reported": ["Sole author approved article and AI-assistance disclosure", "Original work, not under consideration elsewhere", "Contributions and possible future commercialization supplied by the author", "Required contact details supplied privately"],
        "journal_submitted": False,
        "payment_made": False,
        "acceptance_probability_estimated": False,
        "commercial_performance_gates_passed": False,
    }
    target = HERE / "generated/final-readiness.json"
    require(not target.exists(), "readiness receipt already exists")
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().release), sort_keys=True))
