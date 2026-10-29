"""Early live MCP falsification of source-state qualification enforcement.

Only the deterministic A fixture is qualified. B is an intentionally unreviewed
but benign deterministic edit. No nondeterministic program is executed here.
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
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from zerorun.manifest import load_manifest
from zerorun import trust
from research.q1q2 import qualification_gate as gate

IMAGE = "python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a"
A = "VALUE = 1\nassert VALUE == 1\n"
B = "VALUE = 2\nassert VALUE == 2\n"


def invoke(repo, env, *, verify=False):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "zerorun-scope-probe", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "run_tests", "arguments": {"task": "tests", "verify": verify}}},
    ]
    started = time.perf_counter_ns()
    process = subprocess.run([sys.executable, "-B", "-m", "zerorun", "mcp-server"],
                             cwd=repo, env=env, input="\n".join(map(json.dumps, messages)) + "\n",
                             text=True, capture_output=True, timeout=90)
    wall = (time.perf_counter_ns() - started) / 1e9
    replies = [json.loads(line) for line in process.stdout.splitlines() if line.strip()]
    reply = next((row for row in replies if row.get("id") == 2), {})
    result = reply.get("result", {})
    contents = result.get("content", [])
    payload = json.loads(contents[0]["text"]) if contents else None
    return {"outer_seconds": wall, "returncode": process.returncode,
            "stdout": process.stdout, "stderr": process.stderr,
            "payload": payload, "error": reply.get("error")}


def main():
    output = Path(sys.argv[1])
    guarded = "--guarded" in sys.argv[2:]
    report = {"schema": "zerorun.q1q2.live-qualification-probe.v1",
              "utc": datetime.now(timezone.utc).isoformat(),
              "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "python": platform.python_version(), "image": IMAGE, "rows": [],
              "source_state_gate": guarded,
              "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((ROOT / "src/zerorun").glob("*.py"))},
              "interpretation": "Unreviewed B admission tests scope enforcement, not false success or nondeterministic replay."}
    try:
        with tempfile.TemporaryDirectory(prefix="zerorun-live-scope-") as folder:
            base = Path(folder)
            repo = base / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            program = repo / "check.py"
            program.write_text(A)
            task = {"command": ["python", "check.py"], "inputs": ["check.py"],
                    "outputs": [], "env": [], "cacheable": True, "unsafe_effects": [],
                    "cache_streams": False, "image": IMAGE, "platform": "linux/amd64",
                    "result_only": True, "closure_reviewed": True}
            manifest_path = repo / ".zerorun.json"
            manifest_path.write_text(json.dumps({"version": 2, "tasks": {"tests": task}}))
            os.environ["ZERORUN_TRUST_ROOT"] = str(base / "private-authority")
            env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            report["rows"].append({"stage": "A_without_authority", **invoke(repo, env)})
            manifest = load_manifest(manifest_path)
            trust.authorize_manifest(manifest, expected_manifest_sha256=trust.manifest_sha256(manifest))
            qualification_path = base / "operator-scope.json"
            if guarded:
                key, _ = gate.identity(repo, manifest_path)
                gate.qualify(repo, manifest_path, qualification_path,
                             rationale="Only the fixed arithmetic fixture A and its exact restoration were inspected.",
                             expected_identity=key)
            for stage, source, verify in [
                ("A_producer", A, False), ("A_consumer", A, False),
                ("unreviewed_B_producer", B, False), ("unreviewed_B_consumer", B, False),
                ("restored_A_consumer", A, False), ("restored_A_fresh_check", A, True),
            ]:
                program.write_text(source)
                started = time.perf_counter()
                invoked = False
                response = None
                try:
                    before = gate.require(repo, manifest_path, qualification_path) if guarded else None
                    invoked = True
                    response = invoke(repo, env, verify=verify)
                    observed = response
                    after = gate.require(repo, manifest_path, qualification_path) if guarded else None
                    if before != after:
                        raise gate.QualificationRequired("source changed during MCP request")
                except gate.QualificationRequired as error:
                    observed = {"deployment_status": "QUALIFICATION_REQUIRED", "reason": str(error),
                                "payload": None, "mcp_invoked": invoked,
                                "discarded_mcp_response": response}
                observed["guarded_outer_seconds"] = time.perf_counter() - started
                report["rows"].append({"stage": stage,
                                       "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                                       "manifest_sha256": trust.manifest_sha256(load_manifest(manifest_path)),
                                       **observed})
            report["completed"] = True
            if guarded:
                observed_statuses = [row.get("deployment_status") or (row.get("payload") or {}).get("status")
                                     for row in report["rows"]]
                report["gate_check_passed"] = observed_statuses == [
                    "ERROR", "MISS_EXECUTED", "HIT_REUSED", "QUALIFICATION_REQUIRED",
                    "QUALIFICATION_REQUIRED", "HIT_REUSED", "VERIFY_MATCH"]
    except Exception as error:
        report["completed"] = False
        report["execution_error"] = {"type": type(error).__name__, "message": str(error)}
    output.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["completed"] and report.get("gate_check_passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
