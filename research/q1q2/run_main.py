"""Main study execution. Every output directory is a new, preserved attempt."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from research.q1q2.run_pilot import acquire, initialize, invoke, extract_outcomes, monitor, save, utc
from research.q1q2.prepare_runtime import prepare


def oracle(source, image, folder, command):
    root, authority = folder / "oracle-root", folder / "oracle-authority"
    setup = initialize(source, root, image, "fresh", authority, command)
    capture = folder / "oracle-full-stdout.txt"
    response = invoke(root, "fresh", authority, oracle_capture=capture)
    summary = extract_outcomes(response.get("payload", {}))
    full = capture.read_text() if capture.exists() else ""
    full_verified = bool(summary and summary["collected_count"] and "Q1Q2_OUTCOMES=" in full
                         and summary["exit_code"] == response.get("payload", {}).get("exit_code")
                         and response.get("disposition") == "completed"
                         and extract_outcomes({"stdout_tail": full}) == summary)
    return {"setup": setup, "response": response, "summary": summary, "full_verified": full_verified}


def preflight(target, source, image, folder):
    records = {"fresh": oracle(source, image, folder, target["command"])}
    expected = records["fresh"]["summary"]
    for arm in ("receipt", "zerorun"):
        root, authority = folder / (arm + "-root"), folder / (arm + "-authority")
        setup = initialize(source, root, image, arm, authority, target["command"])
        row = invoke(root, arm, authority)
        records[arm] = {"setup": setup, "response": row, "summary": extract_outcomes(row.get("payload", {}))}
    valid = bool(records["fresh"]["full_verified"] and expected["exit_code"] == 0 and all(
        records[arm]["summary"] == expected and records[arm]["response"].get("payload", {}).get("exit_code") == 0
        for arm in records))
    return {"records": records, "equal_nonempty_success": valid}


def timed_block(target, source, image, folder, schedule):
    block = {**schedule, "started_utc": utc(), "arms": {}, "status": "begun"}
    for arm in schedule["order"]:
        armfolder = folder / arm
        armfolder.mkdir()
        root, authority = armfolder / "source", armfolder / "authority"
        setup = initialize(source, root, image, arm, authority, target["command"])
        qualification = None
        expected = None
        if arm != "fresh":
            qualification = oracle(source, image, armfolder, target["command"])
            expected = qualification["summary"]
            if not (qualification["full_verified"] and expected["exit_code"] == 0):
                block["arms"][arm] = {"setup": setup, "qualification": qualification, "calls": [], "status": "qualification_failed"}
                block["status"] = "qualification_failed"
                return block
        calls = []
        block["arms"][arm] = {"setup": setup, "qualification": qualification, "calls": calls}
        for request_id in range(5):
            row = invoke(root, arm, authority)
            row["request"] = request_id
            payload = row.get("payload", {})
            wanted = "FRESH_EXECUTED" if arm == "fresh" else "MISS_EXECUTED" if request_id == 0 else "HIT_REUSED"
            row["expected_status"] = wanted
            row["correctness_passed"] = payload.get("status") == wanted and payload.get("exit_code") == 0
            if wanted != "HIT_REUSED":
                observed = extract_outcomes(payload)
                if expected is None:
                    expected = observed
                row["correctness_passed"] = row["correctness_passed"] and observed == expected and bool(observed and observed["collected_count"])
            calls.append(row)
            save(armfolder / f"request-{request_id}.json", row)
            if not row["correctness_passed"]:
                block["status"] = "correctness_failure"
                return block
        block["arms"][arm]["expected_outcomes"] = expected
    digests = [x["expected_outcomes"] for x in block["arms"].values()]
    block["status"] = "completed" if all(x == digests[0] for x in digests) else "cross_arm_disagreement"
    return block


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--block", choices=["B1", "B2"], required=True)
    parser.add_argument("--session", type=int, choices=[0, 1], default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    begin = time.perf_counter()
    here = Path(__file__).parent
    report = {"schema": "zerorun.q1q2.main.v1", "block": args.block, "session": args.session,
              "started_utc": utc(), "status": "begun", "targets": [], "host": platform.uname()._asdict(),
              "python": sys.version, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in here.iterdir() if p.suffix in {".py", ".json", ".md"}}}
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(output / "host.jsonl", stop), daemon=True)
    thread.start()
    try:
        image = prepare(output / "runtime", main=True)
        report["image"] = image
        if args.block == "B1":
            from research.q1q2.correctness_matrix import run as run_matrix
            matrix = run_matrix(image, output / "correctness")
            report["correctness_matrix"] = {"passed": matrix["passed"], "rows": [
                {"name": r["name"], "passed": r["passed"], "error": r.get("error")} for r in matrix["rows"]]}
        selection = json.loads((here / "MAIN_SELECTION.json").read_text())
        schedules = json.loads((here / "MAIN_SCHEDULE.json").read_text())["schedule"]
        for target in selection["targets"]:
            folder = output / target["id"]
            folder.mkdir()
            record = {**target, "status": "begun", "blocks": []}
            report["targets"].append(record)
            source = folder / "acquired"
            if not acquire(target, source, folder):
                record["status"] = "acquisition_failed"
                continue
            if args.block == "B1":
                pf = preflight(target, source, image, folder)
                record["preflight"] = pf
                record["status"] = "supported" if pf["equal_nonempty_success"] else "preflight_failed"
                save(folder / "preflight.json", pf)
                print(json.dumps({"target": target["id"], "status": record["status"], "arms": {
                    a: {"summary": r["summary"], "payload": r["response"].get("payload"), "error": r["response"].get("error")}
                    for a,r in pf["records"].items()}}), flush=True)
            else:
                for schedule in schedules:
                    if schedule["session"] != args.session or schedule["target"] != target["id"]:
                        continue
                    blockdir = folder / ("block-" + str(schedule["block"]))
                    blockdir.mkdir()
                    block = timed_block(target, source, image, blockdir, schedule)
                    save(blockdir / "record.json", block)
                    record["blocks"].append(block)
                    print(json.dumps({"target": target["id"], "block": schedule["block"], "status": block["status"]}), flush=True)
                    if block["status"] != "completed":
                        record["status"] = block["status"]
                        break
                if record["status"] == "begun":
                    record["status"] = "completed"
            save(output / "summary.json", report)
        report["status"] = "completed"
    except Exception as error:
        report.update(status="failed", error=type(error).__name__ + ": " + str(error), traceback=traceback.format_exc())
    finally:
        stop.set()
        thread.join(timeout=5)
        report.update(completed_utc=utc(), total_outer_seconds=time.perf_counter() - begin)
        save(output / "summary.json", report)
        print(json.dumps({"block": args.block, "status": report["status"], "error": report.get("error"),
                          "targets": [{"id": t["id"], "status": t["status"]} for t in report["targets"]]}), flush=True)
    return int(report["status"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
