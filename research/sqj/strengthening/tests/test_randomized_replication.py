"""Offline driver checks: no benchmarks, containers, network or authority."""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.sqj.strengthening import randomized_replication as replication


ROOT = Path(__file__).resolve().parents[4]


def test_schedule_is_exact_reproducible_balanced_cohort():
    schedule = replication.schedule()
    assert schedule == replication.schedule()
    assert {row["workload"] for row in schedule} == set(replication.COHORT)
    assert len(schedule) == 4
    assert "more-itertools" not in {row["workload"] for row in schedule}
    for row in schedule:
        orders = [tuple(block["order"]) for block in row["trajectories"]]
        assert set(orders) == set(permutations(replication.ARMS))
        assert len(orders) == 6
        assert [block["trajectory"] for block in row["trajectories"]] == list(range(1, 7))
        for position in range(3):
            assert Counter(order[position] for order in orders) == dict.fromkeys(replication.ARMS, 2)


def test_request_ledger_retains_every_seed_failure_repeat_and_restoration():
    rows = [replication.expected_request(index) for index in range(7)]
    assert [row["label"] for row in rows] == list(replication.LABELS)
    assert Counter(row["expected_cache_status"] for row in rows) == {
        "MISS_EXECUTED": 1, "MISS_FAILED": 2, "HIT_REUSED": 4}
    assert [row["expected_exit_code"] for row in rows] == [0, 0, 0, 1, 1, 0, 0]
    assert 4 * 6 * len(rows) == 168
    assert 4 * 6 * 4 == 96
    assert 4 * 6 * 3 == 72
    assert 4 * 6 * len(rows) * len(replication.ARMS) == 504


@pytest.mark.parametrize("value", [-1, 7, True, 1.0, "1"])
def test_invalid_request_index_rejected(value):
    with pytest.raises(ValueError):
        replication.expected_request(value)


def test_all_cache_namespaces_are_independent():
    names = [replication.task_name(name, trajectory, arm) for name in replication.COHORT
             for trajectory in range(1, 7) for arm in replication.ARMS[1:]]
    assert len(names) == len(set(names)) == 48


def test_phase_wrapper_preserves_arguments_return_value_and_exception():
    calls, phases = [], []
    returned = SimpleNamespace(returncode=0)
    def original(root, argv, *, timeout_seconds):
        calls.append((root, argv, timeout_seconds))
        return returned
    wrapped = replication.measured_plain_runner(original, phases)
    argv = ["/usr/bin/docker", "start", "--attach", "example"]
    assert wrapped("root", argv, timeout_seconds=900) is returned
    assert calls == [("root", argv, 900)]
    assert phases[0]["phase"] == "start"
    assert phases[0]["wall_ms"] >= 0
    assert phases[0]["exit_code"] == 0
    def failing(*args, **kwargs):
        raise RuntimeError("example failure")
    with pytest.raises(RuntimeError, match="example failure"):
        replication.measured_plain_runner(failing, phases)("root", argv, timeout_seconds=900)
    assert phases[-1]["error_type"] == "RuntimeError"


def test_measured_failure_preserved_and_patch_restored(tmp_path, monkeypatch):
    monkeypatch.setattr(replication, "host_snapshot", lambda: {"test": True})
    original = lambda root, argv, timeout_seconds: SimpleNamespace(returncode=0)
    bench = SimpleNamespace(_plain_run=original)
    def failing():
        bench._plain_run("root", ["docker", "inspect", "id"], timeout_seconds=60)
        raise RuntimeError("failure must survive")
    receipt = tmp_path / "failed.json"
    with pytest.raises(RuntimeError, match="failure must survive"):
        replication.measure_call(receipt, failing, bench=bench)
    raw = json.loads(receipt.read_text())
    assert not raw["completed"]
    assert raw["docker_subprocess_phases"][0]["phase"] == "inspect"
    assert raw["request_wall_ms"] >= 0
    assert bench._plain_run is original
    with pytest.raises(FileExistsError):
        replication.save(receipt, {})


def test_limits_are_matched_without_mutation():
    bench = SimpleNamespace(_PYTEST_EXECUTION_TIMEOUT_SECONDS=900, _PLAIN_DOCKER_RESOURCE_ARGS=("--cpus", "2"))
    oci = SimpleNamespace(DOCKER_EXECUTION_TIMEOUT_SECONDS=900, DOCKER_RESOURCE_ARGS=("--cpus", "2"))
    replication.validate_limits(bench, oci)
    oci.DOCKER_EXECUTION_TIMEOUT_SECONDS = 600
    with pytest.raises(ValueError, match="900"):
        replication.validate_limits(bench, oci)
    oci.DOCKER_EXECUTION_TIMEOUT_SECONDS = 900
    oci.DOCKER_RESOURCE_ARGS = ("--cpus", "1")
    with pytest.raises(ValueError, match="resource"):
        replication.validate_limits(bench, oci)


def test_correctness_mismatch_and_false_cache_claim_are_not_passes():
    row = {"index": 1, "arms": {name: {"exit_code": 0, "status": "HIT_REUSED"} for name in replication.ARMS},
           "oracle": {"exit_code": 0, "capture": {"exit_code": 0, "complete_per_node_outcomes": True}}}
    assert all(replication.assess_request(row).values())
    wrong = deepcopy(row)
    wrong["arms"]["fast"]["exit_code"] = 1
    assert not replication.assess_request(wrong)["whole_task_exit_agreement"]
    wrong = deepcopy(row)
    wrong["arms"]["snapshot"]["status"] = "BYPASS"
    assert not replication.assess_request(wrong)["cache_behavior_expected"]
    wrong = deepcopy(row)
    wrong["oracle"]["capture"]["complete_per_node_outcomes"] = False
    assert not replication.assess_request(wrong)["whole_task_exit_agreement"]


def test_readonly_planning_and_helper_loading_preserve_frozen_sources():
    before = replication.protected_source_identity(ROOT)
    recovery = replication.load_recovery()
    assert tuple(recovery.LABELS) == replication.LABELS
    replication.schedule()
    assert before == replication.protected_source_identity(ROOT)
    assert replication.sha256_file(replication.SQJ / "producers/controlled_comparison-recovery.py") == replication.RECOVERY_SHA256


def test_protocol_is_frozen_before_execution_and_cannot_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(replication, "host_snapshot", lambda: {"test": True})
    from research.sqj.run_frozen_campaign import WORKLOADS, IMAGE
    bench = SimpleNamespace(_PYTEST_EXECUTION_TIMEOUT_SECONDS=900,
        _PLAIN_DOCKER_RESOURCE_ARGS=("--cpus", "2"), _capture_source_identity=lambda: {"frozen": True})
    oci = SimpleNamespace(DOCKER_EXECUTION_TIMEOUT_SECONDS=900, DOCKER_RESOURCE_ARGS=("--cpus", "2"))
    protocol = replication.freeze_protocol(tmp_path, ROOT, bench, oci, WORKLOADS, IMAGE)
    saved = json.loads((tmp_path / "protocol.json").read_text())
    assert saved["schedule"] == protocol["schedule"]
    assert saved["expected_requests"] == 168
    assert saved["expected_timed_arm_invocations"] == 504
    assert saved["warmups_removed"] == 0
    assert saved["operator_authority_allowed"] is False
    assert saved["testmon_in_this_replication"] is False
    assert "original five-subject evidence retained" in saved["more_itertools"]
    with pytest.raises(FileExistsError):
        replication.freeze_protocol(tmp_path, ROOT, bench, oci, WORKLOADS, IMAGE)


def mocked_laboratory(tmp_path, monkeypatch, *, inject_mismatch=False):
    """Only filesystem fixtures and in-memory responses; never launches Docker."""
    monkeypatch.setattr(replication, "host_snapshot", lambda: {"test": True})
    monkeypatch.setattr(replication.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stderr=b""))
    monkeypatch.setattr(replication.subprocess, "check_output", lambda *args, **kwargs: "tests\n")
    workspaces, cache_keys, calls = [], set(), []
    @contextmanager
    def dependency(*args, **kwargs):
        yield {"root": tmp_path, "snapshot": {"fake": True}, "timing_ms": {"setup": 0}}
    @contextmanager
    def worktree(*args, trajectory_index, **kwargs):
        work = tmp_path / f"work-{trajectory_index}"
        (work / "tests").mkdir(parents=True)
        (work / "tests/test_example.py").write_bytes(b"def test_example():\n    pass\n")
        workspaces.append(work)
        yield work
    def materialize(work, **kwargs):
        replication.save(work / ".zerorun.json", {"version": 2, "tasks": {
            "pytest-generalization": {"command": ["python", "-m", "pytest"]}}})
        return {"test_setup": True}
    def plain(work, **kwargs):
        calls.append(("direct", str(work)))
        return {"exit_code": int(replication.FAILURE_SUFFIX in (work / "tests/test_example.py").read_bytes()), "wall_ms": 1}
    bench = SimpleNamespace(_capture_source_identity=lambda: {"constant": True},
        _SHADOW_PLUGIN="# fixture only\n", _frozen_dependency_layer=dependency,
        _isolated_worktree=worktree, _materialize_frozen_pytest_environment=materialize,
        _plain_pytest_container=plain, _plain_run=lambda *args, **kwargs: None)
    def load(path):
        manifest = json.loads(path.read_text())
        return SimpleNamespace(root=path.parent, tasks={name: name for name in manifest["tasks"]})
    def run_task(manifest, task, **kwargs):
        failure = replication.FAILURE_SUFFIX in (manifest.root / "tests/test_example.py").read_bytes()
        status = "MISS_FAILED" if failure else "HIT_REUSED" if task in cache_keys else "MISS_EXECUTED"
        code = int(failure)
        if inject_mismatch and status == "HIT_REUSED" and task.endswith("fast"):
            code = 1
        if not failure:
            cache_keys.add(task)
        calls.append((task, str(manifest.root)))
        return SimpleNamespace(as_dict=lambda: {"exit_code": code, "status": status,
                                               "wall_ms": 1, "phase_ms": {"test": 1}})
    def fresh(bench, work, targets, image, support, request):
        calls.append(("oracle", str(work)))
        code = int(replication.FAILURE_SUFFIX in (work / "tests/test_example.py").read_bytes())
        capture = {"exit_code": code, "complete_per_node_outcomes": True}
        replication.save(request / "fresh-outcomes.json", capture)
        return {"exit_code": code, "capture": capture, "wall_ms": 1}
    return bench, SimpleNamespace(run_task=run_task), SimpleNamespace(_try_readonly_whole_task_hit=lambda: "untouched"), load, SimpleNamespace(fresh_capture=fresh), workspaces, cache_keys, calls


def test_mocked_six_block_workload_is_complete_restored_and_namespaced(tmp_path, monkeypatch):
    bench, api, hermetic, load, recovery, workspaces, cache_keys, calls = mocked_laboratory(tmp_path, monkeypatch)
    item = ("packaging", "fixture/upstream", "a" * 40, "fixture", ("tests/test_example.py",), ())
    blocks = next(row["trajectories"] for row in replication.schedule() if row["workload"] == "packaging")
    result = replication.run_workload(bench, api, hermetic, load, recovery, item, tmp_path,
                                      tmp_path / "result", "fixture-image", blocks)
    assert result["completed"] and result["source_stable"]
    assert len(result["rows"]) == 42
    assert len(result["blocks"]) == 6
    assert len(workspaces) == 6
    assert len(cache_keys) == 12
    assert len(calls) == 42 * 4
    assert Counter(row["arms"]["fast"]["status"] for row in result["rows"]) == {
        "HIT_REUSED": 24, "MISS_EXECUTED": 6, "MISS_FAILED": 12}
    assert all(replication.FAILURE_SUFFIX not in (work / "tests/test_example.py").read_bytes() for work in workspaces)
    assert hermetic._try_readonly_whole_task_hit() == "untouched"
    for block in blocks:
        path = tmp_path / "result" / f"trajectory-{block['trajectory']}"
        assert json.loads((path / "restoration.json").read_text())["restored"] is True
        assert (path / "request-6/fresh-outcomes.json").is_file()


def test_material_mismatch_preserves_failed_block_then_stops_workload(tmp_path, monkeypatch):
    bench, api, hermetic, load, recovery, workspaces, cache_keys, calls = mocked_laboratory(tmp_path, monkeypatch, inject_mismatch=True)
    item = ("packaging", "fixture/upstream", "a" * 40, "fixture", ("tests/test_example.py",), ())
    blocks = next(row["trajectories"] for row in replication.schedule() if row["workload"] == "packaging")
    result = replication.run_workload(bench, api, hermetic, load, recovery, item, tmp_path,
                                      tmp_path / "result", "fixture-image", blocks)
    assert not result["completed"]
    assert len(result["blocks"]) == 1
    assert len(result["rows"]) == 2
    assert len(workspaces) == 1
    assert result["blocks"][0]["retry_attempted"] is False
    assert result["blocks"][0]["error_type"] == "RuntimeError"
    assert (tmp_path / "result/trajectory-1/request-1/fresh-outcomes.json").is_file()
    assert not (tmp_path / "result/trajectory-2").exists()
    assert json.loads((tmp_path / "result/trajectory-1/restoration.json").read_text())["restored"]
