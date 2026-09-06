from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import BinaryIO

from .fingerprint import (
    controlled_environment,
    resolve_launch_argv,
    sha256_file,
    task_fingerprint,
    validate_relative,
)
from .model import ConfigurationError, Manifest, RunResult, TaskSpec
from .normalization import normalize_successful_result
from .oci import _TRUNCATION_MARKER, _run_bounded_process
from .path_safety import is_link_like as _is_link_like
from .store import ProjectLockBusy, Store
from .workspace import StagedWorkspace


MAX_EVENT_FILE_BYTES = 8 * 1024 * 1024
MAX_EVENT_LINE_BYTES = 64 * 1024
MAX_EVENTS = 10_000
HOST_EXECUTION_TIMEOUT_SECONDS = 900
HOST_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
_EVENT_STATUS = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_EVENT_FLOAT_FIELDS = ("wall_ms", "execution_ms", "saved_ms")
_EVENT_COUNT_FIELDS = ("reused_nodes", "fresh_nodes", "total_nodes")


class _InputSnapshotChanged(ConfigurationError):
    """A cached or freshly computed result lost its source binding."""


def _emit(data: bytes, stream: BinaryIO) -> None:
    if data:
        stream.write(data)
        stream.flush()


def _execute(
    task: TaskSpec, cwd: Path, environment: dict[str, str], launch_argv: list[str]
) -> tuple[int, float, bytes, bytes]:
    started = time.perf_counter()
    try:
        process, timed_out = _run_bounded_process(
            launch_argv,
            cwd=cwd,
            environment=environment,
            timeout_seconds=HOST_EXECUTION_TIMEOUT_SECONDS,
            output_limit_bytes=HOST_OUTPUT_LIMIT_BYTES,
        )
    except OSError as exc:
        return 127, (time.perf_counter() - started) * 1000, b"", f"{exc}\n".encode()
    elapsed = (time.perf_counter() - started) * 1000
    stdout = process.stdout
    stderr = process.stderr
    returncode = process.returncode
    if timed_out:
        returncode = 124
        stderr += b"\nZeroRun: host execution exceeded the 900 second limit\n"
    if stdout.startswith(_TRUNCATION_MARKER) or stderr.startswith(_TRUNCATION_MARKER):
        if returncode == 0:
            returncode = 74
        stderr += (
            b"\nZeroRun: host execution exceeded the bounded output limit; "
            b"the result was not cached\n"
        )
    return returncode, elapsed, stdout, stderr


def _output_state(
    manifest: Manifest, task: TaskSpec, *, output_root: Path | None = None
) -> dict[str, dict[str, object]]:
    state: dict[str, dict[str, object]] = {}
    output_root = output_root or manifest.root
    for raw in task.outputs:
        relative = validate_relative(manifest.root, raw, field="output").relative_to(manifest.root).as_posix()
        path = output_root / relative
        if _is_link_like(path):
            try:
                target = os.readlink(path)
            except OSError:
                target = "<reparse-point>"
            state[relative] = {"type": "symlink", "target": target}
        elif path.is_file():
            state[relative] = {
                "type": "file",
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "mode": path.stat().st_mode & 0o777,
            }
        elif path.exists():
            state[relative] = {"type": "unsupported"}
        else:
            state[relative] = {"type": "missing"}
    return state


def safety_reason(task: TaskSpec) -> str | None:
    if not task.cacheable:
        return "task is not explicitly marked cacheable"
    if task.unsafe_effects:
        return "declared unsafe effects: " + ", ".join(task.unsafe_effects)
    return None


def _expected_output_state(cached: dict[str, object]) -> dict[str, dict[str, object]]:
    records = cached.get("outputs", [])
    if not isinstance(records, list):
        return {}
    return {
        str(record["path"]): {
            "type": "file",
            "sha256": record["sha256"],
            "size": record["size"],
            "mode": record["mode"],
        }
        for record in records
        if isinstance(record, dict)
    }


def _output_parent_directories(manifest: Manifest, task: TaskSpec) -> set[str]:
    parents: set[str] = set()
    for raw in task.outputs:
        relative = validate_relative(manifest.root, raw, field="output").relative_to(manifest.root)
        for parent in relative.parents:
            if parent != Path("."):
                parents.add(parent.as_posix())
    return parents


def _compare_with_cached(
    store: Store,
    manifest: Manifest,
    task: TaskSpec,
    key: str,
    cached: dict[str, object],
    *,
    exit_code: int,
    command_stdout: bytes,
    command_stderr: bytes,
    output_root: Path,
) -> tuple[bool, dict[str, object]]:
    actual_state = _output_state(manifest, task, output_root=output_root)
    expected_state = _expected_output_state(cached)
    cached_stdout, cached_stderr = store.streams(key, cached)
    streams_match = command_stdout == cached_stdout and command_stderr == cached_stderr
    matches = exit_code == int(cached.get("exit_code", 0)) and actual_state == expected_state and streams_match
    return matches, {
        "expected_exit_code": cached.get("exit_code"),
        "actual_exit_code": exit_code,
        "expected_outputs": expected_state,
        "actual_outputs": actual_state,
        "streams_match": streams_match,
    }


def run_task(
    manifest: Manifest,
    task: TaskSpec,
    *,
    force: bool = False,
    verify: bool = False,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
    lock_timeout_seconds: float = 60.0,
) -> RunResult:
    """Run a task, serializing cacheable publication within one project."""
    if safety_reason(task) is not None:
        return _run_task_unlocked(manifest, task, force=force, verify=verify, stdout=stdout, stderr=stderr)
    store = Store(manifest.root)
    started = time.perf_counter()
    try:
        with store.project_lock(timeout_seconds=lock_timeout_seconds):
            return _run_task_unlocked(manifest, task, force=force, verify=verify, stdout=stdout, stderr=stderr)
    except ProjectLockBusy as exc:
        return RunResult(
            task=task.name,
            status="BYPASS_PROJECT_BUSY",
            exit_code=75,
            wall_ms=(time.perf_counter() - started) * 1000,
            execution_ms=0.0,
            reason=str(exc),
        )


def _run_task_unlocked(
    manifest: Manifest,
    task: TaskSpec,
    *,
    force: bool = False,
    verify: bool = False,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> RunResult:
    stdout = stdout or sys.stdout.buffer
    stderr = stderr or sys.stderr.buffer
    wall_started = time.perf_counter()
    store = Store(manifest.root)
    policy_reason = safety_reason(task)
    reason = policy_reason
    key: str | None = None
    fingerprint: dict[str, object] | None = None
    cached: dict[str, object] | None = None
    invalid_cache_reason: str | None = None
    environment = controlled_environment(task)
    launch_argv = resolve_launch_argv(task.command, manifest.root, environment)

    if reason is None:
        key, fingerprint = task_fingerprint(manifest, task, environment=environment)
        cached = store.validated_metadata(manifest, task, key, fingerprint)
        if store.managed_file(store.entry(key) / "QUARANTINED").is_file():
            invalid_cache_reason = "matching cache entry is quarantined or malformed"

    if cached and not verify and not force:
        def verify_hit_snapshot() -> None:
            try:
                final_key, final_fingerprint = task_fingerprint(
                    manifest,
                    task,
                    environment=environment,
                )
            except ConfigurationError as exc:
                raise _InputSnapshotChanged(
                    f"declared inputs became unstable before cached reuse: {exc}"
                ) from exc
            if final_key != key or final_fingerprint != fingerprint:
                raise _InputSnapshotChanged(
                    "declared inputs changed before cached reuse"
                )

        try:
            cached_stdout, cached_stderr = store.streams(key, cached)
            restored = store.restore(
                manifest,
                task,
                key,
                fingerprint,
                cached,
                before_commit=verify_hit_snapshot,
            )
        except _InputSnapshotChanged as exc:
            wall_ms = (time.perf_counter() - wall_started) * 1000
            result = RunResult(
                task=task.name,
                status="REJECTED_INPUT_RACE",
                exit_code=75,
                wall_ms=wall_ms,
                execution_ms=0.0,
                cache_key=key,
                reason=str(exc),
            )
            store.append_event(result.as_dict())
            return result
        except ConfigurationError as exc:
            store.quarantine_entry(key, {"task": task.name, "key": key, "reason": f"restore rejected: {exc}"})
            cached = None
            invalid_cache_reason = "matching cache entry failed validation during restore"
        else:
            _emit(cached_stdout, stdout)
            _emit(cached_stderr, stderr)
            wall_ms = (time.perf_counter() - wall_started) * 1000
            execution_ms = float(cached.get("execution_ms", 0.0))
            result = RunResult(
                task=task.name,
                status="HIT_REPLAYED",
                exit_code=int(cached.get("exit_code", 0)),
                wall_ms=wall_ms,
                execution_ms=execution_ms,
                cache_key=key,
                restored_outputs=restored,
                saved_ms=max(0.0, execution_ms - wall_ms),
            )
            store.append_event(result.as_dict())
            return result

    if reason is not None:
        exit_code, execution_ms, command_stdout, command_stderr = _execute(
            task, manifest.root, environment, launch_argv
        )
        _emit(command_stdout, stdout)
        _emit(command_stderr, stderr)
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="BYPASS_UNSAFE",
            exit_code=exit_code,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason=reason,
        )
        store.append_event(result.as_dict())
        return result

    workspace = StagedWorkspace(store.state, manifest, task)
    try:
        workspace.prepare()
        staged_key, _ = task_fingerprint(manifest, task, environment=environment)
        if staged_key != key or not workspace.source_matches():
            raise ConfigurationError("declared inputs changed while the isolated workspace was being prepared")
        staged_launch_argv = workspace.launch_argv(environment)
    except ConfigurationError as exc:
        # The command still runs normally, but no cache entry is read or written.
        # This preserves the user's requested command semantics while refusing to
        # make a reuse claim for a task outside the isolated MVP contract.
        exit_code, execution_ms, command_stdout, command_stderr = _execute(
            task, manifest.root, environment, launch_argv
        )
        _emit(command_stdout, stdout)
        _emit(command_stderr, stderr)
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="BYPASS_UNSUPPORTED_INPUT",
            exit_code=exit_code,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason=str(exc),
        )
        store.append_event(result.as_dict())
        return result

    exit_code, execution_ms, command_stdout, command_stderr = _execute(
        task, workspace.root or manifest.root, environment, staged_launch_argv
    )

    if exit_code != 0:
        _emit(command_stdout, stdout)
        _emit(command_stderr, stderr)
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="MISS_FAILED",
            exit_code=exit_code,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason="isolated execution failed; existing project outputs were left unchanged",
        )
        store.append_event(result.as_dict())
        return result

    raw_stdout, raw_stderr = command_stdout, command_stderr
    try:
        command_stdout, command_stderr = normalize_successful_result(
            task, output_root=workspace.root or manifest.root, stdout=command_stdout, stderr=command_stderr
        )
    except ConfigurationError as exc:
        _emit(raw_stdout, stdout)
        _emit(raw_stderr, stderr)
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="REJECTED_NORMALIZATION",
            exit_code=79,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason=str(exc),
        )
        store.append_event(result.as_dict())
        return result

    _emit(command_stdout, stdout)
    _emit(command_stderr, stderr)

    actual_state = _output_state(manifest, task, output_root=workspace.root)
    if not all(state.get("type") == "file" for state in actual_state.values()):
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="BYPASS_UNSUPPORTED_OUTPUT",
            exit_code=exit_code,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason="isolated execution did not freshly produce every declared regular-file output; project outputs were left unchanged",
        )
        store.append_event(result.as_dict())
        return result

    unexpected_writes = workspace.unexpected_writes(
        output_parent_directories=_output_parent_directories(manifest, task)
    )
    if unexpected_writes:
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="REJECTED_UNDECLARED_WRITES",
            exit_code=78,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason="isolated task changed paths outside declared outputs: " + ", ".join(unexpected_writes[:8]),
        )
        store.append_event(result.as_dict())
        return result

    def verify_execution_snapshot() -> None:
        try:
            final_key, final_fingerprint = task_fingerprint(
                manifest,
                task,
                environment=environment,
            )
        except ConfigurationError as exc:
            raise _InputSnapshotChanged(
                f"declared inputs became unstable before fresh-result publication: {exc}"
            ) from exc
        if final_key != key or final_fingerprint != fingerprint:
            raise _InputSnapshotChanged(
                "declared inputs changed before fresh-result publication"
            )

    def reject_execution_snapshot(exc: _InputSnapshotChanged) -> RunResult:
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="REJECTED_INPUT_RACE",
            exit_code=75,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            reason=str(exc),
        )
        store.append_event(result.as_dict())
        return result

    try:
        verify_execution_snapshot()
    except _InputSnapshotChanged as exc:
        return reject_execution_snapshot(exc)

    wall_ms = (time.perf_counter() - wall_started) * 1000
    if cached and key:
        try:
            matches, details = _compare_with_cached(
                store,
                manifest,
                task,
                key,
                cached,
                exit_code=exit_code,
                command_stdout=command_stdout,
                command_stderr=command_stderr,
                output_root=workspace.root,
            )
        except ConfigurationError as exc:
            matches, details = False, {"reason": f"cache comparison could not be completed: {exc}"}
        if matches:
            status = "VERIFY_MATCH" if verify else "FORCE_VERIFY_MATCH"
            try:
                restored = store.publish_outputs(
                    manifest,
                    task,
                    workspace.root,
                    before_commit=verify_execution_snapshot,
                )
            except _InputSnapshotChanged as exc:
                return reject_execution_snapshot(exc)
            result = RunResult(
                task=task.name,
                status=status,
                exit_code=exit_code,
                wall_ms=wall_ms,
                execution_ms=execution_ms,
                cache_key=key,
                restored_outputs=restored,
                verified=True,
                reason="fresh execution matched the cached result",
            )
        else:
            mode = "verification" if verify else "forced execution"
            report = store.quarantine_entry(
                key,
                {"task": task.name, "key": key, "reason": f"{mode} diverged from cached evidence", **details},
            )
            status = "VERIFY_MISMATCH" if verify else "FORCE_CONFLICT"
            result = RunResult(
                task=task.name,
                status=status,
                exit_code=86,
                wall_ms=wall_ms,
                execution_ms=execution_ms,
                cache_key=key,
                verified=True,
                reason=f"cache quarantined; report: {report}",
            )
            # The current isolated execution is the result a normal command would
            # have produced. Publish it after quarantine, never reuse the old one.
            try:
                result.restored_outputs = store.publish_outputs(
                    manifest,
                    task,
                    workspace.root,
                    before_commit=verify_execution_snapshot,
                )
            except _InputSnapshotChanged as exc:
                return reject_execution_snapshot(exc)
        workspace.cleanup()
        result.wall_ms = (time.perf_counter() - wall_started) * 1000
        store.append_event(result.as_dict())
        return result

    if invalid_cache_reason is not None:
        try:
            restored = store.publish_outputs(
                manifest,
                task,
                workspace.root,
                before_commit=verify_execution_snapshot,
            )
        except _InputSnapshotChanged as exc:
            return reject_execution_snapshot(exc)
        workspace.cleanup()
        wall_ms = (time.perf_counter() - wall_started) * 1000
        result = RunResult(
            task=task.name,
            status="BYPASS_INVALID_CACHE",
            exit_code=exit_code,
            wall_ms=wall_ms,
            execution_ms=execution_ms,
            cache_key=key,
            restored_outputs=restored,
            reason=invalid_cache_reason,
        )
        store.append_event(result.as_dict())
        return result

    status = "FORCE_EXECUTED" if force else "MISS_EXECUTED"
    result_reason: str | None = None
    try:
        assert key is not None and fingerprint is not None
        store.save(
            manifest,
            task,
            key,
            fingerprint,
            execution_ms=execution_ms,
            stdout=command_stdout,
            stderr=command_stderr,
            source_root=workspace.root,
        )
        restored = store.publish_outputs(
            manifest,
            task,
            workspace.root,
            before_commit=verify_execution_snapshot,
        )
    except _InputSnapshotChanged as exc:
        return reject_execution_snapshot(exc)
    except ConfigurationError as exc:
        status = "BYPASS_UNSUPPORTED_OUTPUT"
        result_reason = str(exc)
        restored = []

    workspace.cleanup()
    wall_ms = (time.perf_counter() - wall_started) * 1000

    result = RunResult(
        task=task.name,
        status=status,
        exit_code=exit_code,
        wall_ms=wall_ms,
        execution_ms=execution_ms,
        cache_key=key,
        restored_outputs=restored,
        reason=result_reason,
    )
    store.append_event(result.as_dict())
    return result


def observe(
    command: list[str],
    *,
    cwd: Path | None = None,
    stdout: BinaryIO | None = None,
    stderr: BinaryIO | None = None,
) -> RunResult:
    if not command:
        raise ConfigurationError("observe requires a command after --")
    started = time.perf_counter()
    try:
        process, timed_out = _run_bounded_process(
            command,
            cwd=cwd,
            environment=dict(os.environ),
            timeout_seconds=HOST_EXECUTION_TIMEOUT_SECONDS,
            output_limit_bytes=HOST_OUTPUT_LIMIT_BYTES,
        )
        command_stdout = process.stdout or b""
        command_stderr = process.stderr or b""
        code = 124 if timed_out else process.returncode
        if timed_out:
            command_stderr += (
                b"\nZeroRun: observed host execution exceeded the 900 second limit\n"
            )
        if command_stdout.startswith(_TRUNCATION_MARKER) or command_stderr.startswith(
            _TRUNCATION_MARKER
        ):
            if code == 0:
                code = 74
            command_stderr += (
                b"\nZeroRun: observed host execution exceeded the bounded output limit\n"
            )
        _emit(command_stdout, stdout or sys.stdout.buffer)
        _emit(command_stderr, stderr or sys.stderr.buffer)
        reason = "trace-only measurement; unconfigured commands are never cached"
    except OSError as exc:
        code = 127
        reason = str(exc)
    elapsed = (time.perf_counter() - started) * 1000
    return RunResult(
        task="<observed>",
        status="OBSERVED_EXECUTED",
        exit_code=code,
        wall_ms=elapsed,
        execution_ms=elapsed,
        reason=reason,
    )


def read_events(store: Store) -> list[dict[str, object]]:
    events_path = store.managed_file(store.events)
    if not events_path.is_file():
        return []

    def discard_line(handle) -> None:
        while True:
            chunk = handle.readline(MAX_EVENT_LINE_BYTES + 1)
            if not chunk or chunk.endswith(b"\n"):
                return

    def sanitize(raw: object) -> dict[str, object] | None:
        if not isinstance(raw, dict):
            return None
        status = raw.get("status")
        if not isinstance(status, str) or not _EVENT_STATUS.fullmatch(status):
            return None
        event: dict[str, object] = {"status": status}
        for field in _EVENT_FLOAT_FIELDS:
            value = raw.get(field, 0.0)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            try:
                number = float(value)
            except (OverflowError, TypeError, ValueError):
                return None
            if not math.isfinite(number) or not 0.0 <= number <= 1e15:
                return None
            event[field] = number
        for field in _EVENT_COUNT_FIELDS:
            value = raw.get(field, 0)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 1_000_000_000
            ):
                return None
            event[field] = value
        return event

    try:
        size = events_path.stat().st_size
        start = max(0, size - MAX_EVENT_FILE_BYTES)
        from collections import deque

        events = deque(maxlen=MAX_EVENTS)
        with events_path.open("rb") as handle:
            if start:
                handle.seek(start)
                discard_line(handle)
            while True:
                line = handle.readline(MAX_EVENT_LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > MAX_EVENT_LINE_BYTES or not line.endswith(b"\n"):
                    if not line.endswith(b"\n"):
                        discard_line(handle)
                    continue
                try:
                    decoded = json.loads(line.decode("utf-8"))
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    RecursionError,
                    ValueError,
                ):
                    continue
                event = sanitize(decoded)
                if event is not None:
                    events.append(event)
        return list(events)
    except OSError:
        return []
