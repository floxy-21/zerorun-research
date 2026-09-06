from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from zerorun import store as store_module
from zerorun import pytest_review
from zerorun import trust
from zerorun.manifest import load_manifest
from zerorun.model import ConfigurationError
from zerorun.pytest_qualify import activate_pytest_candidate
from zerorun.pytest_review import (
    activate_reviewed_candidate,
    write_review_template,
)
from zerorun.store import Store


def _manifest(root: Path):
    root.mkdir()
    (root / ".git").mkdir()
    path = root / ".zerorun.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "tests": {
                        "command": ["python", "-m", "pytest"],
                        "inputs": ["tests"],
                        "outputs": ["result.json"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return load_manifest(path)


def _candidate(path: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "zerorun-pytest-candidate-v1",
        "authorizes_reuse": False,
        "candidate_only": True,
        "generated_from_observation": True,
        "task": "tests",
        "base_args": [],
        "targets": ["tests"],
        "static_inputs": ["pyproject.toml"],
        "session_closure": {
            "selectors": [],
            "fallback_files": ["conftest.py"],
        },
        "nodes": {
            "tests/test_sample.py::test_sample": {
                "reviewable": True,
                "fresh_required": False,
                "selectors": ["src/sample.py::run"],
                "fallback_files": [],
            }
        },
        "review": {
            "required": True,
            "independence_review_sha256": None,
            "reason": "candidate only",
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["candidate_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return payload


def _inject_duplicate_schema(path: Path, schema: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("{", '{"schema":' + json.dumps(schema) + ",", 1),
        encoding="utf-8",
    )


def test_authority_receipt_rejects_duplicate_members_even_with_valid_mac(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path / "repository")
    digest = trust.manifest_sha256(manifest)
    trust.authorize_manifest(manifest, expected_manifest_sha256=digest)
    payload = trust._authority_payload(manifest, profile_sha256=None)
    receipt = trust._authority_path(manifest, payload)
    _inject_duplicate_schema(receipt, "zerorun-user-authority-v1")

    assert trust.manifest_is_authorized(manifest) is False


def test_authority_receipt_size_limit_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path / "repository")
    digest = trust.manifest_sha256(manifest)
    trust.authorize_manifest(manifest, expected_manifest_sha256=digest)
    payload = trust._authority_payload(manifest, profile_sha256=None)
    receipt = trust._authority_path(manifest, payload)
    receipt.write_bytes(b" " * (trust._MAX_AUTHORITY_RECEIPT_BYTES + 1))

    assert trust.manifest_is_authorized(manifest) is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"schema": 3, "schema": 3}',
        '{"schema": 3, "wall_ms": NaN}',
        '{"schema": 3, "wall_ms": 1e999}',
    ],
)
def test_cache_metadata_rejects_ambiguous_or_nonfinite_json(
    tmp_path: Path, raw: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    store = Store(root)
    key = "a" * 64
    entry = store.entry(key)
    entry.mkdir(parents=True)
    (entry / "metadata.json").write_text(raw, encoding="utf-8")

    assert store.metadata(key) is None


def test_cache_metadata_rejects_excessive_json_depth(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").mkdir()
    store = Store(root)
    key = "b" * 64
    entry = store.entry(key)
    entry.mkdir(parents=True)
    depth = store_module._CACHE_METADATA_JSON_LIMITS.max_depth + 1
    (entry / "metadata.json").write_text(
        '{"nested":' + ("[" * depth) + "0" + ("]" * depth) + "}",
        encoding="utf-8",
    )

    assert store.metadata(key) is None


def test_candidate_loaders_reject_duplicate_members_with_matching_digest(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "candidate.json"
    candidate = _candidate(candidate_path)
    _inject_duplicate_schema(candidate_path, "zerorun-pytest-candidate-v1")

    with pytest.raises(ConfigurationError, match="duplicate JSON member"):
        write_review_template(candidate_path, output=tmp_path / "review.json")
    with pytest.raises(ConfigurationError, match="duplicate JSON member"):
        activate_pytest_candidate(
            candidate_path,
            expected_candidate_sha256=str(candidate["candidate_sha256"]),
            independence_review_sha256="b" * 64,
            output=tmp_path / "profile.json",
        )


@pytest.mark.parametrize("mutation", ["truncate", "grow", "rewrite"])
def test_review_json_reader_rejects_in_place_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "candidate.json"
    original_bytes = b'{"x":1}\n'
    path.write_bytes(original_bytes)
    real_read = pytest_review.os.read
    mutated = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            before = path.stat()
            if mutation == "truncate":
                replacement = original_bytes[:-1]
            elif mutation == "grow":
                replacement = original_bytes + b" "
            else:
                replacement = b'{"x":2}\n'
            path.write_bytes(replacement)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return real_read(descriptor, size)

    monkeypatch.setattr(pytest_review.os, "read", racing_read)
    with pytest.raises(ConfigurationError, match="changed while it was being read"):
        pytest_review._read_json(path, label="pytest candidate")


def test_review_record_rejects_duplicate_members_and_boolean_counts(
    tmp_path: Path,
) -> None:
    candidate_path = tmp_path / "candidate.json"
    _candidate(candidate_path)
    review_path = tmp_path / "review.json"
    review = write_review_template(
        candidate_path,
        output=review_path,
        reviewer="operator",
    )
    review.update(
        {
            "closure_completeness_reviewed": True,
            "node_independence_reviewed": True,
            "all_candidate_reviewable_nodes_covered": True,
            "authorizes_activation": True,
        }
    )
    review_path.write_text(json.dumps(review, sort_keys=True), encoding="utf-8")
    _inject_duplicate_schema(review_path, "zerorun-pytest-review-v1")
    with pytest.raises(ConfigurationError, match="duplicate JSON member"):
        activate_reviewed_candidate(
            candidate_path,
            review_path=review_path,
            output=tmp_path / "profile-duplicate.json",
        )

    review["candidate_node_count"] = True
    review_path.write_text(json.dumps(review, sort_keys=True), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="does not match candidate evidence"):
        activate_reviewed_candidate(
            candidate_path,
            review_path=review_path,
            output=tmp_path / "profile-bool.json",
        )
