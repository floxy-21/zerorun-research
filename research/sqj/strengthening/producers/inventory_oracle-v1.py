"""Independent, research-only inventory for explicit whole-file input boundaries.

This module imports no ZeroRun code. Its digest is NOT a ZeroRun cache key,
authorization, execution oracle, or proof that a declared closure is complete.
Only equality/change/refusal decisions are compared with the production key.

Scope: quiescent local trees, canonical literal relative inputs (directories
include every descendant), explicit command argv, and supplied runtime/env
identities. All project-local command sources must also be declared inputs.
Glob expansion, symbols, implicit command-source discovery, host executable
resolution, runtime attestation, and concurrent-tree correctness are excluded.
Timestamps are deliberately omitted under normalized-metadata execution.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Any, Mapping, Sequence


class InventoryRefusal(ValueError):
    """The explicit research inventory cannot represent this input safely."""


def _literal(raw: str) -> tuple[str, ...]:
    if not isinstance(raw, str) or not raw or any(c in raw for c in "\\:*?[]\x00"):
        raise InventoryRefusal("only canonical literal relative inputs are supported")
    parts = tuple(raw.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        raise InventoryRefusal("input contains a noncanonical or escaping component")
    if PurePosixPath(raw).is_absolute() or parts[0] in {".git", ".zerorun"}:
        raise InventoryRefusal("root metadata/state cannot be a declared input")
    return parts


def _details(path: Path) -> os.stat_result:
    try:
        result = path.lstat()
    except OSError as exc:
        raise InventoryRefusal(f"missing or unreadable input: {path.name}") from exc
    # Refuse Windows reparse points as well as POSIX symbolic links. The
    # researcher must not silently inventory a different tree through a link.
    if stat.S_ISLNK(result.st_mode) or getattr(result, "st_file_attributes", 0) & 0x400:
        raise InventoryRefusal(f"link-like input is unsupported: {path.name}")
    if not (stat.S_ISREG(result.st_mode) or stat.S_ISDIR(result.st_mode)):
        raise InventoryRefusal(f"special input is unsupported: {path.name}")
    return result


def _read_identity(details: os.stat_result) -> tuple[int, ...]:
    return (details.st_dev, details.st_ino, details.st_mode, details.st_size,
            details.st_mtime_ns, details.st_ctime_ns)


def explicit_inventory(
    root: Path,
    inputs: Sequence[str],
    *,
    command: Sequence[str],
    task_name: str,
    runtime_identity: Mapping[str, Any],
    environment_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe the declared execution material without production helpers.

    This is a bounded offline measurement. Stable per-file reads are checked,
    but enumeration is not a globally atomic snapshot. Callers must supply a
    quiescent tree and must not use this function to authorize cache reuse.
    """
    root = Path(os.path.abspath(root))
    root_details = _details(root)
    if not stat.S_ISDIR(root_details.st_mode):
        raise InventoryRefusal("inventory root must be a regular directory")
    if isinstance(inputs, (str, bytes)) or not inputs:
        raise InventoryRefusal("a nonempty sequence of explicit inputs is required")
    if (not isinstance(task_name, str) or not task_name or isinstance(command, (str, bytes)) or not command
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)):
        raise InventoryRefusal("task name and nonempty command argv are required")

    records: dict[str, dict[str, Any]] = {
        ".": {"path": ".", "kind": "directory", "mode": stat.S_IMODE(root_details.st_mode)}
    }

    def directory(path: Path, details: os.stat_result) -> None:
        relative = path.relative_to(root).as_posix()
        records[relative] = {"path": relative, "kind": "directory",
                             "mode": stat.S_IMODE(details.st_mode)}

    def visit(path: Path) -> None:
        relative = path.relative_to(root).as_posix()
        if relative in records:
            return
        before = _details(path)
        if stat.S_ISDIR(before.st_mode):
            directory(path, before)
            try:
                children = sorted(path.iterdir(), key=lambda child: child.name)
            except OSError as exc:
                raise InventoryRefusal(f"unreadable directory: {relative}") from exc
            for child in children:
                visit(child)
        else:
            try:
                with path.open("rb") as stream:
                    opened = os.fstat(stream.fileno())
                    content_hash = hashlib.sha256()
                    while block := stream.read(1024 * 1024):
                        content_hash.update(block)
                    digest = content_hash.hexdigest()
                    finished = os.fstat(stream.fileno())
            except OSError as exc:
                raise InventoryRefusal(f"unreadable file: {relative}") from exc
            after = _details(path)
            # Windows Python can expose different ctime meanings via lstat
            # and fstat. Check ctime stability within each API, and compare
            # material identity/mode/size/mtime across path and descriptor.
            if (_read_identity(before) != _read_identity(after)
                    or _read_identity(opened) != _read_identity(finished)
                    or _read_identity(before)[:-1] != _read_identity(opened)[:-1]):
                raise InventoryRefusal(f"file changed during inventory: {relative}")
            records[relative] = {"path": relative, "kind": "file",
                                 "mode": stat.S_IMODE(after.st_mode),
                                 "size": after.st_size, "content_sha256": digest}

    # Materialize explicit declarations before traversal: no glob semantics,
    # automatic discovery, or untracked-file filtering is hidden in this step.
    declarations = sorted(set(tuple(_literal(raw)) for raw in inputs))
    for parts in declarations:
        cursor = root
        for part in parts[:-1]:
            cursor = cursor / part
            details = _details(cursor)
            if not stat.S_ISDIR(details.st_mode):
                raise InventoryRefusal("an input ancestor is not a directory")
            directory(cursor, details)
        visit(root.joinpath(*parts))

    # Round-tripping creates detached finite JSON identities rather than
    # retaining caller-owned mutable dictionaries in an earlier inventory.
    payload = {"contract": "research-explicit-result-only-normalized-metadata-v1",
               "task_name": task_name, "command": list(command),
               "runtime_identity": runtime_identity,
               "environment_identity": environment_identity,
               "records": [records[path] for path in sorted(records)]}
    try:
        return json.loads(json.dumps(payload, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise InventoryRefusal("identities must be finite JSON values") from exc


def inventory_digest(*args: Any, **kwargs: Any) -> str:
    """Return an independently namespaced digest; never a production key."""
    inventory = explicit_inventory(*args, **kwargs)
    encoded = json.dumps(inventory, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
