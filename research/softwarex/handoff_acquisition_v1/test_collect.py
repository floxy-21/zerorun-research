from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

spec = importlib.util.spec_from_file_location("handoff_acquisition_collector", Path(__file__).with_name("collect.py"))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def row(repo, index, **changes):
    value = {"repo": repo, "instance_id": repo.replace("/", "__") + f"-{index}",
             "base_commit": f"{index:040x}",
             "license_name": "MIT License",
             "patch": "diff --git a/src.py b/src.py\n--- a/src.py\n+++ b/src.py\n@@ -1 +1 @@\n-a\n+b\n",
             "test_patch": "diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-a\n+b\n"}
    value.update(changes)
    return value


def test_fixed_main_and_pilot_selection_are_disjoint_and_not_outcome_based():
    rows = [row(repo, index) for repo in collector.SHORTLIST for index in range(1, 6)]
    selected = collector.select(rows)
    assert selected["selection_complete"] is True
    assert len(selected["main"]) == 30 and len(selected["pilot"]) == 2
    assert list(dict.fromkeys(case["repo"] for case in selected["main"])) == list(collector.SHORTLIST[:10])
    assert not ({case["case_id"] for case in selected["main"]} & {case["case_id"] for case in selected["pilot"]})
    for entry in rows:
        entry.update(resolved=False, duration=999999, FAIL_TO_PASS=["anything"])
    changed = collector.select(list(reversed(rows)))
    for phase in ("pilot", "main"):
        assert [case["case_id"] for case in selected[phase]] == [case["case_id"] for case in changed[phase]]


def test_underpopulated_and_missing_repositories_are_not_forced_into_cohort():
    rows = [row(collector.SHORTLIST[0], 1), row(collector.SHORTLIST[0], 2)]
    result = collector.select(rows)
    assert result["selection_complete"] is False
    assert result["main"] == result["pilot"] == []
    assert len(result["repository_ledger"]) == 20
    assert result["repository_ledger"][0]["valid_count"] == 2
    assert result["repository_ledger"][1]["candidate_count"] == 0


def test_missing_spare_pilot_cases_remains_incomplete():
    rows = [row(repo, index) for repo in collector.SHORTLIST[:10] for index in range(1, 4)]
    result = collector.select(rows)
    assert len(result["main"]) == 30 and result["pilot"] == []
    assert result["selection_complete"] is False


@pytest.mark.parametrize("target", ["../test_x.py", "/test_x.py", "tests/../test_x.py", ".git/test_x.py", ".zerorun/test_x.py", "-x.py", "tests/--x.py", "tests\\test_x.py", "tests/test x.py", "C:/test_x.py", "tests//test_x.py"])
def test_unsafe_targets_rejected(target):
    with pytest.raises(ValueError):
        collector.safe_target(target)


@pytest.mark.parametrize("change", [
    {"base_commit": "main"}, {"base_commit": "A" * 40}, {"instance_id": "../../bad"}, {"license_name": ""},
    {"patch": ""}, {"patch": "not a diff"}, {"test_patch": ""},
    {"test_patch": "diff --git a/readme b/readme\n+++ b/readme\n@@ -1 +1 @@\n-x\n+y\n"},
    {"test_patch": "diff --git a/x b/x\n+++ b/../x.py\n@@ -1 +1 @@\n-x\n+y\n"},
])
def test_invalid_candidates_are_retained_in_ledger(change):
    result = collector.select([row(collector.SHORTLIST[0], 1, **change)])
    assert len(result["invalid_cases"]) == 1
    assert result["repository_ledger"][0]["valid_count"] == 0


def test_duplicate_identifiers_are_all_excluded_not_first_wins():
    repeated = row(collector.SHORTLIST[0], 1)
    result = collector.select([repeated, repeated])
    assert len(result["invalid_cases"]) == 2
    assert all("duplicate" in item["reason"] for item in result["invalid_cases"])


def test_freeze_is_saved_before_first_request_and_failure_is_preserved(monkeypatch, tmp_path):
    output = tmp_path / "new"
    def fail_download(actual_output, relative, url, limit):
        assert (output / "plan.json").is_file()
        raise RuntimeError("controlled acquisition failure")
    monkeypatch.setattr(collector, "download", fail_download)
    result = collector.collect(output)
    assert result["completed"] is False
    assert "controlled acquisition failure" in result["error"]
    assert (output / "receipt.json").is_file()
    with pytest.raises(FileExistsError):
        collector.collect(output)


def test_metadata_serialization_refuses_nonfinite_data():
    result = collector.select([row(collector.SHORTLIST[0], 1, score=float("nan"))])
    assert len(result["invalid_cases"]) == 1


def test_download_byte_limit_retains_partial_receipt_without_retry(monkeypatch, tmp_path):
    class Response:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, length): return b"x" * length
    calls = []
    def open_url(*args, **kwargs):
        calls.append(args)
        return Response()
    monkeypatch.setattr(collector.urllib.request, "urlopen", open_url)
    with pytest.raises(RuntimeError, match="byte bound"):
        collector.download(tmp_path, "archive.tar.gz", "https://example.invalid/archive", 10)
    assert len(calls) == 1
    assert (tmp_path / "archive.tar.gz.download.json").is_file()
    assert (tmp_path / "archive.tar.gz").stat().st_size <= 10


def test_global_row_indices_survive_unselected_rows():
    selected = collector.select([None, {"repo": "not/selected"}, row(collector.SHORTLIST[0], 1),
                                 row(collector.SHORTLIST[0], 2), row(collector.SHORTLIST[0], 3)])
    assert {case["row_index"] for case in selected["main"]} == {2, 3, 4}


def test_more_than_sixteen_targets_is_invalid():
    patch = "".join(f"diff --git a/test_{i}.py b/test_{i}.py\n+++ b/test_{i}.py\n@@ -1 +1 @@\n-x\n+y\n" for i in range(17))
    with pytest.raises(ValueError, match="sixteen"):
        collector.patch_targets(patch)


def test_complete_collector_shapes_and_retained_archive_failure(monkeypatch, tmp_path):
    rows = [row(repo, index) for repo in collector.SHORTLIST[:10] for index in range(1, 6)]
    monkeypatch.setattr(collector, "TOTAL_ROWS", len(rows))
    arrow = ModuleType("pyarrow")
    arrow.__version__ = "23.0.1"
    parquet = ModuleType("pyarrow.parquet")
    class Batch:
        def __init__(self, rows): self.rows = rows
        def to_pylist(self): return self.rows
    class ParquetFile:
        def __init__(self, path): self.path = path
        def iter_batches(self, batch_size):
            assert batch_size == 128
            yield Batch(rows[:25] if "00000" in self.path.name else rows[25:])
    parquet.ParquetFile = ParquetFile
    arrow.parquet = parquet
    monkeypatch.setitem(sys.modules, "pyarrow", arrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet)
    failed = []
    def fake_download(output, relative, url, limit):
        assert (output / "plan.json").is_file()
        if relative == "dataset-before.json":
            return collector.save_json(output, relative, {"sha": collector.REVISION, "cardData": {"license": "cc-by-4.0"}})
        if relative.endswith("source.tar.gz"):
            assert (output / "selection.json").is_file()
            if not failed:
                failed.append(relative)
                raise RuntimeError("controlled archive unavailable")
        bound = collector.save_new(output / relative, b"inert fixture bytes")
        bound["path"] = relative
        return bound
    monkeypatch.setattr(collector, "download", fake_download)
    output = tmp_path / "collect"
    receipt = collector.collect(output)
    assert receipt["completed"] is False
    pilot = json.loads((output / "pilot.json").read_bytes())
    main = json.loads((output / "main.json").read_bytes())
    assert pilot["schema"] == main["schema"] == "zerorun.handoff-selection.v1"
    assert pilot["phase"] == "pilot" and main["phase"] == "main"
    assert len(pilot["cases"]) == 2 and len(main["cases"]) == 30
    assert pilot["selection_complete"] is True
    assert pilot["acquisition_complete"] is False and main["acquisition_complete"] is True
    assert pilot["cases"][0]["disposition"] == "UNAVAILABLE_ACQUISITION"
    assert pilot["cases"][0]["source_archive"] is None
    for case in pilot["cases"] + main["cases"]:
        metadata = case["metadata"]
        raw = (output / metadata["path"]).read_bytes()
        assert len(raw) == metadata["bytes"] and collector.sha(raw) == metadata["sha256"]
        wrapper = json.loads(raw)
        assert wrapper["partial"] is False
        assert len(wrapper["rows"]) == 1
        assert wrapper["rows"][0]["truncated_cells"] == []
        assert wrapper["rows"][0]["row"]["instance_id"] == case["case_id"]
