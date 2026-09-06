from __future__ import annotations

import hashlib
import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .model import ConfigurationError, TaskSpec
from .path_safety import private_temporary_directory
from .pytest_node_identity import (
    PYTEST_NODE_ACTION_SCHEMA,
    PytestNodeResultIdentity,
    validate_pytest_node_action_identity,
)
from .store import ProjectLockBusy, Store
from .trust import (
    CacheTrustSession,
    cache_payload_is_trusted,
    manifest_sha256_for_root,
    sign_cache_payload,
)


_COMPACT_RESULT_STORAGE_SCHEMA = 1
_MERKLE_RESULT_STORAGE_SCHEMA = 2
_MAX_COMPACT_NODEID_CHARS = 8192
_COMPACT_RESULT_FIELDS = frozenset(
    {
        "schema",
        "storage_schema",
        "kind",
        "task",
        "key",
        "fingerprint_sha256",
        "nodeid",
        "profile_sha256",
        "runtime_identity_sha256",
        "created_at",
        "execution_ms",
        "exit_code",
        "provenance",
    }
)
_LEGACY_RESULT_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "task",
        "key",
        "created_at",
        "execution_ms",
        "fingerprint",
        "exit_code",
        "provenance",
    }
)


def _canonical_sha256(value: object, *, label: str) -> str:
    """Match the canonical action-key encoding used by the key builders."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _is_exact_int(value: object, expected: int) -> bool:
    """Reject booleans, whose numeric equality would otherwise pass."""

    return type(value) is int and value == expected


def _schema_six_identity(
    task: TaskSpec,
    key: str,
    fingerprint: dict[str, Any],
) -> dict[str, str]:
    """Validate and reduce one canonical pytest-node action identity.

    The full fingerprint remains the canonical preimage for ``key``. Compact
    on-disk metadata stores only signed identity digests plus the human-auditable
    task/node/profile fields, eliminating a redundant ~100--200 KiB preimage
    from every pytest node entry without weakening its binding.
    """

    if fingerprint.get("schema") != 6:
        raise ConfigurationError("compact result metadata requires fingerprint schema 6")
    fingerprint_sha256 = _canonical_sha256(
        fingerprint,
        label="pytest node fingerprint",
    )
    if fingerprint_sha256 != key:
        raise ConfigurationError(
            "pytest node cache key does not match its canonical fingerprint"
        )
    if fingerprint.get("kind") != "hermetic-result-only":
        raise ConfigurationError("pytest node fingerprint kind is malformed")
    if fingerprint.get("task") != task.name:
        raise ConfigurationError("pytest node fingerprint task is malformed")
    nodeid = fingerprint.get("nodeid")
    if (
        not isinstance(nodeid, str)
        or not nodeid
        or len(nodeid) > _MAX_COMPACT_NODEID_CHARS
        or "\x00" in nodeid
    ):
        raise ConfigurationError("pytest node fingerprint node id is malformed")
    profile_sha256 = fingerprint.get("profile_sha256")
    if not _is_lower_sha256(profile_sha256):
        raise ConfigurationError("pytest node fingerprint profile digest is malformed")
    runtime_identity = fingerprint.get("runtime_identity")
    if not isinstance(runtime_identity, dict):
        raise ConfigurationError("pytest node fingerprint runtime identity is malformed")
    return {
        "fingerprint_sha256": fingerprint_sha256,
        "nodeid": nodeid,
        "profile_sha256": profile_sha256,
        "runtime_identity_sha256": _canonical_sha256(
            runtime_identity,
            label="pytest node runtime identity",
        ),
    }


def _compact_metadata_matches(
    metadata: dict[str, Any],
    *,
    task: TaskSpec,
    key: str,
    identity: dict[str, str],
    fingerprint_schema: int = 6,
    storage_schema: int = _COMPACT_RESULT_STORAGE_SCHEMA,
) -> bool:
    return (
        set(metadata) == _COMPACT_RESULT_FIELDS
        and _is_exact_int(metadata.get("schema"), fingerprint_schema)
        and _is_exact_int(
            metadata.get("storage_schema"),
            storage_schema,
        )
        and metadata.get("kind") == "hermetic-result-only"
        and metadata.get("task") == task.name
        and metadata.get("key") == key
        and metadata.get("fingerprint_sha256") == identity["fingerprint_sha256"]
        and metadata.get("nodeid") == identity["nodeid"]
        and metadata.get("profile_sha256") == identity["profile_sha256"]
        and metadata.get("runtime_identity_sha256")
        == identity["runtime_identity_sha256"]
        and _valid_nonnegative_number(metadata.get("created_at"))
        and _valid_nonnegative_number(metadata.get("execution_ms"))
        and _is_exact_int(metadata.get("exit_code"), 0)
    )


def _emit(data: bytes, stream: BinaryIO) -> None:
    if data:
        stream.write(data)
        stream.flush()


@contextmanager
def _action_lock(store: Store, key: str, *, timeout_seconds: float = 60.0):
    """Serialize publication for one immutable hermetic action key only."""
    if not key or any(ch not in "0123456789abcdef" for ch in key) or len(key) != 64:
        raise ConfigurationError("action lock requires a 64-character lowercase hex cache key")
    store.ensure()
    locks = store.managed_directory(store.state / "locks")
    locks.mkdir(parents=True, exist_ok=True)
    locks = store.managed_directory(locks)
    lock_path = store.managed_file(locks / f"action-{key}.lock")
    token = f"pid={os.getpid()} created_at={time.time()}"
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ProjectLockBusy(
                    f"another ZeroRun invocation owns {lock_path}; refusing concurrent action publication"
                )
            time.sleep(0.05)
    try:
        os.write(descriptor, token.encode("utf-8"))
        yield
    finally:
        os.close(descriptor)
        try:
            if lock_path.read_text(encoding="utf-8") == token:
                lock_path.unlink()
        except OSError:
            pass


def _fingerprint_schema(fingerprint: dict[str, Any]) -> int:
    schema = fingerprint.get("schema")
    # Schema 6 is the legacy pytest node-level result identity that keeps the exact
    # node's structural collection identity in the persistent key while moving
    # the whole-partition collection digest to request-local race validation.
    # Store semantics remain identical: metadata must match the supplied
    # fingerprint byte-for-byte and only successful result-only entries load.
    # Schema 9 is its compact, domain-separated Merkle successor. Schemas 7 and
    # 8 are task-level identities for execution from a private
    # exact-closure snapshot (without/with reviewed symbol projections). They
    # intentionally invalidate schemas 4/5, whose execution saw the live tree.
    if schema not in {4, 5, 6, 7, 8, PYTEST_NODE_ACTION_SCHEMA}:
        raise ConfigurationError(f"unsupported hermetic fingerprint schema: {schema!r}")
    return int(schema)


def _profile_digest(fingerprint: dict[str, Any]) -> str | None:
    schema = _fingerprint_schema(fingerprint)
    if schema not in {6, PYTEST_NODE_ACTION_SCHEMA}:
        return None
    digest = fingerprint.get("profile_sha256")
    if not isinstance(digest, str):
        raise ConfigurationError("pytest node fingerprint has no profile digest")
    return digest


def _load_result_entry(
    store: Store,
    task: TaskSpec,
    key: str,
    fingerprint: dict[str, Any],
    *,
    result_identity: PytestNodeResultIdentity | None = None,
    trust_session: CacheTrustSession | None = None,
    trust_session_provider: Callable[[], CacheTrustSession] | None = None,
) -> dict[str, Any] | None:
    expected_schema = _fingerprint_schema(fingerprint)
    compact_identity: dict[str, str] | None = None
    compact_storage_schema: int | None = None
    if expected_schema == 6:
        if result_identity is not None:
            return None
        try:
            compact_identity = _schema_six_identity(task, key, fingerprint)
            compact_storage_schema = _COMPACT_RESULT_STORAGE_SCHEMA
        except ConfigurationError:
            # A caller-supplied key/preimage mismatch must never quarantine a
            # valid entry that merely happens to live at the supplied path.
            return None
    elif expected_schema == PYTEST_NODE_ACTION_SCHEMA:
        try:
            derived_identity = validate_pytest_node_action_identity(
                task_name=task.name,
                key=key,
                fingerprint=fingerprint,
            )
        except ConfigurationError:
            return None
        if result_identity is not None and result_identity != derived_identity:
            return None
        compact_identity = derived_identity.metadata_fields()
        compact_storage_schema = _MERKLE_RESULT_STORAGE_SCHEMA
    metadata = store.metadata(key)
    if metadata is None:
        return None
    # Opening a cache trust session performs repository-wide pre/post
    # attestation.  Defer that fixed cost until an on-disk candidate actually
    # exists: an absent entry has no payload that could authorize reuse.  Once
    # a candidate exists the provider must return the request-scoped session,
    # so every payload remains HMAC checked and the caller can attest the whole
    # lookup phase before any provisional hit escapes.
    if trust_session is None and trust_session_provider is not None:
        trust_session = trust_session_provider()
    profile_digest = _profile_digest(fingerprint)
    if trust_session is None:
        trusted = cache_payload_is_trusted(
            store.root,
            metadata,
            manifest_digest=manifest_sha256_for_root(store.root),
            profile_digest=profile_digest,
        )
    else:
        trusted = trust_session.payload_is_trusted(
            metadata,
            manifest_digest=trust_session.manifest_digest,
            profile_digest=profile_digest,
        )
    if expected_schema in {6, PYTEST_NODE_ACTION_SCHEMA}:
        assert compact_identity is not None
        assert compact_storage_schema is not None
        if "storage_schema" in metadata:
            identity_matches = _compact_metadata_matches(
                metadata,
                task=task,
                key=key,
                identity=compact_identity,
                fingerprint_schema=expected_schema,
                storage_schema=compact_storage_schema,
            )
        else:
            # Compatibility is intentionally read-only: new publications never
            # write this full-preimage representation. Exact fields prevent a
            # malformed compact/legacy hybrid from being interpreted as legacy.
            identity_matches = expected_schema == 6 and (
                set(metadata) == _LEGACY_RESULT_FIELDS
                and _is_exact_int(metadata.get("schema"), expected_schema)
                and metadata.get("kind") == "hermetic-result-only"
                and metadata.get("task") == task.name
                and metadata.get("key") == key
                and metadata.get("fingerprint") == fingerprint
                and _valid_nonnegative_number(metadata.get("created_at"))
                and _valid_nonnegative_number(metadata.get("execution_ms"))
                and _is_exact_int(metadata.get("exit_code"), 0)
            )
    else:
        identity_matches = (
            _is_exact_int(metadata.get("schema"), expected_schema)
            and metadata.get("kind") == "hermetic-result-only"
            and metadata.get("task") == task.name
            and metadata.get("key") == key
            and metadata.get("fingerprint") == fingerprint
            and _is_exact_int(metadata.get("exit_code"), 0)
            and _valid_nonnegative_number(metadata.get("execution_ms"))
        )
    valid = trusted and identity_matches
    if valid:
        return metadata
    store.quarantine_entry(key, {"task": task.name, "key": key, "reason": "invalid hermetic result metadata"})
    return None


def _save_result_entry(
    store: Store,
    task: TaskSpec,
    key: str,
    fingerprint: dict[str, Any],
    *,
    result_identity: PytestNodeResultIdentity | None = None,
    execution_ms: float,
    trust_session: CacheTrustSession | None = None,
) -> dict[str, Any]:
    store.ensure()
    fingerprint_schema = _fingerprint_schema(fingerprint)
    compact_storage_schema: int | None = None
    if fingerprint_schema == 6:
        if result_identity is not None:
            raise ConfigurationError(
                "legacy pytest node fingerprint cannot carry a schema-9 identity"
            )
        compact_identity = _schema_six_identity(task, key, fingerprint)
        compact_storage_schema = _COMPACT_RESULT_STORAGE_SCHEMA
    elif fingerprint_schema == PYTEST_NODE_ACTION_SCHEMA:
        derived_identity = validate_pytest_node_action_identity(
            task_name=task.name,
            key=key,
            fingerprint=fingerprint,
        )
        if result_identity is not None and result_identity != derived_identity:
            raise ConfigurationError(
                "pytest node carried identity does not match its compact Merkle fingerprint"
            )
        compact_identity = derived_identity.metadata_fields()
        compact_storage_schema = _MERKLE_RESULT_STORAGE_SCHEMA
    else:
        if result_identity is not None:
            raise ConfigurationError(
                "non-pytest result fingerprint cannot carry a pytest node identity"
            )
        compact_identity = None
    if not _valid_nonnegative_number(execution_ms):
        raise ConfigurationError(
            "hermetic result execution time must be finite and nonnegative"
        )
    final_entry = store.entry(key)
    if final_entry.exists():
        existing = _load_result_entry(
            store,
            task,
            key,
            fingerprint,
            result_identity=result_identity,
            trust_session=trust_session,
        )
        if existing is not None:
            return existing
        raise ConfigurationError("cache key exists but is not a valid hermetic result entry")
    if compact_identity is not None:
        metadata = {
            "schema": fingerprint_schema,
            "storage_schema": compact_storage_schema,
            "kind": "hermetic-result-only",
            "task": task.name,
            "key": key,
            **compact_identity,
            "created_at": time.time(),
            "execution_ms": execution_ms,
            "exit_code": 0,
        }
    else:
        metadata = {
            "schema": fingerprint_schema,
            "kind": "hermetic-result-only",
            "task": task.name,
            "key": key,
            "created_at": time.time(),
            "execution_ms": execution_ms,
            "fingerprint": fingerprint,
            "exit_code": 0,
        }
    profile_digest = _profile_digest(fingerprint)
    if trust_session is None:
        metadata["provenance"] = sign_cache_payload(
            store.root,
            metadata,
            manifest_digest=manifest_sha256_for_root(store.root),
            profile_digest=profile_digest,
        )
    else:
        metadata["provenance"] = trust_session.sign_payload(
            metadata,
            manifest_digest=trust_session.manifest_digest,
            profile_digest=profile_digest,
        )
    with private_temporary_directory(store.state, prefix="zerorun-result-") as temp:
        (temp / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        if os.name != "nt":
            os.chmod(temp / "metadata.json", 0o600)
        try:
            os.replace(temp, final_entry)
        except OSError:
            existing = _load_result_entry(
                store,
                task,
                key,
                fingerprint,
                result_identity=result_identity,
                trust_session=trust_session,
            )
            if existing is not None:
                return existing
            raise
    return metadata
