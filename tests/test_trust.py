from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from zerorun import store as store_module
from zerorun import trust
from zerorun.manifest import load_manifest
from zerorun.model import ConfigurationError
from zerorun.pytest_profile import load_pytest_profile
from zerorun.store import Store


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IMAGE = "example.invalid/python@sha256:" + "a" * 64


def _write_v2_repository(root: Path, *, profile: bool = False):
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    (root / "input.txt").write_text("stable\n", encoding="utf-8")
    manifest_path = root / ".zerorun.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 2,
                "tasks": {
                    "tests": {
                        "command": ["python", "-m", "pytest"],
                        "inputs": ["input.txt"],
                        "outputs": [],
                        "env": [],
                        "cacheable": True,
                        "unsafe_effects": [],
                        "cache_streams": False,
                        "image": IMAGE,
                        "platform": "linux/amd64",
                        "result_only": True,
                        "closure_reviewed": True,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    if not profile:
        return manifest, None
    profile_path = root / ".zerorun-pytest.json"
    profile_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "task": "tests",
                "base_args": [],
                "targets": ["tests"],
                "static_inputs": ["input.txt"],
                "session_closure": {
                    "selectors": [],
                    "fallback_files": ["input.txt"],
                },
                "nodes": {
                    "tests/test_sample.py::test_sample": {
                        "reviewable": True,
                        "fresh_required": False,
                        "selectors": [],
                        "fallback_files": ["input.txt"],
                    }
                },
                "independence_review_sha256": "b" * 64,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return manifest, load_pytest_profile(profile_path, manifest)


def _write_v1_repository(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").mkdir()
    (root / "input.txt").write_text("alpha\n", encoding="utf-8")
    (root / "task.py").write_text(
        "from pathlib import Path\n"
        "Path('out.txt').write_text(Path('input.txt').read_text().upper())\n",
        encoding="utf-8",
    )
    manifest = root / ".zerorun.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "work": {
                        "command": [sys.executable, "task.py"],
                        "inputs": ["task.py", "input.txt"],
                        "outputs": ["out.txt"],
                        "cacheable": True,
                        "unsafe_effects": [],
                        "env": [],
                        "cache_streams": True,
                    }
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return manifest


def _run_cli(manifest: Path) -> dict:
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "zerorun",
            "--manifest",
            str(manifest),
            "--json",
            "run",
            "work",
        ],
        cwd=PROJECT_ROOT,
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


def test_exact_manifest_authority_persists_across_processes_and_invalidates_on_edit(
    tmp_path: Path,
) -> None:
    manifest, _ = _write_v2_repository(tmp_path / "repository")
    digest = trust.manifest_sha256(manifest)
    trust.authorize_manifest(manifest, expected_manifest_sha256=digest)

    script = (
        "from pathlib import Path; "
        "from zerorun.manifest import load_manifest; "
        "from zerorun.trust import manifest_is_authorized; "
        f"m=load_manifest(Path({str(manifest.path)!r})); "
        "raise SystemExit(0 if manifest_is_authorized(m) else 7)"
    )
    process = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr

    manifest.path.write_text(
        manifest.path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    changed = load_manifest(manifest.path)
    assert trust.manifest_is_authorized(changed) is False


def test_manifest_swap_cannot_authorize_different_parsed_tasks(tmp_path: Path) -> None:
    manifest, _ = _write_v2_repository(tmp_path / "repository")
    parsed_digest = manifest.source_sha256
    raw = json.loads(manifest.path.read_text(encoding="utf-8"))
    raw["tasks"]["tests"]["command"].append("--strict-markers")
    manifest.path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    replacement = load_manifest(manifest.path)
    assert replacement.source_sha256 != parsed_digest

    with pytest.raises(ConfigurationError, match="changed after it was loaded"):
        trust.authorize_manifest(
            manifest,
            expected_manifest_sha256=str(replacement.source_sha256),
        )

    assert trust.manifest_is_authorized(manifest) is False


def test_profile_authority_binds_exact_profile_bytes(tmp_path: Path) -> None:
    manifest, profile = _write_v2_repository(tmp_path / "repository", profile=True)
    assert profile is not None
    trust.authorize_pytest_profile(manifest, profile.profile_sha256)
    assert trust.pytest_profile_is_authorized(manifest, profile.profile_sha256)

    profile.path.write_text(
        profile.path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    changed = load_pytest_profile(profile.path, manifest)
    assert changed.profile_sha256 != profile.profile_sha256
    assert trust.pytest_profile_is_authorized(manifest, changed.profile_sha256) is False


def test_checkout_forged_authority_receipt_cannot_authorize_manifest(
    tmp_path: Path,
) -> None:
    manifest, _ = _write_v2_repository(tmp_path / "repository")
    payload = trust._authority_payload(manifest, profile_sha256=None)
    receipt = trust._authority_path(manifest, payload)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps({**payload, "mac": "0" * 64}),
        encoding="utf-8",
    )

    assert trust.manifest_is_authorized(manifest) is False


def test_cache_tampering_and_cross_checkout_copy_never_yield_a_hit(
    tmp_path: Path,
) -> None:
    first_manifest = _write_v1_repository(tmp_path / "first")
    first = _run_cli(first_manifest)
    assert first["status"] == "MISS_EXECUTED"
    key = first["cache_key"]
    metadata = first_manifest.parent / ".zerorun" / "cache" / key / "metadata.json"
    raw = json.loads(metadata.read_text(encoding="utf-8"))
    assert raw["provenance"]["schema"] == "zerorun-cache-provenance-v1"
    assert not (first_manifest.parent / "authority.key").exists()

    second_manifest = _write_v1_repository(tmp_path / "second")
    source_cache = first_manifest.parent / ".zerorun" / "cache"
    destination_cache = second_manifest.parent / ".zerorun" / "cache"
    destination_cache.parent.mkdir()
    shutil.copytree(source_cache, destination_cache)
    copied = _run_cli(second_manifest)
    assert copied["status"] != "HIT_REPLAYED"

    raw["execution_ms"] = 999999.0
    metadata.write_text(json.dumps(raw), encoding="utf-8")
    tampered = _run_cli(first_manifest)
    assert tampered["status"] != "HIT_REPLAYED"


def test_legitimate_cache_reuse_survives_a_new_process(tmp_path: Path) -> None:
    manifest = _write_v1_repository(tmp_path / "repository")
    first = _run_cli(manifest)
    assert first["status"] == "MISS_EXECUTED"
    (manifest.parent / "out.txt").unlink()

    second = _run_cli(manifest)

    assert second["status"] == "HIT_REPLAYED"
    assert (manifest.parent / "out.txt").read_text(encoding="utf-8") == "ALPHA\n"


def test_concurrent_forged_cache_publication_is_never_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path = _write_v1_repository(tmp_path / "repository")
    manifest = load_manifest(manifest_path)
    task = manifest.tasks["work"]
    (manifest.root / "out.txt").write_text("ALPHA\n", encoding="utf-8")
    store = Store(manifest.root)
    key = "c" * 64
    fingerprint = {"schema": 2, "attacker": "cannot-sign"}
    destination = store.entry(key)
    real_replace = os.replace

    def attacker_wins_publish_race(source, target) -> None:
        if Path(target) == destination:
            destination.mkdir(parents=True)
            (destination / "metadata.json").write_text(
                json.dumps(
                    {
                        "schema": 3,
                        "task": task.name,
                        "key": key,
                        "fingerprint": fingerprint,
                        "exit_code": 0,
                        "streams_cached": True,
                    }
                ),
                encoding="utf-8",
            )
            raise FileExistsError("attacker published an unsigned entry first")
        real_replace(source, target)

    monkeypatch.setattr(store_module.os, "replace", attacker_wins_publish_race)

    with pytest.raises(FileExistsError, match="unsigned entry"):
        store.save(
            manifest,
            task,
            key,
            fingerprint,
            execution_ms=1.0,
            stdout=b"",
            stderr=b"",
        )

    assert (destination / "QUARANTINED").is_file()


def test_artifact_and_stream_swap_after_validation_are_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest_path = _write_v1_repository(tmp_path / "repository")
    first = _run_cli(manifest_path)
    manifest = load_manifest(manifest_path)
    task = manifest.tasks["work"]
    store = Store(manifest.root)
    key = first["cache_key"]
    metadata = store.metadata(key)
    assert metadata is not None
    fingerprint = metadata["fingerprint"]
    artifact = store.entry(key) / "artifacts" / "0000.bin"
    original_artifact = artifact.read_bytes()
    stdout = store.entry(key) / "stdout.bin"
    (manifest.root / "out.txt").write_text("ORIGINAL\n", encoding="utf-8")
    original_validate = Store._validate_metadata

    def validate_then_swap(self, *args, **kwargs) -> None:
        original_validate(self, *args, **kwargs)
        artifact.write_bytes(b"FORGED\n")

    monkeypatch.setattr(Store, "_validate_metadata", validate_then_swap)
    with pytest.raises(ConfigurationError, match="signed metadata"):
        store.restore(manifest, task, key, fingerprint, metadata)
    assert (manifest.root / "out.txt").read_text(encoding="utf-8") == "ORIGINAL\n"

    monkeypatch.setattr(Store, "_validate_metadata", original_validate)
    # Restore the signed artifact, validate once, then replace a stream before
    # the replay read. The stable stream handle must verify the final bytes.
    artifact.write_bytes(original_artifact)
    store._validate_metadata(manifest, task, key, fingerprint, metadata)
    stdout.write_bytes(b"forged stream")
    with pytest.raises(ConfigurationError, match="signed metadata"):
        store.streams(key, metadata)


def test_git_tracked_runtime_state_is_rejected(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("Git is unavailable")
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        [git, "init", "--quiet", str(repository)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )
    runtime_file = repository / ".zerorun" / "cache" / ("a" * 64) / "metadata.json"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("{}", encoding="utf-8")
    subprocess.run(
        [git, "-C", str(repository), "add", "-f", ".zerorun"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
    )

    with pytest.raises(ConfigurationError, match="Git-tracked .zerorun"):
        Store(repository)


def test_trust_root_inside_repository_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    manifest, _ = _write_v2_repository(tmp_path / "repository")
    monkeypatch.setenv(
        "ZERORUN_TRUST_ROOT",
        str(manifest.root / ".attacker-controlled-trust"),
    )

    with pytest.raises(ConfigurationError, match="outside the repository"):
        trust.authorize_manifest(
            manifest,
            expected_manifest_sha256=trust.manifest_sha256(manifest),
        )
