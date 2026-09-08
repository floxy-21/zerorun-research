"""Refuse pre-existing model attempts or changed sources in a continuation."""
from pathlib import Path

import pytest

from research.softwarex.agent_application_053 import consumer_continuation_v2 as c
from research.softwarex.agent_application_053 import validation as v


def test_wrong_original_export_is_not_an_authorized_continuation(tmp_path):
    (tmp_path / "RECORD_MANIFEST.json").write_bytes(b'{"files":[]}\n')
    with pytest.raises(ValueError, match="original preparation export differs"):
        c.verify_prior(tmp_path)


def test_restoration_refuses_source_drift_before_mutating(tmp_path):
    root = tmp_path / "source"; root.mkdir(); (root / ".git").mkdir()
    module = root / "module.py"; module.write_bytes(b"unexpected new edit")
    q = {"root": str(root), "states": {"final": {"files": [{"path": "module.py", "bytes": 8, "sha256": v.sha(b"repaired")}]}}}
    with pytest.raises(ValueError, match="outside exact final state"):
        c.restore(tmp_path, q, {})
    assert module.read_bytes() == b"unexpected new edit"


def test_unsealed_continuation_cannot_claim_application(tmp_path):
    (tmp_path / "completion.json").write_bytes(b'{"successful_application_claimed":true}\n')
    with pytest.raises(FileNotFoundError):
        c.verify_campaign(tmp_path)
