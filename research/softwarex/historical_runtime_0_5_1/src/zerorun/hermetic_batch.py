from __future__ import annotations

from typing import BinaryIO, Mapping

from .hermetic import run_hermetic_task
from .model import ConfigurationError, Manifest, RunResult, TaskSpec


def _validate_tasks(manifest: Manifest, tasks: tuple[TaskSpec, ...]) -> None:
    if manifest.version != 2:
        raise ConfigurationError("hermetic batch runner requires manifest version 2")
    if not tasks:
        raise ConfigurationError("hermetic batch runner requires at least one task")
    names = [task.name for task in tasks]
    if len(names) != len(set(names)):
        raise ConfigurationError("hermetic batch request contains duplicate task names")
    for task in tasks:
        if manifest.tasks.get(task.name) != task:
            raise ConfigurationError(
                f"batch task {task.name!r} does not match the loaded manifest"
            )
        if not task.cacheable or task.unsafe_effects:
            raise ConfigurationError(
                f"task {task.name!r}: hermetic v2 currently accepts only "
                "deterministic cacheable tasks"
            )


def _stream(streams: Mapping[str, BinaryIO], task: TaskSpec) -> BinaryIO:
    try:
        return streams[task.name]
    except KeyError as exc:
        raise ConfigurationError(
            f"batch request has no stream for task {task.name!r}"
        ) from exc


def run_hermetic_tasks(
    manifest: Manifest,
    tasks: tuple[TaskSpec, ...],
    *,
    stdout_by_task: Mapping[str, BinaryIO],
    stderr_by_task: Mapping[str, BinaryIO],
    lock_timeout_seconds: float = 60.0,
) -> list[RunResult]:
    """Run an ordered v2 task set with one exact namespace per task.

    A union checkout is not a sound execution optimization: a command can
    observe a file declared only by another task and publish that outcome under
    its smaller standalone key. The host page cache remains available, while
    every task receives precisely its own reviewed closure and therefore
    retains single/batch key equivalence.
    """

    _validate_tasks(manifest, tasks)
    # Resolve every stream before the first task can execute or publish. A
    # malformed later member must fail the whole request at preflight.
    streams = [
        (
            task,
            _stream(stdout_by_task, task),
            _stream(stderr_by_task, task),
        )
        for task in tasks
    ]
    return [
        run_hermetic_task(
            manifest,
            task,
            stdout=stdout,
            stderr=stderr,
            lock_timeout_seconds=lock_timeout_seconds,
        )
        for task, stdout, stderr in streams
    ]
