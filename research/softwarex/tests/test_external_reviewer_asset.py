"""Tiny invented external assets test strict offline package completeness."""
import hashlib
import json

import pytest

from research.softwarex import build_public_release as release
from research.softwarex import build_readiness as readiness
from research.softwarex import build_submission_artifacts as archives
from research.softwarex import verify_submission as verifier


@pytest.fixture
def external(tmp_path):
    raw = b"Opaque artifact fixture, not a verified ZIP."
    (tmp_path / "payload.py").write_bytes(b"# payload\n")
    manifest = {"current_version": "0.5.2", "files": [{"path": "payload.py", "bytes": 10,
        "sha256": hashlib.sha256(b"# payload\n").hexdigest()}], "external_artifacts": [
        {"path": release.REVIEWER_ASSET, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
         "url": release.REVIEWER_ASSET_URL}]}
    (tmp_path / release.MANIFEST).write_text(json.dumps(manifest))
    return tmp_path, manifest, raw


def put_asset(context):
    root, _, raw = context
    path = root / release.REVIEWER_ASSET
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def test_git_payload_can_be_inspected_without_asset_but_complete_offline_verify_requires_it(external):
    root, manifest, _ = external
    assert release.inspect(root) == manifest
    with pytest.raises(ValueError, match="Download.*ZeroRun_SoftwareX_reviewer.zip"):
        verifier.manifest_check(root)
    put_asset(external)
    assert verifier.manifest_check(root)["external_artifacts_required"] == manifest["external_artifacts"]


def test_known_whole_asset_uses_stream_hash_not_member_read_limit(external, monkeypatch):
    root, _, _ = external
    put_asset(external)
    monkeypatch.setattr(archives, "MAX_MEMBER_BYTES", 1)
    # Matching one known outer archive never changes the ZIP member-reader bound.
    readiness.matching_public_file(root, release.REVIEWER_ASSET, root / release.REVIEWER_ASSET)
    assert archives.MAX_MEMBER_BYTES == 1
    with pytest.raises(ValueError, match="oversized"):
        archives.read_regular(root / release.REVIEWER_ASSET)


@pytest.mark.parametrize("mode", ["changed", "missing", "unlisted", "bad-url", "outside-path", "duplicate",
                                 "false-size", "also-tracked", "undeclared-current"])
def test_external_asset_cannot_hide_missing_changed_or_unlisted_payloads(external, mode):
    root, manifest, raw = external
    path = put_asset(external)
    if mode == "changed":
        path.write_bytes(raw + b"different")
    elif mode == "missing":
        path.unlink()
    elif mode == "unlisted":
        (root / "other.zip").write_bytes(b"unlisted")
    elif mode == "bad-url":
        manifest["external_artifacts"][0]["url"] = "https://example.invalid/other.zip"
    elif mode == "outside-path":
        manifest["external_artifacts"][0]["path"] = "../outside.zip"
    elif mode == "duplicate":
        manifest["external_artifacts"] *= 2
    elif mode == "false-size":
        manifest["external_artifacts"][0]["bytes"] = True
    elif mode == "also-tracked":
        manifest["files"].append({key: manifest["external_artifacts"][0][key] for key in ("path", "bytes", "sha256")})
    else:
        manifest["external_artifacts"] = []
        path.unlink()
    (root / release.MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verifier.manifest_check(root)
