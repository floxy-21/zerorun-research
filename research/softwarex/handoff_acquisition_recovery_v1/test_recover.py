"""Tiny offline transport fixtures, not acquisition or execution evidence."""
from copy import deepcopy
import io
import json
from pathlib import Path

import pytest

from . import recover as r


def put(base, relative, value):
    return r.original.save_json(base, relative, value)


@pytest.fixture
def prior(tmp_path):
    base = tmp_path / "original"
    base.mkdir()
    plan = put(base, "plan.json", r.original.protocol_plan())
    raw = {
        "dataset-before.json": r.original.encoded({"sha": r.original.REVISION, "cardData": {"license": "cc-by-4.0"}}),
        "source-notices/README.md": b"CC-BY-4.0 offline fixture",
        r.FIRST: b"PAR1first fixturePAR1",
        r.SECOND: b"PAR1partial fixture",
    }
    files = {}
    for name, data in raw.items():
        r.original.save_new(base / name, data)
        files[name] = r.file_record(base, name)
        parquet = name in r.original.PARQUETS
        record = {**files[name], "url": r.parquet_url(name) if parquet else "https://example.invalid/fixture",
                  "attempts": 1, "limit_bytes": r.original.PARQUET_LIMIT if parquet else r.original.METADATA_LIMIT,
                  "status": "unavailable" if name == r.SECOND else "ok"}
        if name == r.SECOND:
            record["error"] = "TimeoutError: download exceeded 180-second wall limit"
        put(base, name + ".download.json", record)
    put(base, "receipt.json", {"schema": "zerorun.handoff-acquisition.v1", "plan": plan,
        "completed": False, "code_executed": False, "archives_extracted": False,
        "metadata_before": files["dataset-before.json"], "source_notice": files["source-notices/README.md"],
        "error": "RuntimeError: download unavailable: " + r.SECOND + ": TimeoutError: download exceeded 180-second wall limit"})
    return base


def replace_json(path, value):
    path.write_bytes(r.original.encoded(value))


def test_original_plan_and_all_partial_bytes_are_bound(prior):
    rows = r.prior_inventory(prior)
    assert len(rows) == len(r.SMALL) + 4
    assert any(row["path"] == r.SECOND for row in rows)


@pytest.mark.parametrize("mode", ["selection", "pilot", "main", "seed", "source", "partial-byte", "first-byte", "reason", "prior-success", "plan-hash", "duplicate-json"])
def test_wrong_prior_attempt_is_not_recovered(prior, mode):
    plan = json.loads((prior / "plan.json").read_bytes())
    receipt = json.loads((prior / "receipt.json").read_bytes())
    if mode in {"selection", "pilot", "main"}:
        put(prior, mode + ".json", {})
    elif mode in {"seed", "source"}:
        plan["seed" if mode == "seed" else "source_sha256"] = "changed"
        replace_json(prior / "plan.json", plan)
    elif mode in {"partial-byte", "first-byte"}:
        (prior / (r.SECOND if mode == "partial-byte" else r.FIRST)).write_bytes(b"changed")
    elif mode == "reason":
        path = prior / (r.SECOND + ".download.json")
        record = json.loads(path.read_bytes())
        record["error"] = "HTTPError: 404"
        replace_json(path, record)
    elif mode == "prior-success":
        receipt["completed"] = True
        replace_json(prior / "receipt.json", receipt)
    elif mode == "plan-hash":
        receipt["plan"]["sha256"] = "0" * 64
        replace_json(prior / "receipt.json", receipt)
    else:
        (prior / "receipt.json").write_bytes(b'{"completed":false,"completed":true}')
    with pytest.raises(ValueError):
        r.prior_inventory(prior)


class Response(io.BytesIO):
    def __init__(self, data, status=200, length=None):
        super().__init__(data)
        self.status = status
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def test_second_download_one_full_request_and_record(tmp_path, monkeypatch):
    calls = []
    def network(request, timeout):
        calls.append((request.full_url, timeout, dict(request.header_items())))
        return Response(b"PAR1fixturePAR1")
    monkeypatch.setattr(r.urllib.request, "urlopen", network)
    entry = r.second_download(tmp_path, r.SECOND, r.parquet_url(r.SECOND), r.original.PARQUET_LIMIT)
    assert entry == r.file_record(tmp_path, r.SECOND)
    assert len(calls) == 1 and calls[0][1] == 45
    assert not any(key.lower() == "range" for key in calls[0][2])
    record = r.read(tmp_path, r.SECOND + ".download.json")
    assert record["resume_used"] is False and record["deadline_between_reads_seconds"] == 600


@pytest.mark.parametrize("mode", ["truncated", "not-parquet", "partial-http", "bad-length", "timeout", "over-limit"])
def test_failed_new_download_is_retained_without_retry(tmp_path, monkeypatch, mode):
    calls = []
    def network(*args, **kwargs):
        calls.append(True)
        if mode == "timeout":
            raise TimeoutError("offline socket fixture")
        if mode == "truncated":
            return Response(b"PAR1shortPAR1", length=100)
        if mode == "not-parquet":
            return Response(b"html error page")
        if mode == "partial-http":
            return Response(b"PAR1fixturePAR1", status=206)
        if mode == "bad-length":
            return Response(b"PAR1fixturePAR1", length="wrong")
        return Response(b"PAR1fixturePAR1", length=r.original.PARQUET_LIMIT + 1)
    monkeypatch.setattr(r.urllib.request, "urlopen", network)
    with pytest.raises(RuntimeError):
        r.second_download(tmp_path, r.SECOND, r.parquet_url(r.SECOND), r.original.PARQUET_LIMIT)
    assert len(calls) == 1
    record = r.read(tmp_path, r.SECOND + ".download.json")
    assert record["status"] == "unavailable"
    assert (tmp_path / r.SECOND).is_file()
    assert r.file_record(tmp_path, r.SECOND)["sha256"] == record["sha256"]


@pytest.mark.parametrize("relative,url,limit", [
    (r.FIRST, r.parquet_url(r.FIRST), r.original.PARQUET_LIMIT),
    (r.SECOND, "https://example.invalid", r.original.PARQUET_LIMIT),
    (r.SECOND, r.parquet_url(r.SECOND), r.original.PARQUET_LIMIT + 1),
])
def test_larger_budget_cannot_apply_to_other_requests(tmp_path, relative, url, limit):
    with pytest.raises(ValueError):
        r.second_download(tmp_path, relative, url, limit)


def test_scoped_wrapper_preserves_original_and_selection_functions(prior, tmp_path, monkeypatch):
    initial = r.prior_inventory(prior)
    original_select = r.original.select
    original_download = r.original.download
    output = tmp_path / "recovery"
    calls = []

    def fake_collect(destination):
        destination.mkdir()
        plan = r.original.protocol_plan()
        assert r.original.select is original_select
        assert plan["seed"] == r.original.SEED and plan["shortlist"] == list(r.original.SHORTLIST)
        assert plan["download_deadline_between_reads_seconds"] == 180
        assert plan["transport_recovery"]["second_parquet_deadline_between_reads_seconds"] == 600
        put(destination, "plan.json", plan)
        for name in r.REUSED:
            limit = r.original.PARQUET_LIMIT if name == r.FIRST else r.original.METADATA_LIMIT
            entry = r.original.download(destination, name, "unused fixture URL", limit)
            assert entry == r.file_record(prior, name)
        entry = r.original.download(destination, r.SECOND, r.parquet_url(r.SECOND), r.original.PARQUET_LIMIT)
        assert entry["bytes"] > 0
        result = {"completed": True}
        put(destination, "receipt.json", result)
        return result

    def network(request, timeout):
        assert (output / "plan.json").is_file()  # Freeze before the first network call.
        assert (output / "initial-attempt/receipt.json").read_bytes() == (prior / "receipt.json").read_bytes()
        calls.append(request.full_url)
        return Response(b"PAR1new fixturePAR1")

    monkeypatch.setattr(r.original, "collect", fake_collect)
    monkeypatch.setattr(r.urllib.request, "urlopen", network)
    result = r.recover(prior, output)
    assert result["completed"] is True and result["original_inputs_unchanged"] is True
    assert r.prior_inventory(prior) == initial
    assert calls == [r.parquet_url(r.SECOND)]
    assert r.original.select is original_select and r.original.download is original_download


def test_existing_output_and_prior_subdirectory_refused_before_network(prior, monkeypatch):
    monkeypatch.setattr(r.urllib.request, "urlopen", lambda *a, **k: pytest.fail("unexpected network"))
    for destination in (prior, prior / "nested"):
        with pytest.raises(ValueError):
            r.recover(prior, destination)
