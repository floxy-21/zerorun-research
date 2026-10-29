"""Observe source-state scope of the unmodified 0.5.3 authority component.

This does not execute Docker, cache a test outcome, or prove a false hit.
Private authority keys exist only inside a disposable temporary directory.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from zerorun.manifest import load_manifest
from zerorun import trust


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    states = {
        "reviewed_A": "VALUE = 1\nassert VALUE == 1\n",
        "unreviewed_deterministic_B": "VALUE = 2\nassert VALUE == 2\n",
        "unreviewed_nondeterministic_C": "import secrets\nassert secrets.randbelow(2) == 0\n",
        "restored_A": "VALUE = 1\nassert VALUE == 1\n",
    }
    rows = []
    with tempfile.TemporaryDirectory(prefix="zerorun-qualification-probe-") as temp:
        base = Path(temp)
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        task = {
            "command": ["python", "check.py"], "inputs": ["check.py"],
            "outputs": [], "env": [], "cacheable": True, "unsafe_effects": [],
            "cache_streams": False, "image": "example.invalid/python@sha256:" + "a" * 64,
            "platform": "linux/amd64", "result_only": True, "closure_reviewed": True,
        }
        path = repo / ".zerorun.json"
        path.write_text(json.dumps({"version": 2, "tasks": {"tests": task}}))
        os.environ["ZERORUN_TRUST_ROOT"] = str(base / "private-authority")
        for index, (name, source) in enumerate(states.items()):
            (repo / "check.py").write_text(source)
            manifest = load_manifest(path)
            before = trust.manifest_is_authorized(manifest)
            if index == 0:
                trust.authorize_manifest(manifest, expected_manifest_sha256=trust.manifest_sha256(manifest))
            rows.append({"state": name, "source_sha256": digest(repo / "check.py"),
                         "manifest_sha256": trust.manifest_sha256(manifest),
                         "authorized_before": before,
                         "authorized_after": trust.manifest_is_authorized(manifest)})
    result = {
        "schema": "zerorun.q1q2.qualification-component-probe.v1",
        "utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "component_sha256": {str(p.relative_to(ROOT)): digest(p) for p in
                             [ROOT / "src/zerorun/trust.py", ROOT / "src/zerorun/manifest.py", Path(__file__)]},
        "rows": rows,
        "observation": "Exact-manifest authority remains valid after declared program edits, including a newly nondeterministic program.",
        "scope": "Authority component only; no program, Docker task or cache lookup was executed.",
        "interpretation": "A qualification limited to reviewed source states needs an additional state guard. Manifest authentication alone does not enforce that policy.",
        "not_established": ["an in-contract false hit", "a violation of the original frozen-state handoff claim", "population incidence"],
    }
    assert rows[0]["authorized_before"] is False
    assert all(row["authorized_after"] for row in rows)
    assert len({row["manifest_sha256"] for row in rows}) == 1
    assert rows[0]["source_sha256"] == rows[-1]["source_sha256"]
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
