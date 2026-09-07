#!/usr/bin/env python3
"""Run one sealed, read-only Codex-to-ZeroRun ``doctor`` smoke.

This is a functional integration check, not an agent-effect, performance, user,
or commercial-viability experiment.  It records adverse outcomes instead of
discarding them and never executes repository code through ZeroRun.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from zerorun import __version__ as SOURCE_ZERORUN_VERSION
from zerorun.bounded_json import JsonLimits, loads_bounded_json
from zerorun.model import ConfigurationError
from zerorun.oci import _TRUNCATION_MARKER, _run_bounded_process
from zerorun.path_safety import private_temporary_directory


SCHEMA = "zerorun.codex-agent-integration-smoke/v1"
FINAL_MARKER = "ZERORUN_DOCTOR_SMOKE_COMPLETE"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_OUTPUT_LIMIT_BYTES = 2 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 20.0
COMMAND_OUTPUT_LIMIT_BYTES = 256 * 1024
MAX_JSONL_LINES = 1024
MAX_JSONL_LINE_BYTES = 256 * 1024
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_RELEVANT_FILE_BYTES = 32 * 1024 * 1024

OFFICIAL_NONINTERACTIVE_DOC = (
    "https://learn.chatgpt.com/docs/non-interactive-mode"
)
OFFICIAL_MCP_DOC = "https://learn.chatgpt.com/docs/extend/mcp?surface=cli"

_SAFE_ITEM_TYPES = {"reasoning", "agent_message"}
_TERMINAL_EVENT_TYPES = {"turn.completed", "turn.failed"}
_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "item.started",
    "item.updated",
    "item.completed",
    "error",
}
_RELEVANT_PATHS = (
    ".zerorun.json",
    ".zerorun-pytest.json",
    ".zerorun-pytest.candidate.json",
    ".zerorun-pytest.review.json",
    ".agents/skills/zerorun/SKILL.md",
    ".codex/config.toml",
    ".zerorun/events.jsonl",
)
_SECRET_ENV_RE = re.compile(
    r"(?:^|_)(?:ACCESS_?KEY|API_?KEY|AUTH(?:ORIZATION)?|BEARER|COOKIE|"
    r"CRED(?:ENTIALS?)?|DATABASE_?URL|CONNECTION_?STRING|PASS(?:WORD|PHRASE)?|"
    r"PAT|PRIVATE_?KEY|SECRET|SESSION|TOKEN)(?:_|$)",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"^[A-Za-z0-9_.-]+\s+([^\s]+)$")


class SmokeError(RuntimeError):
    """A bounded integration check failed closed."""


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


Runner = Callable[..., CommandResult]
Clock = Callable[[], datetime]


_JSON_LIMITS = JsonLimits(
    max_bytes=MAX_JSONL_LINE_BYTES,
    max_depth=32,
    max_values=20_000,
    max_object_members=512,
    max_structural_tokens=40_000,
    max_number_chars=64,
    max_string_chars=MAX_JSONL_LINE_BYTES,
    max_total_string_chars=MAX_JSONL_LINE_BYTES,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path | None,
    environment: dict[str, str],
    timeout_seconds: float,
    output_limit_bytes: int,
    input_bytes: bytes | None = None,
) -> CommandResult:
    argv = [str(part) for part in command]
    completed, timed_out = _run_bounded_process(
        argv,
        cwd=cwd,
        environment=environment,
        timeout_seconds=timeout_seconds,
        output_limit_bytes=output_limit_bytes,
        input_bytes=input_bytes,
    )
    stdout = completed.stdout
    stderr = completed.stderr
    return CommandResult(
        command=tuple(argv),
        returncode=int(completed.returncode),
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        stdout_truncated=stdout.startswith(_TRUNCATION_MARKER),
        stderr_truncated=stderr.startswith(_TRUNCATION_MARKER),
    )


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_link_like(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & 0x0400
    )


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _decode_utf8(raw: bytes, *, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SmokeError(f"{label} was not valid UTF-8") from exc


def _require_complete(result: CommandResult, *, label: str) -> None:
    if result.timed_out:
        raise SmokeError(f"{label} exceeded its time limit")
    if result.stdout_truncated or result.stderr_truncated:
        raise SmokeError(f"{label} exceeded its output limit")


def _run_small(
    runner: Runner,
    command: Sequence[str],
    *,
    label: str,
    cwd: Path | None,
    environment: dict[str, str],
) -> CommandResult:
    result = runner(
        command,
        cwd=cwd,
        environment=environment,
        timeout_seconds=COMMAND_TIMEOUT_SECONDS,
        output_limit_bytes=COMMAND_OUTPUT_LIMIT_BYTES,
        input_bytes=None,
    )
    _require_complete(result, label=label)
    if result.returncode != 0:
        tail = _decode_utf8(result.stderr or result.stdout, label=f"{label} output")
        raise SmokeError(f"{label} failed with exit code {result.returncode}: {tail[-500:]}")
    return result


def _hash_regular_file(path: Path, *, max_bytes: int, label: str) -> dict[str, Any]:
    lexical = _absolute_lexical(path)
    try:
        before = lexical.lstat()
    except OSError as exc:
        raise SmokeError(f"{label} is unavailable: {lexical}: {exc}") from exc
    if _is_link_like(before) or not stat.S_ISREG(before.st_mode):
        raise SmokeError(f"{label} is not a regular, non-linked file: {lexical}")
    if before.st_size > max_bytes:
        raise SmokeError(f"{label} exceeds the {max_bytes}-byte hashing limit")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise SmokeError(f"could not open {label}: {lexical}: {exc}") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise SmokeError(f"{label} changed to a special file while opening")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise SmokeError(f"{label} grew beyond the hashing limit")
            digest.update(chunk)
    finally:
        os.close(descriptor)
    after = lexical.lstat()
    if (
        _is_link_like(after)
        or not stat.S_ISREG(after.st_mode)
        or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise SmokeError(f"{label} changed while it was hashed")
    return {
        "path": str(lexical),
        "size": size,
        "sha256": digest.hexdigest(),
    }


def _resolve_executable(
    value: str | None,
    *,
    name: str,
    repository_root: Path,
) -> tuple[Path, Path]:
    discovered = value or shutil.which(name)
    if not discovered:
        raise SmokeError(f"{name} executable was not found")
    lexical = _absolute_lexical(Path(discovered))
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise SmokeError(f"{name} executable could not be resolved: {lexical}") from exc
    if _within(lexical, repository_root) or _within(resolved, repository_root):
        raise SmokeError(f"refusing a repository-controlled {name} executable")
    if not resolved.is_file():
        raise SmokeError(f"{name} executable is not a regular file: {resolved}")
    return lexical, resolved


def _version(
    runner: Runner,
    executable: Path,
    *,
    label: str,
    environment: dict[str, str],
) -> dict[str, str]:
    result = _run_small(
        runner,
        [str(executable), "--version"],
        label=f"{label} version check",
        cwd=None,
        environment=environment,
    )
    value = _decode_utf8(result.stdout, label=f"{label} version").strip()
    if len(value) > 256 or "\n" in value or "\r" in value:
        raise SmokeError(f"{label} returned an invalid version line")
    match = _VERSION_RE.fullmatch(value)
    if match is None:
        raise SmokeError(f"{label} returned an unrecognized version line: {value!r}")
    return {"raw": value, "version": match.group(1)}


def _load_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = loads_bounded_json(raw, label=label, limits=_JSON_LIMITS)
    except ConfigurationError as exc:
        raise SmokeError(str(exc)) from exc
    if not isinstance(value, dict):
        raise SmokeError(f"{label} must be a JSON object")
    return value


def _registration(
    runner: Runner,
    codex: Path,
    *,
    root: Path,
    environment: dict[str, str],
) -> tuple[dict[str, Any], Path, Path]:
    result = _run_small(
        runner,
        [str(codex), "mcp", "get", "zerorun", "--json"],
        label="Codex ZeroRun MCP registration inspection",
        cwd=None,
        environment=environment,
    )
    registration = _load_object(result.stdout, label="Codex MCP registration")
    if registration.get("name") != "zerorun":
        raise SmokeError("Codex MCP registration name is not 'zerorun'")
    if registration.get("enabled") is not True or registration.get("disabled_reason") is not None:
        raise SmokeError("Codex ZeroRun MCP registration is not enabled")
    transport = registration.get("transport")
    if not isinstance(transport, dict) or transport.get("type") != "stdio":
        raise SmokeError("Codex ZeroRun MCP registration is not stdio")
    command = transport.get("command")
    arguments = transport.get("args")
    if not isinstance(command, str) or not command:
        raise SmokeError("Codex ZeroRun MCP registration has no command")
    if arguments != ["mcp-server"]:
        raise SmokeError("Codex ZeroRun MCP registration args are not exactly ['mcp-server']")
    if transport.get("env") not in (None, {}) or transport.get("env_vars") not in (None, []):
        raise SmokeError("Codex ZeroRun MCP registration forwards environment values")
    configured_cwd = transport.get("cwd")
    if configured_cwd is not None:
        if not isinstance(configured_cwd, str):
            raise SmokeError("Codex ZeroRun MCP registration cwd is invalid")
        try:
            configured = Path(configured_cwd).expanduser().resolve(strict=True)
        except OSError as exc:
            raise SmokeError("Codex ZeroRun MCP registration cwd is unavailable") from exc
        if os.path.normcase(str(configured)) != os.path.normcase(str(root)):
            raise SmokeError("Codex ZeroRun MCP registration is bound to another cwd")
    enabled_tools = registration.get("enabled_tools")
    disabled_tools = registration.get("disabled_tools")
    if enabled_tools is not None and (
        not isinstance(enabled_tools, list) or "doctor" not in enabled_tools
    ):
        raise SmokeError("Codex ZeroRun MCP registration does not enable doctor")
    if disabled_tools is not None and (
        not isinstance(disabled_tools, list) or "doctor" in disabled_tools
    ):
        raise SmokeError("Codex ZeroRun MCP registration disables doctor")
    lexical, resolved = _resolve_executable(
        command,
        name="zerorun",
        repository_root=root,
    )
    return registration, lexical, resolved


def _git_environment(environment: dict[str, str]) -> dict[str, str]:
    result = dict(environment)
    result.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_EXTERNAL_DIFF": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return result


def _git_command(git: str, root: Path, *arguments: str) -> list[str]:
    return [
        git,
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-C",
        str(root),
        *arguments,
    ]


def _hash_bytes(raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def _snapshot_path(root: Path, relative: str) -> dict[str, Any]:
    path = root / Path(relative)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": relative, "kind": "absent"}
    except OSError as exc:
        raise SmokeError(f"could not snapshot relevant path {relative}: {exc}") from exc
    if _is_link_like(info):
        return {"path": relative, "kind": "link_or_reparse", "size": info.st_size}
    if stat.S_ISDIR(info.st_mode):
        try:
            entries = sorted(child.name for child in path.iterdir())
        except OSError as exc:
            raise SmokeError(f"could not list relevant directory {relative}: {exc}") from exc
        return {
            "path": relative,
            "kind": "directory",
            "entries": entries,
        }
    if not stat.S_ISREG(info.st_mode):
        return {"path": relative, "kind": "special", "mode": info.st_mode}
    digest = _hash_regular_file(
        path,
        max_bytes=MAX_RELEVANT_FILE_BYTES,
        label=f"relevant file {relative}",
    )
    return {
        "path": relative,
        "kind": "regular_file",
        "bytes": digest["size"],
        "sha256": digest["sha256"],
    }


def _git_snapshot(
    runner: Runner,
    *,
    git: str,
    root: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    git_env = _git_environment(environment)

    def run(*args: str, label: str, allowed: tuple[int, ...] = (0,)) -> CommandResult:
        result = runner(
            _git_command(git, root, *args),
            cwd=None,
            environment=git_env,
            timeout_seconds=COMMAND_TIMEOUT_SECONDS,
            output_limit_bytes=DEFAULT_OUTPUT_LIMIT_BYTES,
            input_bytes=None,
        )
        _require_complete(result, label=label)
        if result.returncode not in allowed:
            raise SmokeError(f"{label} failed with exit code {result.returncode}")
        return result

    head = _decode_utf8(
        run("rev-parse", "HEAD", label="Git HEAD snapshot").stdout,
        label="Git HEAD",
    ).strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", head) is None:
        raise SmokeError("Git HEAD snapshot was not a commit object ID")
    branch_result = run(
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        label="Git branch snapshot",
        allowed=(0, 1),
    )
    branch = (
        _decode_utf8(branch_result.stdout, label="Git branch").strip()
        if branch_result.returncode == 0
        else None
    )
    status = run(
        "status",
        "--porcelain=v2",
        "-z",
        "--untracked-files=all",
        "--ignored=no",
        label="Git status snapshot",
    ).stdout
    diff = run(
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
        label="Git worktree diff snapshot",
    ).stdout
    index = run("ls-files", "-s", "-z", label="Git index snapshot").stdout
    relevant = [_snapshot_path(root, relative) for relative in _RELEVANT_PATHS]
    components = {
        "head": head,
        "branch": branch,
        "status": _hash_bytes(status),
        "diff_from_head": _hash_bytes(diff),
        "index": _hash_bytes(index),
        "relevant_paths": relevant,
    }
    canonical = json.dumps(
        components,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {**components, "identity_sha256": hashlib.sha256(canonical).hexdigest()}


def _snapshot_empty_execution_root(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if len(rows) >= 256:
            raise SmokeError("isolated execution root contains too many entries")
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if _is_link_like(info):
            rows.append({"path": relative, "kind": "link_or_reparse"})
        elif stat.S_ISDIR(info.st_mode):
            rows.append({"path": relative, "kind": "directory"})
        elif stat.S_ISREG(info.st_mode):
            digest = _hash_regular_file(
                path,
                max_bytes=MAX_RELEVANT_FILE_BYTES,
                label="isolated execution file",
            )
            rows.append(
                {
                    "path": relative,
                    "kind": "regular_file",
                    "bytes": digest["size"],
                    "sha256": digest["sha256"],
                }
            )
        else:
            rows.append({"path": relative, "kind": "special"})
    encoded = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode()
    return {"entries": rows, "identity_sha256": hashlib.sha256(encoded).hexdigest()}


def _sanitize_environment(environment: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    sanitized: dict[str, str] = {}
    removed: list[str] = []
    for name, value in environment.items():
        if _SECRET_ENV_RE.search(name) or name.upper() in {"PYTHONHOME", "PYTHONPATH"}:
            removed.append(name)
        else:
            sanitized[name] = value
    sanitized.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "NO_COLOR": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )
    return sanitized, sorted(removed, key=str.casefold)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _prompt(root: Path) -> str:
    root_json = json.dumps(str(root), ensure_ascii=False)
    return (
        "This is a deterministic, read-only functional integration smoke. "
        "Call exactly one tool: the ZeroRun MCP server's doctor tool, once, with "
        f"the arguments {{\"root\":{root_json}}}. Do not call shell, file, browser, "
        "web, plan, collaboration, or any other tool. Do not call doctor a second "
        "time, including after an error. An error is final fail-closed evidence. "
        f"After that one result, make no further tool calls and reply with exactly "
        f"{FINAL_MARKER} and nothing else."
    )


def _codex_command(codex: Path, zerorun: Path, *, root: Path) -> list[str]:
    command = [
        str(codex),
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--json",
    ]
    for feature in (
        "apps",
        "browser_use",
        "computer_use",
        "hooks",
        "image_generation",
        "in_app_browser",
        "js_repl",
        "multi_agent",
        "plugins",
        "shell_tool",
        "unified_exec",
        "workspace_dependencies",
    ):
        command.extend(["--disable", feature])
    overrides = (
        f"mcp_servers.zerorun.command={_toml_string(str(zerorun))}",
        'mcp_servers.zerorun.args=["mcp-server"]',
        f"mcp_servers.zerorun.cwd={_toml_string(str(root))}",
        "mcp_servers.zerorun.enabled=true",
        "mcp_servers.zerorun.required=true",
        'mcp_servers.zerorun.enabled_tools=["doctor"]',
        "mcp_servers.zerorun.disabled_tools=[]",
        "mcp_servers.zerorun.startup_timeout_sec=15",
        "mcp_servers.zerorun.tool_timeout_sec=30",
        'mcp_servers.zerorun.default_tools_approval_mode="auto"',
        'mcp_servers.zerorun.tools.doctor.approval_mode="auto"',
        "mcp_servers.zerorun.tools.doctor.output_token_limit=4096",
    )
    for override in overrides:
        command.extend(["--config", override])
    command.append("-")
    return command


def _parse_jsonl(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        raise SmokeError("Codex JSONL stream was empty")
    if raw.startswith(_TRUNCATION_MARKER):
        raise SmokeError("Codex JSONL stream was truncated")
    if not raw.endswith(b"\n"):
        raise SmokeError("Codex JSONL stream ended without a newline")
    lines = raw.splitlines()
    if not lines or len(lines) > MAX_JSONL_LINES:
        raise SmokeError("Codex JSONL stream has an invalid event count")
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        if not line or len(line) > MAX_JSONL_LINE_BYTES:
            raise SmokeError(f"Codex JSONL line {index} has an invalid size")
        event = _load_object(line, label=f"Codex JSONL line {index}")
        event_type = event.get("type")
        if event_type not in _EVENT_TYPES:
            raise SmokeError(f"Codex JSONL line {index} has unknown event type {event_type!r}")
        events.append(event)
    return events


def _same_root(value: object, root: Path) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        candidate = Path(value).expanduser().resolve(strict=True)
    except OSError:
        return False
    return os.path.normcase(str(candidate)) == os.path.normcase(str(root))


def _doctor_payload(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    status = item.get("status")
    result = item.get("result")
    if status == "failed":
        if not isinstance(item.get("error"), dict):
            raise SmokeError("failed doctor call did not preserve an error object")
        return {}, True
    if status != "completed" or not isinstance(result, dict):
        raise SmokeError("doctor call did not complete with a result")
    structured = result.get("structured_content")
    if not isinstance(structured, dict):
        raise SmokeError("doctor result has no structured_content object")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise SmokeError("doctor result did not contain exactly one content block")
    block = content[0]
    if not isinstance(block, dict) or block.get("type") != "text":
        raise SmokeError("doctor result content was not one text block")
    text = block.get("text")
    if not isinstance(text, str):
        raise SmokeError("doctor result text was invalid")
    text_payload = _load_object(text.encode("utf-8"), label="doctor text result")
    if text_payload != structured:
        raise SmokeError("doctor text and structured results disagree")
    ok = structured.get("ok")
    if not isinstance(ok, bool):
        raise SmokeError("doctor result has no boolean ok state")
    return structured, not ok


def _validate_doctor_state(payload: dict[str, Any], *, root: Path) -> None:
    if payload.get("manifest_present") is not True or payload.get("manifest_version") != 2:
        raise SmokeError("doctor did not report a present version-2 manifest")
    if payload.get("mcp_manifest_supported") is not True:
        raise SmokeError("doctor did not report MCP support for manifest v2")
    if not _same_root(payload.get("root"), root):
        raise SmokeError("doctor result was not bound to the target repository")
    mode = payload.get("mode")
    if mode not in {"observe-only", "task-reuse", "pytest-node-reuse"}:
        raise SmokeError("doctor returned an unknown mode")
    values = {
        name: payload.get(name)
        for name in ("reuse_ready", "task_reuse_ready", "pytest_reuse_ready")
    }
    if not all(isinstance(value, bool) for value in values.values()):
        raise SmokeError("doctor reuse readiness fields were not boolean")
    reuse = bool(values["reuse_ready"])
    task = bool(values["task_reuse_ready"])
    pytest = bool(values["pytest_reuse_ready"])
    expected_mode = "pytest-node-reuse" if pytest else "task-reuse" if task else "observe-only"
    if mode != expected_mode or reuse != (task or pytest):
        raise SmokeError("doctor mode and reuse readiness fields are incoherent")


def _analyze_events(events: list[dict[str, Any]], *, root: Path) -> dict[str, Any]:
    counts: dict[str, int] = {}
    mcp_by_id: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    final_messages: list[str] = []
    forbidden: list[dict[str, str]] = []
    for event in events:
        event_type = str(event["type"])
        counts[event_type] = counts.get(event_type, 0) + 1
        if event_type == "error":
            forbidden.append({"event": event_type, "item_type": "error"})
            continue
        if not event_type.startswith("item."):
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            raise SmokeError(f"{event_type} did not contain an item object")
        item_id = item.get("id")
        item_type = item.get("type")
        if not isinstance(item_id, str) or not item_id or not isinstance(item_type, str):
            raise SmokeError(f"{event_type} contained an invalid item identity")
        if item_type == "mcp_tool_call":
            mcp_by_id.setdefault(item_id, []).append((event_type, item))
        elif item_type in _SAFE_ITEM_TYPES:
            if event_type != "item.completed":
                raise SmokeError(f"non-tool item {item_type!r} was not terminal")
            if item_type == "agent_message":
                text = item.get("text")
                if not isinstance(text, str):
                    raise SmokeError("agent message text was invalid")
                final_messages.append(text)
        else:
            forbidden.append({"event": event_type, "item_type": item_type})
    if forbidden:
        names = ", ".join(f"{row['event']}:{row['item_type']}" for row in forbidden)
        raise SmokeError(f"Codex emitted forbidden tool or item events: {names}")
    if counts.get("thread.started") != 1 or counts.get("turn.started") != 1:
        raise SmokeError("Codex JSONL did not contain one thread and one turn start")
    terminal_count = sum(counts.get(name, 0) for name in _TERMINAL_EVENT_TYPES)
    if terminal_count != 1 or counts.get("turn.completed") != 1:
        raise SmokeError("Codex turn did not complete exactly once")
    if len(mcp_by_id) != 1:
        raise SmokeError(f"Codex emitted {len(mcp_by_id)} distinct MCP calls, expected one")
    call_id, lifecycle = next(iter(mcp_by_id.items()))
    event_names = [event_type for event_type, _item in lifecycle]
    if event_names.count("item.started") != 1 or event_names.count("item.completed") != 1:
        raise SmokeError("doctor MCP call did not have one start and one completion")
    if any(name == "item.updated" for name in event_names):
        raise SmokeError("doctor MCP call had an unexpected update event")
    started = next(item for name, item in lifecycle if name == "item.started")
    completed = next(item for name, item in lifecycle if name == "item.completed")
    for item in (started, completed):
        if item.get("server") != "zerorun" or item.get("tool") != "doctor":
            raise SmokeError("the only MCP call was not zerorun/doctor")
        arguments = item.get("arguments")
        if not isinstance(arguments, dict) or set(arguments) != {"root"}:
            raise SmokeError("doctor arguments were not exactly {'root': ...}")
        if not _same_root(arguments.get("root"), root):
            raise SmokeError("doctor was called for a different repository")
    if started.get("status") != "in_progress":
        raise SmokeError("doctor start event was not in_progress")
    payload, server_is_error = _doctor_payload(completed)
    if payload:
        _validate_doctor_state(payload, root=root)
    if final_messages != [FINAL_MARKER]:
        raise SmokeError("Codex final message did not exactly match the smoke marker")
    return {
        "event_counts": dict(sorted(counts.items())),
        "mcp_call_count": 1,
        "call_id": call_id,
        "server": "zerorun",
        "tool": "doctor",
        "status": completed.get("status"),
        "server_is_error": server_is_error,
        "is_error_source": (
            "failed Codex MCP item status"
            if completed.get("status") == "failed"
            else "ZeroRun doctor ok=false contract"
            if server_is_error
            else "ZeroRun doctor ok=true contract"
        ),
        "error": completed.get("error"),
        "doctor": payload or None,
        "forbidden_item_count": 0,
        "command_execution_count": 0,
        "file_change_count": 0,
        "final_message": FINAL_MARKER,
    }


def _encoded_stream(result: CommandResult) -> dict[str, Any]:
    return {
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "stdout_bytes": len(result.stdout),
        "stderr_bytes": len(result.stderr),
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
        "stdout_base64": base64.b64encode(result.stdout).decode("ascii"),
        "stderr_base64": base64.b64encode(result.stderr).decode("ascii"),
    }


def _failure(receipt: dict[str, Any], *, phase: str, exc: BaseException) -> None:
    receipt["status"] = "fail_closed"
    receipt["functional_integration_pass"] = False
    receipt["failure"] = {
        "phase": phase,
        "type": type(exc).__name__,
        "message": str(exc)[:2000],
    }


def run_smoke(
    root: Path,
    *,
    approve_remote_metadata: bool = False,
    codex_command: str | None = None,
    git_command: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    work_root: Path | None = None,
    runner: Runner = _default_runner,
    clock: Clock = _utc_now,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    started_wall = time.perf_counter()
    started = clock().astimezone(timezone.utc)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "artifact_role": "functional_commercial_integration_smoke_only",
        "claim_scope": (
            "One read-only Codex-to-ZeroRun doctor call; no agent-effect, user, "
            "performance, market-demand, or commercial-viability claim."
        ),
        "started_at_utc": started.isoformat(),
        "status": "fail_closed",
        "evidence_valid": False,
        "functional_integration_pass": False,
        "upstream_repository_code_executed": False,
        "documentation": {
            "codex_noninteractive": OFFICIAL_NONINTERACTIVE_DOC,
            "codex_mcp": OFFICIAL_MCP_DOC,
        },
        "limits": {
            "timeout_seconds": timeout_seconds,
            "output_limit_bytes_per_stream": output_limit_bytes,
            "max_jsonl_lines": MAX_JSONL_LINES,
            "max_jsonl_line_bytes": MAX_JSONL_LINE_BYTES,
        },
    }
    phase = "input_validation"
    source_before: dict[str, Any] | None = None
    source_after: dict[str, Any] | None = None
    codex_result: CommandResult | None = None
    try:
        if approve_remote_metadata is not True:
            raise SmokeError(
                "live Codex execution requires --approve-remote-metadata because "
                "the repository path and ZeroRun doctor result are sent to the "
                "configured Codex service"
            )
        receipt["remote_metadata_disclosure"] = {
            "approved": True,
            "scope": ["absolute repository path", "ZeroRun doctor result"],
            "repository_file_contents_requested": False,
        }
        if not (30.0 <= timeout_seconds <= 600.0):
            raise SmokeError("timeout_seconds must be within [30, 600]")
        if not (64 * 1024 <= output_limit_bytes <= 8 * 1024 * 1024):
            raise SmokeError("output_limit_bytes must be within [65536, 8388608]")
        lexical_root = _absolute_lexical(root)
        try:
            canonical_root = lexical_root.resolve(strict=True)
        except OSError as exc:
            raise SmokeError(f"repository root is unavailable: {lexical_root}") from exc
        if lexical_root != canonical_root or not canonical_root.is_dir():
            raise SmokeError("repository root must be a real, non-linked directory")
        base_environment, removed_env = _sanitize_environment(
            dict(os.environ if environment is None else environment)
        )
        git_discovered = git_command or shutil.which("git")
        if not git_discovered:
            raise SmokeError("git executable was not found")
        git = str(_absolute_lexical(Path(git_discovered)).resolve(strict=True))
        root_result = _run_small(
            runner,
            _git_command(git, canonical_root, "rev-parse", "--show-toplevel"),
            label="Git repository-root inspection",
            cwd=None,
            environment=_git_environment(base_environment),
        )
        reported_root = Path(
            _decode_utf8(root_result.stdout, label="Git repository root").strip()
        ).resolve(strict=True)
        if os.path.normcase(str(reported_root)) != os.path.normcase(str(canonical_root)):
            raise SmokeError("--root must name the exact Git worktree root")

        phase = "executable_identity"
        codex_lexical, codex_resolved = _resolve_executable(
            codex_command,
            name="codex",
            repository_root=canonical_root,
        )
        codex_identity_before = _hash_regular_file(
            codex_resolved,
            max_bytes=MAX_EXECUTABLE_BYTES,
            label="Codex executable",
        )
        codex_version = _version(
            runner,
            codex_lexical,
            label="Codex",
            environment=base_environment,
        )

        phase = "registration_identity"
        registration, zerorun_lexical, zerorun_resolved = _registration(
            runner,
            codex_lexical,
            root=canonical_root,
            environment=base_environment,
        )
        zerorun_identity_before = _hash_regular_file(
            zerorun_resolved,
            max_bytes=MAX_EXECUTABLE_BYTES,
            label="registered ZeroRun executable",
        )
        zerorun_version = _version(
            runner,
            zerorun_lexical,
            label="ZeroRun",
            environment=base_environment,
        )
        if zerorun_version["raw"].split(maxsplit=1)[0].casefold() != "zerorun":
            raise SmokeError("registered MCP command did not identify as ZeroRun")
        if zerorun_version["version"] != SOURCE_ZERORUN_VERSION:
            raise SmokeError(
                "registered ZeroRun version does not match the source checkout "
                f"({zerorun_version['version']} != {SOURCE_ZERORUN_VERSION})"
            )
        receipt["repository"] = {"root": str(canonical_root)}
        receipt["codex"] = {
            "launcher": str(codex_lexical),
            "resolved_executable": codex_identity_before,
            **codex_version,
        }
        receipt["zerorun"] = {
            "source_version": SOURCE_ZERORUN_VERSION,
            "registered_launcher": str(zerorun_lexical),
            "resolved_executable": zerorun_identity_before,
            **zerorun_version,
        }
        receipt["registration"] = {
            "name": registration.get("name"),
            "enabled": registration.get("enabled"),
            "transport": {
                "type": registration["transport"].get("type"),
                "command": registration["transport"].get("command"),
                "args": registration["transport"].get("args"),
                "cwd": registration["transport"].get("cwd"),
                "environment_forwarding": False,
            },
            "enabled_tools": registration.get("enabled_tools"),
            "disabled_tools": registration.get("disabled_tools"),
            "inspection_sha256": hashlib.sha256(
                json.dumps(
                    registration,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "isolated_invocation_reuses_registered_executable": True,
        }
        receipt["environment"] = {
            "removed_sensitive_variable_names": removed_env,
            "secret_values_recorded": False,
        }

        phase = "pre_execution_snapshot"
        source_before = _git_snapshot(
            runner,
            git=git,
            root=canonical_root,
            environment=base_environment,
        )
        receipt["workspace"] = {"before": source_before}

        temporary_parent = None
        if work_root is not None:
            temporary_parent = _absolute_lexical(work_root).resolve(strict=True)
            if not temporary_parent.is_dir():
                raise SmokeError("work_root is not a directory")
        phase = "codex_exec"
        temporary_base = temporary_parent or Path(tempfile.gettempdir())
        with private_temporary_directory(
            temporary_base, prefix="zerorun-codex-agent-smoke-"
        ) as temporary:
            isolated_root = temporary.resolve(strict=True)
            isolated_before = _snapshot_empty_execution_root(isolated_root)
            if isolated_before["entries"]:
                raise SmokeError("isolated execution root was not empty")
            prompt = _prompt(canonical_root)
            command = _codex_command(
                codex_lexical,
                zerorun_resolved,
                root=canonical_root,
            )
            receipt["invocation"] = {
                "command": command,
                "prompt_utf8_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "prompt": prompt,
                "prompt_transport": "stdin",
                "execution_root": str(isolated_root),
                "ephemeral": True,
                "ignore_user_config": True,
                "ignore_rules": True,
                "sandbox": "read-only",
                "required_mcp_server": "zerorun",
                "enabled_mcp_tools": ["doctor"],
            }
            codex_result = runner(
                command,
                cwd=isolated_root,
                environment=base_environment,
                timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
                input_bytes=prompt.encode("utf-8"),
            )
            receipt["codex_jsonl"] = _encoded_stream(codex_result)
            isolated_after = _snapshot_empty_execution_root(isolated_root)
            isolated_unchanged = isolated_before == isolated_after
            receipt["isolated_execution_root"] = {
                "before": isolated_before,
                "after": isolated_after,
                "unchanged": isolated_unchanged,
            }

        phase = "post_execution_snapshot"
        source_after = _git_snapshot(
            runner,
            git=git,
            root=canonical_root,
            environment=base_environment,
        )
        receipt["workspace"]["after"] = source_after
        workspace_unchanged = source_before == source_after
        receipt["workspace"]["unchanged_before_receipt"] = workspace_unchanged
        codex_identity_after = _hash_regular_file(
            codex_resolved,
            max_bytes=MAX_EXECUTABLE_BYTES,
            label="Codex executable after smoke",
        )
        zerorun_identity_after = _hash_regular_file(
            zerorun_resolved,
            max_bytes=MAX_EXECUTABLE_BYTES,
            label="registered ZeroRun executable after smoke",
        )
        receipt["executable_identity_unchanged"] = {
            "codex": codex_identity_before == codex_identity_after,
            "zerorun": zerorun_identity_before == zerorun_identity_after,
        }

        phase = "evidence_validation"
        assert codex_result is not None
        _require_complete(codex_result, label="Codex agent integration smoke")
        if codex_result.returncode != 0:
            raise SmokeError(
                f"Codex agent integration smoke exited {codex_result.returncode}"
            )
        if not receipt["isolated_execution_root"]["unchanged"]:
            raise SmokeError("Codex changed the isolated execution root")
        if not workspace_unchanged:
            raise SmokeError("source/worktree identity changed during the smoke")
        if not all(receipt["executable_identity_unchanged"].values()):
            raise SmokeError("Codex or ZeroRun executable identity changed during the smoke")
        events = _parse_jsonl(codex_result.stdout)
        analysis = _analyze_events(events, root=canonical_root)
        receipt["event_analysis"] = analysis
        receipt["evidence_valid"] = True
        if analysis["server_is_error"]:
            receipt["status"] = "fail_closed"
            receipt["functional_integration_pass"] = False
            receipt["failure"] = {
                "phase": "doctor",
                "type": "ZeroRunDoctorError",
                "message": "ZeroRun doctor returned an adverse/isError-equivalent result; no retry was attempted",
            }
        else:
            receipt["status"] = "pass"
            receipt["functional_integration_pass"] = True
    except (OSError, SmokeError, ValueError) as exc:
        if source_before is not None and source_after is None:
            try:
                source_after = _git_snapshot(
                    runner,
                    git=locals().get("git", "git"),
                    root=locals().get("canonical_root", _absolute_lexical(root)),
                    environment=locals().get("base_environment", dict(os.environ)),
                )
                receipt.setdefault("workspace", {"before": source_before})["after"] = source_after
                receipt["workspace"]["unchanged_before_receipt"] = source_before == source_after
            except Exception as snapshot_exc:  # preserve both bounded failures
                receipt.setdefault("secondary_failures", []).append(
                    {
                        "phase": "post_failure_snapshot",
                        "type": type(snapshot_exc).__name__,
                        "message": str(snapshot_exc)[:1000],
                    }
                )
        _failure(receipt, phase=phase, exc=exc)
    completed = clock().astimezone(timezone.utc)
    receipt["completed_at_utc"] = completed.isoformat()
    receipt["wall_ms"] = round((time.perf_counter() - started_wall) * 1000.0, 3)
    return receipt


def _validate_output_destination(output: Path) -> tuple[Path, Path]:
    lexical = _absolute_lexical(output)
    if lexical.exists() or lexical.is_symlink():
        raise SmokeError(f"receipt already exists; refusing overwrite: {lexical}")
    parent = lexical.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise SmokeError(f"receipt parent is unavailable: {parent}: {exc}") from exc
    if _is_link_like(parent_info) or not stat.S_ISDIR(parent_info.st_mode):
        raise SmokeError("receipt parent must be a regular, non-linked directory")
    if parent.resolve(strict=True) != parent:
        raise SmokeError("receipt parent path is redirected")
    return lexical, parent


def write_receipt_atomic(output: Path, receipt: dict[str, Any]) -> None:
    destination, parent = _validate_output_destination(output)
    try:
        payload = (
            json.dumps(
                receipt,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SmokeError(f"receipt is not canonical JSON: {exc}") from exc
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".zerorun-codex-smoke.",
        suffix=".tmp",
        dir=parent,
    )
    temporary = Path(temporary_name)
    linked = False
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = os.write(descriptor, view[offset : offset + 1024 * 1024])
            if written <= 0:
                raise SmokeError("receipt staging write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.link(temporary, destination, follow_symlinks=False)
        linked = True
        published = destination.lstat()
        staged = temporary.lstat()
        if (
            _is_link_like(published)
            or not stat.S_ISREG(published.st_mode)
            or (published.st_dev, published.st_ino) != (staged.st_dev, staged.st_ino)
        ):
            raise SmokeError("receipt publication identity could not be verified")
        if os.name != "nt":
            directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except FileExistsError as exc:
        raise SmokeError(f"receipt appeared concurrently; refusing overwrite: {destination}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        if not linked and destination.exists():
            # A destination created by another process is never ours to remove.
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex-command")
    parser.add_argument("--git-command")
    parser.add_argument("--work-root", type=Path)
    parser.add_argument(
        "--approve-remote-metadata",
        action="store_true",
        help=(
            "confirm that the absolute repository path and ZeroRun doctor result "
            "may be sent to the configured Codex service"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--output-limit-bytes",
        type=int,
        default=DEFAULT_OUTPUT_LIMIT_BYTES,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _validate_output_destination(args.output)
        receipt = run_smoke(
            args.root,
            approve_remote_metadata=args.approve_remote_metadata,
            codex_command=args.codex_command,
            git_command=args.git_command,
            timeout_seconds=args.timeout_seconds,
            output_limit_bytes=args.output_limit_bytes,
            work_root=args.work_root,
        )
        write_receipt_atomic(args.output, receipt)
    except (OSError, SmokeError, ValueError) as exc:
        print(f"Codex agent integration smoke could not publish evidence: {exc}", file=os.sys.stderr)
        return 2
    summary = {
        "schema": receipt["schema"],
        "status": receipt["status"],
        "evidence_valid": receipt["evidence_valid"],
        "functional_integration_pass": receipt["functional_integration_pass"],
        "receipt": str(_absolute_lexical(args.output)),
    }
    print(json.dumps(summary, allow_nan=False, indent=2, sort_keys=True))
    return 0 if receipt["functional_integration_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
