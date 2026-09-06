from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import (
    atomic_replace_bytes,
    create_private_temp_directory,
    is_link_like as _is_link_like,
    private_temporary_directory,
)
from .oci import (
    DOCKER_EXECUTION_TIMEOUT_SECONDS,
    DOCKER_INSPECT_TIMEOUT_SECONDS,
    DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
    DOCKER_PULL_TIMEOUT_SECONDS,
    DOCKER_RESOURCE_ARGS,
    FIXED_CONTAINER_ENV,
    _docker_client_environment,
    _docker_bind_mount,
    _decode_docker_json,
    _docker_path,
    _new_container_name,
    _run_bounded_process,
    _run_created_container,
    hermetic_environment,
    inspect_runtime,
    validate_host,
)
from .pytest_runtime import collect_pytest
from .store import Store

ENV_DIR = ".zerorun-env"
ENV_SCHEMA = "zerorun-managed-pytest-env-v1"
TASK_NAME = "pytest-managed"

_REQUIREMENT_NAMES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "test-requirements.txt",
)
_DESCRIPTOR_NAMES = (
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    ".python-version",
)
_PYTHON_VERSION_RE = re.compile(r"^(\d+)\.(\d+)")
MAX_DESCRIPTOR_BYTES = 1024 * 1024


def git_marker_kind(root: Path) -> str:
    """Validate a direct checkout or linked-worktree marker without following links."""

    marker = root / ".git"
    if _is_link_like(marker):
        raise ConfigurationError("Git metadata marker must not be a symbolic link or junction")
    try:
        metadata = marker.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ConfigurationError("managed pytest setup requires a Git worktree") from exc
    except OSError as exc:
        raise ConfigurationError(f"could not inspect Git metadata marker: {exc}") from exc
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    if stat.S_ISREG(metadata.st_mode):
        return "file"
    raise ConfigurationError("Git metadata marker must be a regular file or directory")


def _safe_descriptor_bytes(root: Path, path: Path) -> bytes:
    """Read a small repository descriptor through a no-link, stable file handle."""

    root = root.resolve(strict=True)
    lexical = Path(os.path.abspath(path))
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(f"descriptor escaped the repository: {path}") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if _is_link_like(cursor):
            raise ConfigurationError(
                f"refusing linked managed-setup descriptor: {relative.as_posix()}"
            )
    try:
        before = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError(f"could not inspect descriptor {relative.as_posix()}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ConfigurationError(
            f"managed-setup descriptor is not a regular file: {relative.as_posix()}"
        )
    if before.st_size > MAX_DESCRIPTOR_BYTES:
        raise ConfigurationError(
            f"managed-setup descriptor exceeds {MAX_DESCRIPTOR_BYTES} bytes: "
            f"{relative.as_posix()}"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lexical, flags)
    except OSError as exc:
        raise ConfigurationError(f"could not safely open descriptor {relative.as_posix()}: {exc}") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ConfigurationError(
                f"managed-setup descriptor changed during validation: {relative.as_posix()}"
            )
        chunks: list[bytes] = []
        remaining = MAX_DESCRIPTOR_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(data) > MAX_DESCRIPTOR_BYTES:
        raise ConfigurationError(
            f"managed-setup descriptor exceeds {MAX_DESCRIPTOR_BYTES} bytes: "
            f"{relative.as_posix()}"
        )
    try:
        after = lexical.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError(
            f"managed-setup descriptor changed during validation: {relative.as_posix()}"
        ) from exc
    if (
        len(data) != opened.st_size
        or _is_link_like(lexical)
        or not stat.S_ISREG(after.st_mode)
        or (
            after_open.st_dev,
            after_open.st_ino,
            after_open.st_mode,
            after_open.st_size,
            after_open.st_mtime_ns,
            getattr(after_open, "st_ctime_ns", None),
        )
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
            getattr(opened, "st_ctime_ns", None),
        )
        or (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_size,
            opened.st_mtime_ns,
        )
    ):
        raise ConfigurationError(
            f"managed-setup descriptor changed during validation: {relative.as_posix()}"
        )
    return data


def _existing_descriptor_paths(root: Path, names: tuple[str, ...]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for name in names:
        path = root / name
        if _is_link_like(path):
            raise ConfigurationError(f"refusing linked managed-setup descriptor: {name}")
        if path.exists():
            _safe_descriptor_bytes(root, path)
            paths.append(path)
    return tuple(paths)


def _sha256(path: Path) -> str:
    return hashlib.sha256(_safe_descriptor_bytes(path.parent, path)).hexdigest()


def _project_python_version(root: Path) -> tuple[int, int]:
    version_file = root / ".python-version"
    if _is_link_like(version_file):
        raise ConfigurationError("refusing linked managed-setup descriptor: .python-version")
    if version_file.exists():
        try:
            text = _safe_descriptor_bytes(root, version_file).decode("utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(f"could not read .python-version: {exc}") from exc
        first = text.split()[0] if text.split() else ""
        match = _PYTHON_VERSION_RE.match(first)
        if match is None:
            raise ConfigurationError(
                ".python-version does not begin with a supported major.minor"
            )
        version = (int(match.group(1)), int(match.group(2)))
    else:
        version = (sys.version_info.major, sys.version_info.minor)
    if version < (3, 10):
        raise ConfigurationError(
            f"managed pytest setup requires Python >=3.10; selected "
            f"{version[0]}.{version[1]}"
        )
    return version


def _requirement_files(root: Path) -> tuple[Path, ...]:
    files = _existing_descriptor_paths(root, _REQUIREMENT_NAMES)
    for path in files:
        try:
            lines = _safe_descriptor_bytes(root, path).decode("utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ConfigurationError(f"could not read {path.name}: {exc}") from exc
        for number, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            lowered = line.lower()
            if (
                line.startswith("-")
                or "://" in line
                or " @ " in line
                or lowered.startswith(("git+", "file:"))
                or line.startswith((".", "/", "~"))
            ):
                raise ConfigurationError(
                    f"{path.name}:{number}: managed setup v1 refuses requirement "
                    "directives, URLs, VCS references, and local paths"
                )
    return files


def descriptor_hashes(
    root: Path,
    requirements: tuple[Path, ...] | None = None,
) -> dict[str, str]:
    requirements = requirements if requirements is not None else _requirement_files(root)
    paths = list(requirements)
    paths.extend(_existing_descriptor_paths(root, _DESCRIPTOR_NAMES))
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(
            _safe_descriptor_bytes(root, path)
        ).hexdigest()
        for path in sorted(set(paths))
    }


def _docker(repository_root: Path) -> str:
    validate_host()
    return _docker_path(repository_root)


def _resolve_python_image(version: tuple[int, int], *, repository_root: Path) -> str:
    docker = _docker(repository_root)
    docker_environment = _docker_client_environment(docker)
    tag = f"python:{version[0]}.{version[1]}-slim"
    try:
        pull, pull_timed_out = _run_bounded_process(
            [docker, "pull", "--platform", "linux/amd64", tag],
            cwd=None,
            environment=docker_environment,
            timeout_seconds=DOCKER_PULL_TIMEOUT_SECONDS,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"could not start acquisition of supported Python image {tag}: {exc}"
        ) from exc
    if pull_timed_out:
        raise ConfigurationError(f"timed out acquiring supported Python image {tag}")
    if pull.returncode != 0:
        detail = pull.stderr.decode("utf-8", errors="replace")[-3000:]
        raise ConfigurationError(
            f"could not acquire supported Python image {tag}: {detail}"
        )
    try:
        inspect, inspect_timed_out = _run_bounded_process(
            [docker, "image", "inspect", tag],
            cwd=None,
            environment=docker_environment,
            timeout_seconds=DOCKER_INSPECT_TIMEOUT_SECONDS,
            output_limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"could not start inspection of supported Python image {tag}: {exc}"
        ) from exc
    if inspect_timed_out:
        raise ConfigurationError(f"timed out inspecting supported Python image {tag}")
    if inspect.returncode != 0:
        raise ConfigurationError(f"could not inspect resolved Python image {tag}")
    try:
        rows = _decode_docker_json(
            inspect.stdout,
            label="Docker Python image metadata",
        )
    except ConfigurationError as exc:
        raise ConfigurationError(
            f"Docker returned invalid image metadata: {exc}"
        ) from exc
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
    ):
        raise ConfigurationError("Docker returned unexpected Python image metadata")
    row = rows[0]
    if (
        str(row.get("Os", "")).lower() != "linux"
        or str(row.get("Architecture", "")).lower() != "amd64"
    ):
        raise ConfigurationError("resolved Python image is not linux/amd64")
    repo_digests = sorted(
        item for item in row.get("RepoDigests", []) if isinstance(item, str)
    )
    matches = [
        item
        for item in repo_digests
        if item.split("@", 1)[0].split("/")[-1] == "python"
        and re.fullmatch(r".+@sha256:[0-9a-f]{64}", item)
    ]
    if not matches:
        raise ConfigurationError(
            "resolved Python tag did not expose an immutable repo digest"
        )
    return matches[0]


def _install_dependencies(
    root: Path,
    *,
    image: str,
    requirements: tuple[Path, ...],
    build_dir: Path,
) -> None:
    docker = _docker(root)
    docker_environment = _docker_client_environment(docker)
    site_packages = build_dir / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-managed-requirements-"
    ) as support:
        copied: list[str] = []
        for source in requirements:
            target = support / source.name
            target.write_bytes(_safe_descriptor_bytes(root, source))
            target.chmod(0o444)
            copied.append(source.name)

        container_name = _new_container_name("setup")
        argv = [
            docker,
            "create",
            "--name",
            container_name,
            "--pull",
            "never",
            "--platform",
            "linux/amd64",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *DOCKER_RESOURCE_ARGS,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=512m",
            "--mount",
            _docker_bind_mount(support, "/requirements", readonly=True),
            "--mount",
            _docker_bind_mount(build_dir, "/zerorun-env"),
        ]
        for name, value in sorted(FIXED_CONTAINER_ENV.items()):
            argv.extend(["--env", f"{name}={value}"])
        argv.extend(
            [
                image,
                "python",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-compile",
                "--target",
                "/zerorun-env/site-packages",
                "pytest",
            ]
        )
        for name in copied:
            argv.extend(["-r", f"/requirements/{name}"])

        try:
            process = _run_created_container(
                argv,
                docker=docker,
                container_name=container_name,
                cwd=root,
                environment=docker_environment,
                execution_timeout_seconds=DOCKER_EXECUTION_TIMEOUT_SECONDS,
                operation_label="managed dependency installation",
            )
        except OSError as exc:
            raise ConfigurationError(
                f"managed dependency installation could not start: {exc}"
            ) from exc
        if process.returncode != 0:
            detail = (process.stdout + process.stderr).decode(
                "utf-8", errors="replace"
            )[-5000:]
            raise ConfigurationError(
                "managed dependency installation failed in the pinned Python image: "
                + detail
            )


def _write_environment_files(
    build_dir: Path,
    *,
    image: str,
    version: tuple[int, int],
    descriptors: dict[str, str],
) -> None:
    bin_dir = build_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    checker = build_dir / "check.py"
    checker.write_text(
        "from __future__ import annotations\n"
        "import hashlib, pathlib, sys\n"
        f"EXPECTED = {descriptors!r}\n"
        f"WATCHED = {tuple(dict.fromkeys((*_REQUIREMENT_NAMES, *_DESCRIPTOR_NAMES)))!r}\n"
        "ROOT = pathlib.Path('/workspace')\n"
        "def sha(path):\n"
        "    h = hashlib.sha256()\n"
        "    with path.open('rb') as f:\n"
        "        for chunk in iter(lambda: f.read(1024 * 1024), b''):\n"
        "            h.update(chunk)\n"
        "    return h.hexdigest()\n"
        "for relative in WATCHED:\n"
        "    path = ROOT / relative\n"
        "    present = path.is_file() and not path.is_symlink()\n"
        "    if present != (relative in EXPECTED):\n"
        "        print('ZeroRun managed pytest environment is stale: ' + relative, file=sys.stderr)\n"
        "        raise SystemExit(86)\n"
        "for relative, expected in sorted(EXPECTED.items()):\n"
        "    path = ROOT / relative\n"
        "    if not path.is_file() or sha(path) != expected:\n"
        "        print('ZeroRun managed pytest environment is stale: ' + relative, "
        "file=sys.stderr)\n"
        "        raise SystemExit(86)\n",
        encoding="utf-8",
    )
    checker.chmod(0o444)

    wrapper = bin_dir / "python"
    wrapper.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "/usr/local/bin/python /workspace/.zerorun-env/check.py\n"
        "export PYTHONPATH=\"${PYTHONPATH:+$PYTHONPATH:}"
        "/workspace/.zerorun-env/site-packages:/workspace/src:/workspace\"\n"
        "exec /usr/local/bin/python \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    marker = {
        "schema": ENV_SCHEMA,
        "image": image,
        "python": f"{version[0]}.{version[1]}",
        "descriptor_sha256": descriptors,
    }
    (build_dir / "runtime.json").write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _managed_env_owned(path: Path) -> bool:
    marker = path / "runtime.json"
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema") == ENV_SCHEMA
    )


def _replace_managed_env(root: Path, build_dir: Path) -> Path:
    final = root / ENV_DIR
    legacy_backup = root / f"{ENV_DIR}.previous"
    for path in (final, legacy_backup):
        if path.exists() or _is_link_like(path):
            raise ConfigurationError(
                f"refusing managed pytest setup because {path.name} already exists; "
                "remove reviewed stale ZeroRun state manually before retrying"
            )
    os.replace(build_dir, final)
    return final


def _exclude_managed_env_from_git(root: Path) -> None:
    exclude = root / ".git" / "info" / "exclude"
    if any(
        _is_link_like(path)
        for path in (root / ".git", root / ".git" / "info", exclude)
    ):
        return
    if not exclude.parent.is_dir():
        return
    try:
        if exclude.is_file():
            existing = _safe_descriptor_bytes(root, exclude).decode("utf-8")
            mode = stat.S_IMODE(exclude.stat(follow_symlinks=False).st_mode)
        else:
            existing = ""
            mode = 0o644
    except (ConfigurationError, OSError, UnicodeDecodeError):
        return
    rules = ("/.zerorun/", f"/{ENV_DIR}/")
    present = {line.strip() for line in existing.splitlines()}
    missing = [rule for rule in rules if rule not in present]
    if not missing:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    try:
        atomic_replace_bytes(
            exclude,
            (
                existing
                + prefix
                + "# ZeroRun local runtime state\n"
                + "".join(rule + "\n" for rule in missing)
            ).encode("utf-8"),
            mode=mode,
        )
    except OSError:
        pass


def managed_task(image: str, descriptors: dict[str, str]) -> TaskSpec:
    inputs = tuple(dict.fromkeys((ENV_DIR, *descriptors.keys())))
    return TaskSpec(
        name=TASK_NAME,
        command=(
            "sh",
            f"{ENV_DIR}/bin/python",
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
        ),
        inputs=inputs,
        outputs=(),
        env=(),
        cacheable=False,
        unsafe_effects=(),
        cache_streams=False,
        image=image,
        platform="linux/amd64",
        result_only=True,
        closure_reviewed=True,
    )


def _manifest_payload(task: TaskSpec) -> dict[str, Any]:
    return {
        "version": 2,
        "tasks": {
            task.name: {
                "command": list(task.command),
                "inputs": list(task.inputs),
                "outputs": [],
                "env": [],
                "cacheable": False,
                "unsafe_effects": [],
                "cache_streams": False,
                "result_only": True,
                "closure_reviewed": True,
                "image": task.image,
                "platform": "linux/amd64",
            }
        },
    }


def default_targets(root: Path) -> tuple[str, ...]:
    for candidate in ("tests", "test", "testing"):
        if (root / candidate).exists():
            return (candidate,)
    return (".",)


def setup_managed_pytest_task(
    root: Path,
    *,
    targets: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ConfigurationError(f"repository root does not exist: {root}")
    git_marker_kind(root)

    manifest_path = root / ".zerorun.json"
    temp_manifest = root / ".zerorun.json.tmp"
    if manifest_path.exists() or _is_link_like(manifest_path):
        raise ConfigurationError(
            "refusing to overwrite an existing .zerorun.json"
        )
    if temp_manifest.exists() or _is_link_like(temp_manifest):
        raise ConfigurationError(
            "refusing managed pytest setup because .zerorun.json.tmp already exists"
        )

    version = _project_python_version(root)
    requirements = _requirement_files(root)
    descriptors = descriptor_hashes(root, requirements)
    image = _resolve_python_image(version, repository_root=root)

    build_dir = create_private_temp_directory(
        root, prefix=".zerorun-env-build-"
    )
    # The pinned Docker runtime may use a remapped root/user namespace, so it
    # must be able to traverse this dependency-only environment after it is
    # bind-mounted read-only.
    build_dir.chmod(0o755)
    installed_env: Path | None = None
    try:
        _install_dependencies(
            root,
            image=image,
            requirements=requirements,
            build_dir=build_dir,
        )
        _write_environment_files(
            build_dir,
            image=image,
            version=version,
            descriptors=descriptors,
        )
        installed_env = _replace_managed_env(root, build_dir)
        build_dir = installed_env
        _exclude_managed_env_from_git(root)

        Store(root).ensure()
        task = managed_task(image, descriptors)
        manifest = Manifest(
            root=root,
            path=manifest_path,
            tasks={task.name: task},
            version=2,
        )
        runtime = inspect_runtime(task, repository_root=root)
        environment, _ = hermetic_environment(task)
        selected_targets = targets or default_targets(root)
        profile = type(
            "ManagedSetupProfile",
            (),
            {"base_args": (), "targets": selected_targets},
        )()
        collection, collection_ms = collect_pytest(
            manifest,
            task,
            profile,
            runtime_identity=runtime,
            environment=environment,
        )
        nodeids = collection.get("nodeids")
        if not isinstance(nodeids, list) or not nodeids:
            raise ConfigurationError(
                "managed pytest setup collected no tests; staying observation-only"
            )

        manifest_text = (
            json.dumps(
                _manifest_payload(task),
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        try:
            with manifest_path.open("x", encoding="utf-8") as handle:
                handle.write(manifest_text)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise ConfigurationError(
                "refusing to overwrite a .zerorun.json created during setup"
            ) from exc
        return {
            "status": "PYTEST_TASK_READY",
            "task": task.name,
            "manifest": str(manifest_path),
            "managed_env": str(root / ENV_DIR),
            "image": image,
            "python": f"{version[0]}.{version[1]}",
            "requirements": [path.name for path in requirements],
            "targets": list(selected_targets),
            "collected_nodes": len(nodeids),
            "collection_ms": round(collection_ms, 3),
            "task_level_reuse": False,
            "pytest_node_reuse": False,
            "next_action": (
                "generate a non-authorizing pytest candidate with prepare_pytest"
            ),
        }
    except Exception:
        if (
            installed_env is not None
            and installed_env.is_dir()
            and _managed_env_owned(installed_env)
            and not manifest_path.exists()
        ):
            shutil.rmtree(installed_env, ignore_errors=True)
        elif build_dir.exists() and build_dir.name.startswith(
            ".zerorun-env-build-"
        ):
            shutil.rmtree(build_dir, ignore_errors=True)
        raise
