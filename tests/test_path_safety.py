from __future__ import annotations

import ast
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from zerorun import path_safety


ROOT = Path(__file__).resolve().parents[1]


class _Pre312Path:
    """Path-like test double with no ``is_junction`` attribute."""

    def __init__(self, value: str, *, symlink: bool = False) -> None:
        self.value = value
        self.symlink = symlink

    def __fspath__(self) -> str:
        return self.value

    def is_symlink(self) -> bool:
        return self.symlink


def test_pre312_windows_reparse_point_is_link_like(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected = _Pre312Path("junction-without-pathlib-support")
    monkeypatch.setattr(
        path_safety,
        "_LSTAT",
        lambda path: SimpleNamespace(
            st_file_attributes=path_safety._WINDOWS_REPARSE_POINT
        ),
    )

    assert path_safety.is_link_like(inspected) is True


def test_symlink_and_native_junction_short_circuit_lstat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_lstat(_path):
        pytest.fail("lstat must not run after a positive link classification")

    monkeypatch.setattr(path_safety, "_LSTAT", unexpected_lstat)
    assert path_safety.is_link_like(_Pre312Path("symlink", symlink=True)) is True

    native = _Pre312Path("native-junction")
    native.is_junction = lambda: True
    assert path_safety.is_link_like(native) is True


def test_non_reparse_and_missing_paths_are_not_link_like(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected = _Pre312Path("ordinary")
    monkeypatch.setattr(
        path_safety,
        "_LSTAT",
        lambda path: SimpleNamespace(st_file_attributes=0),
    )
    assert path_safety.is_link_like(inspected) is False

    def missing(_path):
        raise FileNotFoundError

    monkeypatch.setattr(path_safety, "_LSTAT", missing)
    assert path_safety.is_link_like(inspected) is False


def test_link_inspection_errors_fail_closed_to_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspected = _Pre312Path("unreadable")

    def unreadable(_path):
        raise PermissionError("denied")

    monkeypatch.setattr(path_safety, "_LSTAT", unreadable)
    with pytest.raises(PermissionError, match="denied"):
        path_safety.is_link_like(inspected)


def test_private_temporary_directory_is_writable_and_removed(tmp_path: Path) -> None:
    with path_safety.private_temporary_directory(
        tmp_path,
        prefix="zerorun-test-",
    ) as temporary:
        assert temporary.parent == tmp_path
        assert temporary.name.startswith("zerorun-test-")
        (temporary / "proof.txt").write_text("writable\n", encoding="utf-8")
        assert (temporary / "proof.txt").read_text(encoding="utf-8") == "writable\n"

    assert not temporary.exists()


def test_private_temporary_directory_removes_readonly_git_style_files(
    tmp_path: Path,
) -> None:
    with path_safety.private_temporary_directory(
        tmp_path,
        prefix="zerorun-readonly-",
    ) as temporary:
        artifact = temporary / "object"
        artifact.write_bytes(b"immutable object\n")
        artifact.chmod(stat.S_IREAD)

    assert not temporary.exists()


def test_private_temp_creation_avoids_special_windows_0700_acl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[object, ...]] = []
    token_sizes: list[int] = []

    def record_mkdir(*arguments: object) -> None:
        calls.append(arguments)

    def deterministic_token(size: int) -> str:
        token_sizes.append(size)
        return "a" * (size * 2)

    monkeypatch.setattr(path_safety.os, "mkdir", record_mkdir)
    monkeypatch.setattr(path_safety.secrets, "token_hex", deterministic_token)

    created = path_safety.create_private_temp_directory(
        tmp_path,
        prefix="zerorun-test-",
    )

    assert created == tmp_path / ("zerorun-test-" + "a" * 16)
    assert token_sizes == [8]
    expected_calls = [(created,)] if os.name == "nt" else [(created, 0o700)]
    assert calls == expected_calls


@pytest.mark.parametrize(
    "prefix", ["", ".", "..", "../escape-", "dir/child-", "dir\\child-"]
)
def test_private_temp_creation_rejects_non_basename_prefixes(
    prefix: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="prefix"):
        path_safety.create_private_temp_directory(tmp_path, prefix=prefix)


def test_all_junction_checks_use_the_cross_version_utility() -> None:
    offenders = []
    for source in sorted((ROOT / "src" / "zerorun").glob("*.py")):
        if source.name == "path_safety.py":
            continue
        if "is_junction" in source.read_text(encoding="utf-8"):
            offenders.append(source.name)
    assert offenders == []


def test_product_temp_directories_avoid_acl_sensitive_tempfile_apis() -> None:
    sources = list((ROOT / "src" / "zerorun").glob("*.py"))
    sources.extend(
        ROOT / "tools" / name
        for name in (
            "codex_agent_integration_smoke.py",
            "codex_agent_lifecycle.py",
            "commercial_repo_smoke.py",
            "install_verified_codex_cli.py",
            "product_generalization_benchmark.py",
            "pytest_batched_executor.py",
        )
    )
    offenders: list[str] = []
    for source in sorted(sources):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "tempfile"
                and function.attr in {"TemporaryDirectory", "mkdtemp"}
            ) or (
                isinstance(function, ast.Name)
                and function.id in {"TemporaryDirectory", "mkdtemp"}
            ):
                offenders.append(f"{source.relative_to(ROOT)}:{node.lineno}")

    assert offenders == []


def test_product_windows_directory_creation_avoids_literal_0700_mode() -> None:
    sources = [
        source
        for source in (ROOT / "src" / "zerorun").glob("*.py")
        if source.name != "path_safety.py"
    ]
    sources.extend(
        ROOT / "tools" / name
        for name in (
            "codex_agent_integration_smoke.py",
            "codex_agent_lifecycle.py",
            "commercial_repo_smoke.py",
            "install_verified_codex_cli.py",
            "product_generalization_benchmark.py",
            "pytest_batched_executor.py",
        )
    )
    offenders: list[str] = []
    for source in sorted(sources):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else function.id if isinstance(function, ast.Name) else ""
            )
            if name not in {"mkdir", "makedirs"}:
                continue
            mode_values = list(node.args)
            mode_values.extend(
                keyword.value for keyword in node.keywords if keyword.arg == "mode"
            )
            if any(
                isinstance(value, ast.Constant) and value.value == 0o700
                for value in mode_values
            ):
                offenders.append(f"{source.relative_to(ROOT)}:{node.lineno}")

    assert offenders == []


def test_runtime_and_commercial_sources_parse_as_python_310() -> None:
    sources = list((ROOT / "src" / "zerorun").glob("*.py"))
    sources.extend(
        [
            ROOT / "tools" / "commercial_repo_smoke.py",
            ROOT / "tools" / "aggregate_commercial_smoke.py",
        ]
    )
    for source in sorted(sources):
        ast.parse(
            source.read_text(encoding="utf-8"),
            filename=str(source),
            feature_version=(3, 10),
        )
