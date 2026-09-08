"""Read-only verification of the completed public submission snapshot."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
CURRENT_RECEIPT = "research/softwarex/evidence/current-runtime-0.5.3-v1/receipt.json"
CURRENT_QUICKSTART = "research/softwarex/evidence/quickstart-public-053-v1"
MAX_OUTPUT = 16000
CHECK_TIMEOUT_SECONDS = 120


def require(condition, message):
    if not condition:
        raise ValueError(message)


def manifest_check(root):
    from research.softwarex import build_public_release as release
    from research.softwarex.build_submission_artifacts import strict_json, read_regular
    raw = read_regular(root / release.MANIFEST)
    saved = strict_json(raw)
    rows = saved["files"]
    require(isinstance(rows, list) and rows and len(rows) == len({r["path"] for r in rows}), "public manifest rows absent/duplicated")
    require(saved.get("current_version") != release.CURRENT_VERSION or len(release.external_artifacts(saved)) == 1,
            "complete " + release.CURRENT_VERSION + " submission requires its exact external reviewer ZIP declaration")
    actual = release.inspect(root, require_external=True)
    require(actual == saved, "public manifest changed during verification")
    return {"manifest_sha256": release.digest(raw), "payload_files": len(rows),
            "external_artifacts_required": release.external_artifacts(saved),
            "scope": "exact complete payload and hashes; .git administration excluded"}


def artifact_check(root):
    from research.softwarex import build_submission_artifacts as a
    def read(relative):
        return a.strict_json(a.read_regular(root / relative))
    def sha(relative):
        return a.digest(a.read_regular(root / relative))
    prefix = "research/softwarex/"
    record = read(prefix + "generated/artifact-build.json")
    qa = read(prefix + "generated/pdf-review.json")
    evidence = read(prefix + "generated/paper-evidence.json")
    require(record["schema"] == "zerorun.softwarex-artifact-build.v1" and record["completed"] is True,
            "completed artifact receipt missing")
    require(record["builder_sha256"] == sha(prefix + "build_submission_artifacts.py"), "artifact builder binding is stale")
    require(record["paper_evidence_sha256"] == sha(prefix + "generated/paper-evidence.json")
            and record["pdf_review_sha256"] == sha(prefix + "generated/pdf-review.json"), "article/review receipt binding is stale")
    pdf = "output/pdf/zerorun-softwarex.pdf"
    require(record["pdf_sha256"] == qa["pdf_sha256"] == sha(pdf), "final PDF bytes differ")
    require(qa["all_pages_visually_reviewed"] is True and qa["word_limit_pass"] is True
            and qa["unresolved_references"] is False, "saved visual/editorial review incomplete")
    sources = {name: sha(prefix + "paper/" + name) for name in a.SOURCE_FILES}
    require(sources == record["compilation_source_sha256"] == qa["compilation_source_sha256"], "compilation source differs from reviewed PDF source")
    require(sources["main.tex"] == evidence["main_tex_sha256"] and sources["references.bib"] == evidence["bibliography_sha256"], "manuscript evidence is stale")
    archives = {}
    for name, relative in (("source_archive", "output/submission/SoftwareX_source.zip"),
                           ("reviewer_archive", "output/submission/ZeroRun_SoftwareX_reviewer.zip")):
        require(record[name]["path"] == relative, "unexpected submission archive target")
        actual = a.verify(root / relative)
        # The archive reader may be imported from a development checkout during
        # offline unit tests; the supported CLI always runs inside this release.
        actual["path"] = relative
        require(actual == record[name], "submission archive no longer matches current receipt")
        archives[name] = actual
    require(all(record[key] is False for key in ("journal_submitted", "payment_made", "private_history_included", "private_authority_included")), "artifact scope declaration changed")
    return {"pdf_sha256": record["pdf_sha256"], "archives": archives,
            "visual_qa": "saved reviewed-PDF hash reconciled; this command does not perform a new visual review"}


def commands():
    """Only explicit --check routes: never commands embedded in evidence."""
    return [
        ("trace_inventory", "research.sqj.strengthening.validate_traces", ["--check"]),
        ("comparison", "research.sqj.analyze_comparison", ["--check"]),
        ("canonical_analysis", "research.softwarex.analysis_reproduction", ["--check"]),
        ("state_rejoin", "research.sqj.strengthening.analyze_state_rejoin", ["--directory", "research/sqj/strengthening/evidence/agent-state-rejoin-v3",
            "--replication-directory", "research/sqj/strengthening/evidence/short-randomized-replication-v1",
            "--output", "research/sqj/strengthening/evidence/state-rejoin-analysis-v1.json", "--check"]),
        ("operating_region", "research.softwarex.analyze_operating_region", ["--check"]),
        ("extension", "research.softwarex.build_extension_evidence", ["--check"]),
        ("application", "research.softwarex.build_application_evidence", ["--check"]),
        ("handoff", "research.softwarex.build_handoff_evidence", ["--check"]),
        ("actual_agent_application", "research.softwarex.build_agent_application_evidence", ["--check"]),
        ("current_runtime", "research.softwarex.five_hour_review.current_runtime_053", ["--release", ".", "--check", CURRENT_RECEIPT]),
        ("current_quickstart", "research.softwarex.quickstart_053", ["--source-root", ".", "--check",
            CURRENT_QUICKSTART + "/check.json", "--installation-receipt", CURRENT_QUICKSTART + "/install.json"]),
        ("paper", "research.softwarex.build_paper", ["--check"]),
    ]


def check_command(root, module, args):
    require("--check" in args, "only read-only checker modes are permitted")
    script = root.joinpath(*module.split(".")).with_suffix(".py")
    require(script.is_file(), "required offline checker is missing: " + module)
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONPATH"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = os.pathsep.join((str(root / "src"), str(root)))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    argv = [sys.executable, "-B", "-s", "-m", module, *args]
    try:
        result = subprocess.run(argv, cwd=root, env=environment, capture_output=True, timeout=CHECK_TIMEOUT_SECONDS)
        value = {"argv": argv, "returncode": result.returncode,
                 "stdout_tail": result.stdout[-MAX_OUTPUT:].decode(errors="replace"),
                 "stderr_tail": result.stderr[-MAX_OUTPUT:].decode(errors="replace"),
                 "output_truncated": len(result.stdout) > MAX_OUTPUT or len(result.stderr) > MAX_OUTPUT}
        require(result.returncode == 0, "offline checker failed: " + module + ": " + value["stderr_tail"][-3000:])
        return value
    except subprocess.TimeoutExpired as error:
        raise ValueError("offline checker exceeded " + str(CHECK_TIMEOUT_SECONDS) + " seconds: " + module) from error


def verify(root=ROOT):
    from research.softwarex.analysis_reproduction import require_canonical_python
    root = Path(root).resolve(strict=True)
    started = time.perf_counter()
    rows = []
    def check(name, function):
        before = time.perf_counter()
        row = {"check": name, "passed": False, "result": None, "error": None}
        try:
            row["result"] = function()
            row["passed"] = True
        except Exception as error:
            row["error"] = {"type": type(error).__name__, "message": str(error)}
        row["elapsed_seconds"] = time.perf_counter() - before
        rows.append(row)
        return row["passed"]
    if check("canonical_python", require_canonical_python) and check("release_manifest_before", lambda: manifest_check(root)):
        check("submission_artifacts", lambda: artifact_check(root))
        for name, module, args in commands():
            check(name, lambda module=module, args=args: check_command(root, module, args))
        initial_manifest = rows[1]["result"]
        def unchanged_manifest():
            after = manifest_check(root)
            require(after == initial_manifest, "public manifest changed between checks")
            return after
        check("release_manifest_after", unchanged_manifest)
    return {"schema": "zerorun.public-submission-verification.v1", "passed": all(r["passed"] for r in rows),
        "checks": rows, "elapsed_seconds": time.perf_counter() - started,
        "scope": "read-only saved artifact/evidence verification; no install, new tests, Docker, network, model, authority change, manuscript regeneration or readiness builder",
        "output": "stdout only", "analysis_python_required": "CPython 3.12-3.14",
        "journal_acceptance_estimated": False, "independent_human_reproduction_claimed": False}


def main():
    result = verify()
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
