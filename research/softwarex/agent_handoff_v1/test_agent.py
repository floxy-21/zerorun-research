from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as c
spec = importlib.util.spec_from_file_location("producer_validator", HERE / "validate.py")
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def ledger():
    return {"schema": "zerorun.handoff-selection.v1", "phase": "main",
            "cases": [{"case_id": f"case-{repo}-{case}", "repo": f"owner/repo-{repo}", "base_commit": "a" * 40}
                      for repo in range(10) for case in range(3)]}


def events(item_type="agent_message", usage=None):
    data = [{"type": "thread.started", "thread_id": "test"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "i", "type": item_type, "text": "Actual validation unavailable."}},
            {"type": "turn.completed", "usage": usage if usage is not None else {"input_tokens": 10, "output_tokens": 5}}]
    return b"\n".join(json.dumps(row).encode() for row in data)


def file_row(path, content):
    return {"path": path, "kind": "file", "bytes": len(content), "sha256": c.sha(content)}


def test_fixed_six_cases_one_per_first_six_repos_not_outcome_selection():
    before = ledger()
    selection = c.selected(before)
    assert len(selection) == 6
    assert [row["repo"] for row in selection] == [f"owner/repo-{i}" for i in range(6)]
    for row in before["cases"]:
        row.update(disposition="UNAVAILABLE_ACQUISITION", error="unavailable", solved=False)
    assert [row["case_id"] for row in c.selected(before)] == [row["case_id"] for row in selection]


def test_pilot_separate_and_insufficient_main_not_forced():
    data = ledger()
    data["cases"] = data["cases"][:2]
    with pytest.raises(ValueError):
        c.selected(data)
    data["phase"] = "pilot"
    assert c.selected(data) == data["cases"]


def test_command_preserves_normal_workspace_permissions_without_mcp_or_network():
    command = c.command("/tools/codex", "/work/project", "/tools/python")
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--model") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="medium"' in command
    assert 'approval_policy="never"' in command
    assert "mcp_servers={}" in command
    assert "sandbox_workspace_write.network_access=false" in command
    assert "--ignore-user-config" in command and "--ignore-rules" in command and "--strict-config" in command
    assert "shell_tool" not in command and "unified_exec" not in command
    assert "danger-full-access" not in command


def test_prompt_supplies_task_and_fresh_tests_without_solution_or_cache_authority():
    prompt = c.prompt("Fix the reported bug.", ["tests/test_bug.py"], "/venv/bin/python")
    assert "Fix the reported bug." in prompt
    assert "/venv/bin/python -B -m pytest -p no:cacheprovider tests/test_bug.py" in prompt
    assert "reference source fix is not supplied" in prompt
    assert "Do not commit or create branches" in prompt
    assert "do not install anything" in prompt


@pytest.mark.parametrize("path", ["tests/test_x.py", "test_x.py", "pkg/foo_test.py", "conftest.py", "setup.py", "pyproject.toml", ".zerorun.json", ".codex/config.toml", "docs/conf.py", "test_helpers/helper.py"])
def test_protected_paths_are_not_source_edits(path):
    before = [file_row(path, b"old")]
    after = [file_row(path, b"new")]
    result = c.changes(before, after, ["test_helpers/helper.py"])
    assert result["source_scope_pass"] is False


def test_production_change_and_new_source_are_allowed_but_empty_directory_is_not():
    before = [file_row("pkg/main.py", b"old")]
    after = [file_row("pkg/main.py", b"new"), file_row("pkg/new.py", b"new")]
    assert c.changes(before, after, [])["source_scope_pass"] is True
    after.append({"path": "empty-output", "kind": "directory"})
    assert c.changes(before, after, [])["source_scope_pass"] is False


def test_complete_events_record_requested_model_not_inferred_backend():
    result = c.analyze_events(events(), 0, False, False)
    assert result["cli_completed"] is True
    assert result["requested_model"] == "gpt-6-astra"
    assert result["observed_model_identifiers"] == []
    assert result["usage"]["output_tokens"] == 5


@pytest.mark.parametrize("item", ["mcp_tool_call", "web_search", "collab_tool_call", "unknown_tool"])
def test_prohibited_tool_events_fail_boundary(item):
    result = c.analyze_events(events(item), 0, False, False)
    assert result["boundary_pass"] is False and result["cli_completed"] is False


@pytest.mark.parametrize("usage", [{}, {"input_tokens": True, "output_tokens": 1}, {"input_tokens": -1, "output_tokens": 1}])
def test_incomplete_usage_never_accepted(usage):
    assert c.analyze_events(events(usage=usage), 0, False, False)["cli_completed"] is False


@pytest.mark.parametrize("code, timed, truncated", [(1, False, False), (0, True, False), (0, False, True)])
def test_timeout_failure_and_truncation_preserved_as_incomplete(code, timed, truncated):
    assert c.analyze_events(events(), code, timed, truncated)["cli_completed"] is False


def test_final_source_archive_is_read_without_extraction():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        item = tarfile.TarInfo("agent-final/pkg/main.py")
        item.size = 3
        archive.addfile(item, io.BytesIO(b"new"))
    assert v.source_archive_inventory(raw.getvalue()) == [file_row("pkg/main.py", b"new")]


def test_final_source_archive_rejects_link():
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        item = tarfile.TarInfo("agent-final/link")
        item.type = tarfile.SYMTYPE
        item.linkname = "/etc/passwd"
        archive.addfile(item)
    with pytest.raises(ValueError, match="special member"):
        v.source_archive_inventory(raw.getvalue())


def complete_receipt(tmp_path):
    """Synthetic transport evidence only; never represents an actual model call."""
    case_dir = tmp_path / "case-00"
    case_dir.mkdir()
    def store(name, value, directory=case_dir):
        raw = value if isinstance(value, bytes) else c.raw_json(value)
        (directory / name).write_bytes(raw)
        return {"path": name, "bytes": len(raw), "sha256": c.sha(raw)}
    selected = {"case_id": "sample-1", "repo": "owner/sample", "base_commit": "a" * 40,
                "targets": ["tests/test_bug.py"]}
    row = {"instance_id": "sample-1", "repo": "owner/sample", "base_commit": "a" * 40,
           "problem_statement": "Fix the bug.", "test_patch": "public test patch", "patch": "withheld source fix"}
    metadata = store("metadata-hf.json", {"partial": False, "rows": [{"row": row, "truncated_cells": []}]})
    selected["metadata"] = metadata
    client = {"node": {"path": "/tools/node", "sha256": c.NODE_SHA},
              "native": {"path": "/tools/native", "sha256": c.NATIVE_SHA},
              "launcher": {"path": "/tools/codex", "sha256": c.LAUNCHER_SHA},
              "test_python": {"path": "/tools/python", "sha256": "0" * 64}}
    before, after = [file_row("pkg/main.py", b"old")], [file_row("pkg/main.py", b"new")]
    prompt = c.prompt(row["problem_statement"], selected["targets"], "/tools/python").encode()
    prep = {"case_id": "sample-1", "repo": "owner/sample", "ready": True, "error": None,
            "workspace": "/original/study/case-00/workspace", "public_issue": row["problem_statement"],
            "metadata": metadata, "targets": selected["targets"], "test_paths": selected["targets"],
            "reference_patch_applied": False, "reference_patch_sha256": c.sha(row["patch"].encode()),
            "prompt": store("prompt.txt", prompt), "test_patch": store("provided-tests.patch", row["test_patch"].encode()),
            "before": before, "git_before": []}
    prep["command"] = c.command("/tools/codex", prep["workspace"], "/tools/python")
    prep_binding = store("preparation.json", prep)
    frozen = {"schema": "zerorun.agent-producer-freeze.v1", "cases": [selected, {**selected, "case_id": "sample-2"}],
              "model_requested": c.MODEL, "reasoning_effort": c.EFFORT, "timeout_seconds": c.SECONDS,
              "output_limit_bytes": c.OUTPUT_LIMIT, "reference_patch_supplied_to_model": False,
              "mcp_authority_created": False, "preparations": [prep_binding], "client": client,
              "study_sources": {name: {"sha256": c.sha((HERE / name).read_bytes())}
                                for name in ("common.py", "run.py", "validate.py", "PROTOCOL.md")}}
    frozen["ledger"] = {"schema": "zerorun.handoff-selection.v1", "phase": "pilot", "cases": frozen["cases"]}
    freeze_binding = store("freeze.json", frozen, tmp_path)
    started = {"case_index": 0, "case_id": "sample-1", "freeze_sha256": freeze_binding["sha256"], "started_utc": "synthetic"}
    store("started.json", started)
    source = io.BytesIO()
    with tarfile.open(fileobj=source, mode="w:gz") as archive:
        item = tarfile.TarInfo("agent-final/pkg/main.py")
        item.size = 3
        archive.addfile(item, io.BytesIO(b"new"))
    session = {**started, "model_invocation_attempted": True, "boundary_pass": True, "error": None,
               "client_version": "codex-cli 0.153.3", "command": prep["command"], "prompt_sha256": c.sha(prompt),
               "stdout": store("events.log", events()), "stderr": store("stderr.log", b""),
               "returncode": 0, "timed_out": False, "truncated": False,
               "analysis": c.analyze_events(events(), 0, False, False), "after": after, "git_after": [],
               "scope": c.changes(before, after, selected["targets"]),
               "tracked_patch": store("proposed-tracked.patch", b"synthetic diff"),
               "final_source": store("final-source.tar.gz", source.getvalue()), "client_after": client}
    store("session.json", session)
    return case_dir, session, store


def test_complete_receipt_recomputes_and_never_infers_correctness(tmp_path):
    case_dir, _, _ = complete_receipt(tmp_path)
    result = v.validate_receipt(case_dir / "session.json")
    assert result["producer_completed"] and result["boundary_pass"] and result["source_patch_present"]
    assert result["patch_correctness_established"] is False and result["fresh_oracle_required"] is True


@pytest.mark.parametrize("field,value", [("command", ["unsafe"]), ("boundary_pass", False), ("analysis", {}), ("after", [])])
def test_receipt_rejects_reclassified_or_changed_evidence(tmp_path, field, value):
    case_dir, session, store = complete_receipt(tmp_path)
    session[field] = value
    store("session.json", session)
    with pytest.raises(ValueError):
        v.validate_receipt(case_dir / "session.json")


def test_frozen_preparation_cannot_be_edited_to_change_prompt_or_test_scope(tmp_path):
    case_dir, _, store = complete_receipt(tmp_path)
    prep = c.strict((case_dir / "preparation.json").read_bytes())
    prep["test_paths"] = []
    store("preparation.json", prep)
    with pytest.raises(ValueError, match="preparation receipt"):
        v.validate_receipt(case_dir / "session.json")


def test_launch_failure_retained_as_incomplete_and_stops_campaign(tmp_path):
    case_dir, session, store = complete_receipt(tmp_path)
    del session["stdout"]
    session.update(error="OSError: synthetic launch failure", boundary_pass=False)
    store("session.json", session)
    result = v.validate_receipt(case_dir / "session.json")
    assert result["capture_incomplete"] and not result["producer_completed"] and not result["boundary_pass"]


def test_changed_git_retains_source_without_executing_git_diff(tmp_path):
    case_dir, session, store = complete_receipt(tmp_path)
    del session["tracked_patch"]
    session.update(git_after=[file_row("config", b"changed")], boundary_pass=False,
                   tracked_patch_unavailable="Protected Git metadata changed; no post-model Git command executed.")
    store("session.json", session)
    result = v.validate_receipt(case_dir / "session.json")
    assert result["producer_completed"] and not result["boundary_pass"]
