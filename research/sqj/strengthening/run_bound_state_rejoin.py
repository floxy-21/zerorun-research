"""Freeze external engine/helper bindings around the unchanged state case.

This wrapper must run only after the short-replication campaign has completed.
It never edits the frozen runner, protected sources, or operator authority.
The outer protocol precedes case execution; completion preserves failures.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
RUNNER_SHA = "70732ee170c789ddcf8e66b8d8ecb2f147ec1a5c3e294d5b8d0e8d2ba6449db1"
RECOVERY_SHA = "2fb6b23f5bae6e2f1f2f8681bc9f492be704f43d56f69c20c6c17ad95d9aa2d9"
REPLICATION_HELPER_SHA = "eaa420f858dc07349a42ae6be36aba795ba9d57387d5be795e6e3552f5ca0a4a"
EVIDENCE = {
    "django-environ-state-rejoin-candidate-v1.json": "a157e97ceac0d2c3f9e334fdfc1395979429dfbdc807bf51d03ad432e87b4cfb",
    "django-environ-metadata-response-v1.json": "b70d84f366c70456c2c8aeedd1ba675b70b71270b7237282709f90924f02d828",
    "django-environ-31c8f983-source.tar.gz": "6264853af7ab12c7265be9b10115007fe4977de9b1636c1d86875ca1a0654425",
    "trace-cohort-2/rows/row-51126.json": "16a0438ab2bc234587dc50129320fbb64063b7c90927fcb501f39eeea0c434ae",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def reject(value):
        raise ValueError("nonfinite JSON value")

    def floating(value):
        result = float(value)
        if not math.isfinite(result):
            reject(value)
        return result

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject, parse_float=floating)


def read_record(path, *, label=None):
    path = Path(path)
    before = path.lstat()
    if path.is_symlink() or not path.is_file() or getattr(before, "st_file_attributes", 0) & 0x400:
        raise ValueError("binding requires ordinary file: " + str(path))
    if before.st_size > 32 * 1024 * 1024:
        raise ValueError("binding file exceeds bound")
    raw = path.read_bytes()
    after = path.lstat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("binding file changed while reading")
    return {"path": label or str(path), "sha256": sha(raw), "bytes": len(raw)}, raw


def file_record(path, *, label=None):
    return read_record(path, label=label)[0]


def write_once(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def core_identity(protected):
    files = [dict(row) for row in protected["files"] if row["path"].startswith("engine/")]
    if not files or len({row["path"] for row in files}) != len(files):
        raise ValueError("core protected-file list is empty or duplicate")
    # Preserve the source helper's ordering so independently computed digests
    # have a fully specified projection, not filesystem enumeration order.
    return {"sha256": sha(canonical(files)), "files": files}


def helper_records(engine):
    rows = []
    paths = [
        (HERE / "randomized_replication.py", "strengthening/randomized_replication.py"),
        (HERE.parent / "producers/controlled_comparison-recovery.py", "sqj/producers/controlled_comparison-recovery.py"),
        (engine / "tools/product_generalization_benchmark.py", "engine/tools/product_generalization_benchmark.py"),
        (engine / "ci/generalization-runtime-requirements.txt", "engine/ci/generalization-runtime-requirements.txt"),
    ]
    for path, label in paths:
        rows.append(file_record(path, label=label))
    return rows


def require_exact_module(module, path):
    if Path(module.__file__).resolve(strict=True) != path.resolve(strict=True):
        raise ValueError("module import resolved outside bound source: " + module.__name__)


def load_components(engine):
    # Only trusted project modules are loaded; the source archive remains inert.
    sys.path.insert(0, str(engine))
    bench = importlib.import_module("tools.product_generalization_benchmark")
    replication = importlib.import_module("research.sqj.strengthening.randomized_replication")
    runner = importlib.import_module("research.sqj.strengthening.state_rejoin")
    require_exact_module(bench, engine / "tools/product_generalization_benchmark.py")
    require_exact_module(replication, HERE / "randomized_replication.py")
    require_exact_module(runner, HERE / "state_rejoin.py")
    return bench, replication, runner


def run_bound(engine, evidence, replication_protocol, output):
    engine = Path(engine).resolve(strict=True)
    evidence = Path(evidence).resolve(strict=True)
    replication_protocol = Path(replication_protocol).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    if output.is_relative_to(engine):
        raise ValueError("case output must be outside the bound engine checkout")
    output.mkdir(parents=True, exist_ok=False)
    protocol_path = output / "protocol.json"
    completion_path = output / "completion.json"
    runner_path = HERE / "state_rejoin.py"
    runner_args = ["--engine", str(engine), "--evidence", str(evidence),
                   "--output", str(output / "case"), "--execute-reviewed-lab"]
    protocol = {
        "schema": "zerorun.bound-state-rejoin.protocol.v1", "created_utc": utc_now(),
        "runner": None, "wrapper": None, "helper_files": [], "evidence_files": [],
        "protected_source_before": None, "core_protected_before": None, "engine_identity_before": None,
        "replication": None, "checks": {}, "preflight_errors": [],
        "invocation": {"engine": str(engine), "evidence": str(evidence), "child_directory": "case", "runner_args": runner_args},
        "scope": "post-observation purposive controlled result-only case; no original environment or autonomous-agent equivalence",
        "operator_authorization_permitted": False,
    }
    bench = replication = runner = None
    try:
        protocol["runner"] = file_record(runner_path, label="strengthening/state_rejoin.py")
        protocol["wrapper"] = file_record(Path(__file__), label="strengthening/run_bound_state_rejoin.py")
        protocol["helper_files"] = helper_records(engine)
        protocol["checks"]["runner_hash_expected"] = protocol["runner"]["sha256"] == RUNNER_SHA
        protocol["checks"]["replication_helper_hash_expected"] = protocol["helper_files"][0]["sha256"] == REPLICATION_HELPER_SHA
        protocol["checks"]["recovery_helper_hash_expected"] = protocol["helper_files"][1]["sha256"] == RECOVERY_SHA
        for relative, expected in EVIDENCE.items():
            record = file_record(evidence / relative, label=relative)
            protocol["evidence_files"].append(record)
            if record["sha256"] != expected:
                raise ValueError("case evidence hash mismatch: " + relative)
        if not all(protocol["checks"].values()):
            raise ValueError("frozen producer/helper hash mismatch")
        before_protocol_record, previous_raw = read_record(replication_protocol)
        previous = strict_json(previous_raw)
        previous_completion_path = replication_protocol.parent / "campaign-summary.json"
        before_completion_record, previous_completion_raw = read_record(previous_completion_path)
        previous_completion = strict_json(previous_completion_raw)
        if previous.get("schema") != "zerorun.randomized-short-replication.v1":
            raise ValueError("unrecognized short replication protocol")
        previous_protected = previous["protected_source_identity"]
        previous_core = core_identity(previous_protected)
        protocol["replication"] = {
            "protocol_file": before_protocol_record, "completion_file": before_completion_record,
            "protected_source_identity": previous_protected, "core_protected_identity": previous_core,
            "engine_identity": previous["engine_identity"],
            "completed": previous_completion.get("completed") is True,
        }
        protocol["checks"]["replication_completed"] = (
            previous_completion.get("schema") == "zerorun.randomized-short-replication-summary.v1"
            and previous_completion.get("completed") is True
            and previous_completion.get("protected_source_unchanged") is True
            and previous_completion.get("operator_authority_receipts_created") is False
            and previous_completion.get("protected_source_after") == previous_protected)
        bench, replication, runner = load_components(engine)
        protected = replication.protected_source_identity(engine)
        protocol["protected_source_before"] = protected
        protocol["core_protected_before"] = core_identity(protected)
        protocol["engine_identity_before"] = bench._capture_source_identity()
        protocol["checks"].update(
            protected_matches_replication=protected == previous_protected,
            core_matches_replication=protocol["core_protected_before"] == previous_core,
            engine_matches_replication=protocol["engine_identity_before"] == previous["engine_identity"])
        if file_record(replication_protocol) != before_protocol_record or file_record(previous_completion_path) != before_completion_record:
            raise ValueError("short replication binding changed while reading")
        if not all(protocol["checks"].values()):
            raise ValueError("completed replication engine/protected-source binding mismatch")
    except BaseException as error:
        protocol["preflight_errors"].append({"error_type": type(error).__name__, "error": str(error)})
    # No runner invocation can precede this durable protocol write.
    write_once(protocol_path, protocol)
    outcome = {"return_code": None, "error_type": None, "error": None, "runner_invoked": False}
    completion = {
        "schema": "zerorun.bound-state-rejoin.completion.v1",
        "protocol_sha256": file_record(protocol_path)["sha256"], "outcome": outcome,
        "protected_source_after": None, "core_protected_after": None, "engine_identity_after": None,
        "helper_files_after": [], "evidence_files_after": [], "wrapper_after": None,
        "runner_after": None, "case_summary_file": None, "postflight_errors": [], "checks": {},
    }
    try:
        if protocol["preflight_errors"]:
            raise ValueError("case not invoked because external binding preflight failed")
        outcome["runner_invoked"] = True
        outcome["return_code"] = runner.main(runner_args)
    except BaseException as error:
        outcome["error_type"] = type(error).__name__
        outcome["error"] = str(error)
    finally:
        try:
            if replication is not None and bench is not None:
                protected_after = replication.protected_source_identity(engine)
                completion["protected_source_after"] = protected_after
                completion["core_protected_after"] = core_identity(protected_after)
                completion["engine_identity_after"] = bench._capture_source_identity()
            completion["helper_files_after"] = helper_records(engine)
            completion["wrapper_after"] = file_record(Path(__file__), label="strengthening/run_bound_state_rejoin.py")
            completion["runner_after"] = file_record(runner_path, label="strengthening/state_rejoin.py")
            completion["evidence_files_after"] = [file_record(evidence / p, label=p) for p in EVIDENCE]
            summary_path = output / "case/summary.json"
            case_completed = False
            if summary_path.is_file():
                summary_record, summary_raw = read_record(summary_path, label="case/summary.json")
                completion["case_summary_file"] = summary_record
                summary = strict_json(summary_raw)
                case_completed = summary.get("completed") is True
            authority_files = list((output / "case/private-cache-authentication-NOT-FOR-PUBLICATION").glob("repositories/*/authorities/*.json"))
            completion["checks"] = {
                "protected_source_unchanged": completion["protected_source_after"] is not None and completion["protected_source_after"] == protocol["protected_source_before"],
                "core_source_unchanged": completion["core_protected_after"] is not None and completion["core_protected_after"] == protocol["core_protected_before"],
                "engine_identity_unchanged": completion["engine_identity_after"] is not None and completion["engine_identity_after"] == protocol["engine_identity_before"],
                "helper_files_unchanged": completion["helper_files_after"] == protocol["helper_files"],
                "wrapper_unchanged": completion["wrapper_after"] == protocol["wrapper"],
                "runner_unchanged": completion["runner_after"] == protocol["runner"],
                "evidence_files_unchanged": completion["evidence_files_after"] == protocol["evidence_files"],
                "case_completed": case_completed, "no_operator_authority": not authority_files,
            }
        except BaseException as error:
            completion["postflight_errors"].append({"error_type": type(error).__name__, "error": str(error)})
        completion["completed_utc"] = utc_now()
        completion["completed"] = (
            outcome["runner_invoked"] and type(outcome["return_code"]) is int and outcome["return_code"] == 0
            and outcome["error_type"] is None and not protocol["preflight_errors"]
            and not completion["postflight_errors"] and bool(completion["checks"])
            and all(completion["checks"].values()))
        write_once(completion_path, completion)
    return int(not completion["completed"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--replication-protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    return run_bound(args.engine, args.evidence, args.replication_protocol, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
