"""Offline tests only: no model, Docker, downloaded-source execution or authority."""
import copy
import io
import json
from pathlib import Path
import tarfile

import pytest

from research.softwarex.handoff_v1 import run as h


def case():
    return {"case_id": "example__library-1", "repo": "example/library", "base_commit": "a" * 40,
            "targets": ["tests/test_issue.py"]}


def wrapper():
    return {"partial": False, "rows": [{"truncated_cells": [], "row": {
        "instance_id": "example__library-1", "repo": "example/library", "base_commit": "a" * 40,
        "license_name": "MIT License", "patch": "some patch", "test_patch": ""}}]}


@pytest.mark.parametrize("path", ["../bad", "/bad", "a//b", "a/./b", "a/../b", "a\\b", "a:b", "a\x00b", "", "a\nb"])
def test_bad_paths(path):
    with pytest.raises(ValueError):
        h.literal(path)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}'])
def test_strict_json(raw):
    with pytest.raises(ValueError):
        h.strict(raw)


def test_order_balanced_and_stable():
    for name in ("a", "b", "c"):
        assert h.order(name, 0) == list(reversed(h.order(name, 1)))
        assert h.order(name, 0) == h.order(name, 0)
    with pytest.raises(ValueError):
        h.order("a", 2)


def test_ledger_bounds_and_unavailable():
    good = {"schema": "zerorun.handoff-selection.v1", "phase": "pilot", "cases": [case()]}
    assert h.validate_ledger(good) == [case()]
    unavailable = {"case_id": "missing", "repo": "a/b", "disposition": "UNAVAILABLE_ACQUISITION", "error": "not downloadable"}
    good["cases"].append(unavailable)
    assert len(h.validate_ledger(good)) == 2
    good["cases"].append(dict(unavailable, case_id="missing2"))
    with pytest.raises(ValueError):
        h.validate_ledger(good)


@pytest.mark.parametrize("key,value", [("case_id", "../escape"), ("repo", "x/y/z"), ("base_commit", "main"),
    ("targets", ["--ignore=tests.py"]), ("targets", []), ("targets", [".zerorun/x.py"]),
    ("targets", ["tests/test_issue.py", "tests/test_issue.py"])])
def test_bad_case(key, value):
    row = case()
    row[key] = value
    with pytest.raises(ValueError):
        h.validate_ledger({"schema": "zerorun.handoff-selection.v1", "phase": "pilot", "cases": [row]})


def test_duplicate_ids():
    with pytest.raises(ValueError):
        h.validate_ledger({"schema": "zerorun.handoff-selection.v1", "phase": "main", "cases": [case(), case()]})


def test_metadata_identity():
    data = wrapper()
    assert h.metadata_row(json.dumps(data).encode(), case())["repo"] == "example/library"
    data["rows"][0]["row"]["base_commit"] = "b" * 40
    with pytest.raises(ValueError):
        h.metadata_row(json.dumps(data).encode(), case())


@pytest.mark.parametrize("change", ["partial", "truncated", "license", "duplicate", "patch"])
def test_metadata_fail_closed(change):
    data = wrapper()
    if change == "partial":
        data["partial"] = True
    elif change == "truncated":
        data["rows"][0]["truncated_cells"] = ["patch"]
    elif change == "license":
        data["rows"][0]["row"]["license_name"] = ""
    elif change == "duplicate":
        data["rows"].append(copy.deepcopy(data["rows"][0]))
    else:
        data["rows"][0]["row"]["patch"] = ""
    with pytest.raises(ValueError):
        h.metadata_row(json.dumps(data).encode(), case())


def test_patch_paths():
    diff = "diff --git a/tests/test_x.py b/tests/test_x.py\n--- a/tests/test_x.py\n+++ b/tests/test_x.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert h.patch_paths(diff) == ["tests/test_x.py"]
    assert h.patch_paths("") == []
    for bad in (diff.replace("tests/test_x.py", "../bad"), diff.replace("tests/test_x.py", ".git/config"),
                diff + "new file mode 120000\n", diff + "GIT binary patch\n", diff + "rename from a\n", diff * 2):
        with pytest.raises(ValueError):
            h.patch_paths(bad)


def archive(kind="normal", commit="a" * 40):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, raw in (("LICENSE", b"MIT"), ("tests/test_issue.py", b"def test_issue(): assert True\n")):
            info = tarfile.TarInfo("library-" + commit + "/" + name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
        if kind == "link":
            info = tarfile.TarInfo("library-" + commit + "/evil")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        if kind == "traversal":
            info = tarfile.TarInfo("library-" + commit + "/../escape")
            tar.addfile(info, io.BytesIO(b""))
    return buffer.getvalue()


def test_archive_plain_only(tmp_path):
    root = tmp_path / "source"
    result = h.extract_source(archive(), root, "a" * 40)
    assert result["members"] == 2
    assert (root / "LICENSE").read_bytes() == b"MIT"


@pytest.mark.parametrize("kind", ["link", "traversal"])
def test_archive_adverse(tmp_path, kind):
    with pytest.raises(ValueError):
        h.extract_source(archive(kind), tmp_path / "source", "a" * 40)
    assert not (tmp_path / "source").exists()


def test_archive_wrong_commit(tmp_path):
    with pytest.raises(ValueError):
        h.extract_source(archive(), tmp_path / "source", "b" * 40)


def test_archive_checks_members_incrementally(tmp_path, monkeypatch):
    raw = archive()
    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded member-list allocation")
    monkeypatch.setattr(tarfile.TarFile, "getmembers", forbidden)
    assert h.extract_source(raw, tmp_path / "source", "a" * 40)["members"] == 2


def test_binding_mutation(tmp_path):
    path = tmp_path / "record.json"
    path.write_bytes(b"{}")
    row = h.record(path)
    assert h.bound(tmp_path, row) == b"{}"
    path.write_bytes(b"[]")
    with pytest.raises(ValueError):
        h.bound(tmp_path, row)


def test_identity_preserves_case_and_source(tmp_path):
    (tmp_path / "A.py").write_bytes(b"first")
    (tmp_path / "z.py").write_bytes(b"last")
    first = h.identity(tmp_path)
    (tmp_path / ".zerorun").mkdir()
    (tmp_path / ".zerorun/state.json").write_bytes(b"cache")
    assert h.identity(tmp_path) == first
    (tmp_path / "A.py").write_bytes(b"changed")
    assert h.identity(tmp_path) != first


def test_bounded_product_output():
    sink = h.Tail(4)
    assert sink.write(b"abcdef") == 6
    assert sink.value() == {"text": "cdef", "captured_bytes": 4, "emitted_bytes": 6, "truncated": True}


def test_operation_preserves_failure(tmp_path):
    def fail():
        raise ValueError("expected failure")
    with pytest.raises(ValueError):
        h.operation(tmp_path, "failed", fail)
    row = h.strict((tmp_path / "failed.json").read_bytes())
    assert row["result"] is None and row["error"]["message"] == "expected failure"
    assert row["outer_ms"] >= 0
    assert (tmp_path / "failed.started.json").is_file()


def test_complete_cost_includes_cold_producer_and_excludes_oracle():
    def op(ms, status="MISS_EXECUTED"):
        return {"outer_ms": ms, "result": {"status": status}}
    rows = {"fresh": {"producer": op(100), "consumer": op(100), "oracle": op(500), "setup_outer_ms": 10},
            "zerorun": {"producer": op(250), "consumer": op(20, "HIT_REUSED"), "oracle": op(500),
                        "diagnostics": op(200), "setup_outer_ms": 10}}
    result = h.summarize_block(rows)
    assert result["chain_ms"] == {"fresh": 200, "zerorun": 270}
    assert result["consumer_saved_fraction"] == .8
    assert result["chain_saved_fraction"] == pytest.approx(-.35)
    assert result["fresh_diagnostics_ms"] == 200
    assert result["oracle_ms"] == 1000


def test_empty_or_incomplete_oracle_refused():
    for row in ({"complete_per_node_outcomes": False}, {"complete_per_node_outcomes": True, "nodeids": [], "outcomes": []}):
        with pytest.raises(ValueError):
            h.outcome(row)


def test_no_mcp_authorization_calls_in_module():
    text = Path(h.__file__).read_text()
    assert "authorize(" not in text and "prepare_pytest(" not in text and "activate_reviewed" not in text
