"""Run ZeroRun's non-executing Codex/MCP smoke across a frozen repo sample.

The harness checks out each public repository at its frozen commit, installs the
managed project skill through the real ZeroRun CLI, validates the real Codex MCP
registration, and exercises the MCP handshake/read-only tools and repository
boundary.  It never installs or executes code from the sampled repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Sequence

from zerorun import __version__
from zerorun.codex import _SKILL
from zerorun.oci import _run_bounded_process
from zerorun.path_safety import is_link_like, private_temporary_directory


SELECTION_SCHEMA = "zerorun.commercial-compatibility-selection.v1"
RESULT_SCHEMA = "zerorun.commercial-compatibility-result.v1"
_EXPECTED_TOOLS = {
    "prepare_pytest",
    "run_pytest",
    "run_tests",
    "stats",
    "explain",
    "doctor",
    "list_tasks",
}
MIN_SUCCESSFUL_INTEGRATIONS = 90
COMMERCIAL_OUTPUT_LIMIT_BYTES = 256 * 1024
COMMERCIAL_STDIN_LIMIT_BYTES = 64 * 1024
SELECTION_FILE_LIMIT_BYTES = 1024 * 1024
RECEIPT_FILE_LIMIT_BYTES = 2 * 1024 * 1024
_JSON_MAX_DEPTH = 12
_JSON_MAX_OBJECT_MEMBERS = 128
_JSON_MAX_ARRAY_ITEMS = 1_000
_JSON_MAX_STRING_CHARS = 16_384
_SELECTION_MAX_MEMBERS = 10_000
_RECEIPT_MAX_MEMBERS = 50_000
_GITHUB_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ROLE = "engineering_smoke_selection_only"
_SELECTION_SEED = "zerorun-commercial-100-repositories-v1"
_PLANNED_OUTCOME = (
    "attempt-all non-executing compatibility smoke; checkout layout, managed-skill "
    "installation or safe refusal, Codex registration, MCP protocol identity, and "
    "repository-boundary enforcement are measured; upstream code is not installed "
    "or executed"
)
_STRATA = (
    ("stars-100-499", "100..499", 100, 499),
    ("stars-500-1999", "500..1999", 500, 1999),
    ("stars-2000-9999", "2000..9999", 2000, 9999),
    ("stars-10000-plus", ">=10000", 10000, None),
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _preflight_json_bytes(
    raw: bytes,
    *,
    label: str,
    max_depth: int,
    max_members: int,
    max_string_chars: int,
) -> None:
    """Bound JSON nesting and structural width before decoder allocation."""

    depth = 0
    structural_tokens = 0
    max_structural_tokens = (max_members * 4) + 32
    max_encoded_string_bytes = (max_string_chars * 6) + 2
    in_string = False
    escaped = False
    string_bytes = 0
    for byte in raw:
        if in_string:
            string_bytes += 1
            if string_bytes > max_encoded_string_bytes:
                raise ValueError(f"{label} contains an overlong encoded JSON string")
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
            string_bytes = 0
        elif byte in (0x7B, 0x5B):
            depth += 1
            structural_tokens += 1
            if depth > max_depth:
                raise ValueError(f"{label} exceeds the JSON depth limit of {max_depth}")
        elif byte in (0x7D, 0x5D):
            depth -= 1
            structural_tokens += 1
            if depth < 0:
                break
        elif byte in (0x2C, 0x3A):
            structural_tokens += 1
        if structural_tokens > max_structural_tokens:
            raise ValueError(f"{label} exceeds the JSON structural-member limit")


def _validate_json_shape(
    payload: object,
    *,
    label: str,
    max_depth: int,
    max_members: int,
    max_object_members: int,
    max_array_items: int,
    max_string_chars: int,
) -> None:
    stack: list[tuple[object, int]] = [(payload, 1)]
    member_count = 0
    while stack:
        value, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"{label} exceeds the JSON depth limit of {max_depth}")
        if isinstance(value, dict):
            if len(value) > max_object_members:
                raise ValueError(f"{label} contains an object with too many members")
            member_count += len(value)
            for key, child in value.items():
                if len(key) > max_string_chars or "\x00" in key:
                    raise ValueError(f"{label} contains an invalid or overlong member name")
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            if len(value) > max_array_items:
                raise ValueError(f"{label} contains an oversized array")
            member_count += len(value)
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            if len(value) > max_string_chars or "\x00" in value:
                raise ValueError(f"{label} contains an invalid or overlong string")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise ValueError(f"{label} contains an unsupported JSON value")
        if member_count > max_members:
            raise ValueError(f"{label} exceeds the JSON member limit of {max_members}")


def _load_bounded_json_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    max_members: int,
) -> object:
    """Read one exact regular JSON file under explicit resource ceilings."""

    path = Path(os.path.abspath(path.expanduser()))
    if is_link_like(path):
        raise ValueError(f"{label} must not be a symbolic link or junction: {path}")
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
                raise ValueError(f"{label} must be a regular file: {path}")
            if opened.st_size > max_bytes:
                raise ValueError(f"{label} exceeds the {max_bytes}-byte safety limit")
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > max_bytes:
                raise ValueError(f"{label} exceeds the {max_bytes}-byte safety limit")
            current = path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError(f"{label} changed while it was being read")
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ValueError(f"could not safely read {label} {path}: {exc}") from exc

    _preflight_json_bytes(
        raw,
        label=label,
        max_depth=_JSON_MAX_DEPTH,
        max_members=max_members,
        max_string_chars=_JSON_MAX_STRING_CHARS,
    )

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len(pairs) > _JSON_MAX_OBJECT_MEMBERS:
            raise ValueError(f"{label} contains an object with too many members")
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} contains duplicate JSON member {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"{label} contains non-standard JSON number {value}")

    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"could not decode {label} {path}: {exc}") from exc
    _validate_json_shape(
        payload,
        label=label,
        max_depth=_JSON_MAX_DEPTH,
        max_members=max_members,
        max_object_members=_JSON_MAX_OBJECT_MEMBERS,
        max_array_items=_JSON_MAX_ARRAY_ITEMS,
        max_string_chars=_JSON_MAX_STRING_CHARS,
    )
    return payload


def _selection_window(payload: dict[str, Any]) -> tuple[datetime, datetime]:
    def parse_boundary(field: str) -> date:
        value = payload.get(field)
        if not isinstance(value, str):
            raise ValueError(f"commercial selection {field} must be YYYY-MM-DD")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(
                f"commercial selection {field} must be YYYY-MM-DD"
            ) from exc
        if parsed.isoformat() != value:
            raise ValueError(f"commercial selection {field} must be YYYY-MM-DD")
        return parsed

    start_date = parse_boundary("maintenance_start_utc_date_inclusive")
    cutoff_date = parse_boundary("selection_cutoff_utc_date_exclusive")
    if start_date >= cutoff_date:
        raise ValueError("commercial selection maintenance window is empty or reversed")
    return (
        datetime.combine(start_date, datetime_time.min, tzinfo=timezone.utc),
        datetime.combine(cutoff_date, datetime_time.min, tzinfo=timezone.utc),
    )


def _pushed_at_in_window(
    value: object,
    *,
    start_utc: datetime,
    cutoff_utc: datetime,
) -> bool:
    if not isinstance(value, str) or not _GITHUB_TIMESTAMP.fullmatch(value):
        return False
    try:
        pushed_at = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return start_utc <= pushed_at < cutoff_utc


def _sample_rank(repository: str, *, seed: str, stratum: str) -> str:
    material = f"{seed}\0{stratum}\0{repository.casefold()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def load_selection(path: Path) -> dict[str, Any]:
    payload = _load_bounded_json_file(
        path,
        label="commercial selection",
        max_bytes=SELECTION_FILE_LIMIT_BYTES,
        max_members=_SELECTION_MAX_MEMBERS,
    )
    if not isinstance(payload, dict) or payload.get("schema") != SELECTION_SCHEMA:
        raise ValueError(f"unsupported commercial selection schema in {path}")
    if payload.get("artifact_role") != _ARTIFACT_ROLE:
        raise ValueError("commercial selection has the wrong artifact role")
    if payload.get("selection_seed") != _SELECTION_SEED:
        raise ValueError("commercial selection has an unexpected selection seed")
    claimed = payload.get("corpus_sha256")
    unhashed = dict(payload)
    unhashed.pop("corpus_sha256", None)
    actual = hashlib.sha256(_canonical(unhashed)).hexdigest()
    if claimed != actual:
        raise ValueError(f"commercial selection digest mismatch: {claimed!r} != {actual}")
    repositories = payload.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != 100:
        raise ValueError("commercial selection must contain exactly 100 repositories")
    start_utc, cutoff_utc = _selection_window(payload)
    start_date = start_utc.date().isoformat()
    final_date = date.fromordinal(cutoff_utc.date().toordinal() - 1).isoformat()
    expected_strata = {name: (stars, minimum, maximum) for name, stars, minimum, maximum in _STRATA}
    frames = payload.get("frames")
    if not isinstance(frames, list) or len(frames) != len(_STRATA):
        raise ValueError("commercial selection must contain exactly four frame receipts")
    frame_names: set[str] = set()
    for index, (frame, expected) in enumerate(zip(frames, _STRATA)):
        if not isinstance(frame, dict):
            raise ValueError(f"commercial frame {index} is not an object")
        name, stars, _minimum, _maximum = expected
        expected_query = (
            "language:Python archived:false fork:false pytest in:readme "
            f"stars:{stars} pushed:{start_date}..{final_date}"
        )
        if frame.get("stratum") != name or frame.get("stars") != stars:
            raise ValueError(f"commercial frame {index} does not match its declared stratum")
        if frame.get("query") != expected_query:
            raise ValueError(f"commercial frame {index} has an unexpected query")
        retrieved = frame.get("retrieved_frame_count")
        rejected = frame.get("frame_validation_rejection_count")
        total = frame.get("reported_total_count")
        if (
            not isinstance(retrieved, int)
            or isinstance(retrieved, bool)
            or not 25 <= retrieved <= 200
            or not isinstance(rejected, int)
            or isinstance(rejected, bool)
            or not 0 <= rejected <= 200
            or retrieved + rejected > 200
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < retrieved
            or not isinstance(frame.get("frame_sha256"), str)
            or not _SHA256.fullmatch(frame["frame_sha256"])
        ):
            raise ValueError(f"commercial frame {index} has invalid receipt metadata")
        if name in frame_names:
            raise ValueError(f"duplicate commercial frame stratum: {name}")
        frame_names.add(name)

    names: set[str] = set()
    per_stratum: dict[str, list[str]] = {name: [] for name in expected_strata}
    for index, row in enumerate(repositories):
        if not isinstance(row, dict):
            raise ValueError(f"repository row {index} is not an object")
        if not _pushed_at_in_window(
            row.get("pushed_at"),
            start_utc=start_utc,
            cutoff_utc=cutoff_utc,
        ):
            raise ValueError(
                f"repository row {index} has pushed_at outside "
                "the declared maintenance window"
            )
        name = row.get("repository")
        commit = row.get("commit")
        clone_url = row.get("clone_url")
        stratum = row.get("stratum")
        stars = row.get("stars_at_selection")
        if (
            not isinstance(name, str)
            or name.count("/") != 1
            or not isinstance(commit, str)
            or len(commit) != 40
            or any(character not in "0123456789abcdef" for character in commit)
            or not isinstance(clone_url, str)
            or clone_url != f"https://github.com/{name}.git"
            or stratum not in expected_strata
            or not isinstance(stars, int)
            or isinstance(stars, bool)
        ):
            raise ValueError(f"repository row {index} has an invalid identity")
        _stars_label, minimum, maximum = expected_strata[stratum]
        if stars < minimum or (maximum is not None and stars > maximum):
            raise ValueError(
                f"repository row {index} stars do not match stratum {stratum}"
            )
        expected_rank = _sample_rank(
            name,
            seed=payload["selection_seed"],
            stratum=stratum,
        )
        if row.get("sample_rank_sha256") != expected_rank:
            raise ValueError(f"repository row {index} has an invalid sample rank")
        if row.get("planned_outcome") != _PLANNED_OUTCOME:
            raise ValueError(f"repository row {index} has an invalid planned outcome")
        folded = name.casefold()
        if folded in names:
            raise ValueError(f"duplicate repository in selection: {name}")
        names.add(folded)
        per_stratum[stratum].append(expected_rank)
    expected_order = [name for name, *_rest in _STRATA for _ in range(25)]
    actual_order = [row.get("stratum") for row in repositories]
    if actual_order != expected_order:
        raise ValueError("commercial selection must contain 25 ordered rows per stratum")
    if any(len(ranks) != 25 or ranks != sorted(ranks) for ranks in per_stratum.values()):
        raise ValueError("commercial selection rank ordering is inconsistent")

    exclusions = payload.get("selection_exclusions")
    if not isinstance(exclusions, list):
        raise ValueError("commercial selection exclusions must be an array")
    frame_rejection_counts = {name: 0 for name in expected_strata}
    for index, exclusion in enumerate(exclusions):
        if (
            not isinstance(exclusion, dict)
            or exclusion.get("stratum") not in expected_strata
            or not isinstance(exclusion.get("repository"), str)
            or not exclusion["repository"]
            or not isinstance(exclusion.get("reason"), str)
            or not exclusion["reason"]
        ):
            raise ValueError(f"commercial selection exclusion {index} is invalid")
        if exclusion["reason"].startswith("GitHub search candidate rejected"):
            frame_rejection_counts[exclusion["stratum"]] += 1
    declared_rejections = {
        frame["stratum"]: frame["frame_validation_rejection_count"] for frame in frames
    }
    if frame_rejection_counts != declared_rejections:
        raise ValueError("commercial frame rejection counts do not match exclusions")
    if payload.get("repository_count") != 100 or payload.get(
        "distinct_repository_count"
    ) != 100:
        raise ValueError("commercial selection count declarations are inconsistent")
    return payload


def load_receipt(path: Path) -> dict[str, Any]:
    payload = _load_bounded_json_file(
        path,
        label="commercial compatibility receipt",
        max_bytes=RECEIPT_FILE_LIMIT_BYTES,
        max_members=_RECEIPT_MAX_MEMBERS,
    )
    if not isinstance(payload, dict):
        raise ValueError("commercial compatibility receipt root must be an object")
    return payload


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_ms: float


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    timeout: int,
) -> CommandResult:
    started = time.perf_counter()
    input_bytes = input_text.encode("utf-8") if input_text is not None else None
    if input_bytes is not None and len(input_bytes) > COMMERCIAL_STDIN_LIMIT_BYTES:
        raise ValueError(
            "commercial MCP stdin payload exceeds the 64 KiB safety limit"
        )
    process, timed_out = _run_bounded_process(
        list(argv),
        cwd=cwd,
        environment=dict(os.environ) if env is None else env,
        timeout_seconds=timeout,
        output_limit_bytes=COMMERCIAL_OUTPUT_LIMIT_BYTES,
        input_bytes=input_bytes,
    )
    return CommandResult(
        returncode=process.returncode,
        stdout=process.stdout.decode("utf-8", errors="replace"),
        stderr=process.stderr.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )


def _tail(value: str, limit: int = 2000) -> str:
    return value[-limit:]


def _json_object(value: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} did not return one JSON document: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} returned a non-object JSON document")
    return payload


def _resolve_executable(command: str) -> str:
    discovered = shutil.which(command)
    if discovered is None:
        raise ValueError(f"required executable was not found on PATH: {command}")
    return str(Path(discovered).expanduser().resolve(strict=True))


def _skill_snapshot(repository: Path, path: Path) -> dict[str, Any]:
    """Describe a skill path without following repository-controlled links."""

    try:
        relative = path.relative_to(repository)
    except ValueError as exc:
        raise ValueError("skill path escaped the sampled repository") from exc
    cursor = repository
    for part in relative.parts:
        cursor = cursor / part
        if is_link_like(cursor):
            return {
                "kind": "link",
                "link_component": cursor.relative_to(repository).as_posix(),
            }
    if not path.exists():
        return {"kind": "absent"}
    if not path.is_file():
        return {"kind": "non_regular"}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {
        "kind": "regular_file",
        "size": size,
        "sha256": digest.hexdigest(),
    }


def _clone(row: dict[str, Any], destination: Path, environment: dict[str, str]) -> None:
    initialized = _run(
        ["git", "init", "-q", str(destination)], env=environment, timeout=30
    )
    if initialized.returncode != 0:
        raise RuntimeError(f"git init failed: {_tail(initialized.stderr)}")
    commands = (
        ["git", "-C", str(destination), "remote", "add", "origin", row["clone_url"]],
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "--depth=1",
            "--filter=blob:none",
            "--no-tags",
            "origin",
            row["commit"],
        ],
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.required=false",
            "checkout",
            "-q",
            "--detach",
            row["commit"],
        ],
    )
    for command in commands:
        result = _run(command, env=environment, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(
                f"{' '.join(command[3:6])} failed ({result.returncode}): "
                f"{_tail(result.stderr or result.stdout)}"
            )
    head = _run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        env=environment,
        timeout=30,
    )
    if head.returncode != 0 or head.stdout.strip() != row["commit"]:
        raise RuntimeError("checked-out HEAD does not equal the frozen commit")


def _mcp_requests(root: Path, escape_root: Path) -> str:
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "zerorun-commercial-smoke", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"root": str(root)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "stats", "arguments": {"root": str(root)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "stats", "arguments": {"root": str(escape_root)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "observe_test",
                "arguments": {
                    "root": str(root),
                    "command": ["python", "-m", "pytest"],
                    "approve_execution": True,
                },
            },
        },
    ]
    return "".join(json.dumps(request, separators=(",", ":")) + "\n" for request in requests)


def _validate_mcp(
    registration: dict[str, Any],
    *,
    root: Path,
    escape_root: Path,
    environment: dict[str, str],
    expected_command: str,
) -> dict[str, Any]:
    transport = registration.get("transport")
    if not isinstance(transport, dict):
        raise ValueError("Codex registration has no transport object")
    command = transport.get("command")
    arguments = transport.get("args", [])
    if not isinstance(command, str) or not isinstance(arguments, list) or not all(
        isinstance(value, str) for value in arguments
    ):
        raise ValueError("Codex registration transport is invalid")
    if (
        os.path.normcase(str(Path(command).expanduser().resolve(strict=False)))
        != os.path.normcase(str(Path(expected_command).resolve(strict=True)))
        or arguments != ["mcp-server"]
    ):
        raise ValueError(
            "Codex registration transport does not exactly match the trusted "
            "installed ZeroRun launcher"
        )
    result = _run(
        [command, *arguments],
        cwd=root,
        env=environment,
        input_text=_mcp_requests(root, escape_root),
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"MCP transport failed: {_tail(result.stderr)}")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) != 6:
        raise ValueError(f"MCP transport returned {len(lines)} responses, expected 6")
    responses = [_json_object(line, label="MCP response") for line in lines]
    if any(
        response.get("jsonrpc") != "2.0"
        or type(response.get("id")) is not int
        or "error" in response
        for response in responses
    ):
        raise ValueError("MCP response envelopes are malformed")
    by_id = {response["id"]: response for response in responses}
    if set(by_id) != {1, 2, 3, 4, 5, 6}:
        raise ValueError("MCP response IDs are incomplete or duplicated")

    def success_result(response_id: int, *, tool_call: bool = False) -> dict[str, Any]:
        response = by_id[response_id]
        result = response.get("result")
        if "error" in response or not isinstance(result, dict):
            raise ValueError(f"MCP response {response_id} is not a success result")
        if tool_call and result.get("isError") is not False:
            raise ValueError(f"MCP response {response_id} reported a tool error")
        return result

    server = success_result(1).get("serverInfo")
    if server != {"name": "zerorun", "version": __version__}:
        raise ValueError(f"unexpected MCP server identity: {server!r}")
    tools = success_result(2).get("tools")
    if (
        not isinstance(tools, list)
        or len(tools) != len(_EXPECTED_TOOLS)
        or any(not isinstance(tool, dict) for tool in tools)
        or any(not isinstance(tool.get("name"), str) for tool in tools)
    ):
        raise ValueError("MCP tool surface has invalid or duplicate entries")
    names = [tool["name"] for tool in tools]
    if len(set(names)) != len(names) or set(names) != _EXPECTED_TOOLS:
        raise ValueError(f"unexpected MCP tool surface: {sorted(names)}")
    doctor = success_result(3, tool_call=True).get("structuredContent")
    stats = success_result(4, tool_call=True).get("structuredContent")
    if (
        not isinstance(doctor, dict)
        or doctor.get("ok") is not True
        or doctor.get("root") != str(root.resolve())
    ):
        raise ValueError(f"doctor failed: {doctor!r}")
    if not isinstance(stats, dict) or stats.get("root") != str(root.resolve()):
        raise ValueError(f"stats was not repository-bound: {stats!r}")
    escape = by_id[5]
    escape_result = escape.get("result")
    escape_payload = (
        escape_result.get("structuredContent")
        if isinstance(escape_result, dict)
        else None
    )
    if (
        not isinstance(escape_result, dict)
        or escape_result.get("isError") is not True
        or not isinstance(escape_payload, dict)
        or escape_payload.get("status") != "ERROR"
        or "refusing access" not in str(escape_payload.get("error", ""))
    ):
        raise ValueError("MCP repository-boundary escape was not rejected")
    legacy_result = by_id[6].get("result")
    legacy_payload = (
        legacy_result.get("structuredContent")
        if isinstance(legacy_result, dict)
        else None
    )
    if (
        not isinstance(legacy_result, dict)
        or legacy_result.get("isError") is not True
        or not isinstance(legacy_payload, dict)
        or legacy_payload.get("status") != "ERROR"
        or "unknown MCP tool 'observe_test'"
        not in str(legacy_payload.get("error", ""))
    ):
        raise ValueError("legacy observe_test MCP call did not hard-error")
    return {
        "server": server,
        "tool_count": len(names),
        "doctor_mode": doctor.get("mode"),
        "stats_mode": stats.get("mode"),
        "escape_rejected": True,
        "legacy_observe_test_rejected": True,
        "transport_stderr_tail": _tail(result.stderr),
    }


def smoke_repository(
    row: dict[str, Any],
    *,
    zerorun_command: str,
    codex_command: str,
    work_root: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    zerorun_executable = _resolve_executable(zerorun_command)
    codex_executable = _resolve_executable(codex_command)
    record: dict[str, Any] = {
        "repository": row["repository"],
        "commit": row["commit"],
        "status": "failure",
        "phase": "clone",
    }
    environment = dict(os.environ)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    try:
        with private_temporary_directory(
            work_root, prefix="zerorun-commercial-"
        ) as temporary:
            temporary_root = temporary.resolve(strict=True)
            repository = temporary_root / "repository"
            escape_root = temporary_root / "outside-repository"
            (escape_root / ".git").mkdir(parents=True)
            _clone(row, repository, environment)

            record["phase"] = "codex_init"
            codex_home = temporary_root / "codex-home"
            codex_home.mkdir()
            execution_environment = dict(environment)
            execution_environment["CODEX_HOME"] = str(codex_home)
            before_skill = repository / ".agents" / "skills" / "zerorun" / "SKILL.md"
            before_snapshot = _skill_snapshot(repository, before_skill)
            record["skill_snapshot_before"] = before_snapshot
            initialized = _run(
                [
                    zerorun_executable,
                    "--json",
                    "init",
                    "--codex",
                    "--root",
                    str(repository),
                ],
                cwd=repository,
                env=execution_environment,
                timeout=60,
            )
            install = _json_object(initialized.stdout, label="zerorun init")
            record["install"] = install
            if initialized.returncode != 0:
                conflict = install.get("skill", {}).get("conflict") is True
                after_snapshot = _skill_snapshot(repository, before_skill)
                record["skill_snapshot_after"] = after_snapshot
                unchanged = before_snapshot == after_snapshot
                if conflict and unchanged and not install.get("integration_ready"):
                    record.update(
                        {
                            "status": "safe_refusal",
                            "phase": "complete",
                            "reason": install.get("skill", {}).get("reason"),
                            "refusal_proof": {
                                "skill_conflict": True,
                                "unchanged": True,
                                "before": before_snapshot,
                                "after": after_snapshot,
                            },
                        }
                    )
                    return record
                raise RuntimeError(
                    f"zerorun init failed ({initialized.returncode}): "
                    f"{_tail(initialized.stderr)}"
                )
            if install.get("integration_ready") is not True:
                raise ValueError("zerorun init exited zero without integration readiness")
            installed_snapshot = _skill_snapshot(repository, before_skill)
            record["skill_snapshot_after"] = installed_snapshot
            expected_skill_sha256 = hashlib.sha256(_SKILL.encode("utf-8")).hexdigest()
            if installed_snapshot.get("sha256") != expected_skill_sha256:
                raise ValueError("installed Codex project skill differs from packaged policy")
            record["installed_skill_sha256"] = expected_skill_sha256

            record["phase"] = "codex_registration"
            registration_result = _run(
                [codex_executable, "mcp", "get", "zerorun", "--json"],
                cwd=repository,
                env=execution_environment,
                timeout=30,
            )
            if registration_result.returncode != 0:
                raise RuntimeError(
                    f"codex mcp get failed: {_tail(registration_result.stderr)}"
                )
            registration = _json_object(
                registration_result.stdout, label="codex mcp get"
            )

            record["phase"] = "mcp_protocol"
            record["mcp"] = _validate_mcp(
                registration,
                root=repository,
                escape_root=escape_root,
                environment=execution_environment,
                expected_command=zerorun_executable,
            )
            record.update({"status": "pass", "phase": "complete"})
    except Exception as exc:  # preserve every attempted outcome in the receipt
        record["reason"] = f"{type(exc).__name__}: {exc}"
    finally:
        record["wall_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    return record


def run_batch(
    selection: dict[str, Any],
    *,
    batch_index: int,
    batch_count: int,
    zerorun_command: str,
    codex_command: str,
    work_root: Path,
) -> dict[str, Any]:
    if batch_count < 1 or batch_index < 0 or batch_index >= batch_count:
        raise ValueError("batch index must be within [0, batch_count)")
    zerorun_executable = _resolve_executable(zerorun_command)
    codex_executable = _resolve_executable(codex_command)
    codex_version_result = _run([codex_executable, "--version"], timeout=30)
    if codex_version_result.returncode != 0:
        raise ValueError(f"Codex version check failed: {_tail(codex_version_result.stderr)}")
    selected = [
        (index, row)
        for index, row in enumerate(selection["repositories"])
        if index % batch_count == batch_index
    ]
    rows = []
    for index, repository in selected:
        print(
            f"[{len(rows) + 1:02d}/{len(selected):02d}] {repository['repository']} "
            f"{repository['commit'][:12]}",
            flush=True,
        )
        row = smoke_repository(
            repository,
            zerorun_command=zerorun_executable,
            codex_command=codex_executable,
            work_root=work_root,
        )
        row["selection_index"] = index
        rows.append(row)
        print(f"  -> {row['status']} ({row['phase']})", flush=True)
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("pass", "safe_refusal", "failure")
    }
    return {
        "schema": RESULT_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_sha256": selection["corpus_sha256"],
        "engine_version": __version__,
        "engine_commit": os.environ.get("GITHUB_SHA"),
        "codex_version": codex_version_result.stdout.strip(),
        "batch_index": batch_index,
        "batch_count": batch_count,
        "attempted": len(rows),
        "counts": counts,
        "upstream_code_executed": False,
        "rows": rows,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-index", type=int, default=0)
    parser.add_argument("--batch-count", type=int, default=1)
    parser.add_argument("--work-root", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--zerorun-command", default="zerorun")
    parser.add_argument("--codex-command", default="codex")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selection = load_selection(args.selection)
    args.work_root.mkdir(parents=True, exist_ok=True)
    try:
        zerorun_command = _resolve_executable(args.zerorun_command)
        codex_command = _resolve_executable(args.codex_command)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    receipt = run_batch(
        selection,
        batch_index=args.batch_index,
        batch_count=args.batch_count,
        zerorun_command=zerorun_command,
        codex_command=codex_command,
        work_root=args.work_root.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: receipt[key] for key in ("attempted", "counts")}, indent=2))
    return 1 if receipt["counts"]["failure"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
