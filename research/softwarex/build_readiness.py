"""Record completed package checks, without claiming submission or acceptance."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from research.softwarex.build_paper import build as build_paper
from research.softwarex.build_public_release import inspect
from research.softwarex import build_submission_artifacts as artifacts_builder
from research.softwarex.build_submission_artifacts import verify, strict_json, read_regular, member_name, SOURCE_FILES

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "research/softwarex"
EXTENSION_TESTS = "research/softwarex/evidence/publication-extension-final-v2"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return strict_json(read_regular(path))


def sha(path):
    return hashlib.sha256(read_regular(path)).hexdigest()


def matching_public_file(release, relative, local):
    member_name(relative)
    require(read_regular(release / relative) == read_regular(local), "public release file differs: " + relative)


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


def check_test_and_install_bindings(release):
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
        raw = read_regular(release / row["path"])
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
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
    xml = ET.fromstring(read_regular(ROOT / EXTENSION_TESTS / "tests.xml"))
    suites = [xml] if xml.tag == "testsuite" else xml.findall(".//testsuite")
    counts = {key: sum(int(s.attrib[key]) for s in suites) for key in ("tests", "failures", "errors", "skipped")}
    require(counts == receipt["junit_counts"] and counts["tests"] == receipt["testcases_present"]
            == len(xml.findall(".//testcase")) and counts["failures"] == counts["errors"] == 0
            and counts["tests"] > counts["skipped"] >= 0
            and not xml.findall(".//failure") and not xml.findall(".//error"), "publication JUnit disagrees")
    return {"receipt": relative, "sha256": sha(ROOT / relative), "counts": counts,
            "source_files_bound": len(rows), "scope": receipt["scope"]}


def build(release):
    inspect(release)
    _, _, regenerated = build_paper()
    evidence = read(HERE / "generated/paper-evidence.json")
    require(evidence == regenerated and evidence["preview"] is False, "article evidence is stale")
    require(evidence["replication"]["completed"] is True and evidence["state_rejoin"]["independent_analysis"]["completed"] is True,
            "completed independently reconciled examples required")
    artifacts, pdf = check_artifacts(release, evidence)
    files = {}
    for name in ("COVER_LETTER.txt", "HIGHLIGHTS.txt", "UPLOAD_GUIDE.md", "SUBMISSION_CHECKLIST.md", "REPRODUCIBILITY.md"):
        path = HERE / name
        require(path.is_file() and path.stat().st_size > 0, "missing author upload item")
        files[path.relative_to(ROOT).as_posix()] = sha(path)
        matching_public_file(release, path.relative_to(ROOT).as_posix(), path)
    highlights = (HERE / "HIGHLIGHTS.txt").read_text(encoding="utf-8").splitlines()
    require(3 <= len(highlights) <= 5 and all(0 < len(line) <= 85 for line in highlights), "highlight limits not met")
    install, final_tests = check_test_and_install_bindings(release)
    extension_tests = check_extension_tests(release)
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
        "runtime_core_commit": install["core_commit"],
        "validated_pre_readiness_manifest_sha256": sha(release / "PUBLIC_RELEASE_MANIFEST.json"),
        "manifest_scope": "Checked enclosing stage before adding this readiness file; the later public manifest includes this receipt, avoiding a self-hash cycle.",
        "paper_evidence_sha256": sha(HERE / "generated/paper-evidence.json"),
        "pdf": {"path": pdf.relative_to(ROOT).as_posix(), "sha256": sha(pdf), "review_receipt_sha256": sha(HERE / "generated/pdf-review.json")},
        "archives": {key: artifacts[key] for key in ("source_archive", "reviewer_archive")},
        "author_upload_texts_sha256": files,
        "selected_public_tests": {"passed": final_tests["pytest_passed"], "skipped": final_tests["pytest_skipped"], "additional_passing_subtests": final_tests["subtests_passed"]},
        "targeted_public_test_amendments": final_tests.get("targeted_test_amendments", []),
        "publication_extension_tests": extension_tests,
        "client_extension": {"scripted_cases": extension["scripted_client_conformance"]["scripted_cases"],
                             "live_model_lifecycle_pass": extension["bounded_live_client"]["functional_lifecycle_pass"],
                             "recorded_model_turns": extension["bounded_live_client"]["agent_stages_recorded"],
                             "evidence_sha256": sha(HERE / "generated/extension-evidence-v1.json")},
        "replication": {"completed": True, "planned_requests": planned["requests"],
                        "fresh_agreements": replication["counts"]["fresh_agreements"], "optimized_hits": replication["counts"]["optimized_hits"]},
        "state_case": {"completed": True, "requests": state["requests"], "optimized_hits": state["optimized_hits"], "autonomous_agent_evaluation": False},
        "author_actions_remaining": ["Read and approve manuscript and evidence", "Confirm originality and no concurrent submission", "Complete publisher competing-interest and actual contribution declarations", "Supply any required private address information", "Upload files and approve the publisher-generated review PDF"],
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
