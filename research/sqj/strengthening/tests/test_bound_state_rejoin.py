"""Offline external-binding tests. The real case runner is never invoked."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.sqj.strengthening import run_bound_state_rejoin as w


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}'])
def test_non_strict_json_refused(raw):
    with pytest.raises(ValueError):
        w.strict_json(raw)


def test_core_projection_order_and_digest():
    files = [{"path": "engine/a", "sha256": "one", "bytes": 1},
             {"path": "sqj/b", "sha256": "two", "bytes": 2},
             {"path": "engine/c", "sha256": "three", "bytes": 3}]
    result = w.core_identity({"files": files})
    expected = [files[0], files[2]]
    assert result == {"files": expected, "sha256": w.sha(w.canonical(expected))}


@pytest.mark.parametrize("files", [[], [{"path": "sqj/a"}], [{"path": "engine/a"}, {"path": "engine/a"}]])
def test_core_projection_empty_or_duplicate_refused(files):
    with pytest.raises(ValueError):
        w.core_identity({"files": files})


def prepare(monkeypatch, tmp_path, *, fail_runner=False, drift=False, prior_completed=True):
    engine = tmp_path / "engine"
    engine.mkdir()
    evidence = w.HERE / "evidence"
    previous = tmp_path / "replication"
    previous.mkdir()
    protected = {"sha256": "fixture-protected", "files": [
        {"path": "engine/a.py", "sha256": "fixture-file", "bytes": 1},
        {"path": "sqj/producers/a.py", "sha256": "fixture-helper", "bytes": 2}]}
    engine_identity = {"identity_sha256": "fixture-engine"}
    protocol = {"schema": "zerorun.randomized-short-replication.v1",
                "protected_source_identity": protected, "engine_identity": engine_identity}
    (previous / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
    (previous / "campaign-summary.json").write_text(json.dumps({
        "schema": "zerorun.randomized-short-replication-summary.v1", "completed": prior_completed,
        "protected_source_unchanged": True, "operator_authority_receipts_created": False,
        "protected_source_after": protected}), encoding="utf-8")
    helpers = [
        {"path": "replication", "sha256": w.REPLICATION_HELPER_SHA, "bytes": 1},
        {"path": "recovery", "sha256": w.RECOVERY_SHA, "bytes": 1},
    ]
    monkeypatch.setattr(w, "helper_records", lambda root: helpers)
    output = tmp_path / "bound"
    calls = []
    captures = []

    def capture(root):
        captures.append(True)
        if drift and len(captures) > 1:
            return {"sha256": "changed", "files": [
                {"path": "engine/a.py", "sha256": "changed", "bytes": 1}]}
        return protected

    def fake_main(args):
        calls.append(args)
        # Primary safety boundary: a complete protocol is already on disk.
        frozen = json.loads((output / "protocol.json").read_bytes())
        assert frozen["checks"]["core_matches_replication"]
        assert frozen["invocation"]["child_directory"] == "case"
        assert args[args.index("--output") + 1] == str(output / "case")
        if fail_runner:
            raise RuntimeError("fixture execution failed")
        case = output / "case"
        case.mkdir()
        (case / "summary.json").write_text('{"completed":true}', encoding="utf-8")
        return 0

    monkeypatch.setattr(w, "load_components", lambda root: (
        SimpleNamespace(_capture_source_identity=lambda: engine_identity),
        SimpleNamespace(protected_source_identity=capture),
        SimpleNamespace(main=fake_main)))
    return engine, evidence, previous / "protocol.json", output, calls


def test_protocol_before_case_and_success_receipt(monkeypatch, tmp_path):
    engine, evidence, previous, output, calls = prepare(monkeypatch, tmp_path)
    assert w.run_bound(engine, evidence, previous, output) == 0
    result = json.loads((output / "completion.json").read_bytes())
    assert result["completed"]
    assert len(calls) == 1
    assert result["protocol_sha256"] == w.sha((output / "protocol.json").read_bytes())
    assert all(result["checks"].values())


def test_runner_failure_preserves_post_identity(monkeypatch, tmp_path):
    engine, evidence, previous, output, calls = prepare(monkeypatch, tmp_path, fail_runner=True)
    assert w.run_bound(engine, evidence, previous, output) == 1
    result = json.loads((output / "completion.json").read_bytes())
    assert len(calls) == 1
    assert not result["completed"]
    assert result["outcome"]["error_type"] == "RuntimeError"
    assert result["checks"]["protected_source_unchanged"]
    assert result["protected_source_after"]


def test_post_execution_core_drift_invalidates(monkeypatch, tmp_path):
    engine, evidence, previous, output, calls = prepare(monkeypatch, tmp_path, drift=True)
    assert w.run_bound(engine, evidence, previous, output) == 1
    result = json.loads((output / "completion.json").read_bytes())
    assert len(calls) == 1
    assert not result["completed"]
    assert not result["checks"]["protected_source_unchanged"]
    assert not result["checks"]["core_source_unchanged"]


def test_incomplete_replication_never_invokes_case(monkeypatch, tmp_path):
    engine, evidence, previous, output, calls = prepare(monkeypatch, tmp_path, prior_completed=False)
    assert w.run_bound(engine, evidence, previous, output) == 1
    assert calls == []
    protocol = json.loads((output / "protocol.json").read_bytes())
    result = json.loads((output / "completion.json").read_bytes())
    assert protocol["preflight_errors"]
    assert not result["outcome"]["runner_invoked"]
    assert not result["completed"]


def test_runner_hash_mismatch_never_invokes_case(monkeypatch, tmp_path):
    engine, evidence, previous, output, calls = prepare(monkeypatch, tmp_path)
    monkeypatch.setattr(w, "RUNNER_SHA", "0" * 64)
    assert w.run_bound(engine, evidence, previous, output) == 1
    assert calls == []
    result = json.loads((output / "completion.json").read_bytes())
    assert not result["completed"]


def test_append_only_completion(tmp_path):
    path = tmp_path / "receipt"
    w.write_once(path, {"completed": False})
    with pytest.raises(FileExistsError):
        w.write_once(path, {"completed": True})
    assert json.loads(path.read_bytes()) == {"completed": False}
