from research.sqj.strengthening.collect_traces import indices, TOTAL

import json
import sys
import urllib.error

import pytest

from research.sqj.strengthening import collect_traces as collector


def test_pilot_and_main_samples_are_fixed_disjoint_and_unique():
    pilot, main = indices("pilot"), indices("cohort")
    assert pilot == list(range(10))
    assert main == indices("cohort")
    assert len(main) == len(set(main)) == 128
    assert main == sorted(main)
    assert not set(main) & set(pilot)
    assert all(10 <= i < TOTAL for i in main)


def test_sampling_does_not_accept_an_unknown_mode():
    import pytest
    with pytest.raises(ValueError):
        indices("favorable")


class Response:
    headers = {"X-Revision": collector.REVISION, "Set-Cookie": "not-exported"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        return b"{}"[:limit]


def test_rate_aware_retry_and_header_allowlist(monkeypatch):
    attempts, sleeps = [], []

    def request(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise urllib.error.HTTPError("https://example.invalid", 429, "rate limit", {"Retry-After": "31"}, None)
        return Response()

    monkeypatch.setattr(collector, "_previous_request_start", 0)
    monkeypatch.setattr(collector.time, "monotonic", lambda: 1000)
    monkeypatch.setattr(collector.time, "sleep", sleeps.append)
    monkeypatch.setattr(collector.urllib.request, "urlopen", request)
    raw, tries, headers = collector.fetch("https://example.invalid")
    assert raw == b"{}" and tries == 2
    assert sleeps == [30, 1, 4]
    assert headers == {"x-revision": collector.REVISION}


def test_excessive_rate_reset_is_bounded(monkeypatch):
    def request(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid", 429, "rate limit", {"RateLimit": '"api";r=0;t=601'}, None)

    monkeypatch.setattr(collector, "_previous_request_start", 0)
    monkeypatch.setattr(collector.time, "monotonic", lambda: 1000)
    monkeypatch.setattr(collector.urllib.request, "urlopen", request)
    with pytest.raises(RuntimeError, match="bounded"):
        collector.fetch("https://example.invalid")


def test_oversized_response_is_refused(monkeypatch):
    monkeypatch.setattr(collector, "MAX_BYTES", 1)
    monkeypatch.setattr(collector, "_previous_request_start", 0)
    monkeypatch.setattr(collector.time, "monotonic", lambda: 1000)
    monkeypatch.setattr(collector.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(ValueError, match="bound"):
        collector.fetch("https://example.invalid")


def test_changed_revision_keeps_raw_but_never_becomes_available(tmp_path, monkeypatch):
    raw = json.dumps({"num_rows_total": TOTAL, "rows": [{"row_idx": 4}]}).encode()
    monkeypatch.setattr(collector, "fetch", lambda url: (raw, 1, {"x-revision": "wrong"}))
    entry = collector.collect_row(4, tmp_path)
    assert entry["status"] == "unavailable"
    assert (tmp_path / entry["path"]).read_bytes() == raw
    assert entry["bytes"] == len(raw) and len(entry["sha256"]) == 64


def test_final_metadata_failure_preserves_complete_denominator(tmp_path, monkeypatch):
    output = tmp_path / "collection"
    monkeypatch.setattr(sys, "argv", ["collect", "--mode", "pilot", "--output", str(output)])
    monkeypatch.setattr(collector, "indices", lambda mode: [0, 1])

    def metadata(output, label):
        if label == "after":
            raise ValueError("changed revision")
        return {"revision": collector.REVISION}

    monkeypatch.setattr(collector, "metadata", metadata)
    monkeypatch.setattr(collector, "fetch", lambda url: (b"notice", 1, {}))
    monkeypatch.setattr(collector, "collect_row", lambda index, output: {"row_index": index, "status": "unavailable"})
    with pytest.raises(SystemExit, match="preserved"):
        collector.main()
    receipt = json.loads((output / "collection.json").read_text())
    assert receipt["selected_indices"] == [0, 1]
    assert len(receipt["entries"]) == receipt["unavailable_downloads"] == 2
    assert receipt["final_metadata_valid"] is False
    assert all(len(row["sha256"]) == 64 for row in receipt["source_notices"])


def test_existing_raw_evidence_cannot_be_overwritten(tmp_path):
    target = tmp_path / "evidence.json"
    collector.save_new(target, b"original")
    with pytest.raises(FileExistsError):
        collector.save_new(target, b"replacement")
    assert target.read_bytes() == b"original"
