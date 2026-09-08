"""Offline campaign-integrity tests; no model, authority or runtime execution."""
from pathlib import Path

import pytest

from research.softwarex.agent_application_053 import consumer_campaign as campaign
from research.softwarex.agent_application_053 import consumer as c
from research.softwarex.agent_application_053 import validation as v


def test_sealed_campaign_rejects_changed_and_extra_raw_records(tmp_path):
    c.save(tmp_path / "raw.json", {"status": "failed"})
    campaign.seal(tmp_path)
    (tmp_path / "raw.json").write_bytes(b'{"status":"passed"}\n')
    with pytest.raises(ValueError, match="raw bytes differ"):
        campaign.verify_campaign(tmp_path)
    other = tmp_path / "other"; other.mkdir()
    c.save(other / "raw.json", {"status": "failed"}); campaign.seal(other)
    c.save(other / "extra.json", {"status": "unlisted"})
    with pytest.raises(ValueError, match="inventory differs"):
        campaign.verify_campaign(other)


def test_unsealed_campaign_never_returns_success(tmp_path):
    c.save(tmp_path / "campaign-completion.json", {"successful_application_claimed": True})
    with pytest.raises(FileNotFoundError):
        campaign.verify_campaign(tmp_path)


def test_self_consistent_but_unreviewed_bundle_is_refused(tmp_path):
    # All enclosing hashes agree. The independently frozen inspection identity
    # must still refuse this invented qualification, rather than trusting it.
    c.save(tmp_path / "inspection-bundle.json", {"claimed_eligible": 5})
    c.save(tmp_path / "campaign-protocol.json", {"schema": campaign.SCHEMA,
        "inspection_bundle_sha256": v.sha((tmp_path / "inspection-bundle.json").read_bytes())})
    c.save(tmp_path / "campaign-completion.json", {"schema": campaign.SCHEMA,
        "protocol_sha256": v.sha((tmp_path / "campaign-protocol.json").read_bytes())})
    campaign.seal(tmp_path)
    with pytest.raises(ValueError, match="campaign protocol differs"):
        campaign.verify_campaign(tmp_path)


def test_campaign_preserves_an_operator_failed_command(tmp_path):
    # Running one deliberately failing local process tests the wrapper's raw
    # retention, not the installed runtime or a fabricated model response.
    import sys, os
    with pytest.raises(ValueError, match="operator command failed"):
        campaign.raw_command(tmp_path, "failure", [sys.executable, "-B", "-c", "import sys;print('retained');sys.exit(3)"], tmp_path, dict(os.environ), 10)
    row = v.strict((tmp_path / "failure.json").read_bytes())
    assert row["returncode"] == 3
    assert (tmp_path / "failure.stdout.log").read_bytes().strip() == b"retained"
    assert (tmp_path / "failure.started.json").is_file()
