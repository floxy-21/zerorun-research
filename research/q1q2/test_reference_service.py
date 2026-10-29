"""Component challenges; live Docker contract checks are separate evidence."""
import json
import os
from pathlib import Path
from unittest.mock import patch

from research.q1q2 import reference_service as ref


def fixture(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".zerorun").mkdir()
    (root / "value.txt").write_text("A")
    manifest = {"tasks": {"tests": {"command": ["python", "check.py"], "image": "test@sha256:" + "0"*64,
                "env": [], "outputs": [], "unsafe_effects": [], "result_only": True}}}
    (root / ".zerorun.json").write_text(json.dumps(manifest))
    key = tmp_path / "private.key"
    key.write_bytes(b"k"*32)
    key.chmod(0o600)
    return root, key


def successful(snapshot, task):
    assert (snapshot / ".git").is_dir() and (snapshot / ".zerorun").is_dir()
    assert snapshot.stat().st_mtime_ns == 0
    assert (snapshot / "value.txt").stat().st_mtime_ns == 0
    return {"exit_code": 0, "historical": False, "stdout_tail": "fresh", "stderr_tail": ""}


def test_success_reused_but_forged_receipt_executes(tmp_path):
    root, key = fixture(tmp_path)
    with patch.object(ref, "runtime", return_value={"id": "fixed"}), patch.object(ref, "fresh", side_effect=successful) as run:
        assert ref.request(root, "receipt", key)["status"] == "MISS_EXECUTED"
        hit = ref.request(root, "receipt", key)
        assert hit["status"] == "HIT_REUSED" and hit["stdout_tail"] == "" and hit["historical"]
        receipt, = (root / ".zerorun/reference").glob("*.json")
        data = json.loads(receipt.read_text()); data["hmac"] = "0"*64; receipt.write_text(json.dumps(data))
        assert ref.request(root, "receipt", key)["status"] == "MISS_EXECUTED"
        assert run.call_count == 2


def test_changed_bytes_and_root_mode_invalidate(tmp_path):
    root, key = fixture(tmp_path)
    with patch.object(ref, "runtime", return_value={"id": "fixed"}), patch.object(ref, "fresh", side_effect=successful):
        first = ref.request(root, "receipt", key)
        original = (root / "value.txt").stat()
        (root / "value.txt").write_text("B")
        os.utime(root / "value.txt", ns=(original.st_atime_ns, original.st_mtime_ns))
        changed = ref.request(root, "receipt", key)
        assert changed["status"] == "MISS_EXECUTED" and changed["cache_key"] != first["cache_key"]
        root.chmod(0o750)
        mode = ref.request(root, "receipt", key)
        assert mode["status"] == "MISS_EXECUTED" and mode["cache_key"] != changed["cache_key"]


def test_failure_is_never_reused(tmp_path):
    root, key = fixture(tmp_path)
    with patch.object(ref, "runtime", return_value={"id": "fixed"}), patch.object(ref, "fresh", return_value={"exit_code": 1}) as run:
        assert ref.request(root, "receipt", key)["exit_code"] == 1
        assert ref.request(root, "receipt", key)["status"] == "MISS_EXECUTED"
        assert run.call_count == 2
        assert not list((root / ".zerorun/reference").glob("*.json"))
