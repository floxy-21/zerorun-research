"""Fabricated unit fixtures for reconciliation; never research observations."""
import copy
import json

import pytest

from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1 import validate as v
from research.softwarex.handoff_v1.export import export


def rewrite(path, value):
    path.write_bytes(h.encoded(value))


def op(root, name, result, ms=10):
    h.save(root / (name + ".started.json"), {"operation": name, "started_utc": "unit-fixture"})
    row = {"operation": name, "result": result, "error": None, "outer_ms": ms, "completed_utc": "unit-fixture"}
    h.save(root / (name + ".json"), row)
    return row


def oracle(root, name, folder):
    nodes = ["tests/test_x.py::test_x"]
    values = [{"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}]
    capture = {"schema": "zerorun.benchmark-independent-pytest-shadow.v1", "exit_code": 0,
               "nodeids": nodes, "nodeid_sha256": h.sha(h.encoded({"nodeids": nodes})), "outcomes": values}
    h.save(root / folder / "raw-outcomes.json", capture)
    plugin = root / folder / "support/benchmark_shadow_plugin.py"
    plugin.parent.mkdir()
    plugin.write_bytes(b"# inert unit fixture\n")
    result = {"runner": {"exit_code": 0}, "capture": dict(capture, complete_per_node_outcomes=True),
              "verdict": {"exit_code": 0, "nodes": dict(zip(nodes, values))},
              "raw_outcomes": h.record(root / folder / "raw-outcomes.json"), "plugin_sha256": h.sha(plugin.read_bytes())}
    return op(root, name, result)


def sample(tmp_path):
    case = {"case_id": "a__b-1", "repo": "a/b", "base_commit": "a" * 40, "targets": ["tests/test_x.py"]}
    ledger = {"schema": "zerorun.handoff-selection.v1", "phase": "pilot", "cases": [case]}
    protocol = {"schema": "zerorun.controlled-handoff-run.v1", "phase": "pilot", "selection": ledger,
                "selection_sha256": "b" * 64, "selection_file": {"sha256": "b" * 64},
                "model_calls": 0, "mcp_authorities_created": False, "study_sources": h.code_inventory(),
                "selected_cases": 1, "planned_cases": 2}
    h.save(tmp_path / "protocol.json", protocol)
    base = tmp_path / "cases" / case["case_id"]
    oracle(base, "compatibility-oracle", "compatibility-capture")
    blocks = []
    for index in (0, 1):
        block_root = base / ("block-" + str(index))
        arms = {}
        for arm in ("fresh", "zerorun"):
            root = block_root / arm
            source = {"rows": [], "sha256": h.sha(h.encoded([])), "excluded_engine_state": sorted(h.STATE)}
            setup = {"before": source, "setup_outer_ms": 5}
            h.save(root / "setup.json", setup)
            h.save(root / "source-after.json", source)
            producer = op(root, "producer", {"exit_code": 0, "status": "MISS_EXECUTED", "cache_key": "a" * 64})
            consumer = op(root, "consumer", {"exit_code": 0, "status": "HIT_REUSED" if arm == "zerorun" else "MISS_EXECUTED", "cache_key": "a" * 64})
            fresh = oracle(root, "oracle", "oracle-capture")
            diagnostics = op(root, "diagnostics", {"exit_code": 0, "status": "VERIFY_MATCH"}) if arm == "zerorun" else None
            arms[arm] = {"arm": arm, "producer": producer, "consumer": consumer, "oracle": fresh,
                         "diagnostics": diagnostics, "setup_outer_ms": 5, "source_sha256": source["sha256"], "source_unchanged": True}
            h.save(root / "summary.json", arms[arm])
        block = {"block": index, "order": h.order(case["case_id"], index), "arms": arms, "measurements": h.summarize_block(arms)}
        h.save(block_root / "summary.json", block)
        blocks.append(block)
    completed_case = {"case_id": case["case_id"], "repo": case["repo"], "disposition": "COMPLETE",
                      "blocks": blocks, "all_blocks_complete": True, "agent_session": False}
    h.save(base / "completion.json", completed_case)
    completion = {"schema": "zerorun.controlled-handoff-completion.v1", "phase": "pilot",
        "protocol_sha256": h.sha((tmp_path / "protocol.json").read_bytes()), "new_model_calls": 0,
        "mcp_authority_files_found": 0, "study_source_unchanged": True, "runtime_source_unchanged": True,
        "cases": [completed_case], "selected_cases": 1, "complete_cases": 1,
        "all_assigned_outcomes_retained": True, "material_correctness_stop": False}
    h.save(tmp_path / "completion.json", completion)
    return tmp_path


def test_valid_complete_unit_fixture(tmp_path):
    assert v.validate_saved(sample(tmp_path))["complete_blocks"] == 2


@pytest.mark.parametrize("name", ["raw_oracle", "zero_seed_cost", "stale_status", "source_drift", "drop_case", "false_count", "key_change", "oracle_clock", "negative_setup"])
def test_saved_mutations_refused(tmp_path, name):
    sample(tmp_path)
    arm = tmp_path / "cases/a__b-1/block-0/zerorun"
    if name == "raw_oracle":
        path = arm / "oracle-capture/raw-outcomes.json"
        value = v.read(path)
        value["exit_code"] = 1
    elif name == "zero_seed_cost":
        path = arm / "producer.json"
        value = v.read(path)
        value["outer_ms"] = 0
    elif name == "stale_status":
        path = arm / "consumer.json"
        value = v.read(path)
        value["result"]["exit_code"] = 1
    elif name == "source_drift":
        path = arm / "source-after.json"
        value = v.read(path)
        value["rows"] = [{"changed": True}]
    elif name in {"drop_case", "false_count"}:
        path = tmp_path / "completion.json"
        value = v.read(path)
        value["cases" if name == "drop_case" else "complete_cases"] = [] if name == "drop_case" else 2
    elif name == "key_change":
        path = arm / "consumer.json"
        value = v.read(path)
        value["result"]["cache_key"] = "b" * 64
    elif name == "oracle_clock":
        path = arm / "oracle.json"
        value = v.read(path)
        value["outer_ms"] = False
    else:
        path = arm / "setup.json"
        value = v.read(path)
        value["setup_outer_ms"] = -10
    rewrite(path, value)
    with pytest.raises(ValueError):
        v.validate_saved(tmp_path)


def test_export_excludes_workspace_and_secret(tmp_path):
    source = sample(tmp_path / "run")
    secret = source / "private-cache-authentication-NOT-FOR-PUBLICATION/key.json"
    secret.parent.mkdir()
    secret.write_bytes(b"never publish")
    workspace = source / "cases/a__b-1/block-0/zerorun/workspace"
    workspace.mkdir()
    (workspace / "private.txt").write_bytes(b"not part of evidence")
    target = tmp_path / "export"
    export(source, target)
    assert not (target / secret.relative_to(source)).exists()
    assert not (target / workspace.relative_to(source)).exists()
    assert v.validate_saved(target)["complete_cases"] == 1


def test_incomplete_case_retains_disposition(tmp_path):
    sample(tmp_path)
    base = tmp_path / "cases/a__b-1"
    row = {"case_id": "a__b-1", "repo": "a/b", "disposition": "INCOMPLETE_OR_UNSUPPORTED", "error": "unit fixture"}
    rewrite(base / "completion.json", row)
    completion = v.read(tmp_path / "completion.json")
    completion.update(cases=[row], complete_cases=0)
    rewrite(tmp_path / "completion.json", completion)
    result = v.validate_saved(tmp_path)
    assert result["complete_blocks"] == 0
    assert result["additional_completed_blocks_from_incomplete_cases"] == 2
    assert result["complete_pair_chain_saved_fraction"] is None
