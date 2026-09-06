from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import hermetic_pytest_experiment as experiment
from tools import pytest_collection_identity_v2 as collection_identity
from tools import pytest_result_dependency_probe as result_probe
from tools import pytest_incremental_collection as incremental_collection
from tools.pytest_node_cache import NodeCacheDecision, fresh_nodeids as cache_fresh_nodeids
from zerorun.oci import FIXED_CONTAINER_ENV, hermetic_environment, inspect_runtime
from zerorun.model import Manifest
from zerorun.path_safety import (
    create_private_directory,
    is_link_like,
    private_temporary_directory,
)


_MOUNT = "/zerorun-batch"
_OUTPUT_MOUNT = "/zerorun-batch-output"
_DRIVER_FILE = "driver.py"
_PLAN_FILE = "plan.json"
_RESULT_FILE = "results.json"
_INCREMENTAL_PLUGIN_FILE = "zerorun_incremental_plugin.py"
_INCREMENTAL_PLUGIN_NAME = "zerorun_incremental_plugin"
_INCREMENTAL_CLASSIFIER_FILE = "zerorun_incremental_collection.py"
_DRIVER_BOOTSTRAP = (
    "source /opt/miniconda3/etc/profile.d/conda.sh && "
    "conda activate testbed && "
    f"python {_MOUNT}/{_DRIVER_FILE}"
)


_INCREMENTAL_PLUGIN_SOURCE = collection_identity._PLUGIN_SOURCE + r'''

import pytest as _zerorun_pytest

from zerorun_incremental_collection import classify_incremental_collection as _classify


def _full_collection(items, config):
    rows = [_item_row(item, config) for item in items]
    payload = {
        "items": rows,
        "nodeids": [row["nodeid"] for row in rows],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["collection_sha256"] = hashlib.sha256(encoded).hexdigest()
    return payload


@_zerorun_pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    # Run after every other collection modifier so the compared order and item
    # identities are exactly the ones this invocation would execute.
    yield
    plan_path = os.environ.get("ZERORUN_INCREMENTAL_PLAN")
    partition = os.environ.get("ZERORUN_INCREMENTAL_PARTITION")
    output_path = os.environ.get("ZERORUN_INCREMENTAL_OUTPUT")
    if not plan_path or not partition or not output_path:
        raise RuntimeError("ZeroRun incremental collection environment is incomplete")
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    matches = [entry for entry in plan if entry.get("partition") == partition]
    if len(matches) != 1:
        raise RuntimeError("ZeroRun incremental plan has no unique partition entry")
    entry = matches[0]
    baseline = entry.get("baseline_collection")
    source_fresh = entry.get("source_fresh_nodeids")
    if not isinstance(baseline, dict) or not isinstance(source_fresh, list):
        raise RuntimeError("ZeroRun incremental plan entry is incomplete")
    if not all(isinstance(nodeid, str) for nodeid in source_fresh):
        raise RuntimeError("ZeroRun source-fresh node list is malformed")

    current = _full_collection(list(items), config)
    classification = _classify(baseline, current)
    current_ids = set(current["nodeids"])
    unknown_source = sorted(set(source_fresh) - set(baseline.get("nodeids", [])))
    force_reason = entry.get("force_full_reason")
    if force_reason is not None and not isinstance(force_reason, str):
        raise RuntimeError("ZeroRun force-full reason is malformed")

    if force_reason is not None or unknown_source or classification.get("safe_incremental") is not True:
        fresh = list(current["nodeids"])
        mode = "whole-partition-fresh"
        reason = (
            force_reason
            or ("source-fresh node absent from baseline collection" if unknown_source else None)
            or classification.get("reason")
            or "unsafe incremental collection"
        )
    else:
        fresh_set = set(classification["fresh_current_nodes"])
        fresh_set.update(nodeid for nodeid in source_fresh if nodeid in current_ids)
        fresh = [nodeid for nodeid in current["nodeids"] if nodeid in fresh_set]
        mode = "single-pass-incremental"
        reason = None

    fresh_set = set(fresh)
    reusable = [nodeid for nodeid in current["nodeids"] if nodeid not in fresh_set]
    evidence = {
        "partition": partition,
        "mode": mode,
        "reason": reason,
        "force_full_reason": force_reason,
        "unknown_source_fresh_nodes": unknown_source,
        "source_fresh_current_nodes": [
            nodeid for nodeid in current["nodeids"] if nodeid in set(source_fresh)
        ],
        "fresh_nodeids": fresh,
        "reused_nodeids": reusable,
        "collection": current,
        "collection_delta": classification,
    }
    Path(output_path).write_text(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    config._zerorun_incremental_empty = bool(current["nodeids"]) and not fresh and bool(reusable)
    if reusable:
        deselected = [item for item in items if item.nodeid in set(reusable)]
        config.hook.pytest_deselected(items=deselected)
        items[:] = [item for item in items if item.nodeid in fresh_set]


def pytest_sessionfinish(session, exitstatus):
    if (
        getattr(session.config, "_zerorun_incremental_empty", False)
        and exitstatus == _zerorun_pytest.ExitCode.NO_TESTS_COLLECTED
    ):
        session.exitstatus = _zerorun_pytest.ExitCode.OK
'''


_DRIVER_SOURCE = r'''from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

_ROOT = Path("/workspace")
_SUPPORT = Path("/zerorun-batch")
_OUTPUT = Path("/zerorun-batch-output")
_PYTEST_INI = Path("/tmp/zerorun-pytest.ini")
_PYTEST_INI_TEXT = (
    "[pytest]\n"
    "xfail_strict = true\n"
    "pytester_example_dir = testing/example_scripts\n"
)
_PYTEST_ADDOPTS = "-W ignore::_pytest.warning_types.PytestUnknownMarkWarning"


def _clear_dir(path):
    try:
        entries = list(path.iterdir())
    except OSError:
        return
    for child in entries:
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            pass


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _prepare_incremental_pytest(child_env):
    """Recreate the stable task prelude discarded by direct argv execution."""
    _PYTEST_INI.write_text(_PYTEST_INI_TEXT, encoding="utf-8")
    child_env["PYTHONPATH"] = "%s:%s" % (_SUPPORT, _ROOT / "src")
    child_env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    child_env["PYTEST_ADDOPTS"] = _PYTEST_ADDOPTS


def _read_and_unlink(path):
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return data


def _run_forked_pytest(command, child_env, index):
    """Run one pristine pytest child without repeating shell/Conda startup.

    The container driver itself is activated in the pinned testbed environment.
    It forks before importing pytest, so every logical partition still receives
    independent pytest and tested-module state while sharing only the immutable
    already-activated interpreter/stdlib image.
    """
    if command[:3] != ["python", "-m", "pytest"]:
        raise RuntimeError("incremental pytest command is not direct module execution")
    stdout_path = _OUTPUT / ("stdout-%d.bin" % index)
    stderr_path = _OUTPUT / ("stderr-%d.bin" % index)
    for path in (stdout_path, stderr_path):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except BaseException:
        os.close(stdout_fd)
        raise
    try:
        pid = os.fork()
    except BaseException:
        os.close(stdout_fd)
        os.close(stderr_fd)
        raise
    if pid == 0:
        exit_code = 97
        try:
            os.dup2(stdout_fd, 1)
            os.dup2(stderr_fd, 2)
            os.close(stdout_fd)
            os.close(stderr_fd)
            os.chdir(_ROOT)
            os.environ.clear()
            os.environ.update(child_env)
            support_path = str(_SUPPORT)
            source_path = str(_ROOT / "src")
            sys.path[:] = [
                item for item in sys.path if item not in {support_path, source_path}
            ]
            sys.path.insert(0, source_path)
            sys.path.insert(0, support_path)
            sys.argv = ["pytest", *command[3:]]
            # Deliberately import after fork: pytest and project modules are
            # pristine and private to this logical partition.
            import pytest

            exit_code = int(pytest.console_main())
        except SystemExit as exc:
            if exc.code is None:
                exit_code = 0
            elif isinstance(exc.code, int):
                exit_code = exc.code
            else:
                print(str(exc.code), file=sys.stderr, flush=True)
                exit_code = 97
        except BaseException:
            traceback.print_exc()
            exit_code = 97
        finally:
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            finally:
                os._exit(exit_code if 0 <= exit_code <= 255 else 97)

    os.close(stdout_fd)
    os.close(stderr_fd)
    _, status = os.waitpid(pid, 0)
    return (
        os.waitstatus_to_exitcode(status),
        _read_and_unlink(stdout_path),
        _read_and_unlink(stderr_path),
    )


def main():
    plan = json.loads((_SUPPORT / "plan.json").read_text(encoding="utf-8"))
    rows = []
    for index, entry in enumerate(plan):
        # Separate subprocesses preserve Python/module state isolation. Clearing
        # the writable tmpfs mounts recreates the per-task writable filesystem
        # state that separate `docker run` calls previously provided.
        _clear_dir(Path("/tmp"))
        _clear_dir(_ROOT / ".zerorun")
        _clear_dir(_ROOT / ".git")
        command = entry["command"]
        child_env = os.environ.copy()
        selection_path = None
        if entry.get("mode") == "incremental":
            selection_path = _OUTPUT / ("selection-%s.json" % entry["partition"])
            try:
                selection_path.unlink()
            except FileNotFoundError:
                pass
            child_env["ZERORUN_INCREMENTAL_PLAN"] = str(
                _SUPPORT / ("incremental-plan-%d.json" % index)
            )
            child_env["ZERORUN_INCREMENTAL_PARTITION"] = entry["partition"]
            child_env["ZERORUN_INCREMENTAL_OUTPUT"] = str(selection_path)
            _prepare_incremental_pytest(child_env)
        started = time.perf_counter()
        if entry.get("mode") == "incremental":
            returncode, stdout, stderr = _run_forked_pytest(command, child_env, index)
            process_model = "fork-before-pytest-import"
        else:
            process = subprocess.run(
                command,
                cwd=_ROOT,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            returncode, stdout, stderr = (
                process.returncode,
                process.stdout,
                process.stderr,
            )
            process_model = "fresh-exec"
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        selection = None
        evidence_error = None
        if selection_path is not None:
            try:
                selection = json.loads(selection_path.read_text(encoding="utf-8"))
            except BaseException as exc:
                evidence_error = "%s: %s" % (type(exc).__name__, exc)
        reported_nodeids = list(entry["nodeids"])
        if isinstance(selection, dict) and isinstance(selection.get("fresh_nodeids"), list):
            reported_nodeids = list(selection["fresh_nodeids"])
        effective_exit_code = returncode if evidence_error is None else 97
        rows.append(
            {
                "partition": entry["partition"],
                "mode": entry.get("mode", "fresh"),
                "process_model": process_model,
                "node_count": len(reported_nodeids),
                "nodeids": reported_nodeids,
                "exit_code": effective_exit_code,
                "execution_ms": round(elapsed_ms, 3),
                "incremental_selection": selection,
                "incremental_evidence_error": evidence_error,
                "stdout_sha256": _digest(stdout),
                "stderr_sha256": _digest(stderr),
                "stdout_tail_b64": base64.b64encode(stdout[-4000:]).decode("ascii"),
                "stderr_tail_b64": base64.b64encode(stderr[-4000:]).decode("ascii"),
            }
        )
    (_OUTPUT / "results.json").write_text(
        json.dumps(rows, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return 0 if all(row["exit_code"] == 0 for row in rows) else 1


raise SystemExit(main())
'''


def incremental_plugin_source() -> str:
    """Return the exact embedded plugin bytes used by the pinned OCI executor."""
    return _INCREMENTAL_PLUGIN_SOURCE


def container_driver_source() -> str:
    """Return the exact embedded Linux driver used by the pinned OCI executor."""
    return _DRIVER_SOURCE


def _partition_map() -> dict[str, experiment.Partition]:
    return {partition.name: partition for partition in experiment.PARTITIONS}


def _node_belongs_to_partition(nodeid: str, partition: experiment.Partition) -> bool:
    return any(
        nodeid == target or nodeid.startswith(target + "::")
        for target in partition.targets
    )


def build_fresh_plan(
    fresh_nodes: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    """Build logical fresh-node commands without merging pytest configurations."""
    result_probe.base._configure_harness()
    partitions = _partition_map()
    plan: list[dict[str, Any]] = []
    for name in sorted(fresh_nodes):
        if name not in partitions:
            raise RuntimeError(f"unknown pytest partition in fresh batch: {name}")
        partition = partitions[name]
        nodeids = tuple(dict.fromkeys(str(nodeid) for nodeid in fresh_nodes[name]))
        if not nodeids:
            continue
        invalid = [nodeid for nodeid in nodeids if not _node_belongs_to_partition(nodeid, partition)]
        if invalid:
            raise RuntimeError(
                f"fresh nodes do not belong to pytest partition {name}: {invalid[:5]!r}"
            )
        candidate = experiment.Partition(
            partition.name,
            nodeids,
            partition.enabled_optional_plugins,
        )
        plan.append(
            {
                "partition": name,
                "node_count": len(nodeids),
                "nodeids": list(nodeids),
                "command": experiment._task_command(candidate),
            }
        )
    return plan


def _incremental_command(partition: experiment.Partition) -> list[str]:
    command = list(experiment._task_command(partition))
    if len(command) != 3 or command[:2] != ["bash", "-c"]:
        raise RuntimeError(
            f"unexpected pytest task command shape for {partition.name}: {command!r}"
        )
    script = command[2]
    path_marker = "export PYTHONPATH=/workspace/src"
    if path_marker not in script:
        raise RuntimeError(
            f"pytest task command has no PYTHONPATH marker for {partition.name}"
        )
    if "export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1" not in script:
        raise RuntimeError(
            f"pytest task command does not disable plugin autoload for {partition.name}"
        )
    addopts_marker = (
        "export PYTEST_ADDOPTS="
        "'-W ignore::_pytest.warning_types.PytestUnknownMarkWarning'"
    )
    if addopts_marker not in script:
        raise RuntimeError(
            f"pytest task command has unexpected PYTEST_ADDOPTS for {partition.name}"
        )
    config_marker = (
        "printf '[pytest]\\nxfail_strict = true\\n"
        "pytester_example_dir = testing/example_scripts\\n' "
        "> /tmp/zerorun-pytest.ini"
    )
    if config_marker not in script:
        raise RuntimeError(
            f"pytest task command has unexpected pytest config for {partition.name}"
        )
    python_marker = "python -m pytest"
    if python_marker not in script:
        raise RuntimeError(
            f"pytest task command has no Python module marker for {partition.name}"
        )
    pytest_args = shlex.split(script.split(python_marker, 1)[1].strip())
    return [
        "python",
        "-m",
        "pytest",
        "-p",
        _INCREMENTAL_PLUGIN_NAME,
        *pytest_args,
    ]


def _collection_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"items": payload.get("items"), "nodeids": payload.get("nodeids")},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_incremental_plan(
    baseline_collections: Mapping[str, Mapping[str, Any]],
    source_fresh_nodes: Mapping[str, Sequence[str]],
    *,
    force_full_reasons: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build one fresh-collect/select/execute invocation per logical partition.

    Every partition is required even when it has no source miss. That keeps
    collection fail-closed for core source edits while the in-process plugin
    removes exact reusable nodes only after comparing the full current v2
    collection against the cached baseline snapshot.
    """
    result_probe.base._configure_harness()
    partitions = _partition_map()
    expected = set(partitions)
    if set(baseline_collections) != expected:
        raise RuntimeError("incremental plan requires every reviewed pytest partition")
    if set(source_fresh_nodes) != expected:
        raise RuntimeError("incremental plan requires source-impact evidence for every partition")
    reasons = dict(force_full_reasons or {})
    if set(reasons) - expected:
        raise RuntimeError("incremental plan has a force-full reason for an unknown partition")
    if any(not isinstance(reason, str) or not reason.strip() for reason in reasons.values()):
        raise RuntimeError("incremental plan force-full reasons must be non-empty strings")

    plan: list[dict[str, Any]] = []
    for partition in experiment.PARTITIONS:
        name = partition.name
        baseline = dict(baseline_collections[name])
        baseline_nodeids = baseline.get("nodeids")
        baseline_items = baseline.get("items")
        if (
            not isinstance(baseline_nodeids, list)
            or not all(isinstance(nodeid, str) for nodeid in baseline_nodeids)
            or not isinstance(baseline_items, list)
            or len(baseline_items) != len(baseline_nodeids)
        ):
            raise RuntimeError(f"baseline collection is incomplete for {name}")
        # The classifier validates exact row ordering, duplicate node ids, and
        # structural identity fields. The stored digest is independently
        # recomputed so a malformed baseline cannot authorize deselection.
        incremental_collection.classify_incremental_collection(baseline, baseline)
        baseline_sha = baseline.get("collection_sha256")
        if not isinstance(baseline_sha, str) or baseline_sha != _collection_sha256(baseline):
            raise RuntimeError(f"baseline collection digest mismatch for {name}")
        raw_source_fresh = source_fresh_nodes[name]
        if isinstance(raw_source_fresh, (str, bytes)) or not isinstance(
            raw_source_fresh, Sequence
        ):
            raise RuntimeError(f"source-impact evidence is malformed for {name}")
        if not all(isinstance(nodeid, str) for nodeid in raw_source_fresh):
            raise RuntimeError(f"source-impact evidence contains a non-string node for {name}")
        source_fresh = tuple(dict.fromkeys(raw_source_fresh))
        invalid = [
            nodeid
            for nodeid in source_fresh
            if not _node_belongs_to_partition(nodeid, partition)
        ]
        if invalid:
            raise RuntimeError(
                f"source-fresh nodes do not belong to pytest partition {name}: {invalid[:5]!r}"
            )
        plan.append(
            {
                "mode": "incremental",
                "partition": name,
                "node_count": len(baseline_nodeids),
                "nodeids": list(baseline_nodeids),
                "command": _incremental_command(partition),
                "baseline_collection": baseline,
                "source_fresh_nodeids": list(source_fresh),
                "force_full_reason": reasons.get(name),
            }
        )
    return plan


def _validate_incremental_rows(
    plan: Sequence[dict[str, Any]],
    rows: Sequence[Any],
) -> None:
    if len(rows) != len(plan):
        raise RuntimeError("incremental result partition count differs from the plan")
    expected_names = [str(entry.get("partition")) for entry in plan]
    if len(set(expected_names)) != len(expected_names):
        raise RuntimeError("incremental plan contains duplicate partitions")

    for entry, raw_row in zip(plan, rows):
        name = str(entry["partition"])
        if not isinstance(raw_row, dict) or raw_row.get("partition") != name:
            raise RuntimeError(f"incremental result row does not match partition {name}")
        if raw_row.get("process_model") != "fork-before-pytest-import":
            raise RuntimeError(f"incremental result process model is invalid for {name}")
        selection = raw_row.get("incremental_selection")
        if not isinstance(selection, dict) or selection.get("partition") != name:
            raise RuntimeError(f"incremental selection evidence is missing for {name}")
        current = selection.get("collection")
        if not isinstance(current, dict):
            raise RuntimeError(f"incremental collection evidence is missing for {name}")
        current_sha = current.get("collection_sha256")
        if not isinstance(current_sha, str) or current_sha != _collection_sha256(current):
            raise RuntimeError(f"incremental collection digest mismatch for {name}")

        baseline = entry.get("baseline_collection")
        source_fresh = entry.get("source_fresh_nodeids")
        if not isinstance(baseline, dict) or not isinstance(source_fresh, list):
            raise RuntimeError(f"incremental plan evidence is malformed for {name}")
        classification = incremental_collection.classify_incremental_collection(
            baseline, current
        )
        current_nodeids = current.get("nodeids")
        baseline_nodeids = baseline.get("nodeids")
        fresh = selection.get("fresh_nodeids")
        reused = selection.get("reused_nodeids")
        if (
            not isinstance(current_nodeids, list)
            or not isinstance(baseline_nodeids, list)
            or not isinstance(fresh, list)
            or not isinstance(reused, list)
            or not all(isinstance(nodeid, str) for nodeid in fresh + reused)
            or len(fresh) != len(set(fresh))
            or len(reused) != len(set(reused))
        ):
            raise RuntimeError(f"incremental node selection is malformed for {name}")

        unknown_source = sorted(set(source_fresh) - set(baseline_nodeids))
        force_reason = entry.get("force_full_reason")
        if (
            force_reason is not None
            or unknown_source
            or classification.get("safe_incremental") is not True
        ):
            expected_fresh = list(current_nodeids)
            expected_mode = "whole-partition-fresh"
        else:
            expected_fresh_set = set(classification["fresh_current_nodes"])
            expected_fresh_set.update(
                nodeid for nodeid in source_fresh if nodeid in set(current_nodeids)
            )
            expected_fresh = [
                nodeid for nodeid in current_nodeids if nodeid in expected_fresh_set
            ]
            expected_mode = "single-pass-incremental"
        expected_fresh_set = set(expected_fresh)
        expected_reused = [
            nodeid for nodeid in current_nodeids if nodeid not in expected_fresh_set
        ]
        if fresh != expected_fresh or reused != expected_reused:
            raise RuntimeError(f"incremental selection differs from host classification for {name}")
        if selection.get("mode") != expected_mode:
            raise RuntimeError(f"incremental selection mode is invalid for {name}")
        if selection.get("collection_delta") != classification:
            raise RuntimeError(f"incremental collection delta is invalid for {name}")
        if raw_row.get("nodeids") != fresh or raw_row.get("node_count") != len(fresh):
            raise RuntimeError(f"incremental execution evidence differs from selection for {name}")


def build_fresh_plan_from_cache(
    decisions_by_partition: Mapping[str, Sequence[NodeCacheDecision]],
) -> list[dict[str, Any]]:
    """Convert initial fail-closed cache decisions into one physical miss plan."""
    fresh: dict[str, list[str]] = {}
    for partition, decisions in decisions_by_partition.items():
        nodeids = cache_fresh_nodeids(list(decisions))
        if nodeids:
            fresh[partition] = nodeids
    return build_fresh_plan(fresh)


def _shared_runtime_and_environment(
    manifest: Manifest,
    plan: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str], tuple[str, ...]]:
    if not plan:
        raise RuntimeError("cannot execute an empty pytest fresh batch")
    first_name = str(plan[0]["partition"])
    first_task = manifest.tasks[first_name]
    runtime = inspect_runtime(first_task)
    environment, fingerprint = hermetic_environment(first_task)
    env_names = tuple(first_task.env)
    for entry in plan[1:]:
        name = str(entry["partition"])
        task = manifest.tasks[name]
        candidate_runtime = inspect_runtime(task)
        candidate_environment, candidate_fingerprint = hermetic_environment(task)
        if candidate_runtime != runtime:
            raise RuntimeError(f"pytest batch crosses OCI runtime identity at {name}")
        if tuple(task.env) != env_names or candidate_fingerprint != fingerprint:
            raise RuntimeError(f"pytest batch crosses environment identity at {name}")
        if candidate_environment != environment:
            raise RuntimeError(f"pytest batch environment values differ at {name}")
    return runtime, environment, env_names


def prepare_workspace_mountpoints(root: Path) -> None:
    """Create only the host paths Docker must overmount inside the read-only checkout.

    Docker cannot create a missing tmpfs target beneath a read-only bind mount.
    ``.zerorun`` is ZeroRun-owned state, so creating the directory on the host is
    allowed; source files remain untouched. Existing symlinks or non-directories
    are rejected rather than followed. The benchmark checkout must also have a
    real ``.git`` directory because that path is hidden with tmpfs in the batch.
    """
    root = root.resolve(strict=True)
    git_dir = root / ".git"
    if is_link_like(git_dir) or not git_dir.is_dir():
        raise RuntimeError("pytest batch requires a real .git directory mountpoint")

    state = root / ".zerorun"
    if is_link_like(state) or (state.exists() and not state.is_dir()):
        raise RuntimeError("pytest batch .zerorun mountpoint is not a safe directory")
    try:
        create_private_directory(state)
    except FileExistsError:
        pass
    if is_link_like(state) or not state.is_dir():
        raise RuntimeError("pytest batch .zerorun mountpoint is not a safe directory")
    if os.name != "nt":
        state.chmod(0o700)


def container_driver_command(image: str) -> list[str]:
    """Launch the batch driver from the same pinned testbed environment as pytest."""
    return [image, "bash", "-c", _DRIVER_BOOTSTRAP]


def _execute_plan(
    manifest: Manifest,
    plan: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    runtime, environment, env_names = _shared_runtime_and_environment(manifest, plan)
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker is required for pytest batched execution")
    image = str(runtime["requested_image"])
    prepare_workspace_mountpoints(manifest.root)

    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-pytest-batch-"
    ) as temp_name:
        temp_root = temp_name.resolve(strict=True)
        support = temp_root / "input"
        evidence = temp_root / "output"
        support.mkdir()
        evidence.mkdir()
        (support / _DRIVER_FILE).write_text(_DRIVER_SOURCE, encoding="utf-8")
        (support / _PLAN_FILE).write_text(
            json.dumps(list(plan), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        incremental_mode = any(entry.get("mode") == "incremental" for entry in plan)
        if incremental_mode:
            if not all(entry.get("mode") == "incremental" for entry in plan):
                raise RuntimeError("pytest batch cannot mix fresh and incremental entries")
            (support / _INCREMENTAL_PLUGIN_FILE).write_text(
                _INCREMENTAL_PLUGIN_SOURCE,
                encoding="utf-8",
            )
            classifier_source = Path(incremental_collection.__file__).read_text(
                encoding="utf-8"
            )
            (support / _INCREMENTAL_CLASSIFIER_FILE).write_text(
                classifier_source,
                encoding="utf-8",
            )
            for index, entry in enumerate(plan):
                (support / f"incremental-plan-{index}.json").write_text(
                    json.dumps([entry], sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
        # Host files remain owner-writable for reliable temporary cleanup; the
        # container receives this directory through an explicit readonly bind.
        support.chmod(0o755)
        evidence.chmod(0o777)
        support_files = [support / _DRIVER_FILE, support / _PLAN_FILE]
        if incremental_mode:
            support_files.extend(
                [
                    support / _INCREMENTAL_PLUGIN_FILE,
                    support / _INCREMENTAL_CLASSIFIER_FILE,
                    *[
                        support / f"incremental-plan-{index}.json"
                        for index in range(len(plan))
                    ],
                ]
            )
        for path in support_files:
            path.chmod(0o644)

        command = [
            docker,
            "run",
            "--rm",
            "--pull",
            "never",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "512",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=512m",
            "--tmpfs",
            "/workspace/.zerorun:rw,nosuid,nodev,noexec,size=1m",
            "--tmpfs",
            "/workspace/.git:rw,nosuid,nodev,noexec,size=1m",
            "--mount",
            f"type=bind,src={manifest.root},dst=/workspace,readonly",
            "--mount",
            f"type=bind,src={support},dst={_MOUNT},readonly",
            "--mount",
            f"type=bind,src={evidence},dst={_OUTPUT_MOUNT}",
            "--workdir",
            "/workspace",
        ]
        for name, value in sorted(FIXED_CONTAINER_ENV.items()):
            command.extend(["--env", f"{name}={value}"])
        for name in env_names:
            if name in environment:
                command.extend(["--env", name])
        command.extend(container_driver_command(image))

        docker_environment = {name: environment[name] for name in env_names if name in environment}
        docker_environment.update(FIXED_CONTAINER_ENV)
        started = time.perf_counter()
        process = subprocess.run(
            command,
            cwd=manifest.root,
            env=docker_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        wall_ms = (time.perf_counter() - started) * 1000.0
        result_path = evidence / _RESULT_FILE
        if not result_path.is_file():
            detail = (process.stdout + process.stderr).decode("utf-8", errors="replace")[-8000:]
            raise RuntimeError(
                f"pytest batch container produced no result evidence ({process.returncode}): {detail}"
            )
        rows = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise RuntimeError("pytest batch result payload is invalid")
        if incremental_mode:
            _validate_incremental_rows(plan, rows)

    return {
        "pass": process.returncode == 0 and all(
            isinstance(row, dict) and row.get("exit_code") == 0 for row in rows
        ),
        "candidate_only": True,
        "authorizes_reuse": False,
        "execution_mode": "incremental" if incremental_mode else "fresh",
        "physical_container_count": 1,
        "pytest_process_count": len(plan),
        "wall_ms": round(wall_ms, 3),
        "plan_sha256": hashlib.sha256(
            json.dumps(list(plan), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "partitions": rows,
    }


def execute_fresh_plan(
    manifest: Manifest,
    plan: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Execute exact fresh node commands in one hardened container.

    Each logical partition remains a separate pytest subprocess and writable
    tmpfs state is cleared between subprocesses.
    """
    return _execute_plan(manifest, plan)


def execute_incremental_plan(
    manifest: Manifest,
    plan: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Fresh-collect every partition and execute only exact misses in-process."""
    if not plan or not all(entry.get("mode") == "incremental" for entry in plan):
        raise RuntimeError("incremental pytest execution requires a non-empty incremental plan")
    return _execute_plan(manifest, plan)


def successful_nodeids(result: Mapping[str, Any]) -> set[str]:
    """Return only exact requested nodeids covered by zero-exit subprocesses."""
    rows = result.get("partitions")
    if not isinstance(rows, list):
        raise RuntimeError("pytest batch result has no partition evidence")
    successful: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("pytest batch result contains an invalid partition row")
        nodeids = row.get("nodeids")
        if not isinstance(nodeids, list) or not all(isinstance(nodeid, str) for nodeid in nodeids):
            raise RuntimeError("pytest batch result partition has no exact nodeid evidence")
        if row.get("exit_code") == 0:
            successful.update(nodeids)
    return successful


def execution_ms_by_node(result: Mapping[str, Any]) -> dict[str, float]:
    """Allocate partition subprocess wall evenly for cache metadata diagnostics."""
    rows = result.get("partitions")
    if not isinstance(rows, list):
        raise RuntimeError("pytest batch result has no partition evidence")
    timing: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("exit_code") != 0:
            continue
        nodeids = row.get("nodeids")
        elapsed = row.get("execution_ms")
        if (
            not isinstance(nodeids, list)
            or not nodeids
            or not all(isinstance(nodeid, str) for nodeid in nodeids)
            or not isinstance(elapsed, (int, float))
        ):
            continue
        per_node = max(0.0, float(elapsed)) / len(nodeids)
        for nodeid in nodeids:
            timing[nodeid] = per_node
    return timing


def decode_tail(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        return ""
    try:
        return base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
    except (ValueError, UnicodeDecodeError):
        return ""
