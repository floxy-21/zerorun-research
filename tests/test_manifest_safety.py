from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from zerorun import manifest as manifest_module
from zerorun.manifest import load_manifest
from zerorun.model import ConfigurationError


def _write_manifest(path: Path, raw: str) -> Path:
    path.write_text(raw, encoding="utf-8")
    return path


def _valid_task() -> dict[str, object]:
    return {
        "command": ["python", "-m", "pytest"],
        "inputs": ["tests"],
        "outputs": ["result.json"],
    }


@pytest.mark.parametrize(
    "raw",
    [
        (
            '{"version": 999, "version": 1, "tasks": '
            + json.dumps({"tests": _valid_task()})
            + "}"
        ),
        (
            '{"version": 1, "tasks": {"tests": '
            '{"command": ["first"], "command": ["second"], '
            '"inputs": ["tests"], "outputs": ["result.json"]}}}'
        ),
    ],
)
def test_manifest_rejects_duplicate_json_members(tmp_path: Path, raw: str) -> None:
    with pytest.raises(ConfigurationError, match="duplicate JSON member"):
        load_manifest(_write_manifest(tmp_path / ".zerorun.json", raw))


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_manifest_rejects_non_finite_json_numbers(
    tmp_path: Path, value: str
) -> None:
    raw = (
        '{"version": 1, "ignored": '
        + value
        + ', "tasks": '
        + json.dumps({"tests": _valid_task()})
        + "}"
    )
    with pytest.raises(ConfigurationError, match="non-finite JSON"):
        load_manifest(_write_manifest(tmp_path / ".zerorun.json", raw))


def test_manifest_rejects_boolean_version(tmp_path: Path) -> None:
    raw = json.dumps({"version": True, "tasks": {"tests": _valid_task()}})
    with pytest.raises(ConfigurationError, match="unsupported manifest version"):
        load_manifest(_write_manifest(tmp_path / ".zerorun.json", raw))


def test_manifest_normalizes_integer_limit_failure(tmp_path: Path) -> None:
    raw = '{"version": ' + ("9" * 5_000) + ', "tasks": {}}'
    with pytest.raises(ConfigurationError, match="JSON integer exceeds"):
        load_manifest(_write_manifest(tmp_path / ".zerorun.json", raw))


def test_manifest_bounds_depth_before_decode(tmp_path: Path) -> None:
    nested = "0"
    for _ in range(manifest_module._MAX_JSON_DEPTH + 1):
        nested = "[" + nested + "]"
    raw = '{"version": 1, "ignored": ' + nested + ', "tasks": {}}'
    with pytest.raises(ConfigurationError, match="JSON depth limit"):
        load_manifest(_write_manifest(tmp_path / ".zerorun.json", raw))


def test_manifest_bounds_size_and_object_members_before_schema_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * 65)
    monkeypatch.setattr(manifest_module, "_MAX_MANIFEST_BYTES", 64)
    monkeypatch.setattr(
        manifest_module.json,
        "loads",
        lambda *_args, **_kwargs: pytest.fail("oversized manifest reached JSON decode"),
    )
    with pytest.raises(ConfigurationError, match="safety limit"):
        load_manifest(oversized)

    monkeypatch.setattr(manifest_module, "_MAX_MANIFEST_BYTES", 8 * 1024 * 1024)
    monkeypatch.undo()
    member_bounded = _write_manifest(
        tmp_path / "members.json",
        json.dumps({"version": 1, "tasks": {"tests": _valid_task()}}),
    )
    monkeypatch.setattr(manifest_module, "_MAX_JSON_OBJECT_MEMBERS", 1)
    with pytest.raises(ConfigurationError, match="too many members"):
        load_manifest(member_bounded)


def test_manifest_bounds_total_decoded_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _write_manifest(
        tmp_path / ".zerorun.json",
        json.dumps({"version": 1, "tasks": {"tests": _valid_task()}}),
    )
    monkeypatch.setattr(manifest_module, "_MAX_JSON_VALUES", 1)
    with pytest.raises(ConfigurationError, match="too many JSON values"):
        load_manifest(path)


@pytest.mark.parametrize("mutation", ["truncate", "grow", "rewrite"])
def test_manifest_rejects_in_place_mutation_during_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / ".zerorun.json"
    original_bytes = json.dumps(
        {"version": 1, "tasks": {"tests": _valid_task()}}
    ).encode("utf-8")
    path.write_bytes(original_bytes)
    real_read = manifest_module.os.read
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
                replacement = b"[" + original_bytes[1:]
            path.write_bytes(replacement)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return real_read(descriptor, size)

    monkeypatch.setattr(manifest_module.os, "read", racing_read)
    with pytest.raises(ConfigurationError, match="changed while it was being read"):
        load_manifest(path)
