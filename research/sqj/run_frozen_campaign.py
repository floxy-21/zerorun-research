"""Run the existing frozen product benchmark; never grant external authority.

This driver preserves every workload's raw receipt, logs and exit status. An
exit code of one can mean a valid negative performance result, not a runner
failure. The benchmark retains responsibility for all execution and safety
checks. Workload selection and product thresholds are not configurable here.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
LOCK = "a04f815c62754114a0f0a4b7db15d02c920e7c7d006d813ac9955be00482c02c"
WORKLOADS = (
    ("packaging", "pypa/packaging", "10590c194edb33c82f84a127883d6097c56b7840", "old-regression", ("tests/test_tags.py",), ("pretend",)),
    ("pycparser", "eliben/pycparser", "10d17757e282d8af5426d6df4d55eb394042b550", "old-regression", ("tests/test_c_parser.py",), ()),
    ("colorama", "tartley/colorama", "841634ed2a0da5d5ac2d867db533da8131266cb2", "old-regression", tuple("colorama/tests/" + p for p in ("ansi_test.py", "ansitowin32_test.py", "initialise_test.py", "isatty_test.py", "winterm_test.py")), ()),
    ("click", "pallets/click", "36baa15ff831b939a22bc527cd76ce653ef6f66d", "new-unseen", ("tests/test_arguments.py",), ()),
    ("more-itertools", "more-itertools/more-itertools", "d92f081a089714e0aa92434c797fdd1a06da1290", "new-unseen", ("tests/test_more.py",), ()),
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def write_new_json(path, payload):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def command_for(args, workload, output):
    name, upstream, commit, classification, targets, extras = workload
    argv = [sys.executable, str(args.engine_root / "tools/product_generalization_benchmark.py"),
            "--root", str(args.workloads_root / name), "--workload", name,
            "--upstream-repo", upstream, "--frozen-sha", commit,
            "--holdout-class", classification, "--case-count", "20",
            "--min-compute", "5.0", "--min-p95-reduction", "50.0",
            "--engine-sha", args.base_sha, "--engine-source-sha256", args.engine_source_sha256,
            "--trial-tool-sha256", args.trial_tool_sha256, "--runtime-image", IMAGE,
            "--runtime-requirements-sha256", LOCK, "--output", str(output)]
    for target in targets:
        argv.extend(("--target", target))
    for extra in extras:
        argv.extend(("--extra-requirement", extra))
    return argv


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--workloads-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--engine-source-sha256", required=True)
    parser.add_argument("--trial-tool-sha256", required=True)
    parser.add_argument("--total-timeout-seconds", type=int, default=14400)
    args = parser.parse_args(argv)
    if os.name != "posix" or args.total_timeout_seconds <= 0:
        parser.error("requires a POSIX host and a positive campaign timeout")
    args.engine_root = args.engine_root.resolve(strict=True)
    args.workloads_root = args.workloads_root.resolve(strict=True)
    args.output_root = args.output_root.absolute()
    args.output_root.mkdir(parents=False, exist_ok=False)
    started = time.monotonic()
    manifest = {"schema": "zerorun.bounded-campaign.v1", "started_utc": utc_now(),
                "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "external_authority_created": False, "operator_review_claimed": False,
                "independent_holdout_claimed": False,
                "historical_classification_note": "new-unseen labels are legacy cohort labels; these workloads have since been observed",
                "selected_workloads": [w[0] for w in WORKLOADS],
                "expected_engine_source_sha256": args.engine_source_sha256,
                "expected_trial_tool_sha256": args.trial_tool_sha256,
                "total_timeout_seconds": args.total_timeout_seconds}
    write_new_json(args.output_root / "campaign-plan.json", manifest)
    rows = []
    for workload in WORKLOADS:
        name = workload[0]
        remaining = args.total_timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            rows.append({"workload": name, "status": "NOT_EXECUTED_TIME_BUDGET"})
            continue
        output = args.output_root / (name + ".json")
        command = command_for(args, workload, output)
        row = {"workload": name, "started_utc": utc_now(), "command": command}
        write_new_json(args.output_root / (name + "-invocation.json"), row)
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never", PYTHONDONTWRITEBYTECODE="1")
        print(f"START {name} {row['started_utc']}", flush=True)
        with (args.output_root / (name + ".log")).open("xb") as log:
            process = subprocess.Popen(command, cwd=args.engine_root, env=environment,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                row["process_exit_code"] = process.wait(timeout=remaining)
                row["status"] = "PROCESS_COMPLETED"
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                row.update(status="TIMEOUT", process_exit_code=process.returncode)
        row["completed_utc"] = utc_now()
        if output.is_file():
            raw = output.read_bytes()
            row["raw_receipt_sha256"] = hashlib.sha256(raw).hexdigest()
            try:
                result = json.loads(raw)
                for key in ("benchmark_error", "safety_pass", "performance_gate_pass",
                            "same_runner_compute_efficiency", "p95_reduction_percent",
                            "reuse_rate_percent", "case_count", "stale_successes", "shadow_mismatches"):
                    if key in result:
                        row[key] = result[key]
            except (ValueError, TypeError) as exc:
                row["receipt_parse_error"] = str(exc)
        else:
            row["receipt_missing"] = True
        write_new_json(args.output_root / (name + "-process.json"), row)
        rows.append(row)
        print(f"END {name} {row['status']} release_gate={row.get('performance_gate_pass')} error={row.get('benchmark_error')}", flush=True)
    manifest.update(completed_utc=utc_now(), elapsed_seconds=time.monotonic() - started, rows=rows)
    write_new_json(args.output_root / "campaign-processes.json", manifest)
    return int(any(r.get("status") != "PROCESS_COMPLETED" or r.get("receipt_missing") or r.get("benchmark_error") or r.get("receipt_parse_error") for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
