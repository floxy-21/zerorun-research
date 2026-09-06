"""Fixed direct/snapshot-first/read-only-hit/Testmon laboratory comparison."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

LABELS = ("seed", "repeat-1", "repeat-2", "failing-test-added", "failure-repeated", "restored", "repeat-restored")
ORDERS = (("direct", "snapshot", "fast", "testmon"), ("testmon", "fast", "snapshot", "direct"))
TESTMON_LOCK = """--only-binary=:all:
pytest-testmon==2.2.0 \\
    --hash=sha256:2604ca44a54d61a2e830d9ce828b41a837075e4ebc1f81b148add8e90d34815b
coverage==7.10.7 \\
    --hash=sha256:314f2c326ded3f4b09be11bc282eb2fc861184bc95748ae67b360ac962770be7
"""


def save(path, payload):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def file_rows(root):
    rows = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise RuntimeError(f"unexpected symlink in experiment runtime: {p}")
        if p.is_file():
            raw = p.read_bytes()
            rows.append({"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)})
    return rows


def container(bench, root, image, arguments, *, mounts=(), env=None, setup=False):
    """Exact-ID Docker lifecycle, bounded capture and explicit cleanup proof."""
    docker = shutil.which("docker")
    name = "zerorun-controlled-comparison-" + secrets.token_hex(16)
    argv = [docker, "create", "--name", name, "--pull", "never", "--platform", "linux/amd64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "512", "--memory", "2g", "--cpus", "2"]
    if setup:
        argv.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    else:
        bench._ensure_plain_state_mountpoint(root)
        argv.extend(["--network", "none", "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
                     "--tmpfs", "/workspace/.zerorun:rw,nosuid,nodev,noexec,size=1m", *bench._plain_git_mask_args(root),
                     "--mount", f"type=bind,src={root},dst=/workspace,readonly", "--workdir", "/workspace"])
    for host, guest, writable in mounts:
        if "," in str(host) or "\n" in str(host):
            raise RuntimeError("unsupported Docker mount path")
        argv.extend(["--mount", f"type=bind,src={host},dst={guest}" + ("" if writable else ",readonly")])
    for key, value in sorted((env or {}).items()):
        argv.extend(["--env", key + "=" + value])
    argv.extend([image, "/usr/local/bin/python", *arguments])
    created_id = None
    submitted = False
    started = time.perf_counter()
    if bench._plain_exact_container_ids(root, docker, name):
        raise RuntimeError("unexpected experiment container-name collision")
    try:
        submitted = True
        created = bench._plain_run(root, argv, timeout_seconds=60)
        if created.returncode != 0:
            raise RuntimeError("container creation failed: " + created.stderr)
        created_id = created.stdout.strip()
        if len(created_id) != 64 or any(c not in "0123456789abcdef" for c in created_id):
            created_id = None
            raise RuntimeError("malformed experiment container ID")
        identity = bench._plain_inspect_container(root, docker, created_id)
        if identity[:3] != (created_id, "/" + name, image) or identity[3].get("Status") != "created":
            raise RuntimeError("container initial identity mismatch")
        completed = bench._plain_run(root, [docker, "start", "--attach", created_id], timeout_seconds=600)
        terminal = bench._plain_inspect_container(root, docker, created_id)
        if terminal[:3] != identity[:3] or terminal[3].get("Status") != "exited" or terminal[3].get("ExitCode") != completed.returncode or terminal[3].get("OOMKilled"):
            raise RuntimeError("container terminal identity/status mismatch: " + json.dumps({
                "expected": identity[:3], "terminal": terminal,
                "process_exit_code": completed.returncode,
                "stdout_tail": completed.stdout[-2000:], "stderr_tail": completed.stderr[-2000:],
            }, sort_keys=True))
    finally:
        if created_id is not None:
            removed = bench._plain_run(root, [docker, "container", "rm", "--force", "--volumes", created_id], timeout_seconds=60)
            if removed.returncode or not bench._plain_confirm_container_absent(root, docker, created_id):
                raise RuntimeError("exact experiment container cleanup unconfirmed: " + created_id)
        elif submitted and not bench._plain_remove_uncertain_container(root, docker, name):
            raise RuntimeError("uncertain experiment container cleanup unconfirmed")
    return {"exit_code": completed.returncode, "wall_ms": (time.perf_counter() - started) * 1000,
            "stdout": completed.stdout, "stderr": completed.stderr, "create_argv": argv,
            "container_id": created_id, "terminal_state": terminal[3], "cleanup_confirmed": True}


def prepare_testmon(bench, output, image):
    target = output / "testmon-runtime"
    target.mkdir()
    lock = target / "requirements.txt"
    lock.write_text(TESTMON_LOCK, encoding="utf-8")
    run = container(bench, output, image,
        ["-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-compile", "--require-hashes", "--no-deps", "--only-binary=:all:", "--target", "/comparator/site", "-r", "/comparator/requirements.txt"],
        mounts=((target, "/comparator", True),), env={"HOME": "/tmp"}, setup=True)
    run["requirements_sha256"] = hashlib.sha256(lock.read_bytes()).hexdigest()
    run["files"] = file_rows(target)
    save(output / "testmon-setup.json", run)
    if run["exit_code"]:
        raise RuntimeError("hash-locked Testmon setup failed")
    return target, run["files"]


def fresh_capture(bench, work, targets, image, support, request):
    output = request / "fresh-outcomes.json"
    output.touch(exist_ok=False)
    output.chmod(0o666)
    run = bench._plain_pytest_container(work, targets=targets, runtime_image=image, support=support, shadow_output=output)
    raw = json.loads(output.read_text())
    validated = bench._validate_shadow_evidence(raw)
    if run["exit_code"] != validated["exit_code"] or not validated["complete_per_node_outcomes"]:
        raise RuntimeError("fresh complete oracle evidence is inconsistent")
    return {**run, "capture": validated}


def testmon_run(bench, work, targets, image, runtime, state, support, request, seed, project_compat=False):
    capture = request / "testmon-outcomes.json"
    capture.touch(exist_ok=False)
    capture.chmod(0o666)
    environment = {"LANG": "C", "LC_ALL": "C", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPYCACHEPREFIX": "/tmp/zerorun-pycache", "TZ": "UTC", "HOME": "/tmp",
                   "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                   "PYTHONPATH": "/support:/workspace/src:/workspace:/workspace/.zerorun-env/site-packages:/comparator/site",
                   "TESTMON_DATAFILE": "/testmon-state/.testmondata", "ZERORUN_BENCHMARK_SHADOW_OUTPUT": "/capture.json"}
    run = container(bench, work, image,
        ["-m", "pytest", "-o", "cache_dir=/tmp/testmon-pytest-cache", "-p", "benchmark_shadow_plugin", "-p", "testmon.pytest_testmon", "--testmon",
         *(["-p", "legacypath"] if project_compat else []),
         *(["--testmon-forceselect"] if project_compat and not seed else []),
         *(["--testmon-noselect"] if seed else []), *targets],
        mounts=((runtime, "/comparator", False), (state, "/testmon-state", True), (support, "/support", False), (capture, "/capture.json", True)), env=environment)
    # Testmon may turn pytest's no-tests-collected status into success after the
    # capture hook. Retain both raw statuses; never silently edit the receipt.
    if capture.stat().st_size:
        run["capture"] = bench._validate_shadow_evidence(json.loads(capture.read_text()))
    else:
        run["capture_error"] = "Testmon did not produce outcome capture"
    return run


def outcome_map(capture):
    if not capture["complete_per_node_outcomes"]:
        raise ValueError("incomplete selected outcomes")
    return dict(zip(capture["nodeids"], capture["outcomes"], strict=True))


def is_plain_pass(outcome):
    return outcome == {"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}


def selector_comparison(selected, prior_passed, oracle):
    selected_rows = outcome_map(selected)
    oracle_rows = outcome_map(oracle)
    claimed = {node: value for node, value in prior_passed.items() if node not in selected_rows}
    reconstructed = {**claimed, **selected_rows}
    mismatches = [node for node, value in reconstructed.items() if oracle_rows.get(node) != value]
    unknown = sorted(set(oracle_rows) - set(reconstructed))
    result = {"selected_nodes": len(selected_rows), "prior_passed_omitted_nodes": len(claimed),
              "selected_node_ids": sorted(selected_rows), "claimed_node_ids": sorted(claimed),
              "mismatched_or_absent_node_ids": sorted(mismatches), "uncovered_node_ids": unknown,
              "complete_reconstruction_matches": not mismatches and not unknown,
              "selector_is_not_a_fresh_full_oracle": True}
    next_passed = dict(prior_passed)
    for node, value in selected_rows.items():
        if is_plain_pass(value):
            next_passed[node] = value
        else:
            next_passed.pop(node, None)
    return result, next_passed


def run_workload(bench, api, hermetic, load_manifest, item, root, output, runtime, image, project_compat=False):
    name, upstream, commit, classification, targets, extras = item
    output.mkdir()
    # git archive reads every object of this exact frozen tree without running
    # repository code; detect the previous missing-blob failure before timing.
    with open(os.devnull, "wb") as sink:
        check = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", commit], stdout=sink, stderr=subprocess.PIPE, check=False)
    save(output / "tree-preflight.json", {"commit": commit, "exit_code": check.returncode, "stderr": check.stderr.decode(errors="replace")})
    if check.returncode:
        raise RuntimeError("frozen Git tree is incomplete")
    before = bench._capture_source_identity()
    rows = []
    support = output / "support"
    support.mkdir()
    (support / "benchmark_shadow_plugin.py").write_text(bench._SHADOW_PLUGIN, encoding="utf-8")
    with bench._frozen_dependency_layer(root, runtime_image=image, extra_requirements=extras) as layer:
        for trajectory, order in enumerate(ORDERS, 1):
            trajectory_root = output / f"trajectory-{trajectory}"
            trajectory_root.mkdir()
            selector_state = trajectory_root / "testmon-state"
            selector_state.mkdir(mode=0o777)
            selector_state.chmod(0o777)
            previous_passed = {}
            with bench._isolated_worktree(root, git=shutil.which("git"), seed_sha=commit, trajectory_index=90 + trajectory, expected_dependency_snapshot=layer["snapshot"]) as work:
                setup = bench._materialize_frozen_pytest_environment(work, dependency_layer=layer, runtime_image=image, extra_requirements=extras)
                paths = subprocess.check_output(["git", "-C", str(work), "ls-tree", "--name-only", commit], text=True).splitlines()
                path = work / ".zerorun.json"
                manifest = json.loads(path.read_text())
                task = manifest["tasks"].pop("pytest-generalization")
                task.update(cacheable=True, inputs=sorted(set([".zerorun-env", *paths])))
                task["command"].extend(targets)
                manifest["tasks"] = {"pytest-snapshot": dict(task), "pytest-fast": dict(task)}
                path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
                save(trajectory_root / "manifest.json", manifest)
                save(trajectory_root / "setup.json", setup)
                loaded = load_manifest(path)
                source = work / targets[0]
                original = source.read_bytes()
                for index, label in enumerate(LABELS):
                    if index == 3:
                        source.write_bytes(original + b"\n\ndef test_zerorun_controlled_added_failure():\n    assert False, 'controlled full-target failure'\n")
                    elif index == 5:
                        source.write_bytes(original)
                    request = trajectory_root / f"request-{index}"
                    request.mkdir()
                    row = {"workload": name, "trajectory": trajectory, "order": order, "index": index, "label": label,
                           "source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "arms": {}}
                    save(request / "invocation.json", {**row, "frozen_sha": commit, "targets": targets})
                    for arm in order:
                        if arm == "direct":
                            result = bench._plain_pytest_container(work, targets=targets, runtime_image=image)
                        elif arm == "testmon":
                            result = testmon_run(bench, work, targets, image, runtime, selector_state, support, request, index == 0, project_compat)
                        else:
                            stdout, stderr = io.BytesIO(), io.BytesIO()
                            start = time.perf_counter()
                            if arm == "snapshot":
                                with patch.object(hermetic, "_try_readonly_whole_task_hit", return_value=None):
                                    result_object = api.run_task(loaded, loaded.tasks["pytest-snapshot"], stdout=stdout, stderr=stderr)
                            else:
                                result_object = api.run_task(loaded, loaded.tasks["pytest-fast"], stdout=stdout, stderr=stderr)
                            result = {**result_object.as_dict(), "request_wall_ms": (time.perf_counter() - start) * 1000,
                                      "stdout": stdout.getvalue().decode(errors="replace"), "stderr": stderr.getvalue().decode(errors="replace")}
                        save(request / (arm + ".json"), result)
                        row["arms"][arm] = result
                    oracle = fresh_capture(bench, work, targets, image, support, request)
                    row["oracle"] = oracle
                    expected_exit = 1 if index in (3, 4) else 0
                    row["whole_task_exit_agreement"] = all(row["arms"][a]["exit_code"] == oracle["exit_code"] == expected_exit for a in ("direct", "snapshot", "fast"))
                    row["cache_behavior_expected"] = all(
                        row["arms"][a]["status"] == ("MISS_FAILED" if index in (3, 4) else "MISS_EXECUTED" if index == 0 else "HIT_REUSED")
                        for a in ("snapshot", "fast"))
                    if "capture" in row["arms"]["testmon"]:
                        row["testmon_comparison"], previous_passed = selector_comparison(row["arms"]["testmon"]["capture"], previous_passed, oracle["capture"])
                    else:
                        row["testmon_comparison"] = {"unavailable": True}
                    save(request / "observation.json", row)
                    rows.append(row)
                    print(f"{name} {trajectory} {label}: fast={row['arms']['fast']['status']} agreement={row['whole_task_exit_agreement']} testmon_exit={row['arms']['testmon']['exit_code']}", flush=True)
                if source.read_bytes() != original:
                    raise RuntimeError("source was not restored")
    environment = {key: value for key, value in layer.items() if key != "root"}
    after = bench._capture_source_identity()
    result = {"workload": name, "upstream": upstream, "commit": commit, "targets": targets, "rows": rows,
              "source_before": before, "source_after": after, "source_stable": before == after,
              "environment": environment, "operator_review_claimed": False,
              "whole_task_challenges_pass": before == after and len(rows) == 14 and all(r["whole_task_exit_agreement"] and r["cache_behavior_expected"] for r in rows)}
    save(output / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--workloads", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", choices=("packaging", "pycparser", "colorama", "click", "more-itertools"))
    parser.add_argument("--testmon-project-compat", action="store_true", help="Enable stock legacy-path support and selection with project marker filters; recorded setup correction")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("POSIX Docker laboratory required")
    sys.path.insert(0, str(args.engine.resolve(strict=True)))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tools import product_generalization_benchmark as bench
    from zerorun import api, hermetic
    from zerorun.manifest import load_manifest
    from run_frozen_campaign import WORKLOADS, IMAGE
    args.output = args.output.absolute()
    args.output.mkdir(parents=True, exist_ok=False)
    private = args.output / "private-cache-authentication-NOT-FOR-PUBLICATION"
    private.mkdir(mode=0o700)
    os.environ["ZERORUN_TRUST_ROOT"] = str(private)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    selected = [item for item in WORKLOADS if args.only is None or item[0] == args.only]
    save(args.output / "protocol.json", {"started_utc": datetime.now(timezone.utc).isoformat(), "labels": LABELS, "orders": ORDERS,
        "workloads": selected, "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime_image": IMAGE, "operator_authority_allowed": False, "cache_authentication_private_fixture_only": True,
        "testmon_project_compatibility": args.testmon_project_compat})
    runtime, runtime_before = prepare_testmon(bench, args.output, IMAGE)
    results = []
    for item in selected:
        root_name = "more-itertools-repaired" if item[0] == "more-itertools" else item[0]
        try:
            result = run_workload(bench, api, hermetic, load_manifest, item, (args.workloads / root_name).resolve(strict=True), args.output / item[0], runtime, IMAGE, args.testmon_project_compat)
            results.append({"workload": item[0], "completed": True, "whole_task_challenges_pass": result["whole_task_challenges_pass"]})
        except Exception as error:
            result = {"workload": item[0], "completed": False, "error_type": type(error).__name__, "error": str(error)}
            save(args.output / (item[0] + "-failure.json"), result)
            results.append(result)
            import traceback
            traceback.print_exc()
    runtime_after = file_rows(runtime)
    authorities = list(private.glob("repositories/*/authorities/*.json"))
    final = {"completed_utc": datetime.now(timezone.utc).isoformat(), "results": results,
             "testmon_runtime_unchanged": runtime_before == runtime_after,
             "operator_authority_receipts_created": bool(authorities),
             "completed": all(r["completed"] for r in results) and runtime_before == runtime_after and not authorities}
    save(args.output / "campaign-summary.json", final)
    print(json.dumps(final))
    return int(not final["completed"])


if __name__ == "__main__":
    raise SystemExit(main())
