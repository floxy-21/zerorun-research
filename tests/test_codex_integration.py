from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from zerorun import codex
from zerorun.manifest import load_manifest
from zerorun.pytest_profile import load_pytest_profile
from zerorun.trust import (
    authorize_manifest,
    authorize_pytest_profile,
    manifest_sha256,
)


def test_bundled_skill_matches_the_repository_codex_skill() -> None:
    root = Path(__file__).resolve().parents[1]
    skill = root / "docs" / "zerorun-SKILL.md"
    assert skill.read_text(encoding="utf-8") == codex._SKILL


def _write_reusable_manifest(root: Path) -> None:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".zerorun.json").write_text(
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
                        "image": "python@sha256:" + "1" * 64,
                        "platform": "linux/amd64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )





def _write_valid_pytest_profile_pair(root: Path) -> None:
    (root / ".git").mkdir(exist_ok=True)
    (root / ".zerorun.json").write_text(
        json.dumps(
            {
                "version": 2,
                "tasks": {
                    "tests": {
                        "command": ["python", "-m", "pytest"],
                        "inputs": ["tests"],
                        "outputs": [],
                        "env": [],
                        "cacheable": True,
                        "unsafe_effects": [],
                        "cache_streams": False,
                        "result_only": True,
                        "closure_reviewed": True,
                        "image": "python@sha256:" + "1" * 64,
                        "platform": "linux/amd64",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / ".zerorun-pytest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "task": "tests",
                "base_args": [],
                "targets": ["tests"],
                "static_inputs": ["pyproject.toml"],
                "session_closure": {
                    "selectors": ["pkg.py::setup"],
                    "fallback_files": [],
                },
                "nodes": {
                    "tests/test_basic.py::test_basic": {
                        "reviewable": True,
                        "fresh_required": False,
                        "reason": None,
                        "selectors": ["pkg.py::run"],
                        "fallback_files": [],
                    }
                },
                "independence_review_sha256": "2" * 64,
            }
        ),
        encoding="utf-8",
    )


def _installable_codex(monkeypatch, calls):
    real_which = codex.shutil.which
    registered = False
    zerorun = str(Path("/usr/bin/zerorun").resolve())
    monkeypatch.setattr(
        codex.shutil,
        "which",
        lambda name: (
            "/usr/bin/codex"
            if name == "codex"
            else "/usr/bin/zerorun"
            if name == "zerorun"
            else real_which(name)
        ),
    )

    def fake_run(command):
        nonlocal registered
        calls.append(command)
        if command[1:5] == ["mcp", "get", "zerorun", "--json"]:
            if not registered:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "name": "zerorun",
                        "enabled": True,
                        "disabled_reason": None,
                        "transport": {
                            "type": "stdio",
                            "command": zerorun,
                            "args": ["mcp-server"],
                            "env": None,
                            "env_vars": [],
                            "cwd": None,
                        },
                        "enabled_tools": None,
                        "disabled_tools": None,
                    }
                ),
                stderr="",
            )
        registered = True
        return SimpleNamespace(returncode=0, stdout="Added global MCP server 'zerorun'.", stderr="")

    monkeypatch.setattr(codex, "_run_codex_command", fake_run)
    monkeypatch.setattr(codex, "inspect_runtime", lambda *args, **kwargs: {})


def test_codex_registration_process_is_time_and_output_bounded(monkeypatch) -> None:
    captured = {}

    def fake_bounded(command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            args=command,
            returncode=0,
            stdout=b"{}",
            stderr=b"",
        ), False

    monkeypatch.setattr(codex, "_run_bounded_process", fake_bounded)

    completed = codex._run_codex_command(["codex", "mcp", "get"])

    assert completed.returncode == 0
    assert completed.stdout == "{}"
    assert captured["timeout_seconds"] == codex._CODEX_COMMAND_TIMEOUT_SECONDS
    assert (
        captured["output_limit_bytes"]
        == codex._CODEX_COMMAND_OUTPUT_LIMIT_BYTES
    )


def test_codex_registration_timeout_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        codex,
        "_run_bounded_process",
        lambda command, **kwargs: (
            SimpleNamespace(
                args=command,
                returncode=124,
                stdout=b"",
                stderr=b"",
            ),
            True,
        ),
    )

    with pytest.raises(TimeoutError, match="exceeded"):
        codex._run_codex_command(["codex", "mcp", "get"])


def _authorize_reuse(root: Path, *, profile: bool = False) -> None:
    manifest = load_manifest(root / ".zerorun.json")
    authorize_manifest(
        manifest,
        expected_manifest_sha256=manifest_sha256(manifest),
    )
    if profile:
        loaded = load_pytest_profile(root / ".zerorun-pytest.json", manifest)
        authorize_pytest_profile(manifest, loaded.profile_sha256)


def test_install_codex_creates_project_skill_and_registers_mcp(monkeypatch, tmp_path: Path):
    _write_reusable_manifest(tmp_path)
    _authorize_reuse(tmp_path)
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    skill = tmp_path / ".agents" / "skills" / "zerorun" / "SKILL.md"
    assert skill.is_file()
    contents = skill.read_text(encoding="utf-8")
    assert "name: zerorun" in contents
    assert "observe_test" not in contents
    assert "direct tests outside ZeroRun" in contents
    assert "run_pytest" in contents
    assert result["ready"] is True
    assert result["integration_ready"] is True
    assert result["task_reuse_ready"] is True
    assert result["pytest_reuse_ready"] is False
    assert result["reuse_ready"] is True
    assert result["mode"] == "task-reuse"
    assert result["mcp"]["registered"] is True
    add = next(call for call in calls if call[1:4] == ["mcp", "add", "zerorun"])
    assert add[:5] == [
        str(Path("/usr/bin/codex").resolve()),
        "mcp",
        "add",
        "zerorun",
        "--",
    ]
    assert add[-2:] == [str(Path("/usr/bin/zerorun").resolve()), "mcp-server"]


def test_repository_manifest_alone_cannot_claim_codex_reuse_ready(
    monkeypatch, tmp_path: Path
) -> None:
    _write_reusable_manifest(tmp_path)
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert result["integration_ready"] is True
    assert result["manifest_authorized"] is False
    assert result["task_reuse_ready"] is False
    assert result["reuse_ready"] is False
    assert result["mode"] == "observe-only"
    assert len(result["manifest_sha256"]) == 64


def test_install_codex_keeps_legacy_manifest_observation_only(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".zerorun.json").write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "host-command": {
                        "command": ["python", "untrusted.py"],
                        "inputs": ["untrusted.py"],
                        "outputs": [],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert result["integration_ready"] is True
    assert result["task_reuse_ready"] is False
    assert result["pytest_reuse_ready"] is False
    assert result["reuse_ready"] is False
    assert result["mode"] == "observe-only"


def test_skill_install_refuses_symlinked_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    try:
        (repository / ".agents").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symbolic links are unavailable: {exc}")

    result = codex.install_project_skill(repository)

    assert result["conflict"] is True
    assert result["installed"] is False
    assert "symbolic link or junction" in result["reason"]
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize(
    "component", (".agents", ".agents/skills", ".agents/skills/zerorun")
)
def test_skill_install_refuses_intermediate_regular_file_without_exception(
    tmp_path: Path, component: str
) -> None:
    blocked = tmp_path / component
    blocked.parent.mkdir(parents=True, exist_ok=True)
    blocked.write_text("user-owned\n", encoding="utf-8")

    result = codex.install_project_skill(tmp_path)

    assert result["conflict"] is True
    assert result["installed"] is False
    assert "non-directory component" in result["reason"]
    assert blocked.read_text(encoding="utf-8") == "user-owned\n"


def test_install_codex_surfaces_intermediate_file_as_structured_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".agents").write_text("user-owned\n", encoding="utf-8")
    calls: list[list[str]] = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert result["skill"]["conflict"] is True
    assert result["integration_ready"] is False
    assert result["mcp"]["skipped"] is True
    assert calls == []


@pytest.mark.parametrize("mutation", ["truncate", "grow", "rewrite"])
def test_existing_skill_reader_rejects_in_place_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "SKILL.md"
    original_bytes = b"# ZeroRun\n"
    path.write_bytes(original_bytes)
    real_read = codex.os.read
    mutated = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            before = path.stat()
            if mutation == "truncate":
                replacement = original_bytes[:-1]
            elif mutation == "grow":
                replacement = original_bytes + b" "
            else:
                replacement = b"# ZeroSun\n"
            path.write_bytes(replacement)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return real_read(descriptor, size)

    monkeypatch.setattr(codex.os, "read", racing_read)
    with pytest.raises(ValueError, match="changed while it was inspected"):
        codex._read_existing_skill(path)


def test_expected_mcp_command_prefers_installed_zerorun(monkeypatch):
    monkeypatch.setattr(codex, "_is_compiled_executable", lambda: False)
    monkeypatch.setattr(
        codex.shutil,
        "which",
        lambda name: "/usr/local/bin/zerorun" if name == "zerorun" else None,
    )

    assert codex._expected_mcp_command() == (
        str(Path("/usr/local/bin/zerorun").resolve()),
        ["mcp-server"],
    )


def test_expected_mcp_command_uses_compiled_binary_without_python_module_args(
    monkeypatch,
):
    monkeypatch.setattr(codex, "_is_compiled_executable", lambda: True)
    monkeypatch.setattr(codex.sys, "executable", "/opt/zerorun-linux-amd64")

    assert codex._expected_mcp_command() == (
        str(Path("/opt/zerorun-linux-amd64").resolve()),
        ["mcp-server"],
    )


def test_mcp_registration_refuses_python_module_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(codex, "_is_compiled_executable", lambda: False)
    monkeypatch.setattr(
        codex.shutil,
        "which",
        lambda name: "/usr/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        codex.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    result = codex.register_codex_mcp()

    assert result["registered"] is False
    assert result["changed"] is False
    assert "could shadow that module" in result["reason"]
    assert calls == []


def test_mcp_registration_refuses_repository_controlled_launcher(
    monkeypatch, tmp_path: Path
):
    launcher = tmp_path / "bin" / "zerorun"
    launcher.parent.mkdir()
    launcher.write_text("untrusted\n", encoding="utf-8")
    monkeypatch.setattr(codex, "_is_compiled_executable", lambda: False)
    monkeypatch.setattr(codex.shutil, "which", lambda _name: str(launcher))

    with pytest.raises(ValueError, match="repository-controlled launcher"):
        codex._expected_mcp_command(repository_root=tmp_path)


def test_mcp_registration_refuses_repository_controlled_codex(
    monkeypatch, tmp_path: Path
) -> None:
    untrusted_codex = tmp_path / "codex.exe"
    untrusted_codex.write_text("untrusted\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        codex.shutil,
        "which",
        lambda name: str(untrusted_codex) if name == "codex" else "/safe/zerorun",
    )
    monkeypatch.setattr(
        codex.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    result = codex.register_codex_mcp(repository_root=tmp_path)

    assert result["registered"] is False
    assert "repository-controlled launcher" in result["reason"]
    assert calls == []


@pytest.mark.parametrize(
    "adversity",
    (
        "disabled",
        "fixed_env",
        "host_env",
        "other_cwd",
        "tool_allowlist",
        "disabled_tool",
        "non_stdio",
        "unknown_transport_field",
    ),
)
def test_existing_mcp_registration_must_match_full_safe_semantics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, adversity: str
) -> None:
    expected = str(Path("/usr/bin/zerorun").resolve())
    other = tmp_path / "other"
    other.mkdir()
    registration = {
        "name": "zerorun",
        "enabled": True,
        "disabled_reason": None,
        "transport": {
            "type": "stdio",
            "command": expected,
            "args": ["mcp-server"],
            "env": None,
            "env_vars": [],
            "cwd": None,
        },
        "enabled_tools": None,
        "disabled_tools": None,
    }
    if adversity == "disabled":
        registration["enabled"] = False
    elif adversity == "fixed_env":
        registration["transport"]["env"] = {"SECRET_TOKEN": "must-not-leak"}
    elif adversity == "host_env":
        registration["transport"]["env_vars"] = ["SECRET_TOKEN"]
    elif adversity == "other_cwd":
        registration["transport"]["cwd"] = str(other)
    elif adversity == "tool_allowlist":
        registration["enabled_tools"] = ["doctor"]
    elif adversity == "disabled_tool":
        registration["disabled_tools"] = ["run_tests"]
    elif adversity == "non_stdio":
        registration["transport"]["type"] = "http"
    else:
        registration["transport"]["future_environment"] = "must-not-leak"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        codex.shutil,
        "which",
        lambda name: "/usr/bin/codex" if name == "codex" else "/usr/bin/zerorun",
    )
    monkeypatch.setattr(
        codex,
        "_run_codex_command",
        lambda command: (
            calls.append(command)
            or SimpleNamespace(
                returncode=0, stdout=json.dumps(registration), stderr=""
            )
        ),
    )

    result = codex.register_codex_mcp(repository_root=tmp_path)

    assert result["registered"] is False
    assert result["changed"] is False
    assert "refusing to overwrite" in result["reason"]
    assert "must-not-leak" not in json.dumps(result)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "raw",
    (
        '{"name":"zerorun","name":"other"}',
        ("[" * 40) + "0" + ("]" * 40),
    ),
)
def test_existing_mcp_registration_json_is_bounded_and_unambiguous(
    raw: str, tmp_path: Path
) -> None:
    registration, problem = codex._validate_existing_server(
        raw,
        expected_command="zerorun",
        expected_args=["mcp-server"],
        repository_root=tmp_path,
    )

    assert registration is None
    assert problem


def test_mcp_registration_requires_safe_post_add_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = str(Path("/usr/bin/zerorun").resolve())
    calls: list[list[str]] = []

    def fake_run(command):
        calls.append(command)
        if command[1:5] == ["mcp", "get", "zerorun", "--json"] and len(calls) == 1:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        if command[1:4] == ["mcp", "add", "zerorun"]:
            return SimpleNamespace(returncode=0, stdout="added", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "name": "zerorun",
                    "enabled": False,
                    "disabled_reason": "policy",
                    "transport": {
                        "type": "stdio",
                        "command": expected,
                        "args": ["mcp-server"],
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(
        codex.shutil,
        "which",
        lambda name: "/usr/bin/codex" if name == "codex" else "/usr/bin/zerorun",
    )
    monkeypatch.setattr(codex, "_run_codex_command", fake_run)

    result = codex.register_codex_mcp(repository_root=tmp_path)

    assert result["registered"] is False
    assert result["changed"] is True
    assert "post-registration verification" in result["reason"]
    assert len(calls) == 3


def test_install_codex_refuses_to_overwrite_user_authored_skill(
    monkeypatch, tmp_path: Path
):
    (tmp_path / ".git").mkdir()
    skill = tmp_path / ".agents" / "skills" / "zerorun" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: zerorun\n---\n\n# Custom policy\n", encoding="utf-8")
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert skill.read_text(encoding="utf-8").endswith("# Custom policy\n")
    assert result["skill"]["conflict"] is True
    assert result["integration_ready"] is False
    assert result["ready"] is False
    assert result["mcp"]["skipped"] is True
    assert calls == []


def test_install_codex_marker_substring_is_not_overwrite_authority(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    skill = tmp_path / ".agents" / "skills" / "zerorun" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    authored = (
        "---\nname: zerorun\n---\n\n"
        f"{codex._MANAGED_SKILL_MARKER}\n\n"
        "# User-authored policy that must survive\n"
    )
    skill.write_text(authored, encoding="utf-8")
    calls: list[list[str]] = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert skill.read_text(encoding="utf-8") == authored
    assert result["skill"]["conflict"] is True
    assert result["skill"]["installed"] is False
    assert result["integration_ready"] is False
    assert result["mcp"]["skipped"] is True
    assert calls == []


def test_skill_install_never_overwrites_file_created_during_absence_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    skill = tmp_path / ".agents" / "skills" / "zerorun" / "SKILL.md"
    raced_content = "# Concurrent user policy\n"
    real_create = codex._create_project_skill

    def racing_create(path: Path) -> None:
        path.write_text(raced_content, encoding="utf-8")
        real_create(path)

    monkeypatch.setattr(codex, "_create_project_skill", racing_create)

    result = codex.install_project_skill(tmp_path)

    assert result["conflict"] is True
    assert result["installed"] is False
    assert "appeared while installation was in progress" in result["reason"]
    assert skill.read_text(encoding="utf-8") == raced_content


def test_install_codex_refuses_symlinked_skill_path(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    target = tmp_path / "outside.md"
    target.write_text("do not replace\n", encoding="utf-8")
    skill = tmp_path / ".agents" / "skills" / "zerorun" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    try:
        skill.symlink_to(target)
    except OSError as exc:
        import pytest

        pytest.skip(f"symbolic links are not available on this host: {exc}")
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert target.read_text(encoding="utf-8") == "do not replace\n"
    assert result["skill"]["conflict"] is True
    assert result["ready"] is False
    assert calls == []


def test_install_codex_refuses_oversized_or_non_utf8_existing_skill(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    skill = tmp_path / ".agents" / "skills" / "zerorun" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    calls = []
    _installable_codex(monkeypatch, calls)

    skill.write_bytes(b"x" * (codex._MAX_EXISTING_SKILL_BYTES + 1))
    oversized = codex.install_codex(tmp_path)
    assert oversized["skill"]["conflict"] is True
    assert "1 MiB safety limit" in oversized["skill"]["reason"]
    assert calls == []

    skill.write_bytes(b"\xff\xfe")
    invalid = codex.install_codex(tmp_path)
    assert invalid["skill"]["conflict"] is True
    assert "valid UTF-8" in invalid["skill"]["reason"]
    assert calls == []


def test_install_codex_reports_pytest_node_reuse_when_profile_exists(monkeypatch, tmp_path: Path):
    _write_valid_pytest_profile_pair(tmp_path)
    _authorize_reuse(tmp_path, profile=True)
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert result["task_reuse_ready"] is True
    assert result["pytest_reuse_ready"] is True
    assert result["reuse_ready"] is True
    assert result["mode"] == "pytest-node-reuse"
    assert "fine-grained pytest" in result["next_action"]


def test_install_codex_does_not_claim_reuse_when_pinned_runtime_is_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_valid_pytest_profile_pair(tmp_path)
    _authorize_reuse(tmp_path, profile=True)
    calls: list[list[str]] = []
    _installable_codex(monkeypatch, calls)

    def missing_runtime(*_args, **_kwargs):
        raise codex.ConfigurationError("pinned OCI image is not present locally")

    monkeypatch.setattr(codex, "inspect_runtime", missing_runtime)

    result = codex.install_codex(tmp_path)

    assert result["integration_ready"] is True
    assert result["manifest_authorized"] is True
    assert result["pytest_profile_authorized"] is True
    assert result["task_reuse_ready"] is False
    assert result["pytest_reuse_ready"] is False
    assert result["reuse_ready"] is False
    assert result["mode"] == "observe-only"


def test_pytest_profile_alone_marks_reuse_ready(monkeypatch, tmp_path: Path):
    _write_valid_pytest_profile_pair(tmp_path)
    raw = json.loads((tmp_path / ".zerorun.json").read_text(encoding="utf-8"))
    raw["tasks"]["tests"]["cacheable"] = False
    (tmp_path / ".zerorun.json").write_text(
        json.dumps(raw), encoding="utf-8"
    )
    _authorize_reuse(tmp_path, profile=True)
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert result["task_reuse_ready"] is False
    assert result["pytest_reuse_ready"] is True
    assert result["reuse_ready"] is True
    assert result["mode"] == "pytest-node-reuse"


def test_existing_different_mcp_registration_is_not_overwritten(monkeypatch, tmp_path: Path):
    _write_reusable_manifest(tmp_path)
    calls = []

    monkeypatch.setattr(codex.shutil, "which", lambda _name: "/usr/bin/codex")

    def fake_run(command):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout='{"name":"zerorun","transport":{"type":"stdio","command":"other","args":["server"]}}',
            stderr="",
        )

    monkeypatch.setattr(codex, "_run_codex_command", fake_run)

    result = codex.install_codex(tmp_path)

    assert result["ready"] is False
    assert result["integration_ready"] is False
    assert result["reuse_ready"] is False
    assert result["mode"] == "unavailable"
    assert result["mcp"]["registered"] is False
    assert "refusing to overwrite" in result["mcp"]["reason"]
    assert len(calls) == 1


def test_install_codex_is_observation_ready_without_manifest(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    calls = []
    _installable_codex(monkeypatch, calls)

    result = codex.install_codex(tmp_path)

    assert result["manifest_present"] is False
    assert result["ready"] is True
    assert result["integration_ready"] is True
    assert result["task_reuse_ready"] is False
    assert result["pytest_reuse_ready"] is False
    assert result["reuse_ready"] is False
    assert result["mode"] == "observe-only"
    assert "prepare_pytest" in result["next_action"]
    assert "observation-only" in result["next_action"].lower()


def test_install_codex_is_not_ready_when_codex_cli_is_missing(monkeypatch, tmp_path: Path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(codex.shutil, "which", lambda _name: None)

    result = codex.install_codex(tmp_path)

    assert result["manifest_present"] is False
    assert result["ready"] is False
    assert result["integration_ready"] is False
    assert result["reuse_ready"] is False
    assert result["mode"] == "unavailable"


def test_install_codex_refuses_a_non_repository_before_writing(
    monkeypatch, tmp_path: Path
):
    calls = []
    _installable_codex(monkeypatch, calls)

    try:
        codex.install_codex(tmp_path)
    except ValueError as exc:
        assert "containing .git" in str(exc)
    else:
        raise AssertionError("Codex integration accepted a non-repository directory")

    assert not (tmp_path / ".agents").exists()
    assert calls == []
