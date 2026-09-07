"""Artificial raw streams check export completeness and directory boundaries."""
import hashlib
import json

import pytest

from research.softwarex import build_public_release as release


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "ROOT", tmp_path)
    base = tmp_path / "research/softwarex/evidence" / release.VM_REGRESSION_EVIDENCE_DIRS[0]
    stdout, stderr = b"Artificial failed trial output.\n", b""
    value = {}
    for stream, raw in (("stdout", stdout), ("stderr", stderr)):
        name = "trial." + stream + ".txt"
        write(base / name, raw)
        value[stream] = {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    write(base / "receipt.json", json.dumps({"commands": [value]}).encode())
    return base, value


def test_only_named_top_level_capture_and_jsonl_outputs_exported(recorded):
    base, _ = recorded
    write(base / "collected-nodes.jsonl", b'{"artificial": true}\n')
    write(base / "test-reports.jsonl", b'{"passed": false}\n')
    write(base / "private-note.txt", b"not a selected capture")
    write(base / "client-home/secret.stdout.txt", b"must remain private")
    write(base.parent / "unrelated/secret.stdout.txt", b"outside named regression records")
    rows = release.vm_regression_text_inputs()
    assert {row["path"].rsplit("/", 1)[-1] for row in rows} == {
        "trial.stdout.txt", "trial.stderr.txt", "collected-nodes.jsonl", "test-reports.jsonl"}
    assert not any("client-home" in row["path"] or "unrelated" in row["path"] for row in rows)


@pytest.mark.parametrize("mode", ["missing", "changed", "false-hash", "outside-path"])
def test_raw_receipt_references_cannot_dangle_or_drift(recorded, mode):
    base, value = recorded
    if mode == "missing":
        (base / "trial.stdout.txt").unlink()
    elif mode == "changed":
        (base / "trial.stdout.txt").write_bytes(b"different")
    elif mode == "false-hash":
        value["stdout"]["sha256"] = "0" * 64
    else:
        value["stdout"]["path"] = "../private.stdout.txt"
    (base / "receipt.json").write_text(json.dumps({"commands": [value]}))
    with pytest.raises(ValueError):
        release.vm_regression_text_inputs()


def test_every_named_failure_diagnostic_and_shard_directory_is_eligible(recorded):
    base, _ = recorded
    for directory in release.VM_REGRESSION_EVIDENCE_DIRS:
        write(base.parent / directory / "captured.stderr.txt", b"Retained output.\n")
    rows = release.vm_regression_text_inputs()
    assert {row["path"].split("/")[-2] for row in rows} == set(release.VM_REGRESSION_EVIDENCE_DIRS)
