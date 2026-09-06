"""Offline, adversarial state-case receipts; no target code or Docker runs."""
import json
import os
from pathlib import Path

import pytest

from research.sqj.strengthening import analyze_state_rejoin as a
from research.sqj.strengthening import state_rejoin as producer
from research.sqj.strengthening import run_bound_state_rejoin as wrapper
from research.sqj.strengthening.tests.test_analyze_replication import make_protocol, stamp


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def capture(count, code):
    nodes = ["tests/test_search.py::test_fixture_" + str(i) for i in range(min(count, 14))]
    if count == 15:
        nodes.append("tests/test_search.py::test_search[elasticsearch7]")
    return {"schema": "zerorun.benchmark-independent-pytest-shadow.v1", "exit_code": code,
            "nodeids": nodes, "nodeid_sha256": a.sha(a.canonical({"nodeids": nodes}).encode()),
            "outcomes": [dict(a.PASS) for _ in nodes]}


@pytest.fixture
def case(tmp_path):
    case = tmp_path / "case"
    plan, reconstruction = a.frozen_material()
    write(case / "invocation.json", {"utc": stamp(100), "producer_sha256": a.RUNNER_SHA, "execute": True,
                                    "purposive_case": True, "full_agent_replay": False})
    write(case / "reconstruction.json", reconstruction)
    runtime = a.identity([{"path": "bin", "kind": "directory"},
        {"path": "bin/python", "kind": "file", "bytes": 3, "sha256": a.sha(b"run")}])
    runtime_rows = [{**r, "path": ".zerorun-env/" + r["path"]} for r in runtime["records"]]
    runtime_rows.insert(0, {"path": ".zerorun-env", "kind": "directory"})
    write(case / "dependency-setup.json", {"isolated_result_cache": True, "shared_hardlinks": False,
        "private_byte_copy": True, "read_only_mode_and_content_verified": True, "environment_bootstrap_ms": 2.0})
    inputs = sorted({r["path"].split("/")[0] for r in reconstruction["seed_source"]["records"]} | {".zerorun-env"})
    manifest = {"version": 2, "tasks": {}}
    for name, targets in (("rejoin-target", a.TARGET), ("rejoin-collection", a.COLLECTION)):
        manifest["tasks"][name] = {"inputs": inputs, "command": ["sh", ".zerorun-env/bin/python", "-m", "pytest", "-p", "no:cacheprovider", *targets],
            "image": a.IMAGE, "platform": "linux/amd64", "cacheable": True, "result_only": True, "closure_reviewed": True,
            "outputs": [], "cache_streams": False, "env": [], "unsafe_effects": []}
    write(case / "manifest.json", manifest)
    history = [c["call_id"] for c in sorted(plan["prior_non_mutation_tool_calls"] + plan["prior_recorded_source_mutations"], key=lambda c: c["index"])]
    rows = []
    for i, label in enumerate(a.LABELS):
        if i == 1:
            history.append(plan["segment"][1]["call_id"])
        elif i == 2:
            history.extend([plan["segment"][2]["call_id"], plan["segment"][3]["call_id"]])
        elif i == 3:
            history.append(plan["segment"][4]["call_id"])
        targets = a.COLLECTION if i == 2 else a.TARGET
        product = {"status": a.STATUSES[i], "exit_code": a.CODES[i], "cache_key": "a" * 64 if i in (0, 3) else str(i) * 64,
                   "request_wall_ms": 1.0, "stdout": "" if i == 3 else "test output", "stderr": ""}
        raw = capture(a.COUNTS[i], a.CODES[i])
        fresh = {"exit_code": a.CODES[i], "wall_ms": 3.0, "runner": "independent-docker-plain-pytest", "instrumented": True,
                 "capture": {**raw, "complete_per_node_outcomes": True, "failing_nodeids": [], "all_nodes_non_failing": True},
                 "stdout_tail": "test output", "stderr_tail": ""}
        source = reconstruction["changed_source" if i == 1 else "seed_source"]
        tree = a.identity(source["records"] + runtime_rows, {".zerorun", ".zerorun.json"})
        row = {"label": label, "source_before": tree, "source_after": tree,
               "history_reference_key": a.sha(a.canonical({"prior_event_ids": history, "command": targets}).encode()),
               "history_reference_is_not_tvcache": True, "product": product, "oracle_verdict": a.capture_verdict(raw, a.COUNTS[i], a.CODES[i]),
               "oracle_wall_ms": fresh["wall_ms"], "expected_exit_code": a.CODES[i], "expected_nodes": a.COUNTS[i],
               "source_unchanged_by_execution": True, "expected_verdict_observed": True}
        for name, value in (("product.json", product), ("fresh.json", fresh), ("fresh-outcomes.json", raw), ("observation.json", row)):
            write(case / label / name, value)
        rows.append(row)
        if i == 0:
            history.append(plan["segment"][0]["call_id"])
    summary = {"schema": "zerorun.controlled-agent-source-rejoin.v1", "requests": rows, "actual_product_statuses": a.STATUSES,
        "runtime_identity": runtime, "runtime_image": a.IMAGE, "operator_authority_created": False, "original_environment_reproduced": False,
        "scope": "changed-state oracle is extra instrumentation; no autonomous agent"}
    for key in ("completed", "cache_behavior_expected", "seed_repeat_source_equal", "seed_changed_source_different", "seed_repeat_key_equal",
                "seed_changed_key_different", "seed_repeat_fresh_verdict_equal", "history_reference_key_diverged"):
        summary[key] = True
    write(case / "summary.json", summary)
    return case


def mutate_row(case, index, change):
    summary = a.read_json(case / "summary.json")
    row = summary["requests"][index]
    change(row)
    write(case / "summary.json", summary)
    write(case / a.LABELS[index] / "observation.json", row)
    write(case / a.LABELS[index] / "product.json", row["product"])


def test_independent_reconstruction_matches_frozen_producer(tmp_path):
    _, expected = a.frozen_material()
    _, _, actual = producer.reconstruct(a.HERE / "evidence", tmp_path)
    # The actual experiment is POSIX-only. WindowsPath sorts case-insensitively;
    # compare all independently reconstructed names/bytes without claiming its
    # host-specific record ordering is the frozen POSIX ordering.
    if os.name == "nt":
        for key in ("seed_source", "changed_source", "restored_source"):
            original = actual[key]
            assert {r["path"]: r for r in original["records"]} == {r["path"]: r for r in expected[key]["records"]}
            assert original["sha256"] == a.sha(a.canonical(original["records"]).encode())
            actual[key] = a.identity(original["records"])
    assert actual == expected
    assert a.digest(a.HERE / "state_rejoin.py") == a.RUNNER_SHA


def test_complete_case_retains_counterfactual_and_no_agent_speedup(case):
    result = a.validate_case(case)
    assert result["requests"] == 4
    assert result["fresh_outcomes"] == [14, 15, 0, 14]
    assert result["optimized_hits"] == 1
    assert result["rows"][1]["counterfactual_instrumentation"] is True
    assert not result["autonomous_agent_evaluated"] and not result["tvcache_compared"]


def test_inventory_matches_posix_component_order_for_package_metadata():
    result = a.identity([{"path": name, "kind": "directory"} for name in ("pkg-1.dist-info", "pkg/module", "pkg")])
    assert [r["path"] for r in result["records"]] == ["pkg", "pkg/module", "pkg-1.dist-info"]
    a.validate_identity(result)


@pytest.mark.parametrize("index,status", [(0, "HIT_REUSED"), (1, "HIT_REUSED"), (2, "MISS_EXECUTED"), (3, "MISS_EXECUTED")])
def test_actual_wrong_status_rejected_even_if_summary_says_success(case, index, status):
    mutate_row(case, index, lambda r: r["product"].update(status=status))
    with pytest.raises(ValueError, match="cache status"):
        a.validate_case(case)


@pytest.mark.parametrize("kind", ["restored-key", "same-changed-key", "hit-stdout", "source-after", "scope", "authority", "missing-capture", "failure-receipt"])
def test_adversarial_case_receipts_rejected(case, kind):
    if kind == "restored-key":
        mutate_row(case, 3, lambda r: r["product"].update(cache_key="b" * 64))
    elif kind == "same-changed-key":
        mutate_row(case, 1, lambda r: r["product"].update(cache_key="a" * 64))
    elif kind == "hit-stdout":
        mutate_row(case, 3, lambda r: r["product"].update(stdout="a transcript"))
    elif kind == "source-after":
        mutate_row(case, 3, lambda r: r["source_after"].update(sha256="0" * 64))
    elif kind == "scope":
        raw = a.read_json(case / "summary.json")
        raw["original_environment_reproduced"] = True
        write(case / "summary.json", raw)
    elif kind == "authority":
        write(case / "private-cache-authentication-NOT-FOR-PUBLICATION/repositories/fixture/authorities/authority.json", {})
    elif kind == "missing-capture":
        (case / a.LABELS[3] / "fresh-outcomes.json").unlink()
    else:
        write(case / "failure.json", {"error": "retained failure"})
    with pytest.raises((ValueError, OSError)):
        a.validate_case(case)


@pytest.mark.parametrize("kind", ["bool-exit", "duplicate-node", "wrong-hash", "skipped-node", "wrong-node-count", "exit-five-to-zero"])
def test_fresh_outcome_adversarial_capture(kind):
    raw, count, code = capture(14, 0), 14, 0
    if kind == "bool-exit":
        raw["exit_code"] = False
    elif kind == "duplicate-node":
        raw["nodeids"][1] = raw["nodeids"][0]
    elif kind == "wrong-hash":
        raw["nodeid_sha256"] = "0" * 64
    elif kind == "skipped-node":
        raw["outcomes"][0]["call"] = "skipped"
    elif kind == "wrong-node-count":
        count = 15
    else:
        raw, count, code = capture(0, 0), 0, 5
    with pytest.raises(ValueError):
        a.capture_verdict(raw, count, code)


def test_missing_wrapper_never_certifies_case(case):
    result = a.analyze(case.parent)
    assert not result["completed"] and result["errors"] and not result["rows"]


def test_failed_case_does_not_emit_favorable_partial_table(case):
    write(case / "failure.json", {"error": "retained failure"})
    result = a.analyze(case.parent, wrapper_validator=lambda _: {})
    assert not result["completed"] and result["rows"] == []
    assert len(result["retained_request_directories"]) == 4


@pytest.fixture
def bound(case):
    root = case.parent
    prior = root / "replication"
    prior.mkdir()
    frozen = make_protocol(prior)
    completion = {"schema": "zerorun.randomized-short-replication-summary.v1", "completed": True,
        "completed_utc": stamp(10), "protected_source_unchanged": True, "operator_authority_receipts_created": False,
        "protected_source_after": frozen["protected_source_identity"]}
    write(prior / "campaign-summary.json", completion)
    protected = frozen["protected_source_identity"]
    core = wrapper.core_identity(protected)
    protocol = {"schema": "zerorun.bound-state-rejoin.protocol.v1", "created_utc": stamp(20),
        "runner": wrapper.file_record(a.HERE / "state_rejoin.py", label="strengthening/state_rejoin.py"),
        "wrapper": wrapper.file_record(a.HERE / "run_bound_state_rejoin.py", label="strengthening/run_bound_state_rejoin.py"),
        "helper_files": wrapper.helper_records(a.HERE.parent / "source-final"),
        "evidence_files": [wrapper.file_record(a.HERE / "evidence" / p, label=p) for p in wrapper.EVIDENCE],
        "protected_source_before": protected, "core_protected_before": core, "engine_identity_before": frozen["engine_identity"],
        "replication": {"protocol_file": wrapper.file_record(prior / "protocol.json"), "completion_file": wrapper.file_record(prior / "campaign-summary.json"),
            "protected_source_identity": protected, "core_protected_identity": core, "engine_identity": frozen["engine_identity"], "completed": True},
        "preflight_errors": [], "operator_authorization_permitted": False,
        "invocation": {"engine": "/engine", "evidence": "/evidence", "child_directory": "case",
                       "runner_args": ["--engine", "/engine", "--evidence", "/evidence", "--output", "/results/case", "--execute-reviewed-lab"]},
        "checks": {k: True for k in ("runner_hash_expected", "replication_helper_hash_expected", "recovery_helper_hash_expected", "replication_completed",
                  "protected_matches_replication", "core_matches_replication", "engine_matches_replication")}}
    write(root / "protocol.json", protocol)
    final = {"schema": "zerorun.bound-state-rejoin.completion.v1", "protocol_sha256": a.digest(root / "protocol.json"),
        "outcome": {"return_code": 0, "error_type": None, "error": None, "runner_invoked": True},
        "protected_source_after": protected, "core_protected_after": core, "engine_identity_after": frozen["engine_identity"],
        "helper_files_after": protocol["helper_files"], "evidence_files_after": protocol["evidence_files"],
        "wrapper_after": protocol["wrapper"], "runner_after": protocol["runner"], "completed_utc": stamp(200), "completed": True,
        "case_summary_file": wrapper.file_record(case / "summary.json", label="case/summary.json"), "postflight_errors": [],
        "checks": {k: True for k in ("protected_source_unchanged", "core_source_unchanged", "engine_identity_unchanged", "helper_files_unchanged",
                  "wrapper_unchanged", "runner_unchanged", "evidence_files_unchanged", "case_completed", "no_operator_authority")}}
    write(root / "completion.json", final)
    return root, prior


def test_wrapper_validates_source_hashes_protocol_and_full_case(bound):
    root, prior = bound
    result = a.analyze(root, replication_directory=prior)
    assert result["completed"], result["errors"]
    assert result["provenance"]["protected_source_verified"]
    assert len(result["compact_table_markdown"].splitlines()) == 6


@pytest.mark.parametrize("field", ["protocol_sha256", "protected_source_after", "helper_files_after", "case_summary_file", "completed"])
def test_wrapper_tampering_never_certifies(bound, field):
    root, prior = bound
    final = a.read_json(root / "completion.json")
    final[field] = "changed" if field == "protocol_sha256" else False if field == "completed" else {}
    write(root / "completion.json", final)
    result = a.analyze(root, replication_directory=prior)
    assert not result["completed"] and result["errors"]
