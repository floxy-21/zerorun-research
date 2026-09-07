"""Offline tests only: synthetic rows and inert archive/Git preparation."""
from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile

import pytest

from . import run as r
from . import validate as v


def node(call="passed", xfail=False):
    return {"setup": "passed", "call": call, "teardown": "passed", "wasxfail": xfail}


def oracle(exit_code, nodes):
    return {"result": {"verdict": {"exit_code": exit_code, "nodes": nodes}}}


def producer_summary(**overrides):
    return {"model_invocation_attempted": True, "producer_completed": True,
            "boundary_pass": True, "source_patch_present": True, **overrides}


def test_verified_fix_requires_actual_before_failure_and_after_pass():
    result = r.fresh_classification(producer_summary(), oracle(1, {"test": node("failed")}), oracle(0, {"test": node()}))
    assert result["fresh_regression_repaired"] and result["completed_verified_fix"]
    assert result["producer_completed"] and result["final_fresh_pass"]


@pytest.mark.parametrize("baseline", [None, oracle(0, {"test": node()}), oracle(2, {"test": node("failed")}),
    oracle(1, {"test": node("failed", True)}), oracle(1, {"test": {"setup": "failed", "call": None, "teardown": "passed", "wasxfail": False}})])
def test_passing_unavailable_collection_or_xfail_baseline_never_counts_resolved(baseline):
    result = r.fresh_classification(producer_summary(), baseline, oracle(0, {"test": node()}))
    assert result["final_fresh_pass"] and not result["completed_verified_fix"]


@pytest.mark.parametrize("producer", [producer_summary(producer_completed=False), producer_summary(boundary_pass=False),
    producer_summary(source_patch_present=False), {}])
def test_completion_scope_and_nonempty_patch_are_separate_gates(producer):
    result = r.fresh_classification(producer, oracle(1, {"test": node("failed")}), oracle(0, {"test": node()}))
    assert result["fresh_regression_repaired"] and not result["completed_verified_fix"]


@pytest.mark.parametrize("final", [None, oracle(1, {"test": node("failed")}), oracle(0, {"other": node()}),
    oracle(0, {"test": node("skipped")}), oracle(0, {"test": node("passed", True)})])
def test_final_failure_skip_xfail_or_changed_collection_never_counts_fix(final):
    result = r.fresh_classification(producer_summary(), oracle(1, {"test": node("failed")}), final)
    assert not result["completed_verified_fix"]


def tar_bytes(prefix, files, special=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        directories = sorted({str(Path(name).parent).replace("\\", "/") for name in files if "/" in name})
        for directory in directories:
            item = tarfile.TarInfo(prefix + "/" + directory)
            item.type = tarfile.DIRTYPE
            archive.addfile(item)
        for name, content in files.items():
            item = tarfile.TarInfo(prefix + "/" + name)
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
        if special:
            archive.addfile(special)
    return output.getvalue()


def differently_ordered_source_inventory():
    # A sibling HTML file and nested module expose the real Path-vs-string
    # ordering difference without importing or executing either file.
    raw = tar_bytes("agent-final", {"docs/sqlglot.html": b"page\n",
                                    "docs/sqlglot/_typing.html": b"nested page\n"})
    archive_rows = r.pv.source_archive_inventory(raw)
    captured_rows = sorted(archive_rows, key=lambda row: Path(row["path"]))
    assert captured_rows != archive_rows
    return raw, captured_rows


def test_archived_final_source_accepts_complete_inventory_in_producer_path_order():
    raw, captured = differently_ordered_source_inventory()
    original = r.h.encoded(captured)
    v.check_archived_final_source(raw, captured)
    assert r.h.encoded(captured) == original


@pytest.mark.parametrize("change", ["missing", "duplicate", "path", "kind", "bytes", "sha256"])
def test_order_normalization_does_not_hide_source_inventory_changes(change):
    raw, captured = differently_ordered_source_inventory()
    captured = [dict(row) for row in captured]
    row = next(row for row in captured if row["path"] == "docs/sqlglot.html")
    if change == "missing":
        captured.remove(row)
    elif change == "duplicate":
        captured.append(dict(row))
    elif change == "path":
        row["path"] = "docs/another.html"
    elif change == "kind":
        row["kind"] = "directory"
    elif change == "bytes":
        row["bytes"] += 1
    else:
        row["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="archived final source bytes differ"):
        v.check_archived_final_source(raw, captured)


def fixture(tmp_path, monkeypatch):
    prepared, output = tmp_path / "producer", tmp_path / "evaluation"
    case_dir = prepared / "case-00"
    case_dir.mkdir(parents=True)
    output.mkdir()
    commit = "a" * 40
    original = {"pkg/main.py": b"old source\n", "tests/test_bug.py": b"old test\n"}
    patch = ("diff --git a/tests/test_bug.py b/tests/test_bug.py\n"
             "--- a/tests/test_bug.py\n+++ b/tests/test_bug.py\n@@ -1 +1 @@\n-old test\n+new test\n").encode()
    def bind(name, raw):
        (case_dir / name).write_bytes(raw)
        return {"path": name, "bytes": len(raw), "sha256": r.h.sha(raw)}
    source = bind("base-source.tar.gz", tar_bytes("repo-" + commit, original))
    test_patch = bind("provided-tests.patch", patch)
    base_expected = tmp_path / "expected-base"
    r.h.extract_source((case_dir / source["path"]).read_bytes(), base_expected, commit)
    (base_expected / "tests/test_bug.py").write_bytes(b"new test\n")
    before = r.producer.inventory(base_expected)
    final_files = {"pkg/main.py": b"new source\n", "tests/test_bug.py": b"new test\n"}
    final = bind("final-source.tar.gz", tar_bytes("agent-final", final_files))
    after = r.pv.source_archive_inventory((case_dir / final["path"]).read_bytes())
    case = {"case_id": "test-case", "repo": "owner/repo", "base_commit": commit,
            "targets": ["tests/test_bug.py"], "source_archive": source}
    (prepared / "freeze.json").write_bytes(r.h.encoded({"cases": [case]}))
    (case_dir / "preparation.json").write_bytes(r.h.encoded({"source_archive": source, "test_patch": test_patch,
        "test_paths": ["tests/test_bug.py"], "before": before}))
    (case_dir / "session.json").write_bytes(r.h.encoded({"final_source": final, "after": after}))
    monkeypatch.setattr(r.pv, "validate_receipt", lambda *a, **kw: producer_summary())
    calls = []
    def inert_git(root, args, raw=None):
        calls.append((args, raw))
        if args[0] == "init":
            (root / ".git").mkdir()
        elif args[0] == "apply" and "--check" not in args:
            assert raw == patch
            (root / "tests/test_bug.py").write_bytes(b"new test\n")
        return {"argv": args, "returncode": 0, "stdout": "", "stderr": ""}
    monkeypatch.setattr(r.h, "git", inert_git)
    return prepared, output, calls, patch


def test_reconstruction_applies_public_tests_only_and_uses_full_final_archive(tmp_path, monkeypatch):
    prepared, output, calls, patch = fixture(tmp_path, monkeypatch)
    base, final, receipt = r.reconstruct(prepared, 0, output)
    assert (base / "pkg/main.py").read_bytes() == b"old source\n"
    assert (final / "pkg/main.py").read_bytes() == b"new source\n"
    assert (final / "tests/test_bug.py").read_bytes() == b"new test\n"
    assert [raw for args, raw in calls if raw is not None] == [patch, patch]
    assert receipt["reference_source_patch_applied"] is False and receipt["full_final_snapshot_used"] is True


def test_reconstruction_refuses_baseline_inventory_drift_before_final(tmp_path, monkeypatch):
    prepared, output, _, _ = fixture(tmp_path, monkeypatch)
    prep_path = prepared / "case-00/preparation.json"
    prep = r.h.strict(prep_path.read_bytes())
    prep["before"] = []
    prep_path.write_bytes(r.h.encoded(prep))
    with pytest.raises(ValueError, match="baseline differs"):
        r.reconstruct(prepared, 0, output)
    assert not (output / "source").exists()


def test_reconstruction_refuses_boundary_failed_producer_before_git(tmp_path, monkeypatch):
    prepared, output, calls, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(r.pv, "validate_receipt", lambda *a, **kw: producer_summary(boundary_pass=False))
    with pytest.raises(ValueError, match="boundary"):
        r.reconstruct(prepared, 0, output)
    assert calls == []


def test_reconstruction_refuses_wrong_selected_base_archive(tmp_path, monkeypatch):
    prepared, output, calls, _ = fixture(tmp_path, monkeypatch)
    path = prepared / "freeze.json"
    freeze = r.h.strict(path.read_bytes())
    freeze["cases"][0]["source_archive"]["sha256"] = "0" * 64
    path.write_bytes(r.h.encoded(freeze))
    with pytest.raises(ValueError, match="selected immutable"):
        r.reconstruct(prepared, 0, output)
    assert calls == []


def test_input_inventory_includes_captured_files_not_mutable_workspace(tmp_path):
    (tmp_path / "freeze.json").write_bytes(b"{}")
    (tmp_path / "case-00/workspace").mkdir(parents=True)
    (tmp_path / "case-00/session.json").write_bytes(b"{}")
    (tmp_path / "case-00/workspace/unrelated.py").write_bytes(b"mutable")
    assert [row["path"] for row in r.input_inventory(tmp_path, 1)] == ["freeze.json", "case-00/session.json"]


def test_missing_or_failed_oracle_never_fabricates_a_result(tmp_path):
    assert v.optional_oracle(tmp_path, "baseline-oracle", "capture") is None
    r.h.save(tmp_path / "baseline-oracle.started.json", {"operation": "baseline-oracle"})
    r.h.save(tmp_path / "baseline-oracle.json", {"operation": "baseline-oracle", "result": None,
                                                "error": {"type": "MissingDependency"}})
    assert v.optional_oracle(tmp_path, "baseline-oracle", "capture") is None


def test_partial_oracle_cannot_hide_a_result(tmp_path):
    r.h.save(tmp_path / "baseline-oracle.started.json", {"operation": "baseline-oracle"})
    r.h.save(tmp_path / "baseline-oracle.json", {"operation": "baseline-oracle", "result": {"exit_code": 0},
                                                "error": {"type": "MissingDependency"}})
    with pytest.raises(ValueError, match="hides result"):
        v.optional_oracle(tmp_path, "baseline-oracle", "capture")


def test_cutoff_requires_explicit_timezone():
    with pytest.raises(ValueError, match="timezone"):
        r.cutoff_seconds("2026-09-07T06:50:00")
    assert r.cutoff_seconds("2000-01-01T00:00:00Z") < 0


def test_source_adapter_does_not_modify_frozen_helpers():
    assert r.h.sha(r.h.ordinary(r.h.HERE / "run.py")) == "b549bbc77745a99413c6a3f9976ccdb0af204e831686b2a6af761690c8e9c064"
    assert r.h.sha(r.h.ordinary(r.producer.HERE / "run.py")) == "e72a15411098988a8451ed4a0ddcc30467ff5becefbd1fafc72afb4d8aab0a8c"


def unavailable_campaign(tmp_path, monkeypatch):
    """Complete synthetic failure-only ledger; no actual agent or test run."""
    prepared, output = tmp_path / "producer", tmp_path / "companion"
    prepared.mkdir()
    output.mkdir()
    cases = [{"case_id": "pilot-" + str(i), "repo": "owner/repo" + str(i), "base_commit": "a" * 40} for i in range(2)]
    freeze = {"phase": "pilot", "cases": cases,
              "ledger": {"schema": "zerorun.handoff-selection.v1", "phase": "pilot", "cases": cases}}
    r.h.save(prepared / "freeze.json", freeze)
    source_sets = {"sources": r.sources(r.HERE), "handoff_sources": r.sources(r.h.HERE),
                   "producer_sources": r.sources(r.producer.HERE)}
    inputs = r.input_inventory(prepared, 2)
    attestation = {"requested_image": r.h.IMAGE}
    protocol = {"schema": "zerorun.agent-handoff-evaluation-protocol.v1", "producer_freeze": inputs[0],
        "producer_inputs": inputs, "cases": cases, "phase": "pilot", "planned_cases": 2,
        "budget_seconds": 5400, "cutoff_utc": "2026-09-07T06:50:00Z", **source_sets,
        "engine": {"runtime_files": []}, "runtime_attestation": attestation, "runtime_image": r.h.IMAGE,
        "execution_seconds": 120, "new_model_calls": 0, "mcp_authority_created": False,
        "reference_patch_applied": False, "natural_hit_frequency_study": False,
        "agent_and_reference_patch_denominators_combined": False, "image_deployment": None, "image_sources": None}
    r.h.save(output / "protocol.json", protocol)
    rows = []
    for index, case in enumerate(cases):
        row = {"case_id": case["case_id"], "repo": case["repo"], "case_index": index,
               "disposition": "PRODUCER_UNAVAILABLE", "producer": None, "error": None,
               "classification": r.fresh_classification({}, None, None)}
        r.h.save(output / "cases" / case["case_id"] / "completion.json", row)
        rows.append(row)
    result = {"schema": "zerorun.agent-handoff-evaluation-completion.v1",
        "protocol_sha256": r.h.sha(r.h.ordinary(output / "protocol.json")), "cases": rows,
        "selected_cases": 2, "planned_cases": 2, "complete_controlled_cases": 0, "completed_verified_fixes": 0,
        "final_fresh_passes": 0, "campaign_error": None, "material_correctness_stop": False,
        "runtime_after": [], "runtime_attestation_after": attestation, "producer_inputs_after": inputs,
        **{key + "_after": value for key, value in source_sets.items()}, "image_sources_after": None,
        "new_model_calls": 0, "mcp_authority_files_found": 0, "inputs_and_sources_unchanged": True,
        "all_selected_outcomes_retained": True, "natural_hit_frequency": False}
    r.h.save(output / "completion.json", result)
    monkeypatch.setattr(r.h, "validate_runtime_rows", lambda rows: rows)
    return prepared, output, result


def test_full_failure_only_receipt_reconciles_all_selected_cases(tmp_path, monkeypatch):
    prepared, output, _ = unavailable_campaign(tmp_path, monkeypatch)
    result = v.validate_saved(output, prepared)
    assert result["selected_cases"] == 2 and result["planned_cases"] == 2
    assert result["completed_verified_fixes"] == result["complete_controlled_cases"] == result["paired_blocks"] == 0
    assert result["new_model_calls"] == 0 and result["end_to_end_agent_acceleration"] is False


@pytest.mark.parametrize("field,value", [("completed_verified_fixes", 1), ("selected_cases", 1),
                                        ("new_model_calls", 1), ("natural_hit_frequency", True)])
def test_full_receipt_refuses_inflated_counts_or_changed_claims(tmp_path, monkeypatch, field, value):
    prepared, output, result = unavailable_campaign(tmp_path, monkeypatch)
    result[field] = value
    (output / "completion.json").write_bytes(r.h.encoded(result))
    with pytest.raises(ValueError):
        v.validate_saved(output, prepared)


@pytest.mark.parametrize("value", [True, -1, float("inf"), "10"])
def test_descriptive_timings_must_be_finite_nonnegative_numbers(value):
    with pytest.raises(ValueError, match="time"):
        v.milliseconds(value)
