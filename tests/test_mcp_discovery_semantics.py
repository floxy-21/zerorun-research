"""Offline contract checks for discoverable task/status semantics.

Execution is stubbed at the existing task boundary: these tests check discovery,
argument dispatch, result serialization, and refusal, not container performance
or model comprehension. No test authorizes a real repository or calls a model.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from zerorun import mcp


def _tool_call(arguments):
    reply = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "run_tests", "arguments": arguments},
        }
    )
    result = reply["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result


@pytest.fixture
def configured_task(monkeypatch, tmp_path):
    task = SimpleNamespace(env=())
    manifest = SimpleNamespace(version=2, root=tmp_path, tasks={"tests": task})
    monkeypatch.setattr(mcp, "_SERVER_ROOT", None)
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda root: manifest)
    monkeypatch.setattr(mcp, "require_manifest_authority", lambda manifest: None)
    monkeypatch.setattr(mcp, "inspect_runtime", lambda *args, **kwargs: {})
    return manifest, task


def test_stdio_discovery_contains_complete_neutral_run_tests_reference(monkeypatch, tmp_path):
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    output = io.StringIO()
    monkeypatch.setattr(mcp, "_SERVER_ROOT", None)
    monkeypatch.setattr(mcp, "_required_repository_root", lambda root: tmp_path)
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    monkeypatch.setattr(mcp.sys, "stdout", output)
    monkeypatch.setattr(mcp, "run_task", lambda *args, **kwargs: pytest.fail("discovery executed tests"))

    assert mcp.serve_stdio() == 0
    rows = output.getvalue().splitlines()
    assert len(rows) == 1
    reply = json.loads(rows[0])
    assert reply["id"] == 1
    tools = {tool["name"]: tool for tool in reply["result"]["tools"]}
    assert set(tools) == set(mcp._TOOL_ARGUMENTS)
    tool = tools["run_tests"]
    schema = tool["inputSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["task"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"task", "root", "verify"}
    assert schema["properties"]["task"]["type"] == "string"
    assert schema["properties"]["root"]["type"] == "string"
    verify = schema["properties"]["verify"]
    assert verify["type"] == "boolean" and verify["default"] is False
    assert "False or omitted" in verify["description"]
    assert "fresh miss path" in verify["description"]
    assert "cannot select another repository" in schema["properties"]["root"]["description"]
    assert "not a shell command" in schema["properties"]["task"]["description"]
    description = tool["description"]
    for phrase in (
        "Only task, root, and verify", "HIT_REUSED", "MISS_EXECUTED", "VERIFY_MATCH",
        "MISS_FAILED", "exit_code", "isError", "mode=reuse alone",
        "does not replay stdout/stderr", "4000 captured bytes", "empty or truncated",
        "externally authorized exact v2 manifest", "does not authorize tasks",
        "protocol or argument error is not fresh test evidence",
    ):
        assert phrase in description
    assert "reuse_prior_success" not in description  # No coaching around a historical mistake.
    assert tool["annotations"] == {
        "title": "Run reviewed ZeroRun task",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }


@pytest.mark.parametrize("argument, expected", [({}, False), ({"verify": False}, False), ({"verify": True}, True)])
def test_omitted_and_explicit_verify_reach_existing_execution_boundary(
    monkeypatch, configured_task, argument, expected,
):
    manifest, task = configured_task
    seen = []

    def execute(actual_manifest, actual_task, *, verify, stdout, stderr):
        assert actual_manifest is manifest and actual_task is task
        seen.append(verify)
        status = "VERIFY_MATCH" if verify else "HIT_REUSED"
        return SimpleNamespace(exit_code=0, as_dict=lambda: {"status": status, "exit_code": 0})

    monkeypatch.setattr(mcp, "run_task", execute)
    result = _tool_call({"task": "tests", **argument})
    assert seen == [expected] and seen[0] is expected
    assert result["isError"] is False
    assert result["structuredContent"]["stdout_tail"] == ""
    assert result["structuredContent"]["stderr_tail"] == ""


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", "false", [], {}])
def test_verify_rejects_non_boolean_values_before_manifest_access(monkeypatch, invalid):
    monkeypatch.setattr(mcp, "_SERVER_ROOT", None)
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda root: pytest.fail("invalid argument reached manifest"))
    result = _tool_call({"task": "tests", "verify": invalid})
    assert result["isError"] is True
    assert result["structuredContent"] == {"status": "ERROR", "error": "verify must be a boolean"}


@pytest.mark.parametrize("unknown", ["force", "reuse_prior_success", "approve_setup", "command", "env"])
def test_unknown_arguments_remain_errors_without_execution(monkeypatch, unknown):
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda root: pytest.fail("unknown argument reached manifest"))
    monkeypatch.setattr(mcp, "run_task", lambda *args, **kwargs: pytest.fail("unknown argument executed"))
    result = _tool_call({"task": "tests", unknown: True})
    assert result["isError"] is True
    assert result["structuredContent"]["status"] == "ERROR"
    assert "unexpected arguments" in result["structuredContent"]["error"]


@pytest.mark.parametrize(
    "status, exit_code, verify, output",
    [
        ("HIT_REUSED", 0, False, b""),
        ("MISS_EXECUTED", 0, False, b"fresh validation\n"),
        ("MISS_EXECUTED", 0, True, b"fresh miss without stored success\n"),
        ("VERIFY_MATCH", 0, True, b"fresh agreement\n"),
        ("MISS_FAILED", 1, False, b"fresh failure\n"),
        ("VERIFY_MISMATCH", 86, True, b"fresh disagreement\n"),
    ],
)
def test_status_and_fresh_output_are_preserved_not_inferred_from_reuse_mode(
    monkeypatch, configured_task, status, exit_code, verify, output,
):
    def execute(manifest, task, **kwargs):
        assert kwargs["verify"] is verify
        kwargs["stdout"].write(output)
        return SimpleNamespace(
            exit_code=exit_code,
            as_dict=lambda: {"status": status, "exit_code": exit_code},
        )

    monkeypatch.setattr(mcp, "run_task", execute)
    result = _tool_call({"task": "tests", "verify": verify})
    payload = result["structuredContent"]
    assert payload["status"] == status
    assert payload["exit_code"] == exit_code
    assert payload["mode"] == "reuse"
    assert result["isError"] is (exit_code != 0)
    assert payload["stdout_tail"] == output.decode("utf-8")
    assert payload["stderr_tail"] == ""


def test_fresh_output_is_bounded_tail_not_claimed_complete_transcript(monkeypatch, configured_task):
    def execute(manifest, task, **kwargs):
        kwargs["stdout"].write(b"discarded-prefix" + b"x" * 4000)
        kwargs["stderr"].write(b"discarded-error" + b"y" * 4000)
        return SimpleNamespace(exit_code=0, as_dict=lambda: {"status": "VERIFY_MATCH", "exit_code": 0})

    monkeypatch.setattr(mcp, "run_task", execute)
    payload = _tool_call({"task": "tests", "verify": True})["structuredContent"]
    assert payload["stdout_tail"] == "x" * 4000
    assert payload["stderr_tail"] == "y" * 4000


@pytest.mark.parametrize("argument", [{}, {"verify": False}, {"verify": True}])
def test_verify_never_overrides_external_authority(monkeypatch, configured_task, argument):
    def refuse(manifest):
        raise mcp.ConfigurationError("no matching external authority")

    monkeypatch.setattr(mcp, "require_manifest_authority", refuse)
    monkeypatch.setattr(mcp, "inspect_runtime", lambda *args, **kwargs: pytest.fail("authority refusal reached runtime"))
    monkeypatch.setattr(mcp, "run_task", lambda *args, **kwargs: pytest.fail("authority refusal executed"))
    result = _tool_call({"task": "tests", **argument})
    assert result["isError"] is True
    assert result["structuredContent"] == {"status": "ERROR", "error": "no matching external authority"}
    assert "stdout_tail" not in result["structuredContent"]
