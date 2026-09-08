"""Adversarial checks of retained real conformance records; no execution claims."""
import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest

from research.softwarex import verify_fresh_public_053 as v

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / v.EVIDENCE


def test_actual_complete_record_replay():
    result = v.verify(ROOT, DATA)
    assert result["passed"] and result["record_files"] == 139
    assert result["normal_mcp_requests"] == 10
    assert result["fresh_failure_requests"] == 2
    assert result["original_pre_mcp_failure_preserved"]
    assert not result["rootfs_archive_rehashed_offline"]
    assert not result["independent_human_replication"]


def test_seal_missing_changed_extra_and_duplicate(tmp_path):
    payload = tmp_path / "raw.txt"
    payload.write_bytes(b"actual raw record")
    seal = {"schema": "zerorun.fresh-public-records.v1", "files": [{"path": "raw.txt", **v.identity(payload)}]}
    manifest = tmp_path / "RECORD_MANIFEST.json"
    manifest.write_text(json.dumps(seal))
    assert v.seal(tmp_path) == 1
    payload.write_bytes(b"altered")
    with pytest.raises(ValueError, match="payload differs"):
        v.seal(tmp_path)
    payload.unlink()
    with pytest.raises(ValueError, match="missing"):
        v.seal(tmp_path)
    payload.write_bytes(b"actual raw record")
    (tmp_path / "extra.log").write_bytes(b"unlisted")
    with pytest.raises(ValueError, match="closure"):
        v.seal(tmp_path)
    (tmp_path / "extra.log").unlink()
    seal["files"].append(copy.deepcopy(seal["files"][0]))
    manifest.write_text(json.dumps(seal))
    with pytest.raises(ValueError, match="duplicate"):
        v.seal(tmp_path)


@pytest.mark.parametrize("relative", ["../raw.txt", "/raw.txt", "a\\raw.txt", "a/../raw.txt"])
def test_record_path_escape_refused(tmp_path, relative):
    with pytest.raises(ValueError):
        v.member(tmp_path, relative)


def test_duplicate_json_keys_refused(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"passed":false,"passed":true}')
    with pytest.raises(ValueError, match="duplicate"):
        v.load(path)


def recode(stage):
    raw = b"".join(v.q.canonical(row) + b"\n" for row in stage["responses"])
    stage["streams"].update(stdout_base64=base64.b64encode(raw).decode(), stdout_bytes=len(raw), stdout_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("mutation", ["missing_wire", "wire_false", "text_mismatch", "wrong_status", "wrong_exit", "wrong_task", "verified", "wrong_request", "truncated", "missing_initialize"])
def test_actual_failure_raw_semantics_refuse_tampering(mutation):
    capture = v.load(DATA / "failure-supplement/completion.json")
    stage = copy.deepcopy(capture["stages"][0])
    wire = stage["responses"][1]["result"]
    payload = wire["structuredContent"]
    if mutation == "missing_wire":
        del wire["isError"]
    elif mutation == "wire_false":
        wire["isError"] = False
    elif mutation == "text_mismatch":
        wire["content"][0]["text"] = "{}"
    elif mutation in ("wrong_status", "wrong_exit", "wrong_task", "verified"):
        field, value = {"wrong_status": ("status", "HIT_REUSED"), "wrong_exit": ("exit_code", 0), "wrong_task": ("task", "other"), "verified": ("verified", True)}[mutation]
        payload[field] = value
        wire["content"][0]["text"] = json.dumps(payload)
    elif mutation == "wrong_request":
        stage["requests"][-1]["params"]["arguments"]["root"] = "/other"
    elif mutation == "truncated":
        stage["streams"]["stdout_truncated"] = True
    else:
        stage["responses"].pop(0)
    recode(stage)
    with pytest.raises(ValueError):
        v.failure_exchange(stage, capture["fixture_root"])


def test_nonempty_docker_inventory_refused(monkeypatch):
    original = v.command
    def changed(directory, name, **kwargs):
        row, streams = original(directory, name, **kwargs)
        if name == "empty-image-inventory":
            streams["stdout"] = b"preexisting-image\n"
        return row, streams
    monkeypatch.setattr(v, "command", changed)
    with pytest.raises(ValueError, match="not empty"):
        v.verify_host(DATA / "host-preparation", DATA / "original-attempt")


def test_public_fetch_cannot_use_different_commit(tmp_path):
    folder = DATA / "original-attempt"
    name = "anonymous-public-fetch"
    row = v.load(folder / (name + ".json"))
    for stream in ("stdout", "stderr"):
        (tmp_path / row[stream]["path"]).write_bytes((folder / row[stream]["path"]).read_bytes())
    row["argv"][-1] = "0" * 40
    (tmp_path / (name + ".json")).write_text(json.dumps(row))
    with pytest.raises(ValueError, match="argv differs"):
        v.command(tmp_path, name, argv=v.GIT + ["fetch", "--depth", "1", "origin", v.COMMIT])
