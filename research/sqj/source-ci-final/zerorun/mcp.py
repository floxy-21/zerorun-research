from __future__ import annotations

import io
import json
import math
import os
import platform
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .api import run_task
from .fingerprint import task_fingerprint
from .hermetic import hermetic_environment, inspect_runtime, task_fingerprint_v2
from .hermetic_store import _load_result_entry
from .manifest import DEFAULT_MANIFEST, find_manifest, load_manifest
from .model import ConfigurationError
from .path_safety import is_link_like as _path_is_link_like
from .pilot import build_pilot_report
from .pytest_profile import DEFAULT_PYTEST_PROFILE, load_pytest_profile
from .pytest_prepare_cli import prepare_candidate
from .pytest_runtime import run_pytest_profile
from .runner import read_events, safety_reason
from .store import Store
from .trust import (
    manifest_is_authorized,
    manifest_sha256,
    pytest_profile_is_authorized,
    repository_marker_kind,
    require_manifest_authority,
    require_pytest_profile_authority,
)

SERVER_NAME = "zerorun"
SERVER_VERSION = __version__
MCP_MANIFEST_VERSION = 2
MCP_REQUEST_LIMIT_BYTES = 1024 * 1024
MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_MAX_JSON_DEPTH = 32
MCP_MAX_JSON_NODES = 10_000
MCP_MAX_NUMBER_CHARS = 256
_SERVER_ROOT: Path | None = None
_TOOL_ARGUMENTS = {
    "prepare_pytest": {"root", "approve_setup", "task", "targets"},
    "run_pytest": {"root", "profile"},
    "run_tests": {"root", "task", "verify"},
    "stats": {"root"},
    "explain": {"root", "task"},
    "doctor": {"root"},
    "list_tasks": {"root"},
}


class JsonRpcParseError(ConfigurationError):
    code = -32700


class JsonRpcInvalidRequest(ConfigurationError):
    code = -32600


class JsonRpcInvalidParams(ConfigurationError):
    code = -32602


def _reject_json_constant(value: str) -> object:
    raise ConfigurationError(f"non-finite JSON constant is forbidden: {value}")


def _parse_json_integer(value: str) -> int:
    if len(value) > MCP_MAX_NUMBER_CHARS:
        raise ConfigurationError(
            f"JSON integer exceeds the {MCP_MAX_NUMBER_CHARS} character limit"
        )
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigurationError("invalid JSON integer") from exc


def _parse_json_float(value: str) -> float:
    if len(value) > MCP_MAX_NUMBER_CHARS:
        raise ConfigurationError(
            f"JSON number exceeds the {MCP_MAX_NUMBER_CHARS} character limit"
        )
    try:
        result = float(value)
    except ValueError as exc:
        raise ConfigurationError("invalid JSON number") from exc
    if not math.isfinite(result):
        raise ConfigurationError("non-finite JSON numbers are forbidden")
    return result


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _validate_json_shape(value: object) -> None:
    """Bound decoded protocol structure independently of the byte limit."""

    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MCP_MAX_JSON_NODES:
            raise ConfigurationError(
                f"JSON-RPC message exceeds the {MCP_MAX_JSON_NODES} node limit"
            )
        if depth > MCP_MAX_JSON_DEPTH:
            raise ConfigurationError(
                f"JSON-RPC message exceeds the {MCP_MAX_JSON_DEPTH} level depth limit"
            )
        if isinstance(current, dict):
            if not all(isinstance(key, str) for key in current):
                raise ConfigurationError("JSON-RPC object keys must be strings")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ConfigurationError("non-finite JSON numbers are forbidden")
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            raise ConfigurationError("JSON-RPC message contains a non-JSON value")


def _parse_json_request(line: str) -> dict[str, Any]:
    try:
        request = json.loads(
            line,
            parse_constant=_reject_json_constant,
            parse_float=_parse_json_float,
            parse_int=_parse_json_integer,
            object_pairs_hook=_unique_json_object,
        )
    except ConfigurationError as exc:
        raise JsonRpcParseError(str(exc)) from exc
    except RecursionError as exc:
        raise JsonRpcParseError("JSON-RPC message nesting is too deep") from exc
    except ValueError as exc:
        raise JsonRpcParseError(f"invalid JSON-RPC message: {exc}") from exc
    if not isinstance(request, dict):
        raise JsonRpcInvalidRequest("JSON-RPC message must be an object")
    try:
        _validate_json_shape(request)
    except ConfigurationError as exc:
        raise JsonRpcInvalidRequest(str(exc)) from exc
    return request


def _validate_request_envelope(request: dict[str, Any]) -> None:
    if request.get("jsonrpc") != "2.0":
        raise JsonRpcInvalidRequest("JSON-RPC version must be exactly '2.0'")
    method = request.get("method")
    if not isinstance(method, str) or not method:
        raise JsonRpcInvalidRequest("JSON-RPC method must be a non-empty string")
    if "params" in request and not isinstance(request["params"], (dict, list)):
        raise JsonRpcInvalidRequest("JSON-RPC params must be an object or array")
    if "id" in request:
        request_id = request["id"]
        if (
            isinstance(request_id, bool)
            or request_id is None
            or not isinstance(request_id, (str, int, float))
        ):
            raise JsonRpcInvalidRequest("MCP request id must be a string or number")
        if isinstance(request_id, float) and not request_id.is_integer():
            raise JsonRpcInvalidRequest("JSON-RPC numeric id must be an integer")


def _validate_initialize_params(params: dict[str, Any]) -> str:
    requested = params.get("protocolVersion")
    if not isinstance(requested, str) or not requested:
        raise JsonRpcInvalidParams(
            "initialize requires a non-empty string protocolVersion"
        )
    if not isinstance(params.get("capabilities"), dict):
        raise JsonRpcInvalidParams("initialize requires object client capabilities")
    client_info = params.get("clientInfo")
    if not isinstance(client_info, dict):
        raise JsonRpcInvalidParams("initialize requires object clientInfo")
    for field in ("name", "version"):
        if not isinstance(client_info.get(field), str) or not client_info[field]:
            raise JsonRpcInvalidParams(
                f"initialize clientInfo requires a non-empty string {field}"
            )
    return requested


def _start_path(root: str | None) -> Path:
    start = Path(root).expanduser().resolve() if root else Path.cwd().resolve()
    return start.parent if start.is_file() else start


def _repository_root(root: str | None) -> Path:
    start = _start_path(root)
    for directory in (start, *start.parents):
        if repository_marker_kind(directory) is not None:
            return directory.resolve()
    try:
        return find_manifest(start=start).parent.resolve()
    except ConfigurationError:
        return start


def _required_repository_root(root: str | None) -> Path:
    """Resolve an execution root, rejecting paths outside a source repository."""

    start = _start_path(root)
    for directory in (start, *start.parents):
        if repository_marker_kind(directory) is not None:
            return directory.resolve()
    raise ConfigurationError(
        "test execution and managed setup require a path inside a repository with .git"
    )


def _bound_repository_root(root: str | None) -> Path:
    project_root = _required_repository_root(root)
    if _SERVER_ROOT is not None and project_root != _SERVER_ROOT:
        raise ConfigurationError(
            "the ZeroRun MCP server is bound to "
            f"{_SERVER_ROOT}; refusing access to {project_root}"
        )
    return project_root


def _is_link_like(path: Path) -> bool:
    """Return whether *path* is a symlink or Windows junction/reparse point."""

    try:
        return _path_is_link_like(path)
    except OSError as exc:
        raise ConfigurationError(f"cannot safely inspect {path}: {exc}") from exc


def _load_regular_manifest(path: Path):
    if _is_link_like(path):
        raise ConfigurationError(
            f"refusing {path}: the ZeroRun manifest must be a regular file, "
            "not a symbolic link or junction"
        )
    if not path.is_file():
        raise ConfigurationError(f"no {DEFAULT_MANIFEST} found at {path}")
    return load_manifest(path)


def _manifest_candidate(root: str | None) -> Path | None:
    """Locate a manifest without allowing a nested repository to inherit one."""

    start = _start_path(root)
    for directory in (start, *start.parents):
        if repository_marker_kind(directory) is not None:
            candidate = directory / DEFAULT_MANIFEST
            if not candidate.exists() and not _is_link_like(candidate):
                return None
            return candidate

    for directory in (start, *start.parents):
        candidate = directory / DEFAULT_MANIFEST
        if candidate.exists() or _is_link_like(candidate):
            return candidate
    return None


def _manifest_for_root(root: str | None):
    candidate = _manifest_candidate(root)
    if candidate is None:
        repository = _repository_root(root)
        raise ConfigurationError(
            f"no {DEFAULT_MANIFEST} found in repository {repository}"
        )
    return _load_regular_manifest(candidate)


def _optional_manifest(root: str | None):
    candidate = _manifest_candidate(root)
    if candidate is None:
        return None
    return _load_regular_manifest(candidate)


def _require_mcp_manifest_v2(manifest, *, authority: bool = True) -> None:
    if manifest.version != MCP_MANIFEST_VERSION:
        raise ConfigurationError(
            "Codex/MCP execution requires a reviewed version 2 manifest with a "
            "pinned hermetic runtime; legacy version 1 manifests are CLI-only"
        )
    if authority:
        require_manifest_authority(manifest)


def _mcp_task_policy_reason(task) -> str | None:
    if task.env:
        return (
            "Codex/MCP refuses repository-declared host environment forwarding; "
            "use an environment-free v2 task"
        )
    return None


def _mcp_task_reuse_ready(manifest, task) -> bool:
    if safety_reason(task) is not None or _mcp_task_policy_reason(task) is not None:
        return False
    try:
        inspect_runtime(
            task,
            allow_pull=False,
            repository_root=manifest.root,
        )
    except (ConfigurationError, OSError):
        return False
    return True


def _require_mcp_execution_task(manifest, task) -> None:
    _require_mcp_manifest_v2(manifest, authority=False)
    reason = _mcp_task_policy_reason(task)
    if reason is not None:
        raise ConfigurationError(reason)
    require_manifest_authority(manifest)
    # Execution tools never acquire an image implicitly. Managed preparation has
    # its own explicit setup approval; normal runs require the pinned image to
    # have been acquired already.
    inspect_runtime(
        task,
        allow_pull=False,
        repository_root=manifest.root,
    )


def _task(manifest, name: str):
    if name not in manifest.tasks:
        raise ConfigurationError(
            f"unknown task {name!r}; available: {', '.join(sorted(manifest.tasks))}"
        )
    return manifest.tasks[name]


def _task_key(manifest, task):
    """Compute a diagnostic key without acquiring external runtime state."""
    if manifest.version == 2:
        runtime = inspect_runtime(
            task,
            allow_pull=False,
            repository_root=manifest.root,
        )
        _, env_fp = hermetic_environment(task)
        return task_fingerprint_v2(
            manifest, task, runtime, environment_fingerprint=env_fp
        )
    return task_fingerprint(manifest, task)


def _profile_path(manifest, raw: object) -> Path:
    if raw is None:
        candidate = manifest.root / DEFAULT_PYTEST_PROFILE
    else:
        if not isinstance(raw, str) or not raw:
            raise ConfigurationError("profile must be a non-empty string path")
        supplied = Path(raw).expanduser()
        candidate = (
            Path(os.path.abspath(supplied))
            if supplied.is_absolute()
            else Path(os.path.abspath(manifest.root / supplied))
        )
    try:
        relative = candidate.relative_to(manifest.root.resolve())
    except ValueError as exc:
        raise ConfigurationError(
            "pytest profile must remain inside the bound repository"
        ) from exc
    cursor = manifest.root.resolve()
    for part in relative.parts:
        cursor = cursor / part
        if _is_link_like(cursor):
            raise ConfigurationError(
                f"pytest profile path contains a symbolic link or junction: {cursor}"
            )
    return candidate


def _stats_payload(root: str | None) -> dict[str, Any]:
    manifest = _optional_manifest(root)
    project_root = manifest.root if manifest is not None else _repository_root(root)
    events = read_events(Store(project_root))
    counts: dict[str, int] = {}
    for event in events:
        status = str(event.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    saved_ms = round(sum(float(event.get("saved_ms", 0) or 0) for event in events), 3)
    wall_ms = round(sum(float(event.get("wall_ms", 0) or 0) for event in events), 3)
    conservative_no_zerorun_ms = round(wall_ms + saved_ms, 3)
    effective_speedup = (
        conservative_no_zerorun_ms / wall_ms if wall_ms > 0 else 1.0
    )
    saved_percent = (
        saved_ms / conservative_no_zerorun_ms * 100.0
        if conservative_no_zerorun_ms > 0
        else 0.0
    )
    reused_nodes = sum(int(event.get("reused_nodes", 0) or 0) for event in events)
    fresh_nodes = sum(int(event.get("fresh_nodes", 0) or 0) for event in events)
    total_nodes = sum(int(event.get("total_nodes", 0) or 0) for event in events)
    reuse_percent = reused_nodes / total_nodes * 100.0 if total_nodes else 0.0
    execution_ms = round(
        sum(float(event.get("execution_ms", 0) or 0) for event in events), 3
    )
    observed_ms = round(
        sum(
            float(event.get("execution_ms", 0) or 0)
            for event in events
            if event.get("status") == "OBSERVED_EXECUTED"
        ),
        3,
    )
    pytest_profile = project_root / DEFAULT_PYTEST_PROFILE
    mcp_manifest_supported = bool(
        manifest is not None and manifest.version == MCP_MANIFEST_VERSION
    )
    manifest_authorized = bool(
        mcp_manifest_supported
        and manifest is not None
        and manifest_is_authorized(manifest)
    )
    task_reuse_ready = bool(
        manifest_authorized
        and any(
            _mcp_task_reuse_ready(manifest, task)
            for task in manifest.tasks.values()
        )
    )
    pytest_reuse_ready = False
    if manifest_authorized and pytest_profile.is_file():
        try:
            profile = load_pytest_profile(pytest_profile, manifest)
            if _mcp_task_policy_reason(manifest.tasks[profile.task_name]) is not None:
                raise ConfigurationError("pytest profile task violates MCP policy")
            if not pytest_profile_is_authorized(manifest, profile.profile_sha256):
                raise ConfigurationError(
                    "pytest profile has no matching external user authority"
                )
            if not _mcp_task_reuse_ready(
                manifest, manifest.tasks[profile.task_name]
            ):
                raise ConfigurationError("pytest profile runtime is not ready")
        except ConfigurationError:
            pytest_reuse_ready = False
        else:
            pytest_reuse_ready = True
    mode = (
        "pytest-node-reuse"
        if pytest_reuse_ready
        else "task-reuse"
        if task_reuse_ready
        else "observe-only"
    )
    return {
        "root": str(project_root),
        "manifest_version": manifest.version if manifest is not None else None,
        "mcp_manifest_supported": mcp_manifest_supported,
        "manifest_authorized": manifest_authorized,
        "mode": mode,
        "reuse_ready": task_reuse_ready or pytest_reuse_ready,
        "task_reuse_ready": task_reuse_ready,
        "pytest_reuse_ready": pytest_reuse_ready,
        "pytest_profile_present": pytest_profile.is_file(),
        "events": len(events),
        "counts": counts,
        "saved_ms": saved_ms,
        "saved_seconds": round(saved_ms / 1000.0, 3),
        "verified_saved_seconds": round(saved_ms / 1000.0, 3),
        "wall_ms": wall_ms,
        "actual_seconds": round(wall_ms / 1000.0, 3),
        "conservative_no_zerorun_ms": conservative_no_zerorun_ms,
        "conservative_no_zerorun_seconds": round(
            conservative_no_zerorun_ms / 1000.0, 3
        ),
        "effective_speedup": round(effective_speedup, 3),
        "saved_percent": round(saved_percent, 3),
        "reused_nodes": reused_nodes,
        "fresh_nodes": fresh_nodes,
        "total_nodes": total_nodes,
        "reuse_percent": round(reuse_percent, 3),
        "execution_ms": execution_ms,
        "observed_execution_ms": observed_ms,
        "observed_execution_seconds": round(observed_ms / 1000.0, 3),
        "pilot": build_pilot_report(events),
    }


def _doctor_payload(root: str | None) -> dict[str, Any]:
    manifest = _optional_manifest(root)
    if manifest is None:
        project_root = _repository_root(root)
        return {
            "root": str(project_root),
            "manifest": str(project_root / DEFAULT_MANIFEST),
            "manifest_present": False,
            "pytest_profile_present": False,
            "mode": "observe-only",
            "reuse_ready": False,
            "manifest_version": None,
            "mcp_manifest_supported": False,
            "platform": platform.platform(),
            "contract": "observation only; no test-result reuse is authorized without a reviewed ZeroRun manifest",
            "tasks": [],
            "ok": True,
        }

    mcp_manifest_supported = manifest.version == MCP_MANIFEST_VERSION
    manifest_authorized = bool(
        mcp_manifest_supported and manifest_is_authorized(manifest)
    )
    checks = []
    for task in manifest.tasks.values():
        reason = safety_reason(task)
        key = None
        if not mcp_manifest_supported:
            status = "UNSUPPORTED"
            reason = (
                "legacy version 1 manifests are CLI-only; Codex/MCP requires a "
                "reviewed version 2 hermetic manifest"
            )
        elif not manifest_authorized:
            status = "UNTRUSTED"
            reason = (
                "manifest has no matching external per-user authority for exact "
                f"SHA-256 {manifest_sha256(manifest)}"
            )
        elif _mcp_task_policy_reason(task) is not None:
            status = "INVALID"
            reason = _mcp_task_policy_reason(task)
        else:
            try:
                key, _ = _task_key(manifest, task) if reason is None else (None, None)
                status = "CACHEABLE" if reason is None else "EXECUTE_ONLY"
            except ConfigurationError as exc:
                status = "INVALID"
                reason = str(exc)
        checks.append(
            {"task": task.name, "status": status, "reason": reason, "cache_key": key}
        )
    profile_path = manifest.root / DEFAULT_PYTEST_PROFILE
    profile_status: dict[str, Any] = {"present": profile_path.is_file(), "valid": False}
    if manifest_authorized and profile_path.is_file():
        try:
            profile = load_pytest_profile(profile_path, manifest)
            policy_reason = _mcp_task_policy_reason(
                manifest.tasks[profile.task_name]
            )
            if policy_reason is not None:
                raise ConfigurationError(policy_reason)
            if not pytest_profile_is_authorized(manifest, profile.profile_sha256):
                raise ConfigurationError(
                    "pytest profile has no matching external per-user authority"
                )
        except ConfigurationError as exc:
            profile_status["error"] = str(exc)
        else:
            profile_status.update(
                {
                    "valid": True,
                    "task": profile.task_name,
                    "reviewed_nodes": len(profile.nodes),
                    "profile_sha256": profile.profile_sha256,
                }
            )
    ok = mcp_manifest_supported and manifest_authorized and not any(
        check["status"] in {"INVALID", "UNTRUSTED"} for check in checks
    )
    task_reuse_ready = any(check["status"] == "CACHEABLE" for check in checks)
    profile_task = profile_status.get("task")
    pytest_reuse_ready = bool(
        profile_status.get("valid")
        and isinstance(profile_task, str)
        and any(
            check["task"] == profile_task and check["status"] == "CACHEABLE"
            for check in checks
        )
    )
    return {
        "root": str(manifest.root),
        "manifest": str(manifest.path),
        "manifest_present": True,
        "manifest_version": manifest.version,
        "mcp_manifest_supported": mcp_manifest_supported,
        "manifest_authorized": manifest_authorized,
        "pytest_profile": profile_status,
        "pytest_profile_present": profile_path.is_file(),
        "mode": (
            "pytest-node-reuse"
            if pytest_reuse_ready
            else "task-reuse"
            if task_reuse_ready
            else "observe-only"
        ),
        "reuse_ready": task_reuse_ready or pytest_reuse_ready,
        "task_reuse_ready": task_reuse_ready,
        "pytest_reuse_ready": pytest_reuse_ready,
        "platform": platform.platform(),
        "contract": (
            "pinned OCI digest; Linux/amd64; network-disabled read-only checkout; reviewed source closure; result-only reuse; per-action-key lock"
            if mcp_manifest_supported
            else "legacy manifest is CLI-only; Codex/MCP execution is refused"
        ),
        "tasks": checks,
        "ok": ok,
    }


def _text_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _tools() -> list[dict[str, Any]]:
    root_property = {
        "type": "string",
        "description": "Repository root or any path inside the repository. Defaults to the MCP server working directory.",
    }
    return [
        {
            "name": "prepare_pytest",
            "description": (
                "With explicit setup approval, inspect and execute pytest collection, "
                "possibly create managed repository files and acquire a pinned runtime, "
                "then generate a non-authorizing .zerorun-pytest.candidate.json. This "
                "never activates reuse."
            ),
            "annotations": {
                "title": "Prepare reviewed pytest candidate",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "root": root_property,
                    "approve_setup": {
                        "type": "boolean",
                        "description": (
                            "Must be true only after the user approves managed setup, "
                            "repository-file changes, runtime acquisition, and pytest collection."
                        ),
                    },
                    "task": {
                        "type": "string",
                        "description": "Optional configured pytest task name. Auto-selected when exactly one direct python -m pytest task exists.",
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "Optional pytest targets. Defaults to tests/, test/, testing/, then repository root.",
                    },
                },
                "required": ["approve_setup"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_pytest",
            "description": (
                "Run the reviewed generic pytest node adapter. It reuses only exact nodes authorized by a valid .zerorun-pytest.json profile; unknown, changed, unsupported, or racing nodes execute fresh."
                " The pinned image must already be present and host environment forwarding is refused."
            ),
            "annotations": {
                "title": "Run reviewed pytest selection",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "root": root_property,
                    "profile": {
                        "type": "string",
                        "description": "Profile path relative to the repository root. Defaults to .zerorun-pytest.json.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "run_tests",
            "description": (
                "Run a configured task-level ZeroRun task. Use run_pytest instead when a valid reviewed pytest profile is present."
                " The task must be hermetic v2, environment-free, and use an already-present pinned image."
            ),
            "annotations": {
                "title": "Run reviewed ZeroRun task",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "root": root_property,
                    "verify": {"type": "boolean", "default": False},
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        },
        {
            "name": "stats",
            "description": "Return local ZeroRun reuse counts, conservative verified time saved, observation time, and pilot metrics.",
            "annotations": {
                "title": "Read ZeroRun statistics",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {"root": root_property},
                "additionalProperties": False,
            },
        },
        {
            "name": "explain",
            "description": "Explain whether a configured task is currently a cache HIT, MISS, or safety BYPASS without executing it; invalid cache metadata is quarantined.",
            "annotations": {
                "title": "Explain reuse decision",
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {"task": {"type": "string"}, "root": root_property},
                "required": ["task"],
                "additionalProperties": False,
            },
        },
        {
            "name": "doctor",
            "description": "Validate ZeroRun setup, including the reviewed pytest profile when present.",
            "annotations": {
                "title": "Validate ZeroRun setup",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {"root": root_property},
                "additionalProperties": False,
            },
        },
        {
            "name": "list_tasks",
            "description": "List configured ZeroRun tasks and their cache policy.",
            "annotations": {
                "title": "List ZeroRun tasks",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            "inputSchema": {
                "type": "object",
                "properties": {"root": root_property},
                "additionalProperties": False,
            },
        },
    ]


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    allowed_arguments = _TOOL_ARGUMENTS.get(name)
    if allowed_arguments is None:
        raise ConfigurationError(f"unknown MCP tool {name!r}")
    unexpected_arguments = sorted(set(arguments) - allowed_arguments)
    if unexpected_arguments:
        raise ConfigurationError(
            f"unexpected arguments for MCP tool {name!r}: "
            + ", ".join(unexpected_arguments)
        )
    root = arguments.get("root")
    if root is not None and not isinstance(root, str):
        raise ConfigurationError("root must be a string path")
    if _SERVER_ROOT is not None:
        _bound_repository_root(root)

    if name == "prepare_pytest":
        if arguments.get("approve_setup") is not True:
            raise ConfigurationError(
                "prepare_pytest requires approve_setup=true after explicit user approval"
            )
        task_name = arguments.get("task")
        if task_name is not None and (not isinstance(task_name, str) or not task_name):
            raise ConfigurationError("task must be a non-empty string when provided")
        raw_targets = arguments.get("targets")
        if raw_targets is not None and (
            not isinstance(raw_targets, list)
            or not raw_targets
            or not all(isinstance(item, str) and item for item in raw_targets)
        ):
            raise ConfigurationError("targets must be a non-empty string array when provided")
        project_root = _bound_repository_root(root)
        existing_manifest = _optional_manifest(str(project_root))
        if existing_manifest is not None:
            # Preparation is explicitly approved, performs no reuse, and only
            # emits a candidate.  External authority is required later before
            # any cache lookup or reuse-ready claim.
            _require_mcp_manifest_v2(existing_manifest, authority=False)
            if any(
                _mcp_task_policy_reason(task) is not None
                for task in existing_manifest.tasks.values()
            ):
                raise ConfigurationError(
                    "managed MCP preparation refuses manifests that request host environment forwarding"
                )
        payload = prepare_candidate(
            project_root,
            task_name=task_name,
            targets=tuple(raw_targets) if raw_targets is not None else None,
        )
        manifest = _manifest_for_root(str(project_root))
        reviewable = sum(
            1 for row in payload["nodes"].values() if row.get("reviewable") is True
        )
        fresh_required = sum(
            1 for row in payload["nodes"].values() if row.get("fresh_required") is True
        )
        summary = {
            "status": "CANDIDATE_READY",
            "authorizes_reuse": False,
            "reuse_activated": False,
            "candidate_sha256": payload["candidate_sha256"],
            "task": payload["task"],
            "targets": payload["targets"],
            "node_count": len(payload["nodes"]),
            "candidate_reviewable_nodes": reviewable,
            "fresh_required_nodes": fresh_required,
            "output": str(manifest.root / ".zerorun-pytest.candidate.json"),
            "managed_task_bootstrapped": payload["task"] == "pytest-managed",
            "task_level_reuse": bool(manifest.tasks[payload["task"]].cacheable),
            "next_action": (
                "review closure completeness and node independence before activating any reuse"
            ),
        }
        return _text_result(summary)

    if name == "run_pytest":
        manifest = _manifest_for_root(root)
        # A repository-controlled profile may be large and adversarial.  Check
        # exact external manifest authority before parsing any profile bytes.
        _require_mcp_manifest_v2(manifest, authority=True)
        profile = load_pytest_profile(_profile_path(manifest, arguments.get("profile")), manifest)
        require_pytest_profile_authority(manifest, profile.profile_sha256)
        _require_mcp_execution_task(manifest, manifest.tasks[profile.task_name])
        payload = run_pytest_profile(manifest, profile)
        payload["mode"] = "pytest-node-reuse"
        payload["saved_seconds"] = round(float(payload.get("saved_ms", 0.0)) / 1000.0, 3)
        return _text_result(payload, is_error=int(payload.get("exit_code", 1)) != 0)

    if name == "run_tests":
        task_name = arguments.get("task")
        if not isinstance(task_name, str) or not task_name:
            raise ConfigurationError("task must be a non-empty string")
        verify = arguments.get("verify", False)
        if not isinstance(verify, bool):
            raise ConfigurationError("verify must be a boolean")
        manifest = _manifest_for_root(root)
        task = _task(manifest, task_name)
        _require_mcp_execution_task(manifest, task)
        stdout = io.BytesIO()
        stderr = io.BytesIO()
        result = run_task(
            manifest,
            task,
            verify=verify,
            stdout=stdout,
            stderr=stderr,
        )
        payload = result.as_dict()
        payload["mode"] = "reuse"
        payload["stdout_tail"] = stdout.getvalue()[-4000:].decode("utf-8", errors="replace")
        payload["stderr_tail"] = stderr.getvalue()[-4000:].decode("utf-8", errors="replace")
        payload["saved_seconds"] = round(float(payload.get("saved_ms", 0.0)) / 1000.0, 3)
        return _text_result(payload, is_error=result.exit_code != 0)

    if name == "stats":
        return _text_result(_stats_payload(root))

    if name == "explain":
        task_name = arguments.get("task")
        if not isinstance(task_name, str) or not task_name:
            raise ConfigurationError("task must be a non-empty string")
        manifest = _optional_manifest(root)
        if manifest is None:
            return _text_result(
                {
                    "task": task_name,
                    "decision": "BYPASS",
                    "reason": "no reviewed ZeroRun manifest; observation-only mode cannot authorize reuse",
                    "cache_key": None,
                    "mode": "observe-only",
                }
            )
        if manifest.version != MCP_MANIFEST_VERSION:
            return _text_result(
                {
                    "task": task_name,
                    "decision": "BYPASS",
                    "reason": "legacy version 1 manifests are CLI-only; Codex/MCP requires version 2",
                    "cache_key": None,
                    "mode": "observe-only",
                }
            )
        store = Store(manifest.root)
        task = _task(manifest, task_name)
        reason = safety_reason(task)
        key = None
        decision = "BYPASS"
        policy_reason = _mcp_task_policy_reason(task)
        if policy_reason is not None:
            reason = policy_reason
        elif reason is None:
            if not manifest_is_authorized(manifest):
                reason = (
                    "manifest has no matching external per-user authority; "
                    "Codex/MCP reuse is disabled"
                )
            else:
                key, fingerprint = _task_key(manifest, task)
                cached = _load_result_entry(store, task, key, fingerprint)
                decision = "HIT" if cached is not None else "MISS"
                reason = (
                    "all declared dependencies and signed result provenance are unchanged"
                    if decision == "HIT"
                    else "no matching externally authenticated cache entry"
                )
        return _text_result(
            {
                "task": task.name,
                "decision": decision,
                "reason": reason,
                "cache_key": key,
                "mode": "reuse",
            }
        )

    if name == "doctor":
        payload = _doctor_payload(root)
        return _text_result(payload, is_error=not payload["ok"])

    if name == "list_tasks":
        manifest = _optional_manifest(root)
        if manifest is None:
            return _text_result(
                {
                    "mode": "observe-only",
                    "reuse_ready": False,
                    "tasks": [],
                    "reason": "no reviewed ZeroRun manifest",
                }
            )
        mcp_manifest_supported = manifest.version == MCP_MANIFEST_VERSION
        manifest_authorized = bool(
            mcp_manifest_supported and manifest_is_authorized(manifest)
        )
        rows = [
            {
                "task": task.name,
                "cacheable": task.cacheable,
                "unsafe_effects": list(task.unsafe_effects),
                "command": list(task.command),
                "result_only": task.result_only,
                "image": task.image,
                "platform": task.platform,
            }
            for task in manifest.tasks.values()
        ]
        task_reuse_ready = manifest_authorized and any(
            _mcp_task_reuse_ready(manifest, task) for task in manifest.tasks.values()
        )
        profile_path = manifest.root / DEFAULT_PYTEST_PROFILE
        pytest_reuse_ready = False
        if manifest_authorized and profile_path.is_file():
            try:
                profile = load_pytest_profile(profile_path, manifest)
                if _mcp_task_policy_reason(
                    manifest.tasks[profile.task_name]
                ) is not None:
                    raise ConfigurationError("pytest profile task violates MCP policy")
                if not pytest_profile_is_authorized(
                    manifest, profile.profile_sha256
                ):
                    raise ConfigurationError(
                        "pytest profile has no matching external user authority"
                    )
                if not _mcp_task_reuse_ready(
                    manifest, manifest.tasks[profile.task_name]
                ):
                    raise ConfigurationError("pytest profile runtime is not ready")
            except ConfigurationError:
                pass
            else:
                pytest_reuse_ready = True
        return _text_result(
            {
                "mode": (
                    "pytest-node-reuse"
                    if pytest_reuse_ready
                    else "task-reuse"
                    if task_reuse_ready
                    else "observe-only"
                ),
                "reuse_ready": task_reuse_ready or pytest_reuse_ready,
                "manifest_version": manifest.version,
                "mcp_manifest_supported": mcp_manifest_supported,
                "manifest_authorized": manifest_authorized,
                "task_reuse_ready": task_reuse_ready,
                "pytest_reuse_ready": pytest_reuse_ready,
                "tasks": rows,
            }
        )

    raise AssertionError(f"unhandled MCP tool {name!r}")


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    _validate_json_shape(request)
    _validate_request_envelope(request)
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params", {})

    if method in {
        "notifications/initialized",
        "notifications/cancelled",
        "initialize",
        "ping",
        "tools/list",
        "tools/call",
        "resources/list",
        "prompts/list",
    } and not isinstance(params, dict):
        raise JsonRpcInvalidParams(f"{method} params must be an object")

    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None

    if method == "initialize":
        requested = _validate_initialize_params(params)
        protocol = (
            requested
            if requested == MCP_PROTOCOL_VERSION
            else MCP_PROTOCOL_VERSION
        )
        result = {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "ZeroRun is repository-bound and fail-closed. Execution tools may run "
                "tests or create local setup state; honor their approval fields and "
                "never treat a candidate, bypass, miss, or uncertainty as reusable."
            ),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": _tools()}
    elif method == "tools/call":
        if not isinstance(params, dict):
            raise JsonRpcInvalidParams("tools/call params must be an object")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise JsonRpcInvalidParams(
                "tools/call requires a tool name and object arguments"
            )
        try:
            result = _call_tool(name, arguments)
        except (ConfigurationError, OSError, RuntimeError) as exc:
            result = _text_result(
                {"status": "ERROR", "error": str(exc)},
                is_error=True,
            )
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    else:
        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _read_bounded_stdio_line() -> tuple[str | None, bool]:
    """Read and, when necessary, drain one JSON-RPC line with bounded memory."""

    raw = sys.stdin.readline(MCP_REQUEST_LIMIT_BYTES + 1)
    if raw == "":
        return None, False
    oversized = len(raw.encode("utf-8")) > MCP_REQUEST_LIMIT_BYTES
    if not raw.endswith("\n"):
        while True:
            remainder = sys.stdin.readline(MCP_REQUEST_LIMIT_BYTES + 1)
            if remainder == "" or remainder.endswith("\n"):
                break
            oversized = True
    return raw if not oversized else "", oversized


def serve_stdio() -> int:
    global _SERVER_ROOT
    try:
        _SERVER_ROOT = _required_repository_root(None)
    except ConfigurationError as exc:
        print(f"ZeroRun MCP startup refused: {exc}", file=sys.stderr)
        return 2
    while True:
        raw_line, oversized = _read_bounded_stdio_line()
        if raw_line is None:
            break
        if oversized:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": (
                        "JSON-RPC request exceeds the 1 MiB safety limit"
                    ),
                },
            }
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue
        line = raw_line.strip()
        if not line:
            continue
        request_id: str | int | float | None = None
        is_notification = False
        try:
            request = _parse_json_request(line)
            candidate_id = request.get("id")
            if (
                isinstance(candidate_id, str)
                or (
                    not isinstance(candidate_id, bool)
                    and isinstance(candidate_id, int)
                )
                or (
                    isinstance(candidate_id, float)
                    and math.isfinite(candidate_id)
                    and candidate_id.is_integer()
                )
            ):
                request_id = candidate_id
            # A message is a notification only after it is known to be a valid
            # JSON-RPC Request object.  An envelope-invalid object without an
            # id still requires an Invalid Request response with ``id: null``.
            _validate_request_envelope(request)
            is_notification = "id" not in request
            response = handle_request(request)
        except (JsonRpcParseError, JsonRpcInvalidRequest, JsonRpcInvalidParams) as exc:
            response = None if is_notification else {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except (ConfigurationError, OSError, RecursionError, RuntimeError) as exc:
            response = None if is_notification else {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(exc)},
            }
        if response is not None:
            sys.stdout.write(
                json.dumps(response, separators=(",", ":"), allow_nan=False) + "\n"
            )
            sys.stdout.flush()
    return 0
