"""Dependency-free filesystem link and Windows reparse-point checks."""

from __future__ import annotations

import os
import secrets
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_LSTAT = os.lstat
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_TEMP_DIRECTORY_ATTEMPTS = 100
_TEMP_DIRECTORY_RANDOM_BYTES = 8


def _pathlib_reports_junction(path: Path) -> bool:
    """Use pathlib's junction check when the running Python provides it."""

    checker = getattr(path, "is_junction", None)
    return bool(checker is not None and checker())


def is_link_like(path: Path) -> bool:
    """Return whether *path* is a symlink, junction, or other reparse point.

    ``Path.is_junction`` was added in Python 3.12, while ZeroRun supports
    Python 3.10+.  The ``lstat`` attribute fallback keeps the same fail-closed
    Windows behavior on every supported interpreter without following the
    inspected path.
    """

    if path.is_symlink() or _pathlib_reports_junction(path):
        return True
    try:
        details = _LSTAT(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(attributes & _WINDOWS_REPARSE_POINT)


def create_private_directory(path: Path) -> Path:
    """Create one directory with safe private semantics for this platform.

    Starting with CPython 3.13, Windows interprets mode ``0o700`` as a special
    restrictive ACL. A restricted Codex token can create such a directory and
    then be denied access to it, so Windows inherits the reviewed parent ACL.
    POSIX retains an explicit private ``0o700`` mode.
    """

    if os.name == "nt":
        os.mkdir(path)
    else:
        os.mkdir(path, 0o700)
    return path


def create_private_temp_directory(parent: Path, *, prefix: str) -> Path:
    """Create an unpredictable temporary child compatible with restricted Windows."""

    if (
        not isinstance(prefix, str)
        or not prefix
        or prefix in {".", ".."}
        or "/" in prefix
        or "\\" in prefix
    ):
        raise ValueError("temporary-directory prefix must be a non-empty basename")
    for _ in range(_TEMP_DIRECTORY_ATTEMPTS):
        # Sixty-four random bits exceed tempfile's collision resistance while
        # preserving enough Windows MAX_PATH budget for nested Git worktrees.
        candidate = parent / (
            f"{prefix}{secrets.token_hex(_TEMP_DIRECTORY_RANDOM_BYTES)}"
        )
        try:
            create_private_directory(candidate)
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError("could not allocate a unique temporary directory")


def atomic_replace_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    """Publish bytes without ever writing through the destination entry.

    Repository-local runtime state is untrusted.  In particular, a regular
    looking destination can be a hard link to an unrelated user file; opening
    it with ``"w"`` would truncate that other file.  Write a new single-link
    regular file in the same real directory and atomically replace only the
    destination directory entry instead.

    Callers remain responsible for validating that *path* belongs to their
    managed tree.  This helper independently rejects link-like parents and
    parent replacement while the temporary file is being prepared.
    """

    if not isinstance(payload, bytes):
        raise TypeError("atomic replacement payload must be bytes")
    path = Path(os.path.abspath(path))
    parent = path.parent
    if is_link_like(parent) or not parent.is_dir():
        raise OSError(f"atomic replacement parent is not a real directory: {parent}")
    parent_before = parent.stat(follow_symlinks=False)
    descriptor, raw_temporary = tempfile.mkstemp(
        prefix=".zerorun-write-",
        dir=parent,
    )
    temporary = Path(raw_temporary)
    opened = True
    try:
        if os.name != "nt":
            os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("atomic replacement write made no progress")
            offset += written
        os.fsync(descriptor)
        temporary_open = os.fstat(descriptor)
        temporary_path = temporary.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(temporary_open.st_mode)
            or not stat.S_ISREG(temporary_path.st_mode)
            or temporary_open.st_nlink != 1
            or temporary_path.st_nlink != 1
            or (temporary_open.st_dev, temporary_open.st_ino)
            != (temporary_path.st_dev, temporary_path.st_ino)
            or temporary_open.st_size != len(payload)
        ):
            raise OSError("atomic replacement temporary file lost its identity")
        os.close(descriptor)
        opened = False

        parent_current = parent.stat(follow_symlinks=False)
        if (
            is_link_like(parent)
            or not stat.S_ISDIR(parent_current.st_mode)
            or (parent_current.st_dev, parent_current.st_ino)
            != (parent_before.st_dev, parent_before.st_ino)
        ):
            raise OSError("atomic replacement parent changed during publication")
        os.replace(temporary, path)

        published = path.stat(follow_symlinks=False)
        if (
            is_link_like(path)
            or not stat.S_ISREG(published.st_mode)
            or published.st_nlink != 1
            or (published.st_dev, published.st_ino)
            != (temporary_open.st_dev, temporary_open.st_ino)
            or published.st_size != len(payload)
        ):
            raise OSError("atomic replacement could not attest the published file")
    finally:
        if opened:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


@contextmanager
def private_temporary_directory(parent: Path, *, prefix: str) -> Iterator[Path]:
    """Yield a temporary directory and remove only that exact real directory."""

    path = create_private_temp_directory(parent, prefix=prefix)
    try:
        yield path
    finally:
        if is_link_like(path):
            raise OSError(f"temporary directory became link-like: {path}")
        if path.exists():
            if not path.is_dir():
                raise OSError(f"temporary directory changed type: {path}")

            root = os.path.normcase(os.path.abspath(path))

            def remove_readonly(function, target, error_info) -> None:
                error = error_info[1]
                candidate = os.path.normcase(os.path.abspath(target))
                try:
                    contained = os.path.commonpath((root, candidate)) == root
                except ValueError:
                    contained = False
                if not contained or not isinstance(error, PermissionError):
                    raise error
                inspected = Path(target)
                if is_link_like(inspected):
                    raise OSError(
                        f"temporary-directory cleanup encountered a link-like path: {target}"
                    ) from error
                # Git and archive tools can legitimately create read-only files.
                # Clear only that flag inside the exact temporary tree, then let
                # shutil retry its original unlink/rmdir operation.
                os.chmod(inspected, stat.S_IRWXU)
                function(target)

            shutil.rmtree(path, onerror=remove_readonly)
