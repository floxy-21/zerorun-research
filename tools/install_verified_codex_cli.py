#!/usr/bin/env python3
"""Install an exact Codex CLI from two authenticated npm archives.

Only the two fixed registry URLs are accessed. Both archives are completely
downloaded and checked against caller-supplied SHA-512 SRI values before the
installation prefix is created. The wrapper and Linux/x64 vendor payload are
then extracted directly with a fail-closed tar reader. No package manager is
discovered or executed. The complete installed package tree is compared to the
authenticated archive manifests before a receipt is published.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zerorun.path_safety import (
    create_private_directory,
    private_temporary_directory,
)


SCHEMA = "zerorun.verified-codex-install.v3"
REGISTRY_ORIGIN = "https://registry.npmjs.org"
MAX_MAIN_BYTES = 1_000_000
MAX_MAIN_FILES = 32
MAX_MAIN_UNPACKED_BYTES = 4_000_000
MAX_PLATFORM_BYTES = 200_000_000
MAX_PLATFORM_FILES = 32
MAX_PLATFORM_UNPACKED_BYTES = 400_000_000
MAX_RUNTIME_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_PATH_BYTES = 4096
MAX_ARCHIVE_METADATA_BYTES = 64 * 1024
MAX_PACKAGE_JSON_BYTES = 64 * 1024
ARCHIVE_MEMBER_MULTIPLIER = 4
MAX_ARCHIVE_EXTENSION_DEPTH = 16
# Allow bounded PAX/GNU extension headers in addition to the 128 semantic
# members accepted by the historical contract. They are counted before their
# bodies are read and remain capped independently.
MAX_RAW_ARCHIVE_HEADERS = MAX_PLATFORM_FILES * ARCHIVE_MEMBER_MULTIPLIER * 4
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
EVIDENCE_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")


class InstallError(RuntimeError):
    """Raised when the exact-package installation contract is not satisfied."""


@dataclass(frozen=True)
class _ArchiveIdentity:
    device: int
    inode: int
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    sha256: str
    sha512: str


class _DigestingReader:
    """Hash every compressed byte consumed by the streaming tar reader."""

    def __init__(self, handle: BinaryIO) -> None:
        self._handle = handle
        self._sha256 = hashlib.sha256()
        self._sha512 = hashlib.sha512()
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        block = self._handle.read(size)
        if block:
            self.bytes_read += len(block)
            self._sha256.update(block)
            self._sha512.update(block)
        return block

    def drain(self) -> None:
        for block in iter(lambda: self.read(1024 * 1024), b""):
            pass

    @property
    def sha256(self) -> str:
        return self._sha256.hexdigest()

    @property
    def sha512(self) -> str:
        return self._sha512.hexdigest()


class _BoundedTarInfo(tarfile.TarInfo):
    """Reject dangerous extension headers before tarfile allocates their body."""

    def _proc_member(self, archive: tarfile.TarFile) -> tarfile.TarInfo:
        count = int(getattr(archive, "_zerorun_raw_header_count", 0)) + 1
        archive._zerorun_raw_header_count = count
        if count > MAX_RAW_ARCHIVE_HEADERS:
            raise InstallError("archive exceeds its raw-header count bound")
        if self.type == tarfile.GNUTYPE_SPARSE:
            raise InstallError("archive contains a sparse member")
        extension_types = {
            tarfile.GNUTYPE_LONGNAME,
            tarfile.GNUTYPE_LONGLINK,
            tarfile.XHDTYPE,
            tarfile.XGLTYPE,
            tarfile.SOLARIS_XHDTYPE,
        }
        if self.type not in extension_types:
            return super()._proc_member(archive)
        if self.size < 0 or self.size > MAX_ARCHIVE_METADATA_BYTES:
            raise InstallError("archive metadata header exceeds its size bound")
        depth = int(getattr(archive, "_zerorun_extension_depth", 0)) + 1
        if depth > MAX_ARCHIVE_EXTENSION_DEPTH:
            raise InstallError("archive exceeds its extension-header nesting bound")
        archive._zerorun_extension_depth = depth
        try:
            return super()._proc_member(archive)
        finally:
            archive._zerorun_extension_depth = depth - 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _archive_stat_fields(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _archive_path_fields(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Windows reports creation/change time differently through path stat and
    # descriptor fstat. The remaining fields still bind the path to the exact
    # opened regular file; descriptor-to-descriptor checks below retain ctime.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
    )


def _open_regular_archive(
    path: Path,
    *,
    label: str,
    maximum_bytes: int | None,
) -> tuple[BinaryIO, os.stat_result, Path]:
    lexical = Path(os.path.abspath(path.expanduser()))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise InstallError(f"could not safely open {label} archive: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        current = lexical.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or _archive_path_fields(current) != _archive_path_fields(opened)
        ):
            raise InstallError(f"{label} archive is not a stable regular file")
        if opened.st_size < 1:
            raise InstallError(f"{label} archive is empty")
        if maximum_bytes is not None and opened.st_size > maximum_bytes:
            raise InstallError(f"{label} archive exceeds its compressed-byte bound")
        return os.fdopen(descriptor, "rb", closefd=True), opened, lexical
    except Exception:
        os.close(descriptor)
        raise


def _verify_open_archive_unchanged(
    handle: BinaryIO,
    *,
    opened: os.stat_result,
    lexical: Path,
    label: str,
) -> None:
    try:
        after = os.fstat(handle.fileno())
        current = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise InstallError(f"{label} archive changed while being read: {exc}") from exc
    if (
        not stat.S_ISREG(after.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or _archive_stat_fields(after) != _archive_stat_fields(opened)
        or _archive_path_fields(current) != _archive_path_fields(opened)
    ):
        raise InstallError(f"{label} archive changed while being read")


def _identity_from_digests(
    opened: os.stat_result,
    *,
    sha256: str,
    sha512: str,
) -> _ArchiveIdentity:
    return _ArchiveIdentity(
        device=opened.st_dev,
        inode=opened.st_ino,
        mode=opened.st_mode,
        size=opened.st_size,
        mtime_ns=opened.st_mtime_ns,
        ctime_ns=opened.st_ctime_ns,
        sha256=sha256,
        sha512=sha512,
    )


def _identity_stat_fields(identity: _ArchiveIdentity) -> tuple[int, int, int, int, int, int]:
    return (
        identity.device,
        identity.inode,
        identity.mode,
        identity.size,
        identity.mtime_ns,
        identity.ctime_ns,
    )


@contextlib.contextmanager
def _stream_archive(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    expected_identity: _ArchiveIdentity | None = None,
) -> Iterator[tarfile.TarFile]:
    handle, opened, lexical = _open_regular_archive(
        path,
        label=label,
        maximum_bytes=maximum_bytes,
    )
    reader = _DigestingReader(handle)
    try:
        if (
            expected_identity is not None
            and _archive_stat_fields(opened) != _identity_stat_fields(expected_identity)
        ):
            raise InstallError(f"{label} archive changed since integrity verification")
        with tarfile.open(
            fileobj=reader,
            mode="r|gz",
            tarinfo=_BoundedTarInfo,
        ) as archive:
            yield archive
        # tar streams may stop at the end-of-archive marker. Include any
        # authenticated trailing bytes in the exact archive digest.
        reader.drain()
        _verify_open_archive_unchanged(
            handle,
            opened=opened,
            lexical=lexical,
            label=label,
        )
        if reader.bytes_read != opened.st_size:
            raise InstallError(f"{label} archive was not read completely")
        if expected_identity is not None and (
            reader.sha256 != expected_identity.sha256
            or reader.sha512 != expected_identity.sha512
        ):
            raise InstallError(f"{label} archive changed since integrity verification")
    finally:
        handle.close()


def _parse_sri(value: str) -> bytes:
    if not value.startswith("sha512-"):
        raise InstallError("integrity must be a sha512 SRI value")
    try:
        decoded = base64.b64decode(value[7:], validate=True)
    except ValueError as exc:
        raise InstallError("integrity is not canonical base64") from exc
    if len(decoded) != hashlib.sha512().digest_size:
        raise InstallError("integrity has the wrong SHA-512 length")
    if "sha512-" + base64.b64encode(decoded).decode("ascii") != value:
        raise InstallError("integrity must use canonical base64")
    return decoded


def _download_exact(url: str, destination: Path, *, maximum: int) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ZeroRun-release-verifier/2"},
        method="GET",
    )
    try:
        response: BinaryIO = urllib.request.urlopen(request, timeout=90)
    except Exception as exc:  # pragma: no cover - CI/network failure
        raise InstallError(f"unable to download exact package: {exc}") from exc
    with response:
        final_url = getattr(response, "geturl", lambda: url)()
        if final_url != url:
            raise InstallError("package URL redirected; refusing mutable routing")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError as exc:
                raise InstallError("invalid package Content-Length") from exc
            if declared_size < 1 or declared_size > maximum:
                raise InstallError("package Content-Length is outside the bound")
        total = 0
        with destination.open("xb") as handle:
            while True:
                block = response.read(min(1024 * 1024, maximum + 1 - total))
                if not block:
                    break
                total += len(block)
                if total > maximum:
                    raise InstallError("package exceeds the download bound")
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        if total < 1:
            raise InstallError("package download is empty")
        if declared is not None and total != declared_size:
            raise InstallError("package length differs from Content-Length")


def _verify_sri(path: Path, expected: str) -> _ArchiveIdentity:
    expected_digest = _parse_sri(expected)
    sha256 = hashlib.sha256()
    sha512 = hashlib.sha512()
    handle, opened, lexical = _open_regular_archive(
        path,
        label="package",
        maximum_bytes=None,
    )
    try:
        read_bytes = 0
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            read_bytes += len(block)
            sha256.update(block)
            sha512.update(block)
        _verify_open_archive_unchanged(
            handle,
            opened=opened,
            lexical=lexical,
            label="package",
        )
    finally:
        handle.close()
    if read_bytes != opened.st_size:
        raise InstallError("package changed while its integrity was verified")
    if not hmac.compare_digest(sha512.digest(), expected_digest):
        raise InstallError(f"SHA-512 integrity mismatch for {path.name}")
    return _identity_from_digests(
        opened,
        sha256=sha256.hexdigest(),
        sha512=sha512.hexdigest(),
    )


def _safe_member_path(name: str) -> PurePosixPath:
    try:
        encoded = name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InstallError("archive member path is not valid UTF-8") from exc
    if (
        not encoded
        or len(encoded) > MAX_ARCHIVE_PATH_BYTES
        or "\\" in name
        or "\x00" in name
    ):
        raise InstallError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise InstallError(f"unsafe archive path: {name!r}")
    if path.parts[0] != "package" or len(path.parts) < 2:
        raise InstallError(f"archive member is outside package/: {name!r}")
    return path


def _validated_archive_member(
    member: tarfile.TarInfo,
    *,
    label: str,
) -> tuple[PurePosixPath, bool]:
    path = _safe_member_path(member.name)
    if member.mode < 0 or member.mode & 0o7000:
        raise InstallError(f"{label} archive contains privileged mode bits")
    if member.type == tarfile.GNUTYPE_SPARSE or member.sparse is not None:
        raise InstallError(f"{label} archive contains a sparse member")
    if member.isdir():
        if member.size != 0:
            raise InstallError(f"{label} archive directory has a nonzero size")
        return path, False
    if not member.isreg():
        raise InstallError(f"{label} archive contains a link or special file")
    if type(member.size) is not int or member.size < 0:
        raise InstallError(f"{label} archive member has an invalid declared size")
    return path, True


def _read_package_json(
    archive_path: Path,
    expected_version: str,
    *,
    maximum_members: int = MAX_PLATFORM_FILES * ARCHIVE_MEMBER_MULTIPLIER,
    maximum_unpacked_bytes: int = MAX_PLATFORM_UNPACKED_BYTES,
    maximum_archive_bytes: int = MAX_PLATFORM_BYTES,
    expected_identity: _ArchiveIdentity | None = None,
) -> dict[str, object]:
    package_payload: bytes | None = None
    seen: set[PurePosixPath] = set()
    member_count = 0
    total_declared_bytes = 0
    try:
        with _stream_archive(
            archive_path,
            label="package metadata",
            maximum_bytes=maximum_archive_bytes,
            expected_identity=expected_identity,
        ) as archive:
            for member in archive:
                member_count += 1
                if member_count > maximum_members:
                    raise InstallError("package archive exceeds its member-count bound")
                path, regular = _validated_archive_member(
                    member,
                    label="package",
                )
                if path in seen:
                    raise InstallError("package archive contains duplicate paths")
                seen.add(path)
                if not regular:
                    continue
                total_declared_bytes += member.size
                if total_declared_bytes > maximum_unpacked_bytes:
                    raise InstallError("package archive exceeds its declared-size bound")
                if path.as_posix() != "package/package.json":
                    continue
                if member.size > MAX_PACKAGE_JSON_BYTES:
                    raise InstallError("package archive has no unique bounded package.json")
                handle = archive.extractfile(member)
                if handle is None:
                    raise InstallError("package.json is unreadable")
                package_payload = handle.read(MAX_PACKAGE_JSON_BYTES + 1)
                if len(package_payload) != member.size:
                    raise InstallError("package.json is truncated")
    except (tarfile.TarError, OSError, EOFError, RecursionError) as exc:
        raise InstallError(f"invalid npm archive: {exc}") from exc
    if package_payload is None:
        raise InstallError("package archive has no unique bounded package.json")
    try:
        data = json.loads(package_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid npm archive: {exc}") from exc
    if not isinstance(data, dict):
        raise InstallError("package.json must be an object")
    if data.get("name") != "@openai/codex" or data.get("version") != expected_version:
        raise InstallError("npm package identity does not match the requested Codex version")
    return data


def _safe_destination(root: Path, relative: PurePosixPath) -> Path:
    destination = root.joinpath(*relative.parts)
    try:
        destination.resolve(strict=False).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise InstallError(f"archive destination escaped the package root: {relative}") from exc
    return destination


def _extract_authenticated_files(
    archive_path: Path,
    destination_root: Path,
    *,
    label: str,
    maximum_files: int,
    maximum_unpacked_bytes: int,
    install_member: Callable[[PurePosixPath], bool],
    allow_member: Callable[[PurePosixPath], bool],
    maximum_archive_bytes: int = MAX_PLATFORM_BYTES,
    expected_identity: _ArchiveIdentity | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Audit every tar member and directly extract selected regular files."""

    installed: list[dict[str, object]] = []
    archive_rows: list[dict[str, object]] = []
    total_files = 0
    total_bytes = 0
    member_count = 0
    seen: set[PurePosixPath] = set()
    try:
        with _stream_archive(
            archive_path,
            label=label,
            maximum_bytes=maximum_archive_bytes,
            expected_identity=expected_identity,
        ) as archive:
            for member in archive:
                member_count += 1
                if member_count > maximum_files * ARCHIVE_MEMBER_MULTIPLIER:
                    raise InstallError(f"{label} archive exceeds its member-count bound")
                member_path, regular = _validated_archive_member(
                    member,
                    label=label,
                )
                if member_path in seen:
                    raise InstallError(f"{label} archive contains duplicate paths")
                seen.add(member_path)
                relative = PurePosixPath(*member_path.parts[1:])
                if not allow_member(relative):
                    raise InstallError(f"{label} archive contains an unexpected member: {relative}")
                if not regular:
                    archive_rows.append({"path": relative.as_posix(), "kind": "directory"})
                    continue
                total_files += 1
                total_bytes += member.size
                if total_files > maximum_files or total_bytes > maximum_unpacked_bytes:
                    raise InstallError(f"{label} archive exceeds extraction bounds")
                source = archive.extractfile(member)
                if source is None:
                    raise InstallError(f"{label} archive member is unreadable")
                digest = hashlib.sha256()
                copied = 0
                selected = install_member(relative)
                output = None
                destination = None
                if selected:
                    destination = _safe_destination(destination_root, relative)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.exists() or destination.is_symlink():
                        raise InstallError(f"installed path appeared before extraction: {relative}")
                    output = destination.open("xb")
                try:
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        copied += len(block)
                        if copied > member.size:
                            raise InstallError(f"{label} archive member exceeds declared size")
                        digest.update(block)
                        if output is not None:
                            output.write(block)
                    if copied != member.size:
                        raise InstallError(f"{label} archive member is truncated")
                    if output is not None:
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    if output is not None:
                        output.close()
                mode = member.mode & 0o777
                row = {
                    "path": relative.as_posix(),
                    "bytes": copied,
                    "mode": mode,
                    "sha256": digest.hexdigest(),
                }
                archive_rows.append({**row, "kind": "regular_file"})
                if selected:
                    assert destination is not None
                    destination.chmod(mode)
                    installed.append(row)
    except (tarfile.TarError, OSError, EOFError, RecursionError) as exc:
        raise InstallError(f"invalid {label} archive: {exc}") from exc
    if not installed:
        raise InstallError(f"{label} archive has no installable payload")
    installed.sort(key=lambda row: str(row["path"]))
    archive_rows.sort(key=lambda row: str(row["path"]))
    return installed, {
        "regular_files": total_files,
        "unpacked_bytes": total_bytes,
        "member_manifest_sha256": _canonical_sha256(archive_rows),
    }


def _extract_verified_vendor(archive_path: Path, package_root: Path) -> tuple[int, int]:
    """Compatibility helper retained for focused extraction tests."""

    rows, _ = _extract_authenticated_files(
        archive_path,
        package_root,
        label="platform",
        maximum_files=MAX_PLATFORM_FILES,
        maximum_unpacked_bytes=MAX_PLATFORM_UNPACKED_BYTES,
        install_member=lambda path: path.parts[0] == "vendor",
        allow_member=lambda path: path.parts[0] == "vendor",
    )
    return len(rows), sum(int(row["bytes"]) for row in rows)


def _tree_manifest(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise InstallError(f"unable to enumerate installed package tree: {exc}") from exc
        for child in children:
            relative = child.relative_to(root).as_posix()
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise InstallError(f"installed package tree contains a link: {relative}")
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise InstallError(f"installed package tree contains a special file: {relative}")
            total += info.st_size
            if len(rows) >= MAX_MAIN_FILES + MAX_PLATFORM_FILES:
                raise InstallError("installed package tree exceeds its file bound")
            if total > MAX_MAIN_UNPACKED_BYTES + MAX_PLATFORM_UNPACKED_BYTES:
                raise InstallError("installed package tree exceeds its byte bound")
            rows.append(
                {
                    "path": relative,
                    "bytes": info.st_size,
                    "mode": stat.S_IMODE(info.st_mode),
                    "sha256": _sha256(child),
                }
            )
    rows.sort(key=lambda row: str(row["path"]))
    return rows


def _regular_file(path: Path, *, within: Path) -> Path:
    try:
        lexical = path.absolute()
        info = lexical.lstat()
        resolved = lexical.resolve(strict=True)
        boundary = within.resolve(strict=True)
        resolved.relative_to(boundary)
        mode = resolved.stat().st_mode
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise InstallError(f"installed path is missing or escaped: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(mode):
        raise InstallError(f"installed path is not a regular non-linked file: {path}")
    return resolved


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(f"command failed: {command[0]}: {exc}") from exc
    output = result.stdout.strip()
    if not output or len(output) > 256 or "\n" in output or "\r" in output:
        raise InstallError(f"command returned an invalid version line: {command[0]}")
    return output


def _runtime_file_identity(name: str) -> dict[str, object]:
    discovered = shutil.which(name)
    if not discovered:
        raise InstallError(f"required runtime command was not found: {name}")
    lexical = Path(os.path.abspath(discovered))
    try:
        lexical_info = lexical.lstat()
        resolved = lexical.resolve(strict=True)
        resolved_info = resolved.stat()
    except OSError as exc:
        raise InstallError(f"required runtime command is unavailable: {name}: {exc}") from exc
    if not stat.S_ISREG(resolved_info.st_mode) or resolved_info.st_size > MAX_RUNTIME_BYTES:
        raise InstallError(f"required runtime command is not a bounded regular file: {name}")
    link_target = os.readlink(lexical) if stat.S_ISLNK(lexical_info.st_mode) else None
    return {
        "command": name,
        "lexical_path": str(lexical),
        "lexical_kind": "symlink" if link_target is not None else "regular_file",
        "link_target": link_target,
        "resolved_path": str(resolved),
        "resolved_bytes": resolved_info.st_size,
        "resolved_sha256": _sha256(resolved),
    }


def _runtime_identity(name: str) -> dict[str, object]:
    identity = _runtime_file_identity(name)
    return {**identity, "reported_version": _run([str(identity["lexical_path"]), "--version"])}


def _publish_receipt(receipt: Path, result: dict[str, object]) -> None:
    encoded = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    temporary_receipt = receipt.parent / f".{receipt.name}.{os.getpid()}.tmp"
    if temporary_receipt.exists() or temporary_receipt.is_symlink():
        raise InstallError("exclusive receipt staging path already exists")
    try:
        with temporary_receipt.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_receipt, receipt)
        except FileExistsError as exc:
            raise InstallError("receipt path appeared during publication") from exc
        directory_fd = os.open(receipt.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_receipt.exists() and not temporary_receipt.is_symlink():
            temporary_receipt.unlink()


def install(
    *,
    version: str,
    main_integrity: str,
    platform_integrity: str,
    prefix: Path,
    receipt: Path,
    evidence_id: str,
    source_commit: str,
) -> dict[str, object]:
    if VERSION_RE.fullmatch(version) is None:
        raise InstallError("version must be an exact three-component release")
    if EVIDENCE_ID_RE.fullmatch(evidence_id) is None:
        raise InstallError("evidence_id is not a canonical bounded identifier")
    if COMMIT_RE.fullmatch(source_commit) is None or source_commit == "0" * 40:
        raise InstallError("source_commit must be an exact lowercase 40-hex commit")
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise InstallError("this installer is intentionally limited to Linux/x86_64")
    if prefix.exists() or prefix.is_symlink():
        raise InstallError("installation prefix must not already exist")
    if receipt.exists() or receipt.is_symlink():
        raise InstallError("receipt path must not already exist")
    receipt.parent.mkdir(parents=True, exist_ok=True)

    # Runtime commands are executed only before retrieval. After both SRI checks
    # the installer performs no subprocess calls and invokes no package manager.
    node_before = _runtime_identity("node")
    main_url = f"{REGISTRY_ORIGIN}/@openai/codex/-/codex-{version}.tgz"
    platform_version = f"{version}-linux-x64"
    platform_url = f"{REGISTRY_ORIGIN}/@openai/codex/-/codex-{platform_version}.tgz"
    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-codex-download-"
    ) as temp:
        main_archive = temp / "codex.tgz"
        platform_archive = temp / "codex-linux-x64.tgz"
        _download_exact(main_url, main_archive, maximum=MAX_MAIN_BYTES)
        _download_exact(platform_url, platform_archive, maximum=MAX_PLATFORM_BYTES)
        verified_main_archive = _verify_sri(main_archive, main_integrity)
        verified_platform_archive = _verify_sri(platform_archive, platform_integrity)
        main_metadata = _read_package_json(
            main_archive,
            version,
            maximum_members=MAX_MAIN_FILES * ARCHIVE_MEMBER_MULTIPLIER,
            maximum_unpacked_bytes=MAX_MAIN_UNPACKED_BYTES,
            maximum_archive_bytes=MAX_MAIN_BYTES,
            expected_identity=verified_main_archive,
        )
        platform_metadata = _read_package_json(
            platform_archive,
            platform_version,
            maximum_members=MAX_PLATFORM_FILES * ARCHIVE_MEMBER_MULTIPLIER,
            maximum_unpacked_bytes=MAX_PLATFORM_UNPACKED_BYTES,
            maximum_archive_bytes=MAX_PLATFORM_BYTES,
            expected_identity=verified_platform_archive,
        )

        create_private_directory(prefix)
        package_root = prefix / "lib" / "node_modules" / "@openai" / "codex"
        package_root.mkdir(parents=True)
        main_rows, main_member_identity = _extract_authenticated_files(
            main_archive,
            package_root,
            label="wrapper",
            maximum_files=MAX_MAIN_FILES,
            maximum_unpacked_bytes=MAX_MAIN_UNPACKED_BYTES,
            install_member=lambda _path: True,
            allow_member=lambda path: path.parts[0] != "vendor",
            maximum_archive_bytes=MAX_MAIN_BYTES,
            expected_identity=verified_main_archive,
        )
        platform_rows, platform_member_identity = _extract_authenticated_files(
            platform_archive,
            package_root,
            label="platform",
            maximum_files=MAX_PLATFORM_FILES,
            maximum_unpacked_bytes=MAX_PLATFORM_UNPACKED_BYTES,
            install_member=lambda path: path.parts[0] == "vendor",
            allow_member=lambda path: (
                path.parts[0] == "vendor"
                or path.as_posix() in {"package.json", "README.md"}
            ),
            maximum_archive_bytes=MAX_PLATFORM_BYTES,
            expected_identity=verified_platform_archive,
        )
        expected_tree = sorted(main_rows + platform_rows, key=lambda row: str(row["path"]))
        if len({str(row["path"]) for row in expected_tree}) != len(expected_tree):
            raise InstallError("authenticated wrapper and platform payload paths overlap")
        installed_tree = _tree_manifest(package_root)
        if installed_tree != expected_tree:
            raise InstallError("installed package tree differs from authenticated archive bytes")
        installed_metadata = json.loads(
            _regular_file(package_root / "package.json", within=package_root).read_text(
                encoding="utf-8"
            )
        )
        if installed_metadata != main_metadata:
            raise InstallError("installed wrapper metadata differs from authenticated archive")
        launcher = _regular_file(package_root / "bin" / "codex.js", within=package_root)
        native = _regular_file(
            package_root / "vendor" / "x86_64-unknown-linux-musl" / "bin" / "codex",
            within=package_root,
        )
        command_dir = prefix / "bin"
        command_dir.mkdir()
        command = command_dir / "codex"
        command_target = "../lib/node_modules/@openai/codex/bin/codex.js"
        command.symlink_to(command_target)
        if (
            not command.is_symlink()
            or os.readlink(command) != command_target
            or command.resolve(strict=True) != launcher
        ):
            raise InstallError("Codex command link does not resolve to the authenticated launcher")

        # Rehash, but do not execute, the runtime commands after the network
        # boundary. Any runner-side replacement during retrieval fails closed.
        if _runtime_file_identity("node") != {
            key: value for key, value in node_before.items() if key != "reported_version"
        }:
            raise InstallError("Node runtime identity changed during installation")
        script_path = Path(__file__).resolve(strict=True)
        payload: dict[str, object] = {
            "schema": SCHEMA,
            "evidence_id": evidence_id,
            "source_commit": source_commit,
            "codex_version": version,
            "platform": "linux-x64",
            "main_package": {
                "url": main_url,
                "integrity": main_integrity,
                "sha256": verified_main_archive.sha256,
                "bytes": verified_main_archive.size,
                "metadata_sha256": _canonical_sha256(main_metadata),
                **main_member_identity,
            },
            "platform_package": {
                "url": platform_url,
                "integrity": platform_integrity,
                "sha256": verified_platform_archive.sha256,
                "bytes": verified_platform_archive.size,
                "metadata_sha256": _canonical_sha256(platform_metadata),
                **platform_member_identity,
            },
            "installed": {
                "package_tree_sha256": _canonical_sha256(installed_tree),
                "package_tree_regular_files": len(installed_tree),
                "package_tree_bytes": sum(int(row["bytes"]) for row in installed_tree),
                "launcher_sha256": _sha256(launcher),
                "native_sha256": _sha256(native),
                "vendor_regular_files": len(platform_rows),
                "vendor_unpacked_bytes": sum(int(row["bytes"]) for row in platform_rows),
                "command_kind": "relative_symlink",
                "command_target": command_target,
                "command_resolves_to_launcher": True,
            },
            "runtime": {
                "node": node_before,
                "runtime_identity_unchanged_after_retrieval": True,
            },
            "installer_source_sha256": _sha256(script_path),
            "network": {
                "fixed_https_archive_downloads": 2,
                "integrity_verified_before_prefix_creation": True,
                "subprocesses_after_integrity_verification": 0,
                "network_operations_after_integrity_verification": 0,
                "package_manager_invoked": False,
            },
            "node_version": node_before["reported_version"],
            "network_after_integrity_verification": False,
            "installation_method": "direct-authenticated-archive-extraction",
        }
        result = {**payload, "evidence_payload_sha256": _canonical_sha256(payload)}
        _publish_receipt(receipt, result)
        return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--main-integrity", required=True)
    parser.add_argument("--linux-x64-integrity", required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--source-commit", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = install(
            version=arguments.version,
            main_integrity=arguments.main_integrity,
            platform_integrity=arguments.linux_x64_integrity,
            prefix=arguments.prefix,
            receipt=arguments.receipt,
            evidence_id=arguments.evidence_id,
            source_commit=arguments.source_commit,
        )
    except InstallError as exc:
        print(f"verified Codex installation refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
