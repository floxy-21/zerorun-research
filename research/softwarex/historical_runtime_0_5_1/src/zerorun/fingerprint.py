from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import os
import platform
import shutil
import stat
import threading
from pathlib import Path
from typing import Any, Iterator

from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import is_link_like as _is_link_like


CHUNK_SIZE = 1024 * 1024

_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)

# A frozen path record contains only the scalar fields emitted by
# ``_path_record``.  Keeping pattern-expansion results immutable prevents one
# consumer from corrupting the request-local snapshot seen by another.
_FrozenPathRecord = tuple[tuple[str, Any], ...]
_FrozenExpansion = tuple[_FrozenPathRecord, ...]
_DirectoryInventorySignature = tuple[
    tuple[int, ...],
    tuple[str, ...],
    tuple[tuple[str, tuple[int, ...], bool], ...],
]

# Only this compact, stable base environment is inherited by child processes.
# Everything else must be explicitly declared in ``task.env``. Values are
# fingerprinted as hashes before a cache entry can be reused.
RUNTIME_ENV_NAMES = (
    "PATH",
    "LD_LIBRARY_PATH",
    "COMSPEC",
    "PATHEXT",
    "SYSTEMROOT",
    "SystemRoot",
    "WINDIR",
)
FIXED_RUNTIME_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONHASHSEED": "0",
    "TZ": "UTC",
}


def _descriptor_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        getattr(details, "st_ctime_ns", 0),
    )


def _path_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
    )


def _open_stable_regular(path: Path) -> tuple[Path, int, os.stat_result]:
    lexical = Path(os.path.abspath(path.expanduser()))
    if _is_link_like(lexical):
        raise ConfigurationError(f"refusing to fingerprint linked file: {lexical}")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise ConfigurationError(f"could not safely open fingerprint input {lexical}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise ConfigurationError(f"could not inspect fingerprint input {lexical}: {exc}") from exc
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise ConfigurationError(f"fingerprint input is not a regular file: {lexical}")
    return lexical, descriptor, opened


def _verify_stable_regular(
    lexical: Path,
    descriptor: int,
    opened: os.stat_result,
    *,
    bytes_read: int,
) -> None:
    try:
        after_open = os.fstat(descriptor)
        current = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError(
            f"fingerprint input changed while it was being read: {lexical}"
        ) from exc
    if (
        bytes_read != opened.st_size
        or _descriptor_identity(after_open) != _descriptor_identity(opened)
        or _is_link_like(lexical)
        or not stat.S_ISREG(current.st_mode)
        or _path_identity(current) != _path_identity(opened)
    ):
        raise ConfigurationError(
            f"fingerprint input changed while it was being read: {lexical}"
        )


def read_stable_file_bytes(path: Path) -> bytes:
    """Read an exact regular-file snapshot through one verified descriptor."""

    lexical, descriptor, opened = _open_stable_regular(path)
    try:
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, CHUNK_SIZE):
            total += len(chunk)
            chunks.append(chunk)
        _verify_stable_regular(
            lexical,
            descriptor,
            opened,
            bytes_read=total,
        )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    lexical, descriptor, opened = _open_stable_regular(path)
    digest = hashlib.sha256()
    total = 0
    try:
        while chunk := os.read(descriptor, CHUNK_SIZE):
            total += len(chunk)
            digest.update(chunk)
        _verify_stable_regular(
            lexical,
            descriptor,
            opened,
            bytes_read=total,
        )
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def is_within(root: Path, path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_link_like_details(details: os.stat_result) -> bool:
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    )


def _relative_to_root(root: Path, candidate: Path) -> bool:
    return _path_key_within(_security_path_key(root), _security_path_key(candidate))


def _security_path_key(path: os.PathLike[str] | str) -> str:
    text = path if isinstance(path, str) else os.fspath(path)
    return os.path.normcase(os.path.normpath(text))


def _cache_path_key(path: os.PathLike[str] | str) -> str:
    # Callers construct paths from one canonical session root, so preserving
    # spelling here is correct and avoids expensive Windows locale case-folding
    # on every dictionary lookup. Differently-cased aliases merely miss the
    # optimization and are still validated independently.
    text = path if isinstance(path, str) else os.fspath(path)
    return os.path.normpath(text)


def _path_key_within(root_key: str, candidate_key: str) -> bool:
    if candidate_key == root_key:
        return True
    prefix = root_key if root_key.endswith(os.sep) else root_key + os.sep
    return candidate_key.startswith(prefix)


def _resolve_directory_cached(
    path: str,
    cache: dict[str, str] | None,
    lstat_cache: dict[str, os.stat_result | None] | None = None,
) -> str:
    key = _cache_path_key(path)
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached

    # Walk forward from the nearest request-locally resolved ancestor. This is
    # materially cheaper than asking pathlib to lstat the root and every parent
    # again for each of thousands of sibling negative-import candidates.
    anchor = path
    suffix: list[str] = []
    resolved_anchor: str | None = None
    if cache is not None:
        while True:
            anchor_key = _cache_path_key(anchor)
            resolved_anchor = cache.get(anchor_key)
            if resolved_anchor is not None:
                break
            parent = os.path.dirname(anchor)
            if parent == anchor:
                break
            suffix.append(os.path.basename(anchor))
            anchor = parent
    if resolved_anchor is None:
        resolved = str(Path(path).resolve(strict=False))
    else:
        resolved = resolved_anchor
        lexical = anchor
        missing_ancestor = False
        for name in reversed(suffix):
            lexical = os.path.join(lexical, name)
            if missing_ancestor:
                resolved = os.path.join(resolved, name)
                continue
            details = _cached_lstat(lexical, lstat_cache)
            if details is None:
                missing_ancestor = True
                resolved = os.path.join(resolved, name)
            elif _is_link_like_details(details):
                resolved = str(Path(lexical).resolve(strict=False))
            else:
                resolved = os.path.join(resolved, name)
    if cache is not None:
        cache[key] = resolved
    return resolved


def _cached_lstat(
    path: os.PathLike[str] | str,
    cache: dict[str, os.stat_result | None] | None,
) -> os.stat_result | None:
    key = _cache_path_key(path)
    if cache is not None and key in cache:
        return cache[key]
    try:
        details: os.stat_result | None = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        details = None
    if cache is not None:
        cache[key] = details
    return details


def validate_relative(
    root: Path,
    raw: str,
    *,
    field: str,
    resolved_root: Path | None = None,
    validation_cache: dict[str, Path] | None = None,
    directory_resolution_cache: dict[str, str] | None = None,
    lstat_cache: dict[str, os.stat_result | None] | None = None,
    state_dir_resolved: Path | None = None,
    root_security_key: str | None = None,
    state_dir_security_key: str | None = None,
) -> Path:
    """Resolve one root-relative path without trusting results across snapshots.

    When request-local caches are supplied, literal missing paths and patterns
    whose magic is confined to the basename share one verified parent
    resolution.  This avoids thousands of redundant ancestor ``lstat`` calls
    for negative import candidates.  Existing link-like leaves still take the
    canonical full-resolution path, and a new ``FingerprintSession`` receives
    empty caches for the mandatory final verification pass.
    """

    if validation_cache is not None:
        cached = validation_cache.get(raw)
        if cached is not None:
            return cached

    root_resolved = resolved_root or root.resolve(strict=False)
    root_key = root_security_key or _security_path_key(root_resolved)
    lexical_text = os.path.abspath(os.path.join(os.fspath(root_resolved), raw))
    lexical_key = _security_path_key(lexical_text)
    lexical = Path(lexical_text)
    candidate: Path
    # The fast path is deliberately narrow: magic in an ancestor can expand
    # through several directories and must retain pathlib's canonical resolve
    # behavior.  A magic basename, or a literal leaf, needs only one safely
    # resolved parent plus an ``lstat`` of the leaf.
    parent_text = os.path.dirname(raw)
    fast_parent = (
        directory_resolution_cache is not None
        and _path_key_within(root_key, lexical_key)
        and lexical_key != root_key
        and not glob.has_magic(parent_text)
    )
    if fast_parent:
        resolved_parent = _resolve_directory_cached(
            os.path.dirname(lexical_text),
            directory_resolution_cache,
            lstat_cache,
        )
        if not _path_key_within(root_key, _security_path_key(resolved_parent)):
            candidate = (root_resolved / raw).resolve(strict=False)
        elif glob.has_magic(os.path.basename(lexical_text)):
            candidate = Path(
                os.path.join(resolved_parent, os.path.basename(lexical_text))
            )
        else:
            try:
                details = _cached_lstat(lexical_text, lstat_cache)
            except OSError:
                candidate = (root_resolved / raw).resolve(strict=False)
            else:
                if details is None:
                    candidate = Path(
                        os.path.join(resolved_parent, os.path.basename(lexical_text))
                    )
                else:
                    candidate = (
                        lexical.resolve(strict=False)
                        if _is_link_like_details(details)
                        else Path(
                            os.path.join(
                                resolved_parent,
                                os.path.basename(lexical_text),
                            )
                        )
                    )
    else:
        candidate = (root_resolved / raw).resolve(strict=False)

    candidate_key = _security_path_key(candidate)
    if not _path_key_within(root_key, candidate_key):
        raise ConfigurationError(f"{field} escapes the project root: {raw}")
    state_dir = state_dir_resolved or (root_resolved / ".zerorun").resolve(
        strict=False
    )
    state_key = state_dir_security_key or _security_path_key(state_dir)
    if _path_key_within(state_key, candidate_key):
        raise ConfigurationError(f"{field} cannot point inside .zerorun: {raw}")
    if validation_cache is not None:
        validation_cache[raw] = candidate
    return candidate


def controlled_environment(task: TaskSpec) -> dict[str, str]:
    """Return the only environment a cacheable child is allowed to observe."""
    environment = dict(FIXED_RUNTIME_ENV)
    for name in RUNTIME_ENV_NAMES:
        if name in os.environ:
            environment[name] = os.environ[name]
    for name in task.env:
        if name in os.environ:
            environment[name] = os.environ[name]
        else:
            environment.pop(name, None)
    return environment


def environment_fingerprint(environment: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "present": True,
            "sha256": hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest(),
        }
        for name, value in sorted(environment.items())
    }


def _path_record(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    if _is_link_like(path):
        try:
            target = os.readlink(path)
        except OSError:
            target = "<junction>"
        return {"path": relative, "type": "symlink", "target": target}
    if path.is_file():
        before = path.stat(follow_symlinks=False)
        digest = sha256_file(path)
        after = path.stat(follow_symlinks=False)
        if (
            _is_link_like(path)
            or not stat.S_ISREG(after.st_mode)
            or (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                getattr(before, "st_ctime_ns", 0),
            )
            != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
                getattr(after, "st_ctime_ns", 0),
            )
        ):
            raise ConfigurationError(
                f"fingerprint input changed while it was being recorded: {path}"
            )
        mode = stat.S_IMODE(after.st_mode)
        return {
            "path": relative,
            "type": "file",
            "size": after.st_size,
            "mode": mode,
            "mtime_ns": after.st_mtime_ns,
            "sha256": digest,
        }
    if path.is_dir():
        details = path.stat()
        return {
            "path": relative,
            "type": "directory",
            "mode": stat.S_IMODE(details.st_mode),
            "mtime_ns": details.st_mtime_ns,
        }
    return {"path": relative, "type": "unsupported"}


def _cached_path_record(
    root: Path,
    path: Path,
    cache: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Return one path record, optionally reusing it inside one snapshot pass.

    The cache is deliberately supplied by the caller rather than process-global:
    reusing content hashes across independent requests could hide a source edit.
    A batch fingerprint pass may safely share records because it represents one
    explicit source snapshot, and the runner takes a new snapshot after execution
    before publishing or returning any result.
    """
    if cache is None:
        return _path_record(root, path)
    relative = path.relative_to(root).as_posix()
    record = cache.get(relative)
    if record is None:
        record = _path_record(root, path)
        cache[relative] = record
    return record


def _tree_records(
    root: Path,
    path: Path,
    *,
    path_record_cache: dict[str, dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Fingerprint a directory recursively without following symlinks."""
    record = _cached_path_record(root, path, path_record_cache)
    yield record
    # The record was produced from a verified lstat/hash snapshot. Repeating
    # ``is_dir`` and link probes here adds no information inside the same
    # snapshot and was responsible for several redundant lstats per overlap.
    if record.get("type") != "directory":
        return
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        yield from _tree_records(root, child, path_record_cache=path_record_cache)


def project_state(root: Path, *, excluded: set[str] | None = None) -> dict[str, dict[str, Any]]:
    """Return a full project snapshot for the cacheable-task write guard."""
    excluded = excluded or set()
    state: dict[str, dict[str, Any]] = {}
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        relative = child.relative_to(root).as_posix()
        if relative == ".zerorun" or relative.startswith(".zerorun/"):
            continue
        for record in _tree_records(root, child):
            path = str(record["path"])
            if path in excluded:
                continue
            state[path] = record
    return state


def changed_paths(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _freeze_expansion(records: list[dict[str, Any]]) -> _FrozenExpansion:
    return tuple(tuple(record.items()) for record in records)


def _thaw_expansion(records: _FrozenExpansion) -> list[dict[str, Any]]:
    return [dict(record) for record in records]


def _simple_basename_glob(
    root_text: str,
    pattern: str,
    *,
    directory_entries_cache: dict[str, tuple[str, ...]] | None,
    lstat_cache: dict[str, os.stat_result | None] | None,
) -> list[str] | None:
    """Expand a basename-only glob with one shared directory scan.

    Returning ``None`` delegates to ``glob.glob`` for recursive or
    ancestor-magic patterns.  Python's glob implementation uses the same
    ``fnmatch.filter`` basename matching, with the additional rule that a
    wildcard not beginning with ``.`` does not select hidden names.
    """

    basename = os.path.basename(pattern)
    parent_text = os.path.dirname(pattern)
    if (
        not basename
        or basename == "**"
        or not glob.has_magic(basename)
        or glob.has_magic(parent_text)
        # On POSIX a backslash is a literal filename character, not a path
        # separator. Avoid changing that unusual but valid glob behavior.
        or (os.sep != "\\" and "\\" in pattern)
    ):
        return None
    parent = os.path.join(root_text, parent_text)
    key = _cache_path_key(parent)
    names: tuple[str, ...] | None = None
    if directory_entries_cache is not None:
        names = directory_entries_cache.get(key)
    if names is None:
        try:
            parent_details = _cached_lstat(parent, lstat_cache)
        except OSError:
            parent_details = None
        if parent_details is None or (
            not stat.S_ISDIR(parent_details.st_mode)
            and not _is_link_like_details(parent_details)
        ):
            names = ()
        else:
            try:
                with os.scandir(parent) as entries:
                    names = tuple(entry.name for entry in entries)
            except OSError:
                # glob treats an unreadable/missing directory as no matches.
                names = ()
        if directory_entries_cache is not None:
            directory_entries_cache[key] = names
    visible = (
        names
        if basename.startswith(".")
        else tuple(name for name in names if not name.startswith("."))
    )
    if basename.count("*") == 1 and not any(
        marker in basename for marker in ("?", "[")
    ):
        prefix, suffix = basename.split("*", 1)
        normalized_prefix = os.path.normcase(prefix)
        normalized_suffix = os.path.normcase(suffix)
        matched_names = [
            name
            for name in visible
            if os.path.normcase(name).startswith(normalized_prefix)
            and os.path.normcase(name).endswith(normalized_suffix)
            and len(name) >= len(prefix) + len(suffix)
        ]
    else:
        matched_names = fnmatch.filter(visible, basename)
    return [os.path.join(parent, name) for name in matched_names]


def _literal_glob_match(
    root_text: str,
    pattern: str,
    *,
    lstat_cache: dict[str, os.stat_result | None] | None,
) -> list[str] | None:
    """Return an exact literal match using one lstat, or ``None`` for globs."""

    if glob.has_magic(pattern) or pattern.endswith((os.sep, os.altsep or os.sep)):
        return None
    candidate = os.path.join(root_text, pattern)
    try:
        details = _cached_lstat(candidate, lstat_cache)
    except OSError:
        # Preserve glob's platform-specific treatment of unusual I/O errors.
        return None
    if details is None:
        return []
    return [str(candidate)]


def _stable_nofollow_directory_inventory(
    path: Path,
    *,
    required_names: frozenset[str],
) -> tuple[
    _DirectoryInventorySignature,
    os.stat_result,
    tuple[str, ...],
    dict[str, tuple[str, os.stat_result]],
]:
    """Capture one POSIX directory without following or trusting its pathname.

    The open descriptor pins the directory during enumeration.  Descriptor and
    pathname identities must agree before and after the scan, while the full
    name inventory proves negative membership.  Only entries needed to descend
    the requested path trie are statted.
    """

    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError(
            f"could not safely open expected-absence directory {path}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        before_path = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or _is_link_like_details(before_path)
            or _descriptor_identity(before_path) != _descriptor_identity(opened)
        ):
            raise ConfigurationError(
                f"expected-absence path contains a linked or replaced directory: {path}"
            )

        names: list[str] = []
        required: dict[str, tuple[str, os.stat_result]] = {}
        with os.scandir(descriptor) as entries:
            for entry in entries:
                name = entry.name
                names.append(name)
                key = os.path.normcase(name)
                if key not in required_names:
                    continue
                if key in required:
                    raise ConfigurationError(
                        "expected-absence directory has ambiguous case-normalized entries: "
                        f"{path}"
                    )
                try:
                    details = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ConfigurationError(
                        "expected-absence directory entry changed while it was inspected: "
                        f"{path / name}"
                    ) from exc
                required[key] = (name, details)

        after_descriptor = os.fstat(descriptor)
        after_path = path.stat(follow_symlinks=False)
        if (
            _descriptor_identity(after_descriptor) != _descriptor_identity(opened)
            or _is_link_like_details(after_path)
            or _descriptor_identity(after_path) != _descriptor_identity(opened)
        ):
            raise ConfigurationError(
                f"expected-absence directory changed while it was inventoried: {path}"
            )
        ordered_names = tuple(sorted(names))
        required_signature = tuple(
            sorted(
                (
                    name,
                    _descriptor_identity(details),
                    _is_link_like_details(details),
                )
                for name, details in required.values()
            )
        )
        signature: _DirectoryInventorySignature = (
            _descriptor_identity(opened),
            ordered_names,
            required_signature,
        )
        return signature, opened, ordered_names, required
    except OSError as exc:
        raise ConfigurationError(
            f"expected-absence directory changed while it was inventoried: {path}"
        ) from exc
    finally:
        os.close(descriptor)


def _canonical_expected_absence_parts(pattern: str) -> tuple[str, ...] | None:
    """Recognize the generated literal/basename-glob negative path subset."""

    if (
        not isinstance(pattern, str)
        or not pattern
        or "\x00" in pattern
        or "\\" in pattern
        or pattern.startswith("/")
        or pattern.endswith("/")
        or (
            len(pattern) >= 2
            and pattern[0].isalpha()
            and pattern[1] == ":"
        )
    ):
        return None
    parts = tuple(pattern.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if parts[0] == ".zerorun":
        return None
    if any(glob.has_magic(part) for part in parts[:-1]):
        return None
    basename = parts[-1]
    if basename == "**":
        return None
    return parts


def _commit_expected_missing_expansions(
    patterns: tuple[str, ...],
    pattern_expansion_cache: dict[str, _FrozenExpansion] | None,
) -> list[dict[str, Any]]:
    """Return generic-equivalent missing rows and commit their cache entries.

    The caller has already proved every declaration missing across two stable
    hierarchy inventories.  Validate every existing cache entry before one
    update so a conflict cannot be partially overwritten.  Supported callers
    hold their ``FingerprintSession`` lock around this operation.
    """

    records = [
        {"pattern": pattern, "type": "missing"}
        for pattern in sorted(patterns)
    ]
    if pattern_expansion_cache is None:
        return records

    pending = {
        pattern: _freeze_expansion(
            [{"pattern": pattern, "type": "missing"}]
        )
        for pattern in patterns
    }
    for pattern, expected in pending.items():
        current = pattern_expansion_cache.get(pattern)
        if current is not None and current != expected:
            raise ConfigurationError(
                "expected-absence pattern expansion changed across one "
                f"fingerprint snapshot: {pattern}"
            )
    pattern_expansion_cache.update(pending)
    return records


def expand_expected_absent_inputs(
    root: Path,
    patterns: tuple[str, ...],
    *,
    path_record_cache: dict[str, dict[str, Any]] | None = None,
    validation_cache: dict[str, Path] | None = None,
    directory_resolution_cache: dict[str, str] | None = None,
    lstat_cache: dict[str, os.stat_result | None] | None = None,
    directory_entries_cache: dict[str, tuple[str, ...]] | None = None,
    pattern_expansion_cache: dict[str, _FrozenExpansion] | None = None,
    resolved_root: Path | None = None,
    state_dir_resolved: Path | None = None,
    root_security_key: str | None = None,
    state_dir_security_key: str | None = None,
) -> list[dict[str, Any]]:
    """Expand expected-missing inputs from stable hierarchical inventories.

    This is a POSIX-only optimization for reviewed negative import surfaces.
    It changes neither the successful records nor generic input expansion.  A
    caller-visible cache is populated only after every opened directory passes
    a second independent identity/inventory comparison.
    """

    fallback_kwargs = {
        "path_record_cache": path_record_cache,
        "validation_cache": validation_cache,
        "directory_resolution_cache": directory_resolution_cache,
        "lstat_cache": lstat_cache,
        "directory_entries_cache": directory_entries_cache,
        "pattern_expansion_cache": pattern_expansion_cache,
        "resolved_root": resolved_root,
        "state_dir_resolved": state_dir_resolved,
        "root_security_key": root_security_key,
        "state_dir_security_key": state_dir_security_key,
    }
    if not patterns:
        return []
    if (
        os.name != "posix"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(
            pattern_expansion_cache is not None
            and pattern in pattern_expansion_cache
            for pattern in patterns
        )
    ):
        return expand_inputs(root, patterns, **fallback_kwargs)

    parsed = {
        pattern: _canonical_expected_absence_parts(pattern)
        for pattern in patterns
    }
    if any(parts is None for parts in parsed.values()):
        return expand_inputs(root, patterns, **fallback_kwargs)

    root_resolved = resolved_root or root.resolve(strict=True)
    if _security_path_key(root_resolved) != _security_path_key(root):
        return expand_inputs(root, patterns, **fallback_kwargs)

    child_directories: dict[tuple[str, ...], set[str]] = {}
    leaves: dict[tuple[str, ...], list[tuple[str, str]]] = {}
    parents: set[tuple[str, ...]] = {()}
    for pattern, maybe_parts in parsed.items():
        assert maybe_parts is not None
        parent = maybe_parts[:-1]
        basename = maybe_parts[-1]
        leaves.setdefault(parent, []).append((pattern, basename))
        parents.add(parent)
        for index, segment in enumerate(parent):
            prefix = parent[:index]
            child_directories.setdefault(prefix, set()).add(segment)
            parents.add(prefix)

    local_lstats: dict[str, os.stat_result | None] = {}
    local_entries: dict[str, tuple[str, ...]] = {}
    local_resolutions: dict[str, str] = {
        _cache_path_key(root_resolved): str(root_resolved)
    }
    snapshots: dict[
        tuple[str, ...],
        tuple[Path, frozenset[str], _DirectoryInventorySignature],
    ] = {}
    parent_exists: dict[tuple[str, ...], bool] = {(): True}
    unexpected_pattern: str | None = None

    for parent in sorted(parents, key=lambda value: (len(value), value)):
        parent_path = root_resolved.joinpath(*parent)
        parent_key = _cache_path_key(parent_path)
        child_names = child_directories.get(parent, set())
        leaf_rows = leaves.get(parent, [])
        literal_leaf_names = {
            basename for _pattern, basename in leaf_rows if not glob.has_magic(basename)
        }
        required_names = frozenset(
            os.path.normcase(name) for name in child_names | literal_leaf_names
        )

        if not parent_exists.get(parent, False):
            local_lstats[parent_key] = None
            local_entries[parent_key] = ()
            for child in child_names:
                parent_exists[(*parent, child)] = False
            for _pattern, basename in leaf_rows:
                if not glob.has_magic(basename):
                    local_lstats[_cache_path_key(parent_path / basename)] = None
            continue

        signature, directory_details, names, required = (
            _stable_nofollow_directory_inventory(
                parent_path,
                required_names=required_names,
            )
        )
        snapshots[parent] = (parent_path, required_names, signature)
        local_lstats[parent_key] = directory_details
        local_entries[parent_key] = names
        local_resolutions[parent_key] = str(parent_path)

        for child in child_names:
            child_key = os.path.normcase(child)
            observed = required.get(child_key)
            child_path = parent_path / child
            cache_key = _cache_path_key(child_path)
            if observed is None:
                local_lstats[cache_key] = None
                parent_exists[(*parent, child)] = False
                continue
            _observed_name, details = observed
            local_lstats[cache_key] = details
            if _is_link_like_details(details) or not stat.S_ISDIR(details.st_mode):
                raise ConfigurationError(
                    "expected-absence path contains a linked or non-directory component: "
                    f"{child_path}"
                )
            parent_exists[(*parent, child)] = True
            local_resolutions[cache_key] = str(child_path)

        visible_names = tuple(name for name in names if not name.startswith("."))
        for pattern, basename in leaf_rows:
            if glob.has_magic(basename):
                candidates = names if basename.startswith(".") else visible_names
                if fnmatch.filter(candidates, basename):
                    unexpected_pattern = pattern
                    break
                continue
            observed = required.get(os.path.normcase(basename))
            leaf_key = _cache_path_key(parent_path / basename)
            local_lstats[leaf_key] = observed[1] if observed is not None else None
            if observed is not None:
                unexpected_pattern = pattern
                break
        if unexpected_pattern is not None:
            break

    if unexpected_pattern is not None:
        return [{"pattern": unexpected_pattern, "type": "present"}]

    # Re-open every directory after the entire hierarchy is captured.  No
    # first-pass cache becomes visible unless identities and complete name
    # inventories remain byte-for-byte stable across this barrier.
    for parent in sorted(snapshots, key=lambda value: (-len(value), value)):
        path, required_names, expected = snapshots[parent]
        current, _details, _names, _required = _stable_nofollow_directory_inventory(
            path,
            required_names=required_names,
        )
        if current != expected:
            raise ConfigurationError(
                f"expected-absence directory changed between inventory passes: {path}"
            )

    # A caller may already have observed a shared ancestor while hashing a
    # positive input in this same snapshot.  Never overwrite that observation:
    # exact agreement is required before the hierarchical evidence is merged.
    if lstat_cache is not None:
        for key, current in local_lstats.items():
            if key not in lstat_cache:
                continue
            previous = lstat_cache[key]
            if (previous is None) != (current is None) or (
                previous is not None
                and current is not None
                and _descriptor_identity(previous) != _descriptor_identity(current)
            ):
                raise ConfigurationError(
                    "expected-absence path changed across one fingerprint snapshot: "
                    f"{key}"
                )
        lstat_cache.update(local_lstats)
    if directory_entries_cache is not None:
        for key, current in local_entries.items():
            previous = directory_entries_cache.get(key)
            if previous is not None and tuple(sorted(previous)) != current:
                raise ConfigurationError(
                    "expected-absence directory membership changed across one "
                    f"fingerprint snapshot: {key}"
                )
        directory_entries_cache.update(local_entries)
    if directory_resolution_cache is not None:
        for key, current in local_resolutions.items():
            previous = directory_resolution_cache.get(key)
            if previous is not None and _security_path_key(previous) != _security_path_key(
                current
            ):
                raise ConfigurationError(
                    "expected-absence directory resolution changed across one "
                    f"fingerprint snapshot: {key}"
                )
        directory_resolution_cache.update(local_resolutions)
    return _commit_expected_missing_expansions(
        patterns,
        pattern_expansion_cache,
    )


def _expand_one_pattern(
    root: Path,
    root_text: str,
    pattern: str,
    *,
    path_record_cache: dict[str, dict[str, Any]] | None,
    directory_entries_cache: dict[str, tuple[str, ...]] | None,
    lstat_cache: dict[str, os.stat_result | None] | None,
) -> list[dict[str, Any]]:
    matches = _literal_glob_match(root_text, pattern, lstat_cache=lstat_cache)
    if matches is None:
        matches = _simple_basename_glob(
            root_text,
            pattern,
            directory_entries_cache=directory_entries_cache,
            lstat_cache=lstat_cache,
        )
    if matches is None:
        matches = glob.glob(str(root / pattern), recursive=True)
    matches.sort()
    if not matches:
        return [{"pattern": pattern, "type": "missing"}]

    records: list[dict[str, Any]] = []
    for matched in matches:
        path = Path(matched)
        if not is_within(root, path):
            raise ConfigurationError(f"input match escapes project root: {path}")
        # ``_tree_records`` emits exactly one record for non-directories and
        # link-like paths, so it also avoids duplicate type/link probes here.
        records.extend(
            _tree_records(root, path, path_record_cache=path_record_cache)
        )
    return records


def expand_inputs(
    root: Path,
    patterns: tuple[str, ...],
    *,
    path_record_cache: dict[str, dict[str, Any]] | None = None,
    validation_cache: dict[str, Path] | None = None,
    directory_resolution_cache: dict[str, str] | None = None,
    lstat_cache: dict[str, os.stat_result | None] | None = None,
    directory_entries_cache: dict[str, tuple[str, ...]] | None = None,
    pattern_expansion_cache: dict[str, _FrozenExpansion] | None = None,
    resolved_root: Path | None = None,
    state_dir_resolved: Path | None = None,
    root_security_key: str | None = None,
    state_dir_security_key: str | None = None,
    cache_lock: threading.RLock | None = None,
) -> list[dict[str, Any]]:
    if cache_lock is not None:
        with cache_lock:
            return expand_inputs(
                root,
                patterns,
                path_record_cache=path_record_cache,
                validation_cache=validation_cache,
                directory_resolution_cache=directory_resolution_cache,
                lstat_cache=lstat_cache,
                directory_entries_cache=directory_entries_cache,
                pattern_expansion_cache=pattern_expansion_cache,
                resolved_root=resolved_root,
                state_dir_resolved=state_dir_resolved,
                root_security_key=root_security_key,
                state_dir_security_key=state_dir_security_key,
            )

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    root_text = os.fspath(root)

    for pattern in patterns:
        validate_relative(
            root,
            pattern,
            field="input",
            resolved_root=resolved_root,
            validation_cache=validation_cache,
            directory_resolution_cache=directory_resolution_cache,
            lstat_cache=lstat_cache,
            state_dir_resolved=state_dir_resolved,
            root_security_key=root_security_key,
            state_dir_security_key=state_dir_security_key,
        )
        pattern_records: list[dict[str, Any]]
        frozen = (
            pattern_expansion_cache.get(pattern)
            if pattern_expansion_cache is not None
            else None
        )
        if frozen is None:
            pattern_records = _expand_one_pattern(
                root,
                root_text,
                pattern,
                path_record_cache=path_record_cache,
                directory_entries_cache=directory_entries_cache,
                lstat_cache=lstat_cache,
            )
            if pattern_expansion_cache is not None:
                pattern_expansion_cache[pattern] = _freeze_expansion(pattern_records)
        else:
            pattern_records = _thaw_expansion(frozen)

        for info in pattern_records:
            relative = info.get("path")
            if isinstance(relative, str):
                if relative in seen:
                    continue
                seen.add(relative)
            records.append(info)

    return sorted(records, key=lambda item: (item.get("path", ""), item.get("pattern", "")))


def _normalized_pattern_parts(pattern: str) -> tuple[str, ...]:
    """Return root-relative path segments using the lexical rules glob sees."""

    normalized: list[str] = []
    for part in pattern.replace("\\", "/").split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if normalized:
                normalized.pop()
            continue
        normalized.append(part)
    return tuple(normalized)


def _glob_covers_relative_path(pattern: str, relative: str) -> bool:
    """Match one relative path with path-aware recursive-glob semantics."""

    pattern_parts = _normalized_pattern_parts(pattern)
    path_parts = tuple(part for part in relative.replace("\\", "/").split("/") if part)
    pending = [(0, 0)]
    visited: set[tuple[int, int]] = set()
    while pending:
        pattern_index, path_index = pending.pop()
        state = (pattern_index, path_index)
        if state in visited:
            continue
        visited.add(state)
        if pattern_index == len(pattern_parts):
            if path_index == len(path_parts):
                return True
            continue
        segment = pattern_parts[pattern_index]
        if segment == "**":
            pending.append((pattern_index + 1, path_index))
            if path_index < len(path_parts):
                pending.append((pattern_index, path_index + 1))
        elif path_index < len(path_parts) and fnmatch.fnmatch(
            path_parts[path_index], segment
        ):
            pending.append((pattern_index + 1, path_index + 1))
    return False


def _reject_cacheable_output_input_overlap(
    root: Path,
    task: TaskSpec,
    outputs: list[str],
    input_records: list[dict[str, Any]],
) -> None:
    """Reject self-referential v1 keys before cache lookup or publication."""

    if not task.cacheable:
        return

    for raw_input in task.inputs:
        input_path = validate_relative(root, raw_input, field="input")
        if not glob.has_magic(raw_input):
            relative_input = input_path.relative_to(root).as_posix()
            for output in outputs:
                if relative_input == "." or output == relative_input or output.startswith(
                    relative_input.rstrip("/") + "/"
                ):
                    raise ConfigurationError(
                        f"task {task.name!r}: cacheable output {output!r} is covered "
                        f"by declared input {raw_input!r}"
                    )
            continue

        for output in outputs:
            candidates = [output]
            parent = Path(output).parent
            while parent != Path("."):
                candidates.append(parent.as_posix())
                parent = parent.parent
            if any(_glob_covers_relative_path(raw_input, candidate) for candidate in candidates):
                raise ConfigurationError(
                    f"task {task.name!r}: cacheable output {output!r} is covered "
                    f"by declared input {raw_input!r}"
                )

    # Defend against any platform-specific glob behavior not represented by the
    # lexical matcher above. A matched input directory recursively covers its
    # descendants because expand_inputs records the whole tree.
    for record in input_records:
        relative = record.get("path")
        if not isinstance(relative, str):
            continue
        for output in outputs:
            if relative == output or (
                record.get("type") == "directory"
                and (relative == "." or output.startswith(relative.rstrip("/") + "/"))
            ):
                raise ConfigurationError(
                    f"task {task.name!r}: cacheable output {output!r} is covered "
                    "by a declared input"
                )


def resolve_executable(
    command: tuple[str, ...], root: Path, environment: dict[str, str] | None = None
) -> dict[str, Any]:
    raw = command[0]
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = (root / candidate).resolve(strict=False) if not candidate.is_absolute() else candidate.resolve(strict=False)
    else:
        found = shutil.which(raw, path=(environment or os.environ).get("PATH"))
        resolved = Path(found).resolve(strict=False) if found else Path(raw)

    result: dict[str, Any] = {
        "requested_sha256": hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest(),
        "resolved": str(resolved),
    }
    if resolved.is_file():
        before = resolved.stat(follow_symlinks=False)
        result["sha256"] = sha256_file(resolved)
        after = resolved.stat(follow_symlinks=False)
        if _descriptor_identity(before) != _descriptor_identity(after):
            raise ConfigurationError(
                f"resolved executable changed while it was fingerprinted: {resolved}"
            )
        result["size"] = after.st_size
    else:
        result["missing"] = True
    return result


def resolve_launch_argv(command: tuple[str, ...], root: Path, environment: dict[str, str]) -> list[str]:
    raw = command[0]
    candidate = Path(raw)
    if candidate.is_absolute() or candidate.parent != Path("."):
        resolved = (root / candidate).resolve(strict=False) if not candidate.is_absolute() else candidate.resolve(strict=False)
    else:
        found = shutil.which(raw, path=environment.get("PATH"))
        resolved = Path(found).resolve(strict=False) if found else None
    return [str(resolved), *command[1:]] if resolved is not None else list(command)


def command_sources(
    command: tuple[str, ...],
    root: Path,
    *,
    excluded: set[str] | None = None,
    path_record_cache: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Fingerprint project-local files named directly by the command argv."""
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    excluded = excluded or set()
    for index, argument in enumerate(command):
        if index == 0 and Path(argument).parent == Path("."):
            # A bare executable is resolved through PATH instead of the project.
            continue
        candidate_text = argument
        if index > 0 and "=" in argument:
            _, candidate_text = argument.split("=", 1)
        if candidate_text.startswith("-"):
            continue
        candidate = (root / candidate_text).resolve(strict=False)
        if not is_within(root, candidate) or not candidate.exists():
            continue
        candidate_relative = candidate.relative_to(root).as_posix()
        if candidate_relative in excluded:
            continue
        source_records = (
            _tree_records(
                root,
                candidate,
                path_record_cache=path_record_cache,
            )
            if candidate.is_dir() and not _is_link_like(candidate)
            else (_cached_path_record(root, candidate, path_record_cache),)
        )
        for record in source_records:
            relative = str(record["path"])
            if relative not in seen:
                seen.add(relative)
                records.append(record)
    return sorted(records, key=lambda item: str(item["path"]))


def execution_directory_records(
    root: Path,
    relative_paths: list[str] | tuple[str, ...] | set[str],
) -> list[dict[str, Any]]:
    """Record root and implicit traversal-directory modes for an exact view.

    File records already bind file modes. These additional records bind every
    directory whose permissions affect whether a container can traverse the
    staged closure, including the repository root even when it was not an
    explicit input.
    """

    root_resolved = root.resolve(strict=True)
    directories: set[Path] = {root_resolved}
    for relative in relative_paths:
        source = validate_relative(root_resolved, relative, field="execution_input")
        cursor = source if source.is_dir() else source.parent
        while True:
            try:
                cursor.relative_to(root_resolved)
            except ValueError as exc:
                raise ConfigurationError(
                    f"execution input escapes the project root: {relative}"
                ) from exc
            directories.add(cursor)
            if cursor == root_resolved:
                break
            cursor = cursor.parent

    records: list[dict[str, Any]] = []
    for directory in sorted(
        directories,
        key=lambda path: (
            len(path.relative_to(root_resolved).parts),
            path.relative_to(root_resolved).as_posix(),
        ),
    ):
        if _is_link_like(directory) or not directory.is_dir():
            relative = directory.relative_to(root_resolved).as_posix()
            raise ConfigurationError(
                f"execution traversal path is not a regular directory: {relative}"
            )
        details = directory.stat(follow_symlinks=False)
        relative = directory.relative_to(root_resolved).as_posix()
        records.append(
            {
                "path": relative,
                "type": "directory",
                "mode": stat.S_IMODE(details.st_mode),
            }
        )
    return records


def task_fingerprint(
    manifest: Manifest, task: TaskSpec, *, environment: dict[str, str] | None = None
) -> tuple[str, dict[str, Any]]:
    outputs = [validate_relative(manifest.root, output, field="output").relative_to(manifest.root).as_posix() for output in task.outputs]
    if len(outputs) != len(set(outputs)):
        raise ConfigurationError(f"task {task.name!r}: outputs contain duplicates")

    input_records = expand_inputs(manifest.root, task.inputs)
    _reject_cacheable_output_input_overlap(
        manifest.root,
        task,
        outputs,
        input_records,
    )

    environment = environment if environment is not None else controlled_environment(task)
    command_digest = hashlib.sha256(
        json.dumps(list(task.command), separators=(",", ":"), ensure_ascii=False).encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    payload: dict[str, Any] = {
        "schema": 3,
        "task": task.name,
        "command": {"argv_sha256": command_digest, "argc": len(task.command)},
        "executable": resolve_executable(task.command, manifest.root, environment),
        "command_sources": command_sources(task.command, manifest.root, excluded=set(outputs)),
        "inputs": input_records,
        "outputs": outputs,
        "environment": environment_fingerprint(environment),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "policy": {
            "cacheable": task.cacheable,
            "unsafe_effects": list(task.unsafe_effects),
            "cache_streams": task.cache_streams,
            "result_normalizer": task.result_normalizer,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), payload
