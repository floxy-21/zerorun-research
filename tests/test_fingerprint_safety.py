from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from zerorun import fingerprint, runner, store as store_module
from zerorun.manifest import load_manifest
from zerorun.model import ConfigurationError
from zerorun.store import OutputTransaction, Store


@pytest.mark.parametrize(
    "declared_input,output,create_output",
    [
        ("generated/out.txt", "generated/out.txt", True),
        ("generated", "generated/out.txt", True),
        ("generated/**/*.txt", "generated/out.txt", False),
    ],
)
def test_cacheable_fingerprint_rejects_output_input_overlap(
    tmp_path: Path,
    declared_input: str,
    output: str,
    create_output: bool,
) -> None:
    (tmp_path / "task.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    if create_output:
        (tmp_path / output).write_text("old\n", encoding="utf-8")
    manifest_path = tmp_path / ".zerorun.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "work": {
                        "command": [sys.executable, "task.py"],
                        "inputs": ["task.py", declared_input],
                        "outputs": [output],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)

    with pytest.raises(ConfigurationError, match="cacheable output.*covered"):
        fingerprint.task_fingerprint(
            manifest,
            manifest.tasks["work"],
            environment=fingerprint.controlled_environment(manifest.tasks["work"]),
        )


def test_cacheable_fingerprint_accepts_non_overlapping_inputs_and_outputs(
    tmp_path: Path,
) -> None:
    (tmp_path / "task.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "input.txt").write_text("input\n", encoding="utf-8")
    (tmp_path / "generated").mkdir()
    manifest_path = tmp_path / ".zerorun.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "work": {
                        "command": [sys.executable, "task.py"],
                        "inputs": ["task.py", "input.txt", "generated/**/*.src"],
                        "outputs": ["generated/out.txt"],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)

    key, payload = fingerprint.task_fingerprint(
        manifest,
        manifest.tasks["work"],
        environment=fingerprint.controlled_environment(manifest.tasks["work"]),
    )
    assert len(key) == 64
    assert payload["outputs"] == ["generated/out.txt"]


@pytest.mark.parametrize("reader", ["bytes", "sha256"])
@pytest.mark.parametrize("mutation", ["truncate", "grow", "rewrite"])
def test_descriptor_bound_fingerprint_reads_reject_in_place_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reader: str,
    mutation: str,
) -> None:
    path = tmp_path / "input.py"
    original_bytes = b"VALUE = 1\n"
    path.write_bytes(original_bytes)
    real_read = fingerprint.os.read
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
                replacement = b"VALUE = 2\n"
            path.write_bytes(replacement)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return real_read(descriptor, size)

    monkeypatch.setattr(fingerprint.os, "read", racing_read)
    with pytest.raises(ConfigurationError, match="changed while it was being read"):
        if reader == "bytes":
            fingerprint.read_stable_file_bytes(path)
        else:
            fingerprint.sha256_file(path)


def test_path_record_rejects_change_between_metadata_and_descriptor_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "input.py"
    original_bytes = b"VALUE = 1\n"
    path.write_bytes(original_bytes)
    real_sha256 = fingerprint.sha256_file

    def mutate_then_hash(candidate: Path) -> str:
        before = path.stat()
        path.write_bytes(b"VALUE = 2\n")
        os.utime(
            path,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        return real_sha256(candidate)

    monkeypatch.setattr(fingerprint, "sha256_file", mutate_then_hash)
    with pytest.raises(ConfigurationError, match="changed while it was being recorded"):
        fingerprint._path_record(tmp_path, path)


def test_legacy_hit_takes_second_snapshot_before_output_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "task.py").write_text(
        "from pathlib import Path\n"
        "Path('out.txt').write_text(Path('input.txt').read_text())\n",
        encoding="utf-8",
    )
    source = tmp_path / "input.txt"
    source.write_text("alpha\n", encoding="utf-8")
    manifest_path = tmp_path / ".zerorun.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "work": {
                        "command": [sys.executable, "task.py"],
                        "inputs": ["task.py", "input.txt"],
                        "outputs": ["out.txt"],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    task = manifest.tasks["work"]
    first = runner.run_task(
        manifest,
        task,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )
    assert first.status == "MISS_EXECUTED"

    output = tmp_path / "out.txt"
    output.write_text("user-preexisting\n", encoding="utf-8")
    real_streams = Store.streams
    real_fingerprint = runner.task_fingerprint
    fingerprint_calls = 0
    mutated = False

    def mutate_after_cache_validation(self, *args, **kwargs):
        nonlocal mutated
        streams = real_streams(self, *args, **kwargs)
        if not mutated:
            mutated = True
            source.write_text("beta\n", encoding="utf-8")
        return streams

    def counted_fingerprint(*args, **kwargs):
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return real_fingerprint(*args, **kwargs)

    monkeypatch.setattr(Store, "streams", mutate_after_cache_validation)
    monkeypatch.setattr(runner, "task_fingerprint", counted_fingerprint)
    raced = runner.run_task(
        manifest,
        task,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )

    assert raced.status == "REJECTED_INPUT_RACE"
    assert raced.exit_code == 75
    assert fingerprint_calls == 2
    assert output.read_text(encoding="utf-8") == "user-preexisting\n"


def test_fresh_execution_rechecks_inputs_at_output_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "task.py").write_text(
        "from pathlib import Path\n"
        "Path('out.txt').write_text(Path('input.txt').read_text())\n",
        encoding="utf-8",
    )
    source = tmp_path / "input.txt"
    source.write_text("alpha\n", encoding="utf-8")
    output = tmp_path / "out.txt"
    output.write_text("user-preexisting\n", encoding="utf-8")
    manifest_path = tmp_path / ".zerorun.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "work": {
                        "command": [sys.executable, "task.py"],
                        "inputs": ["task.py", "input.txt"],
                        "outputs": ["out.txt"],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    task = manifest.tasks["work"]
    original_key, original_fingerprint = fingerprint.task_fingerprint(
        manifest,
        task,
        environment=fingerprint.controlled_environment(task),
    )
    real_prepare = OutputTransaction.prepare
    mutated = False

    def mutate_during_output_prepare(self):
        nonlocal mutated
        real_prepare(self)
        if not mutated:
            mutated = True
            source.write_text("beta\n", encoding="utf-8")

    monkeypatch.setattr(OutputTransaction, "prepare", mutate_during_output_prepare)
    raced = runner.run_task(
        manifest,
        task,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )

    assert mutated is True
    assert raced.status == "REJECTED_INPUT_RACE"
    assert raced.exit_code == 75
    assert raced.cache_key == original_key
    assert output.read_text(encoding="utf-8") == "user-preexisting\n"

    # Saving precedes publication on a first execution. A rejected race may
    # therefore leave a complete entry, but it must remain bound only to the
    # original input snapshot and never satisfy the now-current key.
    store = Store(tmp_path)
    assert (
        store.validated_metadata(
            manifest,
            task,
            original_key,
            original_fingerprint,
        )
        is not None
    )
    current_key, current_fingerprint = fingerprint.task_fingerprint(
        manifest,
        task,
        environment=fingerprint.controlled_environment(task),
    )
    assert current_key != original_key
    assert (
        store.validated_metadata(
            manifest,
            task,
            current_key,
            current_fingerprint,
        )
        is None
    )

    rerun = runner.run_task(
        manifest,
        task,
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )
    assert rerun.status == "MISS_EXECUTED"
    assert rerun.cache_key == current_key
    assert output.read_text(encoding="utf-8") == "beta\n"


@pytest.mark.parametrize("mutation_point", ["first", "last"])
def test_fresh_output_commit_rolls_back_multi_output_replacement_races(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation_point: str,
) -> None:
    (tmp_path / "task.py").write_text(
        "from pathlib import Path\n"
        "value = Path('input.txt').read_text()\n"
        "Path('out-a.txt').write_text('a:' + value)\n"
        "Path('out-b.txt').write_text('b:' + value)\n",
        encoding="utf-8",
    )
    source = tmp_path / "input.txt"
    source.write_text("alpha\n", encoding="utf-8")
    output_a = tmp_path / "out-a.txt"
    output_b = tmp_path / "out-b.txt"
    output_a.write_text("old-a\n", encoding="utf-8")
    output_b.write_text("old-b\n", encoding="utf-8")
    manifest_path = tmp_path / ".zerorun.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": {
                    "work": {
                        "command": [sys.executable, "task.py"],
                        "inputs": ["task.py", "input.txt"],
                        "outputs": ["out-a.txt", "out-b.txt"],
                        "cacheable": True,
                        "cache_streams": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_path)
    real_replace = store_module.os.replace
    mutation_target = output_a if mutation_point == "first" else output_b
    mutated = False

    def mutate_after_output_replacement(source_path, destination_path):
        nonlocal mutated
        result = real_replace(source_path, destination_path)
        if not mutated and Path(destination_path) == mutation_target:
            mutated = True
            source.write_text("beta\n", encoding="utf-8")
        return result

    monkeypatch.setattr(store_module.os, "replace", mutate_after_output_replacement)
    raced = runner.run_task(
        manifest,
        manifest.tasks["work"],
        stdout=io.BytesIO(),
        stderr=io.BytesIO(),
    )

    assert mutated is True
    assert raced.status == "REJECTED_INPUT_RACE"
    assert raced.exit_code == 75
    assert output_a.read_text(encoding="utf-8") == "old-a\n"
    assert output_b.read_text(encoding="utf-8") == "old-b\n"
