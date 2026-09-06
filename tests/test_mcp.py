from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from zerorun import __version__
from zerorun import mcp
from zerorun.manifest import load_manifest
from zerorun.store import Store
from zerorun.trust import authorize_manifest, manifest_sha256


def _directory_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")


def _file_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks are unavailable: {exc}")


def _authorize_manifest(root: Path) -> None:
    manifest = load_manifest(root / ".zerorun.json")
    authorize_manifest(
        manifest,
        expected_manifest_sha256=manifest_sha256(manifest),
    )


def test_initialize_and_tools_list_are_mcp_compatible():
    initialized = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        }
    )
    assert initialized is not None
    assert initialized["result"]["protocolVersion"] == "2025-11-25"
    assert initialized["result"]["serverInfo"]["name"] == "zerorun"
    assert initialized["result"]["serverInfo"]["version"] == __version__
    assert "repository-bound" in initialized["result"]["instructions"]

    listed = mcp.handle_request(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    )
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == {
        "prepare_pytest",
        "run_pytest",
        "run_tests",
        "stats",
        "explain",
        "doctor",
        "list_tasks",
    }
    by_name = {tool["name"]: tool for tool in listed["result"]["tools"]}
    assert by_name["stats"]["annotations"]["readOnlyHint"] is True
    assert by_name["prepare_pytest"]["annotations"]["destructiveHint"] is True
    assert by_name["explain"]["annotations"] == {
        "title": "Explain reuse decision",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert "quarantined" in by_name["explain"]["description"]
    assert "approve_setup" in by_name["prepare_pytest"]["inputSchema"]["required"]
    assert "observe_test" not in by_name


def test_initialize_does_not_claim_an_unsupported_protocol_version():
    initialized = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1"},
            },
        }
    )
    assert initialized is not None
    assert initialized["result"]["protocolVersion"] == mcp.MCP_PROTOCOL_VERSION


@pytest.mark.parametrize(
    "line, message",
    [
        ('{"jsonrpc":"2.0","method":"ping","method":"tools/list"}', "duplicate JSON key"),
        ('{"jsonrpc":"2.0","method":"ping","params":NaN}', "non-finite JSON constant"),
        ('{"jsonrpc":"2.0","method":"ping","params":1e999}', "non-finite JSON numbers"),
    ],
)
def test_mcp_json_parser_rejects_ambiguous_values(line: str, message: str) -> None:
    with pytest.raises(mcp.ConfigurationError, match=message):
        mcp._parse_json_request(line)


def test_mcp_json_parser_bounds_structure_depth_and_count() -> None:
    deeply_nested: object = None
    for _ in range(mcp.MCP_MAX_JSON_DEPTH + 1):
        deeply_nested = [deeply_nested]
    with pytest.raises(mcp.ConfigurationError, match="depth limit"):
        mcp._validate_json_shape(deeply_nested)

    too_many = [None] * mcp.MCP_MAX_JSON_NODES
    with pytest.raises(mcp.ConfigurationError, match="node limit"):
        mcp._validate_json_shape(too_many)


@pytest.mark.parametrize(
    "message_request",
    [
        {"jsonrpc": "1.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1, "method": ""},
        {"jsonrpc": "2.0", "id": True, "method": "ping"},
        {"jsonrpc": "2.0", "id": None, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1.5, "method": "ping"},
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []},
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": 0},
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": False},
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": ""},
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": None},
    ],
)
def test_mcp_rejects_invalid_json_rpc_envelopes(
    message_request: dict[str, object],
) -> None:
    with pytest.raises(mcp.ConfigurationError):
        mcp.handle_request(message_request)


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"protocolVersion": "2025-11-25"},
        {"protocolVersion": "2025-11-25", "capabilities": {}},
        {
            "protocolVersion": 20251125,
            "capabilities": {},
            "clientInfo": {"name": "client", "version": "1"},
        },
        {
            "protocolVersion": "2025-11-25",
            "capabilities": [],
            "clientInfo": {"name": "client", "version": "1"},
        },
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "", "version": "1"},
        },
    ],
)
def test_initialize_requires_the_mcp_handshake_shape(params: dict[str, object]) -> None:
    with pytest.raises(mcp.ConfigurationError):
        mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": params,
            }
        )


def test_mcp_rejects_non_object_and_unknown_tool_arguments() -> None:
    with pytest.raises(mcp.ConfigurationError, match="object arguments"):
        mcp.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "doctor", "arguments": []},
            }
        )

    unknown = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"surprise": True}},
        }
    )
    assert unknown is not None
    assert unknown["result"]["isError"] is True
    assert "unexpected arguments" in unknown["result"]["structuredContent"]["error"]


def test_mcp_stdio_reader_bounds_and_drains_oversized_request(monkeypatch) -> None:
    oversized = "x" * (mcp.MCP_REQUEST_LIMIT_BYTES + 17) + "\n"
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO(oversized + "{}\n"))

    first, rejected = mcp._read_bounded_stdio_line()
    second, second_rejected = mcp._read_bounded_stdio_line()

    assert first == ""
    assert rejected is True
    assert second == "{}\n"
    assert second_rejected is False


def test_mcp_stdio_rejects_huge_integer_and_continues(monkeypatch, tmp_path: Path) -> None:
    huge = "9" * 5000
    invalid = '{"jsonrpc":"2.0","id":' + huge + ',"method":"ping"}'
    valid = '{"jsonrpc":"2.0","id":2,"method":"ping"}'
    output = io.StringIO()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO(invalid + "\n" + valid + "\n"))
    monkeypatch.setattr(mcp.sys, "stdout", output)
    monkeypatch.setattr(mcp, "_SERVER_ROOT", None)
    monkeypatch.chdir(tmp_path)

    assert mcp.serve_stdio() == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0]["error"]["code"] == -32700
    assert "integer exceeds" in responses[0]["error"]["message"]
    assert responses[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_mcp_stdio_uses_standard_json_rpc_error_codes_and_notification_rules(
    monkeypatch, tmp_path: Path
) -> None:
    lines = [
        '{"jsonrpc":',
        '{"jsonrpc":"1.0","id":1,"method":"ping"}',
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":[]}',
        '{"jsonrpc":"1.0","method":"ping"}',
        '{"jsonrpc":"2.0","method":"ping"}',
        '{"jsonrpc":"2.0","method":"tools/call","params":[]}',
        '{"jsonrpc":"2.0","id":3,"method":"ping"}',
    ]
    output = io.StringIO()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr(mcp.sys, "stdout", output)
    monkeypatch.setattr(mcp, "_SERVER_ROOT", None)
    monkeypatch.chdir(tmp_path)

    assert mcp.serve_stdio() == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [response.get("error", {}).get("code") for response in responses[:4]] == [
        -32700,
        -32600,
        -32602,
        -32600,
    ]
    assert responses[0]["id"] is None
    assert responses[1]["id"] == 1
    assert responses[2]["id"] == 2
    assert responses[3]["id"] is None
    assert responses[4] == {"jsonrpc": "2.0", "id": 3, "result": {}}


@pytest.mark.parametrize("invalid_id", ["NaN", "Infinity", "-Infinity"])
def test_mcp_stdio_never_echoes_non_json_numeric_ids(
    monkeypatch, tmp_path: Path, invalid_id: str
) -> None:
    invalid = (
        '{"jsonrpc":"2.0","id":' + invalid_id + ',"method":"ping"}'
    )
    valid = '{"jsonrpc":"2.0","id":2,"method":"ping"}'
    output = io.StringIO()
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(mcp.sys, "stdin", io.StringIO(invalid + "\n" + valid + "\n"))
    monkeypatch.setattr(mcp.sys, "stdout", output)
    monkeypatch.setattr(mcp, "_SERVER_ROOT", None)
    monkeypatch.chdir(tmp_path)

    assert mcp.serve_stdio() == 0
    lines = output.getvalue().splitlines()
    assert len(lines) == 2
    responses = [
        json.loads(line, parse_constant=mcp._reject_json_constant) for line in lines
    ]
    assert responses[0]["id"] is None
    assert "non-finite JSON constant" in responses[0]["error"]["message"]
    assert responses[1] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_stats_tool_surfaces_verified_time_saved(tmp_path: Path):
    (tmp_path / ".zerorun.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "tests": {
                        "command": ["true"],
                        "inputs": ["input.txt"],
                        "outputs": ["out.txt"],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / ".zerorun"
    state.mkdir()
    (state / "events.jsonl").write_text(
        json.dumps(
            {
                "status": "HIT_REUSED",
                "wall_ms": 25,
                "execution_ms": 1025,
                "saved_ms": 1000,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "stats", "arguments": {"root": str(tmp_path)}},
        }
    )
    payload = response["result"]["structuredContent"]
    assert payload["mode"] == "observe-only"
    assert payload["reuse_ready"] is False
    assert payload["task_reuse_ready"] is False
    assert payload["manifest_version"] == 1
    assert payload["mcp_manifest_supported"] is False
    assert payload["events"] == 1
    assert payload["counts"]["HIT_REUSED"] == 1
    assert payload["saved_ms"] == 1000
    assert payload["saved_seconds"] == 1.0


def test_legacy_observe_test_call_is_hard_disabled(tmp_path: Path):
    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "observe_test",
                "arguments": {
                    "root": str(tmp_path),
                    "command": ["python", "-m", "pytest"],
                    "approve_execution": True,
                },
            },
        }
    )
    payload = response["result"]["structuredContent"]
    assert payload["status"] == "ERROR"
    assert "unknown MCP tool 'observe_test'" in payload["error"]


def test_store_rejects_repository_state_symlink(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    (repository / ".git").mkdir(parents=True)
    outside.mkdir()
    _directory_symlink_or_skip(repository / ".zerorun", outside)
    with pytest.raises(mcp.ConfigurationError, match="symbolic link or junction"):
        Store(repository)

    assert list(outside.iterdir()) == []


def test_store_rejects_preseeded_cache_entry_symlink(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    outside = tmp_path / "outside"
    cache = repository / ".zerorun" / "cache"
    cache.mkdir(parents=True)
    (repository / ".zerorun" / "quarantine").mkdir()
    outside.mkdir()
    key = "a" * 64
    _directory_symlink_or_skip(cache / key, outside)

    store = Store(repository)
    with pytest.raises(mcp.ConfigurationError, match="symbolic link or junction"):
        store.entry(key)

    assert list(outside.iterdir()) == []


def test_store_rejects_preseeded_cache_metadata_and_stream_symlinks(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    entry = repository / ".zerorun" / "cache" / ("b" * 64)
    entry.mkdir(parents=True)
    (repository / ".zerorun" / "quarantine").mkdir()
    outside_metadata = tmp_path / "outside-metadata.json"
    outside_metadata.write_text('{"schema": 3}\n', encoding="utf-8")
    _file_symlink_or_skip(entry / "metadata.json", outside_metadata)

    store = Store(repository)
    with pytest.raises(mcp.ConfigurationError, match="symbolic link or junction"):
        store.metadata("b" * 64)

    (entry / "metadata.json").unlink()
    (entry / "metadata.json").write_text("{}\n", encoding="utf-8")
    outside_stream = tmp_path / "outside-stdout.bin"
    outside_stream.write_bytes(b"do-not-read")
    _file_symlink_or_skip(entry / "stdout.bin", outside_stream)
    (entry / "stderr.bin").write_bytes(b"")
    with pytest.raises(mcp.ConfigurationError, match="symbolic link or junction"):
        store.streams("b" * 64, {"streams_cached": True})

    assert outside_metadata.read_text(encoding="utf-8") == '{"schema": 3}\n'
    assert outside_stream.read_bytes() == b"do-not-read"


def test_present_malformed_manifest_is_a_structured_tool_error(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".zerorun.json").write_text("{not-json\n", encoding="utf-8")

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 30,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"root": str(tmp_path)}},
        }
    )

    assert response["result"]["isError"] is True
    payload = response["result"]["structuredContent"]
    assert payload["status"] == "ERROR"
    assert "invalid json" in payload["error"].lower()


def test_unauthorized_manifest_doctor_is_a_structured_tool_error(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "input.txt").write_text("input\n", encoding="utf-8")
    (tmp_path / ".zerorun.json").write_text(
        json.dumps(
            {
                "version": 2,
                "tasks": {
                    "tests": {
                        "command": ["python", "-m", "pytest"],
                        "inputs": ["input.txt"],
                        "outputs": [],
                        "env": [],
                        "cacheable": True,
                        "unsafe_effects": [],
                        "cache_streams": False,
                        "result_only": True,
                        "closure_reviewed": True,
                        "image": "example.invalid/image@sha256:" + "a" * 64,
                        "platform": "linux/amd64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 32,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"root": str(tmp_path)}},
        }
    )

    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    payload = result["structuredContent"]
    assert payload["ok"] is False
    assert payload["manifest_authorized"] is False
    assert payload["mode"] == "observe-only"
    assert payload["reuse_ready"] is False
    assert payload["tasks"][0]["status"] == "UNTRUSTED"


def test_mcp_rejects_symlinked_manifest_without_reading_target(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    target = tmp_path / "outside-manifest.json"
    original = '{"version": 1, "tasks": {}}\n'
    target.write_text(original, encoding="utf-8")
    _file_symlink_or_skip(repository / ".zerorun.json", target)

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 31,
            "method": "tools/call",
            "params": {"name": "doctor", "arguments": {"root": str(repository)}},
        }
    )

    assert response["result"]["isError"] is True
    assert "symbolic link or junction" in response["result"]["structuredContent"]["error"]
    assert target.read_text(encoding="utf-8") == original


def test_mcp_rejects_symlinked_pytest_profile_before_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    target = tmp_path / "outside-profile.json"
    target.write_text("{}\n", encoding="utf-8")
    _file_symlink_or_skip(repository / ".zerorun-pytest.json", target)
    manifest = SimpleNamespace(version=2, root=repository, tasks={})
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)
    monkeypatch.setattr(mcp, "require_manifest_authority", lambda _manifest: None)
    calls = []
    monkeypatch.setattr(mcp, "run_pytest_profile", lambda *args: calls.append(args))

    with pytest.raises(mcp.ConfigurationError, match="symbolic link or junction"):
        mcp._call_tool("run_pytest", {"root": str(repository)})

    assert calls == []
    assert target.read_text(encoding="utf-8") == "{}\n"


def test_run_pytest_requires_manifest_authority_before_profile_parse(
    monkeypatch, tmp_path: Path
) -> None:
    manifest = SimpleNamespace(version=2, root=tmp_path, tasks={})
    calls: list[str] = []
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)

    def reject_authority(_manifest) -> None:
        calls.append("authority")
        raise mcp.ConfigurationError("manifest is not externally authorized")

    def unexpected_profile_load(*_args, **_kwargs):
        calls.append("profile")
        raise AssertionError("repository profile parsed before manifest authority")

    monkeypatch.setattr(mcp, "require_manifest_authority", reject_authority)
    monkeypatch.setattr(mcp, "load_pytest_profile", unexpected_profile_load)

    with pytest.raises(mcp.ConfigurationError, match="not externally authorized"):
        mcp._call_tool("run_pytest", {"root": str(tmp_path)})

    assert calls == ["authority"]


def test_read_only_diagnostics_never_pull_repository_requested_images(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "input.txt").write_text("input\n", encoding="utf-8")
    (tmp_path / ".zerorun.json").write_text(
        json.dumps(
            {
                "version": 2,
                "tasks": {
                    "tests": {
                        "command": ["python", "-m", "pytest"],
                        "inputs": ["input.txt"],
                        "outputs": [],
                        "env": [],
                        "cacheable": True,
                        "unsafe_effects": [],
                        "cache_streams": False,
                        "result_only": True,
                        "closure_reviewed": True,
                        "image": "attacker.invalid/image@sha256:" + "a" * 64,
                        "platform": "linux/amd64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    _authorize_manifest(tmp_path)
    calls: list[bool] = []

    def refuse_runtime(
        _task, *, allow_pull=True, use_cache=True, repository_root=None
    ):
        calls.append(allow_pull)
        raise mcp.ConfigurationError("image is not already present")

    monkeypatch.setattr(mcp, "inspect_runtime", refuse_runtime)

    doctor = mcp._call_tool("doctor", {"root": str(tmp_path)})[
        "structuredContent"
    ]
    assert doctor["ok"] is False
    assert doctor["reuse_ready"] is False
    assert doctor["task_reuse_ready"] is False
    assert doctor["pytest_reuse_ready"] is False
    assert doctor["mode"] == "observe-only"
    assert doctor["tasks"][0]["status"] == "INVALID"

    stats = mcp._call_tool("stats", {"root": str(tmp_path)})[
        "structuredContent"
    ]
    assert stats["reuse_ready"] is False
    assert stats["task_reuse_ready"] is False
    assert stats["mode"] == "observe-only"

    listed = mcp._call_tool("list_tasks", {"root": str(tmp_path)})[
        "structuredContent"
    ]
    assert listed["reuse_ready"] is False
    assert listed["task_reuse_ready"] is False
    assert listed["mode"] == "observe-only"

    with pytest.raises(mcp.ConfigurationError, match="not already present"):
        mcp._call_tool(
            "explain", {"root": str(tmp_path), "task": "tests"}
        )
    assert calls == [False, False, False, False]


def test_mcp_server_binding_rejects_other_repositories(monkeypatch, tmp_path: Path):
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    (allowed / ".git").mkdir(parents=True)
    (other / ".git").mkdir(parents=True)
    monkeypatch.setattr(mcp, "_SERVER_ROOT", allowed.resolve())

    try:
        mcp._call_tool("stats", {"root": str(other)})
    except mcp.ConfigurationError as exc:
        assert "refusing access" in str(exc)
        assert str(allowed.resolve()) in str(exc)
    else:
        raise AssertionError("MCP server accepted a different repository root")


def test_repository_root_accepts_regular_linked_worktree_marker(tmp_path: Path) -> None:
    repository = tmp_path / "linked-worktree"
    nested = repository / "src"
    nested.mkdir(parents=True)
    (repository / ".git").write_text(
        "gitdir: ../primary/.git/worktrees/linked-worktree\n",
        encoding="utf-8",
    )

    assert mcp._required_repository_root(str(nested)) == repository.resolve()


def test_repository_root_rejects_dangling_link_marker(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    nested = repository / "src"
    nested.mkdir(parents=True)
    _file_symlink_or_skip(repository / ".git", tmp_path / "missing-gitdir")

    with pytest.raises(mcp.ConfigurationError, match="must be a regular file or directory"):
        mcp._required_repository_root(str(nested))


def test_run_tests_uses_real_api_boundary_and_keeps_task_output_off_protocol(monkeypatch):
    task = SimpleNamespace(env=())
    manifest = SimpleNamespace(version=2, root=Path("/repo"), tasks={"tests": task})

    class Result:
        exit_code = 0

        @staticmethod
        def as_dict():
            return {
                "task": "tests",
                "status": "HIT_REUSED",
                "exit_code": 0,
                "wall_ms": 5.0,
                "execution_ms": 1005.0,
                "saved_ms": 1000.0,
                "cache_key": "abc",
                "reason": "all declared dependencies are unchanged",
                "verified": False,
                "restored_outputs": [],
                "phase_ms": {},
            }

    seen = {}

    def fake_run_task(_manifest, _task, **kwargs):
        seen.update(kwargs)
        kwargs["stdout"].write(b"test output")
        return Result()

    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)
    monkeypatch.setattr(mcp, "run_task", fake_run_task)
    monkeypatch.setattr(mcp, "inspect_runtime", lambda *args, **kwargs: {})
    monkeypatch.setattr(mcp, "require_manifest_authority", lambda _manifest: None)

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "run_tests",
                "arguments": {"task": "tests", "root": "/repo"},
            },
        }
    )
    payload = response["result"]["structuredContent"]
    assert payload["status"] == "HIT_REUSED"
    assert payload["mode"] == "reuse"
    assert payload["saved_seconds"] == 1.0
    assert payload["stdout_tail"] == "test output"
    assert "stdout" in seen and "stderr" in seen


def test_run_tests_refuses_legacy_manifest_without_host_execution(monkeypatch):
    task = object()
    manifest = SimpleNamespace(version=1, tasks={"tests": task})
    calls = []
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)
    monkeypatch.setattr(mcp, "require_manifest_authority", lambda _manifest: None)
    monkeypatch.setattr(mcp, "run_task", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(mcp.ConfigurationError, match="version 2"):
        mcp._call_tool("run_tests", {"task": "tests", "root": "/repo"})

    assert calls == []


def test_mcp_refuses_host_environment_forwarding_before_runtime_or_execution(
    monkeypatch, tmp_path: Path
) -> None:
    task = SimpleNamespace(env=("AWS_SECRET_ACCESS_KEY",))
    manifest = SimpleNamespace(version=2, root=tmp_path, tasks={"tests": task})
    runtime_calls = []
    execution_calls = []
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)
    monkeypatch.setattr(
        mcp, "inspect_runtime", lambda *args, **kwargs: runtime_calls.append(args)
    )
    monkeypatch.setattr(
        mcp, "run_task", lambda *args, **kwargs: execution_calls.append(args)
    )

    with pytest.raises(mcp.ConfigurationError, match="environment forwarding"):
        mcp._call_tool("run_tests", {"task": "tests", "root": str(tmp_path)})

    assert runtime_calls == []
    assert execution_calls == []


def test_mcp_normal_run_never_pulls_missing_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    task = SimpleNamespace(env=())
    manifest = SimpleNamespace(version=2, root=tmp_path, tasks={"tests": task})
    calls = []
    executed = []
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)
    monkeypatch.setattr(mcp, "require_manifest_authority", lambda _manifest: None)

    def missing(_task, *, allow_pull, repository_root):
        calls.append((allow_pull, repository_root))
        raise mcp.ConfigurationError("pinned OCI image is not present locally")

    monkeypatch.setattr(mcp, "inspect_runtime", missing)
    monkeypatch.setattr(
        mcp, "run_task", lambda *args, **kwargs: executed.append(args)
    )

    with pytest.raises(mcp.ConfigurationError, match="not present locally"):
        mcp._call_tool("run_tests", {"task": "tests", "root": str(tmp_path)})

    assert calls == [(False, tmp_path)]
    assert executed == []


def test_run_pytest_uses_reviewed_profile_runtime(monkeypatch, tmp_path: Path):
    manifest = SimpleNamespace(
        version=2,
        root=tmp_path,
        tasks={"tests": SimpleNamespace(cacheable=False, env=())},
    )
    profile = SimpleNamespace(profile_sha256="a" * 64, task_name="tests")
    seen = {}

    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)
    monkeypatch.setattr(mcp, "inspect_runtime", lambda *args, **kwargs: {})
    monkeypatch.setattr(mcp, "require_manifest_authority", lambda _manifest: None)
    monkeypatch.setattr(
        mcp,
        "require_pytest_profile_authority",
        lambda _manifest, _digest: None,
    )

    def fake_load(path, supplied_manifest):
        seen["path"] = path
        seen["manifest"] = supplied_manifest
        return profile

    def fake_run(supplied_manifest, supplied_profile):
        assert supplied_manifest is manifest
        assert supplied_profile is profile
        return {
            "status": "PYTEST_INCREMENTAL_PASS",
            "exit_code": 0,
            "saved_ms": 2500.0,
            "reused_nodes": 7,
            "fresh_nodes": 2,
            "unknown_nodes": 0,
        }

    monkeypatch.setattr(mcp, "load_pytest_profile", fake_load)
    monkeypatch.setattr(mcp, "run_pytest_profile", fake_run)

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "run_pytest", "arguments": {"root": str(tmp_path)}},
        }
    )
    payload = response["result"]["structuredContent"]
    assert payload["mode"] == "pytest-node-reuse"
    assert payload["saved_seconds"] == 2.5
    assert payload["reused_nodes"] == 7
    assert seen["path"] == tmp_path / ".zerorun-pytest.json"


def test_run_pytest_profile_path_cannot_escape_the_repository(tmp_path: Path):
    manifest = SimpleNamespace(root=(tmp_path / "repo").resolve())
    manifest.root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    for supplied in (str(outside), "../outside.json"):
        try:
            mcp._profile_path(manifest, supplied)
        except mcp.ConfigurationError as exc:
            assert "inside the bound repository" in str(exc)
        else:
            raise AssertionError("MCP accepted a pytest profile outside the repository")


def test_prepare_pytest_returns_compact_non_authorizing_summary(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    manifest = SimpleNamespace(
        root=tmp_path,
        tasks={"tests": SimpleNamespace(cacheable=False)},
    )
    monkeypatch.setattr(mcp, "_manifest_for_root", lambda _root: manifest)

    seen = {}

    def fake_prepare(root, *, task_name=None, targets=None, output=None):
        seen["root"] = root
        seen["task_name"] = task_name
        seen["targets"] = targets
        return {
            "candidate_sha256": "a" * 64,
            "task": task_name or "tests",
            "targets": list(targets or ("tests",)),
            "nodes": {
                "tests/test_one.py::test_one": {
                    "reviewable": True,
                    "fresh_required": False,
                },
                "tests/test_two.py::test_two": {
                    "reviewable": False,
                    "fresh_required": True,
                },
            },
        }

    monkeypatch.setattr(mcp, "prepare_candidate", fake_prepare)

    response = mcp.handle_request(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "prepare_pytest",
                "arguments": {
                    "root": str(tmp_path),
                    "task": "tests",
                    "targets": ["tests"],
                    "approve_setup": True,
                },
            },
        }
    )
    payload = response["result"]["structuredContent"]
    assert payload["status"] == "CANDIDATE_READY"
    assert payload["authorizes_reuse"] is False
    assert payload["reuse_activated"] is False
    assert payload["candidate_reviewable_nodes"] == 1
    assert payload["fresh_required_nodes"] == 1
    assert payload["candidate_sha256"] == "a" * 64
    assert payload["managed_task_bootstrapped"] is False
    assert payload["task_level_reuse"] is False
    assert seen == {
        "root": tmp_path,
        "task_name": "tests",
        "targets": ("tests",),
    }
