"""Prospective, all-attempt pilot for the matched three-arm cost study."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from zerorun import trust
from zerorun.manifest import load_manifest
from research.q1q2 import qualification_gate as gate
from research.q1q2.prepare_runtime import prepare

COMMAND = ["python", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "q1q2_capture", "tests"]


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2))


def extract_outcomes(response):
    text = response.get("stdout_tail", "")
    rows = [line.split("Q1Q2_OUTCOMES=", 1)[1] for line in text.splitlines() if "Q1Q2_OUTCOMES=" in line]
    if len(rows) != 1:
        return None
    row = json.loads(rows[0])
    row["collected"] = sorted(row["collected"])
    row["reports"] = sorted(row["reports"], key=lambda x: (x[0], x[1]))
    return row


def monitor(path, stop):
    with path.open("w") as output:
        while not stop.is_set():
            row = {"utc": utc(), "monotonic": time.monotonic(), "loadavg": os.getloadavg(),
                   "stat": Path("/proc/stat").read_text(), "uptime": Path("/proc/uptime").read_text()}
            output.write(json.dumps(row) + "\n")
            output.flush()
            stop.wait(1)


def acquire(target, destination, output):
    started = time.perf_counter()
    row = {**target, "started_utc": utc(), "commands": []}
    try:
        destination.mkdir(parents=True)
        for args in (["git", "init", "-q", str(destination)],
                     ["git", "-C", str(destination), "remote", "add", "origin", "https://github.com/" + target["repo"] + ".git"],
                     ["git", "-C", str(destination), "fetch", "--depth", "1", "origin", target["sha"]],
                     ["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"]):
            step = time.perf_counter()
            p = subprocess.run(args, capture_output=True, text=True, timeout=120)
            row["commands"].append({"argv": args, "returncode": p.returncode, "stdout": p.stdout,
                                    "stderr": p.stderr, "outer_seconds": time.perf_counter() - step})
            if p.returncode:
                raise RuntimeError("source acquisition failed")
        actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
        assert actual == target["sha"]
        inventory = gate.inventory(destination)
        size = sum(x.get("bytes", 0) for x in inventory)
        if size > 50_000_000:
            raise ValueError("source budget exceeded")
        archive = output / "source.tar.gz"
        with tarfile.open(archive, "w:gz") as package:
            for child in sorted(destination.iterdir()):
                if child.name != ".git":
                    package.add(child, arcname=child.name, recursive=True)
        row.update(status="acquired", inventory=inventory, bytes=size,
                   archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        return True
    except Exception as error:
        row.update(status="source_failure", error=type(error).__name__ + ": " + str(error))
        return False
    finally:
        row["outer_seconds"] = time.perf_counter() - started
        save(output / "acquisition.json", row)


def initialize(source, root, image, arm, authority):
    started = time.perf_counter()
    shutil.copytree(source, root, ignore=lambda folder, names: {".git", ".zerorun"}.intersection(names) if Path(folder) == source else set())
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".zerorun").mkdir()
    inputs = sorted(p.name for p in root.iterdir() if p.name not in gate.EXCLUDED_ROOT)
    task = {"command": COMMAND, "inputs": inputs, "outputs": [], "env": [],
            "cacheable": True, "unsafe_effects": [], "cache_streams": False,
            "image": image, "platform": "linux/amd64", "result_only": True, "closure_reviewed": True}
    manifest_path = root / ".zerorun.json"
    save(manifest_path, {"version": 2, "tasks": {"tests": task}})
    common_seconds = time.perf_counter() - started
    start_authority = time.perf_counter()
    authority.mkdir(parents=True, exist_ok=False)
    os.chmod(authority, 0o700)
    os.environ["ZERORUN_TRUST_ROOT"] = str(authority / "native")
    if arm == "zerorun":
        manifest = load_manifest(manifest_path)
        trust.authorize_manifest(manifest, expected_manifest_sha256=trust.manifest_sha256(manifest))
    if arm in {"zerorun", "receipt"}:
        key, _ = gate.identity(root, manifest_path)
        gate.qualify(root, manifest_path, authority / "scope.json", expected_identity=key,
                     rationale="Exploratory source replay under explicit conditional outcome-determinism assumption; see PILOT_PROTOCOL.md and qualification dossier. This is not human certification.")
    if arm == "receipt":
        fd = os.open(authority / "receipt.key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(secrets.token_bytes(32))
    return {"materialization_seconds": common_seconds, "arm_setup_seconds": time.perf_counter() - start_authority,
            "manifest": task, "authority": str(authority), "root": str(root)}


def invoke(root, arm, authority, verify=False):
    started = time.perf_counter()
    row = {"utc": utc(), "arm": arm, "verify": verify}
    try:
        scoped = arm in {"zerorun", "receipt"}
        before = gate.require(root, root / ".zerorun.json", authority / "scope.json") if scoped else None
        request = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "q1q2-study", "version": "1"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "run_tests", "arguments": {"task": "tests", "verify": verify}}}]
        argv = [sys.executable, "-B", "-m", "zerorun", "mcp-server"] if arm == "zerorun" else [
            sys.executable, "-B", str(Path(__file__).with_name("reference_service.py")), arm, str(authority / "receipt.key")]
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "ZERORUN_TRUST_ROOT": str(authority / "native")}
        p = subprocess.run(argv, input="\n".join(map(json.dumps, request)) + "\n", cwd=root, env=env,
                           capture_output=True, text=True, timeout=1100)
        row.update(argv=argv, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
        replies = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        response = next(x for x in replies if x.get("id") == 2)
        payload = json.loads(response["result"]["content"][0]["text"])
        row["payload"] = payload
        after = gate.require(root, root / ".zerorun.json", authority / "scope.json") if scoped else None
        if before != after:
            raise RuntimeError("source qualification changed; response discarded")
        row["disposition"] = "service_error" if payload.get("status") == "ERROR" else "completed"
    except Exception as error:
        row.update(disposition="request_error", error=type(error).__name__ + ": " + str(error))
    row["outer_seconds"] = time.perf_counter() - started
    return row


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    report = {"schema": "zerorun.q1q2.cost-pilot.v1", "started_utc": utc(), "status": "running",
              "evidence_class": "exploratory", "targets": [], "host": platform.uname()._asdict(),
              "python": sys.version, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}}
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(output / "host.jsonl", stop), daemon=True)
    thread.start()
    try:
        image = prepare(output / "runtime")
        report["image"] = image
        targets = json.loads((Path(__file__).with_name("pilot_targets.json")).read_text())
        for target in targets:
            folder = output / target["id"]
            folder.mkdir()
            source = folder / "acquired"
            target_report = {**target, "blocks": [], "status": "begun"}
            report["targets"].append(target_report)
            if not acquire(target, source, folder):
                target_report["status"] = "source_failure"
                continue
            # Full-tree and staged semantic preflight. Every attempt is kept.
            preflight = {}
            for arm in ("fresh", "receipt", "zerorun"):
                root = folder / ("preflight-" + arm)
                authority = folder / ("authority-preflight-" + arm)
                setup = initialize(source, root, image, arm, authority)
                response = invoke(root, arm, authority)
                preflight[arm] = {"setup": setup, "response": response,
                                  "outcomes": extract_outcomes(response.get("payload", {}))}
                save(folder / ("preflight-" + arm + ".json"), preflight[arm])
            expected = preflight["fresh"]["outcomes"]
            valid = bool(expected and expected["collected"] and expected["exit_code"] == 0 and all(
                preflight[arm]["outcomes"] == expected for arm in preflight))
            target_report["preflight_equal_nonempty_success"] = valid
            if not valid:
                target_report["status"] = "preflight_failed"
                continue
            for block_id, order in enumerate([("fresh", "receipt", "zerorun"), ("zerorun", "fresh", "receipt")]):
                block = {"block": block_id, "order": order, "arms": {}}
                target_report["blocks"].append(block)
                for arm in order:
                    root = folder / f"block-{block_id}-{arm}"
                    authority = folder / f"authority-{block_id}-{arm}"
                    setup = initialize(source, root, image, arm, authority)
                    calls = []
                    for request_id in range(5):
                        row = invoke(root, arm, authority)
                        row["request"] = request_id
                        calls.append(row)
                        save(folder / f"block-{block_id}-{arm}-request-{request_id}.json", row)
                    block["arms"][arm] = {"setup": setup, "calls": calls}
                    if any(x.get("payload", {}).get("exit_code") != 0 for x in calls):
                        raise RuntimeError("pilot timed request failed; retain and investigate")
                save(folder / f"block-{block_id}.json", block)
            target_report["status"] = "completed"
        report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error=type(error).__name__ + ": " + str(error), traceback=traceback.format_exc())
    finally:
        stop.set()
        thread.join(timeout=5)
        report.update(completed_utc=utc(), total_outer_seconds=time.perf_counter() - started)
        save(output / "report.json", report)
        print("Q1Q2_PILOT_REPORT_BEGIN")
        print(json.dumps(report, sort_keys=True))
        print("Q1Q2_PILOT_REPORT_END")
    return int(report["status"] != "completed")


if __name__ == "__main__":
    raise SystemExit(main())
