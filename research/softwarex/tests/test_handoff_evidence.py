"""Artificial offline receipts test reconciliation, not experimental outcomes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from research.softwarex import build_handoff_evidence as evidence
from research.softwarex.handoff_acquisition_recovery_v1.test_recover import prior
from research.softwarex.handoff_acquisition_v1.test_collect import row

a = evidence.acquisition
r = evidence.recovery


def save(base, relative, value):
    return a.save_json(base, relative, value)


def replace(base, relative, value):
    (base / relative).write_bytes(a.encoded(value))


@pytest.fixture
def acquisition_fixture(prior, tmp_path):
    root = tmp_path / "public"
    base = root / evidence.ACQUISITION
    base.mkdir(parents=True)
    inventory = r.prior_inventory(prior)
    indexed = {entry["path"]: entry for entry in inventory}
    for name in r.SMALL:
        r.copy_bound(prior, base, indexed[name], "initial-attempt/" + name)
    for name in ("dataset-before.json", "source-notices/README.md"):
        r.copy_bound(prior, base, indexed[name])
    plan = a.protocol_plan()
    plan["transport_recovery"] = {"schema": "zerorun.handoff-acquisition-transport-recovery.v1",
        "amendment_sources": [r.file_record(r.HERE, name) for name in ("__init__.py", "PROTOCOL.md", "recover.py")],
        "initial_attempt_files": inventory, "initial_failure_retained": True, "resume_used": False,
        "selection_logic_changed": False, "archive_limits_changed": False,
        "second_parquet_deadline_between_reads_seconds": 600}
    plan_row = save(base, "plan.json", plan)
    # Eight eligible repositories deliberately miss the original ten-repo target.
    selected = a.select([row(repo, index) for repo in a.SHORTLIST[:8] for index in range(1, 5)])
    sealed = {key: value for key, value in selected.items() if key not in {"main", "pilot"}}
    sealed.update({phase: [{key: value for key, value in case.items() if key != "row"}
                          for case in selected[phase]] for phase in ("pilot", "main")})
    selection_row = save(base, "selection.json", sealed)
    manifests = {}
    for phase in ("pilot", "main"):
        cases = []
        for case in selected[phase]:
            prefix = "cases/" + case["case_id"]
            wrapper = {"dataset": a.DATASET, "revision": a.REVISION, "config": "default", "split": "test",
                "partial": False, "rows": [{"row_idx": case["row_index"], "row": case["row"], "truncated_cells": []}]}
            metadata = save(base, prefix + "/metadata.json", wrapper)
            a.save_new(base / prefix / "source.tar.gz", b"inert offline archive fixture")
            archive = r.file_record(base, prefix + "/source.tar.gz")
            save(base, archive["path"] + ".download.json", {**archive, "attempts": 1, "status": "ok",
                "limit_bytes": a.ARCHIVE_LIMIT, "url": f"https://codeload.github.com/{case['repo']}/tar.gz/{case['base_commit']}"})
            cases.append({key: case[key] for key in ("case_id", "repo", "base_commit", "targets")}
                         | {"metadata": metadata, "source_archive": archive})
        manifests[phase] = save(base, phase + ".json", {"schema": "zerorun.handoff-selection.v1", "phase": phase,
            "plan": plan_row, "selection": selection_row, "cases": cases,
            "selection_complete": False, "acquisition_complete": False})
    parquet_rows = [indexed[r.FIRST], {"path": r.SECOND, "bytes": 12, "sha256": a.sha(b"PAR1tinyPAR1!")}]
    save(base, r.SECOND + ".download.json", {**parquet_rows[1], "attempts": 1, "status": "ok",
        "url": r.parquet_url(r.SECOND), "limit_bytes": a.PARQUET_LIMIT, "parquet_container_magic_ok": True})
    receipt = {"schema": "zerorun.handoff-acquisition.v1", "completed": False,
        "code_executed": False, "archives_extracted": False, "pyarrow_version": "23.0.1",
        "plan": plan_row, "selection": selection_row, "manifests": manifests,
        "metadata_before": indexed["dataset-before.json"], "source_notice": indexed["source-notices/README.md"],
        "parquets": parquet_rows}
    receipt_row = save(base, "receipt.json", receipt)
    save(base, "recovery-completion.json", {"schema": "zerorun.handoff-acquisition-transport-recovery-completion.v1",
        "completed": False, "original_inputs_unchanged": True, "original_failure_retained": True,
        "selection_logic_changed": False, "code_executed": False, "archives_extracted": False,
        "new_receipt": receipt_row, "new_plan": plan_row, "original_receipt": indexed["receipt.json"]})
    return root, base


def rebind(base, *, phase=None, selection=False, receipt=False):
    raw = json.loads((base / "receipt.json").read_bytes())
    if phase:
        raw["manifests"][phase] = r.file_record(base, phase + ".json")
    if selection:
        raw["selection"] = r.file_record(base, "selection.json")
        for name in ("pilot", "main"):
            manifest = json.loads((base / (name + ".json")).read_bytes())
            manifest["selection"] = raw["selection"]
            replace(base, name + ".json", manifest)
            raw["manifests"][name] = r.file_record(base, name + ".json")
    if phase or selection or receipt:
        replace(base, "receipt.json", raw)
        completion = json.loads((base / "recovery-completion.json").read_bytes())
        completion["new_receipt"] = r.file_record(base, "receipt.json")
        replace(base, "recovery-completion.json", completion)


def test_actual_selected_denominator_is_not_original_target(acquisition_fixture):
    root, base = acquisition_fixture
    summary, manifests = evidence.acquisition_summary(base)
    assert summary["selected_main_cases"] == 24 and summary["selected_main_repositories"] == 8
    assert summary["selected_pilot_cases"] == 2 and summary["original_target_complete"] is False
    assert summary["all_selected_archives_available"] is True
    assert summary["recorded_acquisition_completed"] is False
    assert summary["archive_availability"] == {"main": 24, "pilot": 2}
    assert summary["parquet_files_rechecked"] is False
    assert summary["full_corpus_selection_independently_recomputed"] is False
    assert len(manifests["main"]["cases"]) == 24


def test_record_inventory_preserves_agent_inputs_and_excludes_private_workspaces(acquisition_fixture):
    root, base = acquisition_fixture
    a.save_new(root / "research/softwarex/build_handoff_evidence.py", b"# artificial builder fixture")
    producer = root / evidence.AGENTS["pilot"][0]
    for name in ("prompt.txt", "provided-tests.patch", "base-source.tar.gz", "final-source.tar.gz", "events.log"):
        a.save_new(producer / "case-00" / name, b"inert captured input")
    a.save_new(producer / "case-00/workspace/private.py", b"excluded workspace")
    a.save_new(producer / "client-home/auth.json", b"excluded credential fixture")
    image = root / evidence.IMAGE
    a.save_new(image / "protocol.json", b"{}")
    a.save_new(image / "context/site-packages/dep.py", b"excluded build context")
    rows = evidence.source_inputs(root)
    paths = {row["path"] for row in rows}
    assert len([path for path in paths if path.endswith("/source.tar.gz")]) == 26
    assert evidence.AGENTS["pilot"][0] + "/case-00/final-source.tar.gz" in paths
    assert evidence.AGENTS["pilot"][0] + "/case-00/provided-tests.patch" in paths
    assert not any("workspace" in path or "auth.json" in path or "/context/" in path for path in paths)


def test_complete_cost_summary_keeps_setup_and_oracles_separate():
    rows = [{"chain_ms": {"fresh": 10.0, "zerorun": 12.0}, "consumer_ms": {"fresh": 5.0, "zerorun": 2.0},
             "setup_inclusive_chain_ms": {"fresh": 11.0, "zerorun": 15.0}, "cache_hit_observed": True,
             "fresh_diagnostics_ms": 8.0, "oracle_ms": 14.0}]
    result = evidence.paired_costs(rows)
    assert result["chain_ms"] == rows[0]["chain_ms"]
    assert result["cache_hits"] == 1 and result["consumer_saved_fraction"] == pytest.approx(0.6)
    assert result["chain_saved_fraction"] == pytest.approx(-0.2)
    assert result["oracle_ms"] == 14.0 and result["fresh_diagnostics_ms"] == 8.0
    assert evidence.paired_costs([])["chain_saved_fraction"] is None


@pytest.mark.parametrize("mode", ["archive-byte", "archive-missing", "archive-url", "metadata-byte", "metadata-rebound",
    "false-target", "false-receipt", "subset", "duplicate", "candidate-count", "reused-parquet", "missing-initial", "shortlist-order"])
def test_acquisition_reconciliation_refuses_missing_or_false_green_data(acquisition_fixture, mode):
    _, base = acquisition_fixture
    main = json.loads((base / "main.json").read_bytes())
    case = main["cases"][0]
    if mode == "archive-byte":
        (base / case["source_archive"]["path"]).write_bytes(b"different")
    elif mode == "archive-missing":
        (base / case["source_archive"]["path"]).unlink()
    elif mode == "archive-url":
        path = case["source_archive"]["path"] + ".download.json"
        value = json.loads((base / path).read_bytes())
        value["url"] = "https://example.invalid/changed"
        replace(base, path, value)
    elif mode in {"metadata-byte", "metadata-rebound"}:
        path = case["metadata"]["path"]
        value = json.loads((base / path).read_bytes())
        value["rows"][0]["row"]["base_commit"] = "0" * 40
        replace(base, path, value)
        if mode == "metadata-rebound":
            case["metadata"] = r.file_record(base, path)
            replace(base, "main.json", main)
            rebind(base, phase="main")
    elif mode == "false-target":
        main["selection_complete"] = main["acquisition_complete"] = True
        replace(base, "main.json", main)
        rebind(base, phase="main")
    elif mode == "false-receipt":
        receipt = json.loads((base / "receipt.json").read_bytes())
        receipt["completed"] = True
        replace(base, "receipt.json", receipt)
        rebind(base, receipt=True)
    elif mode in {"subset", "duplicate"}:
        main["cases"] = main["cases"][:-1] if mode == "subset" else main["cases"] + [deepcopy(case)]
        replace(base, "main.json", main)
        rebind(base, phase="main")
    elif mode in {"candidate-count", "shortlist-order"}:
        selected = json.loads((base / "selection.json").read_bytes())
        if mode == "candidate-count":
            selected["repository_ledger"][0]["candidate_count"] += 1
        else:
            selected["repository_ledger"].reverse()
        replace(base, "selection.json", selected)
        rebind(base, selection=True)
    elif mode == "reused-parquet":
        receipt = json.loads((base / "receipt.json").read_bytes())
        receipt["parquets"][0]["sha256"] = "0" * 64
        replace(base, "receipt.json", receipt)
        rebind(base, receipt=True)
    else:
        (base / "initial-attempt/receipt.json").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        evidence.acquisition_summary(base)


def test_absent_runs_and_incomplete_runs_are_never_success(acquisition_fixture):
    root, _ = acquisition_fixture
    value = evidence.build(root)
    assert set(value["unavailable_or_incomplete_runs"]) == {"v1_pilot", "v1_main", "v2_pilot", "v2_pilot_repeat", "v2_main", "v2_main_extended"}
    assert all(run["state"] == "NOT_AVAILABLE" and run["execution_success_claimed"] is False
               for run in value["controlled_runs"].values())
    path = root / evidence.RUNS["v1_pilot"][0]
    path.mkdir()
    save(path, "protocol.json", {"artificial": "unfinished"})
    value = evidence.build(root)
    assert value["controlled_runs"]["v1_pilot"]["state"] == "INCOMPLETE_RECORD"
    assert value["agent_evaluation"]["pilot"]["evaluation"]["resolved_issue_count"] is None
    assert value["agent_evaluation"]["main"]["recorded_model_invocations"] == 0
    assert value["agent_evaluation"]["main"]["selected_cases"] == 6
    assert value["performance_threshold_imposed"] is False


def test_build_is_read_only_and_deterministic(acquisition_fixture):
    root, _ = acquisition_fixture
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    first, second = evidence.build(root), evidence.build(root)
    assert first == second
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_recovery_repeat_requires_amendment_and_does_not_reclassify_original(acquisition_fixture):
    root, _ = acquisition_fixture
    repeat = root / evidence.RUNS["v2_pilot_repeat"][0]
    repeat.mkdir()
    with pytest.raises(ValueError, match="prospective amendment"):
        evidence.build(root)
    a.save_new(root / evidence.AMENDMENTS[0], b"Artificial operator-report amendment fixture.")
    summary = evidence.build(root)
    assert summary["controlled_runs"]["v2_pilot"]["state"] == "NOT_AVAILABLE"
    assert summary["controlled_runs"]["v2_pilot_repeat"]["state"] == "INCOMPLETE_RECORD"
    assert summary["controlled_runs"]["v2_pilot"]["timing_context"]["uninterrupted_timing_certified"] is False
    assert summary["protocol_amendments"][0]["sha256"] == a.sha((root / evidence.AMENDMENTS[0]).read_bytes())


def fake_run(tmp_path, manifests, *, complete=True):
    directory = tmp_path / "run"
    directory.mkdir()
    save(directory, "protocol.json", {"selection": manifests["pilot"], "phase": "pilot", "blocks_per_case": 2,
        "natural_hit_frequency_study": False})
    rows = []
    for case in manifests["pilot"]["cases"]:
        row = {"case_id": case["case_id"], "repo": case["repo"], "disposition": "COMPLETE" if complete else "INCOMPLETE_OR_UNSUPPORTED"}
        if complete:
            row["blocks"] = [{"measurements": {"chain_ms": {"fresh": 10.0, "zerorun": 15.0},
                "consumer_ms": {"fresh": 5.0, "zerorun": 2.0},
                "setup_inclusive_chain_ms": {"fresh": 11.0, "zerorun": 17.0},
                "cache_hit_observed": True, "oracle_ms": 13.0, "fresh_diagnostics_ms": 8.0}}] * 2
        rows.append(row)
    save(directory, "completion.json", {"cases": rows, "campaign_error": None, "material_correctness_stop": False})
    summary = {"complete_paired_chain_ms": {"fresh": 40.0 if complete else 0,
                                           "zerorun": 60.0 if complete else 0}}
    return directory, summary


def test_negative_complete_cost_is_reported_not_failed_or_omitted(acquisition_fixture, tmp_path, monkeypatch):
    _, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    directory, raw = fake_run(tmp_path, manifests)
    monkeypatch.setattr(evidence.v1, "validate_saved", lambda *args: raw)
    result = evidence.run_summary(directory, "v1", "pilot", base, manifests, tmp_path / "image")
    assert result["complete_case_median_chain_saved_fraction"] == -0.5
    assert result["complete_case_faster_count"] == 0 and result["all_selected_cases_completed"] is True
    assert result["live_agent_speedup_established"] is False


def test_complete_raw_validator_does_not_authorize_subset_case_claim(acquisition_fixture, tmp_path, monkeypatch):
    _, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    directory, raw = fake_run(tmp_path, manifests)
    protocol = json.loads((directory / "protocol.json").read_bytes())
    protocol["selection"]["cases"].pop()
    replace(directory, "protocol.json", protocol)
    monkeypatch.setattr(evidence.v1, "validate_saved", lambda *args: raw)
    with pytest.raises(ValueError, match="subset"):
        evidence.run_summary(directory, "v1", "pilot", base, manifests, tmp_path / "image")


def test_case_total_must_match_recomputed_operation_total(acquisition_fixture, tmp_path, monkeypatch):
    _, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    directory, raw = fake_run(tmp_path, manifests)
    raw["complete_paired_chain_ms"]["zerorun"] = 1.0
    monkeypatch.setattr(evidence.v1, "validate_saved", lambda *args: raw)
    with pytest.raises(ValueError, match="operation totals"):
        evidence.run_summary(directory, "v1", "pilot", base, manifests, tmp_path / "image")


def test_zero_completed_cases_have_no_saved_fraction(acquisition_fixture, tmp_path, monkeypatch):
    _, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    directory, raw = fake_run(tmp_path, manifests, complete=False)
    monkeypatch.setattr(evidence.v1, "validate_saved", lambda *args: raw)
    result = evidence.run_summary(directory, "v1", "pilot", base, manifests, tmp_path / "image")
    assert result["all_selected_cases_completed"] is False
    assert result["complete_case_median_chain_saved_fraction"] is None


def test_public_binary_allowlist_is_exact_selected_archives_only(acquisition_fixture, monkeypatch):
    from research.softwarex import build_public_release as public
    root, base = acquisition_fixture
    monkeypatch.setattr(public, "ROOT", root)
    a.save_new(base / "unselected.tar.gz", b"must not be published")
    a.save_new(base / "data/unselected.parquet", b"large corpus not included")
    rows = public.selected_acquisition_archives()
    assert len(rows) == 26
    assert all(name.startswith(evidence.ACQUISITION + "/cases/") and name.endswith("/source.tar.gz")
               for name, _ in rows)
    assert all("unselected" not in name for name, _ in rows)


def test_public_binary_allowlist_refuses_modified_selected_archive(acquisition_fixture, monkeypatch):
    from research.softwarex import build_public_release as public
    root, base = acquisition_fixture
    monkeypatch.setattr(public, "ROOT", root)
    case = json.loads((base / "main.json").read_bytes())["cases"][0]
    (base / case["source_archive"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        public.selected_acquisition_archives()


def test_agent_selection_is_separate_and_absence_does_not_establish_fixes(acquisition_fixture):
    root, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    summary = evidence.agent_summary(root / "producer", root / "evaluation", "main", manifests, root / "image")
    assert summary["selected_cases"] == summary["planned_cases"] == 6
    assert len({row["repo"] for row in summary["producer_cases"]}) == 6
    assert summary["recorded_model_invocations"] == summary["producer_completed"] == 0
    assert summary["evaluation"]["resolved_issue_count"] is None
    assert summary["denominator_combined_with_reference_patch_cases"] is False


def test_agent_frozen_subset_is_rejected_before_session_validation(acquisition_fixture):
    from research.softwarex.agent_handoff_v1 import common as pc
    root, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    prepared = root / "agent"
    frozen = {"ledger": deepcopy(manifests["main"]), "phase": "main", "cases": pc.selected(manifests["main"])}
    frozen["cases"].pop()
    save(prepared, "freeze.json", frozen)
    with pytest.raises(ValueError, match="prespecified"):
        evidence.agent_summary(prepared, root / "evaluation", "main", manifests, root / "image")


def test_failed_image_is_not_silently_treated_as_success(tmp_path):
    directory = tmp_path / "image"
    save(directory, "protocol.json", {"schema": "zerorun.handoff-image-build.v2"})
    protocol = r.file_record(directory, "protocol.json")
    save(directory, "completion.json", {"schema": "zerorun.handoff-image-build-completion.v2",
        "protocol_sha256": protocol["sha256"], "passed": True, "error": {"type": "Failure", "message": "retained"}})
    with pytest.raises(ValueError, match="failure"):
        evidence.failed_image_summary(directory)


def test_failed_producer_capture_remains_reported_without_required_success_fields(acquisition_fixture, monkeypatch):
    from research.softwarex.agent_handoff_v1 import common as pc
    from research.softwarex.agent_handoff_v1 import validate as pv
    root, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    selected = pc.selected(manifests["pilot"])
    prepared = root / "agent-pilot"
    save(prepared, "freeze.json", {"ledger": manifests["pilot"], "phase": "pilot", "cases": selected})
    save(prepared, "case-00/session.json", {"artificial": "failed capture"})
    monkeypatch.setattr(pv, "validate_receipt", lambda *args, **kwargs: {
        "case_id": selected[0]["case_id"], "model_invocation_attempted": True,
        "producer_completed": False, "boundary_pass": False, "capture_incomplete": True,
        "patch_correctness_established": False, "error": "retained failure"})
    result = evidence.agent_summary(prepared, root / "evaluation", "pilot", manifests, root / "image")
    assert result["recorded_model_invocations"] == 1 and result["producer_completed"] == 0
    assert result["producer_cases"][0]["reconciliation"]["capture_incomplete"] is True
    assert result["evaluation"]["resolved_issue_count"] is None


@pytest.fixture
def failed_image_fixture(tmp_path, monkeypatch):
    directory = tmp_path / "failed-image"
    a.save_new(directory / "attempt-sources/build_image.py", b"# original offline source fixture\n")
    sources = [r.file_record(directory / "attempt-sources", "build_image.py")]
    protocol = {"schema": "zerorun.handoff-image-build.v2", "base_image": evidence.v2.b.BASE_IMAGE,
        "listen_address": "127.0.0.1", "build_network": "none", "daemon_configuration_modified": False,
        "paid_services": False, "engine_binding": {"runtime_files": []}, "sources": sources}
    plan = save(directory, "protocol.json", protocol)
    save(directory, "completion.json", {"schema": "zerorun.handoff-image-build-completion.v2",
        "protocol_sha256": plan["sha256"], "passed": False,
        "error": {"type": "ConnectionResetError", "message": "offline readiness fixture"},
        "image_publicly_pullable": False, "bit_identical_recipe_rebuild_claimed": False,
        "sources_after": sources, "source_unchanged": True, "setup_outer_ms": 20.0,
        "registry_container_removed": True})
    save(directory, "commands/build.started.json", {"argv": ["docker", "build", "fixture"]})
    save(directory, "commands/build.json", {"argv": ["docker", "build", "fixture"], "outer_ms": 10.0,
                                            "returncode": 0, "error": None})
    monkeypatch.setattr(evidence.h, "validate_runtime_rows", lambda rows: None)
    return directory


def test_failed_image_retains_original_source_error_and_setup_cost(failed_image_fixture):
    value = evidence.failed_image_summary(failed_image_fixture)
    assert value["state"] == "RECONCILED_RECORDED_PREPARATION_FAILURE"
    assert value["image_success_claimed"] is False and value["setup_outer_ms"] == 20.0
    assert value["retained_command_outer_ms"] == 10.0
    assert value["error"]["type"] == "ConnectionResetError"


@pytest.mark.parametrize("mode", ["source-byte", "command-argv", "protocol-hash", "missing-source", "false-source-after"])
def test_failed_image_record_is_still_strictly_bound(failed_image_fixture, mode):
    directory = failed_image_fixture
    if mode == "source-byte":
        (directory / "attempt-sources/build_image.py").write_bytes(b"changed")
    elif mode == "missing-source":
        (directory / "attempt-sources/build_image.py").unlink()
    elif mode == "command-argv":
        row = json.loads((directory / "commands/build.json").read_bytes())
        row["argv"][-1] = "different"
        replace(directory, "commands/build.json", row)
    else:
        completion = json.loads((directory / "completion.json").read_bytes())
        if mode == "protocol-hash":
            completion["protocol_sha256"] = "0" * 64
        else:
            completion["sources_after"][0]["sha256"] = "0" * 64
        replace(directory, "completion.json", completion)
    with pytest.raises((ValueError, FileNotFoundError)):
        evidence.failed_image_summary(directory)


@pytest.fixture
def previous_companion_fixture(tmp_path):
    directory = tmp_path / "original-companion"
    original = directory / "attempt-sources"
    for name in ("__init__.py", "PROTOCOL.md", "run.py", "validate.py", "test_evaluation.py"):
        raw = b"# Artificial frozen checker fixture.\n"
        if name == "validate.py":
            raw += b"def validate_saved(*args):\n    raise ValueError('archived final source bytes differ')\n"
        a.save_new(original / name, raw)
    rows = [r.file_record(original, name) for name in ("__init__.py", "PROTOCOL.md", "run.py", "validate.py", "test_evaluation.py")]
    protocol = save(directory, "protocol.json", {
        "schema": "zerorun.agent-handoff-evaluation-protocol.v1", "sources": rows})
    save(directory, "completion.json", {
        "schema": "zerorun.agent-handoff-evaluation-completion.v1", "sources_after": rows,
        "protocol_sha256": protocol["sha256"]})
    return directory


def test_previous_companion_reproduces_refusal_without_pooling_success(previous_companion_fixture):
    result = evidence.previous_agent_evaluation(previous_companion_fixture, Path("unused"), Path("unused"))
    assert result["state"] == "RETAINED_FROZEN_CHECKER_REFUSAL"
    assert result["verified_fix_count"] is None and result["not_pooled_with_repaired_companion"] is True
    assert result["checker_failure_reproduced_read_only"] is True
    assert not list(previous_companion_fixture.rglob("*.pyc"))


@pytest.mark.parametrize("mode", ["source-drift", "source-after", "unexpected-refusal"])
def test_previous_companion_cannot_hide_a_different_failure(previous_companion_fixture, mode):
    directory = previous_companion_fixture
    if mode == "source-drift":
        (directory / "attempt-sources/validate.py").write_bytes(b"# changed")
    elif mode == "source-after":
        raw = json.loads((directory / "completion.json").read_bytes())
        raw["sources_after"][0]["sha256"] = "0" * 64
        replace(directory, "completion.json", raw)
    else:
        (directory / "attempt-sources/validate.py").write_bytes(b"def validate_saved(*args):\n    raise ValueError('different defect')\n")
        protocol = json.loads((directory / "protocol.json").read_bytes())
        protocol["sources"] = [r.file_record(directory / "attempt-sources", row["path"]) for row in protocol["sources"]]
        replace(directory, "protocol.json", protocol)
        completion = json.loads((directory / "completion.json").read_bytes())
        completion.update(sources_after=protocol["sources"], protocol_sha256=r.file_record(directory, "protocol.json")["sha256"])
        replace(directory, "completion.json", completion)
    with pytest.raises(ValueError):
        evidence.previous_agent_evaluation(directory, Path("unused"), Path("unused"))


def fake_revision_run(root, relative, manifests, phase, budget):
    """Artificial adapter records exercise aggregation, not the lower raw validator."""
    directory = root / relative
    save(directory, "protocol.json", {"artificial": "adapter boundary fixture"})
    save(directory, "completion.json", {"artificial": "retained unfinished outcomes"})
    save(directory, "run/protocol.json", {
        "selection": deepcopy(manifests[phase]), "phase": phase, "blocks_per_case": 2,
        "natural_hit_frequency_study": False, "budget_seconds": budget, "execution_seconds": 120})
    save(directory, "run/completion.json", {
        "cases": [{"case_id": case["case_id"], "repo": case["repo"], "disposition": "NOT_RUN_BUDGET"}
                  for case in manifests[phase]["cases"]],
        "campaign_error": None, "material_correctness_stop": False})
    return directory


def mock_revision_image_boundary(monkeypatch, expected_images):
    """Preserve a distinguishing image boundary while isolating the new builder route."""
    def validate_saved(directory, image_build, acquisition_base):
        assert acquisition_base.is_dir()
        if Path(image_build) != expected_images[Path(directory)]:
            raise ValueError("artificial image binding differs")
        return {"bound_image": str(image_build), "controlled_handoffs": {
            "complete_paired_chain_ms": {"fresh": 0, "zerorun": 0}}}
    monkeypatch.setattr(evidence.v2, "validate_saved", validate_saved)
    monkeypatch.setattr(evidence.v2, "validate_image", lambda directory: {
        "reconciled": True, "bound_image": str(directory)})


@pytest.mark.parametrize("relative", [evidence.RUNS["v2_main_extended"][0], evidence.FRESH_IMAGE, evidence.FRESH_PILOT])
def test_revision_presence_requires_retained_prospective_amendment(acquisition_fixture, relative):
    root, _ = acquisition_fixture
    (root / relative).mkdir(parents=True)
    with pytest.raises(ValueError, match="prospective amendment"):
        evidence.build(root)


@pytest.mark.parametrize("kind,field,value", [
    ("main", "budget_seconds", 1800), ("main", "execution_seconds", 121),
    ("pilot", "budget_seconds", 1200), ("pilot", "execution_seconds", 121)])
def test_revision_rejects_changed_budget_even_when_lower_records_reconcile(acquisition_fixture, monkeypatch, kind, field, value):
    root, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    a.save_new(root / evidence.AMENDMENTS[2], b"Artificial prospective amendment fixture.")
    relative = evidence.RUNS["v2_main_extended"][0] if kind == "main" else evidence.FRESH_PILOT
    image = root / (evidence.IMAGE if kind == "main" else evidence.FRESH_IMAGE)
    directory = fake_revision_run(root, relative, manifests, kind, 1200 if kind == "main" else 600)
    if kind == "pilot":
        save(image, "completion.json", {"passed": True})
    mock_revision_image_boundary(monkeypatch, {directory: image})
    protocol = json.loads((directory / "run/protocol.json").read_bytes())
    protocol[field] = value
    replace(directory, "run/protocol.json", protocol)
    with pytest.raises(ValueError, match="prospective execution budget"):
        evidence.build(root)


def test_fresh_image_binding_is_separate_and_does_not_pool_historical_runs(acquisition_fixture, monkeypatch):
    root, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    a.save_new(root / evidence.AMENDMENTS[2], b"Artificial prospective amendment fixture.")
    old = fake_revision_run(root, evidence.RUNS["v2_pilot"][0], manifests, "pilot", 600)
    fresh = fake_revision_run(root, evidence.FRESH_PILOT, manifests, "pilot", 600)
    old_image, fresh_image = root / evidence.IMAGE, root / evidence.FRESH_IMAGE
    save(old_image, "completion.json", {"passed": True})
    save(fresh_image, "completion.json", {"passed": True})
    mock_revision_image_boundary(monkeypatch, {old: old_image, fresh: fresh_image})
    summary = evidence.build(root)
    fresh_summary = summary["fresh_public_source_reproduction"]
    assert summary["controlled_runs"]["v2_pilot"]["reconciliation"]["bound_image"] == str(old_image)
    assert fresh_summary["image"]["bound_image"] == str(fresh_image)
    assert fresh_summary["pilot"]["reconciliation"]["bound_image"] == str(fresh_image)
    assert fresh_summary["pooled_with_historical_measurements"] is False
    assert fresh_summary["independent_human_replication"] is False
    assert fresh_summary["pilot"]["all_selected_cases_completed"] is False
    assert summary["reconciled_run_count"] == 1
    with pytest.raises(ValueError, match="image binding"):
        evidence.run_summary(fresh, "v2", "pilot", base, manifests, old_image)


@pytest.mark.parametrize("mode", ["subset", "main-ledger", "reordered"])
def test_fresh_pilot_rejects_changed_frozen_ledger(acquisition_fixture, monkeypatch, mode):
    root, base = acquisition_fixture
    _, manifests = evidence.acquisition_summary(base)
    a.save_new(root / evidence.AMENDMENTS[2], b"Artificial prospective amendment fixture.")
    directory = fake_revision_run(root, evidence.FRESH_PILOT, manifests, "pilot", 600)
    image = root / evidence.FRESH_IMAGE
    save(image, "completion.json", {"passed": True})
    mock_revision_image_boundary(monkeypatch, {directory: image})
    protocol = json.loads((directory / "run/protocol.json").read_bytes())
    if mode == "subset":
        protocol["selection"]["cases"].pop()
    elif mode == "main-ledger":
        protocol["selection"] = deepcopy(manifests["main"])
    else:
        protocol["selection"]["cases"].reverse()
    replace(directory, "run/protocol.json", protocol)
    with pytest.raises(ValueError, match="subset or changed frozen"):
        evidence.build(root)


@pytest.mark.parametrize("mode", ["absent", "image-only", "incomplete-pilot", "pilot-without-image"])
def test_missing_rebuild_records_never_claim_successful_rerun(acquisition_fixture, monkeypatch, mode):
    root, _ = acquisition_fixture
    a.save_new(root / evidence.AMENDMENTS[2], b"Artificial prospective amendment fixture.")
    if mode in {"image-only", "incomplete-pilot"}:
        save(root / evidence.FRESH_IMAGE, "completion.json", {"passed": True})
    if mode in {"incomplete-pilot", "pilot-without-image"}:
        (root / evidence.FRESH_PILOT).mkdir(parents=True)
    mock_revision_image_boundary(monkeypatch, {})
    value = evidence.build(root)["fresh_public_source_reproduction"]
    pilot = value.get("pilot", {})
    assert pilot.get("state") != "RECONCILED_RECORDED_OUTCOMES"
    assert pilot.get("all_selected_cases_completed") is not True
    assert pilot.get("execution_success_claimed") is not True
    assert value["independent_human_replication"] is False
    if mode in {"image-only", "incomplete-pilot"}:
        assert value["state"] == "RECONCILED_IMAGE_BUILD"
        assert pilot["state"] == ("NOT_AVAILABLE" if mode == "image-only" else "INCOMPLETE_RECORD")


def test_unsealed_wrapper_completion_never_confirms_public_provenance(acquisition_fixture):
    root, _ = acquisition_fixture
    a.save_new(root / evidence.AMENDMENTS[2], b"Artificial prospective amendment fixture.")
    save(root / evidence.REVISION_RECORDS, "completion.json", {"artificial": "unsealed export"})
    summary = evidence.build(root)
    revision = summary["application_revision_attempts"]["revision"]
    assert revision["state"] == "INCOMPLETE_RECORD"
    assert revision["sequence_completed"] is False
    assert revision["checkout_execution_binding_confirmed"] is False
    assert summary["fresh_public_source_reproduction"]["fresh_real_workload_reproduction_confirmed"] is False


def test_sealed_revision_wrapper_requires_its_external_interruption_record(acquisition_fixture):
    root, _ = acquisition_fixture
    a.save_new(root / evidence.AMENDMENTS[2], b"Artificial prospective amendment fixture.")
    save(root / evidence.REVISION_RECORDS, "completion.json", {"artificial": "completed wrapper"})
    save(root / evidence.REVISION_RECORDS, "RECORD_MANIFEST.json", {"artificial": "sealed export"})
    with pytest.raises(ValueError, match="retained host interruption record"):
        evidence.build(root)


@pytest.fixture
def host_event_fixture(tmp_path):
    row = {"schema": "zerorun.softwarex-host-interruption.v1", "basis": "operator-observed host event",
        "pause_observed_utc": "2000-01-01T00:00:00Z",
        "resume_command_issued_utc_bounds": {"not_before": "2000-01-01T00:01:00Z",
            "not_after": "2000-01-01T00:01:10Z", "exact_resume_instant_established": False},
        "post_resume_clock_observation": {"guest_reported_utc": "2000-01-01T00:00:10Z",
            "host_observed_utc": "2000-01-01T00:02:00Z", "clocks_agreed": False},
        "virtualbox": {"snapshot_uuid": "11111111-1111-1111-1111-111111111111",
            "delta_uuid": "22222222-2222-2222-2222-222222222222", "exact_delta_file_path_claimed": False},
        "existing_external_drive_files_deleted": False, "raw_guest_timestamps_altered": False,
        "guest_clock_synchronization_requested_during_run": False, "prospective_amendment_modified": False,
        "uninterrupted_timing_certified": False, "interpretation": "Artificial interrupted timing fixture."}
    save(tmp_path, evidence.HOST_INTERRUPTION, row)
    return tmp_path, row


def test_host_event_binding_preserves_clock_interval_and_raw_record(host_event_fixture):
    root, row = host_event_fixture
    path = root / evidence.HOST_INTERRUPTION
    before = path.read_bytes()
    result = evidence.host_interruption_summary(root)
    assert result["host_event_record"] == r.file_record(root, evidence.HOST_INTERRUPTION)
    assert result["resume_command_issued_utc_bounds"] == row["resume_command_issued_utc_bounds"]
    assert result["post_resume_clock_observation"] == row["post_resume_clock_observation"]
    assert result["uninterrupted_timing_certified"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("mode", ["uninterrupted", "altered-times", "exact-resume", "clocks-agree"])
def test_host_event_cannot_be_reclassified_as_clean_timing(host_event_fixture, mode):
    root, row = host_event_fixture
    if mode == "uninterrupted":
        row["uninterrupted_timing_certified"] = True
    elif mode == "altered-times":
        row["raw_guest_timestamps_altered"] = True
    elif mode == "exact-resume":
        row["resume_command_issued_utc_bounds"]["exact_resume_instant_established"] = True
    else:
        row["post_resume_clock_observation"]["clocks_agreed"] = True
    replace(root, evidence.HOST_INTERRUPTION, row)
    with pytest.raises(ValueError):
        evidence.host_interruption_summary(root)


def test_publication_inputs_include_external_host_event(acquisition_fixture):
    root, _ = acquisition_fixture
    a.save_new(root / "research/softwarex/build_handoff_evidence.py", b"# artificial builder fixture")
    save(root, evidence.HOST_INTERRUPTION, {"artificial": "separate host observation"})
    paths = {row["path"] for row in evidence.source_inputs(root)}
    assert evidence.HOST_INTERRUPTION in paths
