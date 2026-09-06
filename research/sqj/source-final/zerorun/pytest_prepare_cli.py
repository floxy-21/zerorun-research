from __future__ import annotations

import argparse
import json
from pathlib import Path

from .manifest import DEFAULT_MANIFEST, load_manifest
from .model import ConfigurationError, Manifest, TaskSpec
from .pytest_qualify import qualify_pytest_candidate, validate_repository_file_path
from .pytest_setup import _is_link_like, git_marker_kind, setup_managed_pytest_task
from .store import Store


def _looks_like_pytest(task: TaskSpec) -> bool:
    direct_python = (
        len(task.command) >= 3
        and Path(task.command[0]).name in {"python", "python3"}
        and list(task.command[1:3]) == ["-m", "pytest"]
    )
    managed_shell = (
        len(task.command) >= 4
        and Path(task.command[0]).name in {"sh", "bash"}
        and task.command[1] == ".zerorun-env/bin/python"
        and list(task.command[2:4]) == ["-m", "pytest"]
    )
    return direct_python or managed_shell


def discover_pytest_task(manifest: Manifest, requested: str | None) -> str:
    if requested is not None:
        if requested not in manifest.tasks:
            raise ConfigurationError(f"unknown task {requested!r}")
        if not _looks_like_pytest(manifest.tasks[requested]):
            raise ConfigurationError(
                "selected task is not a directly supported Python '-m pytest' task in adapter v1"
            )
        return requested
    candidates = [task.name for task in manifest.tasks.values() if _looks_like_pytest(task)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ConfigurationError(
            "no supported Python '-m pytest' ZeroRun task was found"
        )
    raise ConfigurationError(
        "multiple pytest tasks are configured; select one with --task: " + ", ".join(sorted(candidates))
    )


def default_targets(root: Path) -> tuple[str, ...]:
    for candidate in ("tests", "test", "testing"):
        if (root / candidate).exists():
            return (candidate,)
    return (".",)


def prepare_candidate(
    root: Path,
    *,
    task_name: str | None = None,
    targets: tuple[str, ...] | None = None,
    output: Path | None = None,
) -> dict:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ConfigurationError(f"pytest preparation root is not a directory: {root}")
    repository_root: Path | None = None
    for directory in (root, *root.parents):
        git_marker = directory / ".git"
        if _is_link_like(git_marker):
            raise ConfigurationError(
                "managed pytest preparation refuses a linked Git metadata marker"
            )
        if not git_marker.exists():
            continue
        git_marker_kind(directory)
        repository_root = directory
        break
    if repository_root is None:
        raise ConfigurationError("managed pytest preparation requires a Git repository")

    requested_output = output or (
        repository_root / ".zerorun-pytest.candidate.json"
    )
    if not requested_output.is_absolute():
        requested_output = repository_root / requested_output
    candidate_output = validate_repository_file_path(
        repository_root,
        requested_output,
        field="pytest candidate output",
    )

    manifest_path = validate_repository_file_path(
        repository_root,
        repository_root / DEFAULT_MANIFEST,
        field="ZeroRun manifest",
    )
    if not manifest_path.exists():
        setup_managed_pytest_task(
            repository_root,
            targets=targets,
        )
    if not manifest_path.is_file():
        raise ConfigurationError(
            f"managed pytest preparation requires a regular {DEFAULT_MANIFEST} at "
            f"the nearest repository root: {manifest_path}"
        )
    manifest = load_manifest(manifest_path)
    # Create only ZeroRun-owned local state before qualification; no project
    # source or test file is modified by the profiler.
    Store(manifest.root).ensure()
    selected = discover_pytest_task(manifest, task_name)
    selected_targets = targets or default_targets(manifest.root)
    return qualify_pytest_candidate(
        manifest,
        task_name=selected,
        targets=tuple(selected_targets),
        output=candidate_output,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zerorun-pytest-prepare",
        description=(
            "Profile a pinned pytest task and write a non-authorizing ZeroRun candidate profile. "
            "This command never enables reuse."
        ),
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--task")
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = prepare_candidate(
            args.root,
            task_name=args.task,
            targets=tuple(args.targets) if args.targets else None,
            output=args.output,
        )
    except ConfigurationError as exc:
        print(f"ZeroRun pytest preparation error: {exc}")
        return 2

    reviewable = sum(
        1 for row in payload["nodes"].values() if row.get("reviewable") is True
    )
    fresh = sum(
        1 for row in payload["nodes"].values() if row.get("fresh_required") is True
    )
    result = {
        "status": "CANDIDATE_READY",
        "authorizes_reuse": False,
        "reuse_activated": False,
        "candidate_sha256": payload["candidate_sha256"],
        "task": payload["task"],
        "targets": payload["targets"],
        "node_count": len(payload["nodes"]),
        "candidate_reviewable_nodes": reviewable,
        "fresh_required_nodes": fresh,
        "output": str((args.output or (Path(args.root).resolve() / ".zerorun-pytest.candidate.json")).resolve()),
        "next_action": "review closure completeness and node independence before activating any reuse",
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("ZeroRun pytest candidate prepared")
        print(f"task: {result['task']}")
        print(f"nodes: {result['node_count']} ({reviewable} candidate-reviewable, {fresh} fresh-required)")
        print(f"candidate: {result['candidate_sha256']}")
        print("reuse activated: no")
        print(f"next: {result['next_action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
