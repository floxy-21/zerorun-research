"""Build a clean, explicitly allowlisted research release; never publish it.

The runtime is extracted from an exact Git commit, not the working tree. A
refresh verifies the preceding builder-owned inventory before replacing it;
it never deletes directories or unlisted user files.
"""
from __future__ import annotations

import argparse
from email.parser import Parser
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
CORE = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
HISTORICAL_CORE = CORE
CURRENT_CORE = "d02b12e43ece3526566ec69c7516579f8d2fc9dd"
CURRENT_VERSION = "0.5.2"
HISTORICAL_RUNTIME_PREFIX = "research/softwarex/historical_runtime_0_5_1"
TITLE = "ZeroRun: Reproducible test-result reuse for AI coding tools"
MANIFEST = "PUBLIC_RELEASE_MANIFEST.json"
CORE_TESTS = ("conftest.py", "test_hermetic.py", "test_mcp.py", "test_manifest_safety.py",
    "test_fingerprint_safety.py", "test_path_safety.py", "test_runner_safety.py", "test_trust.py",
    "test_security_json_boundaries.py", "test_cache_trust_session.py", "test_codex_integration.py")
SQJ_FILES = ("analyze_campaign.py", "analyze_comparison.py", "analyze_current_revision.py",
    "bind_publication_source.py", "build_submission.py", "run_controlled_comparison.py", "run_frozen_campaign.py",
    "export_public_evidence.py", "import_public_evidence.py", "COMPARISON_PROTOCOL.md",
    "COST_MODEL_NOTE.md", "BUILD_PROVENANCE.md", "REEXECUTION.md", "REGRESSION_TRACEABILITY.md")
STRENGTHENING_FILES = ("collect_traces.py", "trace_analysis.py", "validate_traces.py",
    "inventory_oracle.py", "randomized_replication.py", "analyze_replication.py", "export_evidence.py",
    "PROTOCOL.md", "TRACE_FEASIBILITY.md")
OPTIONAL_STRENGTHENING = ("state_rejoin.py", "run_bound_state_rejoin.py", "analyze_state_rejoin.py", "STATE_REJOIN_PROTOCOL.md", "STATE_REJOIN.md",
    "run_replication_recovery.py", "run_replication_recovery_preflight_v1.py", "analyze_recovered_replication.py",
    "run_recovered_state_rejoin.py", "export_recovered_evidence.py", "state_rejoin_v2.py", "run_recovered_state_rejoin_v2.py",
    "ANALYSIS_CORRECTIONS.md", "analyze_replication_pre_real_validation_v1.py")
SQJ_EVIDENCE_DIRS = ("campaign-1", "comparison-final-1", "comparison-packaging-corrected",
    "comparison-packaging-final", "comparison-more-whole-task", "linux-regression-final",
    "readonly-hit-linux", "cleanup-and-venv-linux", "github-86f4228")
PAPER_OPTIONAL_FILES = ("main.tex", "main.bib", "manuscript.md", "REPRODUCIBILITY.md",
    "HIGHLIGHTS.md", "HIGHLIGHTS.txt", "HIGHLIGHTS.docx", "build_highlights.py", "generated/highlights-docx-review.json",
    "CODE_METADATA.md", "DATA_AVAILABILITY.md", "THIRD_PARTY_NOTICES.md",
    "README.md", "UPLOAD_GUIDE.md", "COVER_LETTER.md", "COVER_LETTER.txt", "SUBMISSION_CHECKLIST.md",
    "MANUSCRIPT_CLAIM_AUDIT.md", "RELATED_WORK_AUDIT.md",
    "paper/main.tex", "paper/main.bbl", "paper/submission.tex.in", "paper/references.bib", "paper/elsarticle.cls", "paper/elsarticle-num.bst",
    "paper/STYLE_SOURCE_NOTICE.txt", "paper/state-rejoin.tex", "generated/state-rejoin-review.json",
    "generated/paper-evidence.json", "generated/pdf-review.json", "build_paper.py", "build_submission_artifacts.py", "build_readiness.py",
    "base-references.bib", "dataset-reference.bib", "paper/dataset-reference.bib", "related-work-additions.bib",
    "generated/public-install-smoke.json", "generated/public-release-tests.json", "generated/public-release.json",
    "generated/initial-publication.json", "generated/artifact-build.json", "generated/final-readiness.json",
    "generated/artifact-builder-unit-v2.xml", "generated/artifact-builder-unit-v3.xml",
    "generated/artifact-builder-sandbox-diagnostic-v1.xml", "generated/artifact-builder-test-attempts.md",
    "generated/readiness-bindings-unit-v1.xml",
    "OPERATING_GUIDE.md", "MANIFEST_REFERENCE.md", "OPERATING_REGION.md", "LIVE_CLIENT_PROTOCOL.md",
    "LIVE_CLIENT_AMENDMENT_1.md", "support/tools/aggregate_codex_install_evidence.py", "RELATED_SYSTEMS.md",
    "NON_MODEL_DIAGNOSTIC_PROTOCOL.md", "diagnose_mcp_authority.py", "PUBLIC_LAYOUT_CORRECTION.md",
    "client_conformance.py", "analyze_operating_region.py", "analysis_reproduction.py", "run_public_lifecycle.py", "build_extension_evidence.py", "run_publication_tests.py",
    "generated/operating-region-v1.json", "generated/extension-evidence-v1.json",
    "REVIEWER_STRENGTHENING_4H.md", "JOURNAL_REQUIREMENTS_REVIEW.md", "QUICKSTART_RESULTS.md",
    "QUICKSTART_LAB.md", "quickstart_check.py", "CLIENT_API_CARD.md", "APPLICATION_RESULTS.md",
    "QUICKSTART_052.md", "quickstart_052.py", "diagnose_mcp_authority_052.py",
    "build_application_evidence.py", "generated/application-evidence-v1.json",
    "live_client_v2/__init__.py", "live_client_v2/PROTOCOL.md", "live_client_v2/run.py",
    "live_client_v2/validation.py", "live_client_v2/test_validation.py",
    "live_client_v3/__init__.py", "live_client_v3/PROTOCOL.md", "live_client_v3/run.py",
    "live_client_v3/validation.py", "live_client_v3/test_validation.py",
    "guided_client_v1/__init__.py", "guided_client_v1/PROTOCOL.md", "guided_client_v1/run.py",
    "guided_client_v1/validation.py", "guided_client_v1/test_validation.py",
    "five_hour_review/__init__.py", "five_hour_review/current_runtime.py",
    "five_hour_review/INTEGRATION_CHECKLIST.md", "five_hour_review/REVIEWER_MATRIX.md",
    "FIVE_HOUR_FINALIZATION_PLAN.md",
    "verify_submission.py", "VERIFY_SUBMISSION.md",
    "handoff_acquisition_v1/PROTOCOL.md", "handoff_acquisition_v1/collect.py", "handoff_acquisition_v1/test_collect.py",
    "handoff_acquisition_recovery_v1/__init__.py", "handoff_acquisition_recovery_v1/PROTOCOL.md",
    "handoff_acquisition_recovery_v1/recover.py", "handoff_acquisition_recovery_v1/test_recover.py",
    "handoff_v1/__init__.py", "handoff_v1/PROTOCOL.md", "handoff_v1/run.py", "handoff_v1/validate.py",
    "handoff_v1/export.py", "handoff_v1/test_run.py", "handoff_v1/test_validate.py",
    "agent_handoff_v1/PROTOCOL.md", "agent_handoff_v1/common.py", "agent_handoff_v1/run.py",
    "agent_handoff_v1/validate.py", "agent_handoff_v1/test_agent.py",
    "agent_handoff_evaluation_v1/__init__.py", "agent_handoff_evaluation_v1/PROTOCOL.md",
    "agent_handoff_evaluation_v1/run.py", "agent_handoff_evaluation_v1/validate.py",
    "agent_handoff_evaluation_v1/test_evaluation.py", "AGENT_AUTHENTICATION_AMENDMENT.md",
    "HANDOFF_IMAGE_RECOVERY_AMENDMENT.md",
    "handoff_image_v2/__init__.py", "handoff_image_v2/PROTOCOL.md", "handoff_image_v2/build_image.py",
    "handoff_image_v2/run.py", "handoff_image_v2/validate.py", "handoff_image_v2/export.py", "handoff_image_v2/test_image.py",
    "handoff_image_v2/TRANSPORT_AMENDMENT_1.md", "build_handoff_evidence.py", "generated/handoff-evidence-v1.json")
SUBMISSION_ARCHIVES = ("output/submission/SoftwareX_source.zip", "output/submission/ZeroRun_SoftwareX_reviewer.zip")
BLOCKED = {".git", "__pycache__", ".pytest_cache", ".venv", ".zerorun-env", "node_modules", "workspace", "workspaces",
    "private-cache-authentication-NOT-FOR-PUBLICATION", "pytest-temp", "testmon-runtime", "testmon-state",
    ".zerorun", "authorities", "authority", "private", ".codex", ".agents"}
TEXT_SUFFIXES = {".py", ".md", ".json", ".xml", ".txt", ".log", ".csv"}
SECRETS = (re.compile(rb"-----BEGIN (?:OPENSSH|RSA|EC|DSA|PGP|PRIVATE) (?:PRIVATE )?KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{40,}\b"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    require(bool(path.parts) and not path.is_absolute() and ".." not in path.parts
            and not set(path.parts) & BLOCKED and ":" not in name and "\\" not in name,
            "unsafe release path: " + name)
    return path


def read_local(path):
    require(path.is_relative_to(ROOT), "source outside workspace")
    cursor = ROOT
    for part in path.relative_to(ROOT).parts:
        cursor = cursor / part
        info = cursor.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                "link/reparse source refused: " + str(path))
    require(stat.S_ISREG(info.st_mode), "source is not a regular file")
    return path.read_bytes()


def entries(directory, suffixes=TEXT_SUFFIXES, excluded=()):
    require(directory.is_dir(), "required evidence directory missing: " + str(directory))
    blocked = BLOCKED | set(excluded)
    for parent, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in blocked)
        for name in dirs:
            child = Path(parent) / name
            info = child.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                    "linked evidence directory refused")
        for name in sorted(files):
            path = Path(parent) / name
            if path.suffix in suffixes or name in {"LICENSE", "LICENSE.txt", "NOTICE", "NOTICE.txt"}:
                yield path


def pyproject(version="0.5.1"):
    require(version in {"0.5.1", CURRENT_VERSION}, "unsupported publication package version")
    return '''[build-system]
requires = ["setuptools==84.0.0"]
build-backend = "setuptools.build_meta"

[project]
name = "zerorun"
version = "0.5.1"
description = "Conservative deterministic test-result reuse for AI coding tools"
readme = "README.md"
requires-python = ">=3.10"
license = "MIT"
license-files = ["LICENSE.txt", "Licence.txt", "THIRD_PARTY_NOTICES.md"]
authors = [{name = "Jishan Kapoor"}]
dependencies = []

[project.scripts]
zerorun = "zerorun.cli:main"
zerorun-pytest-prepare = "zerorun.pytest_prepare_cli:main"
zerorun-pytest-review = "zerorun.pytest_review_cli:review_main"
zerorun-pytest-activate = "zerorun.pytest_review_cli:activate_main"

[tool.setuptools.packages.find]
where = ["src"]
include = ["zerorun*"]

[tool.pytest.ini_options]
testpaths = ["tests", "research/sqj/strengthening/tests"]
'''.replace('version = "0.5.1"', 'version = "' + version + '"').encode()


def exact_analysis_helper(git_bytes):
    recorded = read_local(ROOT / "research/sqj/source-final/tools/product_generalization_benchmark.py")
    require(digest(recorded) == "96588c65ee674597e4c459651345b9e89ac92cbdc0c85baaa3de4f57f6a44359",
            "recorded analysis helper bytes differ")
    require(git_bytes.replace(b"\r\n", b"\n") == recorded.replace(b"\r\n", b"\n"),
            "analysis helper differs beyond recorded physical newlines")
    return recorded


def selected_acquisition_archives():
    """Allow only archive bytes bound by the reconciled frozen case selection."""
    from research.softwarex.build_handoff_evidence import ACQUISITION, acquisition_summary
    base = ROOT / ACQUISITION
    if not base.exists():
        return []
    summary, _ = acquisition_summary(base)
    rows = []
    for case in summary["cases"]:
        row = case["source_archive"]
        if row is not None:
            relative = ACQUISITION + "/" + row["path"]
            safe_name(relative)
            require(row["path"] == "cases/" + case["case_id"] + "/source.tar.gz"
                    and row["bytes"] <= 64 * 1024 * 1024, "selected archive path or bound differs")
            rows.append((relative, row))
    require(len({name for name, _ in rows}) == len(rows), "duplicate selected public archive")
    return sorted(rows)


def archive_source(commit, paths, root=None):
    """Keep the already tested CRLF archive representation independent of host defaults."""
    return subprocess.check_output(
        ["git", "-c", "core.autocrlf=true", "-c", "core.eol=crlf",
         "archive", "--format=tar", commit, *paths], cwd=ROOT if root is None else root)


def collect(paper_files=(), current_core=None):
    payloads, origins = {}, {}

    def add(name, raw, origin):
        safe_name(name)
        require(name not in payloads, "duplicate public release member: " + name)
        require(len(raw) < 80 * 1024 * 1024, "oversized release member: " + name)
        require(not any(pattern.search(raw) for pattern in SECRETS), "possible credential material: " + name)
        payloads[name], origins[name] = raw, origin

    def local(relative, target=None):
        add(target or relative, read_local(ROOT / relative), "workspace:" + relative)

    source_paths = ["zerorun", "tools/product_generalization_benchmark.py", "ci/generalization-runtime-requirements.txt",
                    "tools/validate_release_prerequisites.py", "commercial/corpus/selection-v1.json",
                    ".agents/skills/zerorun/SKILL.md"]
    source_paths += ["tools/" + name for name in ("codex_agent_integration_smoke.py", "codex_agent_lifecycle.py",
        "commercial_repo_smoke.py", "install_verified_codex_cli.py", "aggregate_codex_install_evidence.py", "pytest_batched_executor.py", "aggregate_commercial_smoke.py")]
    source_paths += ["tests/" + name for name in CORE_TESTS]
    raw_tar = archive_source(CORE, source_paths)
    core_count = 0
    with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:") as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            require(member.isfile(), "nonregular frozen source member")
            raw = archive.extractfile(member).read()
            target = "src/" + member.name if member.name.startswith("zerorun/") else member.name
            origin = "git:" + CORE + ":" + member.name
            if member.name == ".agents/skills/zerorun/SKILL.md":
                target = "docs/zerorun-SKILL.md"
            elif member.name == "tools/product_generalization_benchmark.py":
                raw = exact_analysis_helper(raw)
                origin = ("workspace:research/sqj/source-final/tools/product_generalization_benchmark.py; "
                          "exact recorded analysis-helper bytes; normalized content equals git:" + CORE + ":" + member.name)
            elif member.name == "tests/test_path_safety.py":
                require(raw.count(b'ROOT / "zerorun"') == 4, "unexpected path-test adaptation boundary")
                raw = raw.replace(b'ROOT / "zerorun"', b'ROOT / "src" / "zerorun"')
                origin += "; four source-root references adapted to src layout"
            elif member.name == "tests/test_codex_integration.py":
                before = b'root / ".agents" / "skills" / "zerorun" / "SKILL.md"'
                require(raw.count(before) == 1, "unexpected skill-test adaptation boundary")
                raw = raw.replace(before, b'root / "docs" / "zerorun-SKILL.md"')
                origin += "; one bundled-skill path adapted to research docs"
            add(target, raw, origin)
            core_count += member.name.startswith("zerorun/")
    require(core_count == 36, "unexpected frozen core file denominator")
    current_version = "0.5.1"
    if current_core is not None:
        from research.softwarex.five_hour_review.current_runtime import validate_metadata_only
        require(current_core == CURRENT_CORE and current_core != HISTORICAL_CORE,
                "only the reviewed current runtime commit is allowed")
        historical = {Path(name).name: raw for name, raw in payloads.items() if name.startswith("src/zerorun/")}
        current = {}
        new_test = "tests/test_mcp_discovery_semantics.py"
        current_tar = archive_source(current_core, ["zerorun", new_test])
        with tarfile.open(fileobj=io.BytesIO(current_tar), mode="r:") as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                require(member.isfile(), "nonregular current source member")
                raw = archive.extractfile(member).read()
                if member.name.startswith("zerorun/"):
                    require(member.name.count("/") == 1 and member.name.endswith(".py"), "unexpected current runtime member")
                    current[Path(member.name).name] = raw
                else:
                    require(member.name == new_test, "unexpected current test member")
                    add(new_test, raw, "git:" + current_core + ":" + new_test)
        validate_metadata_only(historical, current)
        require(new_test in payloads, "current discovery regression test missing")
        for name, old_raw in sorted(historical.items()):
            old_relative = "src/zerorun/" + name
            add(HISTORICAL_RUNTIME_PREFIX + "/" + old_relative, old_raw,
                "git:" + HISTORICAL_CORE + ":zerorun/" + name)
            payloads[old_relative] = current[name]
            origins[old_relative] = "git:" + current_core + ":zerorun/" + name
        current_version = CURRENT_VERSION
    local("LICENSE.txt", "LICENSE.txt")
    add("Licence.txt", payloads["LICENSE.txt"], "identical alias of LICENSE.txt")
    local("research/softwarex/PUBLIC_RELEASE_README.md", "README.md")
    local("research/softwarex/build_public_release.py")
    add("pyproject.toml", pyproject(current_version), "generated src-layout packaging; runtime uses the identified commit exported with explicitly recorded Git text-newline conversion")
    add(".gitattributes", b"* -text\n", "preserve evidence and frozen source bytes across checkouts")
    add(".gitignore", b"__pycache__/\n*.py[cod]\n.pytest_cache/\n.venv/\nbuild/\ndist/\n*.egg-info/\n", "generated build-only exclusions")
    add("THIRD_PARTY_NOTICES.md", (
        "# License boundaries\n\nZeroRun-owned source and research code use the MIT license in LICENSE.txt. "
        "Licence.txt contains identical bytes for the journal template's alternate spelling.\n\n"
        "The manuscript text is copyright 2026 Jishan Kapoor; its inclusion does not place it under "
        "the code's MIT license. The publisher files research/softwarex/paper/elsarticle.cls "
        "(version 3.3, dated 2020/11/20) and elsarticle-num.bst are unmodified Elsevier files. "
        "Their original copyright and LaTeX Project Public License notices are preserved: "
        "LPPL version 1.2 or, at your option, any later version, https://www.latex-project.org/lppl.txt. "
        "They are not MIT-licensed. Publisher LaTeX guidance: "
        "https://www.elsevier.com/researcher/author/policies-and-guidelines/latex-instructions. "
        "Unmodified class source: https://assets.ctfassets.net/o78em1y1w4i4/UtkmcwzDXZeIUVlmhPGFm/4923f3c3bcc768b6fc4fa4f26cf39d72/elsarticle.cls; "
        "unmodified style source: https://assets.ctfassets.net/o78em1y1w4i4/3AcXXwb1xciwEveI37JmxA/606d2d3ffc7739fe2cb57b2c2eb169e0/elsarticle-num.bst. "
        "The journal template is "
        "https://legacyfileshare.elsevier.com/promis_misc/softwarex-osp-template.tex.\n\n"
        "The public trajectory evidence is from Nebius, SWE-rebench-OpenHands-Trajectories, "
        "https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories, observed revision "
        "35455389ab51bf5e2306bfd436ef72d0f98bf882, retrieved 6 September 2026. It remains CC-BY-4.0, "
        "not MIT. Each collection preserves its source-notices/README.md and complete source-notices/LICENSE. "
        "Raw responses are unchanged; analyses are derived annotations. No endorsement is implied.\n\n"
        "The state-rejoin example pairs a SWE-rebench metadata response (CC-BY-4.0, "
        "https://huggingface.co/datasets/nebius/SWE-rebench) with django-environ source "
        "(MIT, https://github.com/joke2k/django-environ). The acquisition receipt binds the exact "
        "revision and SHA256 of django-environ-31c8f983-source.tar.gz; its LICENSE.txt remains "
        "inside the unchanged archive. Any extracted source subset is for inspection; the complete "
        "archive plus reconstruction driver is the reproducible execution input. Derived trace "
        "annotations do not change either upstream license.\n\n"
        "Upstream test workload source and dependencies retain their own licenses. Their project URLs, "
        "commits and dependency pins are recorded in research/sqj/run_frozen_campaign.py and evidence. "
        "Raw logs may contain short third-party source excerpts and retain that provenance; this release "
        "does not claim ownership of those excerpts. Dependencies and upstream repositories are not relicensed.\n\n"
        "The standalone package declares no third-party runtime Python dependency. Building uses setuptools "
        "(MIT); running the research unit tests uses pytest (MIT) and its separately licensed dependencies. "
        "Docker, Git, Python, benchmark dependencies and optional Codex clients are separately installed tools.\n"
    ).encode(), "generated attribution from preserved dataset license and package metadata")
    for name in SQJ_FILES:
        local("research/sqj/" + name)
    for name in ("comparison-summary.json", "comparison-results.csv", "comparison-tables.tex"):
        local("research/sqj/generated/" + name)
    local("research/sqj/evidence/main-source-binding.json")
    local("research/sqj/evidence/package-validation-delivery.json")
    for name in ("zerorun-0.5.1-py3-none-any.whl", "zerorun-0.5.1.tar.gz"):
        local("output/packages/zerorun-sqj-delivery-20260906/" + name)
    public_wheel = "output/packages/zerorun-softwarex/zerorun-0.5.1-py3-none-any.whl"
    if (ROOT / public_wheel).is_file():
        local(public_wheel)
    if current_core is not None:
        current_wheel = "output/packages/zerorun-softwarex/zerorun-0.5.2-py3-none-any.whl"
        if (ROOT / current_wheel).is_file():
            local(current_wheel)
    for path in entries(ROOT / "docs/evidence/softwarex-regression/windows-20260906-readonly-hit-final"):
        local(path.relative_to(ROOT).as_posix())
    for directory in ("source-final", "source-ci-final", "producers"):
        for path in entries(ROOT / "research/sqj" / directory, {".py", ".txt"}):
            local(path.relative_to(ROOT).as_posix())
    for directory in SQJ_EVIDENCE_DIRS:
        for path in entries(ROOT / "research/sqj/evidence" / directory):
            local(path.relative_to(ROOT).as_posix())
    for name in STRENGTHENING_FILES:
        local("research/sqj/strengthening/" + name)
    for name in OPTIONAL_STRENGTHENING:
        if (ROOT / "research/sqj/strengthening" / name).is_file():
            local("research/sqj/strengthening/" + name)
    for directory in ("tests", "producers", "evidence"):
        for path in entries(ROOT / "research/sqj/strengthening" / directory):
            local(path.relative_to(ROOT).as_posix())
    django_source = "research/sqj/strengthening/evidence/django-environ-31c8f983-source.tar.gz"
    if (ROOT / django_source).is_file():
        local(django_source)
    for name in PAPER_OPTIONAL_FILES:
        if (ROOT / "research/softwarex" / name).is_file():
            local("research/softwarex/" + name)
    publication_tests = ROOT / "research/softwarex/tests"
    if publication_tests.is_dir():
        for path in entries(publication_tests, {".py"}):
            local(path.relative_to(ROOT).as_posix())
    publication_evidence = ROOT / "research/softwarex/evidence"
    if publication_evidence.is_dir():
        # Agent inputs are copied only through their exact public capture list below.
        # Never walk a private client home or generated execution/build directory.
        excluded = {"agent-producer-pilot-v1", "agent-producer-main-v1", "source", "preflight-workspace",
                    "dependency-starter", "context", "registry-store", "client-home", "codex-home"}
        for path in entries(publication_evidence, {".json", ".xml", ".log", ".py", ".md"}, excluded):
            local(path.relative_to(ROOT).as_posix())
    for relative, row in selected_acquisition_archives():
        raw = read_local(ROOT / relative)
        require(len(raw) == row["bytes"] and digest(raw) == row["sha256"],
                "selected source archive changed during release assembly")
        add(relative, raw, "workspace:selection-bound-upstream-source:" + relative)
    if (ROOT / "research/softwarex/generated/handoff-evidence-v1.json").is_file():
        from research.softwarex.build_handoff_evidence import source_inputs as handoff_inputs
        for row in handoff_inputs(ROOT):
            relative = row["path"]
            raw = read_local(ROOT / relative)
            require(len(raw) == row["bytes"] and digest(raw) == row["sha256"],
                    "handoff publication input changed during release assembly")
            if relative in payloads:
                require(payloads[relative] == raw, "duplicate handoff input bytes disagree")
            else:
                add(relative, raw, "workspace:record-only-handoff-input:" + relative)
    # These archives are absent during the checked pre-archive stage. A later
    # refresh can inventory their completed bytes without changing the older
    # manifest sealed inside the reviewer archive or creating a self-reference.
    for name in SUBMISSION_ARCHIVES:
        if (ROOT / name).is_file():
            local(name)
    for number in (1, 2):
        source = f"tmp/softwarex-public-tests-{number}.xml"
        if (ROOT / source).is_file():
            target = f"research/softwarex/evidence/public-release-tests-{number}.xml"
            if target in payloads:
                require(payloads[target] == read_local(ROOT / source),
                        "authoritative public test report differs from retained original: " + target)
            else:
                local(source, target)
    for name in paper_files:
        safe_name(name)
        require((name.startswith("research/softwarex/") or name == "output/pdf/zerorun-softwarex.pdf")
                and Path(name).suffix in {".tex", ".in", ".bib", ".md", ".json", ".pdf", ".png", ".svg", ".cls", ".bst"},
                "paper-file must be an explicit SoftwareX publication asset")
        if name not in payloads:
            local(name)
    manifest = {"schema": "zerorun.public-research-release.v1", "article_title": TITLE,
        "frozen_core_commit": CORE, "frozen_core_files": core_count,
        "builder_sha256": digest(Path(__file__).read_bytes()),
        "construction": "selective exact-commit Git archive with fixed text-newline conversion plus explicit research allowlist; no development history",
        "git_archive_representation": {
            "configuration": {"core.autocrlf": "true", "core.eol": "crlf"},
            "raw_git_blob_byte_identity_claimed": False,
            "scope": "git: origins identify committed paths; recorded payload hashes identify the Git archive representation with explicit CRLF text checkout conversion, preserving the already tested package bytes across host defaults."},
        "runtime_modified": current_core is not None,
        "historical_core_commit": HISTORICAL_CORE,
        "current_core_commit": current_core or HISTORICAL_CORE,
        "current_version": current_version,
        "runtime_change_scope": ("Only run_tests discovery descriptions and version string differ from the preserved 0.5.1 runtime; historical measurements are not reclassified."
                                 if current_core else "Exact historical 0.5.1 runtime, unchanged."),
        "historical_runtime_prefix": HISTORICAL_RUNTIME_PREFIX if current_core else None,
        "packaging_adaptations": ["src layout", "author metadata", "license filenames", "research README", "five selected-test file-path references"],
        "public_repository_destination": "https://github.com/floxy-21/zerorun-research",
        "publication_performed_by_builder": False, "excluded_directory_names": sorted(BLOCKED),
        "archive_binding": "Submission archives, when present, seal an earlier checked pre-archive inventory. This enclosing manifest hashes completed archives; the reviewer archive does not include itself or this later manifest.",
        "files": [{"path": name, "bytes": len(raw), "sha256": digest(raw), "origin": origins[name]}
                  for name, raw in sorted(payloads.items())]}
    payloads[MANIFEST] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    return payloads, manifest


def inspect(directory):
    require(directory.is_dir() and not directory.is_symlink(), "release directory absent or linked")
    manifest = json.loads((directory / MANIFEST).read_bytes())
    expected = {row["path"]: row for row in manifest["files"]}
    require(len(expected) == len(manifest["files"]), "duplicate manifest path")
    actual = set()
    for parent, dirs, files in os.walk(directory, followlinks=False):
        if Path(parent) == directory:
            # A newly initialized public checkout has local VCS administration;
            # it is never a release payload and is never read or changed here.
            dirs[:] = [name for name in dirs if name != ".git"]
            files = [name for name in files if name != ".git"]
        for name in dirs + files:
            path = Path(parent) / name
            info = path.lstat()
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400, "linked release member")
        for name in files:
            path = Path(parent) / name
            actual.add(path.relative_to(directory).as_posix())
    require(actual == set(expected) | {MANIFEST}, "release contains missing or unlisted files")
    for name, row in expected.items():
        safe_name(name)
        raw = (directory / name).read_bytes()
        require(len(raw) == row["bytes"] and digest(raw) == row["sha256"], "modified release file: " + name)
    return manifest


def atomic_write(path, raw, source=None):
    temporary = path.with_name(path.name + ".building")
    require(not temporary.exists(), "incomplete earlier write exists: " + str(temporary))
    if source is not None:
        require(read_local(source) == raw, "source evidence changed during assembly")
        os.link(source, temporary)
    else:
        with temporary.open("xb") as stream:
            stream.write(raw)
    # Replacing detaches an old hardlink; never truncate source evidence.
    os.replace(temporary, path)


def stage_package(release, destination):
    """Small disposable build input: excludes evidence, tests, and private state."""
    inspect(release)
    require(destination.parent.resolve() == (ROOT / "tmp").resolve()
            and destination.name.startswith("softwarex-package-smoke-"), "unsafe package staging path")
    require(not destination.exists(), "package staging directory already exists")
    destination.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE.txt", "Licence.txt", "THIRD_PARTY_NOTICES.md"):
        atomic_write(destination / name, (release / name).read_bytes())
    for path in (release / "src").rglob("*.py"):
        target = destination / path.relative_to(release)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, path.read_bytes())
    return {"package_staging": str(destination), "runtime_files": len(list((destination / "src").rglob("*.py")))}


def self_test():
    """Tiny structural tests, retaining their generated fixtures for inspection."""
    root = Path(tempfile.mkdtemp(prefix="softwarex-builder-tests-", dir=ROOT / "tmp"))
    checks = 0
    require(len(SUBMISSION_ARCHIVES) == len(set(SUBMISSION_ARCHIVES)) == 2 and
            all(name.startswith("output/submission/") and name.endswith(".zip")
                for name in SUBMISSION_ARCHIVES), "unexpected submission archive allowlist")
    for name in SUBMISSION_ARCHIVES:
        safe_name(name)
    checks += 1
    for name in ("../bad", "/absolute", "x\\y", "x:stream", "private/key", "a/.git/config", "workspace/data", ".zerorun-env/key"):
        try:
            safe_name(name)
        except ValueError:
            checks += 1
        else:
            raise AssertionError("unsafe path accepted")
    source, target = root / "source", root / "target"
    source.write_bytes(b"original")
    os.link(source, target)
    atomic_write(target, b"changed")
    require(source.read_bytes() == b"original" and target.read_bytes() == b"changed", "refresh changed hardlinked source")
    checks += 1
    for case in ("valid", "git-metadata", "tampered", "unlisted", "missing"):
        directory = root / case
        directory.mkdir()
        (directory / "payload").write_bytes(b"original")
        record = {"files": [{"path": "payload", "bytes": 8, "sha256": digest(b"original")}]}
        if case == "missing":
            record["files"][0]["path"] = "absent"
        (directory / MANIFEST).write_text(json.dumps(record), encoding="utf-8")
        if case == "tampered":
            (directory / "payload").write_bytes(b"tampered")
        if case == "unlisted":
            (directory / "unlisted").write_bytes(b"extra")
        if case == "git-metadata":
            (directory / ".git").mkdir()
            (directory / ".git/config").write_bytes(b"local VCS metadata is not distributed")
        try:
            inspect(directory)
        except ValueError:
            require(case not in {"valid", "git-metadata"}, "valid inventory rejected")
        else:
            require(case in {"valid", "git-metadata"}, "invalid inventory accepted")
        checks += 1
    return {"self_tests_passed": checks, "fixtures": str(root)}


def verify_install(release, wheel, executable):
    """Record exact wheel/source equality and isolated installed CLI execution."""
    manifest = inspect(release)
    with zipfile.ZipFile(wheel) as archive:
        names = [name for name in archive.namelist() if name.startswith("zerorun/") and name.endswith(".py")]
        require(len(names) == len(set(names)) == 36, "unexpected wheel runtime inventory")
        require(all(archive.read(name) == (release / "src" / name).read_bytes() for name in names), "wheel changed runtime source bytes")
        metadata = archive.read("zerorun-0.5.1.dist-info/METADATA").decode()
        parsed_metadata = Parser().parsestr(metadata)
        require(parsed_metadata.get("License-Expression") == "MIT" and not parsed_metadata.get_all("Requires-Dist"), "wheel license/dependency mismatch")
    script = "import json, pathlib, zerorun; print(json.dumps({'version':zerorun.__version__, 'files':{p.name:__import__('hashlib').sha256(p.read_bytes()).hexdigest() for p in pathlib.Path(zerorun.__file__).parent.glob('*.py')}}))"
    result = subprocess.run([str(executable.resolve()), "-I", "-B", "-c", script], cwd=ROOT / "tmp", capture_output=True, text=True, timeout=30, check=True)
    installed = json.loads(result.stdout)
    expected = {Path(name).name: digest((release / "src" / name).read_bytes()) for name in names}
    require(installed == {"version": "0.5.1", "files": expected}, "installed package differs from release")
    help_result = subprocess.run([str(executable.resolve()), "-I", "-B", "-m", "zerorun", "--help"], cwd=ROOT / "tmp", capture_output=True, text=True, timeout=30, check=True)
    require("mcp-server" in help_result.stdout and "authorize" in help_result.stdout, "installed CLI smoke missing expected interface")
    receipt = {"schema": "zerorun.softwarex-public-install-smoke.v1", "status": "PASS",
        "core_commit": CORE, "wheel_sha256": digest(wheel.read_bytes()), "wheel_bytes": wheel.stat().st_size,
        "wheel_runtime_files_exact": 36, "installed_runtime_files_exact": 36,
        "runtime_dependencies": [], "license_expression": "MIT", "python_isolated_mode": True,
        "cli_help_exit_code": help_result.returncode, "cli_help_sha256": digest(help_result.stdout.encode()),
        "release_manifest_at_test_sha256": digest((release / MANIFEST).read_bytes()),
        "packaging_adaptations": manifest["packaging_adaptations"], "platform": os.name,
        "limits": "Clean local Windows wheel installation and CLI help; not Docker timing, end-to-end Codex task, or runtime qualification.",
        "prior_environment_failures": "Initial restricted-token wheel build and ensurepip blocked by Windows temporary-directory ACL; retained separately; repeated with normal permissions in a fresh environment."}
    destination = ROOT / "research/softwarex/generated/public-install-smoke.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(not destination.exists(), "install smoke receipt already exists")
    atomic_write(destination, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    return receipt


def record_public_tests(release):
    """Bind the two retained public-layout test runs, not future added tests."""
    manifest = inspect(release)
    require(digest((release / MANIFEST).read_bytes()) ==
            "f12d96cf95da0cb3a2f43372443c39e0a16de3b173c86958c2da73c8bd1a1acb",
            "test receipt must bind the actual tested staging inventory")
    runs = []
    for number, expected_hash, failures, passed in (
        (1, "129ce45bb54f655d1c3c0fb68e627eb628fef41672d2e421ba07eec4b4af91f0", 20, 445),
        (2, "d8ed8dfe23c3cf6691f14145d05ea7713df11e9280d95f9cb0f3d77df5815a70", 0, 465),
    ):
        raw = read_local(ROOT / f"tmp/softwarex-public-tests-{number}.xml")
        require(digest(raw) == expected_hash, "retained JUnit bytes changed")
        suite = ET.fromstring(raw).find("testsuite")
        require(suite is not None and all(int(suite.get(key)) == count for key, count in
                {"tests": 494, "failures": failures, "errors": 0, "skipped": 21}.items()),
                "unexpected test denominator")
        skips = [node.get("message", "") for node in suite.findall("testcase/skipped")]
        require(len(skips) == 21 and sum("POSIX" in text for text in skips) == 10,
                "unexpected platform skip classification")
        runs.append({"junit": f"research/softwarex/evidence/public-release-tests-{number}.xml",
            "sha256": expected_hash, "junit_cases_including_subtests": 494,
            "pytest_passed": passed, "pytest_failed": failures, "pytest_skipped": 21,
            "subtests_passed": 8, "errors": 0, "seconds_junit": float(suite.get("time")),
            "started_local": suite.get("timestamp"),
            "skip_categories": {"POSIX_permission_semantics_unavailable": 10,
                                "Windows_symlink_privilege_unavailable": 11},
            "basetemp": (str(ROOT / "tmp/softwarex-public-tests-1") if number == 1 else
                         "C:/Users/floxy/AppData/Local/Temp/zerorun-softwarex-public-tests-2")})
    tested_code = [row for row in manifest["files"] if row["path"].startswith(
        ("src/zerorun/", "tests/", "research/sqj/strengthening/tests/"))]
    require(sum(row["path"].startswith("src/zerorun/") for row in tested_code) == 36,
            "unexpected frozen runtime denominator")
    receipt = {"schema": "zerorun.softwarex-public-release-tests.v1", "status": "PASS_WITH_PLATFORM_SKIPS",
        "platform": "Windows", "python": "CPython 3.14", "core_commit": CORE,
        "tested_manifest_sha256": digest((release / MANIFEST).read_bytes()), "tested_code": tested_code,
        "source_verification": "Exact staged file bytes verified against the tested manifest; 36 runtime files originate unchanged from frozen core commit.",
        "command": "python -B -m pytest tests research/sqj/strengthening/tests -q -p no:cacheprovider --basetemp=<recorded per run> --junitxml=<retained XML>",
        "corrected_environment": {"PYTHONPATH": str(release / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
        "runs": runs,
        "first_failure_reason": "The first basetemp was inside the development Git checkout. Synthetic empty .git fixtures discovered the parent checkout; canonical-root trust correctly refused it. The repeated run changed only fixture placement/environment, not runtime or test assertions.",
        "warnings": "Three pytest record_property warnings with JUnit xunit2; raw XML retained.",
        "limits": "Selected public-layout regression and research tests, not the entire development suite, Docker timing, or end-to-end AI task completion. Subsequent added research drivers/tests are not retroactively covered by this run."}
    destination = ROOT / "research/softwarex/generated/public-release-tests.json"
    require(not destination.exists(), "public test receipt already exists")
    atomic_write(destination, (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())
    return {"receipt": str(destination), "passed": 465, "skipped": 21, "subtests_passed": 8,
            "tested_files": len(tested_code), "sha256": digest(destination.read_bytes())}


def build(directory, refresh=False, paper_files=(), hardlink_identical=False, plan=False, current_core=None):
    directory = directory.absolute()
    require(directory.name.startswith("softwarex-public-release") and directory.parent.resolve() == (ROOT / "tmp").resolve(),
            "output must be a named softwarex-public-release directory immediately under workspace tmp")
    require(not directory.is_symlink() and directory.resolve().is_relative_to((ROOT / "tmp").resolve()), "output escapes workspace tmp")
    payloads, manifest = collect(paper_files, current_core=current_core)
    if plan:
        return {"planned_files": len(payloads), "planned_bytes": sum(map(len, payloads.values())), "output": str(directory)}
    if directory.exists():
        require(refresh, "existing release requires explicit --refresh")
        old = inspect(directory)
        require({row["path"] for row in old["files"]} <= set(payloads), "refresh would remove a prior file; use a fresh output directory")
    else:
        directory.mkdir(parents=True)
    origins = {row["path"]: row["origin"] for row in manifest["files"]}
    hardlinked = 0
    for name, raw in sorted(payloads.items(), key=lambda item: item[0] == MANIFEST):
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() == raw:
            continue
        immutable_evidence = name.startswith(("research/sqj/evidence/", "research/sqj/strengthening/evidence/"))
        origin = origins.get(name, "")
        if hardlink_identical and immutable_evidence and origin.startswith("workspace:"):
            source = ROOT / origin.removeprefix("workspace:")
            atomic_write(path, raw, source)
            hardlinked += 1
        else:
            atomic_write(path, raw)
    inspect(directory)
    return {"files": len(payloads), "bytes": sum(len(raw) for raw in payloads.values()),
            "manifest_sha256": digest(payloads[MANIFEST]), "core_commit": CORE, "output": str(directory),
            "new_immutable_evidence_hardlinks": hardlinked, "refresh_replacements_are_atomic": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/softwarex-public-release")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--paper-file", action="append", default=[])
    parser.add_argument("--hardlink-identical", action="store_true", help="save disk space with immutable evidence hardlinks only")
    parser.add_argument("--plan", action="store_true", help="validate inputs and report size without writing the release")
    parser.add_argument("--self-test", action="store_true", help="run tiny structural and hardlink-refresh safety tests")
    parser.add_argument("--stage-package", type=Path, help="copy small build inputs to a new tmp/softwarex-package-smoke-* directory")
    parser.add_argument("--verify-wheel", type=Path)
    parser.add_argument("--installed-python", type=Path)
    parser.add_argument("--record-public-tests", action="store_true")
    parser.add_argument("--current-core", help="Explicit reviewed 0.5.2 commit; omitting it builds the historical 0.5.1 package")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test()))
    elif args.record_public_tests:
        print(json.dumps(record_public_tests(args.output)))
    elif args.verify_wheel:
        require(args.installed_python is not None, "--verify-wheel requires --installed-python")
        print(json.dumps(verify_install(args.output, args.verify_wheel, args.installed_python)))
    elif args.stage_package:
        print(json.dumps(stage_package(args.output, args.stage_package)))
    elif args.check:
        manifest = inspect(args.output)
        print(json.dumps({"validated": True, "files": len(manifest["files"]) + 1, "core_commit": manifest["frozen_core_commit"]}))
    else:
        print(json.dumps(build(args.output, args.refresh, args.paper_file, args.hardlink_identical, args.plan, args.current_core)))


if __name__ == "__main__":
    main()
