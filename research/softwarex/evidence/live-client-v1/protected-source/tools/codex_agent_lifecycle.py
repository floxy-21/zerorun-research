#!/usr/bin/env python3
"""Prove one bounded Codex-to-ZeroRun lifecycle on a synthetic v2 task.

The harness creates and reviews its own deterministic fixture, stores authority
under an isolated external trust root, and requires the digest-pinned runtime to
already exist.  It never authorizes a caller-supplied repository.  The result is
formative functional evidence only, not a performance or commercial claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import aggregate_codex_install_evidence as codex_install_evidence
from tools import codex_agent_integration_smoke as smoke
from tools import install_verified_codex_cli as codex_installer
from zerorun import __version__ as SOURCE_ZERORUN_VERSION
from zerorun.path_safety import private_temporary_directory


SCHEMA = "zerorun.codex-agent-synthetic-lifecycle/v1"
TASK_NAME = "synthetic-lifecycle"
FIXTURE_DATA = "zerorun synthetic lifecycle fixture v1\n"
FIXTURE_PROGRAM = (
    "from pathlib import Path\n"
    f"expected = {FIXTURE_DATA!r}\n"
    "actual = Path('fixture.txt').read_text(encoding='utf-8')\n"
    "if actual != expected:\n"
    "    raise SystemExit(73)\n"
)
DOCTOR_MARKER = "ZERORUN_SYNTHETIC_DOCTOR_COMPLETE"
MISS_MARKER = "ZERORUN_SYNTHETIC_MISS_COMPLETE"
HIT_MARKER = "ZERORUN_SYNTHETIC_HIT_COMPLETE"
VERIFY_MARKER = "ZERORUN_SYNTHETIC_VERIFY_COMPLETE"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_OUTPUT_LIMIT_BYTES = 2 * 1024 * 1024
MAX_TREE_FILES = 2048
MAX_TREE_BYTES = 128 * 1024 * 1024
MAX_PACKAGE_PROBE_BYTES = 256 * 1024
_IMAGE_RE = re.compile(r"^[A-Za-z0-9._:/-]+@sha256:[0-9a-f]{64}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ITEM_TYPES = {"reasoning", "agent_message"}

CommandResult = smoke.CommandResult
Runner = smoke.Runner
Clock = smoke.Clock
SmokeError = smoke.SmokeError


class AgentStageError(SmokeError):
    """A Codex turn failed validation after bounded evidence was captured."""

    def __init__(self, message: str, record: dict[str, Any]) -> None:
        super().__init__(message)
        self.record = record


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_real_directory(path: Path, *, label: str) -> Path:
    lexical = smoke._absolute_lexical(path)
    try:
        info = lexical.lstat()
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise SmokeError(f"{label} is unavailable: {lexical}: {exc}") from exc
    if smoke._is_link_like(info) or not stat.S_ISDIR(info.st_mode) or lexical != resolved:
        raise SmokeError(f"{label} must be a real, non-linked directory")
    return resolved


def _regular_tree_identity(
    root: Path,
    *,
    label: str,
    ignore_runtime_artifacts: bool = False,
) -> dict[str, Any]:
    """Hash a bounded tree without following links or accepting special files."""

    root = _validate_real_directory(root, label=label)
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda value: value.name.casefold())
        except OSError as exc:
            raise SmokeError(f"could not enumerate {label}: {exc}") from exc
        for child in children:
            relative = child.relative_to(root).as_posix()
            info = child.lstat()
            if smoke._is_link_like(info):
                raise SmokeError(f"{label} contains a link or reparse point: {relative}")
            if stat.S_ISDIR(info.st_mode):
                if ignore_runtime_artifacts and child.name == "__pycache__":
                    continue
                stack.append(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SmokeError(f"{label} contains a special file: {relative}")
            if ignore_runtime_artifacts and child.suffix in {".pyc", ".pyo"}:
                continue
            if len(rows) >= MAX_TREE_FILES:
                raise SmokeError(f"{label} exceeds the {MAX_TREE_FILES}-file limit")
            total_bytes += info.st_size
            if total_bytes > MAX_TREE_BYTES:
                raise SmokeError(f"{label} exceeds the {MAX_TREE_BYTES}-byte limit")
            digest = smoke._hash_regular_file(
                child,
                max_bytes=MAX_TREE_BYTES,
                label=f"{label} file {relative}",
            )
            rows.append(
                {
                    "path": relative,
                    "bytes": digest["size"],
                    "sha256": digest["sha256"],
                }
            )
    rows.sort(key=lambda row: row["path"])
    return {
        "file_count": len(rows),
        "bytes": total_bytes,
        "files": rows,
        "identity_sha256": _canonical_sha256(rows),
    }


def _optional_tree_identity(root: Path, *, label: str) -> dict[str, Any]:
    if not root.exists():
        return {"present": False, "file_count": 0, "bytes": 0, "identity_sha256": None}
    value = _regular_tree_identity(root, label=label)
    return {"present": True, **value}


def _authority_tree_identity(root: Path) -> dict[str, Any]:
    """Snapshot authority structure without hashing or recording the secret key."""

    if not root.exists():
        return {
            "present": False,
            "file_count": 0,
            "bytes": 0,
            "structure_identity_sha256": None,
        }
    root = _validate_real_directory(root, label="isolated external authority")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    stack = [root]
    while stack:
        directory = stack.pop()
        for child in sorted(directory.iterdir(), key=lambda value: value.name.casefold()):
            relative = child.relative_to(root).as_posix()
            info = child.lstat()
            if smoke._is_link_like(info):
                raise SmokeError(
                    f"isolated external authority contains a link or reparse point: {relative}"
                )
            if stat.S_ISDIR(info.st_mode):
                stack.append(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise SmokeError(
                    f"isolated external authority contains a special file: {relative}"
                )
            if len(rows) >= MAX_TREE_FILES:
                raise SmokeError("isolated external authority exceeds its file limit")
            total_bytes += info.st_size
            if total_bytes > MAX_TREE_BYTES:
                raise SmokeError("isolated external authority exceeds its byte limit")
            row: dict[str, Any] = {"path": relative, "bytes": info.st_size}
            if relative == "authority.key":
                row["secret_content_recorded"] = False
            else:
                digest = smoke._hash_regular_file(
                    child,
                    max_bytes=MAX_TREE_BYTES,
                    label=f"external authority receipt {relative}",
                )
                row["sha256"] = digest["sha256"]
            rows.append(row)
    rows.sort(key=lambda row: row["path"])
    if sum(row["path"] == "authority.key" for row in rows) != 1:
        raise SmokeError("isolated external authority has no unique authority.key")
    return {
        "present": True,
        "file_count": len(rows),
        "bytes": total_bytes,
        "files": rows,
        "structure_identity_sha256": _canonical_sha256(rows),
    }


def _source_package_identity(source_root: Path) -> dict[str, Any]:
    package = source_root / "zerorun"
    identity = _regular_tree_identity(
        package,
        label="ZeroRun source package",
        ignore_runtime_artifacts=True,
    )
    if not identity["files"]:
        raise SmokeError("ZeroRun source package is empty")
    return {"root": str(package), **identity}


def _python_candidate(
    zerorun: Path,
    supplied: str | None,
    source_root: Path,
) -> tuple[Path, Path]:
    if supplied:
        lexical, resolved = smoke._resolve_executable(
            supplied,
            name="ZeroRun Python",
            repository_root=source_root,
        )
        return lexical, resolved
    suffixes = ("python.exe", "python3.exe") if os.name == "nt" else ("python", "python3")
    for name in suffixes:
        candidate = zerorun.parent / name
        if candidate.is_file():
            lexical, resolved = smoke._resolve_executable(
                str(candidate),
                name="ZeroRun Python",
                repository_root=source_root,
            )
            return lexical, resolved
    raise SmokeError(
        "could not identify the Python interpreter for the installed ZeroRun; "
        "pass --zerorun-python-command"
    )


def _installed_package_identity(
    runner: Runner,
    *,
    zerorun: Path,
    python_command: str | None,
    source_root: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    interpreter, interpreter_resolved = _python_candidate(
        zerorun,
        python_command,
        source_root,
    )
    probe = (
        "import importlib.metadata as m,json,platform,sys,zerorun;"
        "d=m.distribution('zerorun');"
        "print(json.dumps({'distribution_version':d.version,"
        "'module_version':zerorun.__version__,'module_file':zerorun.__file__,"
        "'distribution_path':str(getattr(d,'_path','')),"
        "'python_executable':sys.executable,'python_version':platform.python_version(),"
        "'python_implementation':platform.python_implementation(),"
        "'python_cache_tag':getattr(sys.implementation,'cache_tag',None)},"
        "sort_keys=True,separators=(',',':')))"
    )
    result = runner(
        [str(interpreter), "-I", "-c", probe],
        cwd=None,
        environment=environment,
        timeout_seconds=smoke.COMMAND_TIMEOUT_SECONDS,
        output_limit_bytes=MAX_PACKAGE_PROBE_BYTES,
        input_bytes=None,
    )
    smoke._require_complete(result, label="installed ZeroRun package probe")
    if result.returncode != 0:
        raise SmokeError("installed ZeroRun package probe failed")
    payload = smoke._load_object(result.stdout, label="installed ZeroRun package probe")
    if set(payload) != {
        "distribution_version",
        "module_version",
        "module_file",
        "distribution_path",
        "python_executable",
        "python_version",
        "python_implementation",
        "python_cache_tag",
    }:
        raise SmokeError("installed ZeroRun package probe returned unexpected fields")
    if payload["distribution_version"] != payload["module_version"]:
        raise SmokeError("installed ZeroRun distribution and module versions disagree")
    if payload["distribution_version"] != SOURCE_ZERORUN_VERSION:
        raise SmokeError("installed ZeroRun package version does not match the source checkout")
    try:
        module_file = smoke._absolute_lexical(Path(payload["module_file"])).resolve(strict=True)
        distribution_path = smoke._absolute_lexical(
            Path(payload["distribution_path"])
        ).resolve(strict=True)
        reported_python = smoke._absolute_lexical(
            Path(payload["python_executable"])
        ).resolve(strict=True)
    except (OSError, TypeError) as exc:
        raise SmokeError("installed ZeroRun package probe returned invalid paths") from exc
    if reported_python.resolve(strict=True) != interpreter_resolved:
        raise SmokeError("ZeroRun package probe executed through a different Python interpreter")
    module_root = module_file.parent
    if module_root.name != "zerorun" or smoke._within(module_root, source_root):
        raise SmokeError("registered ZeroRun must resolve to an external installed package")
    if not distribution_path.name.casefold().endswith(".dist-info"):
        raise SmokeError("installed ZeroRun distribution metadata path is invalid")
    if smoke._within(distribution_path, source_root):
        raise SmokeError("installed ZeroRun distribution metadata is repository-controlled")
    module_identity = _regular_tree_identity(
        module_root,
        label="installed ZeroRun module",
        ignore_runtime_artifacts=True,
    )
    distribution_identity = _regular_tree_identity(
        distribution_path,
        label="installed ZeroRun distribution metadata",
        ignore_runtime_artifacts=True,
    )
    interpreter_identity = smoke._hash_regular_file(
        interpreter_resolved,
        max_bytes=smoke.MAX_EXECUTABLE_BYTES,
        label="ZeroRun Python interpreter",
    )
    return {
        "version": payload["distribution_version"],
        "module_root": str(module_root),
        "module": module_identity,
        "distribution_metadata_root": str(distribution_path),
        "distribution_metadata": distribution_identity,
        "python": {
            "launcher": str(interpreter),
            "executable": interpreter_identity,
            "version": payload["python_version"],
            "implementation": payload["python_implementation"],
            "cache_tag": payload["python_cache_tag"],
        },
        "package_identity_sha256": _canonical_sha256(
            {
                "version": payload["distribution_version"],
                "module": module_identity["identity_sha256"],
                "distribution_metadata": distribution_identity["identity_sha256"],
                "python": interpreter_identity["sha256"],
            }
        ),
    }


def _validated_codex_installation(
    *,
    codex_resolved: Path,
    install_receipt: Path,
    expected_source_commit: str,
    version: str,
    main_integrity: str,
    platform_integrity: str,
) -> dict[str, Any]:
    """Bind a live Codex tree to one authenticated installer receipt."""

    try:
        raw, value = codex_install_evidence._load_receipt(install_receipt)
    except codex_install_evidence.EvidenceError as exc:
        raise SmokeError(f"Codex install receipt failed validation: {exc}") from exc
    evidence_id = value.get("evidence_id") if isinstance(value, dict) else None
    if not isinstance(evidence_id, str):
        raise SmokeError("Codex install receipt has no evidence ID")
    try:
        validated = codex_install_evidence._validate_loaded_receipt(
            install_receipt,
            raw=raw,
            value=value,
            expected_id=evidence_id,
            expected_source_commit=expected_source_commit,
            version=version,
            main_integrity=main_integrity,
            platform_integrity=platform_integrity,
        )
    except codex_install_evidence.EvidenceError as exc:
        raise SmokeError(f"Codex install receipt failed validation: {exc}") from exc
    if codex_resolved.name != "codex.js" or codex_resolved.parent.name != "bin":
        raise SmokeError("Codex executable is not the authenticated package launcher")
    try:
        package_root = codex_resolved.parent.parent
        installed_tree = codex_installer._tree_manifest(package_root)
        launcher_identity = smoke._hash_regular_file(
            codex_resolved,
            max_bytes=smoke.MAX_EXECUTABLE_BYTES,
            label="Codex authenticated launcher",
        )
        native = package_root / "vendor/x86_64-unknown-linux-musl/bin/codex"
        native_identity = smoke._hash_regular_file(
            native,
            max_bytes=smoke.MAX_EXECUTABLE_BYTES,
            label="Codex native executable",
        )
        runtime_now = {"node": codex_installer._runtime_file_identity("node")}
    except (smoke.SmokeError, codex_installer.InstallError) as exc:
        raise SmokeError(str(exc)) from exc
    runtime_recorded = {
        name: {
            key: item
            for key, item in validated[name].items()
            if key != "reported_version"
        }
        for name in ("node",)
    }
    live = {
        "package_tree_sha256": codex_installer._canonical_sha256(installed_tree),
        "launcher_sha256": launcher_identity["sha256"],
        "native_sha256": native_identity["sha256"],
    }
    expected = {
        key: validated[key]
        for key in ("installed_tree_sha256", "launcher_sha256", "native_sha256")
    }
    if (
        live["package_tree_sha256"] != expected["installed_tree_sha256"]
        or live["launcher_sha256"] != expected["launcher_sha256"]
        or live["native_sha256"] != expected["native_sha256"]
        or runtime_now != runtime_recorded
    ):
        raise SmokeError("live Codex package, launcher, native, or Node identity drifted")
    return {
        "receipt": str(smoke._absolute_lexical(install_receipt)),
        "receipt_sha256": validated["receipt_sha256"],
        "evidence_id": evidence_id,
        "source_commit": expected_source_commit,
        "codex_version": version,
        **live,
        "node_sha256": runtime_now["node"]["resolved_sha256"],
    }


def _manifest_bytes(runtime_image: str) -> bytes:
    payload = {
        "version": 2,
        "tasks": {
            TASK_NAME: {
                "command": ["python", "fixture.py"],
                "inputs": ["fixture.py", "fixture.txt"],
                "input_symbols": [],
                "outputs": [],
                "env": [],
                "cacheable": True,
                "unsafe_effects": [],
                "cache_streams": False,
                "result_only": True,
                "closure_reviewed": True,
                "image": runtime_image,
                "platform": "linux/amd64",
            }
        },
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _run_git(
    runner: Runner,
    git: str,
    root: Path,
    environment: dict[str, str],
    *arguments: str,
    label: str,
) -> CommandResult:
    return smoke._run_small(
        runner,
        smoke._git_command(git, root, *arguments),
        label=label,
        cwd=None,
        environment=smoke._git_environment(environment),
    )


def _create_synthetic_repository(
    runner: Runner,
    *,
    root: Path,
    runtime_image: str,
    git: str,
    environment: dict[str, str],
) -> dict[str, Any]:
    root.mkdir()
    manifest = _manifest_bytes(runtime_image)
    (root / ".zerorun.json").write_bytes(manifest)
    (root / "fixture.py").write_text(FIXTURE_PROGRAM, encoding="utf-8", newline="\n")
    (root / "fixture.txt").write_text(FIXTURE_DATA, encoding="utf-8", newline="\n")
    git_env = dict(environment)
    git_env.update(
        {
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
            "TZ": "UTC",
        }
    )
    _run_git(runner, git, root, git_env, "init", "-q", label="synthetic Git init")
    _run_git(
        runner,
        git,
        root,
        git_env,
        "add",
        "--",
        ".zerorun.json",
        "fixture.py",
        "fixture.txt",
        label="synthetic Git add",
    )
    _run_git(
        runner,
        git,
        root,
        git_env,
        "-c",
        "user.name=ZeroRun Synthetic Harness",
        "-c",
        "user.email=synthetic-harness@example.invalid",
        "commit",
        "-q",
        "-m",
        "deterministic synthetic lifecycle fixture",
        label="synthetic Git commit",
    )
    return _synthetic_source_identity(
        runner,
        git=git,
        root=root,
        environment=environment,
        runtime_image=runtime_image,
    )


def _synthetic_source_identity(
    runner: Runner,
    *,
    git: str,
    root: Path,
    environment: dict[str, str],
    runtime_image: str,
) -> dict[str, Any]:
    expected_names = {".git", ".zerorun", ".zerorun.json", "fixture.py", "fixture.txt"}
    present = {path.name for path in root.iterdir()}
    unexpected = sorted(present - expected_names)
    if unexpected:
        raise SmokeError(f"synthetic repository contains unexpected top-level paths: {unexpected}")
    reviewed: list[dict[str, Any]] = []
    for name in (".zerorun.json", "fixture.py", "fixture.txt"):
        reviewed.append(
            smoke._hash_regular_file(
                root / name,
                max_bytes=smoke.MAX_RELEVANT_FILE_BYTES,
                label=f"synthetic reviewed file {name}",
            )
        )
        reviewed[-1]["path"] = name
    expected = {
        ".zerorun.json": _manifest_bytes(runtime_image),
        "fixture.py": FIXTURE_PROGRAM.encode("utf-8"),
        "fixture.txt": FIXTURE_DATA.encode("utf-8"),
    }
    for name, raw in expected.items():
        if (root / name).read_bytes() != raw:
            raise SmokeError(f"synthetic reviewed file drifted from the built-in fixture: {name}")
    head = smoke._decode_utf8(
        _run_git(
            runner,
            git,
            root,
            environment,
            "rev-parse",
            "HEAD",
            label="synthetic Git HEAD",
        ).stdout,
        label="synthetic Git HEAD",
    ).strip()
    if re.fullmatch(r"[0-9a-fA-F]{40,64}", head) is None:
        raise SmokeError("synthetic Git HEAD is invalid")
    diff = _run_git(
        runner,
        git,
        root,
        environment,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-textconv",
        "HEAD",
        "--",
        ".zerorun.json",
        "fixture.py",
        "fixture.txt",
        label="synthetic tracked-source diff",
    ).stdout
    if diff:
        raise SmokeError("synthetic reviewed source differs from its committed tree")
    index = _run_git(
        runner,
        git,
        root,
        environment,
        "ls-files",
        "-s",
        "-z",
        "--",
        ".zerorun.json",
        "fixture.py",
        "fixture.txt",
        label="synthetic Git index",
    ).stdout
    value = {
        "head": head,
        "reviewed_files": reviewed,
        "reviewed_files_identity_sha256": _canonical_sha256(reviewed),
        "index_sha256": hashlib.sha256(index).hexdigest(),
    }
    return {**value, "identity_sha256": _canonical_sha256(value)}


def _prompt(tool: str, root: Path, *, verify: bool | None, marker: str) -> str:
    root_json = json.dumps(str(root), ensure_ascii=False)
    if tool == "doctor":
        arguments = f'{{"root":{root_json}}}'
    else:
        arguments = (
            f'{{"task":{json.dumps(TASK_NAME)},"root":{root_json},'
            f'"verify":{str(bool(verify)).lower()}}}'
        )
    return (
        "This is an explicitly authorized formative test on a synthetic repository. "
        f"Call exactly one tool: the ZeroRun MCP server's {tool} tool, once, with "
        f"the exact arguments {arguments}. Do not call shell, file, browser, web, "
        "plan, collaboration, or any other tool. Do not retry or make another call "
        "after success or error; an error is final fail-closed evidence. After the "
        f"single result, reply with exactly {marker} and nothing else."
    )


def _codex_command(codex: Path, zerorun: Path, *, root: Path, tool: str) -> list[str]:
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
        f"mcp_servers.zerorun.command={smoke._toml_string(str(zerorun))}",
        'mcp_servers.zerorun.args=["mcp-server"]',
        f"mcp_servers.zerorun.cwd={smoke._toml_string(str(root))}",
        "mcp_servers.zerorun.enabled=true",
        "mcp_servers.zerorun.required=true",
        f'mcp_servers.zerorun.enabled_tools=["{tool}"]',
        "mcp_servers.zerorun.disabled_tools=[]",
        "mcp_servers.zerorun.startup_timeout_sec=15",
        "mcp_servers.zerorun.tool_timeout_sec=180",
        'mcp_servers.zerorun.default_tools_approval_mode="auto"',
        f'mcp_servers.zerorun.tools.{tool}.approval_mode="auto"',
        f"mcp_servers.zerorun.tools.{tool}.output_token_limit=4096",
    )
    for override in overrides:
        command.extend(["--config", override])
    command.append("-")
    return command


def _mcp_payload(item: dict[str, Any], *, tool: str) -> dict[str, Any]:
    if item.get("status") != "completed" or item.get("error") is not None:
        raise SmokeError(f"{tool} MCP call did not complete successfully")
    result = item.get("result")
    if not isinstance(result, dict):
        raise SmokeError(f"{tool} MCP call has no result object")
    structured = result.get("structured_content")
    if not isinstance(structured, dict):
        raise SmokeError(f"{tool} MCP result has no structured_content object")
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise SmokeError(f"{tool} MCP result did not contain exactly one content block")
    block = content[0]
    if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(
        block.get("text"), str
    ):
        raise SmokeError(f"{tool} MCP result content is invalid")
    text = smoke._load_object(block["text"].encode("utf-8"), label=f"{tool} text result")
    if text != structured:
        raise SmokeError(f"{tool} text and structured results disagree")
    if result.get("is_error", False) is not False or result.get("isError", False) is not False:
        raise SmokeError(f"{tool} MCP result reported is_error")
    return structured


def _analyze_single_call(
    events: list[dict[str, Any]],
    *,
    tool: str,
    expected_arguments: dict[str, Any],
    marker: str,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    calls: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    messages: list[str] = []
    forbidden: list[str] = []
    for event in events:
        event_type = event["type"]
        counts[event_type] = counts.get(event_type, 0) + 1
        if event_type == "error":
            forbidden.append("error")
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
            calls.setdefault(item_id, []).append((event_type, item))
        elif item_type in _SAFE_ITEM_TYPES:
            if event_type != "item.completed":
                raise SmokeError(f"non-tool item {item_type!r} was not terminal")
            if item_type == "agent_message":
                if not isinstance(item.get("text"), str):
                    raise SmokeError("agent message text was invalid")
                messages.append(item["text"])
        else:
            forbidden.append(f"{event_type}:{item_type}")
    if forbidden:
        raise SmokeError("Codex emitted forbidden events: " + ", ".join(forbidden))
    if counts.get("thread.started") != 1 or counts.get("turn.started") != 1:
        raise SmokeError("Codex JSONL did not contain one thread and one turn start")
    if counts.get("turn.completed") != 1 or counts.get("turn.failed", 0) != 0:
        raise SmokeError("Codex turn did not complete exactly once")
    if len(calls) != 1:
        raise SmokeError(f"Codex emitted {len(calls)} distinct MCP calls, expected one")
    call_id, lifecycle = next(iter(calls.items()))
    names = [name for name, _item in lifecycle]
    if names.count("item.started") != 1 or names.count("item.completed") != 1:
        raise SmokeError(f"{tool} MCP call did not have one start and one completion")
    if "item.updated" in names or len(names) != 2:
        raise SmokeError(f"{tool} MCP call had an unexpected update or duplicate event")
    started = next(item for name, item in lifecycle if name == "item.started")
    completed = next(item for name, item in lifecycle if name == "item.completed")
    for item in (started, completed):
        if item.get("server") != "zerorun" or item.get("tool") != tool:
            raise SmokeError(f"the only MCP call was not zerorun/{tool}")
        if item.get("arguments") != expected_arguments:
            raise SmokeError(f"{tool} arguments did not exactly match the sealed request")
    if started.get("status") != "in_progress":
        raise SmokeError(f"{tool} start event was not in_progress")
    if messages != [marker]:
        raise SmokeError("Codex final message did not exactly match the stage marker")
    payload = _mcp_payload(completed, tool=tool)
    return {
        "event_counts": dict(sorted(counts.items())),
        "mcp_call_count": 1,
        "call_id": call_id,
        "tool": tool,
        "arguments": expected_arguments,
        "response": payload,
        "response_sha256": _canonical_sha256(payload),
        "command_execution_count": 0,
        "file_change_count": 0,
        "retry_count": 0,
        "final_message": marker,
    }


def _validate_doctor(payload: dict[str, Any], *, root: Path) -> str:
    if payload.get("ok") is not True:
        raise SmokeError("synthetic doctor did not report ok=true")
    if payload.get("manifest_present") is not True or payload.get("manifest_version") != 2:
        raise SmokeError("synthetic doctor did not report manifest v2")
    if payload.get("manifest_authorized") is not True:
        raise SmokeError("synthetic doctor did not confirm external authority")
    if payload.get("reuse_ready") is not True or payload.get("task_reuse_ready") is not True:
        raise SmokeError("synthetic doctor did not confirm task reuse readiness")
    if payload.get("mode") != "task-reuse" or not smoke._same_root(payload.get("root"), root):
        raise SmokeError("synthetic doctor was not bound to the authorized repository")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise SmokeError("synthetic doctor did not report exactly one task")
    task = tasks[0]
    if (
        task.get("task") != TASK_NAME
        or task.get("status") != "CACHEABLE"
        or task.get("reason") is not None
    ):
        raise SmokeError("synthetic doctor did not report the sealed task as CACHEABLE")
    key = task.get("cache_key")
    if not isinstance(key, str) or _HEX_64_RE.fullmatch(key) is None:
        raise SmokeError("synthetic doctor returned an invalid cache key")
    return key


def _validate_run(payload: dict[str, Any], *, status: str) -> str:
    if payload.get("task") != TASK_NAME or payload.get("status") != status:
        raise SmokeError(f"synthetic lifecycle expected {status}")
    if payload.get("exit_code") != 0 or payload.get("mode") != "reuse":
        raise SmokeError(f"synthetic lifecycle {status} result was not successful reuse mode")
    if payload.get("restored_outputs") != []:
        raise SmokeError("synthetic result-only task unexpectedly restored outputs")
    if payload.get("stdout_tail") != "" or payload.get("stderr_tail") != "":
        raise SmokeError("synthetic silent task unexpectedly emitted output")
    expected_verified = status == "VERIFY_MATCH"
    if payload.get("verified") is not expected_verified:
        raise SmokeError(f"synthetic lifecycle {status} returned an incoherent verified flag")
    key = payload.get("cache_key")
    if not isinstance(key, str) or _HEX_64_RE.fullmatch(key) is None:
        raise SmokeError(f"synthetic lifecycle {status} returned an invalid cache key")
    return key


def _host_environment_identity(environment: dict[str, str]) -> dict[str, Any]:
    values = [[name, environment[name]] for name in sorted(environment, key=str.casefold)]
    get_encoding = getattr(locale, "getencoding", None)
    locale_encoding = (
        get_encoding()
        if get_encoding is not None
        else locale.getpreferredencoding(False)
    )
    return {
        "os_name": os.name,
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_build": list(platform.python_build()),
        "python_executable": str(Path(sys.executable).resolve()),
        "locale_encoding": locale_encoding,
        "sanitized_variable_names": [row[0] for row in values],
        "sanitized_environment_sha256": _canonical_sha256(values),
        "secret_values_recorded": False,
    }


def _record_failure(receipt: dict[str, Any], *, phase: str, exc: BaseException) -> None:
    receipt["status"] = "fail_closed"
    receipt["functional_lifecycle_pass"] = False
    receipt["failure"] = {
        "phase": phase,
        "type": type(exc).__name__,
        "message": str(exc)[:2000],
    }


def _run_agent_stage(
    runner: Runner,
    *,
    codex: Path,
    zerorun: Path,
    repository: Path,
    execution_parent: Path,
    environment: dict[str, str],
    tool: str,
    verify: bool | None,
    marker: str,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> dict[str, Any]:
    expected_arguments = {"root": str(repository)}
    if tool == "run_tests":
        expected_arguments = {
            "task": TASK_NAME,
            "root": str(repository),
            "verify": bool(verify),
        }
    prompt = _prompt(tool, repository, verify=verify, marker=marker)
    command = _codex_command(codex, zerorun, root=repository, tool=tool)
    with private_temporary_directory(
        execution_parent, prefix=f"zerorun-codex-{tool}-"
    ) as raw:
        execution_root = raw.resolve(strict=True)
        before = smoke._snapshot_empty_execution_root(execution_root)
        if before["entries"]:
            raise SmokeError("isolated Codex execution root was not empty")
        result = runner(
            command,
            cwd=execution_root,
            environment=environment,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            input_bytes=prompt.encode("utf-8"),
        )
        after = smoke._snapshot_empty_execution_root(execution_root)
    record: dict[str, Any] = {
        "tool": tool,
        "verify": verify,
        "prompt": prompt,
        "prompt_utf8_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "command": command,
        "execution_root_unchanged": before == after,
        "jsonl": smoke._encoded_stream(result),
    }
    try:
        smoke._require_complete(result, label=f"Codex {tool} stage")
        if result.returncode != 0:
            raise SmokeError(f"Codex {tool} stage exited {result.returncode}")
        if before != after:
            raise SmokeError(f"Codex {tool} stage changed its isolated execution root")
        events = smoke._parse_jsonl(result.stdout)
        record["analysis"] = _analyze_single_call(
            events,
            tool=tool,
            expected_arguments=expected_arguments,
            marker=marker,
        )
    except (OSError, SmokeError, ValueError, TypeError) as exc:
        record["validation_error"] = {
            "type": type(exc).__name__,
            "message": str(exc)[:2000],
        }
        raise AgentStageError(str(exc), record) from exc
    return record


def run_synthetic_lifecycle(
    source_root: Path,
    *,
    runtime_image: str,
    approve_remote_metadata: bool = False,
    approve_synthetic_formative_authority: bool = False,
    codex_command: str | None = None,
    codex_install_receipt: Path | None = None,
    codex_main_integrity: str | None = None,
    codex_platform_integrity: str | None = None,
    expected_main_commit: str | None = None,
    zerorun_python_command: str | None = None,
    git_command: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    work_root: Path | None = None,
    runner: Runner = smoke._default_runner,
    clock: Clock = smoke._utc_now,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    started_wall = time.perf_counter()
    started = clock().astimezone(timezone.utc)
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "artifact_role": "formative_synthetic_functional_integration_only",
        "claim_scope": (
            "One synthetic Codex-to-ZeroRun MISS_EXECUTED, HIT_REUSED, and "
            "VERIFY_MATCH lifecycle; no performance, user, market-demand, "
            "commercial-viability, or arbitrary-repository claim."
        ),
        "started_at_utc": started.isoformat(),
        "status": "fail_closed",
        "evidence_valid": False,
        "functional_lifecycle_pass": False,
        "real_repository_authorized": False,
        "synthetic_repository_code_executed": False,
        "runtime_acquisition_attempted": False,
        "agent_stages": [],
        "limits": {
            "timeout_seconds_per_agent_turn": timeout_seconds,
            "output_limit_bytes_per_stream": output_limit_bytes,
            "max_jsonl_lines": smoke.MAX_JSONL_LINES,
            "max_jsonl_line_bytes": smoke.MAX_JSONL_LINE_BYTES,
        },
        "design": {
            "logical_stages": 2,
            "doctor_agent_turns": 1,
            "lifecycle_agent_turns": 3,
            "one_mcp_call_per_agent_turn": True,
            "agent_retry_allowed": False,
            "residual_limitation": (
                "Prompting cannot force a stochastic agent to emit three safe calls in "
                "one turn. The lifecycle therefore uses three separately bounded, "
                "ephemeral single-call turns and rejects any deviation."
            ),
        },
        "documentation": {
            "codex_noninteractive": smoke.OFFICIAL_NONINTERACTIVE_DOC,
            "codex_mcp": smoke.OFFICIAL_MCP_DOC,
        },
    }
    phase = "input_validation"
    source_before: dict[str, Any] | None = None
    source_after: dict[str, Any] | None = None
    synthetic_before: dict[str, Any] | None = None
    synthetic_after: dict[str, Any] | None = None
    codex_installation_before: dict[str, Any] | None = None
    try:
        if approve_remote_metadata is not True:
            raise SmokeError(
                "live Codex execution requires --approve-remote-metadata because the "
                "synthetic repository path and structured ZeroRun results are sent to Codex"
            )
        if approve_synthetic_formative_authority is not True:
            raise SmokeError(
                "synthetic external authority requires the explicit "
                "--approve-synthetic-formative-authority acknowledgement"
            )
        if _IMAGE_RE.fullmatch(runtime_image) is None or runtime_image.endswith("0" * 64):
            raise SmokeError("runtime_image must be a non-placeholder digest-pinned OCI reference")
        if not (30.0 <= timeout_seconds <= 600.0):
            raise SmokeError("timeout_seconds must be within [30, 600]")
        if not (64 * 1024 <= output_limit_bytes <= 8 * 1024 * 1024):
            raise SmokeError("output_limit_bytes must be within [65536, 8388608]")
        install_values = (
            codex_install_receipt,
            codex_main_integrity,
            codex_platform_integrity,
        )
        if any(value is not None for value in install_values) and not all(
            value is not None for value in install_values
        ):
            raise SmokeError(
                "Codex install receipt and both frozen SRI values must be supplied together"
            )
        if codex_install_receipt is not None and expected_main_commit is None:
            raise SmokeError(
                "authenticated Codex install evidence requires --expected-main-commit"
            )
        if expected_main_commit is not None and _COMMIT_RE.fullmatch(expected_main_commit) is None:
            raise SmokeError("expected_main_commit must be an exact lowercase 40-hex commit")
        if codex_main_integrity is not None and codex_platform_integrity is not None:
            try:
                codex_installer._parse_sri(codex_main_integrity)
                codex_installer._parse_sri(codex_platform_integrity)
            except codex_installer.InstallError as exc:
                raise SmokeError(f"Codex package SRI is invalid: {exc}") from exc

        source_root = _validate_real_directory(source_root, label="ZeroRun source root")
        _validate_real_directory(source_root / "tools", label="ZeroRun source tools directory")
        harness_path = source_root / "tools" / "codex_agent_lifecycle.py"
        executed_harness = smoke._absolute_lexical(Path(__file__))
        if executed_harness != harness_path or executed_harness.resolve(
            strict=True
        ) != harness_path.resolve(strict=True):
            raise SmokeError(
                "source_root must contain the exact synthetic lifecycle harness being executed"
            )
        base_environment, removed_env = smoke._sanitize_environment(
            dict(os.environ if environment is None else environment)
        )
        git_discovered = git_command or shutil.which("git")
        if not git_discovered:
            raise SmokeError("git executable was not found")
        git = str(smoke._absolute_lexical(Path(git_discovered)).resolve(strict=True))
        reported_root = Path(
            smoke._decode_utf8(
                _run_git(
                    runner,
                    git,
                    source_root,
                    base_environment,
                    "rev-parse",
                    "--show-toplevel",
                    label="ZeroRun source Git root",
                ).stdout,
                label="ZeroRun source Git root",
            ).strip()
        ).resolve(strict=True)
        if os.path.normcase(str(reported_root)) != os.path.normcase(str(source_root)):
            raise SmokeError("source_root must name the exact ZeroRun Git worktree root")

        phase = "source_and_executable_identity"
        source_before = smoke._git_snapshot(
            runner,
            git=git,
            root=source_root,
            environment=base_environment,
        )
        if expected_main_commit is not None:
            try:
                main_commit = smoke._decode_utf8(
                    _run_git(
                        runner,
                        git,
                        source_root,
                        base_environment,
                        "rev-parse",
                        "refs/heads/main",
                        label="local main commit",
                    ).stdout,
                    label="local main commit",
                ).strip()
            except SmokeError as exc:
                raise SmokeError(
                    "exact-main lifecycle requires an available local main branch"
                ) from exc
            if (
                source_before["head"] != expected_main_commit
                or main_commit != expected_main_commit
                or source_before["branch"] != "main"
                or source_before["status"]["bytes"] != 0
                or source_before["diff_from_head"]["bytes"] != 0
            ):
                raise SmokeError(
                    "exact-main lifecycle requires clean main at the expected commit"
                )
            receipt["release_binding"] = {
                "requested": True,
                "expected_commit": expected_main_commit,
                "head_commit": source_before["head"],
                "local_main_commit": main_commit,
                "branch": "main",
                "worktree_clean": True,
            }
        else:
            receipt["release_binding"] = {
                "requested": False,
                "expected_commit": None,
                "worktree_clean_required": False,
            }
        source_package = _source_package_identity(source_root)
        harness_identity = smoke._hash_regular_file(
            harness_path,
            max_bytes=smoke.MAX_RELEVANT_FILE_BYTES,
            label="synthetic lifecycle harness",
        )
        codex_lexical, codex_resolved = smoke._resolve_executable(
            codex_command,
            name="codex",
            repository_root=source_root,
        )
        codex_identity_before = smoke._hash_regular_file(
            codex_resolved,
            max_bytes=smoke.MAX_EXECUTABLE_BYTES,
            label="Codex executable",
        )
        codex_version = smoke._version(
            runner,
            codex_lexical,
            label="Codex",
            environment=base_environment,
        )
        if codex_install_receipt is not None:
            assert expected_main_commit is not None
            assert codex_main_integrity is not None
            assert codex_platform_integrity is not None
            codex_installation_before = _validated_codex_installation(
                codex_resolved=codex_resolved,
                install_receipt=codex_install_receipt,
                expected_source_commit=expected_main_commit,
                version=codex_version["version"],
                main_integrity=codex_main_integrity,
                platform_integrity=codex_platform_integrity,
            )
        registration, zerorun_lexical, zerorun_resolved = smoke._registration(
            runner,
            codex_lexical,
            root=source_root,
            environment=base_environment,
        )
        zerorun_launcher_before = smoke._hash_regular_file(
            zerorun_resolved,
            max_bytes=smoke.MAX_EXECUTABLE_BYTES,
            label="registered ZeroRun executable",
        )
        zerorun_version = smoke._version(
            runner,
            zerorun_lexical,
            label="ZeroRun",
            environment=base_environment,
        )
        if zerorun_version["version"] != SOURCE_ZERORUN_VERSION:
            raise SmokeError("registered ZeroRun version does not match the source checkout")
        installed_before = _installed_package_identity(
            runner,
            zerorun=zerorun_resolved,
            python_command=zerorun_python_command,
            source_root=source_root,
            environment=base_environment,
        )
        if installed_before["module"]["identity_sha256"] != source_package["identity_sha256"]:
            raise SmokeError("installed ZeroRun package bytes do not match the source checkout")

        receipt["source"] = {
            "root": str(source_root),
            "git": source_before,
            "commit": source_before["head"],
            "package": source_package,
            "harness": harness_identity,
        }
        receipt["codex"] = {
            "launcher": str(codex_lexical),
            "resolved_executable": codex_identity_before,
            **codex_version,
            "package_identity_sha256": _canonical_sha256(
                {
                    "version": codex_version,
                    "executable_sha256": codex_identity_before["sha256"],
                }
            ),
        }
        if codex_installation_before is not None:
            receipt["codex"]["authenticated_installation"] = codex_installation_before
        receipt["zerorun"] = {
            "source_version": SOURCE_ZERORUN_VERSION,
            "registered_launcher": str(zerorun_lexical),
            "launcher": zerorun_launcher_before,
            "cli_version": zerorun_version,
            "installed_package": installed_before,
            "installed_matches_source_package": True,
        }
        receipt["registration"] = {
            "inspection_sha256": _canonical_sha256(registration),
            "name": registration.get("name"),
            "enabled": registration.get("enabled"),
            "isolated_invocation_reuses_registered_executable": True,
            "environment_forwarding_configured": False,
        }

        temporary_parent = None
        if work_root is not None:
            temporary_parent = _validate_real_directory(work_root, label="work_root")
            if smoke._within(temporary_parent, source_root) or smoke._within(
                source_root, temporary_parent
            ):
                raise SmokeError("work_root and source_root must be disjoint")

        phase = "synthetic_fixture"
        temporary_base = temporary_parent or Path(tempfile.gettempdir())
        with private_temporary_directory(
            temporary_base, prefix="zerorun-codex-synthetic-lifecycle-"
        ) as raw_temporary:
            temporary = raw_temporary.resolve(strict=True)
            if smoke._within(temporary, source_root) or smoke._within(source_root, temporary):
                raise SmokeError("synthetic workspace and source_root must be disjoint")
            repository = temporary / "repository"
            trust_root = temporary / "external-authority"
            execution_parent = temporary / "agent-turns"
            execution_parent.mkdir()
            synthetic_before = _create_synthetic_repository(
                runner,
                root=repository,
                runtime_image=runtime_image,
                git=git,
                environment=base_environment,
            )
            manifest_digest = hashlib.sha256((repository / ".zerorun.json").read_bytes()).hexdigest()
            if synthetic_before["reviewed_files"][0]["sha256"] != manifest_digest:
                raise SmokeError("independent synthetic manifest digests disagree")
            trust_before = _authority_tree_identity(trust_root)
            lifecycle_environment = dict(base_environment)
            lifecycle_environment["ZERORUN_TRUST_ROOT"] = str(trust_root)
            receipt["environment"] = {
                **_host_environment_identity(lifecycle_environment),
                "removed_sensitive_variable_names": removed_env,
                "remote_metadata_disclosure": {
                    "approved": True,
                    "scope": [
                        "temporary synthetic repository path",
                        "structured ZeroRun doctor and run_tests results",
                    ],
                    "repository_file_contents_requested": False,
                },
            }
            receipt["synthetic_repository"] = {
                "ephemeral": True,
                "path": str(repository),
                "runtime_image": runtime_image,
                "manifest_sha256": manifest_digest,
                "fixture_identity": synthetic_before,
                "closure_review": {
                    "built_in_fixture_only": True,
                    "reviewed_inputs": ["fixture.py", "fixture.txt"],
                    "environment_forwarding": [],
                    "unsafe_effects": [],
                    "result_only": True,
                    "formative_authority_explicitly_approved": True,
                },
            }

            phase = "synthetic_external_authority"
            authorization = smoke._run_small(
                runner,
                [
                    str(zerorun_resolved),
                    "--manifest",
                    str(repository / ".zerorun.json"),
                    "--json",
                    "authorize",
                    "--manifest-sha256",
                    manifest_digest,
                ],
                label="synthetic exact-manifest external authorization",
                cwd=execution_parent,
                environment=lifecycle_environment,
            )
            authorization_payload = smoke._load_object(
                authorization.stdout,
                label="synthetic authorization response",
            )
            if (
                authorization_payload.get("status") != "AUTHORIZED"
                or authorization_payload.get("manifest_sha256") != manifest_digest
                or authorization_payload.get("authority_location") != "external-per-user"
                or authorization_payload.get("pytest_profile_sha256") is not None
            ):
                raise SmokeError("synthetic authorization response did not match the exact manifest")
            trust_after = _authority_tree_identity(trust_root)
            if trust_before["present"] or not trust_after["present"] or trust_after["file_count"] < 2:
                raise SmokeError("isolated external authority was not created from an empty root")
            receipt["external_authority"] = {
                "synthetic_only": True,
                "outside_synthetic_repository": not smoke._within(trust_root, repository),
                "before": trust_before,
                "after": trust_after,
                "response": authorization_payload,
                "response_sha256": hashlib.sha256(authorization.stdout).hexdigest(),
                "secret_values_recorded": False,
            }

            phase = "agent_doctor"
            doctor = _run_agent_stage(
                runner,
                codex=codex_lexical,
                zerorun=zerorun_resolved,
                repository=repository,
                execution_parent=execution_parent,
                environment=lifecycle_environment,
                tool="doctor",
                verify=None,
                marker=DOCTOR_MARKER,
                timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes,
            )
            receipt["agent_stages"].append(doctor)
            doctor_key = _validate_doctor(doctor["analysis"]["response"], root=repository)

            run_specs = (
                ("agent_miss", False, MISS_MARKER, "MISS_EXECUTED"),
                ("agent_hit", False, HIT_MARKER, "HIT_REUSED"),
                ("agent_verify", True, VERIFY_MARKER, "VERIFY_MATCH"),
            )
            keys = [doctor_key]
            for phase_name, verify, marker, expected_status in run_specs:
                phase = phase_name
                stage = _run_agent_stage(
                    runner,
                    codex=codex_lexical,
                    zerorun=zerorun_resolved,
                    repository=repository,
                    execution_parent=execution_parent,
                    environment=lifecycle_environment,
                    tool="run_tests",
                    verify=verify,
                    marker=marker,
                    timeout_seconds=timeout_seconds,
                    output_limit_bytes=output_limit_bytes,
                )
                receipt["agent_stages"].append(stage)
                keys.append(_validate_run(stage["analysis"]["response"], status=expected_status))
                if expected_status == "MISS_EXECUTED":
                    receipt["synthetic_repository_code_executed"] = True
                current = _synthetic_source_identity(
                    runner,
                    git=git,
                    root=repository,
                    environment=lifecycle_environment,
                    runtime_image=runtime_image,
                )
                if current != synthetic_before:
                    raise SmokeError("synthetic reviewed source/worktree drifted during the lifecycle")
            if len(set(keys)) != 1:
                raise SmokeError("doctor and lifecycle calls did not bind the same cache key")
            receipt["lifecycle"] = {
                "statuses": [
                    stage["analysis"]["response"]["status"]
                    for stage in receipt["agent_stages"][1:]
                ],
                "cache_key": doctor_key,
                "exact_expected_sequence": True,
                "total_agent_turns": 4,
                "total_mcp_calls": 4,
                "total_retries": 0,
            }
            synthetic_after = _synthetic_source_identity(
                runner,
                git=git,
                root=repository,
                environment=lifecycle_environment,
                runtime_image=runtime_image,
            )
            receipt["synthetic_repository"]["unchanged_reviewed_source"] = (
                synthetic_before == synthetic_after
            )
            managed_state = repository / ".zerorun"
            receipt["synthetic_repository"]["managed_state"] = _optional_tree_identity(
                managed_state,
                label="synthetic ZeroRun managed state",
            )
            if not receipt["synthetic_repository"]["managed_state"]["present"]:
                raise SmokeError("synthetic lifecycle did not create ZeroRun managed state")

        phase = "post_execution_identity"
        source_after = smoke._git_snapshot(
            runner,
            git=git,
            root=source_root,
            environment=base_environment,
        )
        codex_identity_after = smoke._hash_regular_file(
            codex_resolved,
            max_bytes=smoke.MAX_EXECUTABLE_BYTES,
            label="Codex executable after lifecycle",
        )
        zerorun_launcher_after = smoke._hash_regular_file(
            zerorun_resolved,
            max_bytes=smoke.MAX_EXECUTABLE_BYTES,
            label="registered ZeroRun executable after lifecycle",
        )
        installed_after = _installed_package_identity(
            runner,
            zerorun=zerorun_resolved,
            python_command=zerorun_python_command,
            source_root=source_root,
            environment=base_environment,
        )
        codex_installation_after = None
        if codex_installation_before is not None:
            assert codex_install_receipt is not None
            assert expected_main_commit is not None
            assert codex_main_integrity is not None
            assert codex_platform_integrity is not None
            codex_installation_after = _validated_codex_installation(
                codex_resolved=codex_resolved,
                install_receipt=codex_install_receipt,
                expected_source_commit=expected_main_commit,
                version=codex_version["version"],
                main_integrity=codex_main_integrity,
                platform_integrity=codex_platform_integrity,
            )
        receipt["identity_unchanged"] = {
            "source_worktree": source_before == source_after,
            "codex_executable": codex_identity_before == codex_identity_after,
            "zerorun_launcher": zerorun_launcher_before == zerorun_launcher_after,
            "zerorun_installed_package": installed_before == installed_after,
            "synthetic_reviewed_source": synthetic_before == synthetic_after,
            "codex_authenticated_installation": (
                codex_installation_before == codex_installation_after
                if codex_installation_before is not None
                else True
            ),
        }
        if not all(receipt["identity_unchanged"].values()):
            raise SmokeError("source, worktree, executable, or installed-package identity drifted")
        receipt["source"]["git_after"] = source_after
        receipt["evidence_valid"] = True
        receipt["functional_lifecycle_pass"] = True
        receipt["status"] = "pass"
    except (OSError, SmokeError, ValueError, TypeError) as exc:
        if isinstance(exc, AgentStageError):
            receipt["agent_stages"].append(exc.record)
        _record_failure(receipt, phase=phase, exc=exc)
    completed = clock().astimezone(timezone.utc)
    receipt["completed_at_utc"] = completed.isoformat()
    receipt["wall_ms"] = round((time.perf_counter() - started_wall) * 1000.0, 3)
    receipt["evidence_payload_sha256"] = _canonical_sha256(receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--codex-command")
    parser.add_argument("--codex-install-receipt", type=Path)
    parser.add_argument("--codex-main-integrity")
    parser.add_argument("--codex-platform-integrity")
    parser.add_argument("--expected-main-commit")
    parser.add_argument("--zerorun-python-command")
    parser.add_argument("--git-command")
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--approve-remote-metadata", action="store_true")
    parser.add_argument("--approve-synthetic-formative-authority", action="store_true")
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
        smoke._validate_output_destination(args.output)
        receipt = run_synthetic_lifecycle(
            args.source_root,
            runtime_image=args.runtime_image,
            approve_remote_metadata=args.approve_remote_metadata,
            approve_synthetic_formative_authority=args.approve_synthetic_formative_authority,
            codex_command=args.codex_command,
            codex_install_receipt=args.codex_install_receipt,
            codex_main_integrity=args.codex_main_integrity,
            codex_platform_integrity=args.codex_platform_integrity,
            expected_main_commit=args.expected_main_commit,
            zerorun_python_command=args.zerorun_python_command,
            git_command=args.git_command,
            timeout_seconds=args.timeout_seconds,
            output_limit_bytes=args.output_limit_bytes,
            work_root=args.work_root,
        )
        smoke.write_receipt_atomic(args.output, receipt)
    except (OSError, SmokeError, ValueError) as exc:
        print(f"Codex synthetic lifecycle could not publish evidence: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema": receipt["schema"],
                "status": receipt["status"],
                "evidence_valid": receipt["evidence_valid"],
                "functional_lifecycle_pass": receipt["functional_lifecycle_pass"],
                "receipt": str(smoke._absolute_lexical(args.output)),
            },
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if receipt["functional_lifecycle_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
