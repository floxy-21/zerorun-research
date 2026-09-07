from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .hermetic_batch_key import FingerprintSession
from .hermetic_store import _load_result_entry, _save_result_entry
from .model import ConfigurationError, TaskSpec
from .pytest_node_identity import (
    PYTEST_NODE_ACTION_SCHEMA,
    PytestNodeResultIdentity,
    validate_pytest_node_action_identity,
)
from .pytest_node_key import (
    PytestNodeSharedActionIdentity,
    build_pytest_node_shared_action_identity,
    node_action_fingerprint,
)
from .store import Store
from .trust import CacheTrustSession, manifest_sha256_for_root


@dataclass
class NodeCacheDecision:
    nodeid: str
    cache_task: TaskSpec | None
    key: str | None
    fingerprint: dict[str, Any] | None
    status: str
    reason: str | None
    observed_collection_contract_sha256: str | None = None
    cached: dict[str, Any] | None = None
    result_identity: PytestNodeResultIdentity | None = None


@dataclass(frozen=True)
class NodeCacheContext:
    runtime_identity: dict[str, Any]
    environment_fingerprint: dict[str, dict[str, Any]]
    collection_contract_sha256: str
    collection_plugin_sha256: str
    independence_review_sha256: str
    profile_sha256: str


def _collection_by_node(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("nodeid"), str):
            raise ConfigurationError("pytest collection contains an invalid item")
        nodeid = str(item["nodeid"])
        if nodeid in result:
            raise ConfigurationError(f"pytest collection contains duplicate nodeid {nodeid!r}")
        result[nodeid] = item
    return result


def prepare_node_cache(
    root: Path,
    task: TaskSpec,
    *,
    node_rows: list[dict[str, Any]],
    session_closure: Mapping[str, Any],
    static_inputs: tuple[str, ...],
    collection_items: list[dict[str, Any]],
    context: NodeCacheContext,
    store: Store | None = None,
    session: FingerprintSession | None = None,
    trust_session: CacheTrustSession | None = None,
    trust_session_provider: Callable[[], CacheTrustSession] | None = None,
    allow_cache_lookup: bool = True,
) -> list[NodeCacheDecision]:
    root = root.resolve(strict=True)
    store = store or Store(root)
    session = session or FingerprintSession(root)
    session.validate_root(root)
    collection = _collection_by_node(collection_items)
    decisions: list[NodeCacheDecision] = []
    shared_identity: PytestNodeSharedActionIdentity | None = None
    shared_error: str | None = None
    if any(
        row.get("fresh_required") is not True and row.get("reviewable") is True
        for row in node_rows
        if isinstance(row, Mapping)
    ):
        try:
            shared_identity = build_pytest_node_shared_action_identity(
                root,
                task,
                session_closure=session_closure,
                static_inputs=static_inputs,
                runtime_identity=context.runtime_identity,
                environment_fingerprint=context.environment_fingerprint,
                session=session,
            )
        except (ConfigurationError, RuntimeError) as exc:
            shared_error = str(exc)

    for row in node_rows:
        nodeid = str(row.get("nodeid") or "")
        if not nodeid:
            raise ConfigurationError("reviewed pytest node closure has no nodeid")
        if row.get("fresh_required") is True or row.get("reviewable") is not True:
            decisions.append(
                NodeCacheDecision(
                    nodeid=nodeid,
                    cache_task=None,
                    key=None,
                    fingerprint=None,
                    status="MISS_UNCERTAIN",
                    reason=str(row.get("reason") or "reviewed node requires fresh execution"),
                )
            )
            continue
        item = collection.get(nodeid)
        if item is None:
            decisions.append(
                NodeCacheDecision(
                    nodeid=nodeid,
                    cache_task=None,
                    key=None,
                    fingerprint=None,
                    status="MISS_UNCERTAIN",
                    reason="node is absent from the current exact collection",
                )
            )
            continue
        if shared_error is not None:
            decisions.append(
                NodeCacheDecision(
                    nodeid=nodeid,
                    cache_task=None,
                    key=None,
                    fingerprint=None,
                    status="MISS_UNCERTAIN",
                    reason=f"node action identity is uncertain: {shared_error}",
                )
            )
            continue
        assert shared_identity is not None
        try:
            cache_task, key, fingerprint = node_action_fingerprint(
                root,
                task,
                nodeid=nodeid,
                collection_item=item,
                collection_contract_sha256=context.collection_contract_sha256,
                collection_plugin_sha256=context.collection_plugin_sha256,
                node_closure=row,
                session_closure=session_closure,
                static_inputs=static_inputs,
                runtime_identity=context.runtime_identity,
                environment_fingerprint=context.environment_fingerprint,
                independence_review_sha256=context.independence_review_sha256,
                profile_sha256=context.profile_sha256,
                session=session,
                shared_identity=shared_identity,
            )
            result_identity = (
                validate_pytest_node_action_identity(
                    task_name=cache_task.name,
                    key=key,
                    fingerprint=fingerprint,
                )
                if fingerprint.get("schema") == PYTEST_NODE_ACTION_SCHEMA
                else None
            )
        except (ConfigurationError, RuntimeError) as exc:
            decisions.append(
                NodeCacheDecision(
                    nodeid=nodeid,
                    cache_task=None,
                    key=None,
                    fingerprint=None,
                    status="MISS_UNCERTAIN",
                    reason=f"node action identity is uncertain: {exc}",
                )
            )
            continue
        cached = (
            _load_result_entry(
                store,
                cache_task,
                key,
                fingerprint,
                result_identity=result_identity,
                trust_session=trust_session,
                trust_session_provider=trust_session_provider,
            )
            if allow_cache_lookup
            else None
        )
        decisions.append(
            NodeCacheDecision(
                nodeid=nodeid,
                cache_task=cache_task,
                key=key,
                fingerprint=fingerprint,
                status="HIT_PROVISIONAL" if cached is not None else "MISS_KEYED",
                reason=None,
                observed_collection_contract_sha256=context.collection_contract_sha256,
                cached=cached,
                result_identity=result_identity,
            )
        )
    return decisions


def verify_node_cache_snapshot(
    root: Path,
    task: TaskSpec,
    *,
    decisions: list[NodeCacheDecision],
    node_rows: list[dict[str, Any]],
    session_closure: Mapping[str, Any],
    static_inputs: tuple[str, ...],
    collection_items: list[dict[str, Any]],
    final_context: NodeCacheContext,
    session: FingerprintSession | None = None,
) -> list[NodeCacheDecision]:
    root = root.resolve(strict=True)
    session = session or FingerprintSession(root)
    session.validate_root(root)
    collection = _collection_by_node(collection_items)
    rows = {
        str(row["nodeid"]): row
        for row in node_rows
        if isinstance(row, Mapping) and isinstance(row.get("nodeid"), str)
    }
    verified: list[NodeCacheDecision] = []
    final_shared_identity: PytestNodeSharedActionIdentity | None = None
    final_shared_error: str | None = None
    if any(
        decision.key is not None
        and decision.fingerprint is not None
        and decision.cache_task is not None
        for decision in decisions
    ):
        try:
            final_shared_identity = build_pytest_node_shared_action_identity(
                root,
                task,
                session_closure=session_closure,
                static_inputs=static_inputs,
                runtime_identity=final_context.runtime_identity,
                environment_fingerprint=final_context.environment_fingerprint,
                session=session,
            )
        except (ConfigurationError, RuntimeError) as exc:
            final_shared_error = str(exc)

    for decision in decisions:
        if decision.key is None or decision.fingerprint is None or decision.cache_task is None:
            verified.append(decision)
            continue
        if decision.observed_collection_contract_sha256 != final_context.collection_contract_sha256:
            verified.append(
                NodeCacheDecision(
                    **{
                        **decision.__dict__,
                        "status": "REJECTED_INPUT_RACE",
                        "reason": "full pytest collection snapshot changed during request",
                    }
                )
            )
            continue
        row = rows.get(decision.nodeid)
        item = collection.get(decision.nodeid)
        if row is None or item is None:
            verified.append(
                NodeCacheDecision(
                    **{
                        **decision.__dict__,
                        "status": "REJECTED_INPUT_RACE",
                        "reason": "node or reviewed closure disappeared before final snapshot",
                    }
                )
            )
            continue
        if final_shared_error is not None:
            verified.append(
                NodeCacheDecision(
                    **{
                        **decision.__dict__,
                        "status": "REJECTED_INPUT_RACE",
                        "reason": (
                            "final node action identity is uncertain: "
                            f"{final_shared_error}"
                        ),
                    }
                )
            )
            continue
        assert final_shared_identity is not None
        try:
            final_task, final_key, final_fingerprint = node_action_fingerprint(
                root,
                task,
                nodeid=decision.nodeid,
                collection_item=item,
                collection_contract_sha256=final_context.collection_contract_sha256,
                collection_plugin_sha256=final_context.collection_plugin_sha256,
                node_closure=row,
                session_closure=session_closure,
                static_inputs=static_inputs,
                runtime_identity=final_context.runtime_identity,
                environment_fingerprint=final_context.environment_fingerprint,
                independence_review_sha256=final_context.independence_review_sha256,
                profile_sha256=final_context.profile_sha256,
                session=session,
                shared_identity=final_shared_identity,
            )
            final_result_identity = (
                validate_pytest_node_action_identity(
                    task_name=final_task.name,
                    key=final_key,
                    fingerprint=final_fingerprint,
                )
                if final_fingerprint.get("schema") == PYTEST_NODE_ACTION_SCHEMA
                else None
            )
        except (ConfigurationError, RuntimeError) as exc:
            verified.append(
                NodeCacheDecision(
                    **{
                        **decision.__dict__,
                        "status": "REJECTED_INPUT_RACE",
                        "reason": f"final node action identity is uncertain: {exc}",
                    }
                )
            )
            continue
        if (
            final_task != decision.cache_task
            or final_key != decision.key
            or final_fingerprint != decision.fingerprint
            or final_result_identity != decision.result_identity
        ):
            verified.append(
                NodeCacheDecision(
                    **{
                        **decision.__dict__,
                        "status": "REJECTED_INPUT_RACE",
                        "reason": "node action identity changed during request",
                    }
                )
            )
            continue
        verified.append(
            NodeCacheDecision(
                **{
                    **decision.__dict__,
                    "status": "HIT_REUSED" if decision.cached is not None else "MISS_VERIFIED",
                    "reason": None,
                }
            )
        )
    return verified


def publish_verified_successes(
    root: Path,
    decisions: list[NodeCacheDecision],
    *,
    successful_nodeids: set[str],
    execution_ms_by_node: dict[str, float] | None = None,
    store: Store | None = None,
    manifest_digest: str | None = None,
) -> int:
    root = root.resolve(strict=True)
    store = store or Store(root)
    execution_ms_by_node = execution_ms_by_node or {}
    candidates = [
        decision
        for decision in decisions
        if decision.status == "MISS_VERIFIED"
        and decision.nodeid in successful_nodeids
        and decision.cache_task is not None
        and decision.key is not None
        and decision.fingerprint is not None
    ]
    if not candidates:
        return 0
    profile_digests = {
        str(decision.fingerprint.get("profile_sha256"))
        for decision in candidates
        if decision.fingerprint is not None
    }
    if len(profile_digests) != 1:
        raise ConfigurationError(
            "one pytest cache publication batch must use one exact profile digest"
        )
    profile_digest = next(iter(profile_digests))
    manifest_digest = manifest_digest or manifest_sha256_for_root(root)
    trust_session = CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=profile_digest,
        create_secret=True,
    )
    touched: list[NodeCacheDecision] = []
    try:
        for decision in candidates:
            assert decision.cache_task is not None
            assert decision.key is not None
            assert decision.fingerprint is not None
            _save_result_entry(
                store,
                decision.cache_task,
                decision.key,
                decision.fingerprint,
                result_identity=decision.result_identity,
                execution_ms=max(
                    0.0,
                    float(execution_ms_by_node.get(decision.nodeid, 0.0)),
                ),
                trust_session=trust_session,
            )
            touched.append(decision)
    except Exception as exc:
        trust_session.abort()
        for decision in touched:
            assert decision.cache_task is not None
            assert decision.key is not None
            store.quarantine_entry(
                decision.key,
                {
                    "task": decision.cache_task.name,
                    "key": decision.key,
                    "reason": f"incomplete cache publication batch: {exc}",
                },
            )
        raise
    try:
        trust_session.verify_unchanged()
    except ConfigurationError as exc:
        # Nothing from a publication phase is usable until its postcondition
        # holds. Mark every touched entry so a later stable request cannot load
        # residue from a raced batch.
        for decision in touched:
            assert decision.cache_task is not None
            assert decision.key is not None
            store.quarantine_entry(
                decision.key,
                {
                    "task": decision.cache_task.name,
                    "key": decision.key,
                    "reason": f"cache publication trust drift: {exc}",
                },
            )
        return 0
    return len(touched)


def initial_fresh_nodeids(decisions: list[NodeCacheDecision]) -> list[str]:
    return [
        decision.nodeid
        for decision in decisions
        if decision.status in {"MISS_UNCERTAIN", "MISS_KEYED"}
    ]
