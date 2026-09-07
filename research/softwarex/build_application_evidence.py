"""Reconcile the new application evidence without running tools or models.

Completion means the recorded experiments have been independently reconciled,
not that every experiment passed or that acceptance has been established.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "research/softwarex"
LIVE = "research/softwarex/evidence/application-client-v2/receipt.json"
LIVE_V3 = "research/softwarex/evidence/application-client-v3/receipt.json"
LIVE_V2_SHA256 = "007eb8344bf468b28b28cf7e7b9508dc335eaaa6bb30f40bce30b0ef046eec08"
INSTALL = "research/softwarex/evidence/quickstart-lab-v1/install.json"
QUICKSTART = "research/softwarex/evidence/quickstart-lab-v1/check.json"
PUBLIC_INSTALL = "research/softwarex/evidence/quickstart-public-v1/install.json"
PUBLIC_QUICKSTART = "research/softwarex/evidence/quickstart-public-v1/check.json"
ORIGINAL = "research/softwarex/evidence/live-client-v1/receipt.json"
ORIGINAL_SHA256 = "d1afbce8b54a2e6b65ebb10631a6e21bfc956a2096b77396d7e1a608de221879"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, message):
    if not value:
        raise ValueError(message)


def source_inputs(root=ROOT):
    """Enumerate the exact application producers and retained records, not secrets."""
    from research.softwarex.build_submission_artifacts import read_regular

    root = Path(root)
    paths = {ORIGINAL, LIVE, LIVE_V3, INSTALL, QUICKSTART, PUBLIC_INSTALL, PUBLIC_QUICKSTART,
             "research/softwarex/build_application_evidence.py",
             "research/softwarex/quickstart_check.py", "research/softwarex/QUICKSTART_LAB.md",
             "research/softwarex/tests/test_application_evidence.py",
             "research/softwarex/tests/test_quickstart_check.py"}
    for version in ("v2", "v3"):
        directory = root / ("research/softwarex/live_client_" + version)
        require(directory.is_dir() and not directory.is_symlink(), "missing regular client source directory")
        for path in directory.iterdir():
            if path.suffix in {".py", ".md"}:
                paths.add(path.relative_to(root).as_posix())
        for suffix in (".freeze.json", ".started.json", ".events.log"):
            paths.add("research/softwarex/evidence/application-client-" + version + "/receipt" + suffix)
    rows = []
    for relative in sorted(paths):
        raw = read_regular(root / relative)
        rows.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return rows


def reconcile_live(root, relative, version):
    validation = importlib.import_module("research.softwarex.live_client_" + version + ".validation")
    live_path = root / relative
    live_record = validation.strict_json(live_path.read_bytes())
    live = validation.validate_receipt(
        live_path, directory=root / ("research/softwarex/live_client_" + version))
    freeze_path = live_path.with_suffix(".freeze.json")
    freeze = validation.strict_json(freeze_path.read_bytes())
    require(freeze == live_record["freeze"], "external and embedded freeze differ")
    require(live_record["real_repository_authorized"] is False
            and live_record["runtime_modified"] is False
            and live_record["original_v1_reclassified"] is False,
            "application experiment scope changed")
    if version == "v3":
        require(live_record["original_v2_reclassified"] is False, "second adverse trial was reclassified")
    return {"path": relative, "sha256": sha(live_path),
            "freeze_path": freeze_path.relative_to(root).as_posix(),
            "freeze_sha256": sha(freeze_path),
            "fresh_oracle_calls_passed": sum(row["passed"] is True for row in live_record.get("fresh_oracles", [])),
            **live}


def build(root=ROOT):
    from research.softwarex import quickstart_check

    root = Path(root)
    require(sha(root / ORIGINAL) == ORIGINAL_SHA256,
            "original adverse live experiment changed")
    require(sha(root / LIVE) == LIVE_V2_SHA256, "second adverse live experiment changed")
    live = reconcile_live(root, LIVE, "v2")
    live_v3 = reconcile_live(root, LIVE_V3, "v3")
    installation = quickstart_check.validate_installation_receipt(root, root / INSTALL)
    quickstart = quickstart_check.validate_saved_receipt(root, root / QUICKSTART)
    public_installation = quickstart_check.validate_installation_receipt(root, root / PUBLIC_INSTALL)
    public_quickstart = quickstart_check.validate_saved_receipt(root, root / PUBLIC_QUICKSTART)
    require(all(row["passed"] is True for row in
                (installation, quickstart, public_installation, public_quickstart)),
            "complete passing installation and quickstart records required")
    require(public_installation["source_commit"] == public_quickstart["source_commit"],
            "public guide installation/check commits differ")
    return {
        "schema": "zerorun.softwarex-application-evidence.v1",
        "completed": True,
        "original_live_trial": {"path": ORIGINAL, "sha256": ORIGINAL_SHA256,
                                "preserved_without_reclassification": True},
        "model_backed_application": live,
        "model_backed_application_v3": live_v3,
        "clean_installation": {"path": INSTALL, "sha256": sha(root / INSTALL),
                               "validation": installation},
        "account_free_quickstart": {"path": QUICKSTART, "sha256": sha(root / QUICKSTART),
                                    "validation": quickstart},
        "public_guide_installation": {"path": PUBLIC_INSTALL, "sha256": sha(root / PUBLIC_INSTALL),
                                      "validation": public_installation},
        "public_guide_quickstart": {"path": PUBLIC_QUICKSTART, "sha256": sha(root / PUBLIC_QUICKSTART),
                                   "validation": public_quickstart},
        "source_files": source_inputs(root),
        "interpretation": {
            "model_and_quickstart_installations_are_distinct": True,
            "same_frozen_runtime_required": True,
            "independent_human_users": 0,
            "autonomous_issue_resolution_study": False,
            "workflow_speedup_established": False,
            "acceptance_probability_estimated": False,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    evidence = build()
    path = HERE / "generated/application-evidence-v1.json"
    raw = (json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if args.check:
        require(path.read_bytes() == raw, "application evidence is stale")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    print(json.dumps({"reconciled": True, "path": str(path),
                      "live": evidence["model_backed_application"]}))


if __name__ == "__main__":
    main()
