from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import is_link_like
from .pytest_node_key import PYTEST_SOURCE_REVIEW_SCHEMA


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_PROFILE_BYTES = 8 * 1024 * 1024
_MAX_JSON_DEPTH = 8
_MAX_JSON_VALUES = 250_000
_MAX_JSON_OBJECT_MEMBERS = 25_000
_MAX_JSON_STRUCTURAL_TOKENS = 500_000
_MAX_STRING_CHARS = 8_192
_MAX_TOTAL_STRING_CHARS = 6 * 1024 * 1024
_MAX_ENCODED_STRING_BYTES = (_MAX_STRING_CHARS * 6) + 2
_MAX_PROFILE_NODES = 25_000
_MAX_TOTAL_NODEID_CHARS = 4 * 1024 * 1024
_MAX_BASE_ARGS = 256
_MAX_TARGETS = 4_096
_MAX_STATIC_INPUTS = 25_000
_MAX_CLOSURE_ITEMS = 25_000
_PROFILE_ROOT_FIELDS = {
    "schema",
    "task",
    "base_args",
    "targets",
    "static_inputs",
    "session_closure",
    "nodes",
    "independence_review_sha256",
    # Added by the explicit review/activation flow. Older directly activated
    # reviewed profiles omit these fields and remain supported.
    "candidate_sha256",
    "review_record_sha256",
    "review_schema",
    "review_scope",
    # Optional for backward-readable profiles. Runtime treats profiles without
    # this qualification-time source authority as permanently fresh.
    "source_review",
}
_PROFILE_REQUIRED_FIELDS = {
    "schema",
    "task",
    "base_args",
    "targets",
    "static_inputs",
    "session_closure",
    "nodes",
    "independence_review_sha256",
}
_REVIEW_METADATA_FIELDS = {
    "candidate_sha256",
    "review_record_sha256",
    "review_schema",
    "review_scope",
}
_CLOSURE_FIELDS = {"selectors", "fallback_files"}
_CLOSURE_ALLOWED_FIELDS = {*_CLOSURE_FIELDS, "absent_files"}
_NODE_FIELDS = {
    "reviewable",
    "fresh_required",
    "reason",
    "selectors",
    "fallback_files",
    "absent_files",
}
_SOURCE_REVIEW_FIELDS = {
    "schema",
    "profiler_sha256",
    "collection_plugin_sha256",
    "static_inputs_sha256",
    "session_closure_sha256",
    "node_closure_sha256",
}
DEFAULT_PYTEST_PROFILE = ".zerorun-pytest.json"
_PROFILE_PARSE_CACHE_MAX_ENTRIES = 8
_PROFILE_PARSE_CACHE: OrderedDict[
    str,
    tuple[tuple[int | None, ...], str, "PytestProfile"],
] = OrderedDict()
_PROFILE_PARSE_CACHE_LOCK = threading.RLock()
PYTEST_RESERVED_ENV_NAMES = frozenset({"PYTEST_ADDOPTS", "PYTEST_PLUGINS"})
_PYTEST_ALLOWED_TASK_SUFFIXES = frozenset(
    {(), ("-p", "no:cacheprovider")}
)


def validate_pytest_execution_contract(
    task: TaskSpec,
    *,
    base_args: tuple[str, ...],
    field: str,
) -> tuple[str, ...]:
    """Return the only pytest invocation covered by the reviewed model.

    Generated profiles select tests only through the separately validated
    ``targets`` field. Additional pytest arguments could select extra paths,
    inject an argument file, alter plugin/config discovery, or otherwise escape
    that reviewed surface. Keep the supported command grammar deliberately
    closed until such inputs have a dedicated binding schema.
    """

    if base_args:
        raise ConfigurationError(
            f"{field} requires empty profile base_args; all selection is bound "
            "through reviewed targets"
        )
    command = tuple(task.command)
    direct_python = (
        len(command) >= 3
        and Path(command[0]).name in {"python", "python3"}
        and command[1:3] == ("-m", "pytest")
    )
    managed_shell = (
        len(command) >= 4
        and Path(command[0]).name in {"sh", "bash"}
        and command[1] == ".zerorun-env/bin/python"
        and command[2:4] == ("-m", "pytest")
    )
    if not (direct_python or managed_shell):
        raise ConfigurationError(
            f"{field} requires Python '-m pytest' or the ZeroRun managed pytest wrapper"
        )
    suffix = command[3 if direct_python else 4 :]
    if suffix not in _PYTEST_ALLOWED_TASK_SUFFIXES:
        raise ConfigurationError(
            f"{field} contains unsupported pytest command arguments; the task "
            "suffix must be empty or exactly '-p no:cacheprovider'"
        )
    reserved_environment = sorted(
        set(task.env).intersection(PYTEST_RESERVED_ENV_NAMES)
    )
    if reserved_environment:
        raise ConfigurationError(
            f"{field} forwards unsupported pytest control environment: "
            + ", ".join(reserved_environment)
        )
    return command


class _FrozenMapping(Mapping[str, Any]):
    """Recursively immutable validated JSON mapping.

    This deliberately does not inherit from ``dict``.  A ``dict`` subclass
    can have its Python-level mutator overrides bypassed with
    ``dict.__setitem__``; a read-only mapping proxy has no such base-class
    mutation channel.
    """

    __slots__ = ("__values",)

    def __init__(self, values: dict[str, Any]) -> None:
        object.__setattr__(self, "_FrozenMapping__values", MappingProxyType(values))

    def __getitem__(self, key: str) -> Any:
        return self.__values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.__values)

    def __len__(self) -> int:
        return len(self.__values)

    def __setitem__(self, _key: str, _value: Any) -> None:
        raise TypeError("validated pytest profiles are immutable")

    def __delitem__(self, _key: str) -> None:
        raise TypeError("validated pytest profiles are immutable")

    def __copy__(self):
        return self

    def __deepcopy__(self, _memo: dict[int, Any]):
        return self


class _FrozenList(tuple[Any, ...]):
    """Immutable JSON array with JSON-value equality against plain lists."""

    def __new__(cls, values: Any):
        return super().__new__(cls, values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return False

    __hash__ = None


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMapping(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return _FrozenList(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True)
class PytestProfile:
    path: Path
    task_name: str
    base_args: tuple[str, ...]
    targets: tuple[str, ...]
    static_inputs: tuple[str, ...]
    session_closure: Mapping[str, Any]
    nodes: Mapping[str, Mapping[str, Any]]
    source_review: Mapping[str, Any] | None
    independence_review_sha256: str
    profile_sha256: str
    candidate_sha256: str | None
    review_record_sha256: str | None

    @property
    def task(self) -> str:
        return self.task_name


def _clone_profile(profile: PytestProfile) -> PytestProfile:
    """Validated profiles are recursively immutable and safe to share."""

    return profile


def clear_pytest_profile_cache() -> None:
    """Drop process-local validated parses; used by tests and diagnostics."""

    with _PROFILE_PARSE_CACHE_LOCK:
        _PROFILE_PARSE_CACHE.clear()


def _cached_profile(
    path: Path,
    *,
    descriptor_identity: tuple[int | None, ...],
    profile_sha256: str,
    manifest: Manifest,
) -> PytestProfile | None:
    cache_key = os.path.normcase(str(path))
    with _PROFILE_PARSE_CACHE_LOCK:
        cached = _PROFILE_PARSE_CACHE.get(cache_key)
        if cached is None:
            return None
        cached_identity, cached_sha256, cached_profile = cached
        if (
            cached_identity != descriptor_identity
            or cached_sha256 != profile_sha256
            or cached_profile.task_name not in manifest.tasks
        ):
            _PROFILE_PARSE_CACHE.pop(cache_key, None)
            return None
        _validate_task(manifest.tasks[cached_profile.task_name])
        _PROFILE_PARSE_CACHE.move_to_end(cache_key)
        return _clone_profile(cached_profile)


def _remember_profile(
    profile: PytestProfile,
    *,
    descriptor_identity: tuple[int | None, ...],
) -> None:
    cache_key = os.path.normcase(str(profile.path))
    with _PROFILE_PARSE_CACHE_LOCK:
        _PROFILE_PARSE_CACHE[cache_key] = (
            descriptor_identity,
            profile.profile_sha256,
            _clone_profile(profile),
        )
        _PROFILE_PARSE_CACHE.move_to_end(cache_key)
        while len(_PROFILE_PARSE_CACHE) > _PROFILE_PARSE_CACHE_MAX_ENTRIES:
            _PROFILE_PARSE_CACHE.popitem(last=False)


def _preflight_json_bytes(raw: bytes) -> None:
    """Reject structurally explosive JSON before Python materializes it.

    This is deliberately a conservative lexical pass, not a JSON parser.  The
    standard decoder remains authoritative after depth, structural-token, and
    encoded-string bounds have made parsing safe.
    """

    depth = 0
    structural_tokens = 0
    in_string = False
    escaped = False
    string_bytes = 0
    for byte in raw:
        if in_string:
            string_bytes += 1
            if string_bytes > _MAX_ENCODED_STRING_BYTES:
                raise ConfigurationError(
                    "pytest profile contains an overlong encoded JSON string"
                )
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
            string_bytes = 0
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            structural_tokens += 1
            if depth > _MAX_JSON_DEPTH:
                raise ConfigurationError(
                    f"pytest profile exceeds the JSON depth limit of {_MAX_JSON_DEPTH}"
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
                "pytest profile exceeds the JSON structural-member limit"
            )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len(pairs) > _MAX_JSON_OBJECT_MEMBERS:
        raise ConfigurationError(
            "pytest profile contains an object with too many members"
        )
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(
                f"pytest profile contains duplicate JSON member {key!r}"
            )
        result[key] = value
    return result


def _validate_json_shape(raw: object) -> None:
    stack: list[tuple[object, int]] = [(raw, 1)]
    values = 0
    total_string_chars = 0
    while stack:
        value, depth = stack.pop()
        values += 1
        if values > _MAX_JSON_VALUES:
            raise ConfigurationError("pytest profile contains too many JSON values")
        if depth > _MAX_JSON_DEPTH:
            raise ConfigurationError(
                f"pytest profile exceeds the JSON depth limit of {_MAX_JSON_DEPTH}"
            )
        if isinstance(value, dict):
            if len(value) > _MAX_JSON_OBJECT_MEMBERS:
                raise ConfigurationError(
                    "pytest profile contains an object with too many members"
                )
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ConfigurationError("pytest profile object keys must be strings")
                total_string_chars += len(key)
                if len(key) > _MAX_STRING_CHARS or "\x00" in key:
                    raise ConfigurationError(
                        "pytest profile contains an invalid or overlong object key"
                    )
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > _MAX_JSON_VALUES:
                raise ConfigurationError("pytest profile contains an oversized JSON array")
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            total_string_chars += len(value)
            if len(value) > _MAX_STRING_CHARS or "\x00" in value:
                raise ConfigurationError(
                    "pytest profile contains an invalid or overlong string"
                )
        elif value is not None and not isinstance(value, (bool, int)):
            raise ConfigurationError(
                "pytest profile contains a JSON value outside its bounded schema"
            )
        if total_string_chars > _MAX_TOTAL_STRING_CHARS:
            raise ConfigurationError(
                "pytest profile exceeds the aggregate string-size limit"
            )


def _require_fields(
    raw: dict[str, Any],
    *,
    field: str,
    allowed: set[str],
    required: set[str] | None = None,
) -> None:
    unknown = set(raw) - allowed
    missing = (required or set()) - set(raw)
    if unknown:
        raise ConfigurationError(
            f"pytest profile {field} contains unsupported members: "
            + ", ".join(sorted(unknown))
        )
    if missing:
        raise ConfigurationError(
            f"pytest profile {field} is missing required members: "
            + ", ".join(sorted(missing))
        )


def _string_list(
    raw: object,
    *,
    field: str,
    max_items: int,
    nonempty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise ConfigurationError(f"pytest profile {field} must be a string array")
    if len(raw) > max_items:
        raise ConfigurationError(
            f"pytest profile {field} exceeds the {max_items}-item limit"
        )
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > _MAX_STRING_CHARS
            or "\x00" in item
        ):
            raise ConfigurationError(f"pytest profile {field} must be a bounded string array")
        if item in seen:
            raise ConfigurationError(f"pytest profile {field} contains duplicate strings")
        seen.add(item)
        values.append(item)
    if nonempty and not values:
        raise ConfigurationError(f"pytest profile {field} must not be empty")
    return tuple(values)


def _closure(
    raw: object,
    *,
    field: str,
    allowed_fields: set[str] = _CLOSURE_ALLOWED_FIELDS,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigurationError(f"pytest profile {field} must be an object")
    _require_fields(
        raw,
        field=field,
        allowed=allowed_fields,
        required=_CLOSURE_FIELDS,
    )
    selectors = _string_list(
        raw.get("selectors"),
        field=f"{field}.selectors",
        max_items=_MAX_CLOSURE_ITEMS,
    )
    if any(
        "::" not in item
        or not item.split("::", 1)[0].endswith(".py")
        or not item.split("::", 1)[1]
        for item in selectors
    ):
        raise ConfigurationError(
            f"pytest profile {field}.selectors must use 'relative.py::qualified.symbol' selectors"
        )
    fallback_files = _string_list(
        raw.get("fallback_files"),
        field=f"{field}.fallback_files",
        max_items=_MAX_CLOSURE_ITEMS,
    )
    absent_files = _string_list(
        raw.get("absent_files", []),
        field=f"{field}.absent_files",
        max_items=_MAX_CLOSURE_ITEMS,
    )
    if set(absent_files).intersection(fallback_files):
        raise ConfigurationError(
            f"pytest profile {field} cannot require a path to be both present and absent"
        )
    if not selectors and not fallback_files:
        raise ConfigurationError(
            f"pytest profile {field} must contain selectors and/or fallback_files"
        )
    return {
        "selectors": list(selectors),
        "fallback_files": list(fallback_files),
        "absent_files": list(absent_files),
    }


def _source_review(
    raw: object,
    *,
    nodes: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigurationError("pytest profile source_review must be an object")
    _require_fields(
        raw,
        field="source_review",
        allowed=_SOURCE_REVIEW_FIELDS,
        required=_SOURCE_REVIEW_FIELDS,
    )
    if raw.get("schema") != PYTEST_SOURCE_REVIEW_SCHEMA:
        raise ConfigurationError("pytest profile source_review schema is unsupported")
    for field in (
        "profiler_sha256",
        "collection_plugin_sha256",
        "static_inputs_sha256",
        "session_closure_sha256",
    ):
        value = raw.get(field)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ConfigurationError(
                f"pytest profile source_review.{field} must be a lowercase SHA-256 digest"
            )
    node_anchors = raw.get("node_closure_sha256")
    if not isinstance(node_anchors, dict):
        raise ConfigurationError(
            "pytest profile source_review.node_closure_sha256 must be an object"
        )
    expected = {
        nodeid
        for nodeid, row in nodes.items()
        if row["reviewable"] and not row["fresh_required"]
    }
    if set(node_anchors) != expected:
        raise ConfigurationError(
            "pytest profile source_review node anchors do not match reviewable nodes"
        )
    if any(
        not isinstance(value, str) or not _SHA256_RE.fullmatch(value)
        for value in node_anchors.values()
    ):
        raise ConfigurationError(
            "pytest profile source_review node anchors must be lowercase SHA-256 digests"
        )
    return {
        "schema": PYTEST_SOURCE_REVIEW_SCHEMA,
        "profiler_sha256": raw["profiler_sha256"],
        "collection_plugin_sha256": raw["collection_plugin_sha256"],
        "static_inputs_sha256": raw["static_inputs_sha256"],
        "session_closure_sha256": raw["session_closure_sha256"],
        "node_closure_sha256": dict(node_anchors),
    }


def load_pytest_profile(path: Path, manifest: Manifest) -> PytestProfile:
    supplied = path.expanduser()
    path = Path(os.path.abspath(supplied))
    root = manifest.root.expanduser().resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(
            "pytest profile must remain inside the manifest repository"
        ) from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if is_link_like(cursor):
            raise ConfigurationError(
                f"pytest profile path contains a symbolic link or junction: {cursor}"
            )
    if os.path.normcase(str(path.resolve(strict=False))) != os.path.normcase(str(path)):
        raise ConfigurationError(
            f"pytest profile does not resolve to its exact repository path: {path}"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ConfigurationError(
                    f"pytest profile is not a regular file: {path}"
                )
            if opened.st_size > _MAX_PROFILE_BYTES:
                raise ConfigurationError(
                    "pytest profile exceeds the 8 MiB safety limit"
                )
            chunks: list[bytes] = []
            remaining = _MAX_PROFILE_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw_bytes = b"".join(chunks)
            if len(raw_bytes) > _MAX_PROFILE_BYTES:
                raise ConfigurationError(
                    "pytest profile exceeds the 8 MiB safety limit"
                )
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
                len(raw_bytes) != opened.st_size
                or is_link_like(path)
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
                raise ConfigurationError(
                    "pytest profile changed while it was being read"
                )
        finally:
            os.close(descriptor)
        profile_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        cached_profile = _cached_profile(
            path,
            descriptor_identity=opened_descriptor_identity,
            profile_sha256=profile_sha256,
            manifest=manifest,
        )
        if cached_profile is not None:
            return cached_profile
        _preflight_json_bytes(raw_bytes)
        raw = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
        _validate_json_shape(raw)
    except ConfigurationError:
        raise
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise ConfigurationError(f"could not read pytest profile {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("pytest profile root must be an object")
    _require_fields(
        raw,
        field="root",
        allowed=_PROFILE_ROOT_FIELDS,
        required=_PROFILE_REQUIRED_FIELDS,
    )
    if not isinstance(raw.get("schema"), int) or isinstance(
        raw.get("schema"), bool
    ) or raw.get("schema") != 1:
        raise ConfigurationError("pytest profile schema must be 1")

    task_name = raw.get("task")
    if (
        not isinstance(task_name, str)
        or not task_name
        or len(task_name) > _MAX_STRING_CHARS
        or task_name not in manifest.tasks
    ):
        raise ConfigurationError("pytest profile task must name a configured ZeroRun task")
    task = manifest.tasks[task_name]
    _validate_task(task)

    base_args = _string_list(
        raw.get("base_args"), field="base_args", max_items=_MAX_BASE_ARGS
    )
    validate_pytest_execution_contract(
        task,
        base_args=base_args,
        field="pytest profile",
    )
    forbidden = {"--collect-only", "--co", "--continue-on-collection-errors"}
    if any(item in forbidden for item in base_args):
        raise ConfigurationError("pytest profile base_args contains collection-control flags")
    targets = _string_list(
        raw.get("targets"),
        field="targets",
        max_items=_MAX_TARGETS,
        nonempty=True,
    )
    static_inputs = _string_list(
        raw.get("static_inputs"),
        field="static_inputs",
        max_items=_MAX_STATIC_INPUTS,
        nonempty=True,
    )
    session_closure = _closure(raw.get("session_closure"), field="session_closure")

    nodes_raw = raw.get("nodes")
    if not isinstance(nodes_raw, dict) or not nodes_raw:
        raise ConfigurationError("pytest profile nodes must be a non-empty object")
    if len(nodes_raw) > _MAX_PROFILE_NODES:
        raise ConfigurationError(
            f"pytest profile nodes exceeds the {_MAX_PROFILE_NODES}-node limit"
        )
    total_nodeid_chars = 0
    nodes: dict[str, dict[str, Any]] = {}
    for nodeid, node_raw in nodes_raw.items():
        if (
            not isinstance(nodeid, str)
            or not nodeid
            or len(nodeid) > _MAX_STRING_CHARS
            or "\x00" in nodeid
        ):
            raise ConfigurationError(
                "pytest profile node ids must be bounded non-empty strings"
            )
        total_nodeid_chars += len(nodeid)
        if total_nodeid_chars > _MAX_TOTAL_NODEID_CHARS:
            raise ConfigurationError(
                "pytest profile exceeds the aggregate node-id size limit"
            )
        if not isinstance(node_raw, dict):
            raise ConfigurationError(f"pytest profile node {nodeid!r} must be an object")
        _require_fields(
            node_raw,
            field=f"node {nodeid!r}",
            allowed=_NODE_FIELDS,
            required={"reviewable", "fresh_required", "selectors", "fallback_files"},
        )
        if not isinstance(node_raw.get("fresh_required"), bool) or not isinstance(
            node_raw.get("reviewable"), bool
        ):
            raise ConfigurationError(
                f"pytest profile node {nodeid!r} review flags must be booleans"
            )
        fresh_required = node_raw["fresh_required"]
        reviewable = node_raw["reviewable"]
        reason = node_raw.get("reason")
        if reason is not None and (
            not isinstance(reason, str)
            or len(reason) > _MAX_STRING_CHARS
            or "\x00" in reason
        ):
            raise ConfigurationError(
                f"pytest profile node {nodeid!r} reason must be a bounded string"
            )
        row: dict[str, Any] = {
            "nodeid": nodeid,
            "fresh_required": fresh_required,
            "reviewable": reviewable,
            "reason": reason,
        }
        if reviewable and not fresh_required:
            closure = _closure(
                node_raw,
                field=f"nodes.{nodeid}",
                allowed_fields=_NODE_FIELDS,
            )
            row.update(closure)
        else:
            selectors = _string_list(
                node_raw.get("selectors"),
                field=f"nodes.{nodeid}.selectors",
                max_items=_MAX_CLOSURE_ITEMS,
            )
            fallback_files = _string_list(
                node_raw.get("fallback_files"),
                field=f"nodes.{nodeid}.fallback_files",
                max_items=_MAX_CLOSURE_ITEMS,
            )
            absent_files = _string_list(
                node_raw.get("absent_files", []),
                field=f"nodes.{nodeid}.absent_files",
                max_items=_MAX_CLOSURE_ITEMS,
            )
            if selectors or fallback_files or absent_files:
                raise ConfigurationError(
                    f"pytest profile node {nodeid!r} has ignored closure members"
                )
            if not fresh_required:
                raise ConfigurationError(
                    f"pytest profile node {nodeid!r} is neither reviewable nor fresh-required"
                )
            row.update({"selectors": [], "fallback_files": [], "absent_files": []})
        nodes[nodeid] = row

    source_review = _source_review(raw.get("source_review"), nodes=nodes)

    review_sha = raw.get("independence_review_sha256")
    if not isinstance(review_sha, str) or not _SHA256_RE.fullmatch(review_sha):
        raise ConfigurationError(
            "pytest profile independence_review_sha256 must be a lowercase SHA-256 digest"
        )

    present_review_metadata = set(raw) & _REVIEW_METADATA_FIELDS
    if present_review_metadata and present_review_metadata != _REVIEW_METADATA_FIELDS:
        raise ConfigurationError(
            "pytest profile explicit review metadata must be complete"
        )
    if present_review_metadata:
        candidate_sha = raw.get("candidate_sha256")
        review_record_sha = raw.get("review_record_sha256")
        if not isinstance(candidate_sha, str) or not _SHA256_RE.fullmatch(candidate_sha):
            raise ConfigurationError(
                "pytest profile candidate_sha256 must be a lowercase SHA-256 digest"
            )
        if review_record_sha != review_sha:
            raise ConfigurationError(
                "pytest profile review_record_sha256 must match independence review authority"
            )
        if raw.get("review_schema") != "zerorun-pytest-review-v1":
            raise ConfigurationError("pytest profile review_schema is unsupported")
        review_scope = raw.get("review_scope")
        expected_scope_fields = {
            "candidate_node_count",
            "candidate_reviewable_nodes",
            "fresh_required_nodes",
        }
        if not isinstance(review_scope, dict):
            raise ConfigurationError("pytest profile review_scope must be an object")
        _require_fields(
            review_scope,
            field="review_scope",
            allowed=expected_scope_fields,
            required=expected_scope_fields,
        )
        for field in expected_scope_fields:
            value = review_scope.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ConfigurationError(
                    f"pytest profile review_scope.{field} must be a non-negative integer"
                )
        if review_scope["candidate_node_count"] != len(nodes):
            raise ConfigurationError(
                "pytest profile review_scope node count does not match nodes"
            )
        if review_scope["candidate_reviewable_nodes"] != sum(
            row["reviewable"] and not row["fresh_required"] for row in nodes.values()
        ):
            raise ConfigurationError(
                "pytest profile review_scope reviewable count does not match nodes"
            )
        if review_scope["fresh_required_nodes"] != sum(
            row["fresh_required"] for row in nodes.values()
        ):
            raise ConfigurationError(
                "pytest profile review_scope fresh-required count does not match nodes"
            )

    profile = PytestProfile(
        path=path,
        task_name=task_name,
        base_args=base_args,
        targets=targets,
        static_inputs=static_inputs,
        session_closure=_freeze_json(session_closure),
        nodes=_freeze_json(nodes),
        source_review=(
            _freeze_json(source_review) if source_review is not None else None
        ),
        independence_review_sha256=review_sha,
        # Authority and cache provenance bind the exact reviewed file bytes,
        # including any later non-semantic rewrite of the review artifact.
        profile_sha256=profile_sha256,
        candidate_sha256=(
            str(raw["candidate_sha256"])
            if present_review_metadata
            else None
        ),
        review_record_sha256=(
            str(raw["review_record_sha256"])
            if present_review_metadata
            else None
        ),
    )
    _remember_profile(
        profile,
        descriptor_identity=opened_descriptor_identity,
    )
    return profile


def _validate_task(task: TaskSpec) -> None:
    if not task.result_only or not task.closure_reviewed:
        raise ConfigurationError(
            "generic pytest adapter requires a result-only, closure-reviewed hermetic v2 task"
        )
    if task.unsafe_effects:
        raise ConfigurationError("generic pytest adapter refuses tasks with unsafe effects")
    if task.platform != "linux/amd64" or task.image is None:
        raise ConfigurationError("generic pytest adapter requires a pinned Linux/amd64 OCI task")
    validate_pytest_execution_contract(
        task,
        base_args=(),
        field="pytest carrier task",
    )
