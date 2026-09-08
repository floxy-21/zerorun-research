"""Offline tamper and reconstruction checks; no experimental execution."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.softwarex import verify_recovered_handoff_v6 as r
from research.softwarex import build_handoff_evidence as e


def put(base, name, value):
    path = base / name
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, bytes) else r.h.encoded(value) + b"\n"
    path.write_bytes(raw)
    return {"path": name, "bytes": len(raw), "sha256": r.h.sha(raw)}


@pytest.fixture
def simple_records(tmp_path):
    row = put(tmp_path, "nested/result.json", {"passed": True})
    pin = put(tmp_path, "RECORD_MANIFEST.json", {"files": [row]})["sha256"]
    return tmp_path, row, pin


def test_manifest_is_exhaustive_and_hash_bound(simple_records):
    root, _, pin = simple_records
    assert r.manifest(root, pin, 1)["files_verified"] == 1


@pytest.mark.parametrize("mutation", ["bytes", "missing", "extra", "manifest", "count"])
def test_manifest_refuses_tampering(simple_records, mutation):
    root, _, pin = simple_records
    if mutation == "bytes":
        (root / "nested/result.json").write_bytes(b"changed")
    elif mutation == "missing":
        # Rename only this artificial test file within its new temporary folder.
        (root / "nested/result.json").rename(root / "displaced.json")
    elif mutation == "extra":
        put(root, "unlisted.json", {})
    elif mutation == "manifest":
        put(root, "RECORD_MANIFEST.json", {"files": []})
    with pytest.raises((ValueError, FileNotFoundError)):
        r.manifest(root, pin, 2 if mutation == "count" else 1)


@pytest.mark.parametrize("path", ["../outside", "/absolute", "nested\\result.json", "nested/../result.json", "C:/file"])
def test_manifest_refuses_noncanonical_paths(simple_records, path):
    root, row, _ = simple_records
    row = {**row, "path": path}
    pin = put(root, "RECORD_MANIFEST.json", {"files": [row]})["sha256"]
    with pytest.raises(ValueError):
        r.manifest(root, pin, 1)


def test_manifest_refuses_duplicate_rows(simple_records):
    root, row, _ = simple_records
    pin = put(root, "RECORD_MANIFEST.json", {"files": [row, row]})["sha256"]
    with pytest.raises(ValueError, match="duplicate"):
        r.manifest(root, pin, 2)


def test_float_roundoff_does_not_allow_changed_counts_or_costs():
    r.comparable({"ms": 564564.5969669996, "n": 24}, {"ms": 564564.5969669997, "n": 24})
    for changed in ({"ms": 564565.0, "n": 24}, {"ms": 564564.5969669997, "n": 23},
                    {"ms": float("nan"), "n": 24}, {"ms": 564564.5969669997, "n": True}):
        with pytest.raises(ValueError):
            r.comparable(changed, {"ms": 564564.5969669997, "n": 24})


PATCH = ("diff --git a/" + r.TARGET + " b/" + r.TARGET + "\n--- a/" + r.TARGET
         + "\n+++ b/" + r.TARGET + "\n@@ -1,3 +1,3 @@\n a\n-2\n+3\n z\n")


def test_text_patch_applies_exact_context():
    assert r.apply_target_patch("a\n2\nz\n", PATCH) == "a\n3\nz\n"


@pytest.mark.parametrize("patch,source", [(PATCH, "a\n9\nz\n"),
    (PATCH.replace(r.TARGET, "other.py"), "a\n2\nz\n"),
    (PATCH.replace("-1,3", "-1,2"), "a\n2\nz\n"),
    (PATCH + "@@ -1,1 +1,1 @@\n-a\n+b\n", "a\n2\nz\n")])
def test_text_patch_rejects_other_scope_context_and_overlap(patch, source):
    with pytest.raises(ValueError):
        r.apply_target_patch(source, patch)


def capture_fixture(tmp_path):
    value = {"schema": "zerorun.benchmark-independent-pytest-shadow.v1", "exit_code": 0,
        "nodeids": ["test.py::one"], "outcomes": [{"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}]}
    value["nodeid_sha256"] = r.h.sha(r.h.encoded({"nodeids": value["nodeids"]}))
    put(tmp_path, "raw-outcomes.json", value); put(tmp_path, "runner.json", {"exit_code": 0})
    return value


@pytest.mark.parametrize("mutation", ["failed", "missing", "skip", "xfail", "hash", "duplicate"])
def test_capture_refuses_incomplete_or_unexpected_outcomes(tmp_path, mutation):
    value = capture_fixture(tmp_path)
    if mutation == "failed": value["outcomes"][0]["call"] = "failed"
    elif mutation == "missing": value["outcomes"][0]["teardown"] = None
    elif mutation == "skip": value["outcomes"][0]["call"] = "skipped"
    elif mutation == "xfail": value["outcomes"][0]["wasxfail"] = True
    elif mutation == "hash": value["nodeid_sha256"] = "0" * 64
    else:
        value["nodeids"] *= 2; value["outcomes"] *= 2
        value["nodeid_sha256"] = r.h.sha(r.h.encoded({"nodeids": value["nodeids"]}))
    put(tmp_path, "raw-outcomes.json", value)
    with pytest.raises(ValueError): r.nonfailing_capture(tmp_path, [])


def test_historical_skip_requires_exact_identity(tmp_path):
    value = capture_fixture(tmp_path); value["outcomes"][0]["call"] = "skipped"
    put(tmp_path, "raw-outcomes.json", value)
    assert r.nonfailing_capture(tmp_path, ["test.py::one"])["exit_code"] == 0
    with pytest.raises(ValueError): r.nonfailing_capture(tmp_path, ["other.py::two"])


def test_unavailable_recovered_run_is_not_success(tmp_path):
    for version in ("v5", "v6"):
        row = e.run_summary(tmp_path / version, version, "main", tmp_path, {}, tmp_path)
        assert row["state"] == "NOT_AVAILABLE" and row["execution_success_claimed"] is False


@pytest.fixture(scope="module")
def actual():
    # Only reads retained artifacts. A missing required artifact is a test
    # failure, not a silent skip or a substituted synthetic success.
    root = Path(__file__).resolve().parents[3]
    original = root / r.ORIGINAL
    return root, original


def test_actual_v5_failure_and_v6_corrected_results_reconstruct(actual):
    root, original = actual
    five = r.verify(root / (r.PREFIX + "v5/record-only"), original, version=5)
    six = r.verify(root / (r.PREFIX + "v6/record-only"), original, version=6)
    assert five["controlled_handoffs"]["complete_cases"] == 23
    assert five["controlled_handoffs"]["dispositions"]["INCOMPLETE_OR_UNSUPPORTED"] == 1
    assert six["controlled_handoffs"]["complete_cases"] == 24
    assert six["controlled_handoffs"]["complete_blocks"] == 48
    assert six["fixture_correction"]["upstream_reference"]["corrected_method_ast_matches"] is True
    assert six["host_continuity"]["sampled_continuity_checks_passed"] is False
    assert six["image_build_commands_independently_rechecked"] is True
    assert six["compatible_image_preparation"]["wheel_count"] == 16
    assert six["preflight_reconciliation"]["preflight_source_after_missing"] == ["terryyin__lizard-174"]
    variability = six["descriptive_variability"]
    assert variability["case_faster_count"] + variability["case_slower_count"] == 24
    assert variability["block_faster_count"] + variability["block_slower_count"] == 48
    assert variability["case_slower_count"] == 9
    assert variability["case_block_direction_disagreements"] == 1
    assert all(row["operations"] == 48 for row in variability["zerorun_reported_operation_phases"].values())


def test_upstream_binding_refuses_mutated_method(actual, tmp_path):
    root, _ = actual
    source = root / (r.PREFIX + "v6/upstream-fixture-reference")
    raw = (source / "testCAndCPP.py").read_text(encoding="utf-8")
    # The exact upstream file stays frozen; a different proposed derived
    # method cannot inherit its authority just by copying the receipt.
    with pytest.raises(ValueError, match="not identical"):
        r.upstream_reference(source, raw.replace("class Test_Big(unittest.TestCase):", "class Test_Big(unittest.TestCase):\n    other = 1").replace(
            "        self.assertEqual(3, result[0].cyclomatic_complexity)",
            "        self.assertEqual(4, result[0].cyclomatic_complexity)"))
