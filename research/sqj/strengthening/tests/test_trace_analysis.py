"""Hostile and incomplete traces must never become asserted cache hits."""
from copy import deepcopy
import hashlib
import json

import pytest

from research.sqj.strengthening.trace_analysis import (
    InvalidEvidence, analyze_collection, analyze_episode, observation_evidence,
    recognize_command, strict_json,
)


def pair(identifier, name="execute_bash", arguments=None, observation=None):
    if arguments is None:
        arguments = {"command": "python -m pytest tests/ -q"}
    if observation is None:
        observation = "2 passed in 0.01s\n[The command completed with exit code 0.]\n[Command finished with exit code 0]"
    return [
        {"role": "assistant", "content": "recorded", "tool_calls": [{"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
        {"role": "tool", "name": name, "tool_call_id": identifier, "content": observation},
    ]


def episode(messages=None):
    return {"trajectory_id": "trace-1", "instance_id": "owner__repo-1", "repo": "owner/repo", "trajectory": pair("one") if messages is None else messages, "resolved": 0, "exit_status": "submit"}


@pytest.mark.parametrize("command", ["pytest", "pytest tests/test_a.py -k 'one or two'", "python -m pytest tests/ -q", "python3.10 -m pytest tests/ -q", "cd /workspace/repo && python -m pytest tests/"])
def test_direct_signatures(command):
    assert recognize_command(command)["supported"]


@pytest.mark.parametrize("command", [
    "pytest; touch /tmp/poison", "pytest && echo yes", "cd /tmp && pytest && whoami",
    "pytest | tail -10", "pytest > result.txt", "pytest < commands", "pytest &",
    "pytest $(touch /tmp/poison)", "pytest `whoami`", "pytest\nwhoami",
    "pytest *.py", "pytest tests/test_a.py::test[one]", "X=1 pytest", "env X=1 pytest",
    "python -c 'import pytest; pytest.main()'", "python reproduce.py", "pytest --collect-only",
    "pytest --co", "pytest --help", "python -m pytest --version", "pytest -", "pytest # hidden", "pytest (touch /tmp/poison)",
])
def test_unrecognized_or_complex_commands_never_supported(command):
    assert not recognize_command(command)["supported"]


def test_pipeline_status_is_not_pytest_success():
    content = "1 failed, 38 passed, 2 skipped in 0.12s\n[The command completed with exit code 0.]\n[Command finished with exit code 0]"
    result = analyze_episode(episode(pair("one", arguments={"command": "pytest | tail -10"}, observation=content)), 7)
    assert result["totals"]["direct_pytest_calls"] == 0
    assert result["totals"]["pytest_mention_calls"] == 1
    assert result["events"][0]["recorded_shell_exit_code"] == 0
    assert result["events"][0]["recorded_pytest_summary"]["failed"] == 1
    assert result["totals"]["shell_zero_with_failed_or_error_summary"] == 1


def test_failed_edit_is_explicit_barrier_between_exact_repeat():
    messages = pair("one") + pair("edit", "str_replace_editor", {"command": "str_replace", "path": "/repo/a.py", "old_str": "x", "new_str": "y"}, "ERROR:\nNo replacement performed") + pair("two")
    result = analyze_episode(episode(messages), 4)
    assert result["totals"]["editor_failed_attempt"] == 1
    repeat = result["candidate_repeat_pairs"][0]
    assert repeat["intervening_barrier_counts"] == {"editor_failed_attempt": 1}
    assert not repeat["source_unchanged_established"]
    assert not repeat["cache_hit_established"]


def test_no_intervening_action_still_not_a_cache_hit():
    result = analyze_episode(episode(pair("one") + pair("two")), 0)
    repeat = result["candidate_repeat_pairs"][0]
    assert repeat["intervening_barrier_counts"] == {}
    assert not repeat["source_unchanged_established"]
    assert "unmeasured" in repeat["prior_execution_effects"]


def test_exact_command_distinctions_and_current_directory_preserved():
    commands = ["pytest tests/", "pytest tests/ -q", "cd /one && pytest tests/", "cd /two && pytest tests/", "pytest  tests/", "pytest tests/"]
    messages = sum((pair(str(i), arguments={"command": command}) for i, command in enumerate(commands)), [])
    result = analyze_episode(episode(messages), 0)
    assert result["totals"]["exact_command_repeat_pairs"] == 1
    assert result["totals"]["distinct_direct_pytest_commands"] == 5
    assert result["candidate_repeat_pairs"][0]["intervening_barrier_counts"] == {"test_execution_effects_unmeasured": 4}


@pytest.mark.parametrize("mutate,reason", [
    (lambda m: m.pop(), "unmatched_call_or_observation"),
    (lambda m: m.append(deepcopy(m[1])), "duplicate_observation_id"),
    (lambda m: m.extend(deepcopy(m)), "duplicate_call_id"),
    (lambda m: m.reverse(), "observation_precedes_call"),
    (lambda m: m[1].update(tool_call_id="other"), "unmatched_call_or_observation"),
    (lambda m: m[1].update(name="other"), "observation_name_mismatch"),
])
def test_ambiguous_pairing_is_rejected(mutate, reason):
    messages = pair("one")
    mutate(messages)
    with pytest.raises(InvalidEvidence, match=reason):
        analyze_episode(episode(messages), 0)


def test_pairing_by_id_not_adjacent_position():
    first, second = pair("one"), pair("two", arguments={"command": "pytest other/"})
    messages = [first[0], second[0], second[1], first[1]]
    result = analyze_episode(episode(messages), 0)
    assert result["totals"]["direct_pytest_calls"] == 2


@pytest.mark.parametrize("content", ['{"a": 1, "a": 2}', '{"a": NaN}', '{"a": Infinity}', '{"a": 1e999}'])
def test_strict_json_rejects_ambiguity(content):
    with pytest.raises(InvalidEvidence):
        strict_json(content)


def test_recorded_zero_pass_case_is_not_dropped_or_called_success():
    messages = pair("one", observation="1 error in 0.1s\n[Command finished with exit code 2]")
    result = analyze_episode(episode(messages), 0)
    assert result["recorded_resolved"] == 0
    assert result["totals"]["direct_pytest_calls"] == 1
    assert result["totals"]["direct_pytest_with_recorded_exit_zero"] == 0
    assert result["totals"]["direct_pytest_with_nonzero_pass_count"] == 0
    assert result["totals"]["direct_pytest_without_positive_pass_summary"] == 1


def test_conflicting_exit_markers_not_silently_resolved():
    evidence = observation_evidence("[The command completed with exit code 2.]\n[Command finished with exit code 0]")
    assert evidence["ambiguous_exit_markers"]
    assert evidence["recorded_shell_exit_code"] is None


def test_actual_openhands_observation_clipping_marker():
    evidence = observation_evidence("first lines\n[... Observation truncated due to length ...]\n[Command finished with exit code 0]")
    assert evidence["observation_clipped"]
    assert evidence["recorded_shell_exit_code"] == 0


def test_editor_attempt_and_unknown_shell_are_barriers():
    messages = pair("one") + pair("view", "str_replace_editor", {"command": "view", "path": "/repo/a.py"}, "lines") + pair("shell", arguments={"command": "ls"}) + pair("edit", "str_replace_editor", {"command": "create", "path": "/repo/x"}, "unexpected") + pair("two")
    result = analyze_episode(episode(messages), 0)
    assert result["candidate_repeat_pairs"][0]["intervening_barrier_counts"] == {"bash_effects_unknown": 1, "editor_outcome_unknown": 1}


def test_interactive_input_and_unknown_bash_flags_unsupported():
    messages = pair("one", arguments={"command": "pytest", "is_input": True}) + pair("two", arguments={"command": "pytest", "env": {"X": "1"}})
    result = analyze_episode(episode(messages), 0)
    assert result["totals"]["direct_pytest_calls"] == 0
    assert result["unsupported_bash_reasons"] == {"interactive_stdin_input": 1, "unknown_bash_arguments": 1}


def write_collection(tmp_path, rows=None):
    if rows is None:
        rows = [{"row_idx": 10, "truncated_cells": [], "row": episode()}]
    entries = []
    for wrapped in rows:
        index = wrapped["row_idx"]
        relative = f"rows/row-{index:05d}.json"
        file = tmp_path / relative
        file.parent.mkdir(exist_ok=True)
        raw = json.dumps({"rows": [wrapped], "partial": False, "num_rows_total": 67074}).encode()
        file.write_bytes(raw)
        entries.append({"row_index": index, "path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "status": "ok"})
    rewrite_manifest(tmp_path, entries)
    return entries


def rewrite_manifest(tmp_path, entries, *, update_selection=True):
    selection = {"selected_indices": [entry["row_index"] for entry in entries], "population_rows": 67074}
    if update_selection:
        (tmp_path / "selection.json").write_text(json.dumps(selection), encoding="utf-8")
    else:
        selection = json.loads((tmp_path / "selection.json").read_text(encoding="utf-8"))
    (tmp_path / "collection.json").write_text(json.dumps(selection | {"entries": entries}), encoding="utf-8")


def test_verified_raw_collection_aggregation(tmp_path):
    write_collection(tmp_path)
    result = analyze_collection(tmp_path)
    assert result["selected_rows"] == result["valid_episodes"] == 1
    assert result["repos"]["owner/repo"]["episodes"] == 1


@pytest.mark.parametrize("kind,reason", [("hash", "raw_sha256_mismatch"), ("size", "raw_byte_length_mismatch"), ("index", "row_index_mismatch"), ("path", "manifest_path_outside_raw_directory")])
def test_invalid_manifest_coverage_explicit(tmp_path, kind, reason):
    entries = write_collection(tmp_path)
    if kind == "hash": entries[0]["sha256"] = "0" * 64
    if kind == "size": entries[0]["bytes"] += 1
    if kind == "index": entries[0]["row_index"] = 11
    if kind == "path": entries[0]["path"] = "../not-allowed.json"
    rewrite_manifest(tmp_path, entries)
    result = analyze_collection(tmp_path)
    assert result["valid_episodes"] == 0
    assert result["invalid_coverage_reasons"] == {reason: 1}


def test_hf_truncation_unavailable_not_an_empty_trace(tmp_path):
    write_collection(tmp_path, [{"row_idx": 10, "truncated_cells": ["trajectory"], "row": episode()}])
    result = analyze_collection(tmp_path)
    assert result["invalid_coverage_reasons"] == {"hf_truncated_cells": 1}
    assert result["selected_rows"] == 1


def test_duplicate_episode_identity_counted_unavailable(tmp_path):
    write_collection(tmp_path, [{"row_idx": index, "truncated_cells": [], "row": episode()} for index in (10, 11)])
    result = analyze_collection(tmp_path)
    assert result["valid_episodes"] == 1
    assert result["invalid_coverage_reasons"] == {"duplicate_trajectory_id": 1}


def test_duplicate_manifest_indices_rejected(tmp_path):
    entries = write_collection(tmp_path)
    rewrite_manifest(tmp_path, entries * 2, update_selection=False)
    with pytest.raises(InvalidEvidence, match="duplicate_manifest_row_index"):
        analyze_collection(tmp_path)


def test_unavailable_acquisition_stays_in_denominator(tmp_path):
    entries = write_collection(tmp_path)
    entries.append({"row_index": 11, "status": "unavailable"})
    rewrite_manifest(tmp_path, entries)
    result = analyze_collection(tmp_path)
    assert result["selected_rows"] == 2
    assert result["valid_episodes"] == 1


def test_duplicate_json_key_inside_arguments_rejected():
    messages = pair("one")
    messages[0]["tool_calls"][0]["function"]["arguments"] = '{"command":"pytest", "command":"touch /tmp/poison"}'
    with pytest.raises(InvalidEvidence, match="duplicate_json_key"):
        analyze_episode(episode(messages), 0)


def test_terminal_finish_without_observation_is_explicitly_counted():
    messages = pair("one") + pair("finish", "finish", {"message": "done"})[:1]
    result = analyze_episode(episode(messages), 0)
    assert result["totals"]["terminal_finish_without_observation"] == 1
    assert result["totals"]["direct_pytest_calls"] == 1


def test_nonterminal_unmatched_finish_remains_invalid():
    messages = pair("finish", "finish", {"message": "done"})[:1] + pair("one")
    with pytest.raises(InvalidEvidence, match="unmatched"):
        analyze_episode(episode(messages), 0)


@pytest.mark.parametrize("value", [[], {}, 1, "true"])
def test_malformed_stdin_flag_never_crashes_or_becomes_supported(value):
    result = analyze_episode(episode(pair("one", arguments={"command": "pytest", "is_input": value})), 0)
    assert result["totals"]["direct_pytest_calls"] == 0


def test_malformed_editor_operation_is_unknown_barrier():
    result = analyze_episode(episode(pair("one", "str_replace_editor", {"command": [], "path": "/repo/a"})), 0)
    assert result["totals"]["editor_unknown_operation"] == 1


@pytest.mark.parametrize("kind", ["omit", "extra"])
def test_selection_denominator_cannot_shrink_or_expand(tmp_path, kind):
    entries = write_collection(tmp_path)
    if kind == "omit":
        entries = []
    else:
        entries += [{"row_index": 11, "status": "unavailable"}]
    rewrite_manifest(tmp_path, entries, update_selection=False)
    with pytest.raises(InvalidEvidence, match="frozen_selection"):
        analyze_collection(tmp_path)


@pytest.mark.parametrize("field,value,reason", [("partial", True, "partial_or_unknown_response"), ("partial", None, "partial_or_unknown_response"), ("num_rows_total", 12, "response_population_mismatch")])
def test_response_partial_and_population_checked(tmp_path, field, value, reason):
    entries = write_collection(tmp_path)
    file = tmp_path / entries[0]["path"]
    response = json.loads(file.read_bytes())
    response[field] = value
    raw = json.dumps(response).encode()
    file.write_bytes(raw)
    entries[0].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    rewrite_manifest(tmp_path, entries)
    result = analyze_collection(tmp_path)
    assert result["invalid_coverage_reasons"] == {reason: 1}
