"""External exact-state qualification gate for the research deployment.

This records the scope of an operator's decision, not proof of determinism.
Only trusted code may call qualify(). The MCP service is never given that API.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

EXCLUDED_ROOT = frozenset({".git", ".zerorun", ".zerorun.json"})


class QualificationRequired(RuntimeError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def inventory(root):
    root = Path(root).resolve(strict=True)
    rows = []
    for directory, subdirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if base == root:
            subdirs[:] = [name for name in subdirs if name not in EXCLUDED_ROOT]
            files = [name for name in files if name not in EXCLUDED_ROOT]
        for name in sorted(subdirs + files):
            path = base / name
            before = path.lstat()
            mode = before.st_mode
            row = {"path": path.relative_to(root).as_posix(), "mode": stat.S_IMODE(mode)}
            if stat.S_ISDIR(mode):
                row["type"] = "directory"
            elif stat.S_ISREG(mode):
                row.update(type="file", bytes=before.st_size,
                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            else:
                raise QualificationRequired("linked or special source: " + row["path"])
            after = path.lstat()
            attrs = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
            if any(getattr(before, attr) != getattr(after, attr) for attr in attrs):
                raise QualificationRequired("source changed during inventory")
            rows.append(row)
    return sorted(rows, key=lambda row: row["path"])


def identity(root, manifest_path):
    root = Path(root).resolve(strict=True)
    path = Path(manifest_path)
    if path.is_symlink() or not path.is_file():
        raise QualificationRequired("manifest must be an ordinary file")
    first = inventory(root)
    manifest = hashlib.sha256(path.read_bytes()).hexdigest()
    second = inventory(root)
    if first != second or hashlib.sha256(path.read_bytes()).hexdigest() != manifest:
        raise QualificationRequired("qualification input changed during inspection")
    payload = {"schema": "zerorun.exact-state-qualification.v1",
               "root": str(root), "manifest_sha256": manifest, "inventory": first}
    return hashlib.sha256(canonical(payload)).hexdigest(), payload


def external_path(root, authority_path):
    root = Path(root).resolve(strict=True)
    lexical = Path(os.path.abspath(authority_path))
    if lexical == root or root in lexical.parents:
        raise QualificationRequired("qualification authority must be outside the checkout")
    for parent in (lexical, *lexical.parents):
        if parent.is_symlink():
            raise QualificationRequired("linked qualification authority path")
    if lexical.exists() and not stat.S_ISREG(lexical.lstat().st_mode):
        raise QualificationRequired("qualification authority must be an ordinary file")
    return lexical


def qualify(root, manifest_path, authority_path, *, rationale, expected_identity):
    if not rationale.strip():
        raise QualificationRequired("a scoped operator rationale is required")
    path = external_path(root, authority_path)
    key, payload = identity(root, manifest_path)
    if key != expected_identity:
        raise QualificationRequired("source differs from the inspected state")
    entries = json.loads(path.read_text()) if path.exists() else {}
    entries[key] = {"rationale": rationale, "scope": payload}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".qualification-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(entries))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return key


def require(root, manifest_path, authority_path):
    path = external_path(root, authority_path)
    if not path.exists():
        raise QualificationRequired("no external source-state qualification")
    if path.stat().st_mode & 0o022:
        raise QualificationRequired("qualification authority is group/world writable")
    key, payload = identity(root, manifest_path)
    raw = path.read_bytes()
    if len(raw) > 16 * 1024 * 1024:
        raise QualificationRequired("qualification authority exceeds bound")
    entries = json.loads(raw)
    row = entries.get(key)
    if not isinstance(row, dict) or row.get("scope") != payload or not row.get("rationale"):
        raise QualificationRequired("source state requires qualification")
    return key
