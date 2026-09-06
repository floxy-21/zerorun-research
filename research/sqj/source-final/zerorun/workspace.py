from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .fingerprint import (
    _path_record,
    _is_link_like,
    command_sources,
    execution_directory_records,
    expand_inputs,
    is_within,
    project_state,
    resolve_launch_argv,
    validate_relative,
)
from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import create_private_temp_directory


def _relative_record_paths(manifest: Manifest, task: TaskSpec) -> list[str]:
    """Return the project-relative paths an isolated task is allowed to receive."""
    outputs = {
        validate_relative(manifest.root, raw, field="output").relative_to(manifest.root).as_posix()
        for raw in task.outputs
    }
    records = [
        *expand_inputs(manifest.root, task.inputs),
        *command_sources(task.command, manifest.root, excluded=outputs),
    ]
    paths: set[str] = set()
    for record in records:
        kind = record.get("type")
        relative = record.get("path")
        if kind == "missing":
            continue
        if not isinstance(relative, str) or not relative:
            raise ConfigurationError(f"task {task.name!r}: input record is malformed")
        if relative == ".zerorun" or relative.startswith(".zerorun/"):
            raise ConfigurationError(
                f"task {task.name!r}: isolated source material cannot include .zerorun state"
            )
        if relative == ".git" or relative.startswith(".git/"):
            raise ConfigurationError(
                f"task {task.name!r}: isolated source material cannot include .git state"
            )
        if kind == "symlink":
            raise ConfigurationError(
                f"task {task.name!r}: cacheable isolated execution does not support symlink inputs: {relative}"
            )
        if kind not in {"file", "directory"}:
            raise ConfigurationError(
                f"task {task.name!r}: cacheable isolated execution only supports regular-file and directory inputs: {relative}"
            )
        paths.add(relative)
    for selector in task.input_symbols:
        path_text, _, symbol = selector.rpartition("::")
        if not path_text or not symbol:
            raise ConfigurationError(
                f"task {task.name!r}: invalid reviewed input symbol selector: {selector}"
            )
        source = validate_relative(manifest.root, path_text, field="input_symbol")
        if _is_link_like(source) or not source.is_file():
            raise ConfigurationError(
                f"task {task.name!r}: reviewed input symbol source must be a regular file: {path_text}"
            )
        paths.add(source.relative_to(manifest.root).as_posix())
    return sorted(paths, key=lambda value: (value.count("/"), value))


def _material_record(root: Path, relative: str) -> dict[str, object]:
    path = validate_relative(root, relative, field="staged_input")
    record = dict(_path_record(root, path))
    # mtimes are diagnostic metadata, not execution content, and some file
    # systems cannot preserve their full precision across a private copy.
    record.pop("mtime_ns", None)
    return record


def _validated_exact_paths(
    manifest: Manifest,
    task: TaskSpec,
    paths: tuple[str, ...],
) -> list[str]:
    """Validate already-expanded paths without interpreting them as globs."""

    if len(paths) != len(set(paths)):
        raise ConfigurationError(
            f"task {task.name!r}: exact isolated source paths contain duplicates"
        )
    validated: list[str] = []
    for relative in paths:
        if not isinstance(relative, str) or not relative:
            raise ConfigurationError(
                f"task {task.name!r}: exact isolated source path is malformed"
            )
        if relative == ".zerorun" or relative.startswith(".zerorun/"):
            raise ConfigurationError(
                f"task {task.name!r}: isolated source material cannot include .zerorun state"
            )
        if relative == ".git" or relative.startswith(".git/"):
            raise ConfigurationError(
                f"task {task.name!r}: isolated source material cannot include .git state"
            )
        source = validate_relative(manifest.root, relative, field="exact_input")
        if _is_link_like(source):
            raise ConfigurationError(
                f"task {task.name!r}: cacheable isolated execution does not support symlink inputs: {relative}"
            )
        if not source.is_file() and not source.is_dir():
            raise ConfigurationError(
                f"task {task.name!r}: exact isolated source changed or is unsupported: {relative}"
            )
        validated.append(source.relative_to(manifest.root).as_posix())
    return sorted(validated, key=lambda value: (value.count("/"), value))


def _copy_record(
    source_root: Path,
    destination_root: Path,
    relative: str,
    *,
    deterministic_metadata: bool,
) -> None:
    source = validate_relative(source_root, relative, field="input")
    destination = (destination_root / relative).resolve(strict=False)
    if not is_within(destination_root, destination):
        raise ConfigurationError(f"staged input escapes isolated workspace: {relative}")
    if _is_link_like(source):
        raise ConfigurationError(f"cacheable isolated execution does not support symlink inputs: {relative}")
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        return
    if not source.is_file():
        raise ConfigurationError(f"declared input changed while staging: {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if deterministic_metadata:
        # copy2 would preserve unkeyed timestamps/xattrs. Copy only bytes, then
        # apply the keyed mode and a deterministic timestamp policy.
        shutil.copyfile(source, destination, follow_symlinks=False)
        _apply_mode_and_epoch(source, destination)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def _apply_mode_and_epoch(source: Path, destination: Path) -> None:
    details = source.stat(follow_symlinks=False)
    os.chmod(destination, stat.S_IMODE(details.st_mode))
    try:
        os.utime(destination, ns=(0, 0), follow_symlinks=False)
    except NotImplementedError:
        # Windows does not expose follow_symlinks for utime. Every destination
        # is newly created and link-like paths were rejected before this call.
        os.utime(destination, ns=(0, 0))


def _apply_fixed_directory_metadata(destination: Path, mode: int) -> None:
    os.chmod(destination, mode)
    try:
        os.utime(destination, ns=(0, 0), follow_symlinks=False)
    except NotImplementedError:
        os.utime(destination, ns=(0, 0))


def _assert_workspace_target(state: Path, root: Path) -> None:
    """Validate the exact private temp root before recursive cleanup."""

    state_absolute = Path(os.path.abspath(state))
    root_absolute = Path(os.path.abspath(root))
    if (
        root_absolute.parent != state_absolute
        or not root_absolute.name.startswith("zerorun-run-")
        or _is_link_like(state_absolute)
        or _is_link_like(root_absolute)
        or os.path.normcase(str(state_absolute.resolve(strict=True)))
        != os.path.normcase(str(state_absolute))
        or os.path.normcase(str(root_absolute.resolve(strict=True)))
        != os.path.normcase(str(root_absolute))
        or not root_absolute.is_dir()
    ):
        raise ConfigurationError(
            f"refusing cleanup of an invalid isolated workspace target: {root_absolute}"
        )


def _rmtree_onerror(function, path: str, exc_info, *, root: Path) -> None:
    candidate = Path(os.path.abspath(path))
    # On POSIX unlink permission belongs to the parent directory, not the file.
    # A staged read-only dependency directory must be made removable too. Limit
    # this repair to the previously validated private tree and never chmod links.
    if (
        not isinstance(exc_info[1], PermissionError)
        or not is_within(root, candidate)
        or _is_link_like(candidate)
        or candidate.resolve(strict=True) != candidate
    ):
        raise exc_info[1]
    try:
        if os.name == "posix" and candidate != root:
            parent = candidate.parent
            if not is_within(root, parent) or _is_link_like(parent):
                raise exc_info[1]
            descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fchmod(descriptor, stat.S_IRWXU)
            finally:
                os.close(descriptor)
        os.chmod(candidate, stat.S_IRWXU)
        function(path)
    except OSError:
        raise exc_info[1]


def _remove_workspace(state: Path, root: Path) -> None:
    if not root.exists() and not _is_link_like(root):
        return
    _assert_workspace_target(state, root)
    # The reviewed source can legitimately have a non-writable root/ancestor.
    # Make only this already-validated temp tree removable; rmtree never follows
    # directory symlinks, and none are accepted during staging.
    os.chmod(root, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    try:
        shutil.rmtree(
            root,
            onerror=lambda function, path, error: _rmtree_onerror(
                function, path, error, root=root
            ),
        )
    except OSError as exc:
        raise ConfigurationError(
            f"isolated workspace cleanup failed for exact target: {root}"
        ) from exc
    if root.exists() or _is_link_like(root):
        raise ConfigurationError(
            f"isolated workspace cleanup was not confirmed: {root}"
        )


def _source_directories(root: Path, paths: list[str]) -> list[str]:
    """Return every existing source directory needed to traverse exact paths."""

    directories: set[str] = set()
    for relative in paths:
        source = validate_relative(root, relative, field="input")
        cursor = source if source.is_dir() else source.parent
        while cursor != root:
            directories.add(cursor.relative_to(root).as_posix())
            cursor = cursor.parent
    return sorted(directories, key=lambda value: value.count("/"), reverse=True)


@dataclass
class StagedWorkspace:
    """A scratch copy of declared task material used for cacheable executions.

    The process never receives the repository as its working directory. That keeps
    undeclared writes out of the user checkout and lets the write guard inspect a
    small, explicit workspace rather than walking an arbitrary repository twice.
    This is an isolation boundary for supported deterministic tasks, not an OS
    sandbox for a hostile command with access to absolute host paths.
    """

    state: Path
    manifest: Manifest
    task: TaskSpec
    root: Path | None = None
    input_paths: list[str] = field(default_factory=list)
    before_state: dict[str, dict[str, object]] = field(default_factory=dict)
    track_writes: bool = True
    exact_input_paths: tuple[str, ...] | None = None
    create_git_mask_marker: bool = False
    deterministic_metadata: bool = False

    def prepare(self) -> None:
        # Resolve the source set before creating a destination beneath
        # ``.zerorun``. A malicious broad/hidden glob must never discover and
        # recursively stage the workspace being created for it.
        self.input_paths = (
            _relative_record_paths(self.manifest, self.task)
            if self.exact_input_paths is None
            else _validated_exact_paths(
                self.manifest,
                self.task,
                self.exact_input_paths,
            )
        )
        self.state.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.state, 0o700)
        self.root = create_private_temp_directory(self.state, prefix="zerorun-run-")
        try:
            for relative in self.input_paths:
                _copy_record(
                    self.manifest.root,
                    self.root,
                    relative,
                    deterministic_metadata=self.deterministic_metadata,
                )
            if self.create_git_mask_marker:
                # Docker overlays both locations so repository metadata and
                # mutable ZeroRun state are hidden from the task.  The empty
                # targets must already exist: current runc correctly refuses
                # to create a mountpoint inside the read-only /workspace bind.
                for marker_name in (".git", ".zerorun"):
                    marker = self.root / marker_name
                    if marker.exists() or _is_link_like(marker):
                        raise ConfigurationError(
                            "isolated source closure unexpectedly contains "
                            f"{marker_name} state"
                        )
                    marker.mkdir()
                    _apply_fixed_directory_metadata(marker, 0o700)

            # Apply only source modes and deterministic zero timestamps after
            # all children (and the synthetic mask marker) are present. This
            # supports non-root image users without retaining unkeyed mtimes,
            # xattrs, ownership, or ACL metadata from the live checkout.
            for relative in _source_directories(
                self.manifest.root,
                self.input_paths,
            ):
                source = validate_relative(self.manifest.root, relative, field="input")
                destination = self.root / relative
                if source.is_dir() and destination.is_dir():
                    if self.deterministic_metadata:
                        _apply_mode_and_epoch(source, destination)
                    else:
                        shutil.copystat(source, destination, follow_symlinks=False)
            if self.deterministic_metadata:
                _apply_mode_and_epoch(self.manifest.root, self.root)
            else:
                shutil.copystat(
                    self.manifest.root,
                    self.root,
                    follow_symlinks=False,
                )
            if self.track_writes:
                self.before_state = project_state(
                    self.root, excluded=set(self.output_paths())
                )
        except Exception:
            self.cleanup()
            raise

    def output_paths(self) -> list[str]:
        return [
            validate_relative(self.manifest.root, raw, field="output").relative_to(self.manifest.root).as_posix()
            for raw in self.task.outputs
        ]

    def source_matches(self) -> bool:
        """Return whether every staged byte/type/mode still matches its source."""

        if self.root is None:
            raise ConfigurationError("isolated workspace was not prepared")
        try:
            current_paths = (
                _relative_record_paths(self.manifest, self.task)
                if self.exact_input_paths is None
                else _validated_exact_paths(
                    self.manifest,
                    self.task,
                    self.exact_input_paths,
                )
            )
            if current_paths != self.input_paths:
                return False
            if self.deterministic_metadata and execution_directory_records(
                self.manifest.root, self.input_paths
            ) != execution_directory_records(self.root, self.input_paths):
                return False
            return all(
                _material_record(self.manifest.root, relative)
                == _material_record(self.root, relative)
                for relative in self.input_paths
            )
        except (OSError, ConfigurationError):
            return False

    def launch_argv(self, environment: dict[str, str]) -> list[str]:
        if self.root is None:
            raise ConfigurationError("isolated workspace was not prepared")
        resolved = resolve_launch_argv(self.task.command, self.manifest.root, environment)
        staged_paths = set(self.input_paths) | set(self.output_paths())
        rewritten: list[str] = []
        for index, argument in enumerate(resolved):
            rewritten.append(self._rewrite_argument(argument, index, staged_paths))
        return rewritten

    def _rewrite_argument(self, argument: str, index: int, staged_paths: set[str]) -> str:
        assert self.root is not None
        # The resolved bare executable lives outside the project. A project-local
        # executable and all explicitly named project input/output arguments are
        # rewritten into the isolated workspace.
        candidate_text = argument
        prefix = ""
        if index > 0 and "=" in argument:
            prefix, candidate_text = argument.split("=", 1)
            prefix += "="
        candidate = Path(candidate_text)
        if candidate.is_absolute():
            source = candidate.resolve(strict=False)
        else:
            source = (self.manifest.root / candidate).resolve(strict=False)
        if not is_within(self.manifest.root, source):
            return argument
        relative = source.relative_to(self.manifest.root).as_posix()
        if relative not in staged_paths:
            return argument
        return prefix + str(self.root / relative)

    def unexpected_writes(self, *, output_parent_directories: set[str]) -> list[str]:
        if self.root is None:
            raise ConfigurationError("isolated workspace was not prepared")
        if not self.track_writes:
            raise ConfigurationError("write tracking was disabled for this workspace")
        after = project_state(self.root, excluded=set(self.output_paths()))
        before = self.before_state
        changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
        return [
            path
            for path in changed
            if not (
                path in output_parent_directories
                and after.get(path, {}).get("type") == "directory"
                and before.get(path, {}).get("type") in {None, "directory"}
            )
        ]

    def output(self, relative: str) -> Path:
        if self.root is None:
            raise ConfigurationError("isolated workspace was not prepared")
        path = (self.root / relative).resolve(strict=False)
        if not is_within(self.root, path):
            raise ConfigurationError(f"staged output escapes isolated workspace: {relative}")
        return path

    def cleanup(self) -> None:
        if self.root is not None:
            root = self.root
            _remove_workspace(self.state, root)
            self.root = None
