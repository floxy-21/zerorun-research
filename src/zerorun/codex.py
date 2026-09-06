from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from .bounded_json import JsonLimits, loads_bounded_json
from .manifest import DEFAULT_MANIFEST, load_manifest
from .model import ConfigurationError
from .oci import _run_bounded_process, inspect_runtime
from .path_safety import is_link_like as _is_link_like
from .pytest_profile import DEFAULT_PYTEST_PROFILE, load_pytest_profile
from .trust import (
    manifest_is_authorized,
    manifest_sha256,
    pytest_profile_is_authorized,
    repository_marker_kind,
)

_CANDIDATE = ".zerorun-pytest.candidate.json"
_MANAGED_SKILL_MARKER = "<!-- zerorun-managed-skill:v1 -->"
_MAX_EXISTING_SKILL_BYTES = 1024 * 1024
_CODEX_COMMAND_TIMEOUT_SECONDS = 15.0
_CODEX_COMMAND_OUTPUT_LIMIT_BYTES = 1024 * 1024
_CODEX_REGISTRATION_JSON_LIMITS = JsonLimits(
    max_bytes=_CODEX_COMMAND_OUTPUT_LIMIT_BYTES,
    max_depth=32,
    max_values=10_000,
    max_object_members=2_000,
    max_structural_tokens=40_000,
    max_number_chars=256,
    max_string_chars=128 * 1024,
    max_total_string_chars=_CODEX_COMMAND_OUTPUT_LIMIT_BYTES,
)
_MCP_TRANSPORT_FIELDS = frozenset(
    {"type", "command", "args", "env", "env_vars", "cwd"}
)
_REQUIRED_MCP_TOOLS = frozenset(
    {
        "prepare_pytest",
        "run_pytest",
        "run_tests",
        "stats",
        "explain",
        "doctor",
        "list_tasks",
    }
)

_SKILL = """---
name: zerorun
description: Use ZeroRun for deterministic test loops. Prefer reviewed fine-grained pytest reuse when available; otherwise use configured hermetic task reuse or direct tests outside ZeroRun.
---

<!-- zerorun-managed-skill:v1 -->

# ZeroRun

Use ZeroRun for repository test loops without asking the user to manage cache commands manually.

- Check `doctor` when setup is unclear.
- If `.zerorun-pytest.json` is present and valid, use `run_pytest` for pytest loops. It performs per-node fail-closed reuse and executes unknown, changed, unsupported, or uncertain nodes fresh.
- If there is no reviewed pytest profile, explain that managed setup can create repository files, acquire a pinned runtime, and execute pytest collection. Ask for explicit approval, then call `prepare_pytest` once with `approve_setup=true`. It writes a non-authorizing `.zerorun-pytest.candidate.json` and never activates reuse.
- If managed setup is unsupported or cannot prove a working pinned runtime, keep using direct tests outside ZeroRun. The ZeroRun MCP server never executes repository code directly on the host.
- Never treat `.zerorun-pytest.candidate.json` as an active profile. Generated observation evidence requires explicit closure-completeness and node-independence review before promotion.
- Repository files and generated candidates are non-authorizing. After operator review, reuse requires external per-user authorization bound to the exact manifest/profile bytes; never create, edit, approve, activate, or self-authorize that authority on the model’s own initiative.
- If there is a reviewed version 2 `.zerorun.json` task with no host environment forwarding but no active pytest profile, use `run_tests` for that hermetic configured task. Legacy version 1 manifests and tasks declaring `env` are CLI-only and must never be executed through Codex/MCP. Normal MCP runs require the pinned image to be present already; only explicitly approved managed setup may acquire one.
- Without reviewed reuse configuration, report observation-only readiness and run any user-approved direct test through Codex's normal test tooling, not through ZeroRun MCP.
- Never turn a MISS, BYPASS, uncertainty, invalid configuration, race recovery, candidate, or observation-only result into reuse.
- Use `stats` for repository-local conservative verified time saved and reuse/fresh counts. Observation time is not time saved.
- After `run_pytest`, surface a compact ZeroRun result using repository-local fields: reused/fresh/unknown nodes, `verified_saved_seconds`, `saved_percent`, and `effective_speedup`.
- Use `stats` when the user asks how much ZeroRun has saved in the current repository or when a task summary would benefit from cumulative savings. Prefer `verified_saved_seconds`, `actual_seconds`, `conservative_no_zerorun_seconds`, `reuse_percent`, and `effective_speedup`.
- Report only metrics observed in the current repository, not ZeroRun's historical benchmark numbers.
"""

# A public marker is useful for humans, but it is not proof that ZeroRun owns a
# file.  Only byte-exact versions shipped by ZeroRun may be replaced during a
# future managed upgrade.  Add an old canonical payload here only while its
# corresponding upgrade path is supported.
_KNOWN_MANAGED_SKILLS = frozenset({_SKILL})


def _skill_path(root: Path) -> Path:
    return root / ".agents" / "skills" / "zerorun" / "SKILL.md"


def _legacy_skill_path(root: Path) -> Path:
    return root / ".codex" / "skills" / "zerorun" / "SKILL.md"


def _read_existing_skill(path: Path) -> str | None:
    if _is_link_like(path):
        raise ValueError("the Codex project skill is not a regular file")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"the existing Codex project skill is unreadable: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("the Codex project skill is not a regular file")
        if opened.st_size > _MAX_EXISTING_SKILL_BYTES:
            raise ValueError(
                "the existing Codex project skill exceeds the 1 MiB safety limit"
            )
        chunks: list[bytes] = []
        remaining = _MAX_EXISTING_SKILL_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_EXISTING_SKILL_BYTES:
            raise ValueError(
                "the existing Codex project skill exceeds the 1 MiB safety limit"
            )
        after_open = os.fstat(descriptor)
        # The path must still name the exact file we opened. This detects a
        # symlink or replacement race even where O_NOFOLLOW is unavailable.
        current = path.stat(follow_symlinks=False)
        if (
            len(payload) != opened.st_size
            or _is_link_like(path)
            or not stat.S_ISREG(current.st_mode)
            or (
                after_open.st_dev,
                after_open.st_ino,
                after_open.st_mode,
                after_open.st_size,
                after_open.st_mtime_ns,
                getattr(after_open, "st_ctime_ns", None),
            )
            != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
                getattr(opened, "st_ctime_ns", None),
            )
            or (
                current.st_dev,
                current.st_ino,
                current.st_mode,
                current.st_size,
                current.st_mtime_ns,
            )
            != (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
            )
        ):
            raise ValueError(
                "the existing Codex project skill changed while it was inspected"
            )
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("the existing Codex project skill is not valid UTF-8") from exc
    except OSError as exc:
        raise ValueError(f"the existing Codex project skill is unreadable: {exc}") from exc
    finally:
        os.close(descriptor)


def _create_project_skill(path: Path) -> None:
    """Create the managed skill without overwriting a concurrent file."""

    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o644)
    try:
        payload = _SKILL.encode("utf-8")
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("could not complete the managed skill write")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _skill_conflict(root: Path, path: Path, reason: str) -> dict[str, Any]:
    legacy_path = _legacy_skill_path(root)
    return {
        "path": str(path),
        "changed": False,
        "installed": False,
        "conflict": True,
        "legacy_path": str(legacy_path),
        "legacy_present": legacy_path.is_file(),
        "reason": reason,
    }


def _skill_parent_problem(candidates: tuple[Path, ...]) -> str | None:
    for candidate in candidates:
        try:
            if _is_link_like(candidate):
                return (
                    "the Codex project skill path contains a symbolic link or "
                    f"junction ({candidate}); refusing to write through it"
                )
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"could not safely inspect Codex project skill parent {candidate}: {exc}"
        if not stat.S_ISDIR(info.st_mode):
            return (
                "the Codex project skill path contains a non-directory component "
                f"({candidate}); refusing to replace it"
            )
    return None


def install_project_skill(root: Path) -> dict[str, Any]:
    path = _skill_path(root)
    managed_parents = (root / ".agents", root / ".agents" / "skills", path.parent)
    parent_problem = _skill_parent_problem(managed_parents)
    if parent_problem is not None:
        return _skill_conflict(root, path, parent_problem)
    if _is_link_like(path):
        return _skill_conflict(
            root,
            path,
            "the Codex project skill path contains a symbolic link or junction "
            f"({path}); refusing to write through it",
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _skill_conflict(
            root,
            path,
            f"could not safely create the Codex project skill directory: {exc}",
        )
    # Recheck every managed component after mkdir so an intermediate-file or
    # link replacement race becomes a structured refusal before opening the
    # managed file.
    parent_problem = _skill_parent_problem(managed_parents)
    if parent_problem is not None:
        return _skill_conflict(root, path, parent_problem)
    try:
        previous = _read_existing_skill(path)
    except ValueError as exc:
        return _skill_conflict(root, path, str(exc))
    legacy_path = _legacy_skill_path(root)
    legacy_present = legacy_path.is_file()
    if previous is not None and previous not in _KNOWN_MANAGED_SKILLS:
        return {
            "path": str(path),
            "changed": False,
            "installed": False,
            "conflict": True,
            "legacy_path": str(legacy_path),
            "legacy_present": legacy_present,
            "reason": (
                "a non-ZeroRun-managed skill already exists at the Codex project "
                "skill path; refusing to overwrite it"
            ),
        }
    changed = previous != _SKILL
    if changed:
        try:
            _create_project_skill(path)
        except FileExistsError:
            return {
                "path": str(path),
                "changed": False,
                "installed": False,
                "conflict": True,
                "legacy_path": str(legacy_path),
                "legacy_present": legacy_present,
                "reason": (
                    "the Codex project skill appeared while installation was in "
                    "progress; refusing to overwrite it"
                ),
            }
        except OSError as exc:
            return {
                "path": str(path),
                "changed": False,
                "installed": False,
                "conflict": True,
                "legacy_path": str(legacy_path),
                "legacy_present": legacy_present,
                "reason": f"could not safely create the Codex project skill: {exc}",
            }
    result: dict[str, Any] = {
        "path": str(path),
        "changed": changed,
        "installed": True,
        "conflict": False,
        "legacy_path": str(legacy_path),
        "legacy_present": legacy_present,
    }
    if legacy_present:
        result["warning"] = (
            "a legacy .codex/skills/zerorun skill is still present; review and "
            "remove it after confirming the .agents skill is discovered"
        )
    return result


def _is_compiled_executable() -> bool:
    """Return whether this module is running from a bundled executable."""

    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))


def _trusted_executable(
    discovered: str | Path,
    *,
    repository_root: Path | None,
    label: str,
) -> Path:
    lexical = Path(os.path.abspath(Path(discovered).expanduser()))
    resolved = lexical.resolve()
    if repository_root is not None:
        root = repository_root.expanduser().resolve()
        for candidate in (lexical, resolved):
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            raise ValueError(
                f"the discovered {label} executable is inside the target repository; "
                "refusing to execute a repository-controlled launcher"
            )
    return resolved


def _expected_mcp_command(
    *, repository_root: Path | None = None
) -> tuple[str, list[str]]:
    if _is_compiled_executable():
        executable = _trusted_executable(
            sys.executable,
            repository_root=repository_root,
            label="ZeroRun",
        )
        arguments = ["mcp-server"]
    else:
        discovered = shutil.which("zerorun")
        if discovered is None:
            raise ValueError(
                "a safe installed ZeroRun console entry point was not found on PATH; "
                "refusing a Python '-m zerorun' MCP registration because a repository "
                "could shadow that module"
            )
        executable = _trusted_executable(
            discovered,
            repository_root=repository_root,
            label="ZeroRun",
        )
        arguments = ["mcp-server"]
    return str(executable), arguments


def _parse_existing_server(stdout: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = loads_bounded_json(
            stdout.encode("utf-8"),
            label="Codex MCP registration",
            limits=_CODEX_REGISTRATION_JSON_LIMITS,
        )
    except (ConfigurationError, UnicodeEncodeError) as exc:
        return None, str(exc)
    if not isinstance(payload, dict):
        return None, "Codex MCP registration must be a JSON object"
    return payload, None


def _validate_existing_server(
    stdout: str,
    *,
    expected_command: str,
    expected_args: list[str],
    repository_root: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    payload, problem = _parse_existing_server(stdout)
    if payload is None:
        return None, problem
    if payload.get("name") != "zerorun":
        return None, "registration name is not exactly 'zerorun'"
    if (
        payload.get("enabled") is not True
        or payload.get("disabled_reason") is not None
    ):
        return None, "registration is disabled"
    transport = payload.get("transport")
    if not isinstance(transport, dict):
        return None, "registration transport is not an object"
    unexpected_transport = set(transport) - _MCP_TRANSPORT_FIELDS
    if unexpected_transport:
        return None, (
            "stdio transport contains unsupported fields: "
            + ", ".join(sorted(unexpected_transport))
        )
    if transport.get("type") != "stdio":
        return None, "registration transport is not stdio"
    command = transport.get("command")
    args = transport.get("args", [])
    if not isinstance(command, str) or not isinstance(args, list) or not all(
        isinstance(item, str) for item in args
    ):
        return None, "stdio command or arguments are malformed"
    if command != expected_command or args != expected_args:
        return (
            None,
            "stdio command or arguments do not match the installed ZeroRun launcher",
        )
    if transport.get("env") not in (None, {}):
        return None, "stdio registration forwards fixed environment values"
    if transport.get("env_vars") not in (None, []):
        return None, "stdio registration forwards host environment variables"
    configured_cwd = transport.get("cwd")
    if configured_cwd is not None:
        if not isinstance(configured_cwd, str) or not configured_cwd:
            return None, "stdio registration cwd is malformed"
        if repository_root is None:
            return None, "stdio registration is unexpectedly bound to a cwd"
        try:
            configured = Path(configured_cwd).expanduser().resolve(strict=True)
            expected_root = repository_root.expanduser().resolve(strict=True)
        except OSError:
            return None, "stdio registration cwd is unavailable"
        if os.path.normcase(str(configured)) != os.path.normcase(str(expected_root)):
            return None, "stdio registration is bound to another repository cwd"
    enabled_tools = payload.get("enabled_tools")
    if enabled_tools is not None:
        if not isinstance(enabled_tools, list) or not all(
            isinstance(item, str) for item in enabled_tools
        ):
            return None, "enabled_tools is malformed"
        if not _REQUIRED_MCP_TOOLS.issubset(enabled_tools):
            return None, "registration does not enable every required ZeroRun tool"
    disabled_tools = payload.get("disabled_tools")
    if disabled_tools is not None:
        if not isinstance(disabled_tools, list) or not all(
            isinstance(item, str) for item in disabled_tools
        ):
            return None, "disabled_tools is malformed"
        blocked = _REQUIRED_MCP_TOOLS.intersection(disabled_tools)
        if blocked:
            return None, "registration disables required ZeroRun tools: " + ", ".join(
                sorted(blocked)
            )
    return {"command": command, "args": list(args)}, None


def _run_codex_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed, timed_out = _run_bounded_process(
        command,
        cwd=None,
        environment=dict(os.environ),
        timeout_seconds=_CODEX_COMMAND_TIMEOUT_SECONDS,
        output_limit_bytes=_CODEX_COMMAND_OUTPUT_LIMIT_BYTES,
    )
    if timed_out:
        raise TimeoutError(
            f"Codex command exceeded {_CODEX_COMMAND_TIMEOUT_SECONDS:g} seconds"
        )
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )


def register_codex_mcp(*, repository_root: Path | None = None) -> dict[str, Any]:
    discovered_codex = shutil.which("codex")
    if discovered_codex is None:
        return {
            "available": False,
            "registered": False,
            "changed": False,
            "reason": "Codex CLI was not found on PATH",
        }
    try:
        codex_path = _trusted_executable(
            discovered_codex,
            repository_root=repository_root,
            label="Codex",
        )
    except ValueError as exc:
        return {
            "available": False,
            "registered": False,
            "changed": False,
            "reason": str(exc),
        }
    codex = str(codex_path)
    try:
        expected_command, expected_args = _expected_mcp_command(
            repository_root=repository_root
        )
    except ValueError as exc:
        return {
            "available": True,
            "registered": False,
            "changed": False,
            "reason": str(exc),
        }

    try:
        current = _run_codex_command(
            [codex, "mcp", "get", "zerorun", "--json"]
        )
    except (OSError, TimeoutError) as exc:
        return {
            "available": True,
            "registered": False,
            "changed": False,
            "reason": f"Codex MCP inspection failed safely: {exc}",
        }
    if current.returncode == 0:
        registration, problem = _validate_existing_server(
            current.stdout,
            expected_command=expected_command,
            expected_args=expected_args,
            repository_root=repository_root,
        )
        if registration is not None:
            return {
                "available": True,
                "registered": True,
                "changed": False,
                **registration,
            }
        return {
            "available": True,
            "registered": False,
            "changed": False,
            "reason": (
                "an MCP server named 'zerorun' already exists but is not a safe "
                f"exact ZeroRun stdio registration ({problem}); refusing to "
                "overwrite user configuration"
            ),
            "expected_command": expected_command,
            "expected_args": expected_args,
        }

    try:
        added = _run_codex_command(
            [codex, "mcp", "add", "zerorun", "--", expected_command, *expected_args]
        )
    except (OSError, TimeoutError) as exc:
        return {
            "available": True,
            "registered": False,
            "changed": False,
            "reason": f"Codex MCP registration failed safely: {exc}",
        }
    if added.returncode != 0:
        return {
            "available": True,
            "registered": False,
            "changed": False,
            "reason": (added.stderr or added.stdout or "codex mcp add failed").strip(),
        }
    try:
        verified = _run_codex_command(
            [codex, "mcp", "get", "zerorun", "--json"]
        )
    except (OSError, TimeoutError) as exc:
        return {
            "available": True,
            "registered": False,
            "changed": True,
            "reason": f"Codex MCP post-registration verification failed safely: {exc}",
        }
    if verified.returncode != 0:
        return {
            "available": True,
            "registered": False,
            "changed": True,
            "reason": (
                verified.stderr
                or verified.stdout
                or "codex mcp get failed after registration"
            ).strip(),
        }
    registration, problem = _validate_existing_server(
        verified.stdout,
        expected_command=expected_command,
        expected_args=expected_args,
        repository_root=repository_root,
    )
    if registration is None:
        return {
            "available": True,
            "registered": False,
            "changed": True,
            "reason": (
                "Codex reported success but post-registration verification did "
                f"not yield a safe exact ZeroRun stdio registration: {problem}"
            ),
        }
    return {
        "available": True,
        "registered": True,
        "changed": True,
        **registration,
    }


def _runtime_is_available(root: Path, task: Any) -> bool:
    try:
        inspect_runtime(task, allow_pull=False, repository_root=root)
    except Exception:
        return False
    return True


def _task_reuse_available(manifest_path: Path) -> bool:
    if _is_link_like(manifest_path) or not manifest_path.is_file():
        return False
    try:
        manifest = load_manifest(manifest_path)
    except Exception:
        return False
    return manifest.version == 2 and manifest_is_authorized(manifest) and any(
        task.cacheable
        and not task.unsafe_effects
        and not task.env
        and _runtime_is_available(manifest.root, task)
        for task in manifest.tasks.values()
    )


def _pytest_reuse_available(manifest_path: Path, profile_path: Path) -> bool:
    if (
        _is_link_like(manifest_path)
        or _is_link_like(profile_path)
        or not manifest_path.is_file()
        or not profile_path.is_file()
    ):
        return False
    try:
        manifest = load_manifest(manifest_path)
        if manifest.version != 2:
            return False
        if not manifest_is_authorized(manifest):
            return False
        profile = load_pytest_profile(profile_path, manifest)
        task = manifest.tasks[profile.task_name]
        if task.env:
            return False
        if not pytest_profile_is_authorized(manifest, profile.profile_sha256):
            return False
        if not _runtime_is_available(manifest.root, task):
            return False
    except Exception:
        return False
    return True


def install_codex(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repository root does not exist: {root}")
    if repository_marker_kind(root) is None:
        raise ValueError(
            f"Codex integration requires the repository root containing .git: {root}"
        )
    skill = install_project_skill(root)
    if skill["installed"]:
        mcp = register_codex_mcp(repository_root=root)
    else:
        mcp = {
            "available": None,
            "registered": False,
            "changed": False,
            "skipped": True,
            "reason": "Codex MCP registration skipped because project skill installation was not safe",
        }
    manifest = root / DEFAULT_MANIFEST
    pytest_profile = root / DEFAULT_PYTEST_PROFILE
    candidate = root / _CANDIDATE
    manifest_present = manifest.is_file()
    pytest_profile_present = pytest_profile.is_file()
    candidate_present = candidate.is_file()
    manifest_digest = None
    manifest_authorized = False
    profile_digest = None
    profile_authorized = False
    try:
        loaded_manifest = load_manifest(manifest) if manifest_present else None
        if loaded_manifest is not None:
            manifest_digest = manifest_sha256(loaded_manifest)
            manifest_authorized = manifest_is_authorized(loaded_manifest)
            if pytest_profile_present:
                loaded_profile = load_pytest_profile(
                    pytest_profile, loaded_manifest
                )
                profile_digest = loaded_profile.profile_sha256
                profile_authorized = pytest_profile_is_authorized(
                    loaded_manifest, profile_digest
                )
    except Exception:
        # The regular readiness checks below remain fail-closed and surface no
        # authority for malformed repository-authored configuration.
        pass
    integration_ready = bool(skill["installed"] and mcp.get("registered"))
    task_reuse_ready = integration_ready and _task_reuse_available(manifest)
    pytest_reuse_ready = integration_ready and _pytest_reuse_available(
        manifest, pytest_profile
    )
    if pytest_reuse_ready:
        mode = "pytest-node-reuse"
        next_action = "restart Codex; reviewed fine-grained pytest reuse is available"
    elif task_reuse_ready and candidate_present:
        mode = "task-reuse"
        next_action = "restart Codex; task reuse is available and the generated pytest candidate is awaiting explicit review before node reuse can activate"
    elif task_reuse_ready:
        mode = "task-reuse"
        next_action = "restart Codex; task reuse is available and Codex can prepare a non-authorizing pytest candidate with the prepare_pytest MCP tool"
    elif integration_ready:
        mode = "observe-only"
        next_action = "restart Codex; use prepare_pytest for supported automatic pinned setup, otherwise ZeroRun remains observation-only"
    else:
        mode = "unavailable"
        next_action = "resolve the Codex MCP registration issue above"
    return {
        "schema": "zerorun-codex-install-v5",
        "root": str(root),
        "skill": skill,
        "mcp": mcp,
        "manifest": str(manifest),
        "manifest_present": manifest_present,
        "pytest_profile": str(pytest_profile),
        "pytest_profile_present": pytest_profile_present,
        "pytest_candidate": str(candidate),
        "pytest_candidate_present": candidate_present,
        "manifest_sha256": manifest_digest,
        "manifest_authorized": manifest_authorized,
        "pytest_profile_sha256": profile_digest,
        "pytest_profile_authorized": profile_authorized,
        "integration_ready": integration_ready,
        "task_reuse_ready": task_reuse_ready,
        "pytest_reuse_ready": pytest_reuse_ready,
        "reuse_ready": task_reuse_ready or pytest_reuse_ready,
        "mode": mode,
        "ready": integration_ready,
        "next_action": next_action,
    }
