from __future__ import annotations

from typing import BinaryIO, Iterable, Mapping

from .model import Manifest, RunResult, TaskSpec
from .runner import run_task as run_legacy_task


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
    """Dispatch to the manifest-version-specific fail-closed runner."""
    if manifest.version == 2:
        import sys
        from .hermetic import run_hermetic_task
        return run_hermetic_task(
            manifest,
            task,
            force=force,
            verify=verify,
            stdout=stdout or sys.stdout.buffer,
            stderr=stderr or sys.stderr.buffer,
            lock_timeout_seconds=lock_timeout_seconds,
        )
    return run_legacy_task(
        manifest,
        task,
        force=force,
        verify=verify,
        stdout=stdout,
        stderr=stderr,
        lock_timeout_seconds=lock_timeout_seconds,
    )


def run_tasks(
    manifest: Manifest,
    tasks: Iterable[TaskSpec],
    *,
    stdout_by_task: Mapping[str, BinaryIO] | None = None,
    stderr_by_task: Mapping[str, BinaryIO] | None = None,
    lock_timeout_seconds: float = 60.0,
) -> list[RunResult]:
    """Run an ordered task set without widening any task's source namespace.

    Version 2 retains exact single-task snapshot semantics for every member;
    version 1 retains the established per-task path.
    """
    import sys

    ordered = tuple(tasks)
    stdout_streams = stdout_by_task or {
        task.name: sys.stdout.buffer for task in ordered
    }
    stderr_streams = stderr_by_task or {
        task.name: sys.stderr.buffer for task in ordered
    }
    if manifest.version == 2:
        from .hermetic_batch import run_hermetic_tasks

        return run_hermetic_tasks(
            manifest,
            ordered,
            stdout_by_task=stdout_streams,
            stderr_by_task=stderr_streams,
            lock_timeout_seconds=lock_timeout_seconds,
        )
    return [
        run_legacy_task(
            manifest,
            task,
            stdout=stdout_streams[task.name],
            stderr=stderr_streams[task.name],
            lock_timeout_seconds=lock_timeout_seconds,
        )
        for task in ordered
    ]
