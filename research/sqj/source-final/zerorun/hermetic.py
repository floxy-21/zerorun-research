from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import BinaryIO, Callable

from .hermetic_key import task_fingerprint_v2
from .hermetic_store import _action_lock, _emit, _load_result_entry, _save_result_entry
from .model import ConfigurationError, Manifest, RunResult, TaskSpec
from .oci import _docker_execute, hermetic_environment, inspect_runtime, validate_host
from .source_uncertainty import is_source_projection_uncertainty
from .store import ProjectLockBusy, Store
from .workspace import StagedWorkspace


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _execute_fresh_without_cache(
    manifest: Manifest,
    task: TaskSpec,
    runtime_identity: dict,
    environment: dict,
    *,
    reason: str,
    wall_started: float,
    phase_ms: dict[str, float],
    stdout: BinaryIO,
    stderr: BinaryIO,
    source_matches: Callable[[], bool],
    repository_root: Path,
) -> RunResult:
    """Execute safely when a reviewed symbol can no longer be projected.

    No action key exists in this state, so cache lookup and publication are both
    forbidden. This preserves fail-closed semantics while allowing a real source
    edit that removes/renames a reviewed symbol to continue via fresh execution.
    """
    phase_started = time.perf_counter()
    exit_code, execution_ms, command_stdout, command_stderr = _docker_execute(
        manifest,
        task,
        runtime_identity,
        environment,
        repository_root=repository_root,
    )
    phase_ms["docker_execution"] = _elapsed_ms(phase_started)
    _emit(command_stdout, stdout)
    _emit(command_stderr, stderr)
    if not source_matches():
        result = RunResult(
            task=task.name,
            status="REJECTED_INPUT_RACE",
            exit_code=75,
            wall_ms=_elapsed_ms(wall_started),
            execution_ms=execution_ms,
            cache_key=None,
            reason=(
                "reviewed source material changed while the private execution "
                "snapshot was running; no reusable result was published"
            ),
            phase_ms=phase_ms,
        )
        return result
    result = RunResult(
        task=task.name,
        status="MISS_EXECUTED" if exit_code == 0 else "MISS_FAILED",
        exit_code=exit_code,
        wall_ms=_elapsed_ms(wall_started),
        execution_ms=execution_ms,
        cache_key=None,
        reason=(
            "reviewed source projection became uncertain; cache bypassed and task executed fresh: "
            + reason
        ),
        phase_ms=phase_ms,
    )
    return result


def _run_hermetic_task_snapshot(
    manifest: Manifest,
    task: TaskSpec,
    *,
    live_manifest: Manifest,
    store: Store,
    source_matches: Callable[[], bool],
    wall_started: float,
    force: bool = False,
    verify: bool = False,
    stdout: BinaryIO,
    stderr: BinaryIO,
    lock_timeout_seconds: float = 60.0,
) -> RunResult:
    phase_ms: dict[str, float] = {}
    if manifest.version != 2:
        raise ConfigurationError("hermetic runner requires manifest version 2")
    if not task.cacheable or task.unsafe_effects:
        raise ConfigurationError(f"task {task.name!r}: hermetic v2 currently accepts only deterministic cacheable tasks")

    phase_started = time.perf_counter()
    runtime_identity = inspect_runtime(task, repository_root=live_manifest.root)
    phase_ms["runtime_inspect_initial"] = _elapsed_ms(phase_started)

    phase_started = time.perf_counter()
    environment, env_fp = hermetic_environment(task)
    phase_ms["environment"] = _elapsed_ms(phase_started)

    phase_started = time.perf_counter()
    try:
        key, fingerprint = task_fingerprint_v2(
            manifest, task, runtime_identity, environment_fingerprint=env_fp
        )
    except ConfigurationError as exc:
        phase_ms["fingerprint_initial"] = _elapsed_ms(phase_started)
        if not is_source_projection_uncertainty(task, exc):
            raise
        phase_ms["source_projection_bypass"] = phase_ms["fingerprint_initial"]
        return _execute_fresh_without_cache(
            manifest,
            task,
            runtime_identity,
            environment,
            reason=str(exc),
            wall_started=wall_started,
            phase_ms=phase_ms,
            stdout=stdout,
            stderr=stderr,
            source_matches=source_matches,
            repository_root=live_manifest.root,
        )
    phase_ms["fingerprint_initial"] = _elapsed_ms(phase_started)

    try:
        lock_started = time.perf_counter()
        with _action_lock(store, key, timeout_seconds=lock_timeout_seconds):
            phase_ms["lock_acquire"] = _elapsed_ms(lock_started)

            phase_started = time.perf_counter()
            cached = _load_result_entry(store, task, key, fingerprint)
            phase_ms["cache_lookup"] = _elapsed_ms(phase_started)
            if cached is not None and not force and not verify:
                phase_started = time.perf_counter()
                try:
                    final_key, final_fingerprint = task_fingerprint_v2(
                        live_manifest,
                        task,
                        runtime_identity,
                        environment_fingerprint=env_fp,
                    )
                    unchanged = final_key == key and final_fingerprint == fingerprint
                    race_reason = "source closure changed before cached reuse"
                except ConfigurationError as exc:
                    unchanged = False
                    race_reason = (
                        "reviewed source projection became uncertain before cached reuse: "
                        + str(exc)
                    )
                phase_ms["fingerprint_final"] = _elapsed_ms(phase_started)
                if not unchanged:
                    result = RunResult(
                        task=task.name,
                        status="REJECTED_INPUT_RACE",
                        exit_code=75,
                        wall_ms=_elapsed_ms(wall_started),
                        execution_ms=0.0,
                        cache_key=key,
                        reason=race_reason,
                        phase_ms=phase_ms,
                    )
                    return result
                wall_ms = _elapsed_ms(wall_started)
                execution_ms = float(cached.get("execution_ms", 0.0))
                result = RunResult(
                    task=task.name,
                    status="HIT_REUSED",
                    exit_code=0,
                    wall_ms=wall_ms,
                    execution_ms=execution_ms,
                    cache_key=key,
                    saved_ms=max(0.0, execution_ms - wall_ms),
                    reason="exact reviewed source closure and pinned runtime identity matched",
                    phase_ms=phase_ms,
                )
                return result

            phase_started = time.perf_counter()
            exit_code, execution_ms, command_stdout, command_stderr = _docker_execute(
                manifest,
                task,
                runtime_identity,
                environment,
                repository_root=live_manifest.root,
            )
            phase_ms["docker_execution"] = _elapsed_ms(phase_started)
            _emit(command_stdout, stdout)
            _emit(command_stderr, stderr)

            # The runtime identity is a content-addressed OCI digest attested
            # before execution, and _docker_execute uses --pull=never. It cannot
            # silently become a different runtime under the same identity while
            # the task is running. Revalidate the mutable source closure only.
            phase_started = time.perf_counter()
            try:
                final_key, final_fingerprint = task_fingerprint_v2(
                    live_manifest,
                    task,
                    runtime_identity,
                    environment_fingerprint=env_fp,
                )
            except ConfigurationError as exc:
                phase_ms["fingerprint_final"] = _elapsed_ms(phase_started)
                if not is_source_projection_uncertainty(task, exc):
                    raise
                result = RunResult(
                    task=task.name,
                    status="REJECTED_INPUT_RACE",
                    exit_code=75,
                    wall_ms=_elapsed_ms(wall_started),
                    execution_ms=execution_ms,
                    cache_key=key,
                    reason=(
                        "reviewed source projection became uncertain during execution; "
                        "result was not published: " + str(exc)
                    ),
                    phase_ms=phase_ms,
                )
                return result
            phase_ms["fingerprint_final"] = _elapsed_ms(phase_started)
            if final_key != key or final_fingerprint != fingerprint:
                result = RunResult(
                    task=task.name,
                    status="REJECTED_INPUT_RACE",
                    exit_code=75,
                    wall_ms=_elapsed_ms(wall_started),
                    execution_ms=execution_ms,
                    cache_key=key,
                    reason="source closure changed during execution",
                    phase_ms=phase_ms,
                )
                return result

            if cached is not None:
                if exit_code == 0:
                    status = "VERIFY_MATCH" if verify else "FORCE_VERIFY_MATCH"
                    result = RunResult(
                        task=task.name,
                        status=status,
                        exit_code=0,
                        wall_ms=_elapsed_ms(wall_started),
                        execution_ms=execution_ms,
                        cache_key=key,
                        verified=True,
                        reason="fresh hermetic execution matched cached successful result",
                        phase_ms=phase_ms,
                    )
                else:
                    quarantine_started = time.perf_counter()
                    report = store.quarantine_entry(
                        key,
                        {
                            "task": task.name,
                            "key": key,
                            "reason": "fresh hermetic execution failed while cached result claimed success",
                            "actual_exit_code": exit_code,
                        },
                    )
                    phase_ms["quarantine"] = _elapsed_ms(quarantine_started)
                    result = RunResult(
                        task=task.name,
                        status="VERIFY_MISMATCH" if verify else "FORCE_CONFLICT",
                        exit_code=86,
                        wall_ms=_elapsed_ms(wall_started),
                        execution_ms=execution_ms,
                        cache_key=key,
                        verified=True,
                        reason=f"cache quarantined; report: {report}",
                        phase_ms=phase_ms,
                    )
                return result

            if exit_code != 0:
                result = RunResult(
                    task=task.name,
                    status="MISS_FAILED",
                    exit_code=exit_code,
                    wall_ms=_elapsed_ms(wall_started),
                    execution_ms=execution_ms,
                    cache_key=key,
                    reason="fresh hermetic task failed; no reusable result was stored",
                    phase_ms=phase_ms,
                )
                return result

            publish_started = time.perf_counter()
            _save_result_entry(store, task, key, fingerprint, execution_ms=execution_ms)
            phase_ms["cache_publish"] = _elapsed_ms(publish_started)
            result = RunResult(
                task=task.name,
                status="FORCE_EXECUTED" if force else "MISS_EXECUTED",
                exit_code=0,
                wall_ms=_elapsed_ms(wall_started),
                execution_ms=execution_ms,
                cache_key=key,
                reason="fresh hermetic result stored",
                phase_ms=phase_ms,
            )
            return result
    except ProjectLockBusy as exc:
        phase_ms["lock_acquire"] = _elapsed_ms(lock_started)
        return RunResult(
            task=task.name,
            status="BYPASS_ACTION_BUSY",
            exit_code=75,
            wall_ms=_elapsed_ms(wall_started),
            execution_ms=0.0,
            cache_key=key,
            reason=str(exc),
            phase_ms=phase_ms,
        )


def _try_readonly_whole_task_hit(
    manifest: Manifest,
    task: TaskSpec,
    store: Store,
    *,
    wall_started: float,
    lock_timeout_seconds: float,
) -> RunResult | None:
    """Authenticate and recheck a whole-file hit without creating a copy.

    No repository code runs on this path. Both complete content fingerprints
    retain the normal runtime, environment, directory-mode and input checks.
    Symbol projections deliberately keep the existing snapshot path. A miss
    cannot execute here; it falls through to the immutable execution snapshot.
    """
    if task.input_symbols:
        return None
    phase_ms: dict[str, float] = {}
    started = time.perf_counter()
    runtime = inspect_runtime(task, repository_root=manifest.root)
    phase_ms["runtime_inspect_initial"] = _elapsed_ms(started)
    started = time.perf_counter()
    _, environment_fingerprint = hermetic_environment(task)
    phase_ms["environment"] = _elapsed_ms(started)
    started = time.perf_counter()
    key, fingerprint = task_fingerprint_v2(
        manifest, task, runtime, environment_fingerprint=environment_fingerprint
    )
    phase_ms["fingerprint_initial"] = _elapsed_ms(started)
    started = time.perf_counter()
    try:
        with _action_lock(store, key, timeout_seconds=lock_timeout_seconds):
            phase_ms["lock_acquire"] = _elapsed_ms(started)
            started = time.perf_counter()
            cached = _load_result_entry(store, task, key, fingerprint)
            phase_ms["cache_lookup"] = _elapsed_ms(started)
            if cached is None:
                return None
            started = time.perf_counter()
            try:
                final_key, final_fingerprint = task_fingerprint_v2(
                    manifest, task, runtime,
                    environment_fingerprint=environment_fingerprint,
                )
                unchanged = key == final_key and fingerprint == final_fingerprint
            except ConfigurationError:
                unchanged = False
            phase_ms["fingerprint_final"] = _elapsed_ms(started)
            phase_ms["snapshot_prepare"] = 0.0
            phase_ms["snapshot_cleanup"] = 0.0
            phase_ms["readonly_hit_path"] = _elapsed_ms(wall_started)
            return RunResult(
                task=task.name,
                status="HIT_REUSED" if unchanged else "REJECTED_INPUT_RACE",
                exit_code=0 if unchanged else 75,
                wall_ms=_elapsed_ms(wall_started),
                execution_ms=float(cached.get("execution_ms", 0.0)) if unchanged else 0.0,
                cache_key=key,
                reason=(
                    "authenticated whole-file result matched two complete source fingerprints; no execution copy needed"
                    if unchanged else "source closure changed before cached reuse"
                ),
                phase_ms=phase_ms,
            )
    except ProjectLockBusy as exc:
        phase_ms["lock_acquire"] = _elapsed_ms(started)
        return RunResult(
            task=task.name, status="BYPASS_ACTION_BUSY", exit_code=75,
            wall_ms=_elapsed_ms(wall_started), execution_ms=0.0,
            cache_key=key, reason=str(exc), phase_ms=phase_ms,
        )


def run_hermetic_task(
    manifest: Manifest,
    task: TaskSpec,
    *,
    force: bool = False,
    verify: bool = False,
    stdout: BinaryIO,
    stderr: BinaryIO,
    lock_timeout_seconds: float = 60.0,
) -> RunResult:
    """Execute v2 from an immutable private copy of its reviewed closure."""

    wall_started = time.perf_counter()
    if manifest.version != 2:
        raise ConfigurationError("hermetic runner requires manifest version 2")
    if not task.cacheable or task.unsafe_effects:
        raise ConfigurationError(
            f"task {task.name!r}: hermetic v2 currently accepts only deterministic cacheable tasks"
        )
    # Reject unsupported hosts before enumerating, hashing, or copying any
    # repository material and before creating mutable project state.
    validate_host()

    store = Store(manifest.root)
    store.ensure()
    if not force and not verify:
        hit = _try_readonly_whole_task_hit(
            manifest, task, store, wall_started=wall_started,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        if hit is not None:
            hit.saved_ms = max(0.0, hit.execution_ms - hit.wall_ms) if hit.status == "HIT_REUSED" else 0.0
            event_started = time.perf_counter()
            store.append_event(hit.as_dict())
            hit.phase_ms["event_append"] = _elapsed_ms(event_started)
            return hit
    snapshots = store.managed_directory(store.state / "snapshots")
    workspace = StagedWorkspace(
        snapshots,
        manifest,
        task,
        track_writes=False,
        create_git_mask_marker=True,
        deterministic_metadata=True,
    )
    result: RunResult | None = None
    prepare_started = time.perf_counter()
    prepare_ms = 0.0
    cleanup_ms = 0.0
    try:
        workspace.prepare()
        if not workspace.source_matches():
            # One bounded retry distinguishes ordinary editor churn from a
            # persistently unstable checkout without ever executing live files.
            workspace.cleanup()
            workspace.prepare()
            if not workspace.source_matches():
                result = RunResult(
                    task=task.name,
                    status="REJECTED_INPUT_RACE",
                    exit_code=75,
                    wall_ms=0.0,
                    execution_ms=0.0,
                    reason="reviewed source material changed while creating a private execution snapshot",
                )
        prepare_ms = _elapsed_ms(prepare_started)
        if result is None:
            assert workspace.root is not None
            snapshot_manifest = replace(
                manifest,
                root=workspace.root,
                path=workspace.root / manifest.path.name,
            )
            result = _run_hermetic_task_snapshot(
                snapshot_manifest,
                task,
                live_manifest=manifest,
                store=store,
                source_matches=workspace.source_matches,
                wall_started=wall_started,
                force=force,
                verify=verify,
                stdout=stdout,
                stderr=stderr,
                lock_timeout_seconds=lock_timeout_seconds,
            )
    finally:
        cleanup_started = time.perf_counter()
        workspace.cleanup()
        cleanup_ms = _elapsed_ms(cleanup_started)

    assert result is not None
    result.phase_ms["snapshot_prepare"] = prepare_ms
    result.phase_ms["snapshot_cleanup"] = cleanup_ms
    result.wall_ms = _elapsed_ms(wall_started)
    if result.status == "HIT_REUSED":
        result.saved_ms = max(0.0, result.execution_ms - result.wall_ms)
    event_started = time.perf_counter()
    store.append_event(result.as_dict())
    result.phase_ms["event_append"] = _elapsed_ms(event_started)
    return result
