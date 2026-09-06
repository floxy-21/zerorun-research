"""Six balanced, independently initialized short-subject replication blocks.

Research-only orchestration. No frozen producer or product file is modified;
the original five-subject experiment remains separate and unchanged. This
driver must be reviewed before execution in the existing POSIX Docker lab.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
from itertools import permutations
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from unittest.mock import patch


SEED = "zerorun-short-replication-v1"
ARMS = ("direct", "snapshot", "fast")
COHORT = ("packaging", "pycparser", "colorama", "click")
LABELS = ("seed", "repeat-1", "repeat-2", "failing-test-added",
          "failure-repeated", "restored", "repeat-restored")
EXECUTION_TIMEOUT_SECONDS = 900
RECOVERY_SHA256 = "2fb6b23f5bae6e2f1f2f8681bc9f492be704f43d56f69c20c6c17ad95d9aa2d9"
FAILURE_SUFFIX = b"\n\ndef test_zerorun_controlled_added_failure():\n    assert False, 'controlled full-target failure'\n"
SQJ = Path(__file__).resolve().parents[1]


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    """Never overwrite a protocol, observation or failure receipt."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _rank(namespace, values):
    text = "\0".join((SEED, namespace, *values))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schedule():
    """Hash-shuffle has a portable definition independent of random versions."""
    names = sorted(COHORT, key=lambda name: _rank("workload", (name,)))
    return [{"workload": name, "trajectories": [
        {"trajectory": index, "order": list(order)}
        for index, order in enumerate(sorted(permutations(ARMS),
            key=lambda order: _rank("arms:" + name, order)), 1)]}
        for name in names]


def expected_request(index):
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 7:
        raise ValueError("request index must be an integer in zero through six")
    return {"index": index, "label": LABELS[index],
            "expected_exit_code": 1 if index in (3, 4) else 0,
            "expected_cache_status": "MISS_FAILED" if index in (3, 4)
            else "MISS_EXECUTED" if index == 0 else "HIT_REUSED",
            "source_state": "added-failure" if index in (3, 4) else "original"}


def task_name(workload, trajectory, arm):
    if workload not in COHORT or trajectory not in range(1, 7) or arm not in ARMS[1:]:
        raise ValueError("invalid independent cache namespace")
    return f"replication-{workload}-{trajectory}-{arm}"


def protected_source_identity(engine, sqj=SQJ):
    """Observe bytes only; excludes new-driver files and all generated output."""
    paths = sorted((engine / "zerorun").rglob("*.py"))
    paths += [engine / "tools/product_generalization_benchmark.py"]
    paths += sorted((sqj / "producers").glob("*.py"))
    paths += [sqj / "run_controlled_comparison.py", sqj / "run_frozen_campaign.py"]
    rows = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError("protected source is absent or link-like: " + str(path))
        label = ("engine/" + path.relative_to(engine).as_posix()) if path.is_relative_to(engine / "zerorun") or path == engine / "tools/product_generalization_benchmark.py" else "sqj/" + path.relative_to(sqj).as_posix()
        rows.append({"path": label, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": rows}


def host_snapshot():
    """Read bounded Linux telemetry without running another workload."""
    result = {"utc": utc_now(), "pid": os.getpid(), "cpu_count": os.cpu_count()}
    if hasattr(os, "getloadavg"):
        result["load_average_1_5_15"] = list(os.getloadavg())
    for name in ("meminfo", "stat", "pressure/cpu", "pressure/memory", "pressure/io"):
        path = Path("/proc") / name
        try:
            with path.open(encoding="ascii") as stream:
                result[name] = stream.read(32768)
        except OSError as error:
            result[name] = {"unavailable": type(error).__name__}
    return result


def phase_name(argv):
    """Retain operation names, not environment values or mount-path arguments."""
    parts = [str(value) for value in argv]
    if len(parts) < 2:
        return "other"
    operation = parts[1]
    if operation in {"container", "image"} and len(parts) > 2:
        return operation + " " + parts[2]
    return operation


def measured_plain_runner(original, phases):
    """Observational wrapper preserves every original argument and exception."""
    def run(root, argv, *, timeout_seconds):
        row = {"phase": phase_name(argv), "started_utc": utc_now(),
               "timeout_seconds": timeout_seconds}
        started = time.perf_counter()
        try:
            result = original(root, argv, timeout_seconds=timeout_seconds)
            row["exit_code"] = result.returncode
            return result
        except BaseException as error:
            row["error_type"] = type(error).__name__
            raise
        finally:
            row["wall_ms"] = (time.perf_counter() - started) * 1000
            row["ended_utc"] = utc_now()
            phases.append(row)
    return run


def measure_call(path, call, *, bench=None):
    """Host telemetry is outside the complete outer request clock for all arms."""
    phases = []
    receipt = {"started_utc": utc_now(), "host_before": host_snapshot(),
               "docker_subprocess_phases": phases,
               "timing_boundary": "complete helper/API call; telemetry and receipt writes excluded"}
    context = patch.object(bench, "_plain_run", measured_plain_runner(bench._plain_run, phases)) if bench is not None else nullcontext()
    with context:
        started = time.perf_counter()
        try:
            result = call()
        except BaseException as error:
            receipt.update(error_type=type(error).__name__, error=str(error), completed=False)
            raise
        else:
            receipt.update(result)
            receipt["completed"] = True
        finally:
            receipt["request_wall_ms"] = (time.perf_counter() - started) * 1000
            receipt["ended_utc"] = utc_now()
            receipt["host_after"] = host_snapshot()
            save(path, receipt)
    return receipt


def validate_limits(bench, oci):
    if bench._PYTEST_EXECUTION_TIMEOUT_SECONDS != EXECUTION_TIMEOUT_SECONDS or oci.DOCKER_EXECUTION_TIMEOUT_SECONDS != EXECUTION_TIMEOUT_SECONDS:
        raise ValueError("direct and ZeroRun execution limits must both be 900 seconds")
    if tuple(bench._PLAIN_DOCKER_RESOURCE_ARGS) != tuple(oci.DOCKER_RESOURCE_ARGS):
        raise ValueError("direct and ZeroRun resource limits differ")


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load trusted helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_recovery():
    path = SQJ / "producers/controlled_comparison-recovery.py"
    if sha256_file(path) != RECOVERY_SHA256:
        raise ValueError("frozen recovery producer bytes changed")
    return load_module(path, "zerorun_frozen_recovery_for_replication")


def freeze_protocol(output, engine, bench, oci, workloads, image):
    validate_limits(bench, oci)
    selected = {item[0]: item for item in workloads if item[0] in COHORT}
    if set(selected) != set(COHORT) or len(selected) != len(COHORT):
        raise ValueError("short-subject cohort incomplete")
    protocol = {"schema": "zerorun.randomized-short-replication.v1", "frozen_utc": utc_now(),
        "seed": SEED, "shuffle_algorithm": "ascending SHA256 of NUL-separated seed/namespace/values",
        "schedule": schedule(), "request_states": [expected_request(i) for i in range(7)],
        "workloads": [selected[row["workload"]] for row in schedule()], "runtime_image": image,
        "producer_sha256": sha256_file(Path(__file__)), "engine_identity": bench._capture_source_identity(),
        "protected_source_identity": protected_source_identity(engine),
        "frozen_recovery_producer_sha256": RECOVERY_SHA256,
        "execution_timeout_seconds": EXECUTION_TIMEOUT_SECONDS,
        "docker_resource_args": list(oci.DOCKER_RESOURCE_ARGS),
        "expected_trajectories": 24, "expected_requests": 168,
        "expected_timed_arm_invocations": 504, "expected_fresh_oracles": 168,
        "expected_hit_requests_per_cache_arm": 96, "expected_nonhit_requests_per_cache_arm": 72,
        "operator_authority_allowed": False, "cache_authentication_private_fixture_only": True,
        "cache_isolation": "independent worktree/store per block; different task names and action keys per ZeroRun arm",
        "operator_review_claimed": False, "testmon_in_this_replication": False,
        "scope": "four already-observed short subjects; not an unseen holdout or five-subject replacement",
        "more_itertools": "original five-subject evidence retained; new long-subject diagnostic outside this driver",
        "warmups_removed": 0, "retry_policy": "none; preserve any failed block and stop that workload",
        "correctness_policy": "fresh full-target capture after each complete three-arm request; stop workload on any material mismatch",
        "timing_policy": "complete outer call for every arm; existing product phases; observed direct/oracle subprocess phases; telemetry outside timed boundary",
        "environment": {"python": sys.version, "platform": platform.platform(), "host": host_snapshot()}}
    save(output / "protocol.json", protocol)
    return protocol


def assess_request(row):
    expected = expected_request(row["index"])
    oracle = row["oracle"]
    capture = oracle["capture"]
    agreement = capture["complete_per_node_outcomes"] and capture["exit_code"] == oracle["exit_code"] == expected["expected_exit_code"] and all(
        row["arms"][arm]["exit_code"] == oracle["exit_code"] for arm in ARMS)
    behavior = all(row["arms"][arm]["status"] == expected["expected_cache_status"] for arm in ARMS[1:])
    return {"whole_task_exit_agreement": bool(agreement), "cache_behavior_expected": bool(behavior)}


def run_workload(bench, api, hermetic, load_manifest, recovery, item, root, output, image, trajectories):
    name, upstream, commit, classification, targets, extras = item
    output.mkdir()
    with open(os.devnull, "wb") as sink:
        tree = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", commit],
                              stdout=sink, stderr=subprocess.PIPE, check=False)
    save(output / "tree-preflight.json", {"commit": commit, "exit_code": tree.returncode,
                                         "stderr": tree.stderr.decode(errors="replace")})
    if tree.returncode:
        raise RuntimeError("frozen Git tree is incomplete")
    before = bench._capture_source_identity()
    support = output / "support"
    support.mkdir()
    (support / "benchmark_shadow_plugin.py").write_text(bench._SHADOW_PLUGIN, encoding="utf-8")
    rows, blocks = [], []
    with bench._frozen_dependency_layer(root, runtime_image=image, extra_requirements=extras) as layer:
        for block in trajectories:
            trajectory, order = block["trajectory"], block["order"]
            destination = output / f"trajectory-{trajectory}"
            destination.mkdir()
            save(destination / "block-plan.json", block)
            block_rows = []
            try:
                with bench._isolated_worktree(root, git=shutil.which("git"), seed_sha=commit,
                    trajectory_index=1000 + trajectory, expected_dependency_snapshot=layer["snapshot"]) as work:
                    setup = bench._materialize_frozen_pytest_environment(work, dependency_layer=layer,
                                runtime_image=image, extra_requirements=extras)
                    paths = subprocess.check_output(["git", "-C", str(work), "ls-tree", "--name-only", commit], text=True).splitlines()
                    manifest_path = work / ".zerorun.json"
                    manifest = json.loads(manifest_path.read_text())
                    task = manifest["tasks"].pop("pytest-generalization")
                    task.update(cacheable=True, inputs=sorted(set([".zerorun-env", *paths])))
                    task["command"].extend(targets)
                    manifest["tasks"] = {task_name(name, trajectory, arm): dict(task) for arm in ARMS[1:]}
                    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
                    save(destination / "manifest.json", manifest)
                    save(destination / "setup.json", setup)
                    loaded = load_manifest(manifest_path)
                    source = work / targets[0]
                    original = source.read_bytes()
                    try:
                        for index, label in enumerate(LABELS):
                            if index == 3:
                                source.write_bytes(original + FAILURE_SUFFIX)
                            elif index == 5:
                                source.write_bytes(original)
                            request = destination / f"request-{index}"
                            request.mkdir()
                            row = {"workload": name, "trajectory": trajectory, "index": index,
                                   "label": label, "order": order, "source_file_sha256": sha256_file(source), "arms": {}}
                            save(request / "invocation.json", {**row, "frozen_sha": commit,
                                "targets": targets, "expected": expected_request(index)})
                            for arm in order:
                                if arm == "direct":
                                    call = lambda: bench._plain_pytest_container(work, targets=targets, runtime_image=image)
                                else:
                                    def call(arm=arm):
                                        stdout, stderr = io.BytesIO(), io.BytesIO()
                                        ablation = patch.object(hermetic, "_try_readonly_whole_task_hit", return_value=None) if arm == "snapshot" else nullcontext()
                                        with ablation:
                                            value = api.run_task(loaded, loaded.tasks[task_name(name, trajectory, arm)], stdout=stdout, stderr=stderr)
                                        return {**value.as_dict(), "stdout": stdout.getvalue().decode(errors="replace"),
                                                "stderr": stderr.getvalue().decode(errors="replace")}
                                row["arms"][arm] = measure_call(request / (arm + ".json"), call, bench=bench if arm == "direct" else None)
                            row["oracle"] = measure_call(request / "fresh.json", lambda: recovery.fresh_capture(
                                bench, work, targets, image, support, request), bench=bench)
                            row.update(assess_request(row))
                            save(request / "observation.json", row)
                            rows.append(row)
                            block_rows.append(row)
                            print(f"{name} block {trajectory} {label}: agreement={row['whole_task_exit_agreement']} expected={row['cache_behavior_expected']}", flush=True)
                            if not row["whole_task_exit_agreement"] or not row["cache_behavior_expected"]:
                                raise RuntimeError("material fresh-result or cache-behavior mismatch")
                    finally:
                        source.write_bytes(original)
                        save(destination / "restoration.json", {"source_sha256": sha256_file(source),
                             "original_sha256": hashlib.sha256(original).hexdigest(), "restored": source.read_bytes() == original})
                result = {**block, "completed": True, "requests": len(block_rows)}
            except Exception as error:
                result = {**block, "completed": False, "requests": len(block_rows),
                          "error_type": type(error).__name__, "error": str(error), "retry_attempted": False}
            save(destination / "block-summary.json", result)
            blocks.append(result)
            if not result["completed"]:
                break
    after = bench._capture_source_identity()
    result = {"workload": name, "upstream": upstream, "commit": commit, "targets": targets,
              "rows": rows, "blocks": blocks, "source_before": before, "source_after": after,
              "source_stable": before == after, "environment": {key: value for key, value in layer.items() if key != "root"},
              "operator_review_claimed": False,
              "completed": len(blocks) == 6 and len(rows) == 42 and all(b["completed"] for b in blocks) and before == after}
    save(output / "summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--workloads", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if os.name != "posix":
        parser.error("requires the reviewed POSIX Docker laboratory")
    engine = args.engine.resolve(strict=True)
    sys.path.insert(0, str(engine))
    from tools import product_generalization_benchmark as bench
    from zerorun import api, hermetic, oci
    from zerorun.manifest import load_manifest
    frozen = load_module(SQJ / "run_frozen_campaign.py", "zerorun_frozen_cohort_for_replication")
    recovery = load_recovery()
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    protocol = freeze_protocol(output, engine, bench, oci, frozen.WORKLOADS, frozen.IMAGE)
    private = output / "private-cache-authentication-NOT-FOR-PUBLICATION"
    private.mkdir(mode=0o700)
    results = []
    with patch.dict(os.environ, {"ZERORUN_TRUST_ROOT": str(private), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}):
        for scheduled in protocol["schedule"]:
            item = next(item for item in frozen.WORKLOADS if item[0] == scheduled["workload"])
            try:
                result = run_workload(bench, api, hermetic, load_manifest, recovery, item,
                    (args.workloads / item[0]).resolve(strict=True), output / item[0], frozen.IMAGE, scheduled["trajectories"])
                results.append({"workload": item[0], "completed": result["completed"], "requests": len(result["rows"])})
            except Exception as error:
                failure = {"workload": item[0], "completed": False, "error_type": type(error).__name__, "error": str(error), "retry_attempted": False}
                save(output / (item[0] + "-failure.json"), failure)
                results.append(failure)
    protected_after = protected_source_identity(engine)
    authorities = list(private.glob("repositories/*/authorities/*.json"))
    final = {"schema": "zerorun.randomized-short-replication-summary.v1", "completed_utc": utc_now(),
        "results": results, "protected_source_after": protected_after,
        "protected_source_unchanged": protected_after == protocol["protected_source_identity"],
        "operator_authority_receipts_created": bool(authorities),
        "completed": all(result["completed"] for result in results) and not authorities
            and protected_after == protocol["protected_source_identity"]}
    save(output / "campaign-summary.json", final)
    print(json.dumps(final, sort_keys=True), flush=True)
    return int(not final["completed"])


if __name__ == "__main__":
    raise SystemExit(main())
