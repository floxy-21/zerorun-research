from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .hermetic_batch_key import FingerprintSession, _symbol_records_cached
from .hermetic_key import _content_only, _symbol_content_only
from .model import ConfigurationError, TaskSpec
from .pytest_node_identity import (
    PYTEST_NODE_ACTION_DOMAIN,
    PYTEST_NODE_ACTION_SCHEMA,
    PytestNodeComponentIdentity,
    pytest_node_action_key,
    pytest_node_component_identity,
    pytest_node_component_reference,
    pytest_node_policy,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PYTEST_SOURCE_REVIEW_SCHEMA = "zerorun-pytest-source-review-v2"


@dataclass(frozen=True)
class PytestNodeSharedActionIdentity:
    """Immutable common Merkle leaves for one explicit source-snapshot pass."""

    carrier_task_name: str
    command: PytestNodeComponentIdentity
    session_closure: PytestNodeComponentIdentity
    static_inputs: PytestNodeComponentIdentity
    environment: PytestNodeComponentIdentity
    runtime_identity: PytestNodeComponentIdentity


def _closure_counts(value: Mapping[str, Any]) -> dict[str, int]:
    return {
        "selectors": len(value["selectors"]),
        "fallback_files": len(value["fallback_files"]),
        "absent_files": len(value["absent_files"]),
    }


def node_cache_task(task: TaskSpec, nodeid: str) -> TaskSpec:
    if not nodeid:
        raise ConfigurationError("pytest node cache identity requires a nodeid")
    suffix = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:24]
    return replace(task, name=f"{task.name}::pytest-node::{suffix}", cacheable=True)


def _closure_fingerprint(
    root: Path,
    row: Mapping[str, Any],
    *,
    session: FingerprintSession,
) -> tuple[str, dict[str, Any]]:
    selectors_raw = row.get("selectors")
    fallback_raw = row.get("fallback_files")
    absent_raw = row.get("absent_files", ())
    if not isinstance(selectors_raw, (list, tuple)) or not all(isinstance(item, str) for item in selectors_raw):
        raise ConfigurationError("reviewed pytest closure has invalid selectors")
    if not isinstance(fallback_raw, (list, tuple)) or not all(isinstance(item, str) for item in fallback_raw):
        raise ConfigurationError("reviewed pytest closure has invalid fallback_files")
    if not isinstance(absent_raw, (list, tuple)) or not all(
        isinstance(item, str) for item in absent_raw
    ):
        raise ConfigurationError("reviewed pytest closure has invalid absent_files")
    selectors = tuple(sorted(set(selectors_raw)))
    fallback_files = tuple(sorted(set(fallback_raw)))
    absent_files = tuple(sorted(set(absent_raw)))
    if not selectors and not fallback_files and not absent_files:
        raise ConfigurationError("reviewed pytest closure is empty")

    # Keep the request-local cache's two-tuple representation while making the
    # positive and negative path domains unambiguous.
    cache_key = (
        selectors,
        tuple(f"present:{item}" for item in fallback_files)
        + tuple(f"absent:{item}" for item in absent_files),
    )
    failed = session.pytest_closure_failures.get(cache_key)
    if failed is not None:
        # A FingerprintSession is one explicit source-snapshot pass. Replaying
        # the same deterministic fail-closed result inside that pass avoids
        # reparsing a shared invalid closure for every node. The caller still
        # receives an error, so a cached failure can never produce a key/hit.
        raise ConfigurationError(failed)
    cached = session.pytest_closure_fingerprints.get(cache_key)
    if cached is not None:
        return cached

    try:
        fallback_records = _content_only(
            session.expand_inputs(root, fallback_files)
        )
        for record in fallback_records:
            if record.get("type") in {"missing", "symlink", "unsupported"}:
                raise ConfigurationError(
                    "reviewed pytest closure fallback became unavailable or unsupported: "
                    + str(record.get("path") or record.get("pattern"))
                )
        absent_records = _content_only(
            session.expand_expected_absent_inputs(root, absent_files)
        )
        expected_absent_records = [
            {"pattern": path, "type": "missing"} for path in absent_files
        ]
        if absent_records != expected_absent_records:
            raise ConfigurationError(
                "reviewed pytest negative import candidate became available or unsupported"
            )
        symbol_records = _symbol_content_only(
            _symbol_records_cached(root, selectors, session=session)
        )
    except ConfigurationError as exc:
        session.pytest_closure_failures[cache_key] = str(exc)
        raise
    payload = {
        "kind": "pytest-reviewed-project-closure-v2",
        "fallback_files": fallback_records,
        "absent_files": absent_records,
        "selectors": symbol_records,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    result = (hashlib.sha256(encoded).hexdigest(), payload)
    session.pytest_closure_fingerprints[cache_key] = result
    return result


def _static_input_records(
    root: Path,
    inputs: tuple[str, ...],
    *,
    session: FingerprintSession,
) -> list[dict[str, Any]]:
    cache_key = tuple(inputs)
    cached = session.pytest_static_input_records.get(cache_key)
    if cached is not None:
        return cached

    records = _content_only(
        session.expand_inputs(root, inputs)
    )
    for record in records:
        if record.get("type") in {"missing", "symlink", "unsupported"}:
            raise ConfigurationError(
                "pytest adapter static input became unavailable or unsupported: "
                + str(record.get("path") or record.get("pattern"))
            )
    session.pytest_static_input_records[cache_key] = records
    return records


def build_pytest_node_shared_action_identity(
    root: Path,
    task: TaskSpec,
    *,
    session_closure: Mapping[str, Any],
    static_inputs: tuple[str, ...],
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    session: FingerprintSession,
) -> PytestNodeSharedActionIdentity:
    """Build common leaves exactly once inside one initial/final snapshot pass."""

    root = root.resolve(strict=True)
    session.validate_root(root)
    session_sha, session_payload = _closure_fingerprint(
        root,
        session_closure,
        session=session,
    )
    static_records = _static_input_records(root, static_inputs, session=session)
    return PytestNodeSharedActionIdentity(
        carrier_task_name=task.name,
        command=pytest_node_component_identity(
            "command",
            list(task.command),
            counts={"arguments": len(task.command)},
        ),
        session_closure=pytest_node_component_identity(
            "session_closure",
            {
                "kind": "pytest-reviewed-project-closure-v2",
                "closure_sha256": session_sha,
            },
            counts=_closure_counts(session_payload),
        ),
        static_inputs=pytest_node_component_identity(
            "static_inputs",
            {
                "kind": "pytest-static-input-review-v1",
                "records": static_records,
            },
            counts={"records": len(static_records)},
        ),
        environment=pytest_node_component_identity(
            "environment",
            environment_fingerprint,
            counts={"variables": len(environment_fingerprint)},
        ),
        runtime_identity=pytest_node_component_identity(
            "runtime_identity",
            runtime_identity,
            counts={"fields": len(runtime_identity)},
        ),
    )


def build_pytest_source_review(
    root: Path,
    *,
    static_inputs: tuple[str, ...],
    session_closure: Mapping[str, Any],
    nodes: Mapping[str, Mapping[str, Any]],
    profiler_sha256: str,
    collection_plugin_sha256: str,
    session: FingerprintSession | None = None,
    fail_on_node_error: bool = True,
) -> dict[str, Any]:
    """Bind review authority to the exact source/configuration it covered.

    Dynamic profiling and human review establish a dependency closure at one
    concrete source state. A later edit can introduce a new call, import, data
    read, fixture, or hook that is absent from that reviewed closure. Hashing
    the complete reviewed closure here is intentionally stricter than a
    semantic optimization: changed nodes stay fresh and cannot publish until
    explicit requalification supplies new review authority.
    """

    root = root.resolve(strict=True)
    for label, value in (
        ("profiler", profiler_sha256),
        ("collection plugin", collection_plugin_sha256),
    ):
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ConfigurationError(
                f"pytest source review {label} identity is missing or malformed"
            )
    session = session or FingerprintSession(root)
    session.validate_root(root)
    session_sha256, _ = _closure_fingerprint(
        root,
        session_closure,
        session=session,
    )
    static_records = _static_input_records(
        root,
        static_inputs,
        session=session,
    )
    static_payload = {
        "kind": "pytest-static-input-review-v1",
        "records": static_records,
    }
    static_sha256 = hashlib.sha256(
        json.dumps(
            static_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    node_anchors: dict[str, str] = {}
    for nodeid, row in sorted(nodes.items()):
        if not isinstance(nodeid, str) or not isinstance(row, Mapping):
            raise ConfigurationError(
                "pytest source review contains malformed node evidence"
            )
        if row.get("reviewable") is True and row.get("fresh_required") is not True:
            try:
                node_sha256, _ = _closure_fingerprint(
                    root,
                    row,
                    session=session,
                )
            except (ConfigurationError, RuntimeError):
                if fail_on_node_error:
                    raise
                continue
            node_anchors[nodeid] = node_sha256
    return {
        "schema": PYTEST_SOURCE_REVIEW_SCHEMA,
        "profiler_sha256": profiler_sha256,
        "collection_plugin_sha256": collection_plugin_sha256,
        "static_inputs_sha256": static_sha256,
        "session_closure_sha256": session_sha256,
        "node_closure_sha256": node_anchors,
    }


def node_action_fingerprint(
    root: Path,
    task: TaskSpec,
    *,
    nodeid: str,
    collection_item: dict[str, Any],
    collection_contract_sha256: str,
    collection_plugin_sha256: str,
    node_closure: Mapping[str, Any],
    session_closure: Mapping[str, Any],
    static_inputs: tuple[str, ...],
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    independence_review_sha256: str,
    profile_sha256: str,
    session: FingerprintSession,
    shared_identity: PytestNodeSharedActionIdentity | None = None,
) -> tuple[TaskSpec, str, dict[str, Any]]:
    root = root.resolve(strict=True)
    session.validate_root(root)
    if not task.result_only or not task.closure_reviewed:
        raise ConfigurationError("pytest node action requires a reviewed result-only carrier task")
    if task.unsafe_effects:
        raise ConfigurationError("pytest node action refuses unsafe effects")
    for label, value in (
        ("collection contract", collection_contract_sha256),
        ("collection plugin", collection_plugin_sha256),
        ("node independence review", independence_review_sha256),
        ("pytest profile", profile_sha256),
    ):
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ConfigurationError(f"pytest {label} identity is missing or malformed")

    if collection_item.get("nodeid") != nodeid:
        raise ConfigurationError("pytest collection row does not match nodeid")
    identity = collection_item.get("identity_sha256")
    if not isinstance(identity, str) or not _SHA256_RE.fullmatch(identity):
        raise ConfigurationError("pytest node structural collection identity is missing")
    if collection_item.get("parameter_identity_supported") is not True:
        raise ConfigurationError("pytest node parameter identity is unsupported")

    # The caller normally supplies one immutable set of common leaves for the
    # whole request. Direct callers retain the same semantics by building one
    # here. Initial and final verification use distinct objects and sessions.
    if shared_identity is None:
        shared_identity = build_pytest_node_shared_action_identity(
            root,
            task,
            session_closure=session_closure,
            static_inputs=static_inputs,
            runtime_identity=runtime_identity,
            environment_fingerprint=environment_fingerprint,
            session=session,
        )
    elif shared_identity.carrier_task_name != task.name:
        raise ConfigurationError("pytest node shared action identity belongs to another task")
    node_sha, node_payload = _closure_fingerprint(root, node_closure, session=session)
    cache_task = node_cache_task(task, nodeid)

    # Schema 9 is a compact Merkle action identity. Full source, static,
    # environment, runtime, and command evidence is still read and hashed in
    # this source-snapshot pass, but the node key retains only domain-separated
    # component digests and their exact cardinalities. This removes repeated
    # multi-megabyte closure preimages without removing any action binding.
    collection_value = {
        "nodeid": nodeid,
        "identity_sha256": identity,
        "identity_model": "generic-structural-v1",
        "identity_plugin_sha256": collection_plugin_sha256,
    }
    payload: dict[str, Any] = {
        "schema": PYTEST_NODE_ACTION_SCHEMA,
        "kind": "hermetic-result-only",
        "action_domain": PYTEST_NODE_ACTION_DOMAIN,
        "task": cache_task.name,
        "workload": "pytest-node-generic-v1",
        "nodeid": nodeid,
        "profile_sha256": profile_sha256,
        "node_independence_review_sha256": independence_review_sha256,
        "components": {
            "command": shared_identity.command.reference(),
            "collection": pytest_node_component_reference(
                "collection",
                collection_value,
                counts={"items": 1},
            ),
            "node_closure": pytest_node_component_reference(
                "node_closure",
                {
                    "kind": "pytest-reviewed-project-closure-v2",
                    "closure_sha256": node_sha,
                },
                counts=_closure_counts(node_payload),
            ),
            "session_closure": shared_identity.session_closure.reference(),
            "static_inputs": shared_identity.static_inputs.reference(),
            "environment": shared_identity.environment.reference(),
            "runtime_identity": shared_identity.runtime_identity.reference(),
        },
        "policy": pytest_node_policy(),
    }
    return cache_task, pytest_node_action_key(payload), payload
