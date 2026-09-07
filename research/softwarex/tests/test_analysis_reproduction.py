"""Adversarial canonical-analysis policy tests; no new measurements or models."""
from copy import deepcopy
import hashlib
import json
import math

import pytest

from research.softwarex import analysis_reproduction as a


@pytest.fixture
def supported(monkeypatch):
    monkeypatch.setattr(a, "_runtime_identity", lambda: ("cpython", (3, 12)))


@pytest.fixture
def reference():
    return {"schema": a.SCHEMA, "completed": True,
            "counts": {"requests": 2}, "derived_float": 1.25,
            "raw": {"elapsed": 20.0, "sha256": "a" * 64, "status": "MISS_EXECUTED"},
            "ordered_requests": [1, 2],
            "nested": {a.INVENTORY_FIELD: ["z", "a"]},
            a.INVENTORY_FIELD: [
                {"path": name, "bytes": 0, "sha256": hashlib.sha256(b"").hexdigest(),
                 "classification": "zero-byte crash-flush; no inferable result or duration"}
                for name in ("z/request/invocation.json", "a/request/fast.json")]}


def test_only_named_inventory_order_is_normalized_nonmutating(supported, reference):
    actual = deepcopy(reference)
    actual[a.INVENTORY_FIELD].reverse()
    before = deepcopy(actual)
    result = a.canonical_reconciliation(actual, reference)
    assert result == reference and result is not reference
    assert actual == before
    assert result[a.INVENTORY_FIELD] is not reference[a.INVENTORY_FIELD]
    result[a.INVENTORY_FIELD][0]["path"] = "mutated result only"
    assert reference[a.INVENTORY_FIELD][0]["path"] == "z/request/invocation.json"


@pytest.mark.parametrize("version", [(3, 12), (3, 13), (3, 14)])
def test_supported_cpython_canonical_versions(monkeypatch, version):
    monkeypatch.setattr(a, "_runtime_identity", lambda: ("cpython", version))
    assert a.require_canonical_python() == {"implementation": "cpython", "major_minor": list(version)}


@pytest.mark.parametrize("implementation,version", [
    ("cpython", (3, 10)), ("cpython", (3, 11)), ("cpython", (3, 15)),
    ("cpython", (4, 0)), ("pypy", (3, 12)), ("other", (3, 14)),
])
def test_other_analysis_interpreters_get_precise_diagnostic(monkeypatch, implementation, version):
    monkeypatch.setattr(a, "_runtime_identity", lambda: (implementation, version))
    with pytest.raises(ValueError, match=r"Canonical manuscript/timing analysis requires CPython 3.12--3.14") as error:
        a.require_canonical_python()
    assert "quickstart still support Python 3.10+" in str(error.value)
    assert "No numeric tolerance" in str(error.value)


@pytest.mark.parametrize("mode", [
    "missing-field", "extra-field", "schema", "completed-integer", "count-float", "count-bool",
    "derived-next-float", "raw-next-float", "raw-type-change", "raw-hash", "status",
    "ordered-request-order", "nested-inventory-order", "missing-row", "extra-row", "duplicate-row",
    "wrong-row-path", "row-field-missing", "row-field-extra", "row-bytes-bool", "row-bytes-float",
    "row-nonzero", "row-sha", "row-classification", "inventory-tuple", "non-row", "path-type",
    "empty-path", "top-list", "tuple", "nonstring-key", "nan", "inf",
])
def test_changed_values_types_or_noninventory_order_are_never_accepted(supported, reference, mode):
    actual = deepcopy(reference)
    if mode == "missing-field":
        actual.pop("completed")
    elif mode == "extra-field":
        actual["unclaimed"] = True
    elif mode == "schema":
        actual["schema"] = "another.schema"
    elif mode == "completed-integer":
        actual["completed"] = 1
    elif mode == "count-float":
        actual["counts"]["requests"] = 2.0
    elif mode == "count-bool":
        actual["counts"]["requests"] = True
    elif mode == "derived-next-float":
        actual["derived_float"] = math.nextafter(actual["derived_float"], math.inf)
    elif mode == "raw-next-float":
        actual["raw"]["elapsed"] = math.nextafter(actual["raw"]["elapsed"], math.inf)
    elif mode == "raw-type-change":
        actual["raw"]["elapsed"] = 20
    elif mode == "raw-hash":
        actual["raw"]["sha256"] = "b" * 64
    elif mode == "status":
        actual["raw"]["status"] = "HIT_REUSED"
    elif mode == "ordered-request-order":
        actual["ordered_requests"].reverse()
    elif mode == "nested-inventory-order":
        actual["nested"][a.INVENTORY_FIELD].reverse()
    elif mode == "missing-row":
        actual[a.INVENTORY_FIELD].pop()
    elif mode in {"extra-row", "duplicate-row"}:
        row = deepcopy(actual[a.INVENTORY_FIELD][0])
        if mode == "extra-row":
            row["path"] = "new/row.json"
        actual[a.INVENTORY_FIELD].append(row)
    elif mode == "wrong-row-path":
        actual[a.INVENTORY_FIELD][0]["path"] = "changed/path.json"
    elif mode == "row-field-missing":
        actual[a.INVENTORY_FIELD][0].pop("classification")
    elif mode == "row-field-extra":
        actual[a.INVENTORY_FIELD][0]["invented"] = True
    elif mode == "row-bytes-bool":
        actual[a.INVENTORY_FIELD][0]["bytes"] = False
    elif mode == "row-bytes-float":
        actual[a.INVENTORY_FIELD][0]["bytes"] = 0.0
    elif mode == "row-nonzero":
        actual[a.INVENTORY_FIELD][0]["bytes"] = 1
    elif mode == "row-sha":
        actual[a.INVENTORY_FIELD][0]["sha256"] = "f" * 64
    elif mode == "row-classification":
        actual[a.INVENTORY_FIELD][0]["classification"] = "Changed classification"
    elif mode == "inventory-tuple":
        actual[a.INVENTORY_FIELD] = tuple(actual[a.INVENTORY_FIELD])
    elif mode == "non-row":
        actual[a.INVENTORY_FIELD][0] = "not a row"
    elif mode == "path-type":
        actual[a.INVENTORY_FIELD][0]["path"] = 1
    elif mode == "empty-path":
        actual[a.INVENTORY_FIELD][0]["path"] = ""
    elif mode == "top-list":
        actual = [actual]
    elif mode == "tuple":
        actual["ordered_requests"] = (1, 2)
    elif mode == "nonstring-key":
        actual[True] = "invalid object key"
    elif mode == "nan":
        actual["derived_float"] = math.nan
    else:
        actual["derived_float"] = math.inf
    with pytest.raises(ValueError):
        a.canonical_reconciliation(actual, reference)


def test_duplicate_reference_inventory_rejected_even_when_actual_is_identical(supported, reference):
    reference[a.INVENTORY_FIELD].append(deepcopy(reference[a.INVENTORY_FIELD][0]))
    with pytest.raises(ValueError, match="duplicate path"):
        a.canonical_reconciliation(deepcopy(reference), reference)


def test_archived_reference_is_hash_anchored_before_comparison(supported, reference, monkeypatch):
    from research.softwarex import analyze_operating_region as operating
    raw = json.dumps(reference).encode()
    monkeypatch.setattr(operating, "read_regular", lambda root, name: raw)
    monkeypatch.setattr(operating, "PINNED", {operating.SAVED: hashlib.sha256(raw).hexdigest()})
    assert a.reconcile_saved_analysis(deepcopy(reference)) == reference
    monkeypatch.setattr(operating, "read_regular", lambda root, name: raw + b" ")
    with pytest.raises(ValueError, match="frozen saved reconciliation bytes changed"):
        a.reconcile_saved_analysis(deepcopy(reference))


def test_cli_is_read_only_and_uses_raw_reconciliation(supported, reference, monkeypatch, capsys, tmp_path):
    from research.softwarex import analyze_operating_region as operating
    calls = []
    def reconcile(root):
        calls.append(root)
        return deepcopy(reference), ["artificial anchor"]
    monkeypatch.setattr(operating, "reconcile", reconcile)
    assert a.main(["--check", "--root", str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert calls == [tmp_path.resolve()]
    assert result["passed"] and result["check_only"]
    assert result["numeric_tolerance_applied"] is False
    assert result["order_insensitive_field"] == a.INVENTORY_FIELD
    assert list(tmp_path.iterdir()) == []


def test_final_paper_rejects_unsupported_analysis_before_work(monkeypatch):
    from research.softwarex import build_paper as paper
    monkeypatch.setattr(a, "_runtime_identity", lambda: ("cpython", (3, 10)))
    def no_work(*args, **kwargs):
        raise AssertionError("unsupported canonical analysis should fail before work")
    monkeypatch.setattr(paper, "original_analysis", no_work)
    with pytest.raises(ValueError, match="CPython 3.12--3.14"):
        paper.build(preview=False)


def test_operating_region_rejects_unsupported_analysis_before_reads(monkeypatch, tmp_path):
    from research.softwarex import analyze_operating_region as operating
    monkeypatch.setattr(a, "_runtime_identity", lambda: ("cpython", (3, 11)))
    def no_reads(*args, **kwargs):
        raise AssertionError("unsupported canonical analysis should fail before reads")
    monkeypatch.setattr(operating, "anchored_inputs", no_reads)
    with pytest.raises(ValueError, match="CPython 3.12--3.14"):
        operating.reconcile(tmp_path)


def test_canonical_policy_is_not_imported_by_runtime_or_quickstart():
    from pathlib import Path
    root = Path(a.__file__).resolve().parents[2]
    paths = [root / "research/softwarex/quickstart_check.py", *(root / "zerorun").glob("*.py")]
    for path in paths:
        assert "analysis_reproduction" not in path.read_text(encoding="utf-8")


def test_real_derived_binding_records_static_policy_and_exact_helper_source():
    from research.softwarex import analyze_operating_region as operating
    result = operating.analyze()
    policy = result["binding"]["canonical_replay_policy"]
    assert policy == {"implementation": "CPython", "supported_versions": ["3.12", "3.13", "3.14"],
                      "order_insensitive_field": a.INVENTORY_FIELD, "zero_byte_inventory_order_only": True,
                      "numeric_tolerance_applied": False}
    rows = result["binding"]["input_files"]
    bound = [row for row in rows if row["path"] == "research/softwarex/analysis_reproduction.py"]
    assert len(bound) == 1
    assert bound[0]["sha256"] == hashlib.sha256(operating.read_regular(operating.ROOT, bound[0]["path"])).hexdigest()
