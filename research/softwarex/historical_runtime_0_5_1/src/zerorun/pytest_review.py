from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from .bounded_json import JsonLimits, loads_bounded_json
from .model import ConfigurationError
from .path_safety import is_link_like, private_temporary_directory
from .pytest_qualify import activate_pytest_candidate

CANDIDATE_SCHEMA = "zerorun-pytest-candidate-v1"
REVIEW_SCHEMA = "zerorun-pytest-review-v1"
DEFAULT_CANDIDATE = ".zerorun-pytest.candidate.json"
DEFAULT_REVIEW = ".zerorun-pytest.review.json"
DEFAULT_PROFILE = ".zerorun-pytest.json"
_MAX_REVIEW_JSON_BYTES = 8 * 1024 * 1024
_REVIEW_JSON_LIMITS = JsonLimits(
    max_bytes=_MAX_REVIEW_JSON_BYTES,
    max_depth=16,
    max_values=250_000,
    max_object_members=25_000,
    max_structural_tokens=500_000,
    max_number_chars=256,
    max_string_chars=8_192,
    max_total_string_chars=6 * 1024 * 1024,
)
_REVIEW_FIELDS = {
    "schema",
    "candidate_sha256",
    "candidate_node_count",
    "candidate_reviewable_nodes",
    "fresh_required_nodes",
    "reviewer",
    "closure_completeness_reviewed",
    "node_independence_reviewed",
    "all_candidate_reviewable_nodes_covered",
    "notes",
    "authorizes_activation",
}
_CANDIDATE_ACTIVATION_FIELDS = {
    "schema",
    "authorizes_reuse",
    "candidate_only",
    "candidate_sha256",
    "task",
    "base_args",
    "targets",
    "static_inputs",
    "session_closure",
    "nodes",
}


def _exact_unlinked_path(path: Path, *, label: str) -> Path:
    candidate = Path(os.path.abspath(path.expanduser()))
    for component in (*reversed(candidate.parents), candidate):
        if is_link_like(component):
            raise ConfigurationError(
                f"{label} path contains a symbolic link or junction: {component}"
            )
    return candidate


def _canonical_sha256(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigurationError("pytest review data is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    path = _exact_unlinked_path(path, label=label)
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
                raise ConfigurationError(f"{label} must be a regular file")
            if opened.st_size > _MAX_REVIEW_JSON_BYTES:
                raise ConfigurationError(f"{label} exceeds the 8 MiB safety limit")
            chunks: list[bytes] = []
            remaining = _MAX_REVIEW_JSON_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > _MAX_REVIEW_JSON_BYTES:
                raise ConfigurationError(f"{label} exceeds the 8 MiB safety limit")
            after_open = os.fstat(descriptor)
            current = path.stat(follow_symlinks=False)
            if (
                len(raw) != opened.st_size
                or is_link_like(path)
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
                raise ConfigurationError(f"{label} changed while it was being read")
        finally:
            os.close(descriptor)
        payload = loads_bounded_json(raw, label=label, limits=_REVIEW_JSON_LIMITS)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError(f"could not read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{label} must be a JSON object")
    return payload


def load_candidate(path: Path) -> tuple[dict[str, Any], str]:
    path = _exact_unlinked_path(path, label="pytest candidate")
    candidate = _read_json(path, label="pytest candidate")
    if candidate.get("schema") != CANDIDATE_SCHEMA:
        raise ConfigurationError("pytest candidate has unsupported schema")
    if candidate.get("authorizes_reuse") is not False:
        raise ConfigurationError("pytest candidate unexpectedly authorizes reuse")
    if candidate.get("candidate_only") is not True:
        raise ConfigurationError("pytest candidate is not marked candidate-only")
    missing = _CANDIDATE_ACTIVATION_FIELDS - set(candidate)
    if missing:
        raise ConfigurationError(
            "pytest candidate is missing activation fields: "
            + ", ".join(sorted(missing))
        )
    claimed = candidate.get("candidate_sha256")
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise ConfigurationError("pytest candidate digest is missing or malformed")
    unsigned = dict(candidate)
    unsigned.pop("candidate_sha256", None)
    actual = _canonical_sha256(unsigned)
    if claimed != actual:
        raise ConfigurationError("pytest candidate digest does not match its contents")
    nodes = candidate.get("nodes")
    if not isinstance(nodes, dict) or not nodes:
        raise ConfigurationError("pytest candidate has no node evidence")
    if any(
        not isinstance(nodeid, str) or not nodeid or not isinstance(row, dict)
        for nodeid, row in nodes.items()
    ):
        raise ConfigurationError("pytest candidate node evidence is malformed")
    return candidate, actual


def candidate_counts(candidate: dict[str, Any]) -> tuple[int, int, int]:
    nodes = candidate["nodes"]
    reviewable = sum(
        1
        for row in nodes.values()
        if isinstance(row, dict)
        and row.get("reviewable") is True
        and row.get("fresh_required") is not True
    )
    fresh = sum(
        1
        for row in nodes.values()
        if isinstance(row, dict) and row.get("fresh_required") is True
    )
    return len(nodes), reviewable, fresh


def write_review_template(
    candidate_path: Path,
    *,
    output: Path,
    reviewer: str | None = None,
) -> dict[str, Any]:
    candidate, digest = load_candidate(candidate_path)
    node_count, reviewable, fresh = candidate_counts(candidate)
    output = _exact_unlinked_path(output, label="pytest review record")
    payload: dict[str, Any] = {
        "schema": REVIEW_SCHEMA,
        "candidate_sha256": digest,
        "candidate_node_count": node_count,
        "candidate_reviewable_nodes": reviewable,
        "fresh_required_nodes": fresh,
        "reviewer": reviewer or "",
        "closure_completeness_reviewed": False,
        "node_independence_reviewed": False,
        "all_candidate_reviewable_nodes_covered": False,
        "notes": "",
        "authorizes_activation": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise ConfigurationError(
            f"pytest review record already exists: {output}; refusing to overwrite review evidence"
        ) from exc
    return payload


def _validate_review(
    review: dict[str, Any],
    *,
    candidate_digest: str,
    counts: tuple[int, int, int],
) -> str:
    if set(review) != _REVIEW_FIELDS:
        raise ConfigurationError(
            "pytest review record fields do not match the reviewed schema"
        )
    if review.get("schema") != REVIEW_SCHEMA:
        raise ConfigurationError("pytest review record has unsupported schema")
    if review.get("candidate_sha256") != candidate_digest:
        raise ConfigurationError("pytest review record targets a different candidate digest")
    node_count, reviewable, fresh = counts
    expected_counts = {
        "candidate_node_count": node_count,
        "candidate_reviewable_nodes": reviewable,
        "fresh_required_nodes": fresh,
    }
    for field, expected in expected_counts.items():
        if type(review.get(field)) is not int or review.get(field) != expected:
            raise ConfigurationError(
                f"pytest review record {field} does not match candidate evidence"
            )
    reviewer = review.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ConfigurationError("pytest review record requires a reviewer identity")
    required_true = (
        "closure_completeness_reviewed",
        "node_independence_reviewed",
        "all_candidate_reviewable_nodes_covered",
        "authorizes_activation",
    )
    missing = [field for field in required_true if review.get(field) is not True]
    if missing:
        raise ConfigurationError(
            "pytest review record does not authorize activation; required reviewed fields are false: "
            + ", ".join(missing)
        )
    notes = review.get("notes")
    if notes is not None and not isinstance(notes, str):
        raise ConfigurationError("pytest review record notes must be a string")
    return _canonical_sha256(review)


def activate_reviewed_candidate(
    candidate_path: Path,
    *,
    review_path: Path,
    output: Path,
) -> dict[str, Any]:
    candidate_path = _exact_unlinked_path(candidate_path, label="pytest candidate")
    review_path = _exact_unlinked_path(review_path, label="pytest review record")
    output = _exact_unlinked_path(output, label="pytest profile")
    if output.exists():
        raise ConfigurationError(
            f"pytest profile already exists: {output}; refusing to overwrite active review authority"
        )
    candidate, candidate_digest = load_candidate(candidate_path)
    counts = candidate_counts(candidate)
    review = _read_json(review_path, label="pytest review record")
    review_digest = _validate_review(
        review,
        candidate_digest=candidate_digest,
        counts=counts,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-pytest-activate-"
    ) as temporary:
        staged = temporary / "profile.json"
        activate_pytest_candidate(
            candidate_path,
            expected_candidate_sha256=candidate_digest,
            independence_review_sha256=review_digest,
            output=staged,
        )
        profile = _read_json(staged, label="staged pytest profile")

    profile["candidate_sha256"] = candidate_digest
    profile["review_record_sha256"] = review_digest
    profile["review_schema"] = REVIEW_SCHEMA
    profile["review_scope"] = {
        "candidate_node_count": counts[0],
        "candidate_reviewable_nodes": counts[1],
        "fresh_required_nodes": counts[2],
    }
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise ConfigurationError(
            f"pytest profile already exists: {output}; refusing to overwrite active review authority"
        ) from exc
    return {
        "status": "PYTEST_PROFILE_ACTIVATED",
        "profile": str(output),
        "candidate_sha256": candidate_digest,
        "review_record_sha256": review_digest,
        "candidate_node_count": counts[0],
        "candidate_reviewable_nodes": counts[1],
        "fresh_required_nodes": counts[2],
        "reuse_activated": True,
    }
