"""Predetermined key-boundary matrix, not task execution/correctness evidence."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import stat

import pytest

from research.sqj.strengthening.inventory_oracle import (
    InventoryRefusal, explicit_inventory, inventory_digest,
)
from zerorun.hermetic_key import task_fingerprint_v2
from zerorun.model import ConfigurationError, Manifest, TaskSpec


IMAGE = "example.invalid/research@sha256:" + "a" * 64
RUNTIME = {"runtime": "docker", "requested_image": IMAGE,
           "platform": "linux/amd64", "image_id": "sha256:" + "b" * 64}
ENVIRONMENT = {"LANG": {"present": True,
                        "sha256": hashlib.sha256(b"C").hexdigest()}}
FILES = {
    "src/module.py": b"VALUE = 1\n",
    "src/helper.py": b"def helper():\n    return 1\n",
    "tests/conftest.py": b"FIXTURE_VALUE = 1\n",
    "tests/test_module.py": b"def test_example():\n    assert True\n",
    "pytest.ini": b"[pytest]\naddopts = -q\n",
    "data/fixture.txt": b"fixture-v1\n",
}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    for relative, data in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    task = TaskSpec(name="boundary", command=("python", "-m", "pytest", "tests"),
                    inputs=("src", "tests", "pytest.ini", "data"), outputs=(),
                    cacheable=True, cache_streams=False, result_only=True,
                    closure_reviewed=True, image=IMAGE, platform="linux/amd64")
    # In-memory fixture only: no manifest file, authority, cache store or Docker.
    manifest = Manifest(root, root / ".zerorun.json", {task.name: task}, version=2)
    return manifest, task


def pair(project, *, task=None, runtime=None, environment=None):
    manifest, original_task = project
    task = original_task if task is None else task
    runtime = RUNTIME if runtime is None else runtime
    environment = ENVIRONMENT if environment is None else environment
    independent = inventory_digest(manifest.root, task.inputs, command=task.command,
        task_name=task.name, runtime_identity=runtime, environment_identity=environment)
    production, _ = task_fingerprint_v2(manifest, task, runtime,
                                        environment_fingerprint=environment)
    # Distinct representations are intentional; compare transitions, not bytes.
    assert independent != production
    return independent, production


def changed(before, after):
    assert tuple(a != b for a, b in zip(before, after, strict=True)) == (True, True)


def test_exact_repeat(project):
    assert pair(project) == pair(project)


@pytest.mark.parametrize("relative", tuple(FILES))
def test_content_change_and_exact_restoration(project, relative):
    manifest, _ = project
    source = manifest.root / relative
    before = pair(project)
    original_stat = source.stat()
    # Same byte length and restored mtime rule out a size/mtime-only shortcut.
    # This is an inventory challenge; the mutated fixture is never executed.
    raw = FILES[relative]
    source.write_bytes(bytes((raw[0] ^ 1,)) + raw[1:])
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    changed(before, pair(project))
    source.write_bytes(FILES[relative])
    assert pair(project) == before


@pytest.mark.parametrize("operation", ("add-file", "delete-file", "rename-file", "add-empty-directory"))
def test_directory_membership_and_restoration(project, operation):
    manifest, _ = project
    root = manifest.root
    before = pair(project)
    if operation == "add-file":
        path = root / "src/new_module.py"
        path.write_bytes(b"NEW = True\n")
        changed(before, pair(project))
        path.unlink()
    elif operation == "delete-file":
        path = root / "src/helper.py"
        path.unlink()
        changed(before, pair(project))
        path.write_bytes(FILES["src/helper.py"])
    elif operation == "rename-file":
        source, destination = root / "src/helper.py", root / "src/renamed.py"
        source.rename(destination)
        changed(before, pair(project))
        destination.rename(source)
    else:
        path = root / "data/empty"
        path.mkdir()
        changed(before, pair(project))
        path.rmdir()
    assert pair(project) == before


@pytest.mark.parametrize("arguments", (("-q",), ("-k", "example"), ("--maxfail=1",)))
def test_command_argument_change(project, arguments):
    _, task = project
    changed(pair(project), pair(project, task=replace(task, command=task.command + arguments)))


@pytest.mark.parametrize("relative", ("src/module.py", "src", "."))
def test_posix_file_directory_and_root_modes(project, relative):
    if os.name != "posix":
        pytest.skip("POSIX file/directory permission semantics require a POSIX host")
    manifest, _ = project
    path = manifest.root / relative
    mode = stat.S_IMODE(path.stat().st_mode)
    before = pair(project)
    try:
        # Preserve the research user's read/traverse access while changing a
        # genuinely keyed permission. Removing owner traversal is a refusal
        # challenge below, not a request for a comparable readable key.
        path.chmod(mode ^ stat.S_IXGRP)
        changed(before, pair(project))
    finally:
        path.chmod(mode)
    assert pair(project) == before


def test_implicit_parent_mode(project):
    if os.name != "posix":
        pytest.skip("POSIX directory permission semantics require a POSIX host")
    manifest, original = project
    task = replace(original, inputs=tuple(FILES))
    before = pair(project, task=task)
    parent = manifest.root / "src"
    mode = stat.S_IMODE(parent.stat().st_mode)
    try:
        parent.chmod(mode ^ stat.S_IXGRP)
        changed(before, pair(project, task=task))
    finally:
        parent.chmod(mode)


@pytest.mark.parametrize("boundary", ("declared-directory", "root", "implicit-parent"))
def test_nontraversable_directory_refused_by_both(project, boundary, record_property):
    if os.name != "posix":
        pytest.skip("POSIX directory traversal semantics require a POSIX host")
    # Running as root would bypass the challenged permission and invalidate
    # this experiment; never silently claim a successful denial in that case.
    assert os.geteuid() != 0, "run the permission-refusal matrix as a non-root user"
    manifest, original = project
    task = replace(original, inputs=tuple(FILES)) if boundary == "implicit-parent" else original
    directory = manifest.root if boundary == "root" else manifest.root / "src"
    details = directory.stat()
    assert details.st_uid == os.geteuid(), "fixture must be owned by the test user"
    mode = stat.S_IMODE(details.st_mode)
    assert mode & stat.S_IXUSR, "fixture must initially be traversable"
    try:
        directory.chmod(mode & ~stat.S_IXUSR)
        with pytest.raises(InventoryRefusal) as independent:
            inventory_digest(manifest.root, task.inputs, command=task.command,
                task_name=task.name, runtime_identity=RUNTIME, environment_identity=ENVIRONMENT)
        # Production path validation may expose an OS-level permission error
        # before its ConfigurationError wrapper. Both mean no key was issued;
        # preserve the exact observed type rather than relabeling it.
        with pytest.raises((ConfigurationError, PermissionError)) as production:
            task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=ENVIRONMENT)
        record_property("independent_exception", type(independent.value).__name__)
        record_property("production_exception", type(production.value).__name__)
    finally:
        directory.chmod(mode)


@pytest.mark.parametrize("relative", ("src/module.py", "src", "."))
def test_mtime_only_is_ignored_under_normalized_metadata_contract(project, relative):
    manifest, _ = project
    path = manifest.root / relative
    details = path.stat()
    before = pair(project)
    os.utime(path, ns=(details.st_atime_ns, details.st_mtime_ns + 10_000_000_000))
    assert path.stat().st_mtime_ns != details.st_mtime_ns
    assert pair(project) == before


@pytest.mark.parametrize("relative", ("missing.py", "absent/nested.py"))
def test_missing_literal_input_refused_by_both(project, relative):
    manifest, original = project
    task = replace(original, inputs=original.inputs + (relative,))
    with pytest.raises(InventoryRefusal):
        inventory_digest(manifest.root, task.inputs, command=task.command,
            task_name=task.name, runtime_identity=RUNTIME, environment_identity=ENVIRONMENT)
    with pytest.raises(ConfigurationError):
        task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=ENVIRONMENT)


@pytest.mark.parametrize("target_is_directory", (False, True))
def test_symlink_input_refused_by_both(project, target_is_directory):
    manifest, original = project
    link = manifest.root / "linked-input"
    target = manifest.root / ("src" if target_is_directory else "src/module.py")
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"host cannot create symbolic links: {type(exc).__name__}")
    task = replace(original, inputs=original.inputs + (link.name,))
    with pytest.raises(InventoryRefusal):
        inventory_digest(manifest.root, task.inputs, command=task.command,
            task_name=task.name, runtime_identity=RUNTIME, environment_identity=ENVIRONMENT)
    with pytest.raises(ConfigurationError):
        task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=ENVIRONMENT)


def test_supplied_runtime_identity_change(project):
    changed(pair(project), pair(project, runtime={**RUNTIME, "image_id": "sha256:" + "c" * 64}))


def test_supplied_environment_identity_change(project):
    changed(pair(project), pair(project, environment={"LANG": {"present": False}}))


def test_declaration_order_and_duplicate_inputs_do_not_change_material(project):
    _, task = project
    assert pair(project) == pair(project, task=replace(task, inputs=tuple(reversed(task.inputs)) + task.inputs))


def test_undeclared_new_top_level_file_is_not_a_completeness_claim(project):
    manifest, _ = project
    before = pair(project)
    (manifest.root / "new_top_level_configuration.py").write_bytes(b"VALUE = 2\n")
    assert pair(project) == before
    # This passing equality demonstrates the declared-boundary limitation. It
    # does not establish that real execution could not depend on the new file.


@pytest.mark.parametrize("declaration", ("src/*.py", "../outside", "/absolute", "src/../data", ".git", ".zerorun"))
def test_oracle_explicit_scope_refusals(project, declaration):
    manifest, task = project
    with pytest.raises(InventoryRefusal):
        explicit_inventory(manifest.root, (declaration,), command=task.command,
            task_name=task.name, runtime_identity=RUNTIME, environment_identity=ENVIRONMENT)
    # Deliberately not compared with production: its supported declaration
    # language is broader, and this oracle does not claim to implement it.
