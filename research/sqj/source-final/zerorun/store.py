from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .bounded_json import JsonLimits, loads_bounded_json
from .fingerprint import is_within, sha256_file, validate_relative
from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import (
    atomic_replace_bytes,
    create_private_temp_directory,
    is_link_like as _is_link_like,
    private_temporary_directory,
)
from .trust import (
    cache_payload_is_trusted,
    manifest_sha256,
    reject_tracked_zerorun_state,
    sign_cache_payload,
)


_MAX_CACHE_METADATA_BYTES = 8 * 1024 * 1024
_MAX_EVENT_LOG_BYTES = 8 * 1024 * 1024
_MAX_EVENT_RECORD_BYTES = 64 * 1024
_CACHE_METADATA_JSON_LIMITS = JsonLimits(
    max_bytes=_MAX_CACHE_METADATA_BYTES,
    max_depth=16,
    max_values=250_000,
    max_object_members=25_000,
    max_structural_tokens=500_000,
    max_number_chars=256,
    max_string_chars=1024 * 1024,
    max_total_string_chars=6 * 1024 * 1024,
)


class ProjectLockBusy(ConfigurationError):
    """Raised when another cacheable ZeroRun invocation owns the project lock."""


@dataclass
class OutputBackup:
    path: Path
    relative: str
    artifact: Path | None
    mode: int | None


def expected_output_paths(manifest: Manifest, task: TaskSpec) -> list[tuple[str, Path]]:
    paths: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw_output in task.outputs:
        path = validate_relative(manifest.root, raw_output, field="output")
        relative = path.relative_to(manifest.root).as_posix()
        if relative in seen:
            raise ConfigurationError(f"task {task.name!r}: outputs contain duplicates")
        seen.add(relative)
        paths.append((relative, path))
    return paths


class OutputTransaction:
    """Clear declared outputs for execution and restore them if it is unsafe to cache."""

    def __init__(self, state: Path, manifest: Manifest, task: TaskSpec):
        self.state = state
        self.manifest = manifest
        self.task = task
        self.backups: list[OutputBackup] = []
        self.temporary: Path | None = None

    def prepare(self) -> None:
        planned = expected_output_paths(self.manifest, self.task)
        for _, path in planned:
            if _is_link_like(path) or path.is_dir() or (path.exists() and not path.is_file()):
                raise ConfigurationError(
                    f"task {self.task.name!r}: cacheable execution only supports regular-file outputs; got {path}"
                )

        self.state.mkdir(parents=True, exist_ok=True)
        self.temporary = create_private_temp_directory(
            self.state,
            prefix="zerorun-output-",
        )
        backups_dir = self.temporary / "backups"
        backups_dir.mkdir()
        try:
            for index, (relative, path) in enumerate(planned):
                artifact: Path | None = None
                mode: int | None = None
                if path.is_file():
                    artifact = backups_dir / f"{index:04d}.bin"
                    shutil.copyfile(path, artifact)
                    mode = path.stat().st_mode & 0o777
                    path.unlink()
                self.backups.append(OutputBackup(path, relative, artifact, mode))
        except Exception:
            self.rollback()
            raise

    def commit(self) -> None:
        self._cleanup()

    def rollback(self) -> None:
        for backup in reversed(self.backups):
            path = backup.path
            if _is_link_like(path) or path.is_file():
                path.unlink(missing_ok=True)
            elif path.exists():
                shutil.rmtree(path)
            if backup.artifact is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup.artifact, path)
                if os.name != "nt" and backup.mode is not None:
                    os.chmod(path, backup.mode)
        self._cleanup()

    def _cleanup(self) -> None:
        if self.temporary is not None:
            shutil.rmtree(self.temporary, ignore_errors=True)
            self.temporary = None


class Store:
    def __init__(self, root: Path):
        try:
            self.root = root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ConfigurationError(f"ZeroRun repository root is unavailable: {root}") from exc
        if not self.root.is_dir():
            raise ConfigurationError(f"ZeroRun repository root is not a directory: {self.root}")
        self.state = self.root / ".zerorun"
        self.cache = self.state / "cache"
        self.quarantine = self.state / "quarantine"
        self.events = self.state / "events.jsonl"
        self._validate_state_layout()
        # Runtime state is mutable local evidence.  If it appears in the Git
        # index, a checkout can manufacture cache/results and must never be
        # treated as a reusable store.
        reject_tracked_zerorun_state(self.root)

    def _assert_managed_path(
        self,
        path: Path,
        *,
        expected_kind: str | None = None,
    ) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ConfigurationError(
                f"ZeroRun managed state escapes the repository: {path}"
            ) from exc
        if not relative.parts or relative.parts[0] != ".zerorun":
            raise ConfigurationError(f"unexpected ZeroRun managed-state path: {path}")
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            if _is_link_like(cursor):
                raise ConfigurationError(
                    f"ZeroRun managed state contains a symbolic link or junction: {cursor}"
                )
        resolved = path.resolve(strict=False)
        if os.path.normcase(str(resolved)) != os.path.normcase(str(path.absolute())):
            raise ConfigurationError(
                f"ZeroRun managed state resolves outside its exact repository path: {path}"
            )
        if expected_kind == "directory" and path.exists() and not path.is_dir():
            raise ConfigurationError(f"ZeroRun managed directory is not a directory: {path}")
        if expected_kind == "file" and path.exists() and not path.is_file():
            raise ConfigurationError(f"ZeroRun managed file is not a regular file: {path}")

    def _validate_state_layout(self) -> None:
        for directory in (
            self.state,
            self.cache,
            self.quarantine,
            self.state / "locks",
        ):
            self._assert_managed_path(directory, expected_kind="directory")
        self._assert_managed_path(self.events, expected_kind="file")

    def managed_directory(self, path: Path) -> Path:
        self._assert_managed_path(path, expected_kind="directory")
        return path

    def managed_file(self, path: Path) -> Path:
        self._assert_managed_path(path, expected_kind="file")
        return path

    def _open_managed_regular(self, path: Path, *, label: str) -> tuple[int, os.stat_result]:
        path = self.managed_file(path)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOINHERIT", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise ConfigurationError(f"{label} is unavailable: {exc}") from exc
        try:
            opened = os.fstat(descriptor)
            current = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(current.st_mode)
                or _is_link_like(path)
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise ConfigurationError(
                    f"{label} is not one stable managed regular file"
                )
        except Exception:
            os.close(descriptor)
            raise
        return descriptor, opened

    def _verified_managed_bytes(
        self,
        path: Path,
        record: dict[str, Any],
        *,
        label: str,
    ) -> bytes:
        expected_size = record.get("size")
        expected_sha256 = record.get("sha256")
        if (
            not isinstance(expected_size, int)
            or expected_size < 0
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ConfigurationError(f"{label} metadata is invalid")
        descriptor, opened = self._open_managed_regular(path, label=label)
        try:
            if opened.st_size != expected_size:
                raise ConfigurationError(f"{label} size does not match signed metadata")
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            total = 0
            while chunk := os.read(descriptor, 64 * 1024):
                total += len(chunk)
                if total > expected_size:
                    raise ConfigurationError(
                        f"{label} grew beyond its signed size while being read"
                    )
                digest.update(chunk)
                chunks.append(chunk)
            if total != expected_size or digest.hexdigest() != expected_sha256:
                raise ConfigurationError(f"{label} checksum does not match signed metadata")
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _copy_verified_managed_file(
        self,
        path: Path,
        destination: BinaryIO | None,
        record: dict[str, Any],
        *,
        label: str,
    ) -> None:
        expected_size = record.get("size")
        expected_sha256 = record.get("sha256")
        if (
            not isinstance(expected_size, int)
            or expected_size < 0
            or not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ConfigurationError(f"{label} metadata is invalid")
        descriptor, opened = self._open_managed_regular(path, label=label)
        try:
            if opened.st_size != expected_size:
                raise ConfigurationError(f"{label} size does not match signed metadata")
            digest = hashlib.sha256()
            total = 0
            while chunk := os.read(descriptor, 64 * 1024):
                total += len(chunk)
                if total > expected_size:
                    raise ConfigurationError(
                        f"{label} grew beyond its signed size while being read"
                    )
                digest.update(chunk)
                if destination is not None:
                    destination.write(chunk)
            if total != expected_size or digest.hexdigest() != expected_sha256:
                raise ConfigurationError(f"{label} checksum does not match signed metadata")
        finally:
            os.close(descriptor)

    def ensure(self) -> None:
        self._validate_state_layout()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.quarantine.mkdir(parents=True, exist_ok=True)
        self._validate_state_layout()
        if os.name != "nt":
            os.chmod(self.state, 0o700)
            os.chmod(self.cache, 0o700)
            os.chmod(self.quarantine, 0o700)

    def output_transaction(self, manifest: Manifest, task: TaskSpec) -> OutputTransaction:
        self.ensure()
        return OutputTransaction(self.state, manifest, task)

    @contextmanager
    def project_lock(self, *, timeout_seconds: float = 60.0):
        """Serialize cacheable runs that can publish into the same checkout.

        A stale lock is never removed automatically: availability can be recovered
        manually, while automatically breaking a lock risks two writers publishing
        outputs concurrently. The lock is local to this project and is not a
        distributed-locking implementation.
        """
        self.ensure()
        locks = self.state / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        lock_path = locks / "project.lock"
        token = f"pid={os.getpid()} created_at={time.time()}"
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        descriptor: int | None = None
        while descriptor is None:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise ProjectLockBusy(
                        f"another cacheable ZeroRun run owns {lock_path}; refusing concurrent output publication"
                    )
                time.sleep(0.05)
        try:
            os.write(descriptor, token.encode("utf-8"))
            yield
        finally:
            os.close(descriptor)
            try:
                if lock_path.read_text(encoding="utf-8") == token:
                    lock_path.unlink()
            except OSError:
                # Do not delete an uncertain lock; fail closed on the next run.
                pass

    def entry(self, key: str) -> Path:
        if (
            not isinstance(key, str)
            or len(key) != 64
            or any(character not in "0123456789abcdef" for character in key)
        ):
            raise ConfigurationError("cache key must be exactly 64 lowercase hexadecimal characters")
        self._validate_state_layout()
        entry = self.cache / key
        self._assert_managed_path(entry, expected_kind="directory")
        return entry

    def metadata(self, key: str) -> dict[str, Any] | None:
        entry = self.entry(key)
        path = self.managed_file(entry / "metadata.json")
        quarantined = self.managed_file(entry / "QUARANTINED")
        if not path.is_file() or quarantined.exists():
            return None
        try:
            descriptor, details = self._open_managed_regular(
                path, label="cache metadata"
            )
            try:
                if details.st_size > _MAX_CACHE_METADATA_BYTES:
                    return None
                chunks: list[bytes] = []
                remaining = _MAX_CACHE_METADATA_BYTES + 1
                while remaining > 0:
                    chunk = os.read(descriptor, min(64 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                payload = b"".join(chunks)
                if len(payload) > _MAX_CACHE_METADATA_BYTES:
                    return None
            finally:
                os.close(descriptor)
            raw = loads_bounded_json(
                payload,
                label="cache metadata",
                limits=_CACHE_METADATA_JSON_LIMITS,
            )
        except (ConfigurationError, OSError):
            return None
        return raw if isinstance(raw, dict) else None

    def validated_metadata(
        self,
        manifest: Manifest,
        task: TaskSpec,
        key: str,
        fingerprint: dict[str, Any],
    ) -> dict[str, Any] | None:
        metadata = self.metadata(key)
        if metadata is None:
            return None
        try:
            self._validate_metadata(manifest, task, key, fingerprint, metadata)
        except ConfigurationError as exc:
            self.quarantine_entry(
                key,
                {"task": task.name, "key": key, "reason": f"invalid cache metadata: {exc}"},
            )
            return None
        return metadata

    def _validate_metadata(
        self,
        manifest: Manifest,
        task: TaskSpec,
        key: str,
        fingerprint: dict[str, Any],
        metadata: dict[str, Any],
    ) -> None:
        if not cache_payload_is_trusted(
            self.root,
            metadata,
            manifest_digest=manifest_sha256(manifest),
        ):
            raise ConfigurationError(
                "cache metadata has no valid external per-user provenance"
            )
        if metadata.get("schema") != 3:
            raise ConfigurationError("cache metadata schema is unsupported")
        if metadata.get("task") != task.name or metadata.get("key") != key:
            raise ConfigurationError("cache metadata task or key does not match this request")
        if metadata.get("fingerprint") != fingerprint:
            raise ConfigurationError("cache metadata fingerprint does not match this request")
        if metadata.get("exit_code") != 0 or metadata.get("streams_cached") is not True:
            raise ConfigurationError("cache metadata is not a successful stream-preserving entry")

        expected = expected_output_paths(manifest, task)
        records = metadata.get("outputs")
        if not isinstance(records, list) or len(records) != len(expected):
            raise ConfigurationError("cache metadata output set does not match the task")

        entry = self.entry(key)
        artifacts_directory = self.managed_directory(entry / "artifacts")
        expected_artifacts: set[str] = set()
        for index, ((relative, _), record) in enumerate(zip(expected, records)):
            if not isinstance(record, dict):
                raise ConfigurationError("cache metadata has an invalid output record")
            artifact_name = f"{index:04d}.bin"
            if record.get("path") != relative or record.get("artifact") != artifact_name:
                raise ConfigurationError("cache metadata output record does not match declared outputs")
            if not isinstance(record.get("size"), int) or not isinstance(record.get("mode"), int):
                raise ConfigurationError("cache metadata output record is incomplete")
            if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
                raise ConfigurationError("cache metadata output hash is invalid")
            artifact = self.managed_file(artifacts_directory / artifact_name)
            if not is_within(artifacts_directory, artifact) or not artifact.is_file():
                raise ConfigurationError("cache artifact is missing or escaped its entry")
            self._copy_verified_managed_file(
                artifact,
                destination=None,
                record=record,
                label=f"cache artifact {relative}",
            )
            expected_artifacts.add(artifact_name)

        artifacts = artifacts_directory
        actual_artifacts = {path.name for path in artifacts.iterdir()} if artifacts.is_dir() else set()
        if actual_artifacts != expected_artifacts:
            raise ConfigurationError("cache entry contains unexpected artifacts")
        for stream_name in ("stdout.bin", "stderr.bin"):
            stream = self.managed_file(entry / stream_name)
            if not stream.is_file():
                raise ConfigurationError(f"cache entry is missing {stream_name}")
        streams = metadata.get("streams")
        if not isinstance(streams, dict):
            raise ConfigurationError("cache metadata stream records are missing")
        for field, filename in (("stdout", "stdout.bin"), ("stderr", "stderr.bin")):
            record = streams.get(field)
            stream = self.managed_file(entry / filename)
            if not isinstance(record, dict) or not isinstance(record.get("size"), int):
                raise ConfigurationError(f"cache metadata {field} stream record is invalid")
            if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
                raise ConfigurationError(f"cache metadata {field} stream hash is invalid")
            self._verified_managed_bytes(
                stream,
                record,
                label=f"cache {field} stream",
            )

    def append_event(self, event: dict[str, Any]) -> None:
        self.ensure()
        self._assert_managed_path(self.events, expected_kind="file")
        enriched = {"timestamp": time.time(), **event}
        try:
            record = (
                json.dumps(enriched, sort_keys=True, allow_nan=False) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError):
            record = b""
        if not record or len(record) > _MAX_EVENT_RECORD_BYTES:
            # Metrics are operational evidence, not a channel for unbounded
            # repository-authored task names, paths, or exception text.
            compact: dict[str, Any] = {
                "timestamp": enriched["timestamp"],
                "status": (
                    enriched.get("status")
                    if isinstance(enriched.get("status"), str)
                    and len(enriched["status"]) <= 64
                    else "EVENT_TRUNCATED"
                ),
                "event_truncated": True,
            }
            for field in (
                "wall_ms",
                "execution_ms",
                "saved_ms",
                "reused_nodes",
                "fresh_nodes",
                "total_nodes",
            ):
                value = enriched.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    try:
                        if math.isfinite(float(value)):
                            compact[field] = value
                    except (OverflowError, ValueError):
                        pass
            record = (
                json.dumps(compact, sort_keys=True, allow_nan=False) + "\n"
            ).encode("utf-8")

        budget = _MAX_EVENT_LOG_BYTES - len(record)
        existing = b""
        if self.events.is_file() and budget > 0:
            descriptor, opened = self._open_managed_regular(
                self.events,
                label="event log",
            )
            try:
                start = max(0, opened.st_size - budget)
                os.lseek(descriptor, start, os.SEEK_SET)
                remaining = budget
                chunks: list[bytes] = []
                while remaining > 0:
                    chunk = os.read(descriptor, min(64 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                existing = b"".join(chunks)
                after_open = os.fstat(descriptor)
                current = self.events.stat(follow_symlinks=False)
                if (
                    _is_link_like(self.events)
                    or not stat.S_ISREG(current.st_mode)
                    or (
                        after_open.st_dev,
                        after_open.st_ino,
                        after_open.st_size,
                        after_open.st_mtime_ns,
                    )
                    != (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mtime_ns,
                    )
                    or (current.st_dev, current.st_ino, current.st_size)
                    != (opened.st_dev, opened.st_ino, opened.st_size)
                ):
                    raise ConfigurationError(
                        "event log changed while its bounded tail was read"
                    )
            finally:
                os.close(descriptor)
            if start:
                separator = existing.find(b"\n")
                existing = existing[separator + 1 :] if separator >= 0 else b""
            if existing and not existing.endswith(b"\n"):
                separator = existing.rfind(b"\n")
                existing = existing[: separator + 1] if separator >= 0 else b""

        # Atomic replacement is intentional: direct append would follow an
        # attacker-planted hard link and mutate an unrelated file. It also
        # keeps this best-effort operational log within a fixed disk budget.
        atomic_replace_bytes(self.events, existing + record, mode=0o600)

    def save(
        self,
        manifest: Manifest,
        task: TaskSpec,
        key: str,
        fingerprint: dict[str, Any],
        *,
        execution_ms: float,
        stdout: bytes,
        stderr: bytes,
        source_root: Path | None = None,
    ) -> dict[str, Any]:
        self.ensure()
        if not task.cache_streams:
            raise ConfigurationError(f"task {task.name!r}: cacheable entries must preserve stdout and stderr")
        final_entry = self.entry(key)
        if final_entry.exists():
            raise ConfigurationError("cache key already exists and will not be overwritten")

        with private_temporary_directory(self.state, prefix="zerorun-entry-") as temp:
            artifacts = temp / "artifacts"
            artifacts.mkdir()
            output_records: list[dict[str, Any]] = []

            output_root = source_root or manifest.root
            for index, (relative, destination) in enumerate(expected_output_paths(manifest, task)):
                source = (output_root / relative).resolve(strict=False)
                if not is_within(output_root, source):
                    raise ConfigurationError(f"task {task.name!r}: staged output escapes its workspace: {relative}")
                if _is_link_like(source) or not source.is_file():
                    raise ConfigurationError(
                        f"task {task.name!r}: MVP only caches regular output files; got {source}"
                    )
                artifact_name = f"{index:04d}.bin"
                artifact = artifacts / artifact_name
                shutil.copyfile(source, artifact)
                mode = source.stat().st_mode & 0o777
                output_records.append(
                    {
                        "path": destination.relative_to(manifest.root).as_posix(),
                        "artifact": artifact_name,
                        "sha256": sha256_file(source),
                        "size": source.stat().st_size,
                        "mode": mode,
                    }
                )

            (temp / "stdout.bin").write_bytes(stdout)
            (temp / "stderr.bin").write_bytes(stderr)

            metadata = {
                "schema": 3,
                "task": task.name,
                "key": key,
                "created_at": time.time(),
                "execution_ms": execution_ms,
                "fingerprint": fingerprint,
                "outputs": output_records,
                "streams_cached": True,
                "streams": {
                    "stdout": {"size": len(stdout), "sha256": hashlib.sha256(stdout).hexdigest()},
                    "stderr": {"size": len(stderr), "sha256": hashlib.sha256(stderr).hexdigest()},
                },
                "exit_code": 0,
            }
            metadata["provenance"] = sign_cache_payload(
                self.root,
                metadata,
                manifest_digest=manifest_sha256(manifest),
            )
            (temp / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
            if os.name != "nt":
                for path in temp.rglob("*"):
                    if path.is_file():
                        os.chmod(path, 0o600)
            try:
                os.replace(temp, final_entry)
            except OSError:
                # Another process may have atomically published the same fully
                # formed key first. Reuse only an authenticated, complete entry;
                # never let an attacker win the race with forged checkout state.
                existing = self.validated_metadata(
                    manifest,
                    task,
                    key,
                    fingerprint,
                )
                if existing is not None:
                    return existing
                raise
            return metadata

    def publish_outputs(
        self,
        manifest: Manifest,
        task: TaskSpec,
        source_root: Path,
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> list[str]:
        """Atomically publish regular outputs produced in an isolated workspace."""
        staged: list[tuple[Path, Path, int]] = []
        transaction = self.output_transaction(manifest, task)
        prepared = False
        try:
            for relative, destination in expected_output_paths(manifest, task):
                source = (source_root / relative).resolve(strict=False)
                if not is_within(source_root, source) or _is_link_like(source) or not source.is_file():
                    raise ConfigurationError(
                        f"task {task.name!r}: isolated execution did not produce a regular output: {relative}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(prefix=".zerorun-", dir=destination.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    with source.open("rb") as input_handle:
                        shutil.copyfileobj(input_handle, handle)
                staged.append((temporary, destination, source.stat().st_mode & 0o777))

            transaction.prepare()
            prepared = True
            # Bind fresh results to the latest live input snapshot after all
            # potentially expensive staging and backup work. Recheck at every
            # replacement boundary and once more before committing so a race
            # during a multi-output publication rolls every output back.
            for temporary, destination, mode in staged:
                if before_commit is not None:
                    before_commit()
                os.replace(temporary, destination)
                if os.name != "nt":
                    os.chmod(destination, mode)
            if before_commit is not None:
                before_commit()
            transaction.commit()
            return [relative for relative, _ in expected_output_paths(manifest, task)]
        except Exception:
            for temporary, _, _ in staged:
                temporary.unlink(missing_ok=True)
            if prepared:
                transaction.rollback()
            raise

    def restore(
        self,
        manifest: Manifest,
        task: TaskSpec,
        key: str,
        fingerprint: dict[str, Any],
        metadata: dict[str, Any],
        *,
        before_commit: Callable[[], None] | None = None,
    ) -> list[str]:
        self._validate_metadata(manifest, task, key, fingerprint, metadata)
        entry = self.entry(key)
        transaction = self.output_transaction(manifest, task)
        staged: list[tuple[Path, Path, int]] = []
        prepared = False
        try:
            for (_, destination), record in zip(expected_output_paths(manifest, task), metadata["outputs"]):
                artifact = self.managed_file(
                    entry / "artifacts" / str(record["artifact"])
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(prefix=".zerorun-", dir=destination.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    staged.append((temporary, destination, int(record["mode"])))
                    self._copy_verified_managed_file(
                        artifact,
                        handle,
                        record,
                        label=f"cache artifact {record['path']}",
                    )

            transaction.prepare()
            prepared = True
            # Verify the caller's live source snapshot after authentication and
            # backup work. Recheck at every replacement boundary and once more
            # before committing; any drift restores all preexisting outputs.
            for temporary, destination, mode in staged:
                if before_commit is not None:
                    before_commit()
                os.replace(temporary, destination)
                if os.name != "nt":
                    os.chmod(destination, mode)
            if before_commit is not None:
                before_commit()
            transaction.commit()
            return [relative for relative, _ in expected_output_paths(manifest, task)]
        except Exception:
            for temporary, _, _ in staged:
                temporary.unlink(missing_ok=True)
            if prepared:
                transaction.rollback()
            raise

    def streams(self, key: str, metadata: dict[str, Any]) -> tuple[bytes, bytes]:
        if metadata.get("streams_cached") is not True:
            raise ConfigurationError("cache entry does not preserve streams")
        entry = self.entry(key)
        stdout_path = self.managed_file(entry / "stdout.bin")
        stderr_path = self.managed_file(entry / "stderr.bin")
        if not stdout_path.is_file() or not stderr_path.is_file():
            raise ConfigurationError("cache entry is missing captured streams")
        streams = metadata.get("streams")
        if not isinstance(streams, dict):
            raise ConfigurationError("cache metadata stream records are missing")
        stdout_record = streams.get("stdout")
        stderr_record = streams.get("stderr")
        if not isinstance(stdout_record, dict) or not isinstance(stderr_record, dict):
            raise ConfigurationError("cache metadata stream records are invalid")
        stdout = self._verified_managed_bytes(
            stdout_path,
            stdout_record,
            label="cache stdout stream",
        )
        stderr = self._verified_managed_bytes(
            stderr_path,
            stderr_record,
            label="cache stderr stream",
        )
        return stdout, stderr

    def quarantine_entry(self, key: str, details: dict[str, Any]) -> Path:
        self.ensure()
        entry = self.entry(key)
        marker = entry / "QUARANTINED"
        self._assert_managed_path(marker, expected_kind="file")
        encoded = json.dumps(
            details,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        atomic_replace_bytes(marker, encoded, mode=0o600)
        report = self.quarantine / f"{key}-{int(time.time())}.json"
        self._assert_managed_path(report, expected_kind="file")
        atomic_replace_bytes(report, encoded, mode=0o600)
        return report

    def clear_cache(self) -> int:
        self._validate_state_layout()
        if not self.cache.exists():
            return 0
        resolved = self.cache.resolve()
        expected = (self.root / ".zerorun" / "cache").resolve()
        if resolved != expected:
            raise ConfigurationError(f"refusing to clear unexpected path: {resolved}")
        count = sum(1 for child in self.cache.iterdir() if child.is_dir())
        shutil.rmtree(self.cache)
        self.cache.mkdir(parents=True)
        return count
