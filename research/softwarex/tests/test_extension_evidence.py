"""Offline reconciliation tests; fixtures never invoke Codex, MCP, or Docker."""
import base64
from copy import deepcopy
import json
from pathlib import PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest

from research.softwarex import build_extension_evidence as b


@pytest.mark.parametrize("path_type,base", [(PureWindowsPath, "C:/artifact"), (PurePosixPath, "/artifact")])
@pytest.mark.parametrize("reverse_discovery", [False, True])
def test_retained_inventory_sort_is_case_sensitive_posix_on_every_platform(path_type, base, reverse_discovery):
    base_path = path_type(base)
    names = ["non-model-diagnostic.json", "NON_MODEL_SETUP_NOTES.md", "PRE_MODEL_PREPARATION.md",
             "receipt.json", "nested/b.json", "nested/A.json"]
    discovered = [base_path / "evidence" / name for name in names]
    if reverse_discovery:
        discovered.reverse()
    class Root:
        def __fspath__(self):
            return str(base_path)
        def __truediv__(self, relative):
            assert relative == "evidence"
            class Tree:
                def rglob(self, pattern):
                    assert pattern == "*"
                    return iter(discovered)
            return Tree()
    observed = b.retained_paths(Root(), "evidence")
    assert [path.relative_to(base_path / "evidence").as_posix() for path in observed] == sorted(names)


def seal(value):
    value.pop("evidence_payload_sha256", None)
    value["evidence_payload_sha256"] = b.canonical_hash(value)
    return value


def encoded(events):
    raw = ("\n".join(json.dumps(row) for row in events) + "\n").encode()
    value = {"returncode": 0, "timed_out": False}
    for name, data in (("stdout", raw), ("stderr", b"")):
        value.update({name + "_base64": base64.b64encode(data).decode(), name + "_bytes": len(data),
                      name + "_sha256": b.sha(data), name + "_truncated": False})
    return value


def stage(index):
    tool = "doctor" if index == 0 else "run_tests"
    args = {"root": "/synthetic"}
    if index:
        args.update(task="synthetic-lifecycle", verify=index == 3)
    key = "a" * 64
    if index == 0:
        payload = {"ok": True, "manifest_present": True, "manifest_authorized": True,
                   "reuse_ready": True, "task_reuse_ready": True, "manifest_version": 2,
                   "mode": "task-reuse", "root": "/synthetic", "tasks": [
                       {"task": "synthetic-lifecycle", "status": "CACHEABLE", "reason": None, "cache_key": key}]}
    else:
        payload = {"task": "synthetic-lifecycle", "status": b.STATUSES[index], "mode": "reuse",
                   "exit_code": 0, "verified": index == 3, "restored_outputs": [],
                   "stdout_tail": "", "stderr_tail": "", "cache_key": key}
    call = {"id": "call", "type": "mcp_tool_call", "server": "zerorun", "tool": tool, "arguments": args}
    events = [{"type": "thread.started"}, {"type": "turn.started"},
              {"type": "item.started", "item": {**call, "status": "in_progress"}},
              {"type": "item.completed", "item": {**call, "status": "completed", "result": {
                  "structured_content": payload, "content": [{"type": "text", "text": json.dumps(payload)}]}}},
              {"type": "item.completed", "item": {"id": "message", "type": "agent_message", "text": b.MARKERS[index]}},
              {"type": "turn.completed"}]
    analysis = {"event_counts": {"thread.started": 1, "turn.started": 1, "item.started": 1, "item.completed": 2, "turn.completed": 1},
                "mcp_call_count": 1, "call_id": "call", "tool": tool, "arguments": args, "response": payload,
                "response_sha256": b.canonical_hash(payload), "command_execution_count": 0,
                "file_change_count": 0, "retry_count": 0, "final_message": b.MARKERS[index]}
    return {"tool": tool, "verify": None if index == 0 else index == 3,
            "prompt": "sealed fixture prompt", "prompt_utf8_sha256": b.sha(b"sealed fixture prompt"),
            "execution_root_unchanged": True, "jsonl": encoded(events), "analysis": analysis}


def example():
    expected = [{"path": f"file-{index:02}.py", "bytes": 1, "sha256": "b" * 64} for index in range(36)]
    tree = {"files": expected, "file_count": 36, "bytes": 36, "identity_sha256": b.canonical_hash(expected)}
    original = {"schema": "zerorun.codex-agent-synthetic-lifecycle/v1", "real_repository_authorized": False,
                "runtime_acquisition_attempted": False, "design": {"agent_retry_allowed": False, "one_mcp_call_per_agent_turn": True},
                "agent_stages": [stage(index) for index in range(4)], "functional_lifecycle_pass": True,
                "status": "pass", "evidence_valid": True, "source": {"package": tree,
                    "commit": b.LIVE_PUBLIC_COMMIT, "git": {"head": b.LIVE_PUBLIC_COMMIT}, "git_after": {"head": b.LIVE_PUBLIC_COMMIT}, "harness": {
                    "sha256": b.PROTECTED_HELPERS["tools/codex_agent_lifecycle.py"][0]}},
                "zerorun": {"installed_package": {"module": deepcopy(tree)}, "installed_matches_source_package": True},
                "identity_unchanged": {key: True for key in ("source_worktree", "codex_executable", "zerorun_launcher",
                    "zerorun_installed_package", "synthetic_reviewed_source", "codex_authenticated_installation")},
                "synthetic_repository": {"path": "/synthetic", "unchanged_reviewed_source": True},
                "external_authority": {"synthetic_only": True, "outside_synthetic_repository": True, "secret_values_recorded": False},
                "lifecycle": {"statuses": list(b.STATUSES[1:]), "cache_key": "a" * 64, "exact_expected_sequence": True,
                    "total_agent_turns": 4, "total_mcp_calls": 4, "total_retries": 0}}
    envelope = {"schema": "zerorun.softwarex-public-lifecycle.v1", "original_receipt": seal(original),
                "functional_lifecycle_pass": True, "evidence_valid": True, "source_recheck": {"passed": True}, "research_public_layout_adapter": {
                    "schema": "zerorun.public-lifecycle-layout-adapter.v1", "adapter_sha256": "adapter",
                    "protocol_sha256": "protocol", "pinned_public_manifest_sha256": "manifest",
                    "protected_helpers_sha256": {name: pair[0] for name, pair in b.PROTECTED_HELPERS.items()},
                    "pre_model_support_amendment": deepcopy(b.SUPPORT_AMENDMENT),
                    "only_adaptation": "source-package inventory uses root/src/zerorun instead of root/zerorun",
                    "runtime_modified": False, "original_validation_and_authority_unchanged": True}}
    return seal(envelope), expected


def validate(value, expected):
    return b.validate_live_envelope(value, expected=expected, adapter_sha="adapter", protocol_sha="protocol", manifest_sha="manifest")


def reseal(value):
    seal(value["original_receipt"])
    return seal(value)


def test_exact_pass_recomputes_four_raw_turns():
    value, expected = example()
    result = validate(value, expected)
    assert result["functional_lifecycle_pass"] is True
    assert [row["status"] for row in result["independently_validated_stages"]] == ["READY", *b.STATUSES[1:]]
    assert result["stage_adverse_outcomes"] == []


@pytest.mark.parametrize("part", ["outer", "inner"])
def test_self_hash_tampering_rejected(part):
    value, expected = example()
    if part == "outer":
        value["source_recheck"]["passed"] = False
    else:
        value["original_receipt"]["status"] = "changed"
        seal(value)
    with pytest.raises(ValueError, match="self-hash"):
        validate(value, expected)


@pytest.mark.parametrize("field", ["adapter_sha256", "protocol_sha256", "pinned_public_manifest_sha256", "protected_helpers_sha256", "pre_model_support_amendment"])
def test_live_bindings_reject_changes(field):
    value, expected = example()
    value["research_public_layout_adapter"][field] = "changed"
    with pytest.raises(ValueError, match="binding"):
        validate(seal(value), expected)


@pytest.mark.parametrize("mutation", ["runtime", "authority", "retry", "missing_turn", "extra_turn", "wrong_installed",
    "inventory_count", "source_helper", "changed_identity", "authority_secret", "wrong_summary", "inconsistent_outer", "commit", "git_drift", "renamed_identity"])
def test_unjustified_passes_fail_closed(mutation):
    value, expected = example()
    original = value["original_receipt"]
    if mutation == "runtime":
        value["research_public_layout_adapter"]["runtime_modified"] = True
    elif mutation == "authority":
        original["real_repository_authorized"] = True
    elif mutation == "retry":
        original["design"]["agent_retry_allowed"] = True
    elif mutation == "missing_turn":
        original["agent_stages"].pop()
    elif mutation == "extra_turn":
        original["agent_stages"].append(stage(3))
    elif mutation == "wrong_installed":
        original["zerorun"]["installed_package"]["module"]["files"][0]["sha256"] = "c" * 64
    elif mutation == "inventory_count":
        original["source"]["package"]["file_count"] = 35
    elif mutation == "source_helper":
        original["source"]["harness"]["sha256"] = "c" * 64
    elif mutation == "changed_identity":
        original["identity_unchanged"]["source_worktree"] = False
    elif mutation == "authority_secret":
        original["external_authority"]["secret_values_recorded"] = True
    elif mutation == "wrong_summary":
        original["lifecycle"]["total_mcp_calls"] = 5
    elif mutation == "commit":
        original["source"]["commit"] = "0" * 40
    elif mutation == "git_drift":
        original["source"]["git_after"]["head"] = "0" * 40
    elif mutation == "renamed_identity":
        original["identity_unchanged"]["invented"] = original["identity_unchanged"].pop("source_worktree")
    else:
        value["functional_lifecycle_pass"] = False
    with pytest.raises(ValueError):
        validate(reseal(value), expected)


def test_postflight_source_failure_is_adverse_not_original_pass():
    value, expected = example()
    value["source_recheck"] = {"passed": False, "message": "changed source"}
    value["functional_lifecycle_pass"] = False
    value["evidence_valid"] = False
    result = validate(seal(value), expected)
    assert result["functional_lifecycle_pass"] is False
    assert len(result["independently_validated_stages"]) == 4


def test_explicit_pre_model_failure_is_recorded_not_missing():
    value, expected = example()
    original = value["original_receipt"]
    original.update(agent_stages=[], functional_lifecycle_pass=False, status="fail_closed", evidence_valid=False,
                    failure={"phase": "import", "type": "ImportError", "message": "missing helper"})
    value["functional_lifecycle_pass"] = False
    value["evidence_valid"] = False
    result = validate(reseal(value), expected)
    assert result["recorded"] and not result["functional_lifecycle_pass"]
    assert result["agent_stages_recorded"] == 0


@pytest.mark.parametrize("mutation", ["hash", "size", "base64", "exit", "truncation", "timeout", "bool_exit"])
def test_raw_transport_tampering_and_incomplete_rejected(mutation):
    value = stage(1)
    raw = value["jsonl"]
    if mutation == "hash":
        raw["stdout_sha256"] = "0" * 64
    elif mutation == "size":
        raw["stdout_bytes"] += 1
    elif mutation == "base64":
        raw["stdout_base64"] = "invalid!"
    elif mutation == "exit":
        raw["returncode"] = 1
    elif mutation == "bool_exit":
        raw["returncode"] = False
    elif mutation == "timeout":
        raw["timed_out"] = True
    else:
        raw["stdout_truncated"] = True
    with pytest.raises(ValueError):
        b.analyze_stage(value, 1, "/synthetic")


@pytest.mark.parametrize("mutation", ["extra_call", "shell", "wrong_root", "result_conflict", "marker", "saved_analysis", "prompt"])
def test_raw_event_and_saved_analysis_disagreements_rejected(mutation):
    value = stage(1)
    events = [json.loads(line) for line in b.decoded_stream(value["jsonl"])["stdout"].splitlines()]
    if mutation == "extra_call":
        duplicate = deepcopy(events[2]); duplicate["item"]["id"] = "second"; events.insert(3, duplicate)
    elif mutation == "shell":
        events.insert(3, {"type": "item.completed", "item": {"id": "shell", "type": "command_execution"}})
    elif mutation == "wrong_root":
        events[2]["item"]["arguments"]["root"] = "/other"
    elif mutation == "result_conflict":
        events[3]["item"]["result"]["structured_content"]["exit_code"] = 1
    elif mutation == "marker":
        events[4]["item"]["text"] = "other"
    elif mutation == "saved_analysis":
        value["analysis"]["retry_count"] = 1
    else:
        value["prompt"] = "changed"
    value["jsonl"] = encoded(events)
    with pytest.raises(ValueError):
        b.analyze_stage(value, 1, "/synthetic")


def test_missing_live_is_only_preview(tmp_path):
    assert b.validate_live(tmp_path, preview=True)["recorded"] is False
    with pytest.raises(ValueError, match="recorded live receipt"):
        b.validate_live(tmp_path)


def test_real_client_evidence_reconciles_without_execution():
    result = b.validate_client(b.ROOT)
    assert (result["scripted_passed"], result["live_checks_passed"], result["installed_stdio_requests"]) == (14, 11, 8)
    assert result["raw_response_checks_recomputed"] == 8
    assert result["producer_attested_fixture_checks"] == 3
    assert result["scenario_category_count"] == 10
    assert len(result["prior_failed_attempts"]) == 2
    assert result["positive_mcp_execution_tested"] is False and result["model_called"] is False


@pytest.mark.parametrize("mutation", ["raw_hash", "response", "plan", "check", "new_check"])
def test_client_transport_tamper(mutation):
    module = b.exact_module(b.ROOT, "research.softwarex.client_conformance", b.HERE + "/client_conformance.py")
    transport, live, plan = [b.read(b.ROOT, b.CLIENT_ATTEMPT + "/" + name) for name in
                             ("live-transport.json", "live-analysis.json", "live-request-plan.json")]
    if mutation == "raw_hash":
        transport["stdout_sha256"] = "0" * 64
    elif mutation == "response":
        live["responses"].pop()
    elif mutation == "plan":
        plan["requests"].pop()
    elif mutation == "check":
        live["checks"]["unconfigured_execution_refused"] = False
    else:
        live["checks"]["invented"] = True
    with pytest.raises(ValueError):
        b.validate_client_transport(module, transport, live, plan)


def test_changed_fixed_client_receipt_rejected(monkeypatch):
    original = b.record
    monkeypatch.setattr(b, "record", lambda root, path: {**original(root, path), "sha256": "0" * 64})
    with pytest.raises(ValueError, match="fixed client receipt"):
        b.validate_client(b.ROOT)


def test_cli_check_is_read_only_and_output_create_only(tmp_path, monkeypatch):
    value = {"completed": True, "preview": False, "bounded_live_client": {"recorded": True, "functional_lifecycle_pass": False}}
    monkeypatch.setattr(b, "build", lambda **kwargs: deepcopy(value))
    target = tmp_path / "extension.json"
    assert b.main(["--output", str(target)]) == 0
    before = target.stat().st_mtime_ns
    assert b.main(["--output", str(target), "--check"]) == 0
    assert target.stat().st_mtime_ns == before
    with pytest.raises(FileExistsError):
        b.main(["--output", str(target)])
    value["completed"] = False
    with pytest.raises(ValueError, match="saved extension evidence"):
        b.main(["--output", str(target), "--check"])


def test_preview_cannot_masquerade_as_final():
    with pytest.raises(ValueError, match="explicit separate"):
        b.main(["--preview"])
    with pytest.raises(ValueError, match="final output"):
        b.main(["--preview", "--output", str(b.ROOT / b.OUTPUT)])


def test_actual_live_refusal_remains_adverse_with_independent_raw_explanation():
    result = b.validate_live(b.ROOT)
    assert result["recorded"] and not result["functional_lifecycle_pass"]
    assert result["installed_core_inventory_validated"]
    assert result["agent_stages_recorded"] == 1
    assert result["independently_validated_stages"] == []
    assert result["failure"]["message"] == "Codex final message did not exactly match the stage marker"
    raw = result["stage_adverse_outcomes"][0]["raw_diagnostics"]
    assert raw["classification"] == "untrusted_doctor_readiness_refusal"
    assert raw["counts_reconciled"] and (raw["mcp_calls"], raw["run_tests_calls"]) == (1, 0)
    assert raw["agent_message_count"] == 2 and not raw["exact_marker_only"]
    assert raw["manifest_authorized"] is False and raw["task_status"] == "UNTRUSTED"
    assert result["recorded_codex_version"] == "0.153.3" and result["recorded_host_system"] == "Linux"


@pytest.mark.parametrize("mutation", ["structured_disagreement", "extra_tool", "incomplete", "wrong_reason", "authorized"])
def test_adverse_diagnostic_does_not_invent_refusal(mutation):
    original = b.read(b.ROOT, b.LIVE_BASE + "/receipt.json")["original_receipt"]
    value = original["agent_stages"][0]
    events = [json.loads(line) for line in b.decoded_stream(value["jsonl"])["stdout"].splitlines()]
    ended = next(event["item"] for event in events if event["type"] == "item.completed" and event["item"].get("type") == "mcp_tool_call")
    if mutation == "structured_disagreement":
        ended["result"]["content"][0]["text"] = "{}"
    elif mutation == "extra_tool":
        events.insert(-1, {"type": "item.completed", "item": {"id": "shell", "type": "command_execution"}})
    elif mutation == "incomplete":
        events.pop()
    elif mutation == "wrong_reason":
        ended["result"]["structured_content"]["tasks"][0]["reason"] = "unknown"
        ended["result"]["content"][0]["text"] = json.dumps(ended["result"]["structured_content"])
    else:
        ended["result"]["structured_content"]["manifest_authorized"] = True
        ended["result"]["content"][0]["text"] = json.dumps(ended["result"]["structured_content"])
    value["jsonl"] = encoded(events)
    actual = b.describe_adverse_stage(value, 0, original["synthetic_repository"]["path"])
    assert actual["classification"] == "unclassified_failed_stage"


def test_adverse_source_inventory_is_not_exempt_from_binding():
    value = b.read(b.ROOT, b.LIVE_BASE + "/receipt.json")
    value["original_receipt"]["zerorun"]["installed_package"]["module"]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="exact installed core"):
        b.validate_live_envelope(reseal(value), expected=b.expected_core(b.ROOT),
            adapter_sha=value["research_public_layout_adapter"]["adapter_sha256"],
            protocol_sha=b.LIVE_PROTOCOL_SHA, manifest_sha=b.LIVE_MANIFEST_SHA)


def test_saved_operating_region_tampering_rejected_before_downstream(monkeypatch):
    operating = {"completed": True, "value": "computed"}
    monkeypatch.setattr(b.op, "analyze", lambda root: operating)
    monkeypatch.setattr(b, "read", lambda root, name: {**operating, "value": "changed"})
    with pytest.raises(ValueError, match="saved operating-region"):
        b.build(preview=True)


def test_missing_diagnostic_cannot_be_implicitly_omitted(tmp_path):
    assert b.validate_non_model(tmp_path, preview=True)["recorded"] is False
    with pytest.raises(ValueError, match="separately planned non-model"):
        b.validate_non_model(tmp_path)


def diagnostic_fixture(tmp_path, monkeypatch):
    path = tmp_path / b.DIAGNOSTIC_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fixture exists")
    value = {"passed": False, "model_called": False, "codex_correction_tested": False, "real_repository_authorized": False,
             "helper_sha256": "1" * 64, "protocol_sha256": b.DIAGNOSTIC_PROTOCOL_SHA, "stages": [],
             "original_live_receipt_sha256": "2" * 64, "failure": {"type": "FixtureError", "message": "pre-model diagnostic failed"}}
    def file_record(root, name):
        digest = b.DIAGNOSTIC_PROTOCOL_SHA if name.endswith("NON_MODEL_DIAGNOSTIC_PROTOCOL.md") else "1" * 64
        return {"path": name, "bytes": 1, "sha256": digest}
    module = SimpleNamespace(adapter=SimpleNamespace(__file__=tmp_path / b.HERE / "run_public_lifecycle.py"),
        validate_saved_receipt=lambda *_: {"passed": value["passed"]},
        validate_exchange=lambda *_: (_ for _ in ()).throw(ValueError("fixture exchange failed")))
    monkeypatch.setattr(b, "read", lambda *_: deepcopy(seal(value)))
    monkeypatch.setattr(b, "record", file_record)
    monkeypatch.setattr(b, "exact_module", lambda *_: module)
    monkeypatch.setattr(b, "expected_core", lambda *_: example()[1])
    return value


def test_adverse_nonmodel_is_separate_record_not_live_correction(tmp_path, monkeypatch):
    diagnostic_fixture(tmp_path, monkeypatch)
    result = b.validate_non_model(tmp_path)
    assert result["recorded"] and not result["passed"]
    assert not result["model_called"] and not result["codex_correction_tested"]
    assert result["stages_recorded"] == 0 and result["negative_control_passed"] is False


@pytest.mark.parametrize("field,value", [("helper_sha256", "0" * 64), ("protocol_sha256", "0" * 64),
    ("model_called", True), ("codex_correction_tested", True), ("real_repository_authorized", True), ("passed", 1)])
def test_nonmodel_scope_binding_mutations_rejected(tmp_path, monkeypatch, field, value):
    receipt = diagnostic_fixture(tmp_path, monkeypatch)
    receipt[field] = value
    with pytest.raises(ValueError):
        b.validate_non_model(tmp_path)


def test_adverse_diagnostic_raw_hash_is_not_excused(tmp_path, monkeypatch):
    receipt = diagnostic_fixture(tmp_path, monkeypatch)
    live, _ = example()
    receipt.update(source_package=live["original_receipt"]["source"]["package"],
                   installed_before=live["original_receipt"]["zerorun"]["installed_package"],
                   source_before={"head": b.LIVE_PUBLIC_COMMIT})
    streams = stage(0)["jsonl"]
    streams["stdout_sha256"] = "0" * 64
    receipt["stages"] = [{"name": "negative_doctor", "streams": streams, "explicit_trust_root": False}]
    with pytest.raises(ValueError, match="raw stream hash"):
        b.validate_non_model(tmp_path)


@pytest.mark.parametrize("raw", [b"", b'{}\n', b'{"type":"unknown"}\n', b'{"type":"thread.started"}',
    b'{"type":"thread.started"}\n\n', b'{"type":"thread.started","type":"turn.started"}\n'])
def test_event_parser_preserves_frozen_strictness(raw):
    with pytest.raises(ValueError):
        b.parse_live_events(raw)


def test_actual_nonmodel_diagnostic_proves_only_separate_stdio_intervention():
    result = b.validate_non_model(b.ROOT)
    assert result["recorded"] and result["passed"]
    assert result["stages_recorded"] == result["stages_validated"] == 5
    assert result["negative_control_passed"]
    assert [row["task_status"] for row in result["stages"]] == ["UNTRUSTED", "CACHEABLE", "MISS_EXECUTED", "HIT_REUSED", "VERIFY_MATCH"]
    assert sum(row["responses"] for row in result["stages"]) == 10
    assert result["model_called"] is False and result["codex_correction_tested"] is False


@pytest.mark.parametrize("mutation", ["environment", "authority_location", "cleanup", "fixture", "command", "authorization_raw", "source_inventory"])
def test_nonmodel_additional_binding_checks_reject_forged_pass(monkeypatch, mutation):
    original_read = b.read
    receipt = original_read(b.ROOT, b.DIAGNOSTIC_PATH)
    if mutation == "environment":
        receipt["stages"][0]["recorded_environment_intervention"]["ZERORUN_TRUST_ROOT"] = receipt["synthetic"]["external_authority_path"]
    elif mutation == "authority_location":
        receipt["synthetic"]["external_authority_path"] = receipt["synthetic"]["path"] + "/authority"
    elif mutation == "cleanup":
        receipt["temporary_fixture_and_authority_cleaned"] = False
    elif mutation == "fixture":
        receipt["synthetic"]["runtime_image"] = "other"
    elif mutation == "command":
        receipt["stages"][0]["command"].append("unexpected")
    elif mutation == "authorization_raw":
        receipt["synthetic"]["authorization_streams"]["stdout_sha256"] = "0" * 64
    else:
        receipt["source_package"]["files"][0]["sha256"] = "0" * 64
    seal(receipt)
    monkeypatch.setattr(b, "read", lambda root, name: deepcopy(receipt) if name == b.DIAGNOSTIC_PATH else original_read(root, name))
    with pytest.raises(ValueError):
        b.validate_non_model(b.ROOT)
