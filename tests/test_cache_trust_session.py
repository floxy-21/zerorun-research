from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from zerorun import pytest_node_cache, trust
from zerorun.hermetic_store import _load_result_entry
from zerorun.model import ConfigurationError, TaskSpec
from zerorun.pytest_node_cache import NodeCacheDecision, publish_verified_successes
from zerorun.store import Store


PROFILE_DIGEST = "9" * 64


def _repository(root: Path, *, git: bool = False) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".zerorun.json").write_text(
        '{"tasks":{},"version":2}\n',
        encoding="utf-8",
    )
    if git:
        executable = shutil.which("git")
        if executable is None:
            pytest.skip("Git is unavailable")
        subprocess.run(
            [executable, "init", "--quiet", str(root)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
    return hashlib.sha256((root / ".zerorun.json").read_bytes()).hexdigest()


def _metadata(index: int) -> dict[str, object]:
    key = f"{index:064x}"
    return {
        "schema": 6,
        "kind": "hermetic-result-only",
        "task": "pytest",
        "key": key,
        "created_at": 1.0,
        "execution_ms": float(index),
        "fingerprint": {
            "schema": 6,
            "profile_sha256": PROFILE_DIGEST,
            "node": key,
        },
        "exit_code": 0,
    }


def _task() -> TaskSpec:
    return TaskSpec(
        name="pytest",
        command=("python", "-m", "pytest"),
        inputs=(),
        outputs=(),
        env=(),
        cacheable=True,
        unsafe_effects=(),
        cache_streams=False,
        image="python@sha256:" + "d" * 64,
        platform="linux/amd64",
        result_only=True,
        closure_reviewed=True,
    )


def _fingerprint(nodeid: str) -> dict[str, object]:
    return {
        "schema": 6,
        "kind": "hermetic-result-only",
        "task": _task().name,
        "nodeid": nodeid,
        "profile_sha256": PROFILE_DIGEST,
        "runtime_identity": {
            "resolved_image": "sha256:" + "d" * 64,
            "python_version": "3.12.14",
        },
    }


def _fingerprint_key(fingerprint: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            fingerprint,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize("node_count", [237, 500])
def test_payload_batch_keeps_git_subprocess_count_constant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node_count: int,
) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root, git=True)
    real_run = trust._run_bounded_process
    calls = 0

    def counted_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(trust, "_run_bounded_process", counted_run)
    publication = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    signed = []
    for index in range(node_count):
        metadata = _metadata(index)
        signed.append(
            {
                **metadata,
                "provenance": publication.sign_payload(
                    metadata,
                    manifest_digest=manifest_digest,
                    profile_digest=PROFILE_DIGEST,
                ),
            }
        )
    publication.verify_unchanged()

    lookup = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=False,
    )
    assert all(
        lookup.payload_is_trusted(
            metadata,
            manifest_digest=manifest_digest,
            profile_digest=PROFILE_DIGEST,
        )
        for metadata in signed
    )
    lookup.verify_unchanged()

    # Each phase performs one pre/post tracked-state check, and each check uses
    # exactly two Git subprocesses. The count is independent of node count.
    assert calls == 8
    # The former per-entry lookup+publication path performed one two-process
    # tracked-state check for every operation: 4 * node_count subprocesses.
    assert calls < 4 * node_count


def test_session_still_rejects_individually_tampered_payloads(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    publication = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    metadata = _metadata(1)
    signed = {
        **metadata,
        "provenance": publication.sign_payload(
            metadata,
            manifest_digest=manifest_digest,
            profile_digest=PROFILE_DIGEST,
        ),
    }
    publication.verify_unchanged()

    lookup = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
    )
    assert lookup.payload_is_trusted(
        signed,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
    )
    tampered = {**signed, "execution_ms": 999999.0}
    assert not lookup.payload_is_trusted(
        tampered,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
    )
    extra_provenance = {
        **signed,
        "provenance": {**signed["provenance"], "unsigned_extra": True},
    }
    assert not lookup.payload_is_trusted(
        extra_provenance,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
    )
    malformed_mac = {
        **signed,
        "provenance": {
            **signed["provenance"],
            "mac": str(signed["provenance"]["mac"]).upper(),
        },
    }
    assert not lookup.payload_is_trusted(
        malformed_mac,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
    )
    lookup.verify_unchanged()


def test_session_rejects_forged_binding_and_use_after_close(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    session = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    with pytest.raises(ConfigurationError, match="different provenance binding"):
        session.sign_payload(
            _metadata(1),
            manifest_digest=manifest_digest,
            profile_digest="8" * 64,
        )
    session.verify_unchanged()
    with pytest.raises(ConfigurationError, match="already closed"):
        session.payload_is_trusted(
            _metadata(1),
            manifest_digest=manifest_digest,
            profile_digest=PROFILE_DIGEST,
        )


def test_session_rejects_caller_manifest_identity_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    _repository(root)
    with pytest.raises(ConfigurationError, match="does not match the repository"):
        trust.CacheTrustSession.open(
            root,
            manifest_digest="f" * 64,
            profile_digest=PROFILE_DIGEST,
        )


def test_session_rejects_manifest_drift(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    session = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    (root / ".zerorun.json").write_text(
        '{"tasks":{},"version":2} \n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="manifest changed"):
        session.verify_unchanged()


def test_session_rejects_tracked_state_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    observations = iter([(), (".zerorun/cache/forged/metadata.json",)])
    monkeypatch.setattr(
        trust,
        "tracked_zerorun_paths",
        lambda _root: next(observations),
    )
    session = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    with pytest.raises(ConfigurationError, match="tracked .zerorun state changed"):
        session.verify_unchanged()


def test_session_rejects_repository_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    identities = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(trust, "repository_identity", lambda _root: next(identities))
    session = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    with pytest.raises(ConfigurationError, match="repository identity changed"):
        session.verify_unchanged()


def test_session_rejects_authority_key_drift(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    session = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
        create_secret=True,
    )
    trust._secret_path(root).write_bytes(b"x" * 32)
    with pytest.raises(ConfigurationError, match="authority key changed"):
        session.verify_unchanged()


def test_authority_key_read_is_bounded_before_length_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("ZERORUN_TRUST_ROOT", str((tmp_path / "trust").resolve()))
    root = tmp_path / "repository"
    _repository(root)
    secret = trust._secret_path(root)
    trust._ensure_private_directory(secret.parent)
    secret.write_bytes(b"x" * (trust._SECRET_BYTES + 1))

    with pytest.raises(ConfigurationError, match="32-byte safety limit"):
        trust._read_secret(root, create=False)


@pytest.mark.parametrize("mutation", ["truncate", "grow", "rewrite"])
def test_authority_key_read_rejects_in_place_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    monkeypatch.setenv("ZERORUN_TRUST_ROOT", str((tmp_path / "trust").resolve()))
    root = tmp_path / "repository"
    _repository(root)
    secret = trust._secret_path(root)
    trust._ensure_private_directory(secret.parent)
    original_bytes = b"x" * trust._SECRET_BYTES
    secret.write_bytes(original_bytes)
    real_read = trust.os.read
    mutated = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            before = secret.stat()
            if mutation == "truncate":
                replacement = original_bytes[:-1]
            elif mutation == "grow":
                replacement = original_bytes + b"y"
            else:
                replacement = b"z" * len(original_bytes)
            secret.write_bytes(replacement)
            os.utime(
                secret,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return real_read(descriptor, size)

    monkeypatch.setattr(trust.os, "read", racing_read)
    with pytest.raises(
        ConfigurationError,
        match="changed while it was being read|safety limit",
    ):
        trust._read_secret(root, create=False)


def test_repository_identity_bounds_linked_worktree_marker(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".git").write_bytes(b"x" * (trust._MAX_GIT_MARKER_BYTES + 1))

    with pytest.raises(ConfigurationError, match="4096-byte safety limit"):
        trust.repository_identity(root)


def test_publication_drift_quarantines_every_touched_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _repository(root)
    store = Store(root)
    nodeid = "tests/test_example.py::test_one"
    fingerprint = _fingerprint(nodeid)
    key = _fingerprint_key(fingerprint)
    decision = NodeCacheDecision(
        nodeid=nodeid,
        cache_task=_task(),
        key=key,
        fingerprint=fingerprint,
        status="MISS_VERIFIED",
        reason=None,
    )
    real_save = pytest_node_cache._save_result_entry

    def save_then_drift(*args, **kwargs):
        saved = real_save(*args, **kwargs)
        trust._secret_path(root).write_bytes(b"z" * 32)
        return saved

    monkeypatch.setattr(pytest_node_cache, "_save_result_entry", save_then_drift)
    assert publish_verified_successes(
        root,
        [decision],
        successful_nodeids={decision.nodeid},
        execution_ms_by_node={decision.nodeid: 1.0},
        store=store,
    ) == 0
    assert (store.entry(key) / "QUARANTINED").is_file()
    assert store.metadata(key) is None


def test_incomplete_publication_batch_quarantines_prior_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _repository(root)
    store = Store(root)
    decisions = []
    for index in range(2):
        nodeid = f"tests/test_example.py::test_{index}"
        fingerprint = _fingerprint(nodeid)
        key = _fingerprint_key(fingerprint)
        decisions.append(
            NodeCacheDecision(
                nodeid=nodeid,
                cache_task=_task(),
                key=key,
                fingerprint=fingerprint,
                status="MISS_VERIFIED",
                reason=None,
            )
        )
    real_save = pytest_node_cache._save_result_entry
    calls = 0

    def fail_second_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second-entry publication failure")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(pytest_node_cache, "_save_result_entry", fail_second_save)
    with pytest.raises(OSError, match="second-entry"):
        publish_verified_successes(
            root,
            decisions,
            successful_nodeids={decision.nodeid for decision in decisions},
            store=store,
        )
    first_key = decisions[0].key
    assert first_key is not None
    assert (store.entry(first_key) / "QUARANTINED").is_file()
    assert store.metadata(first_key) is None


def test_load_with_session_preserves_exact_fingerprint_validation(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    store = Store(root)
    nodeid = "tests/test_example.py::test_one"
    fingerprint = _fingerprint(nodeid)
    key = _fingerprint_key(fingerprint)
    decision = NodeCacheDecision(
        nodeid=nodeid,
        cache_task=_task(),
        key=key,
        fingerprint=fingerprint,
        status="MISS_VERIFIED",
        reason=None,
    )
    assert publish_verified_successes(
        root,
        [decision],
        successful_nodeids={decision.nodeid},
        store=store,
        manifest_digest=manifest_digest,
    ) == 1
    lookup = trust.CacheTrustSession.open(
        root,
        manifest_digest=manifest_digest,
        profile_digest=PROFILE_DIGEST,
    )
    changed = {**fingerprint, "nodeid": "tests/test_example.py::test_changed"}
    assert _load_result_entry(
        store,
        _task(),
        key,
        changed,
        trust_session=lookup,
    ) is None
    lookup.verify_unchanged()


def test_absent_entry_does_not_open_lazy_lookup_trust_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _repository(root)
    store = Store(root)
    provider_calls = 0
    fingerprint = _fingerprint("tests/test_example.py::test_absent")
    key = _fingerprint_key(fingerprint)

    def provider() -> trust.CacheTrustSession:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("an absent cache entry has no payload to attest")

    assert _load_result_entry(
        store,
        _task(),
        key,
        fingerprint,
        trust_session_provider=provider,
    ) is None
    assert provider_calls == 0


def test_mixed_absent_and_present_lookup_opens_one_attested_session(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root)
    store = Store(root)
    present_nodeid = "tests/test_example.py::test_present"
    present_fingerprint = _fingerprint(present_nodeid)
    present_key = _fingerprint_key(present_fingerprint)
    present = NodeCacheDecision(
        nodeid=present_nodeid,
        cache_task=_task(),
        key=present_key,
        fingerprint=present_fingerprint,
        status="MISS_VERIFIED",
        reason=None,
    )
    assert publish_verified_successes(
        root,
        [present],
        successful_nodeids={present.nodeid},
        store=store,
        manifest_digest=manifest_digest,
    ) == 1

    session: trust.CacheTrustSession | None = None
    provider_calls = 0

    def provider() -> trust.CacheTrustSession:
        nonlocal provider_calls, session
        provider_calls += 1
        if session is None:
            session = trust.CacheTrustSession.open(
                root,
                manifest_digest=manifest_digest,
                profile_digest=PROFILE_DIGEST,
            )
        return session

    absent_fingerprint = _fingerprint("tests/test_example.py::test_absent")
    absent_key = _fingerprint_key(absent_fingerprint)
    assert _load_result_entry(
        store,
        _task(),
        absent_key,
        absent_fingerprint,
        trust_session_provider=provider,
    ) is None
    loaded = _load_result_entry(
        store,
        _task(),
        present_key,
        present_fingerprint,
        trust_session_provider=provider,
    )
    assert loaded is not None
    assert loaded["key"] == present_key
    assert provider_calls == 1
    assert session is not None
    session.verify_unchanged()


@pytest.mark.parametrize("node_count", [237, 586])
def test_cache_empty_lookup_then_publication_skips_lookup_git_phase(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    node_count: int,
) -> None:
    root = tmp_path / "repository"
    manifest_digest = _repository(root, git=True)
    store = Store(root)
    real_run = trust._run_bounded_process
    git_calls = 0

    def counted_run(*args, **kwargs):
        nonlocal git_calls
        git_calls += 1
        return real_run(*args, **kwargs)

    monkeypatch.setattr(trust, "_run_bounded_process", counted_run)

    def lookup_provider() -> trust.CacheTrustSession:
        return trust.CacheTrustSession.open(
            root,
            manifest_digest=manifest_digest,
            profile_digest=PROFILE_DIGEST,
        )

    for index in range(node_count):
        fingerprint = _fingerprint(f"tests/test_example.py::test_absent_{index}")
        key = _fingerprint_key(fingerprint)
        assert _load_result_entry(
            store,
            _task(),
            key,
            fingerprint,
            trust_session_provider=lookup_provider,
        ) is None
    assert git_calls == 0

    nodeid = "tests/test_example.py::test_new"
    fingerprint = _fingerprint(nodeid)
    key = _fingerprint_key(fingerprint)
    decision = NodeCacheDecision(
        nodeid=nodeid,
        cache_task=_task(),
        key=key,
        fingerprint=fingerprint,
        status="MISS_VERIFIED",
        reason=None,
    )
    assert publish_verified_successes(
        root,
        [decision],
        successful_nodeids={decision.nodeid},
        store=store,
        manifest_digest=manifest_digest,
    ) == 1
    # Publication still receives one exact pre/post trust attestation. Each
    # tracked-state check is two Git subprocesses; only the empty lookup phase
    # is elided.
    assert git_calls == 4
