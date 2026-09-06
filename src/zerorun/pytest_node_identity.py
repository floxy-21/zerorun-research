from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .model import ConfigurationError


PYTEST_NODE_ACTION_SCHEMA = 9
PYTEST_NODE_ACTION_DOMAIN = "zerorun-pytest-node-action-merkle-v1"
PYTEST_NODE_ACTION_KEY_DOMAIN = "zerorun-pytest-node-action-key-v1"
PYTEST_NODE_COMPONENT_DOMAIN = "zerorun-pytest-node-action-component-v1"
PYTEST_NODE_COMPONENT_REFERENCE_DOMAIN = (
    "zerorun-pytest-node-action-component-reference-v1"
)
PYTEST_NODE_RESULT_STORAGE_SCHEMA = 2

_MAX_NODEID_CHARS = 8192
_MAX_COMPONENT_COUNT = (1 << 63) - 1
_LOWER_HEX = frozenset("0123456789abcdef")
_COMPONENT_COUNT_FIELDS = {
    "command": frozenset({"arguments"}),
    "collection": frozenset({"items"}),
    "node_closure": frozenset({"selectors", "fallback_files", "absent_files"}),
    "session_closure": frozenset(
        {"selectors", "fallback_files", "absent_files"}
    ),
    "static_inputs": frozenset({"records"}),
    "environment": frozenset({"variables"}),
    "runtime_identity": frozenset({"fields"}),
}
_ACTION_FIELDS = frozenset(
    {
        "schema",
        "kind",
        "action_domain",
        "task",
        "workload",
        "nodeid",
        "profile_sha256",
        "node_independence_review_sha256",
        "components",
        "policy",
    }
)
_COMPONENT_REFERENCE_FIELDS = frozenset(
    {"domain", "label", "sha256", "counts"}
)
_POLICY = {
    "cacheable": True,
    "result_only": True,
    "closure_reviewed": True,
    "unsafe_effects": [],
    "network": "none",
    "checkout": "read-only",
    "collection_race": "full-contract-checked-request-locally",
    "uncertainty": "fresh",
}


@dataclass(frozen=True)
class PytestNodeResultIdentity:
    """Small derived identity carried beside a freshly built node action.

    It is an optimization hint, not independent authority. Persistent lookup
    independently validates the compact Merkle root before checking HMAC
    provenance, so a forged or stale instance can only force a safe miss.
    """

    fingerprint_schema: int
    storage_schema: int
    fingerprint_sha256: str
    nodeid: str
    profile_sha256: str
    runtime_identity_sha256: str

    def metadata_fields(self) -> dict[str, str]:
        return {
            "fingerprint_sha256": self.fingerprint_sha256,
            "nodeid": self.nodeid,
            "profile_sha256": self.profile_sha256,
            "runtime_identity_sha256": self.runtime_identity_sha256,
        }


@dataclass(frozen=True)
class PytestNodeComponentIdentity:
    """Immutable request-local representation of one Merkle component."""

    label: str
    sha256: str
    counts: tuple[tuple[str, int], ...]

    def reference(self) -> dict[str, Any]:
        return {
            "domain": PYTEST_NODE_COMPONENT_REFERENCE_DOMAIN,
            "label": self.label,
            "sha256": self.sha256,
            "counts": dict(self.counts),
        }


def _is_lower_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _LOWER_HEX for character in value)
    )


def _canonical_sha256(value: object, *, label: str) -> str:
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


def pytest_node_component_identity(
    label: str,
    value: object,
    *,
    counts: dict[str, int],
) -> PytestNodeComponentIdentity:
    """Return an immutable domain-separated identity for one component."""

    expected = _COMPONENT_COUNT_FIELDS.get(label)
    if expected is None or type(counts) is not dict or set(counts) != expected:
        raise ConfigurationError("pytest node Merkle component counts are malformed")
    for count in counts.values():
        if type(count) is not int or count < 0 or count > _MAX_COMPONENT_COUNT:
            raise ConfigurationError("pytest node Merkle component count is malformed")
    canonical_counts = {name: counts[name] for name in sorted(counts)}
    digest = _canonical_sha256(
        {
            "domain": PYTEST_NODE_COMPONENT_DOMAIN,
            "label": label,
            "counts": canonical_counts,
            "value": value,
        },
        label=f"pytest node {label} component",
    )
    return PytestNodeComponentIdentity(
        label=label,
        sha256=digest,
        counts=tuple(canonical_counts.items()),
    )


def pytest_node_component_reference(
    label: str,
    value: object,
    *,
    counts: dict[str, int],
) -> dict[str, Any]:
    """Return the canonical JSON reference for one action component."""

    return pytest_node_component_identity(label, value, counts=counts).reference()


def pytest_node_action_key(fingerprint: dict[str, Any]) -> str:
    """Hash the compact Merkle root with an action-specific outer domain."""

    return _canonical_sha256(
        {
            "domain": PYTEST_NODE_ACTION_KEY_DOMAIN,
            "fingerprint": fingerprint,
        },
        label="pytest node compact Merkle fingerprint",
    )


def validate_pytest_node_action_identity(
    *,
    task_name: str,
    key: str,
    fingerprint: dict[str, Any],
) -> PytestNodeResultIdentity:
    """Validate schema 9 and independently derive its compact store identity."""

    if type(fingerprint) is not dict or set(fingerprint) != _ACTION_FIELDS:
        raise ConfigurationError("pytest node compact Merkle fingerprint shape is malformed")
    if type(fingerprint.get("schema")) is not int or fingerprint["schema"] != 9:
        raise ConfigurationError("pytest node compact Merkle fingerprint schema is malformed")
    if fingerprint.get("kind") != "hermetic-result-only":
        raise ConfigurationError("pytest node compact Merkle fingerprint kind is malformed")
    if fingerprint.get("action_domain") != PYTEST_NODE_ACTION_DOMAIN:
        raise ConfigurationError("pytest node compact Merkle action domain is malformed")
    if fingerprint.get("task") != task_name:
        raise ConfigurationError("pytest node compact Merkle fingerprint task is malformed")
    if fingerprint.get("workload") != "pytest-node-generic-v1":
        raise ConfigurationError("pytest node compact Merkle workload is malformed")

    nodeid = fingerprint.get("nodeid")
    if (
        not isinstance(nodeid, str)
        or not nodeid
        or len(nodeid) > _MAX_NODEID_CHARS
        or "\x00" in nodeid
    ):
        raise ConfigurationError("pytest node compact Merkle node id is malformed")
    profile_sha256 = fingerprint.get("profile_sha256")
    review_sha256 = fingerprint.get("node_independence_review_sha256")
    if not _is_lower_sha256(profile_sha256) or not _is_lower_sha256(review_sha256):
        raise ConfigurationError("pytest node compact Merkle review identity is malformed")
    policy = fingerprint.get("policy")
    if (
        type(policy) is not dict
        or set(policy) != set(_POLICY)
        or policy.get("cacheable") is not True
        or policy.get("result_only") is not True
        or policy.get("closure_reviewed") is not True
        or type(policy.get("unsafe_effects")) is not list
        or policy.get("unsafe_effects") != []
        or policy.get("network") != "none"
        or policy.get("checkout") != "read-only"
        or policy.get("collection_race")
        != "full-contract-checked-request-locally"
        or policy.get("uncertainty") != "fresh"
    ):
        raise ConfigurationError("pytest node compact Merkle policy is malformed")

    components = fingerprint.get("components")
    if type(components) is not dict or set(components) != set(_COMPONENT_COUNT_FIELDS):
        raise ConfigurationError("pytest node compact Merkle component set is malformed")
    for label, expected_count_fields in _COMPONENT_COUNT_FIELDS.items():
        reference = components.get(label)
        if type(reference) is not dict or set(reference) != _COMPONENT_REFERENCE_FIELDS:
            raise ConfigurationError(
                f"pytest node compact Merkle {label} reference is malformed"
            )
        if (
            reference.get("domain") != PYTEST_NODE_COMPONENT_REFERENCE_DOMAIN
            or reference.get("label") != label
            or not _is_lower_sha256(reference.get("sha256"))
        ):
            raise ConfigurationError(
                f"pytest node compact Merkle {label} reference is malformed"
            )
        counts = reference.get("counts")
        if type(counts) is not dict or set(counts) != expected_count_fields:
            raise ConfigurationError(
                f"pytest node compact Merkle {label} counts are malformed"
            )
        if any(
            type(count) is not int or count < 0 or count > _MAX_COMPONENT_COUNT
            for count in counts.values()
        ):
            raise ConfigurationError(
                f"pytest node compact Merkle {label} count is malformed"
            )
    if components["command"]["counts"]["arguments"] < 1:
        raise ConfigurationError("pytest node compact Merkle command is empty")
    if components["collection"]["counts"]["items"] != 1:
        raise ConfigurationError("pytest node compact Merkle collection count is malformed")
    for label in ("node_closure", "session_closure"):
        if sum(components[label]["counts"].values()) < 1:
            raise ConfigurationError(f"pytest node compact Merkle {label} is empty")

    expected_key = pytest_node_action_key(fingerprint)
    if not _is_lower_sha256(key) or expected_key != key:
        raise ConfigurationError(
            "pytest node cache key does not match its compact Merkle fingerprint"
        )
    return PytestNodeResultIdentity(
        fingerprint_schema=PYTEST_NODE_ACTION_SCHEMA,
        storage_schema=PYTEST_NODE_RESULT_STORAGE_SCHEMA,
        fingerprint_sha256=expected_key,
        nodeid=nodeid,
        profile_sha256=profile_sha256,
        runtime_identity_sha256=components["runtime_identity"]["sha256"],
    )


def pytest_node_policy() -> dict[str, Any]:
    """Return a fresh canonical policy object for a schema-9 fingerprint."""

    return {**_POLICY, "unsafe_effects": []}
