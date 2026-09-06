from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import secrets
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Sequence

# When this file is executed by path, Python otherwise searches ``tools/``
# before the adjacent product package.  Make the exact source tree containing
# this trial tool authoritative, then attest that location below.
_ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

import zerorun as _engine_package
from zerorun import __version__ as _engine_version
from zerorun.manifest import load_manifest
from zerorun.model import ConfigurationError
from zerorun.oci import (
    DOCKER_RESOURCE_ARGS,
    _force_remove_container,
    _run_bounded_process,
    reject_sourceless_workspace_bytecode,
)
from zerorun.path_safety import private_temporary_directory
from zerorun.pytest_prepare_cli import prepare_candidate
from zerorun.pytest_profile import load_pytest_profile
from zerorun.pytest_review import (
    activate_reviewed_candidate,
    write_review_template,
)
from zerorun.pytest_runtime import run_pytest_profile
from zerorun.trust import cache_payload_is_trusted, manifest_sha256


GENERALIZATION_RUNTIME_REQUIREMENTS = (
    Path(__file__).resolve().parents[1]
    / "ci"
    / "generalization-runtime-requirements.txt"
)
GENERALIZATION_RUNTIME_IMAGE = (
    "docker.io/library/python@sha256:"
    "9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
)
GENERALIZATION_RUNTIME_IMPLEMENTATION = "CPython"
GENERALIZATION_RUNTIME_PYTHON_VERSION = "3.12.14"
_GENERALIZATION_RUNTIME_IMPLEMENTATION_ID = "cpython"
_RUNTIME_INSTALL_BOOTSTRAP = (
    "import runpy,sys;"
    "expected_implementation=sys.argv.pop(1);"
    "expected_version=sys.argv.pop(1);"
    "actual_implementation=sys.implementation.name;"
    "actual_version='.'.join(str(part) for part in sys.version_info[:3]);"
    "(actual_implementation==expected_implementation and actual_version==expected_version) "
    "or sys.exit('runtime interpreter mismatch: expected %s %s, got %s %s' % "
    "(expected_implementation,expected_version,actual_implementation,actual_version));"
    "sys.argv[0]='pip';"
    "runpy.run_module('pip',run_name='__main__')"
)
_LOCKED_EXTRA_REQUIREMENTS = frozenset({"pretend"})
_DEPENDENCY_LAYER_SCHEMA = "zerorun.generalization-dependency-layer.v1"
_DEPENDENCY_TREE_SCHEMA = "zerorun.generalization-dependency-tree.v1"
_MAX_DEPENDENCY_LAYER_FILES = 100_000
_MAX_DEPENDENCY_LAYER_DIRECTORIES = 25_000
_MAX_DEPENDENCY_LAYER_BYTES = 1024 * 1024 * 1024
_MAX_DEPENDENCY_LAYER_PATH_BYTES = 16 * 1024 * 1024
_MAX_DEPENDENCY_LAYER_RELATIVE_PATH_BYTES = 4096
_MAX_DEPENDENCY_LAYER_DEPTH = 128
_COMMAND_TIMEOUT_SECONDS = 60
_DOCKER_CONTROL_TIMEOUT_SECONDS = 30
_DOCKER_BUILD_TIMEOUT_SECONDS = 300
_MAX_COMMAND_OUTPUT_BYTES = 4096
_MAX_ENGINE_PYTHON_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_SHADOW_EVIDENCE_BYTES = 32 * 1024 * 1024
_MAX_SHADOW_NODES = 25_000
_MAX_SHADOW_NODEID_CHARS = 4_000_000
_PYTEST_EXECUTION_TIMEOUT_SECONDS = 900
_METHODOLOGY_VERSION = "zerorun.product-generalization-counterbalanced-e2e.v2"
_TRAJECTORY_ORDER_LABELS = ("plain-then-zerorun", "zerorun-then-plain")
_PRIMARY_COST_LEDGER_KEYS = (
    "symmetric_environment_bootstrap_charged_to_each_arm",
    "direct_seed_charged_to_direct",
    "zerorun_qualification_charged_to_zerorun",
    "zerorun_activation_charged_to_zerorun",
    "zerorun_seed_charged_to_zerorun",
    "automatic_refresh_charged_to_zerorun",
)
_PLAIN_DOCKER_RESOURCE_ARGS = (
    "--cpus",
    "2",
    "--memory",
    "2g",
    "--memory-swap",
    "2g",
    "--pids-limit",
    "512",
)
_BENCHMARK_STATE_NAMES = (
    ".zerorun",
    ".zerorun-env",
    ".zerorun.json",
    ".zerorun-pytest.candidate.json",
    ".zerorun-pytest.review.json",
    ".zerorun-pytest.json",
)
_COMPARISON_COVERAGE_BASIS = (
    "the independent shadow executed the exact full collection; therefore every "
    "reused node is covered when all per-node shadow outcomes are complete and "
    "non-failing; because the product does not export reused node IDs, any shadow "
    "failure makes a reuse-bearing observation invalid"
)

_RUNTIME_WRAPPER_BYTES = (
    "#!/bin/sh\n"
    "set -eu\n"
    "export PYTHONDONTWRITEBYTECODE=1\n"
    # Preserve an invocation-specific support/plugin path first, then make the
    # frozen checkout authoritative over packages installed only to supply the
    # benchmark's locked third-party runtime dependencies.
    "export PYTHONPATH=\"${PYTHONPATH:+$PYTHONPATH:}"
    "/workspace/src:/workspace:/workspace/.zerorun-env/site-packages\"\n"
    "exec /usr/local/bin/python \"$@\"\n"
).encode("utf-8")
_RUNTIME_INSTALL_ARGUMENTS = (
    "install",
    "--disable-pip-version-check",
    "--no-input",
    "--no-cache-dir",
    "--no-compile",
    "--require-hashes",
    "--only-binary=:all:",
    "--target",
    "/zerorun-env/site-packages",
    "--requirement",
    "/zerorun-env/runtime-requirements.txt",
)


# This deliberately small oracle is benchmark-owned rather than imported from
# ZeroRun.  It runs only in an untimed full-fresh pytest shadow.  The primary
# plain-pytest arm has no plugin at all, so oracle instrumentation cannot inflate
# the performance baseline.  Every collected node receives one conservative
# terminal classification; any malformed, missing, or failing outcome makes a
# reuse-bearing observation ineligible for the safety gate.
_SHADOW_PLUGIN = r'''
import hashlib
import json
import os

_nodeids = []
_outcomes = {}


def pytest_collection_finish(session):
    global _nodeids
    _nodeids = [str(item.nodeid) for item in session.items]


def pytest_runtest_logreport(report):
    nodeid = str(report.nodeid)
    row = _outcomes.setdefault(
        nodeid,
        {"setup": None, "call": None, "teardown": None, "wasxfail": False},
    )
    when = str(report.when)
    if when in row:
        row[when] = str(report.outcome)
    row["wasxfail"] = bool(getattr(report, "wasxfail", False))


def pytest_sessionfinish(session, exitstatus):
    output = os.environ["ZERORUN_BENCHMARK_SHADOW_OUTPUT"]
    canonical = json.dumps(
        {"nodeids": _nodeids},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    payload = {
        "schema": "zerorun.benchmark-independent-pytest-shadow.v1",
        "exit_code": int(exitstatus),
        "nodeids": _nodeids,
        "nodeid_sha256": hashlib.sha256(canonical).hexdigest(),
        "outcomes": [_outcomes.get(nodeid) for nodeid in _nodeids],
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    with open(output, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
'''


class _SourceIdentityError(ConfigurationError):
    def __init__(
        self,
        message: str,
        *,
        pre: dict[str, Any] | None = None,
        post: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.source_identity_pre = pre
        self.source_identity_post = post


def _runtime_requirements_sha256() -> str:
    try:
        raw = GENERALIZATION_RUNTIME_REQUIREMENTS.read_bytes()
    except OSError as exc:
        raise ConfigurationError(
            "could not read the generalization runtime requirements lock: "
            f"{exc}"
        ) from exc
    if not raw or len(raw) > 64 * 1024:
        raise ConfigurationError(
            "generalization runtime requirements lock is empty or oversized"
        )
    return hashlib.sha256(raw).hexdigest()


def _validate_extra_requirements(extra_requirements: Sequence[str]) -> None:
    if (
        len(set(extra_requirements)) != len(extra_requirements)
        or any(requirement not in _LOCKED_EXTRA_REQUIREMENTS for requirement in extra_requirements)
    ):
        raise ConfigurationError(
            "generalization benchmark extras must be distinct members of the "
            "hash-locked runtime closure"
        )


def _command_label(argv: Sequence[str]) -> str:
    label = " ".join(str(part) for part in argv)
    return label if len(label) <= 512 else label[:509] + "..."


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath(
            [os.path.normcase(str(root)), os.path.normcase(str(candidate))]
        ) == os.path.normcase(str(root))
    except ValueError:
        return False


def _host_executable(root: Path, name: str) -> str:
    discovered = shutil.which(name)
    if not discovered:
        raise ConfigurationError(f"generalization benchmark requires host {name}")
    lexical = Path(os.path.abspath(Path(discovered).expanduser()))
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(
            f"could not resolve host {name} executable: {lexical}: {exc}"
        ) from exc
    benchmark_root = root.expanduser().resolve(strict=True)
    if (
        _is_within(benchmark_root, lexical)
        or _is_within(benchmark_root, resolved)
        or not resolved.is_file()
    ):
        raise ConfigurationError(
            f"refusing a repository-controlled host {name} executable: {lexical}"
        )
    return str(resolved)


def _run(
    root: Path,
    argv: Sequence[str],
    *,
    check: bool = False,
    timeout_seconds: int = _COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    command = list(argv)
    if not command:
        raise ConfigurationError("cannot run an empty benchmark command")
    if timeout_seconds <= 0:
        raise ConfigurationError("benchmark command timeout must be positive")
    if not Path(command[0]).is_absolute():
        raise ConfigurationError(
            "benchmark command executable must be an absolute host path"
        )
    try:
        process, timed_out = _run_bounded_process(
            command,
            cwd=root,
            environment=dict(os.environ),
            timeout_seconds=timeout_seconds,
            output_limit_bytes=_MAX_COMMAND_OUTPUT_BYTES,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"could not run benchmark command {_command_label(command)}: {exc}"
        ) from exc
    stdout = process.stdout.decode("utf-8", errors="replace")
    stderr = process.stderr.decode("utf-8", errors="replace")
    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
    if timed_out:
        raise ConfigurationError(
            f"benchmark command timed out after {timeout_seconds}s: "
            f"{_command_label(command)}; stdout tail: {stdout!r}; "
            f"stderr tail: {stderr!r}"
        )
    if check and completed.returncode != 0:
        raise ConfigurationError(
            f"benchmark command failed with exit code {completed.returncode}: "
            f"{_command_label(command)}; stdout tail: {completed.stdout!r}; "
            f"stderr tail: {completed.stderr!r}"
        )
    return completed


class _PlainBoundedTail:
    """Benchmark-owned bounded pipe capture for the independent control arm."""

    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("plain-pytest output limit must be positive")
        self.limit = limit
        self.data = bytearray()

    def append(self, chunk: bytes) -> None:
        self.data.extend(chunk)
        if len(self.data) > self.limit:
            del self.data[: len(self.data) - self.limit]


def _plain_drain(stream, output: _PlainBoundedTail) -> None:
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            output.append(chunk)
    except (OSError, ValueError):
        return


def _plain_stop_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _plain_run(
    root: Path,
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Bounded subprocess runner implemented outside the ZeroRun product path."""

    command = list(argv)
    if not command or not Path(command[0]).is_absolute():
        raise ConfigurationError(
            "independent plain-pytest command requires an absolute executable"
        )
    stdout = _PlainBoundedTail(_MAX_COMMAND_OUTPUT_BYTES)
    stderr = _PlainBoundedTail(_MAX_COMMAND_OUTPUT_BYTES)
    process_group = (
        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **process_group,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"independent plain-pytest command could not start: {exc}"
        ) from exc
    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(
            target=_plain_drain,
            args=(process.stdout, stdout),
            daemon=True,
        ),
        threading.Thread(
            target=_plain_drain,
            args=(process.stderr, stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        _plain_stop_process(process)
        raise ConfigurationError(
            f"independent plain-pytest command timed out after {timeout_seconds}s"
        ) from exc
    except BaseException:
        _plain_stop_process(process)
        raise
    finally:
        deadline = time.monotonic() + 2.0
        for reader in readers:
            reader.join(timeout=max(0.0, deadline - time.monotonic()))
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    if any(reader.is_alive() for reader in readers):
        _plain_stop_process(process)
        raise ConfigurationError(
            "independent plain-pytest output pipes did not close after execution"
        )
    return subprocess.CompletedProcess(
        command,
        int(process.returncode if process.returncode is not None else 1),
        bytes(stdout.data).decode("utf-8", errors="replace"),
        bytes(stderr.data).decode("utf-8", errors="replace"),
    )


def _is_link_like_stat(details: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(details.st_mode) or bool(
        getattr(details, "st_file_attributes", 0) & reparse_flag
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ConfigurationError(f"could not inspect benchmark state path {path}: {exc}") from exc


def _stable_stat_identity(details: os.stat_result) -> tuple[int, ...]:
    return (
        int(details.st_mode),
        int(details.st_size),
        int(details.st_mtime_ns),
        int(details.st_dev),
        int(details.st_ino),
        int(getattr(details, "st_file_attributes", 0)),
    )


def _compatible_entry_stat(
    entry_details: os.stat_result,
    path_details: os.stat_result,
) -> bool:
    """Compare DirEntry/path observations without trusting missing Windows IDs."""

    common_matches = (
        int(entry_details.st_mode) == int(path_details.st_mode)
        and int(entry_details.st_size) == int(path_details.st_size)
        and int(entry_details.st_mtime_ns) == int(path_details.st_mtime_ns)
        and int(getattr(entry_details, "st_file_attributes", 0))
        == int(getattr(path_details, "st_file_attributes", 0))
    )
    entry_identity = (int(entry_details.st_dev), int(entry_details.st_ino))
    path_identity = (int(path_details.st_dev), int(path_details.st_ino))
    if 0 in entry_identity or 0 in path_identity:
        # Python's Windows DirEntry result can omit both stable file IDs and
        # report stale timestamp metadata.  It remains only a type/reparse
        # probe there; the path lstat and full two-pass tree barrier carry the
        # identity proof.
        return (
            stat.S_IFMT(entry_details.st_mode) == stat.S_IFMT(path_details.st_mode)
            and int(getattr(entry_details, "st_file_attributes", 0))
            == int(getattr(path_details, "st_file_attributes", 0))
        )
    return common_matches and entry_identity == path_identity


def _read_identity_file(path: Path, *, relative: str) -> tuple[dict[str, Any], bytes]:
    before = _lstat(path)
    if (
        before is None
        or _is_link_like_stat(before)
        or not stat.S_ISREG(before.st_mode)
    ):
        raise _SourceIdentityError(
            f"source identity requires an ordinary regular file: {relative}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _SourceIdentityError(
            f"could not read source identity file {relative}: {exc}"
        ) from exc
    after = _lstat(path)
    if after is None or _stable_stat_identity(before) != _stable_stat_identity(after):
        raise _SourceIdentityError(
            f"source identity file changed while it was read: {relative}"
        )
    return (
        {
            "path": relative,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        raw,
    )


def _engine_python_source_rows() -> list[dict[str, Any]]:
    package_root = _ENGINE_ROOT / "zerorun"
    root_details = _lstat(package_root)
    if (
        root_details is None
        or _is_link_like_stat(root_details)
        or not stat.S_ISDIR(root_details.st_mode)
    ):
        raise _SourceIdentityError(
            "engine source identity requires an ordinary zerorun package directory"
        )

    pending = [package_root]
    paths: list[Path] = []
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError as exc:
            raise _SourceIdentityError(
                f"could not enumerate engine Python sources under {current}: {exc}"
            ) from exc
        for entry in entries:
            child = Path(entry.path)
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise _SourceIdentityError(
                    f"could not inspect engine source entry {child}: {exc}"
                ) from exc
            if _is_link_like_stat(details):
                raise _SourceIdentityError(
                    "engine source identity refuses linked or reparse-point entries: "
                    + child.relative_to(_ENGINE_ROOT).as_posix()
                )
            if stat.S_ISDIR(details.st_mode):
                pending.append(child)
            elif stat.S_ISREG(details.st_mode) and child.suffix == ".py":
                paths.append(child)

    paths.sort(
        key=lambda path: (
            path.relative_to(_ENGINE_ROOT).as_posix().casefold(),
            path.relative_to(_ENGINE_ROOT).as_posix(),
        )
    )
    if not paths:
        raise _SourceIdentityError("engine source identity found no Python sources")
    relative_paths = [path.relative_to(_ENGINE_ROOT).as_posix() for path in paths]
    if len({path.casefold() for path in relative_paths}) != len(relative_paths):
        raise _SourceIdentityError(
            "engine Python source paths collide under case-insensitive normalization"
        )

    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path, relative in zip(paths, relative_paths, strict=True):
        row, raw = _read_identity_file(path, relative=relative)
        total_bytes += len(raw)
        if total_bytes > _MAX_ENGINE_PYTHON_SOURCE_BYTES:
            raise _SourceIdentityError(
                "engine Python sources exceed the bounded identity input size"
            )
        rows.append(row)
    return rows


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _git_source_identity() -> dict[str, Any]:
    git = _host_executable(_ENGINE_ROOT, "git")
    head = _run(
        _ENGINE_ROOT,
        [git, "rev-parse", "--verify", "HEAD"],
        check=True,
    ).stdout.strip()
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise _SourceIdentityError(
            "engine repository returned a malformed exact commit identity"
        )

    def dirty(pathspec: str) -> bool:
        result = _run(
            _ENGINE_ROOT,
            [
                git,
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                pathspec,
            ],
        )
        if result.returncode != 0:
            raise _SourceIdentityError(
                f"could not inspect engine source Git state for {pathspec}: "
                + result.stderr
            )
        return bool(result.stdout.strip())

    return {
        "commit": head,
        "engine_python_sources_dirty": dirty("zerorun"),
        "trial_tool_dirty": dirty("tools/product_generalization_benchmark.py"),
    }


def _capture_source_identity() -> dict[str, Any]:
    expected_package_root = (_ENGINE_ROOT / "zerorun").resolve(strict=True)
    try:
        loaded_package_root = Path(_engine_package.__file__).resolve(strict=True).parent
    except (OSError, TypeError) as exc:
        raise _SourceIdentityError(
            f"could not identify the loaded ZeroRun package: {exc}"
        ) from exc
    if loaded_package_root != expected_package_root:
        raise _SourceIdentityError(
            "benchmark imported ZeroRun outside the trial tool's exact source tree: "
            f"loaded={loaded_package_root}, expected={expected_package_root}"
        )

    engine_rows = _engine_python_source_rows()
    engine_payload = {
        "schema": "zerorun.engine-python-source-identity.v1",
        "files": engine_rows,
    }
    engine_identity = {
        **engine_payload,
        "file_count": len(engine_rows),
        "total_bytes": sum(int(row["size"]) for row in engine_rows),
        "sha256": _canonical_sha256(engine_payload),
    }
    trial_tool, _ = _read_identity_file(
        Path(__file__).resolve(strict=True),
        relative="tools/product_generalization_benchmark.py",
    )
    git_identity = _git_source_identity()
    payload = {
        "schema": "zerorun.product-generalization-source-identity.v1",
        "engine_version": _engine_version,
        "engine_python_source": engine_identity,
        "trial_tool": trial_tool,
        "git": git_identity,
    }
    return {**payload, "identity_sha256": _canonical_sha256(payload)}


def _validate_sha256(value: str | None, *, label: str) -> None:
    if value is not None and re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise _SourceIdentityError(f"{label} must be 64 lowercase hexadecimal characters")


def _validate_caller_source_identity(
    identity: dict[str, Any],
    *,
    engine_sha: str | None,
    expected_engine_source_sha256: str | None,
    expected_trial_tool_sha256: str | None,
) -> str:
    try:
        _validate_sha256(
            expected_engine_source_sha256,
            label="expected engine Python-source SHA-256",
        )
        _validate_sha256(
            expected_trial_tool_sha256,
            label="expected trial-tool SHA-256",
        )
    except _SourceIdentityError as exc:
        exc.source_identity_pre = identity
        exc.source_identity_post = None
        raise
    actual_commit = str(identity["git"]["commit"])
    if engine_sha is not None and re.fullmatch(r"[0-9a-f]{40}", engine_sha) is None:
        raise _SourceIdentityError(
            "caller engine commit identity must be 40 lowercase hexadecimal characters",
            pre=identity,
            post=None,
        )
    if engine_sha is not None and engine_sha != actual_commit:
        raise _SourceIdentityError(
            "caller engine commit identity does not match the trial source tree: "
            f"expected={engine_sha!r}, actual={actual_commit!r}",
            pre=identity,
            post=None,
        )

    actual_engine = str(identity["engine_python_source"]["sha256"])
    actual_tool = str(identity["trial_tool"]["sha256"])
    if (
        expected_engine_source_sha256 is not None
        and expected_engine_source_sha256 != actual_engine
    ):
        raise _SourceIdentityError(
            "caller engine Python-source identity does not match the loaded product: "
            f"expected={expected_engine_source_sha256}, actual={actual_engine}",
            pre=identity,
            post=None,
        )
    if expected_trial_tool_sha256 is not None and expected_trial_tool_sha256 != actual_tool:
        raise _SourceIdentityError(
            "caller trial-tool identity does not match the executing harness: "
            f"expected={expected_trial_tool_sha256}, actual={actual_tool}",
            pre=identity,
            post=None,
        )

    if (
        identity["git"]["engine_python_sources_dirty"]
        and expected_engine_source_sha256 is None
    ):
        raise _SourceIdentityError(
            "dirty engine Python sources require an explicit matching "
            "--engine-source-sha256 identity",
            pre=identity,
            post=None,
        )
    if identity["git"]["trial_tool_dirty"] and expected_trial_tool_sha256 is None:
        raise _SourceIdentityError(
            "a dirty trial tool requires an explicit matching --trial-tool-sha256 identity",
            pre=identity,
            post=None,
        )
    return actual_commit


def _require_stable_source_identity(
    pre: dict[str, Any],
    post: dict[str, Any],
) -> None:
    if pre != post:
        raise _SourceIdentityError(
            "engine Python source or trial-tool identity drifted during the benchmark: "
            f"pre={pre.get('identity_sha256')}, post={post.get('identity_sha256')}",
            pre=pre,
            post=post,
        )


def _capture_post_source_identity(
    pre: dict[str, Any],
) -> dict[str, Any]:
    try:
        return _capture_source_identity()
    except Exception as exc:
        raise _SourceIdentityError(
            "could not capture the post-trial source identity; the benchmark "
            f"is rejected: {type(exc).__name__}: {exc}",
            pre=pre,
            post=None,
        ) from exc


def _cleanup_inode_identity(details: os.stat_result) -> tuple[int, int, int]:
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(stat.S_IFMT(details.st_mode)),
    )


def _validate_plain_cleanup_entry(
    details: os.stat_result,
    *,
    path: Path | str,
) -> bool:
    """Validate one cleanup entry and return whether it is a directory."""

    if _is_link_like_stat(details):
        raise ConfigurationError(
            f"refusing cleanup of linked or reparse-point benchmark state: {path}"
        )
    if stat.S_ISDIR(details.st_mode):
        return True
    if not stat.S_ISREG(details.st_mode):
        raise ConfigurationError(
            f"refusing cleanup of special benchmark state: {path}"
        )
    if int(details.st_nlink) != 1:
        raise ConfigurationError(
            f"refusing cleanup of multiply linked benchmark state: {path}"
        )
    return False


def _assert_plain_cleanup_tree(path: Path) -> None:
    root_details = _lstat(path)
    if root_details is None:
        raise ConfigurationError(
            f"benchmark state cleanup target disappeared before inspection: {path}"
        )
    if not _validate_plain_cleanup_entry(root_details, path=path):
        return
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                rows = list(entries)
        except OSError as exc:
            raise ConfigurationError(
                f"could not inspect benchmark state tree before cleanup: {current}: {exc}"
            ) from exc
        for entry in rows:
            child = Path(entry.path)
            try:
                entry_details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ConfigurationError(
                    f"could not inspect benchmark state entry before cleanup: {child}: {exc}"
                ) from exc
            details = _lstat(child)
            if details is None or not _compatible_entry_stat(entry_details, details):
                raise ConfigurationError(
                    f"benchmark state entry changed during cleanup inspection: {child}"
                )
            if _validate_plain_cleanup_entry(details, path=child):
                pending.append(child)


def _chmod_plain_cleanup_entry(path: Path, *, directory: bool) -> None:
    before = _lstat(path)
    if before is None or _validate_plain_cleanup_entry(before, path=path) != directory:
        raise ConfigurationError(
            f"benchmark cleanup entry changed kind before chmod: {path}"
        )
    identity = _cleanup_inode_identity(before)
    mode = stat.S_IMODE(before.st_mode) | stat.S_IWUSR
    if directory:
        mode |= stat.S_IXUSR
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        if os.name != "nt":
            raise
        # Windows may not expose no-follow chmod. Re-attest immediately before
        # the platform call and reject any detectable replacement afterward.
        immediate = _lstat(path)
        if (
            immediate is None
            or _cleanup_inode_identity(immediate) != identity
            or _validate_plain_cleanup_entry(immediate, path=path) != directory
        ):
            raise ConfigurationError(
                f"benchmark cleanup entry changed before Windows chmod: {path}"
            )
        os.chmod(path, mode)
    after = _lstat(path)
    if (
        after is None
        or _cleanup_inode_identity(after) != identity
        or _validate_plain_cleanup_entry(after, path=path) != directory
    ):
        raise ConfigurationError(
            f"benchmark cleanup entry changed while made writable: {path}"
        )


def _make_plain_cleanup_tree_writable(path: Path) -> None:
    """Make an already-vetted private tree removable without following links."""

    directories = [path]
    files: list[Path] = []
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                rows = list(entries)
        except OSError as exc:
            raise ConfigurationError(
                f"could not inspect read-only benchmark state: {current}: {exc}"
            ) from exc
        for entry in rows:
            child = Path(entry.path)
            try:
                entry_details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ConfigurationError(
                    f"could not inspect read-only benchmark state entry {child}: {exc}"
                ) from exc
            details = _lstat(child)
            if details is None or not _compatible_entry_stat(entry_details, details):
                raise ConfigurationError(
                    f"read-only benchmark state entry changed during inspection: {child}"
                )
            if _is_link_like_stat(details):
                raise ConfigurationError(
                    f"refusing cleanup of linked or reparse-point benchmark state: {child}"
                )
            if stat.S_ISDIR(details.st_mode):
                directories.append(child)
                pending.append(child)
            elif stat.S_ISREG(details.st_mode):
                files.append(child)
            else:
                raise ConfigurationError(
                    f"refusing cleanup of special benchmark state: {child}"
                )
    try:
        for child in files:
            _chmod_plain_cleanup_entry(child, directory=False)
        for child in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            _chmod_plain_cleanup_entry(child, directory=True)
    except OSError as exc:
        raise ConfigurationError(
            f"could not make exact benchmark state removable: {path}: {exc}"
        ) from exc


_POSIX_FD_CLEANUP_SUPPORTED = (
    os.name == "posix"
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and bool(getattr(os, "O_DIRECTORY", 0))
    and hasattr(os, "fchmod")
    and all(
        operation in os.supports_dir_fd
        for operation in (os.open, os.stat, os.unlink, os.rmdir)
    )
)


def _posix_fd_cleanup_supported() -> bool:
    return _POSIX_FD_CLEANUP_SUPPORTED


def _posix_stat_at(directory_fd: int, name: str, *, label: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError(
            f"benchmark state changed during descriptor-relative cleanup: {label}: {exc}"
        ) from exc


def _require_posix_directory_link(
    parent_fd: int,
    name: str,
    opened: os.stat_result,
    *,
    label: str,
) -> None:
    current = _posix_stat_at(parent_fd, name, label=label)
    if (
        _cleanup_inode_identity(current) != _cleanup_inode_identity(opened)
        or not stat.S_ISDIR(current.st_mode)
        or _is_link_like_stat(current)
    ):
        raise ConfigurationError(
            f"benchmark state directory identity changed during cleanup: {label}"
        )


def _remove_posix_entry_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
    expected_identity: tuple[int, int, int] | None = None,
) -> None:
    before = _posix_stat_at(parent_fd, name, label=label)
    directory = _validate_plain_cleanup_entry(before, path=label)
    identity = _cleanup_inode_identity(before)
    if expected_identity is not None and identity != expected_identity:
        raise ConfigurationError(
            f"benchmark state identity changed before cleanup: {label}"
        )
    if not directory:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | os.O_NOFOLLOW
        )
        descriptor: int | None = None
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
            opened = os.fstat(descriptor)
            if (
                _cleanup_inode_identity(opened) != identity
                or _validate_plain_cleanup_entry(opened, path=label)
            ):
                raise ConfigurationError(
                    f"benchmark state file identity changed before cleanup: {label}"
                )
            immediate = _posix_stat_at(parent_fd, name, label=label)
            if (
                _cleanup_inode_identity(immediate) != identity
                or _validate_plain_cleanup_entry(immediate, path=label)
            ):
                raise ConfigurationError(
                    f"benchmark state file identity changed before unlink: {label}"
                )
            os.unlink(name, dir_fd=parent_fd)
            held_after = os.fstat(descriptor)
            if int(held_after.st_nlink) != 0:
                raise ConfigurationError(
                    f"benchmark state file replacement raced cleanup: {label}"
                )
        except ConfigurationError:
            raise
        except OSError as exc:
            raise ConfigurationError(
                f"descriptor-relative benchmark file cleanup failed: {label}: {exc}"
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ConfigurationError(
                f"could not confirm benchmark file cleanup: {label}: {exc}"
            ) from exc
        raise ConfigurationError(
            f"benchmark state file cleanup was not confirmed: {label}"
        )

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = None
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if (
            _cleanup_inode_identity(opened) != identity
            or not stat.S_ISDIR(opened.st_mode)
            or _is_link_like_stat(opened)
        ):
            raise ConfigurationError(
                f"benchmark state directory identity changed before cleanup: {label}"
            )
        os.fchmod(
            descriptor,
            stat.S_IMODE(opened.st_mode) | stat.S_IWUSR | stat.S_IXUSR,
        )
        _require_posix_directory_link(
            parent_fd,
            name,
            opened,
            label=label,
        )
        try:
            children = sorted(os.listdir(descriptor))
        except OSError as exc:
            raise ConfigurationError(
                f"could not enumerate benchmark state during cleanup: {label}: {exc}"
            ) from exc
        for child_name in children:
            if (
                not isinstance(child_name, str)
                or not child_name
                or child_name in {".", ".."}
                or "/" in child_name
                or "\x00" in child_name
            ):
                raise ConfigurationError(
                    f"benchmark state contains an unsafe cleanup name: {label}"
                )
            _require_posix_directory_link(
                parent_fd,
                name,
                opened,
                label=label,
            )
            _remove_posix_entry_at(
                descriptor,
                child_name,
                label=f"{label}/{child_name}",
            )
        if os.listdir(descriptor):
            raise ConfigurationError(
                f"benchmark state changed before directory removal: {label}"
            )
        _require_posix_directory_link(
            parent_fd,
            name,
            opened,
            label=label,
        )
        os.rmdir(name, dir_fd=parent_fd)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError(
            f"descriptor-relative benchmark directory cleanup failed: {label}: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigurationError(
            f"could not confirm benchmark directory cleanup: {label}: {exc}"
        ) from exc
    raise ConfigurationError(
        f"benchmark state directory cleanup was not confirmed: {label}"
    )


def _remove_plain_path_secure(path: Path) -> None:
    """Remove one exact plain path without following repository-controlled links."""

    path = Path(os.path.abspath(path))
    before = _lstat(path)
    if before is None:
        return
    _validate_plain_cleanup_entry(before, path=path)
    if os.name == "posix":
        if not _posix_fd_cleanup_supported():
            raise ConfigurationError(
                "secure descriptor-relative cleanup is unavailable on this POSIX host"
            )
        parent = path.parent
        parent_before = _lstat(parent)
        if (
            parent_before is None
            or _is_link_like_stat(parent_before)
            or not stat.S_ISDIR(parent_before.st_mode)
        ):
            raise ConfigurationError(
                f"benchmark cleanup parent is not an ordinary directory: {parent}"
            )
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
        parent_fd: int | None = None
        try:
            parent_fd = os.open(parent, flags)
            opened_parent = os.fstat(parent_fd)
            if _cleanup_inode_identity(opened_parent) != _cleanup_inode_identity(
                parent_before
            ):
                raise ConfigurationError(
                    f"benchmark cleanup parent identity changed: {parent}"
                )
            _remove_posix_entry_at(
                parent_fd,
                path.name,
                label=str(path),
                expected_identity=_cleanup_inode_identity(before),
            )
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
    else:
        # Windows lacks the dir_fd unlink/rmdir family. Keep all operations
        # bounded to the exact private path, reject links/hardlinks before and
        # after chmod, and confirm final absence.
        if stat.S_ISDIR(before.st_mode):
            _assert_plain_cleanup_tree(path)
            checked = _lstat(path)
            if (
                checked is None
                or _cleanup_inode_identity(checked)
                != _cleanup_inode_identity(before)
            ):
                raise ConfigurationError(
                    f"benchmark state identity changed before Windows cleanup: {path}"
            )
            _make_plain_cleanup_tree_writable(path)
            _assert_plain_cleanup_tree(path)
            checked = _lstat(path)
            if (
                checked is None
                or _cleanup_inode_identity(checked)
                != _cleanup_inode_identity(before)
            ):
                raise ConfigurationError(
                    f"benchmark state identity changed before Windows removal: {path}"
            )
            shutil.rmtree(path)
        else:
            checked = _lstat(path)
            if (
                checked is None
                or _cleanup_inode_identity(checked)
                != _cleanup_inode_identity(before)
                or _validate_plain_cleanup_entry(checked, path=path)
            ):
                raise ConfigurationError(
                    f"benchmark state file identity changed before Windows removal: {path}"
                )
            path.unlink()
    if _lstat(path) is not None:
        raise ConfigurationError(
            f"benchmark state cleanup was not confirmed for exact path: {path}"
        )


def _remove_benchmark_state_path(root: Path, path: Path) -> None:
    root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(path))
    if path.parent != root or path.name not in _BENCHMARK_STATE_NAMES:
        raise ConfigurationError(f"refusing cleanup outside exact benchmark state: {path}")
    details = _lstat(path)
    if details is None:
        return
    try:
        _validate_plain_cleanup_entry(details, path=path)
        _remove_plain_path_secure(path)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise ConfigurationError(
            f"benchmark state cleanup failed for exact path {path}: {exc}"
        ) from exc


def _read_dependency_layer_file(
    path: Path,
    *,
    relative: str,
) -> tuple[dict[str, Any], tuple[int, int]]:
    """Read one ordinary, singly linked file through a stable nofollow handle."""

    before = _lstat(path)
    if (
        before is None
        or _is_link_like_stat(before)
        or not stat.S_ISREG(before.st_mode)
        or int(before.st_nlink) != 1
    ):
        raise ConfigurationError(
            "dependency layer requires singly linked ordinary files: " + relative
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError(
            f"could not open dependency layer file {relative}: {exc}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            _is_link_like_stat(opened)
            or not stat.S_ISREG(opened.st_mode)
            or int(opened.st_nlink) != 1
            or _stable_stat_identity(before) != _stable_stat_identity(opened)
        ):
            raise ConfigurationError(
                f"dependency layer file identity changed before read: {relative}"
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_DEPENDENCY_LAYER_BYTES:
                raise ConfigurationError(
                    "dependency layer file exceeds the bounded byte limit: " + relative
                )
            digest.update(chunk)
        opened_after = os.fstat(descriptor)
    except OSError as exc:
        raise ConfigurationError(
            f"could not read dependency layer file {relative}: {exc}"
        ) from exc
    finally:
        try:
            os.close(descriptor)
        except OSError as exc:
            raise ConfigurationError(
                f"could not close dependency layer file {relative}: {exc}"
            ) from exc
    after = _lstat(path)
    if (
        after is None
        or size != int(before.st_size)
        or _stable_stat_identity(before) != _stable_stat_identity(opened_after)
        or _stable_stat_identity(before) != _stable_stat_identity(after)
    ):
        raise ConfigurationError(
            f"dependency layer file changed while it was hashed: {relative}"
        )
    return (
        {
            "path": relative,
            "mode": stat.S_IMODE(before.st_mode),
            "size": size,
            "sha256": digest.hexdigest(),
        },
        (int(before.st_dev), int(before.st_ino)),
    )


def _dependency_tree_snapshot(
    root: Path,
    *,
    require_read_only: bool,
) -> dict[str, Any]:
    """Return a bounded two-pass-compatible mode/content tree certificate."""

    root = Path(os.path.abspath(root))
    root_details = _lstat(root)
    if (
        root_details is None
        or _is_link_like_stat(root_details)
        or not stat.S_ISDIR(root_details.st_mode)
    ):
        raise ConfigurationError(
            f"dependency layer root must be an ordinary directory: {root}"
        )

    directories: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    file_identities: dict[str, tuple[int, int]] = {}
    seen_file_identities: set[tuple[int, int]] = set()
    normalized_paths: set[str] = set()
    path_bytes = 0
    total_bytes = 0

    def register(relative: str) -> None:
        nonlocal path_bytes
        try:
            encoded = relative.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ConfigurationError(
                f"dependency layer path is not valid UTF-8 text: {relative!r}"
            ) from exc
        if not encoded or len(encoded) > _MAX_DEPENDENCY_LAYER_RELATIVE_PATH_BYTES:
            raise ConfigurationError(
                f"dependency layer relative path is empty or oversized: {relative!r}"
            )
        path_bytes += len(encoded)
        if path_bytes > _MAX_DEPENDENCY_LAYER_PATH_BYTES:
            raise ConfigurationError("dependency layer paths exceed the bounded byte limit")
        normalized = relative.casefold()
        if normalized in normalized_paths:
            raise ConfigurationError(
                "dependency layer paths collide under case-insensitive normalization: "
                + relative
            )
        normalized_paths.add(normalized)

    def visit(
        directory: Path,
        relative: str,
        depth: int,
        expected: os.stat_result | None = None,
    ) -> None:
        nonlocal total_bytes
        if depth > _MAX_DEPENDENCY_LAYER_DEPTH:
            raise ConfigurationError("dependency layer exceeds the bounded directory depth")
        before = _lstat(directory)
        if (
            before is None
            or _is_link_like_stat(before)
            or not stat.S_ISDIR(before.st_mode)
            or (
                expected is not None
                and not _compatible_entry_stat(expected, before)
            )
        ):
            raise ConfigurationError(
                f"dependency layer directory identity changed: {relative}"
            )
        if require_read_only and stat.S_IMODE(before.st_mode) & 0o222:
            raise ConfigurationError(
                f"dependency layer directory is writable: {relative}"
            )
        register(relative)
        directories.append(
            {"path": relative, "mode": stat.S_IMODE(before.st_mode)}
        )
        if len(directories) > _MAX_DEPENDENCY_LAYER_DIRECTORIES:
            raise ConfigurationError("dependency layer has too many directories")
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError as exc:
            raise ConfigurationError(
                f"could not enumerate dependency layer directory {relative}: {exc}"
            ) from exc
        for entry in entries:
            child = Path(entry.path)
            child_relative = (
                entry.name if relative == "." else f"{relative}/{entry.name}"
            )
            try:
                details = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ConfigurationError(
                    f"could not inspect dependency layer entry {child_relative}: {exc}"
                ) from exc
            if _is_link_like_stat(details):
                raise ConfigurationError(
                    "dependency layer refuses links or reparse points: "
                    + child_relative
                )
            if stat.S_ISDIR(details.st_mode):
                visit(child, child_relative, depth + 1, details)
            elif stat.S_ISREG(details.st_mode):
                register(child_relative)
                row, identity = _read_dependency_layer_file(
                    child,
                    relative=child_relative,
                )
                if require_read_only and int(row["mode"]) & 0o222:
                    raise ConfigurationError(
                        f"dependency layer file is writable: {child_relative}"
                    )
                if identity in seen_file_identities:
                    raise ConfigurationError(
                        "dependency layer refuses multiply referenced file identities: "
                        + child_relative
                    )
                seen_file_identities.add(identity)
                file_identities[child_relative] = identity
                files.append(row)
                total_bytes += int(row["size"])
                if len(files) > _MAX_DEPENDENCY_LAYER_FILES:
                    raise ConfigurationError("dependency layer has too many files")
                if total_bytes > _MAX_DEPENDENCY_LAYER_BYTES:
                    raise ConfigurationError(
                        "dependency layer exceeds the bounded aggregate byte limit"
                    )
            else:
                raise ConfigurationError(
                    "dependency layer refuses nonregular entries: " + child_relative
                )
        after = _lstat(directory)
        if after is None or _stable_stat_identity(before) != _stable_stat_identity(after):
            raise ConfigurationError(
                f"dependency layer directory changed while inspected: {relative}"
            )

    visit(root, ".", 0)
    payload = {
        "schema": _DEPENDENCY_TREE_SCHEMA,
        "directories": sorted(directories, key=lambda row: str(row["path"])),
        "files": sorted(files, key=lambda row: str(row["path"])),
        "directory_count": len(directories),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "path_bytes": path_bytes,
    }
    return {
        **payload,
        "tree_sha256": _canonical_sha256(payload),
        "_file_identities": file_identities,
    }


def _public_dependency_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if not key.startswith("_")}


def _stable_dependency_tree_snapshot(
    root: Path,
    *,
    require_read_only: bool,
) -> dict[str, Any]:
    first = _dependency_tree_snapshot(root, require_read_only=require_read_only)
    second = _dependency_tree_snapshot(root, require_read_only=require_read_only)
    _require_matching_dependency_snapshot(
        second,
        first,
        label="two-pass race barrier",
    )
    return second


def _dependency_content_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "directories": [str(row["path"]) for row in snapshot["directories"]],
        "files": [
            {
                "path": str(row["path"]),
                "size": int(row["size"]),
                "sha256": str(row["sha256"]),
            }
            for row in snapshot["files"]
        ],
    }


def _require_matching_dependency_snapshot(
    actual: dict[str, Any],
    expected: dict[str, Any],
    *,
    label: str,
) -> None:
    if _public_dependency_snapshot(actual) != _public_dependency_snapshot(expected):
        raise ConfigurationError(
            f"{label} dependency layer mode/content digest mismatch"
        )


def _chmod_dependency_entry(path: Path, *, mode: int, directory: bool) -> int:
    before = _lstat(path)
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if before is None or _is_link_like_stat(before) or not expected_kind(before.st_mode):
        raise ConfigurationError(f"dependency layer entry changed before chmod: {path}")
    try:
        os.chmod(path, mode, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        if os.name != "nt":
            raise
        os.chmod(path, mode)
    except OSError as exc:
        raise ConfigurationError(
            f"could not set dependency layer mode for {path}: {exc}"
        ) from exc
    after = _lstat(path)
    actual_mode = stat.S_IMODE(after.st_mode) if after is not None else -1
    mode_matches = (
        (actual_mode & 0o222 != 0) == (mode & 0o222 != 0)
        if os.name == "nt" and not directory
        else actual_mode == mode
    )
    if (
        after is None
        or _is_link_like_stat(after)
        or not expected_kind(after.st_mode)
        or not mode_matches
    ):
        raise ConfigurationError(f"dependency layer mode change was not confirmed: {path}")
    return actual_mode


def _freeze_dependency_tree(root: Path) -> dict[str, Any]:
    mutable = _dependency_tree_snapshot(root, require_read_only=False)
    for row in mutable["files"]:
        mode = int(row["mode"])
        if not mode & 0o444:
            raise ConfigurationError(
                f"dependency layer file is not readable: {row['path']}"
            )
        _chmod_dependency_entry(
            root / str(row["path"]),
            mode=mode & ~0o222,
            directory=False,
        )
    for row in sorted(
        mutable["directories"],
        key=lambda value: (
            -1
            if value["path"] == "."
            else str(value["path"]).count("/")
        ),
        reverse=True,
    ):
        mode = int(row["mode"])
        if not mode & 0o500:
            raise ConfigurationError(
                f"dependency layer directory is not traversable: {row['path']}"
            )
        path = root if row["path"] == "." else root / str(row["path"])
        _chmod_dependency_entry(path, mode=mode & ~0o222, directory=True)
    frozen = _stable_dependency_tree_snapshot(root, require_read_only=True)
    if _dependency_content_projection(frozen) != _dependency_content_projection(
        mutable
    ):
        raise ConfigurationError(
            "dependency layer content changed while it was made read-only"
        )
    return frozen


def _copy_dependency_file(source: Path, destination: Path, *, relative: str) -> None:
    source_details = _lstat(source)
    if (
        source_details is None
        or _is_link_like_stat(source_details)
        or not stat.S_ISREG(source_details.st_mode)
        or int(source_details.st_nlink) != 1
    ):
        raise ConfigurationError(
            f"dependency source changed before private copy: {relative}"
        )
    source_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    destination_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        source_fd = os.open(source, source_flags)
        destination_fd = os.open(destination, destination_flags, 0o600)
        opened_source = os.fstat(source_fd)
        if _stable_stat_identity(source_details) != _stable_stat_identity(opened_source):
            raise ConfigurationError(
                f"dependency source changed while private copy opened: {relative}"
            )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                written = os.write(destination_fd, chunk[offset:])
                if written <= 0:
                    raise ConfigurationError(
                        f"short write while privately copying dependency: {relative}"
                    )
                offset += written
        os.fsync(destination_fd)
        source_after = os.fstat(source_fd)
        if _stable_stat_identity(source_details) != _stable_stat_identity(source_after):
            raise ConfigurationError(
                f"dependency source changed during private copy: {relative}"
            )
    except OSError as exc:
        raise ConfigurationError(
            f"could not privately copy dependency file {relative}: {exc}"
        ) from exc
    finally:
        close_error: OSError | None = None
        for descriptor in (destination_fd, source_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    close_error = close_error or exc
        if close_error is not None:
            raise ConfigurationError(
                f"could not close private dependency copy {relative}: {close_error}"
            ) from close_error


def _copy_frozen_dependency_tree(
    source: Path,
    destination: Path,
    *,
    expected: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, float]]:
    verification_started = time.perf_counter()
    source_before = _stable_dependency_tree_snapshot(
        source,
        require_read_only=True,
    )
    _require_matching_dependency_snapshot(
        source_before,
        expected,
        label="pre-copy source",
    )
    pre_copy_verification_ms = (time.perf_counter() - verification_started) * 1000.0
    if _lstat(destination) is not None:
        raise ConfigurationError(
            f"private dependency materialization already exists: {destination}"
        )
    copy_started = time.perf_counter()
    try:
        staging_mode = 0o777 if os.name == "nt" else 0o700
        destination.mkdir(mode=staging_mode)
        for row in sorted(
            expected["directories"],
            key=lambda value: (str(value["path"]).count("/"), str(value["path"])),
        ):
            if row["path"] == ".":
                continue
            (destination / str(row["path"])).mkdir(mode=staging_mode)
        for row in expected["files"]:
            relative = str(row["path"])
            target = destination / relative
            _copy_dependency_file(source / relative, target, relative=relative)
            _chmod_dependency_entry(
                target,
                mode=int(row["mode"]),
                directory=False,
            )
        for row in sorted(
            expected["directories"],
            key=lambda value: (
                -1
                if value["path"] == "."
                else str(value["path"]).count("/")
            ),
            reverse=True,
        ):
            target = (
                destination
                if row["path"] == "."
                else destination / str(row["path"])
            )
            _chmod_dependency_entry(
                target,
                mode=int(row["mode"]),
                directory=True,
            )
    except BaseException:
        if _lstat(destination) is not None:
            _assert_plain_cleanup_tree(destination)
            _make_plain_cleanup_tree_writable(destination)
            shutil.rmtree(destination)
            if _lstat(destination) is not None:
                raise ConfigurationError(
                    "failed private dependency copy left uncertain state"
                )
        raise

    private_copy_ms = (time.perf_counter() - copy_started) * 1000.0

    verification_started = time.perf_counter()
    destination_snapshot = _stable_dependency_tree_snapshot(
        destination,
        require_read_only=True,
    )
    _require_matching_dependency_snapshot(
        destination_snapshot,
        expected,
        label="private materialization",
    )
    source_after = _stable_dependency_tree_snapshot(
        source,
        require_read_only=True,
    )
    _require_matching_dependency_snapshot(
        source_after,
        expected,
        label="post-copy source",
    )
    source_ids = set(source_after["_file_identities"].values())
    destination_ids = set(destination_snapshot["_file_identities"].values())
    if source_ids & destination_ids:
        raise ConfigurationError(
            "private dependency materialization shares a hard-linked file with source"
        )
    post_copy_verification_ms = (time.perf_counter() - verification_started) * 1000.0
    return (
        destination_snapshot,
        {
            "private_copy_ms": private_copy_ms,
            "verification_ms": pre_copy_verification_ms + post_copy_verification_ms,
        },
    )


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
    return ordered[index]


def _timing_ms(payload: dict[str, Any], key: str, *, default: float = 0.0) -> float:
    value = payload.get(key, default)
    return round(_finite_nonnegative_ms(key, value), 3)


def _finite_nonnegative_ms(name: str, value: object) -> float:
    """Validate one raw wall-cost component without rounding it away."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"benchmark timing {name!r} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ConfigurationError(f"benchmark timing {name!r} is invalid: {value!r}")
    return result


def _finite_positive_ms(name: str, value: object) -> float:
    result = _finite_nonnegative_ms(name, value)
    if result <= 0:
        raise ConfigurationError(f"benchmark timing {name!r} must be positive")
    return result


def _validate_primary_cost_ledger(
    ledger: dict[str, object],
) -> tuple[dict[str, float], float, float]:
    """Fail closed unless every primary end-to-end setup charge reconciles."""

    if not isinstance(ledger, dict):
        raise ConfigurationError("primary end-to-end cost ledger is not an object")
    expected = set(_PRIMARY_COST_LEDGER_KEYS)
    actual = set(ledger)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ConfigurationError(
            "primary end-to-end cost ledger has the wrong components: "
            f"missing={missing}, unexpected={unexpected}"
        )
    validated = {
        name: _finite_nonnegative_ms(name, ledger[name])
        for name in _PRIMARY_COST_LEDGER_KEYS
    }
    environment = validated[
        "symmetric_environment_bootstrap_charged_to_each_arm"
    ]
    direct_overhead = environment + validated["direct_seed_charged_to_direct"]
    zerorun_overhead = math.fsum(
        (
            environment,
            validated["zerorun_qualification_charged_to_zerorun"],
            validated["zerorun_activation_charged_to_zerorun"],
            validated["zerorun_seed_charged_to_zerorun"],
            validated["automatic_refresh_charged_to_zerorun"],
        )
    )
    _finite_nonnegative_ms("direct_primary_overhead", direct_overhead)
    _finite_nonnegative_ms("zerorun_primary_overhead", zerorun_overhead)
    return validated, direct_overhead, zerorun_overhead


def _direct_phase_ms(payload: dict[str, Any]) -> dict[str, float]:
    wall = _timing_ms(payload, "wall_ms")
    return {
        "end_to_end_container_wall_including_collection_execution_and_cleanup": wall,
    }


def _reported_phase_ms(payload: dict[str, Any]) -> dict[str, float]:
    reported = payload.get("phase_ms", {})
    if not isinstance(reported, dict) or not all(
        isinstance(name, str) and name for name in reported
    ):
        raise ConfigurationError("ZeroRun phase_ms is malformed")
    return {
        name: _timing_ms(reported, name)
        for name in sorted(reported)
    }


def _zerorun_timing_ms(
    payload: dict[str, Any],
    *,
    outer_wall_ms: float,
) -> dict[str, float]:
    phases = {
        "outer_wall": _timing_ms(
            {"outer_wall_ms": outer_wall_ms},
            "outer_wall_ms",
        ),
        "product_reported_wall": _timing_ms(
            payload,
            "wall_ms",
            default=outer_wall_ms,
        ),
        "execution": _timing_ms(payload, "execution_ms"),
        "collection_overhead": _timing_ms(payload, "collection_overhead_ms"),
        "single_pass_wall": _timing_ms(payload, "single_pass_wall_ms"),
        "avoided_execution_reference": _timing_ms(
            payload,
            "avoided_execution_reference_ms",
        ),
    }
    return phases


def _corpus(root: Path, git: str, frozen_sha: str, case_count: int) -> list[str]:
    process = _run(
        root,
        [
            git,
            "rev-list",
            "--first-parent",
            f"--max-count={case_count + 1}",
            frozen_sha,
        ],
    )
    if process.returncode != 0:
        raise ConfigurationError(
            "could not derive frozen first-parent corpus: "
            + process.stderr[-4000:]
        )
    newest_first = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if len(newest_first) != case_count + 1:
        raise ConfigurationError(
            f"frozen corpus requires {case_count + 1} states; got {len(newest_first)}"
        )
    return list(reversed(newest_first))


def _checkout(root: Path, git: str, sha: str) -> None:
    process = _run(root, [git, "checkout", "--detach", "-f", sha])
    if process.returncode != 0:
        raise ConfigurationError(f"git checkout failed for {sha}: {process.stderr[-4000:]}")


def _exact_container_ids(root: Path, docker: str, container_name: str) -> list[str]:
    process = _run(
        root,
        [
            docker,
            "container",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"name=^/{container_name}$",
        ],
        timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if process.returncode != 0:
        raise ConfigurationError(
            "could not attest the exact benchmark dependency container name: "
            + process.stderr
        )
    container_ids = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if any(re.fullmatch(r"[0-9a-f]{12,64}", value) is None for value in container_ids):
        raise ConfigurationError(
            "Docker returned a malformed identity while attesting the benchmark container"
        )
    return container_ids


def _inspect_container(
    root: Path,
    docker: str,
    container_reference: str,
) -> tuple[str, str, str, dict[str, Any]]:
    process = _run(
        root,
        [
            docker,
            "container",
            "inspect",
            "--format",
            "{{.Id}}\t{{.Name}}\t{{.Config.Image}}\t{{json .State}}",
            container_reference,
        ],
        timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if process.returncode != 0:
        raise ConfigurationError(
            "could not inspect the exact benchmark dependency container: "
            + process.stderr
        )
    fields = process.stdout.strip().split("\t", 3)
    if len(fields) != 4:
        raise ConfigurationError(
            "Docker returned malformed benchmark dependency container metadata"
        )
    container_id, name, image, state_json = fields
    try:
        state = json.loads(state_json)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "Docker returned malformed benchmark dependency container state"
        ) from exc
    if not isinstance(state, dict):
        raise ConfigurationError(
            "Docker returned non-object benchmark dependency container state"
        )
    return container_id, name, image, state


def _confirm_container_id_absent(root: Path, docker: str, container_id: str) -> bool:
    process = _run(
        root,
        [docker, "container", "inspect", container_id],
        timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if process.returncode == 0:
        return False
    lowered = process.stderr.casefold()
    return "no such container" in lowered or "no such object" in lowered


def _build_frozen_pytest_environment(
    root: Path,
    *,
    runtime_image: str,
    extra_requirements: Sequence[str],
) -> str:
    _validate_extra_requirements(extra_requirements)
    runtime_requirements_row, runtime_requirements_bytes = (
        _read_runtime_requirements_identity()
    )
    runtime_requirements_sha256 = str(runtime_requirements_row["sha256"])
    env_root = root / ".zerorun-env"
    _remove_benchmark_state_path(root, env_root)
    site_packages = env_root / "site-packages"
    bin_dir = env_root / "bin"
    # CPython 3.13+ gives mode 0700 special DACL semantics on Windows.  A
    # restricted service token can create that DACL and then be unable to
    # traverse it to create the staging children.  The Windows benchmark
    # checkout already supplies its inherited ACL; retain private 0700 from
    # the first mkdir on POSIX, where Docker consumes these ownership bits.
    env_root.mkdir(mode=0o777 if os.name == "nt" else 0o700)
    site_packages.mkdir()
    bin_dir.mkdir()
    runtime_requirements = env_root / "runtime-requirements.txt"
    runtime_requirements.write_bytes(runtime_requirements_bytes)

    # The dependency container drops every capability.  Docker bind mounts
    # preserve the host ownership of this deliberately private (0700) staging
    # directory, so UID 0 without CAP_DAC_OVERRIDE cannot traverse it on the
    # GitHub runner.  Run the installer as the exact staging owner instead of
    # weakening the directory or writable target to world-accessible modes.
    staging_metadata = env_root.stat()
    staging_uid = staging_metadata.st_uid
    staging_gid = staging_metadata.st_gid
    if staging_uid < 0 or staging_gid < 0:
        raise ConfigurationError(
            "generalization runtime staging owner could not be represented in Docker"
        )
    dependency_container_user = f"{staging_uid}:{staging_gid}"

    docker = _host_executable(root, "docker")

    container_name = "zerorun-generalization-env-" + secrets.token_hex(16)
    if _exact_container_ids(root, docker, container_name):
        raise ConfigurationError(
            "random benchmark dependency container name unexpectedly already exists"
        )
    create_argv = [
        docker,
        "create",
        "--name",
        container_name,
        "--pull",
        "never",
        "--platform",
        "linux/amd64",
        "--user",
        dependency_container_user,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        *DOCKER_RESOURCE_ARGS,
        "--mount",
        f"type=bind,src={env_root},dst=/zerorun-env",
        runtime_image,
        "/usr/local/bin/python",
        "-I",
        "-c",
        _RUNTIME_INSTALL_BOOTSTRAP,
        _GENERALIZATION_RUNTIME_IMPLEMENTATION_ID,
        GENERALIZATION_RUNTIME_PYTHON_VERSION,
        *_RUNTIME_INSTALL_ARGUMENTS,
    ]
    create_submitted = False
    created_id: str | None = None
    try:
        create_submitted = True
        created = _run(
            root,
            create_argv,
            timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
        )
        if created.returncode != 0:
            raise ConfigurationError(
                "could not create frozen pytest dependency container: "
                + created.stderr
            )
        created_id = created.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{64}", created_id) is None:
            raise ConfigurationError(
                "Docker returned a malformed frozen pytest dependency container ID"
            )

        inspected_id, inspected_name, inspected_image, initial_state = _inspect_container(
            root,
            docker,
            created_id,
        )
        if (
            inspected_id != created_id
            or inspected_name != f"/{container_name}"
            or inspected_image != runtime_image
            or initial_state.get("Status") != "created"
            or initial_state.get("Running") is not False
        ):
            raise ConfigurationError(
                "frozen pytest dependency container failed create-state attestation"
            )

        started = _run(
            root,
            [docker, "start", "--attach", created_id],
            timeout_seconds=_DOCKER_BUILD_TIMEOUT_SECONDS,
        )
        terminal_id, terminal_name, terminal_image, terminal_state = _inspect_container(
            root,
            docker,
            created_id,
        )
        if (
            started.returncode != 0
            or terminal_id != created_id
            or terminal_name != f"/{container_name}"
            or terminal_image != runtime_image
            or terminal_state.get("Status") != "exited"
            or terminal_state.get("Running") is not False
            or terminal_state.get("ExitCode") != 0
        ):
            raise ConfigurationError(
                "frozen pytest dependency container failed bounded execution: "
                f"stdout tail: {started.stdout!r}; stderr tail: {started.stderr!r}; "
                f"state: {terminal_state!r}"
            )
    finally:
        if create_submitted:
            if created_id is None:
                if not _force_remove_container(
                    docker,
                    container_name,
                    environment=dict(os.environ),
                ):
                    raise ConfigurationError(
                        "benchmark dependency container cleanup was not confirmed "
                        f"for uncertain create name: {container_name}"
                    )
            else:
                removed = _run(
                    root,
                    [docker, "container", "rm", "--force", "--volumes", created_id],
                    timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
                )
                if removed.returncode != 0 or not _confirm_container_id_absent(
                    root,
                    docker,
                    created_id,
                ):
                    raise ConfigurationError(
                        "could not confirm removal of the exact benchmark dependency "
                        f"container ID {created_id}: {removed.stderr}"
                    )

    _write_exclusive_verified_file(
        bin_dir / "python",
        _RUNTIME_WRAPPER_BYTES,
        mode=0o755,
    )
    env_root.chmod(0o755)
    _write_frozen_pytest_manifest(root, runtime_image=runtime_image)
    return runtime_requirements_sha256


def _dependency_layer_construction_identity(
    *,
    runtime_image: str,
    extra_requirements: Sequence[str],
    runtime_requirements: dict[str, Any],
) -> dict[str, Any]:
    _validate_extra_requirements(extra_requirements)
    payload = {
        "schema": _DEPENDENCY_LAYER_SCHEMA,
        "methodology_version": _METHODOLOGY_VERSION,
        "runtime": {
            "image": runtime_image,
            "platform": "linux/amd64",
            "python_implementation": GENERALIZATION_RUNTIME_IMPLEMENTATION,
            "python_implementation_id": _GENERALIZATION_RUNTIME_IMPLEMENTATION_ID,
            "python_version": GENERALIZATION_RUNTIME_PYTHON_VERSION,
        },
        "requirements": {
            "path": "ci/generalization-runtime-requirements.txt",
            "size": int(runtime_requirements["size"]),
            "sha256": str(runtime_requirements["sha256"]),
        },
        "extra_requirements": list(extra_requirements),
        "installer": {
            "entrypoint": ["/usr/local/bin/python", "-I", "-c"],
            "bootstrap_sha256": hashlib.sha256(
                _RUNTIME_INSTALL_BOOTSTRAP.encode("utf-8")
            ).hexdigest(),
            "arguments": list(_RUNTIME_INSTALL_ARGUMENTS),
            "pull_policy": "never",
            "container_user": "exact-private-staging-owner",
            "capabilities": "drop-all",
            "no_new_privileges": True,
            "resource_args": list(DOCKER_RESOURCE_ARGS),
        },
        "wrapper_sha256": hashlib.sha256(_RUNTIME_WRAPPER_BYTES).hexdigest(),
        "manifest_sha256": hashlib.sha256(
            _frozen_pytest_manifest_bytes(runtime_image=runtime_image)
        ).hexdigest(),
    }
    return {**payload, "construction_identity_sha256": _canonical_sha256(payload)}


def _read_runtime_requirements_identity() -> tuple[dict[str, Any], bytes]:
    details = _lstat(GENERALIZATION_RUNTIME_REQUIREMENTS)
    if (
        details is None
        or _is_link_like_stat(details)
        or not stat.S_ISREG(details.st_mode)
        or int(details.st_size) <= 0
        or int(details.st_size) > 64 * 1024
    ):
        raise ConfigurationError(
            "generalization runtime requirements lock must be a bounded ordinary file"
        )
    row, raw = _read_identity_file(
        GENERALIZATION_RUNTIME_REQUIREMENTS,
        relative="ci/generalization-runtime-requirements.txt",
    )
    if not raw or len(raw) > 64 * 1024:
        raise ConfigurationError(
            "generalization runtime requirements lock is empty or oversized"
        )
    return row, raw


def _remove_private_dependency_workspace(path: Path) -> None:
    details = _lstat(path)
    if details is None:
        return
    if _is_link_like_stat(details) or not stat.S_ISDIR(details.st_mode):
        raise ConfigurationError(
            f"private dependency workspace cleanup target changed identity: {path}"
        )
    try:
        _remove_plain_path_secure(path)
    except OSError as exc:
        raise ConfigurationError(
            f"private dependency workspace cleanup failed: {path}: {exc}"
        ) from exc
    if _lstat(path) is not None:
        raise ConfigurationError(
            f"private dependency workspace cleanup was not confirmed: {path}"
        )


def _create_private_dependency_workspace(parent: Path) -> Path:
    for _ in range(128):
        candidate = parent / (
            ".zerorun-generalization-dependency-layer-" + secrets.token_hex(16)
        )
        try:
            candidate.mkdir(mode=0o777 if os.name == "nt" else 0o700)
        except FileExistsError:
            continue
        except OSError as exc:
            raise ConfigurationError(
                f"could not create private dependency-layer workspace: {exc}"
            ) from exc
        details = _lstat(candidate)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            _remove_private_dependency_workspace(candidate)
            raise ConfigurationError(
                f"could not resolve private dependency-layer workspace: {exc}"
            ) from exc
        if (
            details is None
            or _is_link_like_stat(details)
            or not stat.S_ISDIR(details.st_mode)
            or resolved != candidate
            or resolved.parent != parent
        ):
            _remove_private_dependency_workspace(candidate)
            raise ConfigurationError(
                "private dependency-layer workspace failed exact-parent attestation"
            )
        return candidate
    raise ConfigurationError(
        "could not allocate a unique private dependency-layer workspace"
    )


@contextmanager
def _frozen_dependency_layer(
    root: Path,
    *,
    runtime_image: str,
    extra_requirements: Sequence[str],
):
    """Build exactly one frozen layer and destroy it after both trajectories."""

    build_started = time.perf_counter()
    root = root.expanduser().resolve(strict=True)
    parent = root.parent.resolve(strict=True)
    requirements_row, requirements_raw = _read_runtime_requirements_identity()
    construction = _dependency_layer_construction_identity(
        runtime_image=runtime_image,
        extra_requirements=extra_requirements,
        runtime_requirements=requirements_row,
    )
    workspace = _create_private_dependency_workspace(parent)
    layer_path: Path | None = None
    layer_snapshot: dict[str, Any] | None = None
    layer_record: dict[str, Any] | None = None
    try:
        # Build directly below the private workspace so the final rename stays
        # within one parent directory.  Linux requires write permission on a
        # moved directory when its ``..`` entry changes; the frozen layer root
        # intentionally has no write bits, so a cross-parent rename would fail.
        builder = workspace
        built_requirements_sha256 = _build_frozen_pytest_environment(
            builder,
            runtime_image=runtime_image,
            extra_requirements=extra_requirements,
        )
        expected_requirements_sha256 = hashlib.sha256(requirements_raw).hexdigest()
        if built_requirements_sha256 != expected_requirements_sha256:
            raise ConfigurationError(
                "dependency layer was built from a different requirements lock"
            )
        source_environment = builder / ".zerorun-env"
        layer_snapshot = _freeze_dependency_tree(source_environment)
        if int(layer_snapshot["file_count"]) < 2:
            raise ConfigurationError(
                "frozen dependency layer is unexpectedly empty"
            )
        locked_row = next(
            (
                row
                for row in layer_snapshot["files"]
                if row["path"] == "runtime-requirements.txt"
            ),
            None,
        )
        if (
            locked_row is None
            or locked_row["sha256"] != expected_requirements_sha256
            or int(locked_row["size"]) != len(requirements_raw)
        ):
            raise ConfigurationError(
                "frozen dependency layer does not contain the exact runtime lock"
            )
        tree_sha256 = str(layer_snapshot["tree_sha256"])
        layer_path = builder / tree_sha256
        if _lstat(layer_path) is not None:
            raise ConfigurationError(
                "content-addressed dependency-layer destination already exists"
            )
        source_environment.replace(layer_path)
        relocated = _stable_dependency_tree_snapshot(
            layer_path,
            require_read_only=True,
        )
        _require_matching_dependency_snapshot(
            relocated,
            layer_snapshot,
            label="relocated content-addressed",
        )
        layer_snapshot = relocated
        _remove_benchmark_state_path(builder, builder / ".zerorun.json")
        try:
            remaining = list(os.scandir(builder))
        except OSError as exc:
            raise ConfigurationError(
                f"dependency-layer workspace could not be revalidated: {exc}"
            ) from exc
        if (
            len(remaining) != 1
            or remaining[0].name != tree_sha256
            or remaining[0].is_symlink()
            or not remaining[0].is_dir(follow_symlinks=False)
        ):
            raise ConfigurationError(
                "dependency-layer build left unexpected private workspace state"
            )
        build_ms = _finite_nonnegative_ms(
            "dependency_layer_build_once",
            (time.perf_counter() - build_started) * 1000.0,
        )
        layer_record = {
            "root": layer_path,
            "snapshot": layer_snapshot,
            "construction": construction,
            "runtime_requirements_sha256": expected_requirements_sha256,
            "build_ms": build_ms,
            "provenance": {
                "schema": _DEPENDENCY_LAYER_SCHEMA,
                "construction_contract": {
                    key: value
                    for key, value in construction.items()
                    if key != "construction_identity_sha256"
                },
                "construction_identity_sha256": construction[
                    "construction_identity_sha256"
                ],
                "content_tree_sha256": layer_snapshot["tree_sha256"],
                "content_addressed_directory_name": layer_path.name,
                "runtime_image": runtime_image,
                "runtime_requirements_sha256": expected_requirements_sha256,
                "extra_requirements": list(extra_requirements),
                "installer_bootstrap_sha256": construction["installer"][
                    "bootstrap_sha256"
                ],
                "installer_arguments_sha256": _canonical_sha256(
                    construction["installer"]["arguments"]
                ),
                "wrapper_sha256": construction["wrapper_sha256"],
                "manifest_sha256": construction["manifest_sha256"],
                "file_count": layer_snapshot["file_count"],
                "directory_count": layer_snapshot["directory_count"],
                "total_bytes": layer_snapshot["total_bytes"],
                "path_bytes": layer_snapshot["path_bytes"],
                "read_only_mode_and_content_verified": True,
                "source_hardlinks_rejected": True,
                "build_count": 1,
                "build_once_ms": round(build_ms, 3),
            },
        }
        yield layer_record
    finally:
        cleanup_error: BaseException | None = None
        final_verification_ms = 0.0
        if layer_path is not None and layer_snapshot is not None:
            verification_started = time.perf_counter()
            try:
                final_snapshot = _stable_dependency_tree_snapshot(
                    layer_path,
                    require_read_only=True,
                )
                _require_matching_dependency_snapshot(
                    final_snapshot,
                    layer_snapshot,
                    label="pre-cleanup content-addressed",
                )
            except BaseException as exc:
                cleanup_error = exc
            finally:
                final_verification_ms = (time.perf_counter() - verification_started) * 1000.0
        teardown_started = time.perf_counter()
        try:
            _remove_private_dependency_workspace(workspace)
        except BaseException as exc:
            cleanup_error = cleanup_error or exc
        teardown_ms = (time.perf_counter() - teardown_started) * 1000.0
        if cleanup_error is not None:
            raise ConfigurationError(
                "frozen dependency-layer integrity or cleanup was not confirmed: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            ) from cleanup_error
        if layer_record is not None:
            final_verification_ms = _finite_nonnegative_ms(
                "dependency_layer_final_verification",
                final_verification_ms,
            )
            teardown_ms = _finite_nonnegative_ms(
                "dependency_layer_teardown",
                teardown_ms,
            )
            layer_record["post_use_cleanup"] = {
                "schema": "zerorun.dependency-layer-post-use-cleanup.v1",
                "final_integrity_verification_ms": final_verification_ms,
                "private_layer_teardown_ms": teardown_ms,
                "total_ms": _finite_nonnegative_ms(
                    "dependency_layer_post_use_cleanup",
                    final_verification_ms + teardown_ms,
                ),
                "final_integrity_verified": True,
                "cleanup_confirmed": True,
            }


def _materialize_frozen_pytest_environment(
    root: Path,
    *,
    dependency_layer: dict[str, Any],
    runtime_image: str,
    extra_requirements: Sequence[str],
) -> dict[str, Any]:
    """Copy and verify one trajectory-private read-only environment."""

    total_started = time.perf_counter()
    root = root.expanduser().resolve(strict=True)
    requirements_row, _ = _read_runtime_requirements_identity()
    current_construction = _dependency_layer_construction_identity(
        runtime_image=runtime_image,
        extra_requirements=extra_requirements,
        runtime_requirements=requirements_row,
    )
    if current_construction != dependency_layer["construction"]:
        raise ConfigurationError(
            "dependency-layer construction inputs drifted before materialization"
        )
    if (
        requirements_row["sha256"]
        != dependency_layer["runtime_requirements_sha256"]
    ):
        raise ConfigurationError(
            "runtime requirements drifted before dependency materialization"
        )

    destination = root / ".zerorun-env"
    _remove_benchmark_state_path(root, destination)
    try:
        destination_snapshot, copy_timing = _copy_frozen_dependency_tree(
            Path(dependency_layer["root"]),
            destination,
            expected=dependency_layer["snapshot"],
        )
        manifest_started = time.perf_counter()
        manifest_sha256 = _write_frozen_pytest_manifest(
            root,
            runtime_image=runtime_image,
        )
        manifest_ms = (time.perf_counter() - manifest_started) * 1000.0
    except BaseException:
        try:
            _remove_benchmark_state_path(root, destination)
        except BaseException as cleanup_exc:
            raise ConfigurationError(
                "private dependency materialization failed and cleanup was uncertain: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            ) from cleanup_exc
        raise

    total_ms = _finite_nonnegative_ms(
        "private_dependency_materialization_and_verification",
        (time.perf_counter() - total_started) * 1000.0,
    )
    private_copy_ms = _finite_nonnegative_ms(
        "private_dependency_copy",
        copy_timing["private_copy_ms"],
    )
    verification_ms = _finite_nonnegative_ms(
        "private_dependency_verification",
        copy_timing["verification_ms"],
    )
    manifest_ms = _finite_nonnegative_ms(
        "private_dependency_manifest",
        manifest_ms,
    )
    return {
        "schema": "zerorun.generalization-private-dependency-materialization.v1",
        "content_tree_sha256": destination_snapshot["tree_sha256"],
        "construction_identity_sha256": current_construction[
            "construction_identity_sha256"
        ],
        "manifest_sha256": manifest_sha256,
        "environment_bootstrap_ms": total_ms,
        "private_copy_ms": private_copy_ms,
        "verification_ms": verification_ms,
        "manifest_write_and_verify_ms": manifest_ms,
        "timing_remainder_ms": max(
            0.0,
            total_ms - private_copy_ms - verification_ms - manifest_ms,
        ),
        "private_byte_copy": True,
        "shared_hardlinks": False,
        "read_only_mode_and_content_verified": True,
        "isolated_result_cache": True,
    }


def _clean_zerorun_state(root: Path) -> None:
    for name in _BENCHMARK_STATE_NAMES:
        _remove_benchmark_state_path(root, root / name)


def _activate_exact_candidate(root: Path) -> dict[str, Any]:
    candidate = root / ".zerorun-pytest.candidate.json"
    review_path = root / ".zerorun-pytest.review.json"
    profile_path = root / ".zerorun-pytest.json"

    review = write_review_template(
        candidate,
        output=review_path,
        reviewer="zerorun-generalization-formative-fixture",
    )
    review.update(
        {
            "closure_completeness_reviewed": True,
            "node_independence_reviewed": True,
            "all_candidate_reviewable_nodes_covered": True,
            "authorizes_activation": True,
            "notes": (
                "Mechanical synthetic activation of the exact generated candidate "
                "for a formative benchmark fixture. It is not external HMAC "
                "authority, operator review, or independent customer evidence."
            ),
        }
    )
    review_path.write_text(
        json.dumps(review, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return activate_reviewed_candidate(
        candidate,
        review_path=review_path,
        output=profile_path,
    )


def _plain_git_mask_args(root: Path) -> list[str]:
    """Mask Git metadata without importing ZeroRun's container implementation."""

    marker = root / ".git"
    details = _lstat(marker)
    if details is None or _is_link_like_stat(details):
        raise ConfigurationError("plain-pytest baseline requires an unlinked .git marker")
    if stat.S_ISDIR(details.st_mode):
        return ["--tmpfs", "/workspace/.git:rw,nosuid,nodev,noexec,size=1m"]
    if not stat.S_ISREG(details.st_mode) or details.st_size <= 0 or details.st_size > 4096:
        raise ConfigurationError(
            "plain-pytest baseline .git marker must be a directory or bounded worktree file"
        )
    try:
        raw = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigurationError(f"plain-pytest baseline .git marker is unreadable: {exc}") from exc
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        raise ConfigurationError("plain-pytest baseline worktree marker is malformed")
    gitdir = Path(lines[0][len("gitdir: ") :])
    if not gitdir.is_absolute():
        gitdir = marker.parent / gitdir
    try:
        resolved_gitdir = gitdir.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(
            f"plain-pytest baseline worktree Git directory is unavailable: {exc}"
        ) from exc
    if _is_link_like_stat(resolved_gitdir.lstat()) or not resolved_gitdir.is_dir():
        raise ConfigurationError(
            "plain-pytest baseline worktree Git directory is not an ordinary directory"
        )
    return [
        "--mount",
        "type=bind,src=/dev/null,dst=/workspace/.git,readonly",
    ]


def _plain_exact_container_ids(
    root: Path,
    docker: str,
    container_name: str,
) -> list[str]:
    process = _plain_run(
        root,
        [
            docker,
            "container",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"name=^/{container_name}$",
        ],
        timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if process.returncode != 0:
        raise ConfigurationError(
            "could not attest independent plain-pytest container name: "
            + process.stderr
        )
    rows = [line.strip() for line in process.stdout.splitlines() if line.strip()]
    if any(re.fullmatch(r"[0-9a-f]{12,64}", row) is None for row in rows):
        raise ConfigurationError(
            "Docker returned a malformed independent plain-pytest container identity"
        )
    return rows


def _plain_inspect_container(
    root: Path,
    docker: str,
    reference: str,
) -> tuple[str, str, str, dict[str, Any]]:
    process = _plain_run(
        root,
        [
            docker,
            "container",
            "inspect",
            "--format",
            "{{.Id}}\t{{.Name}}\t{{.Config.Image}}\t{{json .State}}",
            reference,
        ],
        timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if process.returncode != 0:
        raise ConfigurationError(
            "could not inspect independent plain-pytest container: " + process.stderr
        )
    fields = process.stdout.strip().split("\t", 3)
    if len(fields) != 4:
        raise ConfigurationError(
            "Docker returned malformed independent plain-pytest container metadata"
        )
    container_id, name, image, raw_state = fields
    try:
        state = json.loads(raw_state)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            "Docker returned malformed independent plain-pytest container state"
        ) from exc
    if not isinstance(state, dict):
        raise ConfigurationError(
            "Docker returned non-object independent plain-pytest container state"
        )
    return container_id, name, image, state


def _plain_confirm_container_absent(
    root: Path,
    docker: str,
    reference: str,
) -> bool:
    process = _plain_run(
        root,
        [docker, "container", "inspect", reference],
        timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
    )
    if process.returncode == 0:
        return False
    lowered = process.stderr.casefold()
    return "no such container" in lowered or "no such object" in lowered


def _ensure_plain_state_mountpoint(root: Path) -> tuple[int, ...]:
    """Create and attest the tmpfs destination required by the plain runner.

    Docker cannot create a mountpoint below the read-only workspace bind.  The
    product-first trajectory creates ``.zerorun`` before plain pytest, while
    the counterbalanced plain-first trajectory needs the same empty directory
    explicitly.  Pytest cannot observe its contents because the container
    overlays this path with a private tmpfs.
    """

    state = root / ".zerorun"
    details = _lstat(state)
    if details is None:
        try:
            # CPython 3.14 maps an explicit owner-only mode to a restrictive
            # Windows ACL before this process can reopen the directory.
            state.mkdir(mode=0o755 if os.name == "nt" else 0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ConfigurationError(
                f"could not create independent plain-pytest state mountpoint: {exc}"
            ) from exc
        details = _lstat(state)
    try:
        resolved = state.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(
            f"independent plain-pytest state mountpoint is unavailable: {exc}"
        ) from exc
    if (
        details is None
        or _is_link_like_stat(details)
        or not stat.S_ISDIR(details.st_mode)
        or resolved != state
        or resolved.parent != root
    ):
        raise ConfigurationError(
            "independent plain-pytest state mountpoint is not an exact ordinary directory"
        )
    return _stable_stat_identity(details)


def _frozen_pytest_manifest_bytes(*, runtime_image: str) -> bytes:
    payload = {
        "version": 2,
        "tasks": {
            "pytest-generalization": {
                "command": [
                    "sh",
                    ".zerorun-env/bin/python",
                    "-m",
                    "pytest",
                    "-p",
                    "no:cacheprovider",
                ],
                "inputs": [".zerorun-env"],
                "outputs": [],
                "env": [],
                "cacheable": False,
                "unsafe_effects": [],
                "cache_streams": False,
                "result_only": True,
                "closure_reviewed": True,
                "image": runtime_image,
                "platform": "linux/amd64",
            }
        },
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_exclusive_verified_file(path: Path, raw: bytes, *, mode: int) -> str:
    if _lstat(path) is not None:
        raise ConfigurationError(f"refusing to replace benchmark control file: {path}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, mode)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise ConfigurationError(f"short write for benchmark control file: {path}")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        raise ConfigurationError(
            f"could not create benchmark control file {path}: {exc}"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as exc:
                raise ConfigurationError(
                    f"could not close benchmark control file {path}: {exc}"
                ) from exc
    effective_mode = _chmod_dependency_entry(path, mode=mode, directory=False)
    row, _ = _read_dependency_layer_file(path, relative=path.name)
    expected_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        int(row["size"]) != len(raw)
        or row["sha256"] != expected_sha256
        or int(row["mode"]) != effective_mode
    ):
        raise ConfigurationError(
            f"benchmark control file mode/content verification failed: {path}"
        )
    return expected_sha256


def _write_frozen_pytest_manifest(root: Path, *, runtime_image: str) -> str:
    manifest_path = root / ".zerorun.json"
    _remove_benchmark_state_path(root, manifest_path)
    return _write_exclusive_verified_file(
        manifest_path,
        _frozen_pytest_manifest_bytes(runtime_image=runtime_image),
        mode=0o644,
    )


def _plain_remove_uncertain_container(
    root: Path,
    docker: str,
    reference: str,
) -> bool:
    deadline = time.monotonic() + 10.0
    consecutive_absent = 0
    while time.monotonic() < deadline:
        process = _plain_run(
            root,
            [docker, "container", "rm", "--force", "--volumes", reference],
            timeout_seconds=min(5.0, max(0.1, deadline - time.monotonic())),
        )
        if process.returncode == 0:
            consecutive_absent = 0
        else:
            lowered = process.stderr.casefold()
            if "no such container" not in lowered and "no such object" not in lowered:
                return False
            consecutive_absent += 1
            if consecutive_absent >= 3:
                return True
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return False


def _plain_pytest_container(
    root: Path,
    *,
    targets: Sequence[str],
    runtime_image: str,
    support: Path | None = None,
    shadow_output: Path | None = None,
) -> dict[str, Any]:
    """Run ordinary pytest through Docker, independent of ZeroRun's runner path."""

    root = root.expanduser().resolve(strict=True)
    reject_sourceless_workspace_bytecode(root)
    if re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", runtime_image) is None:
        raise ConfigurationError("plain-pytest baseline requires a digest-pinned runtime image")
    if not targets or any(
        not isinstance(target, str) or not target or "\x00" in target
        for target in targets
    ):
        raise ConfigurationError("plain-pytest baseline targets are malformed")
    if (support is None) != (shadow_output is None):
        raise ConfigurationError("plain-pytest shadow support and output must be supplied together")

    docker = _host_executable(root, "docker")
    container_name = "zerorun-independent-pytest-" + secrets.token_hex(16)
    if _plain_exact_container_ids(root, docker, container_name):
        raise ConfigurationError(
            "random independent-pytest container name unexpectedly already exists"
        )
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/zerorun-pycache",
        "TZ": "UTC",
        "PYTHONPATH": "/workspace/src:/workspace:/workspace/.zerorun-env/site-packages",
    }
    create_argv = [
        docker,
        "create",
        "--name",
        container_name,
        "--pull",
        "never",
        "--platform",
        "linux/amd64",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        *_PLAIN_DOCKER_RESOURCE_ARGS,
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
        "--tmpfs",
        "/workspace/.zerorun:rw,nosuid,nodev,noexec,size=1m",
        *_plain_git_mask_args(root),
        "--mount",
        f"type=bind,src={root},dst=/workspace,readonly",
        "--workdir",
        "/workspace",
    ]
    if support is not None and shadow_output is not None:
        support = support.resolve(strict=True)
        shadow_output = shadow_output.resolve(strict=True)
        create_argv.extend(
            [
                "--mount",
                f"type=bind,src={support},dst=/zerorun-benchmark-support,readonly",
                "--mount",
                f"type=bind,src={shadow_output},dst=/zerorun-benchmark-output.json",
            ]
        )
        environment["PYTHONPATH"] = (
            "/zerorun-benchmark-support:" + environment["PYTHONPATH"]
        )
        environment["ZERORUN_BENCHMARK_SHADOW_OUTPUT"] = (
            "/zerorun-benchmark-output.json"
        )
    for name, value in sorted(environment.items()):
        create_argv.extend(["--env", f"{name}={value}"])
    create_argv.extend(
        [
            runtime_image,
            "/usr/local/bin/python",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
        ]
    )
    if support is not None:
        create_argv.extend(["-p", "benchmark_shadow_plugin"])
    create_argv.extend(targets)

    wall_started = time.perf_counter()
    state_mountpoint_identity = _ensure_plain_state_mountpoint(root)
    create_submitted = False
    created_id: str | None = None
    process: subprocess.CompletedProcess[str] | None = None
    terminal_state: dict[str, Any] | None = None
    try:
        create_submitted = True
        created = _plain_run(
            root,
            create_argv,
            timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
        )
        if created.returncode != 0:
            raise ConfigurationError(
                "could not create independent plain-pytest container: " + created.stderr
            )
        created_id = created.stdout.strip()
        if re.fullmatch(r"[0-9a-f]{64}", created_id) is None:
            raise ConfigurationError(
                "Docker returned a malformed independent plain-pytest container ID"
            )
        inspected_id, inspected_name, inspected_image, initial_state = _plain_inspect_container(
            root, docker, created_id
        )
        if (
            inspected_id != created_id
            or inspected_name != f"/{container_name}"
            or inspected_image != runtime_image
            or initial_state.get("Status") != "created"
            or initial_state.get("Running") is not False
        ):
            raise ConfigurationError(
                "independent plain-pytest container failed create-state attestation"
            )
        process = _plain_run(
            root,
            [docker, "start", "--attach", created_id],
            timeout_seconds=_PYTEST_EXECUTION_TIMEOUT_SECONDS,
        )
        terminal_id, terminal_name, terminal_image, terminal_state = _plain_inspect_container(
            root, docker, created_id
        )
        if (
            terminal_id != created_id
            or terminal_name != f"/{container_name}"
            or terminal_image != runtime_image
            or terminal_state.get("Status") != "exited"
            or terminal_state.get("Running") is not False
            or terminal_state.get("ExitCode") != process.returncode
        ):
            raise ConfigurationError(
                "independent plain-pytest container failed terminal-state attestation: "
                + json.dumps(
                    {
                        "expected_id": created_id,
                        "observed_id": terminal_id,
                        "expected_name": f"/{container_name}",
                        "observed_name": terminal_name,
                        "expected_image": runtime_image,
                        "observed_image": terminal_image,
                        "process_returncode": process.returncode,
                        "terminal_status": terminal_state.get("Status"),
                        "terminal_running": terminal_state.get("Running"),
                        "terminal_exit_code": terminal_state.get("ExitCode"),
                        "terminal_oom_killed": terminal_state.get("OOMKilled"),
                        "terminal_error": terminal_state.get("Error"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
    finally:
        if create_submitted:
            if created_id is None:
                if not _plain_remove_uncertain_container(
                    root,
                    docker,
                    container_name,
                ):
                    raise ConfigurationError(
                        "independent plain-pytest cleanup was not confirmed for "
                        f"uncertain create name {container_name}"
                    )
            else:
                removed = _plain_run(
                    root,
                    [docker, "container", "rm", "--force", "--volumes", created_id],
                    timeout_seconds=_DOCKER_CONTROL_TIMEOUT_SECONDS,
                )
                if removed.returncode != 0 or not _plain_confirm_container_absent(
                    root, docker, created_id
                ):
                    raise ConfigurationError(
                        "independent plain-pytest cleanup was not confirmed for exact "
                        f"container ID {created_id}: {removed.stderr}"
                    )
        state_after = _lstat(root / ".zerorun")
        if (
            state_after is None
            or _is_link_like_stat(state_after)
            or not stat.S_ISDIR(state_after.st_mode)
            or _stable_stat_identity(state_after) != state_mountpoint_identity
        ):
            raise ConfigurationError(
                "independent plain-pytest state mountpoint changed during execution"
            )
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    assert process is not None and terminal_state is not None
    return {
        "exit_code": int(terminal_state["ExitCode"]),
        "wall_ms": wall_ms,
        "stdout_tail": process.stdout[-2000:],
        "stderr_tail": process.stderr[-2000:],
        "runner": "independent-docker-plain-pytest",
        "instrumented": support is not None,
    }


def _direct_once(
    root: Path,
    *,
    targets: Sequence[str],
    runtime_image: str,
) -> dict[str, Any]:
    """Time uninstrumented full-target plain pytest outside ZeroRun internals."""

    result = _plain_pytest_container(
        root,
        targets=targets,
        runtime_image=runtime_image,
    )
    result.update(
        {
            "node_count": None,
            "collection_sha256": None,
        }
    )
    return result


def _validate_shadow_evidence(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "exit_code",
        "nodeids",
        "nodeid_sha256",
        "outcomes",
    }:
        raise ConfigurationError("independent pytest shadow evidence is malformed")
    nodeids = payload.get("nodeids")
    outcomes = payload.get("outcomes")
    if (
        payload.get("schema") != "zerorun.benchmark-independent-pytest-shadow.v1"
        or not isinstance(payload.get("exit_code"), int)
        or isinstance(payload.get("exit_code"), bool)
        or not isinstance(nodeids, list)
        or not isinstance(outcomes, list)
        or len(nodeids) != len(outcomes)
        or len(nodeids) > _MAX_SHADOW_NODES
        or len(nodeids) != len(set(nodeids))
        or any(
            not isinstance(nodeid, str)
            or not nodeid
            or "\x00" in nodeid
            or len(nodeid) > 64 * 1024
            for nodeid in nodeids
        )
        or sum(len(nodeid) for nodeid in nodeids) > _MAX_SHADOW_NODEID_CHARS
    ):
        raise ConfigurationError("independent pytest shadow node evidence is invalid")
    canonical = json.dumps(
        {"nodeids": nodeids},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    if payload.get("nodeid_sha256") != hashlib.sha256(canonical).hexdigest():
        raise ConfigurationError("independent pytest shadow node digest mismatch")
    allowed = {None, "passed", "failed", "skipped"}
    complete = True
    failing_nodeids: list[str] = []
    for nodeid, outcome in zip(nodeids, outcomes, strict=True):
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {"setup", "call", "teardown", "wasxfail"}
            or outcome.get("setup") not in allowed
            or outcome.get("call") not in allowed
            or outcome.get("teardown") not in allowed
            or not isinstance(outcome.get("wasxfail"), bool)
            or outcome.get("setup") is None
        ):
            complete = False
            continue
        if "failed" in {
            outcome.get("setup"),
            outcome.get("call"),
            outcome.get("teardown"),
        }:
            failing_nodeids.append(nodeid)
        if outcome.get("setup") == "passed" and outcome.get("call") is None:
            complete = False
    return {
        **payload,
        "complete_per_node_outcomes": complete,
        "failing_nodeids": failing_nodeids,
        "all_nodes_non_failing": complete and not failing_nodeids,
    }


def _shadow_once(
    root: Path,
    *,
    targets: Sequence[str],
    runtime_image: str,
) -> dict[str, Any]:
    """Run an independently instrumented full-fresh oracle outside gate timing."""

    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-benchmark-shadow-"
    ) as temp_name:
        support = temp_name.resolve(strict=True)
        plugin = support / "benchmark_shadow_plugin.py"
        plugin.write_text(_SHADOW_PLUGIN, encoding="utf-8")
        plugin.chmod(0o644)
        output = support / "shadow.json"
        descriptor = os.open(
            output,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o666,
        )
        os.close(descriptor)
        if os.name != "nt":
            output.chmod(0o666)
            support.chmod(0o755)
        run = _plain_pytest_container(
            root,
            targets=targets,
            runtime_image=runtime_image,
            support=support,
            shadow_output=output,
        )
        before = _lstat(output)
        if (
            before is None
            or _is_link_like_stat(before)
            or not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > _MAX_SHADOW_EVIDENCE_BYTES
        ):
            raise ConfigurationError("independent pytest shadow output is missing or oversized")
        try:
            raw = output.read_bytes()
            payload = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigurationError(
                f"independent pytest shadow output is unreadable: {exc}"
            ) from exc
        after = _lstat(output)
        if after is None or _stable_stat_identity(before) != _stable_stat_identity(after):
            raise ConfigurationError("independent pytest shadow output changed while read")
        validated = _validate_shadow_evidence(payload)
        if validated["exit_code"] != run["exit_code"]:
            raise ConfigurationError(
                "independent pytest shadow exit evidence disagrees with Docker"
            )
        return {
            **run,
            "node_count": len(validated["nodeids"]),
            "nodeids": validated["nodeids"],
            "nodeid_sha256": validated["nodeid_sha256"],
            "complete_per_node_outcomes": validated["complete_per_node_outcomes"],
            "all_nodes_non_failing": validated["all_nodes_non_failing"],
            "failing_nodeids": validated["failing_nodeids"],
        }


def _product_collection_snapshot(
    root: Path,
    expected_profile_sha256: str,
    expected_targets: Sequence[str],
) -> dict[str, Any]:
    path = root / ".zerorun" / "pytest-collection-v1.json"
    before = _lstat(path)
    if (
        before is None
        or _is_link_like_stat(before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size <= 0
        or before.st_size > _MAX_SHADOW_EVIDENCE_BYTES
    ):
        raise ConfigurationError("ZeroRun collection snapshot is missing or oversized")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"ZeroRun collection snapshot is unreadable: {exc}") from exc
    after = _lstat(path)
    if after is None or _stable_stat_identity(before) != _stable_stat_identity(after):
        raise ConfigurationError("ZeroRun collection snapshot changed while read")
    if not isinstance(payload, dict):
        raise ConfigurationError("ZeroRun collection snapshot is not an object")
    expected_wrapper_fields = {
        "schema",
        "profile_sha256",
        "collection",
        "collection_guard_sha256",
        "provenance",
    }
    profile_sha256 = payload.get("profile_sha256")
    collection_guard_sha256 = payload.get("collection_guard_sha256")
    if (
        set(payload) != expected_wrapper_fields
        or payload.get("schema") != 2
        or profile_sha256 != expected_profile_sha256
        or re.fullmatch(r"[0-9a-f]{64}", expected_profile_sha256) is None
        or not (
            collection_guard_sha256 is None
            or re.fullmatch(r"[0-9a-f]{64}", str(collection_guard_sha256))
            is not None
        )
    ):
        raise ConfigurationError("ZeroRun collection snapshot has invalid structure")
    manifest = load_manifest(root / ".zerorun.json")
    if not cache_payload_is_trusted(
        root,
        payload,
        manifest_digest=manifest_sha256(manifest),
        profile_digest=expected_profile_sha256,
    ):
        raise ConfigurationError("ZeroRun collection snapshot has invalid provenance")

    collection = payload.get("collection")
    nodeids = collection.get("nodeids") if isinstance(collection, dict) else None
    digest = collection.get("collection_sha256") if isinstance(collection, dict) else None
    invocation = collection.get("invocation") if isinstance(collection, dict) else None
    items = collection.get("items") if isinstance(collection, dict) else None
    if (
        not isinstance(collection, dict)
        or set(collection) != {"invocation", "items", "nodeids", "collection_sha256"}
        or not isinstance(invocation, dict)
        or set(invocation) != {"rootpath", "args", "pyargs", "addopts"}
        or invocation.get("args") != list(expected_targets)
        or invocation.get("pyargs") is not False
        or not isinstance(invocation.get("rootpath"), str)
        or not isinstance(invocation.get("addopts"), list)
        or any(not isinstance(token, str) for token in invocation.get("addopts", []))
        or not isinstance(items, list)
        or not isinstance(nodeids, list)
        or len(items) != len(nodeids)
        or len(nodeids) > _MAX_SHADOW_NODES
        or len(nodeids) != len(set(nodeids))
        or any(
            not isinstance(nodeid, str)
            or not nodeid
            or "\x00" in nodeid
            or len(nodeid) > 64 * 1024
            for nodeid in nodeids
        )
        or sum(len(nodeid) for nodeid in nodeids) > _MAX_SHADOW_NODEID_CHARS
        or any(
            not isinstance(item, dict)
            or item.get("nodeid") != nodeids[index]
            for index, item in enumerate(items)
        )
        or re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None
    ):
        raise ConfigurationError("ZeroRun collection snapshot has invalid structure")
    canonical = json.dumps(
        {"invocation": invocation, "items": items, "nodeids": nodeids},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise ConfigurationError("ZeroRun collection snapshot digest mismatch")
    return {"nodeids": nodeids, "collection_sha256": digest}


def _candidate_counts(payload: dict[str, Any]) -> tuple[int, int, int]:
    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict):
        return 0, 0, 0
    reviewable = sum(
        1
        for row in nodes.values()
        if isinstance(row, dict)
        and row.get("reviewable") is True
        and row.get("fresh_required") is not True
    )
    forced_fresh = sum(
        1
        for row in nodes.values()
        if isinstance(row, dict) and row.get("fresh_required") is True
    )
    return len(nodes), reviewable, forced_fresh


def _candidate_fresh_reason_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain refusal causes without changing qualification or gate decisions."""
    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict) or any(
        not isinstance(row, dict) for row in nodes.values()
    ):
        raise ConfigurationError("candidate refusal summary requires node objects")
    counts: dict[str, int] = {}
    unspecified = 0
    for row in nodes.values():
        if row.get("fresh_required") is not True:
            continue
        reason = row.get("reason")
        if reason is None or reason == "":
            reason = "<reason not recorded>"
            unspecified += 1
        elif not isinstance(reason, str) or len(reason) > 8192:
            raise ConfigurationError("candidate refusal reason is invalid or oversized")
        counts[reason] = counts.get(reason, 0) + 1
    return {
        "schema": "zerorun.candidate-fresh-reasons.v1",
        "fresh_required_nodes": sum(counts.values()),
        "nodes_with_unspecified_reason": unspecified,
        "complete_reason_labels": unspecified == 0,
        "histogram": [
            {"count": count, "reason": reason}
            for reason, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
    }


def _trajectory_schedule(
    *,
    workload: str,
    frozen_sha: str,
    targets: Sequence[str],
) -> dict[str, Any]:
    seed_material = json.dumps(
        {
            "methodology": _METHODOLOGY_VERSION,
            "workload": workload,
            "frozen_sha": frozen_sha,
            "targets": list(targets),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
    orders = list(_TRAJECTORY_ORDER_LABELS)
    if int(digest[:2], 16) & 1:
        orders.reverse()
    return {
        "seed_material": seed_material,
        "seed_sha256": digest,
        "execution_order": orders,
        "counterbalance_complete": set(orders) == set(_TRAJECTORY_ORDER_LABELS),
    }


@contextmanager
def _isolated_worktree(
    source_root: Path,
    *,
    git: str,
    seed_sha: str,
    trajectory_index: int,
    expected_dependency_snapshot: dict[str, Any] | None = None,
    dependency_cleanup_record: dict[str, Any] | None = None,
):
    source_root = source_root.expanduser().resolve(strict=True)
    parent = source_root.parent.resolve(strict=True)
    destination = parent / (
        f".zerorun-generalization-trajectory-{trajectory_index}-"
        + secrets.token_hex(12)
    )
    if destination.exists() or destination.is_symlink():
        raise ConfigurationError(
            f"random isolated trajectory path unexpectedly exists: {destination}"
        )
    created = False
    cleanup_error: str | None = None
    dependency_verification_ms = 0.0
    dependency_cleanup_ms = 0.0
    try:
        added = _run(
            source_root,
            [git, "worktree", "add", "--detach", str(destination), seed_sha],
            timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
        )
        if added.returncode != 0:
            raise ConfigurationError(
                "could not create isolated benchmark trajectory worktree: "
                + added.stderr
            )
        created = True
        resolved = destination.resolve(strict=True)
        if resolved.parent != parent or resolved != destination:
            raise ConfigurationError(
                "isolated benchmark trajectory escaped its exact parent directory"
            )
        actual = _run(
            resolved,
            [git, "rev-parse", "--verify", "HEAD"],
            check=True,
        ).stdout.strip()
        if actual != seed_sha:
            raise ConfigurationError(
                "isolated benchmark trajectory did not start at the frozen seed"
            )
        yield resolved
    finally:
        if created:
            if destination.exists() or destination.is_symlink():
                if expected_dependency_snapshot is not None:
                    verification_started = time.perf_counter()
                    try:
                        post_run_snapshot = _stable_dependency_tree_snapshot(
                            destination / ".zerorun-env",
                            require_read_only=True,
                        )
                        _require_matching_dependency_snapshot(
                            post_run_snapshot,
                            expected_dependency_snapshot,
                            label="post-trajectory private",
                        )
                    except ConfigurationError as exc:
                        cleanup_error = (
                            "private dependency post-run integrity was not "
                            f"confirmed: {exc}"
                        )
                    finally:
                        dependency_verification_ms = (
                            time.perf_counter() - verification_started
                        ) * 1000.0
                cleanup_started = time.perf_counter()
                try:
                    _remove_benchmark_state_path(
                        destination,
                        destination / ".zerorun-env",
                    )
                except ConfigurationError as exc:
                    cleanup_error = (
                        "private dependency materialization cleanup was not "
                        f"confirmed: {exc}"
                    )
                finally:
                    dependency_cleanup_ms = (
                        time.perf_counter() - cleanup_started
                    ) * 1000.0
            removed = _run(
                source_root,
                [git, "worktree", "remove", "--force", str(destination)],
                timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
            )
            if removed.returncode != 0:
                cleanup_error = cleanup_error or removed.stderr
            if destination.exists() or destination.is_symlink():
                cleanup_error = cleanup_error or "trajectory directory remains present"
            pruned = _run(
                source_root,
                [git, "worktree", "prune", "--expire", "now"],
                timeout_seconds=_COMMAND_TIMEOUT_SECONDS,
            )
            if pruned.returncode != 0:
                cleanup_error = cleanup_error or pruned.stderr
        if cleanup_error is not None:
            raise ConfigurationError(
                "isolated benchmark trajectory cleanup was not confirmed: "
                + cleanup_error
            )
        if created and dependency_cleanup_record is not None:
            dependency_verification_ms = _finite_nonnegative_ms(
                "private_dependency_post_run_verification",
                dependency_verification_ms,
            )
            dependency_cleanup_ms = _finite_nonnegative_ms(
                "private_dependency_cleanup",
                dependency_cleanup_ms,
            )
            dependency_cleanup_record.update(
                {
                    "schema": "zerorun.private-dependency-post-use-cleanup.v1",
                    "post_run_integrity_verification_ms": dependency_verification_ms,
                    "private_environment_cleanup_ms": dependency_cleanup_ms,
                    "total_ms": _finite_nonnegative_ms(
                        "private_dependency_post_use_cleanup",
                        dependency_verification_ms + dependency_cleanup_ms,
                    ),
                    "post_run_integrity_verified": (
                        expected_dependency_snapshot is not None
                    ),
                    "cleanup_confirmed": True,
                }
            )


def _safe_product_once(root: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        manifest = load_manifest(root / ".zerorun.json")
        profile = load_pytest_profile(root / ".zerorun-pytest.json", manifest)
        payload = run_pytest_profile(manifest, profile)
    except Exception as exc:
        payload = {
            "status": "BENCHMARK_PRODUCT_ERROR",
            "exit_code": 98,
            "wall_ms": (time.perf_counter() - started) * 1000.0,
            "reused_nodes": 0,
            "fresh_nodes": 0,
            "unknown_nodes": 0,
            "total_nodes": 0,
            "stderr_tail": f"{type(exc).__name__}: {exc}",
        }
    payload = dict(payload)
    payload["benchmark_outer_wall_ms"] = (
        time.perf_counter() - started
    ) * 1000.0
    return payload


def _safe_direct_once(
    root: Path,
    *,
    targets: Sequence[str],
    runtime_image: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        return _direct_once(root, targets=targets, runtime_image=runtime_image)
    except Exception as exc:
        return {
            "exit_code": 97,
            "wall_ms": (time.perf_counter() - started) * 1000.0,
            "node_count": None,
            "collection_sha256": None,
            "stdout_tail": "",
            "stderr_tail": f"{type(exc).__name__}: {exc}",
            "runner": "independent-docker-plain-pytest",
            "instrumented": False,
        }


def _safe_shadow_once(
    root: Path,
    *,
    targets: Sequence[str],
    runtime_image: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        return _shadow_once(root, targets=targets, runtime_image=runtime_image)
    except Exception as exc:
        return {
            "exit_code": 96,
            "wall_ms": (time.perf_counter() - started) * 1000.0,
            "node_count": 0,
            "nodeids": [],
            "nodeid_sha256": None,
            "complete_per_node_outcomes": False,
            "all_nodes_non_failing": False,
            "failing_nodeids": [],
            "stderr_tail": f"{type(exc).__name__}: {exc}",
            "runner": "independent-docker-plain-pytest-shadow-error",
            "instrumented": True,
        }


def _safe_product_snapshot(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        manifest = load_manifest(root / ".zerorun.json")
        profile = load_pytest_profile(root / ".zerorun-pytest.json", manifest)
        return (
            _product_collection_snapshot(
                root,
                profile.profile_sha256,
                profile.targets,
            ),
            None,
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _comparison_evidence(
    *,
    direct: dict[str, Any],
    product: dict[str, Any],
    shadow: dict[str, Any],
    snapshot: dict[str, Any] | None,
    snapshot_error: str | None,
) -> dict[str, Any]:
    direct_code = int(direct.get("exit_code", 99))
    product_code = int(product.get("exit_code", 99))
    shadow_code = int(shadow.get("exit_code", 99))
    product_counts = _validated_product_node_counts(product)
    reused = product_counts["reused_nodes"]
    product_digest = product.get("collection_sha256")
    snapshot_nodeids = snapshot.get("nodeids") if isinstance(snapshot, dict) else None
    snapshot_nodeid_sha256 = None
    if isinstance(snapshot_nodeids, list):
        snapshot_nodeid_sha256 = hashlib.sha256(
            json.dumps(
                {"nodeids": snapshot_nodeids},
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
    exact_collection = bool(
        snapshot is not None
        and snapshot_error is None
        and shadow.get("nodeids") == snapshot.get("nodeids")
        and product_digest == snapshot.get("collection_sha256")
        and int(product.get("total_nodes", 0) or 0) == len(shadow.get("nodeids", []))
    )
    exits_match = direct_code == product_code == shadow_code
    reuse_shadow_valid = bool(
        reused == 0
        or (
            exact_collection
            and direct_code == 0
            and product_code == 0
            and shadow_code == 0
            and shadow.get("complete_per_node_outcomes") is True
            and shadow.get("all_nodes_non_failing") is True
        )
    )
    return {
        "direct_exit_code": direct_code,
        "zerorun_exit_code": product_code,
        "all_exit_codes_match": exits_match,
        "exact_node_sequence_match": exact_collection,
        "product_snapshot_error": snapshot_error,
        "product_collection_sha256": product_digest,
        "product_snapshot_collection_sha256": (
            snapshot.get("collection_sha256") if isinstance(snapshot, dict) else None
        ),
        "product_snapshot_nodeid_sha256": snapshot_nodeid_sha256,
        "product_snapshot_node_count": (
            len(snapshot_nodeids) if isinstance(snapshot_nodeids, list) else 0
        ),
        "independent_shadow_nodeid_sha256": shadow.get("nodeid_sha256"),
        "independent_shadow_node_count": int(shadow.get("node_count", 0) or 0),
        "independent_shadow_exit_code": shadow_code,
        "independent_shadow_complete_per_node_outcomes": shadow.get(
            "complete_per_node_outcomes"
        )
        is True,
        "independent_shadow_all_nodes_non_failing": shadow.get(
            "all_nodes_non_failing"
        )
        is True,
        "reused_node_fresh_shadow_coverage": reused if reuse_shadow_valid else 0,
        "reused_node_fresh_shadow_denominator": reused,
        "reuse_shadow_valid": reuse_shadow_valid,
        "comparison_pass": exits_match and exact_collection and reuse_shadow_valid,
        "coverage_basis": _COMPARISON_COVERAGE_BASIS,
    }


def _validated_product_node_counts(product: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in (
        "reused_nodes",
        "fresh_nodes",
        "unknown_nodes",
        "total_nodes",
        "published_nodes",
    ):
        value = product.get(name, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigurationError(
                f"ZeroRun result {name!r} must be a nonnegative integer"
            )
        counts[name] = value
    if counts["reused_nodes"] + counts["fresh_nodes"] != counts["total_nodes"]:
        raise ConfigurationError("ZeroRun result node counts do not reconcile")
    if counts["unknown_nodes"] > counts["fresh_nodes"]:
        raise ConfigurationError("ZeroRun unknown nodes exceed fresh nodes")
    if counts["published_nodes"] > counts["fresh_nodes"]:
        raise ConfigurationError("ZeroRun published nodes exceed fresh nodes")
    return counts


def _refresh_signal_fail_closed(product: dict[str, Any]) -> bool:
    return bool(
        product.get("profile_refresh_required") is not True
        or (
            int(product.get("reused_nodes", 0) or 0) == 0
            and int(product.get("published_nodes", 0) or 0) == 0
        )
    )


def _sum_numeric_mappings(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    names = sorted({name for row in rows for name in row})
    return {
        name: round(sum(float(row.get(name, 0.0)) for row in rows), 3)
        for name in names
    }


def _cache_publication_state(root: Path) -> dict[str, Any]:
    """Digest only reusable cache/snapshot state, excluding append-only events."""

    state = root / ".zerorun"
    selected = (state / "cache", state / "pytest-collection-v1.json")
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for anchor in selected:
        if not anchor.exists() and not anchor.is_symlink():
            continue
        candidates = [anchor]
        if anchor.is_dir() and not anchor.is_symlink():
            candidates.extend(
                sorted(
                    anchor.rglob("*"),
                    key=lambda path: (
                        path.relative_to(root).as_posix().casefold(),
                        path.relative_to(root).as_posix(),
                    ),
                )
            )
        for path in candidates:
            if len(rows) >= 100_000:
                raise ConfigurationError("adverse-audit cache state is oversized")
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            if _is_link_like_stat(metadata):
                raise ConfigurationError(
                    "adverse-audit cache state contains a link-like entry"
                )
            if stat.S_ISDIR(metadata.st_mode):
                rows.append({"path": relative, "kind": "directory"})
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigurationError(
                    "adverse-audit cache state contains a special entry"
                )
            if metadata.st_size > 32 * 1024 * 1024:
                raise ConfigurationError("adverse-audit cache entry is oversized")
            raw = path.read_bytes()
            if len(raw) != metadata.st_size:
                raise ConfigurationError("adverse-audit cache entry changed while read")
            total_bytes += len(raw)
            if total_bytes > 512 * 1024 * 1024:
                raise ConfigurationError("adverse-audit cache payload is oversized")
            rows.append(
                {
                    "path": relative,
                    "kind": "file",
                    "size": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
    encoded = json.dumps(
        rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return {
        "schema": "zerorun.cache-publication-state.v1",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "file_count": sum(row["kind"] == "file" for row in rows),
        "directory_count": sum(row["kind"] == "directory" for row in rows),
        "total_bytes": total_bytes,
    }


def _adverse_collection_failure_audit(
    root: Path,
    *,
    git: str,
    frozen_sha: str,
    targets: Sequence[str],
    runtime_image: str,
    order: str,
) -> dict[str, Any]:
    """Inject a transient collection failure outside the frozen performance rows."""

    root = root.resolve(strict=True)
    selected: Path | None = None
    selected_target: str | None = None
    for target in targets:
        lexical = root / target
        try:
            candidate = lexical.resolve(strict=True)
        except OSError:
            continue
        if (
            candidate.suffix == ".py"
            and _is_within(root, candidate)
            and candidate.is_file()
            and not _is_link_like_stat(candidate.lstat())
        ):
            selected = candidate
            selected_target = target
            break
    if selected is None or selected_target is None:
        return {
            "status": "UNSUPPORTED_NO_ORDINARY_PYTHON_TARGET",
            "pass": False,
            "included_in_performance_timing": False,
        }
    cache_before = _cache_publication_state(root)
    before = selected.lstat()
    if before.st_size > 64 * 1024 * 1024:
        return {
            "status": "UNSUPPORTED_OVERSIZED_TARGET",
            "pass": False,
            "included_in_performance_timing": False,
            "target": selected_target,
        }
    original = selected.read_bytes()
    if _stable_stat_identity(before) != _stable_stat_identity(selected.lstat()):
        raise ConfigurationError("adverse-audit target changed while it was read")
    injected = (
        b'raise RuntimeError("ZeroRun benchmark adverse collection sentinel")\n'
        + original
    )
    selected.write_bytes(injected)
    if hashlib.sha256(selected.read_bytes()).digest() == hashlib.sha256(original).digest():
        raise ConfigurationError("adverse-audit source injection was not observed")

    direct: dict[str, Any] | None = None
    product: dict[str, Any] | None = None
    shadow: dict[str, Any] | None = None
    cache_after: dict[str, Any] | None = None
    restored = False
    try:
        if order == "plain-then-zerorun":
            direct = _safe_direct_once(
                root, targets=targets, runtime_image=runtime_image
            )
            product = _safe_product_once(root)
        elif order == "zerorun-then-plain":
            product = _safe_product_once(root)
            direct = _safe_direct_once(
                root, targets=targets, runtime_image=runtime_image
            )
        else:
            raise ConfigurationError(f"unknown adverse-audit order: {order}")
        shadow = _safe_shadow_once(
            root, targets=targets, runtime_image=runtime_image
        )
        cache_after = _cache_publication_state(root)
    finally:
        _checkout(root, git, frozen_sha)
        restored_bytes = selected.read_bytes()
        restored = hashlib.sha256(restored_bytes).digest() == hashlib.sha256(
            original
        ).digest()
    assert (
        direct is not None
        and product is not None
        and shadow is not None
        and cache_after is not None
    )
    direct_code = int(direct.get("exit_code", 99))
    product_code = int(product.get("exit_code", 99))
    shadow_code = int(shadow.get("exit_code", 99))
    counts = _validated_product_node_counts(product)
    refusal_semantics = (
        product_code != 0
        and product.get("status") == "PYTEST_INCREMENTAL_FAIL"
        and counts["reused_nodes"] == 0
        and counts["published_nodes"] == 0
        and product.get("reuse_authorized") is False
        and product.get("profile_refresh_required") is False
        and product.get("profile_refresh_reasons") == []
    )
    cache_unchanged = cache_before == cache_after
    passed = (
        direct_code != 0
        and product_code != 0
        and shadow_code != 0
        and refusal_semantics
        and cache_unchanged
        and restored
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "pass": passed,
        "kind": "transient-changed-source-collection-failure",
        "target": selected_target,
        "order": order,
        "direct_exit_code": direct_code,
        "zerorun_exit_code": product_code,
        "direct_wall_ms": round(float(direct.get("wall_ms", 0.0) or 0.0), 3),
        "zerorun_wall_ms": round(
            float(product.get("benchmark_outer_wall_ms", 0.0) or 0.0), 3
        ),
        "independent_shadow_exit_code": shadow_code,
        "independent_shadow_wall_ms": round(
            float(shadow.get("wall_ms", 0.0) or 0.0), 3
        ),
        "zerorun_stale_success": direct_code != 0 and product_code == 0,
        "zerorun_status": product.get("status"),
        "zerorun_reused_nodes": counts["reused_nodes"],
        "zerorun_published_nodes": counts["published_nodes"],
        "zerorun_reuse_authorized": product.get("reuse_authorized"),
        "profile_refresh_required": product.get("profile_refresh_required"),
        "profile_refresh_reasons": product.get("profile_refresh_reasons"),
        "fail_closed_refusal_semantics": refusal_semantics,
        "cache_publication_state_before": cache_before,
        "cache_publication_state_after": cache_after,
        "cache_publication_state_unchanged": cache_unchanged,
        "source_restored_to_frozen_bytes": restored,
        "included_in_performance_timing": False,
        "scope_note": (
            "This excluded sensitivity audit mutates only the disposable detached "
            "worktree and restores the exact frozen file. It tests changed-source "
            "collection-failure handling, not an injected fault in a reused node."
        ),
    }


def _validate_benchmark_environment_contract(
    root: Path,
    *,
    targets: Sequence[str],
    extra_requirements: Sequence[str],
    runtime_image: str,
    case_count: int,
    min_compute: float,
    min_p95_reduction: float,
    expected_runtime_requirements_sha256: str | None,
) -> tuple[Path, str]:
    root = root.expanduser().resolve()
    if not (root / ".git").exists():
        raise ConfigurationError(f"benchmark root is not a Git repository: {root}")
    if not targets:
        raise ConfigurationError("benchmark requires at least one fixed pytest target")
    if case_count != 20:
        raise ConfigurationError(
            "the frozen generalization protocol requires exactly 20 transitions"
        )
    if (
        not math.isfinite(min_compute)
        or not math.isfinite(min_p95_reduction)
        or min_compute < 5.0
        or min_p95_reduction < 50.0
    ):
        raise ConfigurationError(
            "the frozen generalization gates cannot be lowered below 5x and 50 percent"
        )
    _validate_extra_requirements(extra_requirements)
    if tuple(DOCKER_RESOURCE_ARGS) != _PLAIN_DOCKER_RESOURCE_ARGS:
        raise ConfigurationError(
            "plain-pytest and ZeroRun container resource envelopes differ"
        )
    if runtime_image != GENERALIZATION_RUNTIME_IMAGE:
        raise ConfigurationError(
            "the live generalization protocol requires the exact official "
            f"{GENERALIZATION_RUNTIME_IMPLEMENTATION} "
            f"{GENERALIZATION_RUNTIME_PYTHON_VERSION} Linux/amd64 image digest"
        )
    runtime_requirements_sha256 = _runtime_requirements_sha256()
    if (
        expected_runtime_requirements_sha256 is not None
        and expected_runtime_requirements_sha256 != runtime_requirements_sha256
    ):
        raise ConfigurationError(
            "generalization runtime requirements digest differs from the "
            "release-workflow pin"
        )
    return root, runtime_requirements_sha256


def _charge_dependency_layer_post_use_cleanup(
    result: dict[str, Any],
    cleanup: object,
) -> dict[str, Any]:
    """Charge the measured layer postlude equally before releasing a receipt."""

    if not isinstance(cleanup, dict) or set(cleanup) != {
        "schema",
        "final_integrity_verification_ms",
        "private_layer_teardown_ms",
        "total_ms",
        "final_integrity_verified",
        "cleanup_confirmed",
    }:
        raise ConfigurationError("dependency-layer post-use cleanup evidence is incomplete")
    if (
        cleanup.get("schema") != "zerorun.dependency-layer-post-use-cleanup.v1"
        or cleanup.get("final_integrity_verified") is not True
        or cleanup.get("cleanup_confirmed") is not True
    ):
        raise ConfigurationError("dependency-layer post-use cleanup was not confirmed")
    verification_ms = _finite_nonnegative_ms(
        "dependency_layer_final_integrity_verification",
        cleanup["final_integrity_verification_ms"],
    )
    teardown_ms = _finite_nonnegative_ms(
        "dependency_layer_private_teardown",
        cleanup["private_layer_teardown_ms"],
    )
    cleanup_ms = _finite_nonnegative_ms(
        "dependency_layer_post_use_cleanup",
        cleanup["total_ms"],
    )
    if not math.isclose(
        cleanup_ms,
        verification_ms + teardown_ms,
        rel_tol=0.0,
        abs_tol=0.001,
    ):
        raise ConfigurationError("dependency-layer post-use cleanup timing does not reconcile")

    rows = result.get("rows")
    case_count = result.get("case_count")
    if (
        not isinstance(rows, list)
        or isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count <= 0
        or len(rows) != case_count
    ):
        raise ConfigurationError(
            "dependency-layer cleanup cannot be charged to a malformed horizon"
        )
    cleanup_per_transition_ms = cleanup_ms / case_count
    for row in rows:
        if not isinstance(row, dict):
            raise ConfigurationError(
                "dependency-layer cleanup cannot be charged to a malformed row"
            )
        direct_primary_ms = _finite_positive_ms(
            "pre_cleanup_primary_plain_pytest_row",
            row.get("primary_end_to_end_plain_pytest_ms"),
        ) + cleanup_per_transition_ms
        zerorun_primary_ms = _finite_positive_ms(
            "pre_cleanup_primary_zerorun_row",
            row.get("primary_end_to_end_zerorun_ms"),
        ) + cleanup_per_transition_ms
        direct_phase = row.get("direct_phase_ms")
        zerorun_timing = row.get("zerorun_timing_ms")
        if not isinstance(direct_phase, dict) or not isinstance(zerorun_timing, dict):
            raise ConfigurationError(
                "dependency-layer cleanup cannot be charged to malformed row phases"
            )
        direct_amortized_ms = _finite_nonnegative_ms(
            "pre_cleanup_direct_amortized_setup",
            direct_phase.get("amortized_environment_and_direct_seed"),
        ) + cleanup_per_transition_ms
        zerorun_amortized_ms = _finite_nonnegative_ms(
            "pre_cleanup_zerorun_amortized_setup",
            zerorun_timing.get(
                "amortized_environment_qualification_activation_and_seed"
            ),
        ) + cleanup_per_transition_ms
        row["direct_ms"] = round(direct_primary_ms, 3)
        row["zerorun_ms"] = round(zerorun_primary_ms, 3)
        row["primary_end_to_end_plain_pytest_ms"] = round(direct_primary_ms, 3)
        row["primary_end_to_end_zerorun_ms"] = round(zerorun_primary_ms, 3)
        direct_phase["amortized_environment_and_direct_seed"] = round(
            direct_amortized_ms,
            3,
        )
        direct_phase["primary_end_to_end_paired_total"] = round(
            direct_primary_ms,
            3,
        )
        zerorun_timing[
            "amortized_environment_qualification_activation_and_seed"
        ] = round(zerorun_amortized_ms, 3)
        zerorun_timing["primary_end_to_end_paired_total"] = round(
            zerorun_primary_ms,
            3,
        )

    ledger = result.get("cost_ledger_ms")
    accounting = result.get("primary_cost_accounting")
    layer = result.get("frozen_dependency_layer")
    if not isinstance(ledger, dict) or not isinstance(accounting, dict) or not isinstance(
        layer, dict
    ):
        raise ConfigurationError(
            "dependency-layer cleanup cannot be charged to malformed accounting"
        )
    symmetric_before_ms = _finite_nonnegative_ms(
        "pre_cleanup_symmetric_environment_bootstrap",
        ledger.get("symmetric_environment_bootstrap_charged_to_each_arm"),
    )
    symmetric_after_ms = symmetric_before_ms + cleanup_ms
    ledger["symmetric_environment_bootstrap_charged_to_each_arm"] = round(
        symmetric_after_ms,
        3,
    )
    accounting["direct_overhead_before_horizon_amortization_ms"] = round(
        symmetric_after_ms
        + _finite_nonnegative_ms(
            "direct_seed_charged_to_direct",
            ledger.get("direct_seed_charged_to_direct"),
        ),
        3,
    )
    accounting["zerorun_overhead_before_horizon_amortization_ms"] = round(
        symmetric_after_ms
        + math.fsum(
            _finite_nonnegative_ms(name, ledger.get(name))
            for name in (
                "zerorun_qualification_charged_to_zerorun",
                "zerorun_activation_charged_to_zerorun",
                "zerorun_seed_charged_to_zerorun",
                "automatic_refresh_charged_to_zerorun",
            )
        ),
        3,
    )

    timing = layer.get("timing_ms")
    if not isinstance(timing, dict):
        raise ConfigurationError("dependency-layer timing record is malformed")
    build_only_ms = _finite_nonnegative_ms(
        "dependency_layer_build_only",
        timing.get("build_once"),
    )
    layer_lifecycle_ms = build_only_ms + cleanup_ms
    # Preserve the existing workflow's build-once reconciliation while exposing
    # the exact build/postlude split. This compatibility field now represents
    # the complete one-time layer lifecycle, all of which is common to both arms.
    layer["build_only_ms"] = round(build_only_ms, 3)
    layer["build_once_ms"] = round(layer_lifecycle_ms, 3)
    layer["post_use_cleanup"] = {
        key: round(float(value), 3) if key.endswith("_ms") else value
        for key, value in cleanup.items()
    }
    timing["build_only"] = round(build_only_ms, 3)
    timing["final_integrity_verification"] = round(verification_ms, 3)
    timing["private_layer_teardown"] = round(teardown_ms, 3)
    timing["build_once"] = round(layer_lifecycle_ms, 3)
    timing["total_charged_symmetrically_to_each_arm"] = round(
        symmetric_after_ms,
        3,
    )

    direct_times = [float(row["direct_ms"]) for row in rows]
    zerorun_times = [float(row["zerorun_ms"]) for row in rows]
    direct_total = _finite_positive_ms(
        "post_cleanup_primary_plain_pytest_total",
        math.fsum(direct_times),
    )
    zerorun_total = _finite_positive_ms(
        "post_cleanup_primary_zerorun_total",
        math.fsum(zerorun_times),
    )
    speedup = direct_total / zerorun_total
    direct_p95 = _finite_positive_ms(
        "post_cleanup_primary_plain_pytest_p95",
        percentile(direct_times, 0.95),
    )
    zerorun_p95 = _finite_positive_ms(
        "post_cleanup_primary_zerorun_p95",
        percentile(zerorun_times, 0.95),
    )
    p95_reduction = (1.0 - zerorun_p95 / direct_p95) * 100.0
    result["direct_total_ms"] = round(direct_total, 3)
    result["zerorun_total_ms"] = round(zerorun_total, 3)
    result["end_to_end_plain_pytest_to_zerorun_speedup"] = round(speedup, 6)
    result["same_runner_compute_efficiency"] = round(speedup, 6)
    result["direct_p95_ms"] = round(direct_p95, 3)
    result["zerorun_p95_ms"] = round(zerorun_p95, 3)
    result["end_to_end_p95_reduction_percent"] = round(p95_reduction, 3)
    result["p95_reduction_percent"] = round(p95_reduction, 3)
    gate = result.get("reference_gate")
    if not isinstance(gate, dict):
        raise ConfigurationError("dependency-layer cleanup found a malformed gate")
    result["performance_gate_pass"] = bool(result.get("safety_pass") is True) and (
        speedup
        >= _finite_positive_ms(
            "reference_min_compute_efficiency",
            gate.get("min_compute_efficiency"),
        )
        and p95_reduction
        >= _finite_nonnegative_ms(
            "reference_min_p95_reduction_percent",
            gate.get("min_p95_reduction_percent"),
        )
    )
    return result


def _benchmark_once(
    root: Path,
    *,
    workload: str,
    upstream_repo: str,
    frozen_sha: str,
    targets: Sequence[str],
    extra_requirements: Sequence[str],
    runtime_image: str,
    case_count: int,
    min_compute: float,
    min_p95_reduction: float,
    engine_sha: str | None,
    holdout_class: str,
    expected_runtime_requirements_sha256: str | None = None,
) -> dict[str, Any]:
    root, _ = _validate_benchmark_environment_contract(
        root,
        targets=targets,
        extra_requirements=extra_requirements,
        runtime_image=runtime_image,
        case_count=case_count,
        min_compute=min_compute,
        min_p95_reduction=min_p95_reduction,
        expected_runtime_requirements_sha256=(
            expected_runtime_requirements_sha256
        ),
    )
    with _frozen_dependency_layer(
        root,
        runtime_image=runtime_image,
        extra_requirements=extra_requirements,
    ) as dependency_layer:
        result = _benchmark_once_with_dependency_layer(
            root,
            workload=workload,
            upstream_repo=upstream_repo,
            frozen_sha=frozen_sha,
            targets=targets,
            extra_requirements=extra_requirements,
            runtime_image=runtime_image,
            case_count=case_count,
            min_compute=min_compute,
            min_p95_reduction=min_p95_reduction,
            engine_sha=engine_sha,
            holdout_class=holdout_class,
            dependency_layer=dependency_layer,
            expected_runtime_requirements_sha256=(
                expected_runtime_requirements_sha256
            ),
        )
    return _charge_dependency_layer_post_use_cleanup(
        result,
        dependency_layer.get("post_use_cleanup"),
    )


def _benchmark_once_with_dependency_layer(
    root: Path,
    *,
    workload: str,
    upstream_repo: str,
    frozen_sha: str,
    targets: Sequence[str],
    extra_requirements: Sequence[str],
    runtime_image: str,
    case_count: int,
    min_compute: float,
    min_p95_reduction: float,
    engine_sha: str | None,
    holdout_class: str,
    dependency_layer: dict[str, Any],
    expected_runtime_requirements_sha256: str | None = None,
) -> dict[str, Any]:
    root, runtime_requirements_sha256 = _validate_benchmark_environment_contract(
        root,
        targets=targets,
        extra_requirements=extra_requirements,
        runtime_image=runtime_image,
        case_count=case_count,
        min_compute=min_compute,
        min_p95_reduction=min_p95_reduction,
        expected_runtime_requirements_sha256=(
            expected_runtime_requirements_sha256
        ),
    )
    if (
        dependency_layer.get("runtime_requirements_sha256")
        != runtime_requirements_sha256
    ):
        raise ConfigurationError(
            "dependency layer does not match the frozen runtime requirements"
        )

    git = _host_executable(root, "git")
    corpus = _corpus(root, git, frozen_sha, case_count)
    seed, *cases = corpus
    schedule = _trajectory_schedule(
        workload=workload,
        frozen_sha=frozen_sha,
        targets=targets,
    )
    if not schedule["counterbalance_complete"]:
        raise ConfigurationError("counterbalanced trajectory schedule is incomplete")

    observations_by_case: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(1, case_count + 1)
    }
    trajectory_records: list[dict[str, Any]] = []
    adverse_audits: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    activations: list[dict[str, Any]] = []
    dependency_layer_build_ms = _finite_nonnegative_ms(
        "dependency_layer_build_once",
        dependency_layer.get("build_ms"),
    )
    environment_bootstrap_total_ms = dependency_layer_build_ms
    environment_materializations: list[dict[str, Any]] = []
    private_dependency_cleanup_records: list[dict[str, Any]] = []
    qualification_total_ms = 0.0
    activation_total_ms = 0.0
    direct_seed_total_ms = 0.0
    zerorun_seed_total_ms = 0.0
    shadow_oracle_total_ms = 0.0
    automatic_refresh_total_ms = 0.0
    refresh_required_observations = 0
    refresh_fail_closed_violations = 0
    observed_runtime_image: str | None = None

    for trajectory_index, order in enumerate(schedule["execution_order"], 1):
        dependency_cleanup_record: dict[str, Any] = {}
        with _isolated_worktree(
            root,
            git=git,
            seed_sha=seed,
            trajectory_index=trajectory_index,
            expected_dependency_snapshot=dependency_layer["snapshot"],
            dependency_cleanup_record=dependency_cleanup_record,
        ) as trajectory_root:
            _clean_zerorun_state(trajectory_root)
            environment_materialization = _materialize_frozen_pytest_environment(
                trajectory_root,
                dependency_layer=dependency_layer,
                runtime_image=runtime_image,
                extra_requirements=extra_requirements,
            )
            environment_ms = _finite_nonnegative_ms(
                f"trajectory_{trajectory_index}_environment_bootstrap",
                environment_materialization.get("environment_bootstrap_ms"),
            )
            environment_bootstrap_total_ms += environment_ms
            environment_materializations.append(environment_materialization)
            if (
                environment_materialization.get("content_tree_sha256")
                != dependency_layer["snapshot"]["tree_sha256"]
                or environment_materialization.get(
                    "construction_identity_sha256"
                )
                != dependency_layer["construction"][
                    "construction_identity_sha256"
                ]
            ):
                raise ConfigurationError(
                    "trajectory materialization does not match the frozen dependency layer"
                )

            candidate: dict[str, Any] | None = None
            activated: dict[str, Any] | None = None
            qualification_ms = 0.0
            activation_ms = 0.0

            def qualify_and_activate() -> None:
                nonlocal candidate, activated, qualification_ms, activation_ms
                qualification_started = time.perf_counter()
                candidate = prepare_candidate(
                    trajectory_root,
                    task_name="pytest-generalization",
                    targets=tuple(targets),
                )
                qualification_ms = (
                    time.perf_counter() - qualification_started
                ) * 1000.0
                qualification_ms = _finite_nonnegative_ms(
                    f"trajectory_{trajectory_index}_zerorun_qualification",
                    qualification_ms,
                )
                activation_started = time.perf_counter()
                activated = _activate_exact_candidate(trajectory_root)
                activation_ms = (
                    time.perf_counter() - activation_started
                ) * 1000.0
                activation_ms = _finite_nonnegative_ms(
                    f"trajectory_{trajectory_index}_zerorun_activation",
                    activation_ms,
                )

            if order == "plain-then-zerorun":
                seed_direct = _safe_direct_once(
                    trajectory_root,
                    targets=targets,
                    runtime_image=runtime_image,
                )
                qualify_and_activate()
                seed_product = _safe_product_once(trajectory_root)
            elif order == "zerorun-then-plain":
                qualify_and_activate()
                seed_product = _safe_product_once(trajectory_root)
                seed_direct = _safe_direct_once(
                    trajectory_root,
                    targets=targets,
                    runtime_image=runtime_image,
                )
            else:
                raise ConfigurationError(f"unknown trajectory order: {order}")
            assert candidate is not None and activated is not None
            candidate_count, reviewable_count, forced_fresh_count = _candidate_counts(
                candidate
            )
            candidates.append(candidate)
            activations.append(activated)
            qualification_total_ms += qualification_ms
            activation_total_ms += activation_ms
            direct_seed_ms = _finite_nonnegative_ms(
                f"trajectory_{trajectory_index}_direct_seed",
                seed_direct["wall_ms"],
            )
            zerorun_seed_ms = _finite_nonnegative_ms(
                f"trajectory_{trajectory_index}_zerorun_seed",
                seed_product["benchmark_outer_wall_ms"],
            )
            direct_seed_total_ms += direct_seed_ms
            zerorun_seed_total_ms += zerorun_seed_ms

            manifest = load_manifest(trajectory_root / ".zerorun.json")
            profile = load_pytest_profile(
                trajectory_root / ".zerorun-pytest.json", manifest
            )
            task = manifest.tasks[profile.task_name]
            if observed_runtime_image is None:
                observed_runtime_image = task.image
            elif observed_runtime_image != task.image:
                raise ConfigurationError(
                    "counterbalanced trajectories used different runtime images"
                )

            seed_shadow = _safe_shadow_once(
                trajectory_root,
                targets=targets,
                runtime_image=runtime_image,
            )
            shadow_oracle_total_ms += _finite_nonnegative_ms(
                f"trajectory_{trajectory_index}_seed_shadow_excluded",
                seed_shadow["wall_ms"],
            )
            seed_snapshot, seed_snapshot_error = _safe_product_snapshot(
                trajectory_root
            )
            seed_comparison = _comparison_evidence(
                direct=seed_direct,
                product=seed_product,
                shadow=seed_shadow,
                snapshot=seed_snapshot,
                snapshot_error=seed_snapshot_error,
            )
            if (
                int(seed_direct.get("exit_code", 99)) != 0
                or int(seed_product.get("exit_code", 99)) != 0
                or seed_comparison["comparison_pass"] is not True
            ):
                raise ConfigurationError(
                    "counterbalanced seed failed independent plain-pytest equivalence: "
                    f"order={order}, direct={seed_direct.get('exit_code')}, "
                    f"zerorun={seed_product.get('exit_code')}, "
                    f"direct_stderr={str(seed_direct.get('stderr_tail', ''))[-2000:]!r}, "
                    f"zerorun_stderr={str(seed_product.get('stderr_tail', ''))[-2000:]!r}, "
                    f"comparison={seed_comparison}"
                )

            previous = seed
            trajectory_observations: list[dict[str, Any]] = []
            trajectory_refresh_required = 0
            trajectory_refresh_violations = 0
            for index, sha in enumerate(cases, 1):
                _checkout(trajectory_root, git, sha)
                if order == "plain-then-zerorun":
                    direct = _safe_direct_once(
                        trajectory_root,
                        targets=targets,
                        runtime_image=runtime_image,
                    )
                    product = _safe_product_once(trajectory_root)
                else:
                    product = _safe_product_once(trajectory_root)
                    direct = _safe_direct_once(
                        trajectory_root,
                        targets=targets,
                        runtime_image=runtime_image,
                    )
                shadow = _safe_shadow_once(
                    trajectory_root,
                    targets=targets,
                    runtime_image=runtime_image,
                )
                shadow_oracle_total_ms += _finite_nonnegative_ms(
                    f"trajectory_{trajectory_index}_transition_{index}_shadow_excluded",
                    shadow["wall_ms"],
                )
                snapshot, snapshot_error = _safe_product_snapshot(
                    trajectory_root
                )
                comparison = _comparison_evidence(
                    direct=direct,
                    product=product,
                    shadow=shadow,
                    snapshot=snapshot,
                    snapshot_error=snapshot_error,
                )

                refresh_required = product.get("profile_refresh_required") is True
                refresh_fail_closed = _refresh_signal_fail_closed(product)
                if refresh_required:
                    refresh_required_observations += 1
                    trajectory_refresh_required += 1
                if not refresh_fail_closed:
                    refresh_fail_closed_violations += 1
                    trajectory_refresh_violations += 1

                observation = {
                    "trajectory": trajectory_index,
                    "order": order,
                    "case": index,
                    "commit": sha,
                    "previous_commit": previous,
                    "direct_exit_code": int(direct.get("exit_code", 99)),
                    "zerorun_exit_code": int(product.get("exit_code", 99)),
                    "direct_transition_wall_ms": round(float(direct["wall_ms"]), 3),
                    "zerorun_transition_wall_ms": round(
                        float(product["benchmark_outer_wall_ms"]), 3
                    ),
                    "direct_phase_ms": _direct_phase_ms(direct),
                    "zerorun_phase_ms": _reported_phase_ms(product),
                    "zerorun_phase_ms_emitted": "phase_ms" in product,
                    "zerorun_timing_ms": _zerorun_timing_ms(
                        product,
                        outer_wall_ms=float(product["benchmark_outer_wall_ms"]),
                    ),
                    "zerorun_status": str(product.get("status", "")),
                    "reused_nodes": int(product.get("reused_nodes", 0) or 0),
                    "fresh_nodes": int(product.get("fresh_nodes", 0) or 0),
                    "unknown_nodes": int(product.get("unknown_nodes", 0) or 0),
                    "total_nodes": int(product.get("total_nodes", 0) or 0),
                    "published_nodes": int(product.get("published_nodes", 0) or 0),
                    "reuse_authorized": product.get("reuse_authorized") is True,
                    "zerorun_collection_sha256": product.get("collection_sha256"),
                    "profile_refresh_required": refresh_required,
                    "profile_refresh_fail_closed": refresh_fail_closed,
                    "profile_refresh_reasons": product.get(
                        "profile_refresh_reasons", []
                    ),
                    "comparison": comparison,
                    "direct_stderr_tail": str(direct.get("stderr_tail", ""))[-2000:],
                    "zerorun_stderr_tail": str(product.get("stderr_tail", ""))[-2000:],
                    "shadow_stderr_tail": str(shadow.get("stderr_tail", ""))[-2000:],
                }
                observations_by_case[index].append(observation)
                trajectory_observations.append(observation)
                print(
                    f"[{workload} trajectory={trajectory_index} order={order} "
                    f"{index:02d}/{case_count}] {sha[:10]} "
                    f"plain={float(direct['wall_ms']):.1f}ms "
                    f"zerorun={float(product['benchmark_outer_wall_ms']):.1f}ms "
                    f"reused={observation['reused_nodes']} "
                    f"fresh={observation['fresh_nodes']} "
                    f"shadow={'PASS' if comparison['comparison_pass'] else 'FAIL'}",
                    flush=True,
                )
                previous = sha

            adverse = _adverse_collection_failure_audit(
                trajectory_root,
                git=git,
                frozen_sha=frozen_sha,
                targets=targets,
                runtime_image=runtime_image,
                order=order,
            )
            adverse_audits.append(adverse)
            trajectory_records.append(
                {
                    "trajectory": trajectory_index,
                    "order": order,
                    "isolated_checkout_and_cache": True,
                    "environment_bootstrap_ms": round(environment_ms, 3),
                    "environment_materialization": {
                        key: (
                            round(float(value), 3)
                            if key.endswith("_ms")
                            else value
                        )
                        for key, value in environment_materialization.items()
                        if key != "environment_bootstrap_ms"
                    },
                    "qualification_ms": round(qualification_ms, 3),
                    "activation_ms": round(activation_ms, 3),
                    "direct_seed_ms": round(direct_seed_ms, 3),
                    "zerorun_seed_ms": round(zerorun_seed_ms, 3),
                    "seed_comparison": seed_comparison,
                    "candidate_sha256": candidate.get("candidate_sha256"),
                    "candidate_node_count": candidate_count,
                    "candidate_reviewable_nodes": reviewable_count,
                    "candidate_fresh_required_nodes": forced_fresh_count,
                    "candidate_fresh_reason_summary": _candidate_fresh_reason_summary(
                        candidate
                    ),
                    "activation_candidate_sha256": activated.get(
                        "candidate_sha256"
                    ),
                    "review_record_sha256": activated.get("review_record_sha256"),
                    "profile_refresh_required_observations": (
                        trajectory_refresh_required
                    ),
                    "profile_refresh_fail_closed_violations": (
                        trajectory_refresh_violations
                    ),
                    "automatic_refreshes_performed": 0,
                    "transition_count": len(trajectory_observations),
                    "adverse_audit": adverse,
                }
            )

        required_cleanup_fields = {
            "schema",
            "post_run_integrity_verification_ms",
            "private_environment_cleanup_ms",
            "total_ms",
            "post_run_integrity_verified",
            "cleanup_confirmed",
        }
        if (
            set(dependency_cleanup_record) != required_cleanup_fields
            or dependency_cleanup_record.get("schema")
            != "zerorun.private-dependency-post-use-cleanup.v1"
            or dependency_cleanup_record.get("post_run_integrity_verified") is not True
            or dependency_cleanup_record.get("cleanup_confirmed") is not True
        ):
            raise ConfigurationError(
                "private dependency post-use cleanup evidence is incomplete"
            )
        cleanup_verification_ms = _finite_nonnegative_ms(
            f"trajectory_{trajectory_index}_dependency_post_run_verification",
            dependency_cleanup_record["post_run_integrity_verification_ms"],
        )
        cleanup_remove_ms = _finite_nonnegative_ms(
            f"trajectory_{trajectory_index}_private_dependency_cleanup",
            dependency_cleanup_record["private_environment_cleanup_ms"],
        )
        cleanup_total_ms = _finite_nonnegative_ms(
            f"trajectory_{trajectory_index}_private_dependency_post_use",
            dependency_cleanup_record["total_ms"],
        )
        if not math.isclose(
            cleanup_total_ms,
            cleanup_verification_ms + cleanup_remove_ms,
            rel_tol=0.0,
            abs_tol=0.001,
        ):
            raise ConfigurationError(
                "private dependency post-use cleanup timing does not reconcile"
            )
        environment_bootstrap_total_ms += cleanup_total_ms
        environment_materialization["materialization_only_ms"] = environment_ms
        environment_materialization["environment_bootstrap_ms"] = (
            environment_ms + cleanup_total_ms
        )
        environment_materialization["timing_remainder_ms"] = (
            float(environment_materialization["timing_remainder_ms"])
            + cleanup_total_ms
        )
        environment_materialization["post_use_cleanup"] = dict(
            dependency_cleanup_record
        )
        private_dependency_cleanup_records.append(dict(dependency_cleanup_record))
        trajectory_records[-1]["environment_bootstrap_ms"] = round(
            environment_ms + cleanup_total_ms,
            3,
        )
        trajectory_records[-1]["environment_materialization"] = {
            key: (
                round(float(value), 3)
                if key.endswith("_ms") and isinstance(value, (int, float))
                else value
            )
            for key, value in environment_materialization.items()
            if key != "environment_bootstrap_ms"
        }

    if len(environment_materializations) != 2:
        raise ConfigurationError(
            "frozen protocol requires exactly two private dependency materializations"
        )
    if any(len(rows) != 2 for rows in observations_by_case.values()):
        raise ConfigurationError(
            "each frozen transition requires exactly two counterbalanced observations"
        )

    # The one-time layer lifecycle and both private dependency lifecycles
    # (copy, verification, post-run verification, and cleanup) are required by
    # both arms and therefore charged symmetrically. Direct and ZeroRun seed
    # executions are arm-specific. All ZeroRun qualification and activation
    # costs are charged only to ZeroRun.
    # No automatic profile refresh is authorized; incurred refresh runtime is
    # therefore zero, while every refresh-required signal remains visible.
    primary_cost_ledger, direct_primary_overhead_ms, zerorun_primary_overhead_ms = (
        _validate_primary_cost_ledger(
            {
                "symmetric_environment_bootstrap_charged_to_each_arm": (
                    environment_bootstrap_total_ms
                ),
                "direct_seed_charged_to_direct": direct_seed_total_ms,
                "zerorun_qualification_charged_to_zerorun": (
                    qualification_total_ms
                ),
                "zerorun_activation_charged_to_zerorun": activation_total_ms,
                "zerorun_seed_charged_to_zerorun": zerorun_seed_total_ms,
                "automatic_refresh_charged_to_zerorun": (
                    automatic_refresh_total_ms
                ),
            }
        )
    )
    direct_amortized_ms = direct_primary_overhead_ms / case_count
    zerorun_amortized_ms = zerorun_primary_overhead_ms / case_count

    rows: list[dict[str, Any]] = []
    for index, sha in enumerate(cases, 1):
        observations = sorted(
            observations_by_case[index], key=lambda row: int(row["trajectory"])
        )
        if {row["order"] for row in observations} != set(_TRAJECTORY_ORDER_LABELS):
            raise ConfigurationError(
                f"transition {index} lacks one observation in each counterbalanced order"
            )
        direct_codes = [int(row["direct_exit_code"]) for row in observations]
        product_codes = [int(row["zerorun_exit_code"]) for row in observations]
        direct_code = direct_codes[0] if len(set(direct_codes)) == 1 else 95
        product_code = product_codes[0] if len(set(product_codes)) == 1 else 94
        all_comparisons_pass = all(
            row["comparison"]["comparison_pass"] is True
            and row["profile_refresh_fail_closed"] is True
            for row in observations
        )
        digests = {
            str(row["zerorun_collection_sha256"])
            for row in observations
            if re.fullmatch(
                r"[0-9a-f]{64}", str(row["zerorun_collection_sha256"])
            )
            is not None
        }
        common_collection = (
            next(iter(digests))
            if len(digests) == 1 and all_comparisons_pass
            else None
        )
        statuses = [str(row["zerorun_status"]) for row in observations]
        if any("CONFLICT" in status for status in statuses):
            status = "COUNTERBALANCED_CONFLICT"
        elif any(status == "PYTEST_FRESH_RECOVERY" for status in statuses):
            status = "PYTEST_FRESH_RECOVERY"
        elif len(set(statuses)) == 1:
            status = statuses[0]
        else:
            status = "COUNTERBALANCED_MIXED_PASS"

        direct_transition_ms = sum(
            float(row["direct_transition_wall_ms"]) for row in observations
        )
        zerorun_transition_ms = sum(
            float(row["zerorun_transition_wall_ms"]) for row in observations
        )
        direct_primary_ms = direct_transition_ms + direct_amortized_ms
        zerorun_primary_ms = zerorun_transition_ms + zerorun_amortized_ms
        reused = sum(int(row["reused_nodes"]) for row in observations)
        fresh = sum(int(row["fresh_nodes"]) for row in observations)
        unknown = sum(int(row["unknown_nodes"]) for row in observations)
        nodes = sum(int(row["total_nodes"]) for row in observations)
        published = sum(int(row["published_nodes"]) for row in observations)
        direct_pair = [float(row["direct_transition_wall_ms"]) for row in observations]
        zerorun_pair = [
            float(row["zerorun_transition_wall_ms"]) for row in observations
        ]
        rows.append(
            {
                "case": index,
                "commit": sha,
                "previous_commit": corpus[index - 1],
                "direct_exit_code": direct_code,
                "zerorun_exit_code": product_code,
                # Compatibility aliases are exactly the new primary end-to-end
                # paired totals; they do not hide setup or seed costs.
                "direct_ms": round(direct_primary_ms, 3),
                "zerorun_ms": round(zerorun_primary_ms, 3),
                "primary_end_to_end_plain_pytest_ms": round(
                    direct_primary_ms, 3
                ),
                "primary_end_to_end_zerorun_ms": round(zerorun_primary_ms, 3),
                "steady_state_plain_pytest_transition_ms": round(
                    direct_transition_ms, 3
                ),
                "steady_state_zerorun_transition_ms": round(
                    zerorun_transition_ms, 3
                ),
                "direct_phase_ms": {
                    "timed_transition_outer_wall_sum": round(
                        direct_transition_ms, 3
                    ),
                    "amortized_environment_and_direct_seed": round(
                        direct_amortized_ms, 3
                    ),
                    "primary_end_to_end_paired_total": round(
                        direct_primary_ms, 3
                    ),
                },
                "direct_collection_separately_measured": False,
                "direct_collection_included_in_uninstrumented_single_pass_wall": True,
                "direct_runner_independent_of_zerorun_internals": True,
                "zerorun_phase_ms": _sum_numeric_mappings(
                    [row["zerorun_phase_ms"] for row in observations]
                ),
                "zerorun_phase_ms_emitted": all(
                    row["zerorun_phase_ms_emitted"] for row in observations
                ),
                "zerorun_timing_ms": {
                    **_sum_numeric_mappings(
                        [row["zerorun_timing_ms"] for row in observations]
                    ),
                    "amortized_environment_qualification_activation_and_seed": round(
                        zerorun_amortized_ms, 3
                    ),
                    "primary_end_to_end_paired_total": round(
                        zerorun_primary_ms, 3
                    ),
                },
                "zerorun_status": status,
                "reused_nodes": reused,
                "fresh_nodes": fresh,
                "unknown_nodes": unknown,
                "total_nodes": nodes,
                "published_nodes": published,
                "reuse_authorized": reused > 0,
                "direct_collection_sha256": common_collection,
                "zerorun_collection_sha256": common_collection,
                "direct_stderr_tail": "\n".join(
                    str(row["direct_stderr_tail"]) for row in observations
                )[-2000:],
                "zerorun_stderr_tail": "\n".join(
                    str(row["zerorun_stderr_tail"]) for row in observations
                )[-2000:],
                "counterbalanced_repetitions": observations,
                "repetition_count_per_arm": 2,
                "order_pair_range_ms": {
                    "plain_pytest": round(max(direct_pair) - min(direct_pair), 3),
                    "zerorun": round(max(zerorun_pair) - min(zerorun_pair), 3),
                },
                "all_repetition_comparisons_pass": all_comparisons_pass,
                "reused_node_fresh_shadow_coverage": sum(
                    int(row["comparison"]["reused_node_fresh_shadow_coverage"])
                    for row in observations
                ),
                "reused_node_fresh_shadow_denominator": reused,
            }
        )

    direct_times = [float(row["direct_ms"]) for row in rows]
    zerorun_times = [float(row["zerorun_ms"]) for row in rows]
    direct_total = _finite_positive_ms(
        "primary_plain_pytest_total",
        math.fsum(direct_times),
    )
    zerorun_total = _finite_positive_ms(
        "primary_zerorun_total",
        math.fsum(zerorun_times),
    )
    end_to_end_speedup = direct_total / zerorun_total
    _finite_positive_ms("primary_end_to_end_speedup", end_to_end_speedup)
    direct_p95 = _finite_positive_ms(
        "primary_plain_pytest_p95",
        percentile(direct_times, 0.95),
    )
    zerorun_p95 = _finite_positive_ms(
        "primary_zerorun_p95",
        percentile(zerorun_times, 0.95),
    )
    p95_reduction = (
        (1.0 - zerorun_p95 / direct_p95) * 100.0 if direct_p95 else 0.0
    )
    total_reused_nodes = sum(int(row["reused_nodes"]) for row in rows)
    total_fresh_nodes = sum(int(row["fresh_nodes"]) for row in rows)
    total_unknown_nodes = sum(int(row["unknown_nodes"]) for row in rows)
    total_nodes = sum(int(row["total_nodes"]) for row in rows)
    total_published_nodes = sum(int(row["published_nodes"]) for row in rows)
    reuse_rate = total_reused_nodes / total_nodes * 100.0 if total_nodes else 0.0
    direct_failures = sum(int(row["direct_exit_code"]) != 0 for row in rows)
    zerorun_failures = sum(int(row["zerorun_exit_code"]) != 0 for row in rows)
    stale_successes = sum(
        int(row["direct_exit_code"]) != 0
        and int(row["zerorun_exit_code"]) == 0
        for row in rows
    )
    shadow_mismatches = sum(
        int(row["direct_exit_code"]) != int(row["zerorun_exit_code"])
        or row["direct_collection_sha256"] != row["zerorun_collection_sha256"]
        or row["direct_collection_sha256"] is None
        for row in rows
    )
    cache_conflicts = sum(
        "CONFLICT" in str(row["zerorun_status"]) for row in rows
    )
    recovery_runs = sum(
        row["zerorun_status"] == "PYTEST_FRESH_RECOVERY" for row in rows
    )
    adverse_pass = all(audit.get("pass") is True for audit in adverse_audits)
    adverse_audit_total_ms = math.fsum(
        _finite_nonnegative_ms(
            f"adverse_audit_{audit_index}_{name}_excluded",
            audit.get(name, 0.0) or 0.0,
        )
        for audit_index, audit in enumerate(adverse_audits, 1)
        for name in (
            "direct_wall_ms",
            "zerorun_wall_ms",
            "independent_shadow_wall_ms",
        )
    )

    safety_pass = (
        direct_failures == 0
        and zerorun_failures == 0
        and stale_successes == 0
        and shadow_mismatches == 0
        and cache_conflicts == 0
        and refresh_fail_closed_violations == 0
        and adverse_pass
        and all(
            row["reused_node_fresh_shadow_coverage"]
            == row["reused_node_fresh_shadow_denominator"]
            for row in rows
        )
    )
    performance_pass = (
        safety_pass
        and end_to_end_speedup >= min_compute
        and p95_reduction >= min_p95_reduction
    )

    steady_direct_total = _finite_positive_ms(
        "steady_state_plain_pytest_total",
        math.fsum(
            float(row["steady_state_plain_pytest_transition_ms"])
            for row in rows
        ),
    )
    steady_zerorun_total = _finite_positive_ms(
        "steady_state_zerorun_total",
        math.fsum(
            float(row["steady_state_zerorun_transition_ms"])
            for row in rows
        ),
    )
    steady_speedup = steady_direct_total / steady_zerorun_total
    _finite_positive_ms("steady_state_speedup", steady_speedup)
    first_candidate = candidates[0]
    first_activation = activations[0]
    candidate_count, reviewable_count, forced_fresh_count = _candidate_counts(
        first_candidate
    )
    serialized_primary_cost_ledger = {
        name: round(primary_cost_ledger[name], 3)
        for name in _PRIMARY_COST_LEDGER_KEYS
    }
    # Reconcile the shared charge from the exact components at the receipt's
    # three-decimal precision, avoiding an otherwise possible rounding-only
    # disagreement between the ledger, layer record, and two trajectories.
    serialized_primary_cost_ledger[
        "symmetric_environment_bootstrap_charged_to_each_arm"
    ] = round(
        round(dependency_layer_build_ms, 3)
        + math.fsum(
            round(float(row["environment_bootstrap_ms"]), 3)
            for row in environment_materializations
        ),
        3,
    )
    serialized_direct_overhead_ms = (
        serialized_primary_cost_ledger[
            "symmetric_environment_bootstrap_charged_to_each_arm"
        ]
        + serialized_primary_cost_ledger["direct_seed_charged_to_direct"]
    )
    serialized_zerorun_overhead_ms = (
        serialized_primary_cost_ledger[
            "symmetric_environment_bootstrap_charged_to_each_arm"
        ]
        + serialized_primary_cost_ledger[
            "zerorun_qualification_charged_to_zerorun"
        ]
        + serialized_primary_cost_ledger[
            "zerorun_activation_charged_to_zerorun"
        ]
        + serialized_primary_cost_ledger["zerorun_seed_charged_to_zerorun"]
        + serialized_primary_cost_ledger[
            "automatic_refresh_charged_to_zerorun"
        ]
    )

    return {
        "schema": "zerorun.product-generalization.v1",
        "workload": workload,
        "holdout_class": holdout_class,
        "upstream_repo": upstream_repo,
        "frozen_sha": frozen_sha,
        "engine_sha": engine_sha,
        "protocol": (
            f"{_METHODOLOGY_VERSION}: frozen first-parent corpus; two isolated "
            "chronological trajectories with one plain-then-ZeroRun and one "
            "ZeroRun-then-plain observation per transition; uninstrumented "
            "independent plain pytest baseline; full-fresh per-node shadow excluded "
            "from timing; the complete immutable dependency-layer lifecycle and "
            "both private copy/verification/cleanup lifecycles charged symmetrically, "
            "with all "
            "arm-specific setup/seed costs amortized over exactly 20 transitions; "
            "no corpus, target, or threshold changes after observation"
        ),
        "methodology_version": _METHODOLOGY_VERSION,
        "trajectory_schedule": schedule,
        "targets": list(targets),
        "extra_requirements": list(extra_requirements),
        "frozen_runtime_environment": True,
        "container_resource_envelope": {
            "docker_args": list(_PLAIN_DOCKER_RESOURCE_ARGS),
            "identical_for_plain_pytest_and_zerorun": True,
        },
        "runtime_requirements_sha256": runtime_requirements_sha256,
        "frozen_dependency_layer": {
            **dependency_layer["provenance"],
            "content_addressed": True,
            "private_materialization_count": len(environment_materializations),
            "private_materialization_method": "exclusive-byte-copy-no-hardlinks",
            "shared_writable_dependency_state": False,
            "independent_result_cache_count": 2,
            "cleanup_confirmed_before_receipt_return": True,
            "timing_ms": {
                "build_once": round(dependency_layer_build_ms, 3),
                "private_materialization_and_verification_total": round(
                    math.fsum(
                        round(float(row["environment_bootstrap_ms"]), 3)
                        for row in environment_materializations
                    ),
                    3,
                ),
                "private_post_run_verification_and_cleanup_total": round(
                    math.fsum(
                        float(row["total_ms"])
                        for row in private_dependency_cleanup_records
                    ),
                    3,
                ),
                "total_charged_symmetrically_to_each_arm": (
                    serialized_primary_cost_ledger[
                        "symmetric_environment_bootstrap_charged_to_each_arm"
                    ]
                ),
            },
        },
        "case_count": len(rows),
        "corpus_commits": corpus,
        "candidate_sha256": first_candidate.get("candidate_sha256"),
        "candidate_node_count": candidate_count,
        "candidate_reviewable_nodes": reviewable_count,
        "candidate_fresh_required_nodes": forced_fresh_count,
        "candidate_fresh_reason_summary": _candidate_fresh_reason_summary(
            first_candidate
        ),
        "activation_candidate_sha256": first_activation.get("candidate_sha256"),
        "review_record_sha256": first_activation.get("review_record_sha256"),
        "activation_evidence_kind": "mechanical-synthetic-formative-fixture",
        "external_hmac_authority_created": False,
        "runtime_image": observed_runtime_image,
        "runtime_python_implementation": GENERALIZATION_RUNTIME_IMPLEMENTATION,
        "runtime_python_version": GENERALIZATION_RUNTIME_PYTHON_VERSION,
        "runtime_python_attestation": (
            "verified during the single hash-locked dependency-layer build; the "
            "exact pinned image and private read-only layer bytes are re-attested "
            "for each isolated trajectory"
        ),
        "timing_semantics": {
            "direct": (
                "primary end-to-end paired total: two uninstrumented ordinary pytest "
                "Docker runs plus an equal charge for the complete immutable dependency "
                "layer lifecycle, both private materialization/post-run-verification/"
                "cleanup lifecycles, and both direct seed runs; no ZeroRun collection, "
                "selection, runtime, or cache function is used"
            ),
            "zerorun": (
                "primary end-to-end paired total: two current-product transition "
                "runs plus the same environment-bootstrap charge and all measured "
                "qualification, activation, and ZeroRun seed costs amortized across "
                "the fixed 20-transition horizon"
            ),
            "steady_state_secondary": (
                "transition execution walls only; excludes bootstrap, qualification, "
                "activation, seed, shadow-oracle, adverse-audit, and Git checkout time"
            ),
            "shadow_oracle": (
                "independently instrumented full-fresh pytest per observation; exact "
                "node-sequence and per-node non-failure evidence; excluded from every "
                "performance metric"
            ),
            "gate_inputs_unchanged": True,
            "compatibility_aliases_equal_primary_end_to_end": True,
        },
        "primary_metric": "end_to_end_plain_pytest_to_zerorun_speedup",
        "primary_horizon_transitions": case_count,
        "counterbalanced_trajectory_count": 2,
        "repetitions_per_transition_per_arm": 2,
        "cost_ledger_ms": serialized_primary_cost_ledger,
        "primary_cost_accounting": {
            "schema": "zerorun.primary-end-to-end-cost-accounting.v1",
            "required_components": list(_PRIMARY_COST_LEDGER_KEYS),
            "all_required_components_present": True,
            "all_components_finite_and_nonnegative": True,
            "direct_overhead_before_horizon_amortization_ms": (
                serialized_direct_overhead_ms
            ),
            "zerorun_overhead_before_horizon_amortization_ms": (
                serialized_zerorun_overhead_ms
            ),
            "horizon_transitions": case_count,
            "reconciled_before_gate_evaluation": True,
        },
        "excluded_validation_wall_ms": {
            "independent_fresh_shadow_excluded_from_primary_end_to_end": round(
                _finite_nonnegative_ms(
                    "independent_fresh_shadow_excluded",
                    shadow_oracle_total_ms,
                ),
                3,
            ),
            "adverse_audits_excluded_from_primary_end_to_end": round(
                adverse_audit_total_ms, 3
            ),
        },
        "profile_refresh": {
            "required_observations": refresh_required_observations,
            "automatic_refreshes_performed": 0,
            "fail_closed_violations": refresh_fail_closed_violations,
            "runtime_refresh_cost_ms": 0.0,
            "external_human_review_time_measured": False,
            "reason": (
                "renewed profile review is an external authorization step and is not "
                "mechanically reapproved; a refresh-required observation must reuse "
                "zero nodes and publish zero nodes"
            ),
        },
        "trajectory_records": trajectory_records,
        "adverse_audits": adverse_audits,
        "adverse_audits_pass": adverse_pass,
        "adverse_injection_limitation": (
            "The excluded transient audit injects a changed-source collection failure. "
            "The frozen performance corpus is never mutated. A fault cannot be "
            "injected into an already reused node without changing the frozen test "
            "semantics; normal reuse is instead shadowed by a complete fresh run and "
            "any failing shadow invalidates the observation."
        ),
        "direct_total_ms": round(direct_total, 3),
        "zerorun_total_ms": round(zerorun_total, 3),
        "end_to_end_plain_pytest_to_zerorun_speedup": round(
            end_to_end_speedup, 6
        ),
        "same_runner_compute_efficiency": round(end_to_end_speedup, 6),
        "steady_state_plain_pytest_total_ms": round(steady_direct_total, 3),
        "steady_state_zerorun_total_ms": round(steady_zerorun_total, 3),
        "steady_state_plain_pytest_to_zerorun_speedup": round(
            steady_speedup, 6
        ),
        "direct_p95_ms": round(direct_p95, 3),
        "zerorun_p95_ms": round(zerorun_p95, 3),
        "end_to_end_p95_reduction_percent": round(p95_reduction, 3),
        "p95_reduction_percent": round(p95_reduction, 3),
        "p95_estimation": {
            "estimator": "nearest-rank-on-counterbalanced-paired-transition-totals",
            "sample_size": len(rows),
            "rank": math.ceil(0.95 * len(rows)),
            "independent_repository_count": 1,
            "repetitions_per_transition_per_arm": 2,
            "confidence_interval_reported": False,
            "limitation": (
                "Twenty dependent transitions from one repository provide a coarse "
                "19th-order statistic, not a precise population-tail estimate; the "
                "hard threshold is a release reference, not a confidence claim."
            ),
        },
        "measurement_noise": {
            "raw_counterbalanced_repetitions_retained": True,
            "repetitions_per_transition_per_arm": 2,
            "order_effect_estimable_but_underpowered": True,
            "distributional_confidence_claim": False,
        },
        "reused_nodes": total_reused_nodes,
        "fresh_nodes": total_fresh_nodes,
        "unknown_nodes": total_unknown_nodes,
        "total_nodes": total_nodes,
        "published_nodes": total_published_nodes,
        "reuse_rate_percent": round(reuse_rate, 3),
        "recovery_runs": recovery_runs,
        "direct_failures": direct_failures,
        "zerorun_failures": zerorun_failures,
        "stale_successes": stale_successes,
        "shadow_mismatches": shadow_mismatches,
        "cache_conflicts": cache_conflicts,
        "safety_pass": safety_pass,
        "reference_gate": {
            "min_compute_efficiency": min_compute,
            "min_p95_reduction_percent": min_p95_reduction,
        },
        "reference_gate_metric": "end_to_end_plain_pytest_to_zerorun_speedup",
        "reference_gate_requires_both_thresholds_per_workload": True,
        "performance_gate_pass": performance_pass,
        "review_note": (
            "The candidate was mechanically activated as a synthetic formative "
            "fixture to exercise the internal product path. No external HMAC reuse "
            "authority was created; this is not operator, customer, or design-partner "
            "review evidence. Refresh-required profiles are never mechanically "
            "reapproved during the trajectories."
        ),
        "rows": rows,
    }


def benchmark(
    root: Path,
    *,
    workload: str,
    upstream_repo: str,
    frozen_sha: str,
    targets: Sequence[str],
    extra_requirements: Sequence[str],
    runtime_image: str,
    case_count: int,
    min_compute: float,
    min_p95_reduction: float,
    engine_sha: str | None,
    holdout_class: str,
    expected_runtime_requirements_sha256: str | None = None,
    expected_engine_source_sha256: str | None = None,
    expected_trial_tool_sha256: str | None = None,
) -> dict[str, Any]:
    source_identity_pre = _capture_source_identity()
    actual_engine_sha = _validate_caller_source_identity(
        source_identity_pre,
        engine_sha=engine_sha,
        expected_engine_source_sha256=expected_engine_source_sha256,
        expected_trial_tool_sha256=expected_trial_tool_sha256,
    )
    try:
        result = _benchmark_once(
            root,
            workload=workload,
            upstream_repo=upstream_repo,
            frozen_sha=frozen_sha,
            targets=targets,
            extra_requirements=extra_requirements,
            runtime_image=runtime_image,
            case_count=case_count,
            min_compute=min_compute,
            min_p95_reduction=min_p95_reduction,
            engine_sha=actual_engine_sha,
            holdout_class=holdout_class,
            expected_runtime_requirements_sha256=(
                expected_runtime_requirements_sha256
            ),
        )
    except Exception as exc:
        source_identity_post = _capture_post_source_identity(source_identity_pre)
        try:
            _require_stable_source_identity(
                source_identity_pre,
                source_identity_post,
            )
        except _SourceIdentityError as drift:
            raise drift from exc
        setattr(exc, "source_identity_pre", source_identity_pre)
        setattr(exc, "source_identity_post", source_identity_post)
        raise

    source_identity_post = _capture_post_source_identity(source_identity_pre)
    _require_stable_source_identity(source_identity_pre, source_identity_post)
    result.update(
        {
            "engine_sha": actual_engine_sha,
            "engine_version": source_identity_pre["engine_version"],
            "source_identity": {
                "pre": source_identity_pre,
                "post": source_identity_post,
                "stable": True,
                "caller_expected_engine_sha": engine_sha,
                "caller_expected_engine_python_source_sha256": (
                    expected_engine_source_sha256
                ),
                "caller_expected_trial_tool_sha256": expected_trial_tool_sha256,
            },
        }
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run frozen cross-repository ZeroRun product generalization benchmark."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--upstream-repo", required=True)
    parser.add_argument("--frozen-sha", required=True)
    parser.add_argument("--target", action="append", required=True, dest="targets")
    parser.add_argument("--extra-requirement", action="append", default=[])
    parser.add_argument("--runtime-image", required=True)
    parser.add_argument("--runtime-requirements-sha256")
    parser.add_argument("--case-count", type=int, default=20)
    parser.add_argument("--min-compute", type=float, default=5.0)
    parser.add_argument("--min-p95-reduction", type=float, default=50.0)
    parser.add_argument("--engine-sha")
    parser.add_argument("--engine-source-sha256")
    parser.add_argument("--trial-tool-sha256")
    parser.add_argument(
        "--holdout-class",
        choices=("old-regression", "new-unseen"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = benchmark(
            args.root,
            workload=args.workload,
            upstream_repo=args.upstream_repo,
            frozen_sha=args.frozen_sha,
            targets=tuple(args.targets),
            extra_requirements=tuple(args.extra_requirement),
            runtime_image=args.runtime_image,
            case_count=args.case_count,
            min_compute=args.min_compute,
            min_p95_reduction=args.min_p95_reduction,
            engine_sha=args.engine_sha,
            holdout_class=args.holdout_class,
            expected_runtime_requirements_sha256=(
                args.runtime_requirements_sha256
            ),
            expected_engine_source_sha256=args.engine_source_sha256,
            expected_trial_tool_sha256=args.trial_tool_sha256,
        )
    except Exception as exc:
        source_identity_pre = getattr(exc, "source_identity_pre", None)
        source_identity_post = getattr(exc, "source_identity_post", None)
        observed_engine_sha = (
            source_identity_pre.get("git", {}).get("commit")
            if isinstance(source_identity_pre, dict)
            else None
        )
        result = {
            "schema": "zerorun.product-generalization.v1",
            "workload": args.workload,
            "holdout_class": args.holdout_class,
            "upstream_repo": args.upstream_repo,
            "frozen_sha": args.frozen_sha,
            "engine_sha": observed_engine_sha,
            "engine_version": (
                source_identity_pre.get("engine_version")
                if isinstance(source_identity_pre, dict)
                else None
            ),
            "source_identity": {
                "pre": source_identity_pre,
                "post": source_identity_post,
                "stable": (
                    source_identity_pre is not None
                    and source_identity_pre == source_identity_post
                ),
                "caller_expected_engine_sha": args.engine_sha,
                "caller_expected_engine_python_source_sha256": (
                    args.engine_source_sha256
                ),
                "caller_expected_trial_tool_sha256": args.trial_tool_sha256,
            },
            "targets": list(args.targets),
            "runtime_image": args.runtime_image,
            "runtime_requirements_sha256": (
                _runtime_requirements_sha256()
                if GENERALIZATION_RUNTIME_REQUIREMENTS.is_file()
                else None
            ),
            "activation_evidence_kind": "mechanical-synthetic-formative-fixture",
            "external_hmac_authority_created": False,
            "case_count": args.case_count,
            "reference_gate": {
                "min_compute_efficiency": args.min_compute,
                "min_p95_reduction_percent": args.min_p95_reduction,
            },
            "safety_pass": False,
            "performance_gate_pass": False,
            "benchmark_error": f"{type(exc).__name__}: {exc}",
        }
        code = 1
    else:
        code = 0 if result["performance_gate_pass"] else 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        key: result.get(key)
        for key in (
            "workload",
            "holdout_class",
            "same_runner_compute_efficiency",
            "p95_reduction_percent",
            "reuse_rate_percent",
            "stale_successes",
            "shadow_mismatches",
            "cache_conflicts",
            "safety_pass",
            "performance_gate_pass",
            "benchmark_error",
        )
        if key in result
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
