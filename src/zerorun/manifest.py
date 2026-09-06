from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any

from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import is_link_like as _is_link_like


DEFAULT_MANIFEST = ".zerorun.json"
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_JSON_DEPTH = 8
_MAX_JSON_VALUES = 250_000
_MAX_JSON_OBJECT_MEMBERS = 25_000
_MAX_JSON_STRUCTURAL_TOKENS = 500_000
_MAX_JSON_NUMBER_CHARS = 256


def _preflight_json_bytes(raw: bytes) -> None:
    """Bound structural JSON work before the standard decoder materializes it."""

    depth = 0
    structural_tokens = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            structural_tokens += 1
            if depth > _MAX_JSON_DEPTH:
                raise ConfigurationError(
                    f"manifest exceeds the JSON depth limit of {_MAX_JSON_DEPTH}"
                )
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
            structural_tokens += 1
            if depth < 0:
                break  # the JSON decoder will report the syntax error
        elif byte in (0x2C, 0x3A):  # comma, colon
            structural_tokens += 1
        if structural_tokens > _MAX_JSON_STRUCTURAL_TOKENS:
            raise ConfigurationError(
                "manifest exceeds the JSON structural-member limit"
            )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) > _MAX_JSON_OBJECT_MEMBERS:
        raise ConfigurationError("manifest contains an object with too many members")
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(
                f"manifest contains duplicate JSON member {key!r}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ConfigurationError(f"manifest contains non-finite JSON constant {value}")


def _parse_json_integer(value: str) -> int:
    if len(value) > _MAX_JSON_NUMBER_CHARS:
        raise ConfigurationError(
            f"manifest JSON integer exceeds the {_MAX_JSON_NUMBER_CHARS}-character limit"
        )
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError("manifest contains an invalid JSON integer") from exc


def _parse_json_float(value: str) -> float:
    if len(value) > _MAX_JSON_NUMBER_CHARS:
        raise ConfigurationError(
            f"manifest JSON number exceeds the {_MAX_JSON_NUMBER_CHARS}-character limit"
        )
    try:
        result = float(value)
    except ValueError as exc:
        raise ConfigurationError("manifest contains an invalid JSON number") from exc
    if not math.isfinite(result):
        raise ConfigurationError("manifest contains a non-finite JSON number")
    return result


def _validate_json_shape(raw: object) -> None:
    stack: list[tuple[object, int]] = [(raw, 1)]
    values = 0
    while stack:
        value, depth = stack.pop()
        values += 1
        if values > _MAX_JSON_VALUES:
            raise ConfigurationError("manifest contains too many JSON values")
        if depth > _MAX_JSON_DEPTH:
            raise ConfigurationError(
                f"manifest exceeds the JSON depth limit of {_MAX_JSON_DEPTH}"
            )
        if isinstance(value, dict):
            if len(value) > _MAX_JSON_OBJECT_MEMBERS:
                raise ConfigurationError(
                    "manifest contains an object with too many members"
                )
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ConfigurationError("manifest contains a non-finite JSON number")
            raise ConfigurationError("manifest contains an unsupported JSON number")
        elif value is not None and not isinstance(value, (str, int, bool)):
            raise ConfigurationError("manifest contains a value outside JSON")


def _load_strict_json(path: Path, source_bytes: bytes) -> object:
    try:
        _preflight_json_bytes(source_bytes)
        raw = json.loads(
            source_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
            parse_float=_parse_json_float,
            parse_int=_parse_json_integer,
            object_pairs_hook=_strict_object,
        )
        _validate_json_shape(raw)
        return raw
    except ConfigurationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
        OverflowError,
    ) as exc:
        raise ConfigurationError(f"invalid JSON in {path}: {exc}") from exc


def _regular_manifest_path(path: Path) -> Path:
    candidate = Path(os.path.abspath(path.expanduser()))
    if _is_link_like(candidate):
        raise ConfigurationError(
            f"manifest must be a regular file, not a symbolic link or junction: {candidate}"
        )
    if not candidate.is_file():
        raise ConfigurationError(f"manifest not found: {candidate}")
    return candidate


def read_regular_manifest_bytes(path: Path) -> tuple[Path, bytes]:
    """Read one stable, bounded regular manifest without following file links."""

    path = Path(os.path.abspath(path.expanduser()))
    if _is_link_like(path):
        raise ConfigurationError(
            f"manifest must be a regular file, not a symbolic link or junction: {path}"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError(f"manifest not found or unreadable: {path}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ConfigurationError(f"manifest is not a regular file: {path}")
        if opened.st_size > _MAX_MANIFEST_BYTES:
            raise ConfigurationError("manifest exceeds the 8 MiB safety limit")
        chunks: list[bytes] = []
        remaining = _MAX_MANIFEST_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise ConfigurationError("manifest exceeds the 8 MiB safety limit")
        after_open = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        opened_descriptor_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            getattr(opened, "st_ctime_ns", None),
        )
        opened_path_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        if (
            len(payload) != opened.st_size
            or _is_link_like(path)
            or not stat.S_ISREG(current.st_mode)
            or (
                after_open.st_dev,
                after_open.st_ino,
                after_open.st_size,
                after_open.st_mtime_ns,
                getattr(after_open, "st_ctime_ns", None),
            )
            != opened_descriptor_identity
            or (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            )
            != opened_path_identity
        ):
            raise ConfigurationError("manifest changed while it was being read")
        canonical = path.resolve(strict=True)
        resolved = canonical.stat()
        if (
            resolved.st_dev,
            resolved.st_ino,
            resolved.st_size,
            resolved.st_mtime_ns,
        ) != opened_path_identity:
            raise ConfigurationError("manifest changed while it was being read")
        return canonical, payload
    except OSError as exc:
        raise ConfigurationError(f"manifest became unreadable: {path}: {exc}") from exc
    finally:
        os.close(descriptor)


def find_manifest(start: Path | None = None, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return _regular_manifest_path(explicit)

    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / DEFAULT_MANIFEST
        if candidate.exists() or _is_link_like(candidate):
            return _regular_manifest_path(candidate)
    raise ConfigurationError(f"no {DEFAULT_MANIFEST} found from {current}")


def load_manifest(path: Path) -> Manifest:
    path, source_bytes = read_regular_manifest_bytes(path)
    raw = _load_strict_json(path, source_bytes)

    if not isinstance(raw, dict):
        raise ConfigurationError("manifest root must be an object")
    version = raw.get("version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ConfigurationError(f"unsupported manifest version: {version}")
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, dict) or not raw_tasks:
        raise ConfigurationError("manifest tasks must be a non-empty object")

    tasks: dict[str, TaskSpec] = {}
    for name, task_raw in raw_tasks.items():
        if not isinstance(name, str) or not name:
            raise ConfigurationError("task names must be non-empty strings")
        if not isinstance(task_raw, dict):
            raise ConfigurationError(f"task {name!r} must be an object")
        tasks[name] = TaskSpec.from_dict(name, task_raw, version=version)

    return Manifest(
        root=path.parent.resolve(),
        path=path,
        tasks=tasks,
        version=version,
        source_sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
