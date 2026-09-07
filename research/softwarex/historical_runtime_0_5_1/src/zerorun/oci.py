from __future__ import annotations

import hashlib
import json
import os
import platform as host_platform
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO, Callable

from .bounded_json import JsonLimits, loads_bounded_json
from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import is_link_like

FIXED_CONTAINER_ENV = {
    "LANG": "C",
    "LC_ALL": "C",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    # Do not allow pre-existing adjacent ``__pycache__`` files in a checkout
    # to influence qualification, fresh execution, or reuse validation.
    # ``/tmp`` is a fresh container-local tmpfs for every execution, so Python
    # can only consult an empty bytecode namespace.  Disabling writes keeps it
    # empty for the duration as well.
    "PYTHONPYCACHEPREFIX": "/tmp/zerorun-pycache",
    "TZ": "UTC",
}
DOCKER_RESOURCE_ARGS = (
    "--cpus",
    "2",
    "--memory",
    "2g",
    "--memory-swap",
    "2g",
    "--pids-limit",
    "512",
)
DOCKER_INSPECT_TIMEOUT_SECONDS = 60
DOCKER_PULL_TIMEOUT_SECONDS = 300
DOCKER_EXECUTION_TIMEOUT_SECONDS = 900
DOCKER_CLEANUP_TIMEOUT_SECONDS = 60
DOCKER_OUTPUT_LIMIT_BYTES = 1024 * 1024
DOCKER_METADATA_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
DOCKER_ENVIRONMENT_LIMIT_BYTES = 1024 * 1024

_DOCKER_METADATA_JSON_LIMITS = JsonLimits(
    max_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
    max_depth=32,
    max_values=100_000,
    max_object_members=25_000,
    max_structural_tokens=250_000,
    max_number_chars=256,
    max_string_chars=1024 * 1024,
    max_total_string_chars=6 * 1024 * 1024,
)

_TRUNCATION_MARKER = b"ZeroRun: output truncated; retained bounded tail\n"
_DOCKER_CLIENT_ENV_NAMES = (
    # Docker endpoint and credential/config selection. These are host-side
    # Docker client settings, never task/container settings.
    "DOCKER_API_VERSION",
    "DOCKER_CERT_PATH",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_HOST",
    "DOCKER_TLS",
    "DOCKER_TLS_VERIFY",
    # Docker and credential helpers need a stable user/config/runtime context.
    "HOME",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
    # Windows process startup requires these when the tests exercise the
    # launcher there. Linux hermetic execution still uses a fixed safe PATH.
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
)


def reject_sourceless_workspace_bytecode(root: Path) -> None:
    """Reject direct-path ``.pyc`` modules visible to container imports.

    ``PYTHONPYCACHEPREFIX`` moves ordinary adjacent bytecode lookup into a
    fresh per-execution tmpfs, but Python can still import a legacy/sourceless
    ``module.pyc`` directly from ``sys.path``. Such a module has no source
    bytes for a reviewed closure to bind, so it is refused before execution or
    reuse. Adjacent ``__pycache__`` trees are unreachable under the prefix.
    """

    root = root.expanduser().resolve(strict=True)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    name = entry.name
                    if directory == root and name in {".git", ".zerorun"}:
                        continue
                    if name == "__pycache__":
                        continue
                    if name.casefold().endswith(".pyc"):
                        try:
                            relative = Path(entry.path).relative_to(root).as_posix()
                        except ValueError:
                            relative = name
                        raise ConfigurationError(
                            "sourceless/direct Python bytecode is not allowed in "
                            f"the execution workspace: {relative}"
                        )
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                    except OSError as exc:
                        raise ConfigurationError(
                            "could not inspect workspace bytecode boundary: "
                            f"{entry.path}: {exc}"
                        ) from exc
        except ConfigurationError:
            raise
        except OSError as exc:
            raise ConfigurationError(
                f"could not inspect workspace bytecode boundary: {directory}: {exc}"
            ) from exc


_SAFE_DOCKER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Retained only for API compatibility with older diagnostics. Runtime identity
# caching is deliberately disabled: Docker config/context/certificate contents
# and daemon selection can change without their environment paths changing.
_RUNTIME_IDENTITY_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _decode_docker_json(payload: bytes, *, label: str) -> object:
    """Decode daemon-controlled metadata under explicit structural bounds."""

    return loads_bounded_json(
        payload,
        label=label,
        limits=_DOCKER_METADATA_JSON_LIMITS,
    )


def _docker_path(repository_root: Path | None = None) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise ConfigurationError("hermetic v2 requires Docker Engine on a Linux/amd64 host")
    lexical = Path(os.path.abspath(Path(docker).expanduser()))
    resolved = lexical.resolve()
    if repository_root is not None:
        root = repository_root.expanduser().resolve()
        for candidate in (lexical, resolved):
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            raise ConfigurationError(
                "refusing a repository-controlled Docker executable: "
                f"{lexical}"
            )
    return str(resolved)


def _new_container_name(prefix: str) -> str:
    return f"zerorun-{prefix}-{secrets.token_hex(12)}"


def _docker_bind_mount(
    source: os.PathLike[str] | str,
    destination: str,
    *,
    readonly: bool = False,
) -> str:
    """Build one Docker ``--mount`` value without CSV option injection.

    Docker parses the value as comma-delimited fields even though it is passed
    as one argv element. Repository and temporary paths therefore cannot be
    interpolated verbatim when they contain CSV metacharacters.
    """

    rendered = os.fspath(source)
    # ``/dev/null`` is an intentional Linux-daemon bind source even when the
    # Docker client itself runs on Windows, where ``Path('/dev/null')`` is not
    # classified as host-absolute.
    if not (Path(rendered).is_absolute() or rendered.startswith("/")):
        raise ConfigurationError("Docker bind source must be an absolute path")
    if any(character in rendered for character in ('\x00', '\r', '\n', ',', '"')):
        raise ConfigurationError(
            "Docker bind source contains a character unsafe for --mount transport"
        )
    if (
        not isinstance(destination, str)
        or not destination.startswith("/")
        or any(character in destination for character in ('\x00', '\r', '\n', ',', '"'))
    ):
        raise ConfigurationError("Docker bind destination is malformed")
    suffix = ",readonly" if readonly else ""
    return f"type=bind,src={rendered},dst={destination}{suffix}"


def _docker_client_environment(
    docker: str,
    *,
    excluded_names: tuple[str, ...] = (),
) -> dict[str, str]:
    """Return the only host environment ever exposed to Docker subprocesses.

    Task-declared values are deliberately excluded, even when their names
    overlap normal Docker client settings. Their values travel solely through
    explicit ``--env NAME=value`` argv entries for the container.
    """

    normalize = str.casefold if os.name == "nt" else (lambda value: value)
    excluded = {normalize(name) for name in excluded_names}
    environment = {
        name: os.environ[name]
        for name in _DOCKER_CLIENT_ENV_NAMES
        if normalize(name) not in excluded and name in os.environ
    }
    if normalize("PATH") not in excluded:
        if os.name == "nt":
            # ``os.defpath`` starts with the current directory on Windows. A
            # repository must not be able to supply a Docker credential helper
            # or another child executable merely by naming it in the checkout.
            candidates = [str(Path(docker).resolve().parent)]
            system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
            if system_root:
                candidates.extend(
                    [str(Path(system_root) / "System32"), str(Path(system_root))]
                )
            environment["PATH"] = os.pathsep.join(dict.fromkeys(candidates))
        else:
            environment["PATH"] = _SAFE_DOCKER_PATH
    return environment


def _validate_task_docker_environment(task: TaskSpec) -> None:
    """Keep container inputs from changing the host Docker trust endpoint."""

    reserved = {name.casefold() for name in (*_DOCKER_CLIENT_ENV_NAMES, "PATH")}
    conflicts = sorted(name for name in task.env if name.casefold() in reserved)
    if conflicts:
        raise ConfigurationError(
            f"task {task.name!r}: environment names reserved by the Docker client "
            f"cannot be passed into a hermetic container: {', '.join(conflicts)}"
        )


def _container_environment_bytes(
    task: TaskSpec,
    environment: dict[str, str],
) -> bytes:
    container_environment = dict(FIXED_CONTAINER_ENV)
    for name in task.env:
        if name in environment:
            container_environment[name] = environment[name]
    lines: list[str] = []
    for name, value in sorted(container_environment.items()):
        if (
            "\x00" in name
            or "=" in name
            or "\r" in name
            or "\n" in name
            or name.startswith("#")
            or "\x00" in value
            or "\r" in value
            or "\n" in value
        ):
            raise ConfigurationError(
                f"task {task.name!r}: environment contains a value Docker's "
                "bounded env-file transport cannot represent"
            )
        lines.append(f"{name}={value}\n")
    encoded = "".join(lines).encode("utf-8", errors="surrogatepass")
    if len(encoded) > DOCKER_ENVIRONMENT_LIMIT_BYTES:
        raise ConfigurationError(
            f"task {task.name!r}: container environment exceeds the "
            f"{DOCKER_ENVIRONMENT_LIMIT_BYTES}-byte boundary"
        )
    return encoded


@contextmanager
def _temporary_container_environment_file(
    payload: bytes,
    *,
    repository_root: Path,
):
    """Expose container values to Docker without placing secrets in argv."""

    descriptor, raw_path = tempfile.mkstemp(prefix="zerorun-docker-env-")
    path = Path(os.path.abspath(raw_path))
    opened = True
    try:
        try:
            path.resolve(strict=True).relative_to(repository_root.resolve(strict=True))
        except ValueError:
            pass
        else:
            raise ConfigurationError(
                "trusted Docker environment temp storage resolves inside the repository"
            )
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise ConfigurationError(
                    "could not write the bounded Docker environment transport"
                )
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        opened = False
        yield path
    finally:
        if opened:
            os.close(descriptor)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                "could not remove the trusted Docker environment transport"
            ) from exc
        if path.exists() or is_link_like(path):
            raise ConfigurationError(
                "Docker environment transport cleanup was not confirmed"
            )


class _BoundedTail:
    def __init__(self, limit: int) -> None:
        if limit <= len(_TRUNCATION_MARKER):
            raise ValueError("bounded output limit is too small")
        self.limit = limit
        self.data = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.data.extend(chunk)
        if len(self.data) > self.limit:
            del self.data[: len(self.data) - self.limit]
            self.truncated = True

    def value(self) -> bytes:
        if not self.truncated:
            return bytes(self.data)
        retained = self.limit - len(_TRUNCATION_MARKER)
        return _TRUNCATION_MARKER + bytes(self.data[-retained:])


def _drain_stream(stream: BinaryIO, output: _BoundedTail) -> None:
    try:
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                return
            output.append(chunk)
    except (OSError, ValueError):
        # Process teardown can close a pipe while a daemon reader is blocked.
        return


def _write_stdin(stream: BinaryIO, payload: bytes) -> None:
    """Write a caller-bounded payload without blocking output drainage."""

    try:
        view = memoryview(payload)
        offset = 0
        while offset < len(view):
            written = stream.write(view[offset : offset + (64 * 1024)])
            if written is None:
                written = min(64 * 1024, len(view) - offset)
            if written <= 0:
                break
            offset += written
        stream.flush()
    except (BrokenPipeError, OSError, ValueError):
        # A process may reject the request and exit before consuming stdin.
        pass
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _run_bounded_process(
    command: list[str],
    *,
    cwd: Path | None,
    environment: dict[str, str],
    timeout_seconds: float,
    output_limit_bytes: int = DOCKER_OUTPUT_LIMIT_BYTES,
    on_timeout: Callable[[bool], None] | None = None,
    input_bytes: bytes | None = None,
) -> tuple[subprocess.CompletedProcess[bytes], bool]:
    """Run while concurrently draining both pipes into bounded tail buffers."""

    process_group_args: dict[str, Any]
    if os.name == "nt":
        process_group_args = {
            "creationflags": (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
            )
        }
    else:
        process_group_args = {"start_new_session": True}

    stdout = _BoundedTail(output_limit_bytes)
    stderr = _BoundedTail(output_limit_bytes)
    process: subprocess.Popen[bytes] | None = None
    windows_job: int | None = None
    readers: list[tuple[BinaryIO, threading.Thread]] = []
    started_readers: list[threading.Thread] = []
    writer: threading.Thread | None = None
    writer_started = False
    timed_out = False

    def stop_tree_and_notify() -> bool:
        # This function is also used for BaseException/KeyboardInterrupt. A
        # caller that owns an external resource (notably a Docker container)
        # therefore gets one cleanup opportunity on every abnormal exit.
        nonlocal windows_job
        if process is None:
            return False
        if windows_job is not None:
            _windows_close_handle(windows_job)
            windows_job = None
        _terminate_process_tree(process, include_descendants_if_exited=True)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        client_reaped = process.poll() is not None
        if on_timeout is not None:
            try:
                on_timeout(client_reaped)
            except Exception:
                # Cleanup is best effort and must never hide the primary
                # timeout or caller interruption.
                pass
        return client_reaped

    try:
        # Ownership starts in this exception region. In particular, no Windows
        # child is allowed to execute until it belongs to the kill-on-close job.
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **process_group_args,
        )
        if os.name == "nt":
            windows_job = _windows_create_kill_on_close_job(process)
            if windows_job is None:
                raise OSError(
                    "could not assign the suspended child to a Windows Job Object"
                )
            if not _windows_resume_main_thread(process):
                raise OSError("could not resume the contained Windows child")

        assert process.stdout is not None
        assert process.stderr is not None
        readers = [
            (
                process.stdout,
                threading.Thread(
                    target=_drain_stream,
                    args=(process.stdout, stdout),
                    name="zerorun-stdout-drain",
                    daemon=True,
                ),
            ),
            (
                process.stderr,
                threading.Thread(
                    target=_drain_stream,
                    args=(process.stderr, stderr),
                    name="zerorun-stderr-drain",
                    daemon=True,
                ),
            ),
        ]
        for _, reader in readers:
            reader.start()
            started_readers.append(reader)
        if input_bytes is not None:
            assert process.stdin is not None
            writer = threading.Thread(
                target=_write_stdin,
                args=(process.stdin, input_bytes),
                name="zerorun-stdin-writer",
                daemon=True,
            )
            writer.start()
            writer_started = True

        process.wait(timeout=timeout_seconds)
        windows_descendants = (
            _windows_job_active_processes(windows_job)
            if windows_job is not None
            else 0
        )
        if _process_group_is_alive(process) or (
            windows_descendants is not None and windows_descendants > 0
        ):
            # A leader that exits while descendants retain pipes or continue in
            # the same isolated group has not completed the bounded operation.
            timed_out = True
            stop_tree_and_notify()
        elif windows_job is not None:
            _windows_close_handle(windows_job)
            windows_job = None
    except subprocess.TimeoutExpired:
        timed_out = True
        # Stop the client request before cleanup. For Docker callers, removing a
        # name while ``docker run`` is still issuing the create request can race:
        # ``rm`` reports not-found, then the daemon creates an orphan container.
        stop_tree_and_notify()
    except BaseException:
        stop_tree_and_notify()
        raise
    finally:
        if process is not None:
            # Once the process exits, readers can drain the finite kernel pipes
            # to EOF. Join before closing so the retained tail is not spuriously
            # cut short. A final close still bounds pathological teardown.
            teardown_deadline = time.monotonic() + 2.0
            for reader in started_readers:
                reader.join(
                    timeout=max(0.0, teardown_deadline - time.monotonic())
                )
            if writer is not None and writer_started:
                writer.join(timeout=max(0.0, teardown_deadline - time.monotonic()))
            if (
                writer is not None
                and writer_started
                and writer.is_alive()
                and process.stdin is not None
            ):
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
                writer.join(timeout=max(0.0, teardown_deadline - time.monotonic()))
            elif writer is not None and not writer_started and process.stdin is not None:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
            elif writer is None and process.stdin is not None:
                # Job assignment/resume can fail before the writer exists.
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
            for pipe in (process.stdout, process.stderr):
                if pipe is None:
                    continue
                reader = next(
                    (
                        candidate
                        for candidate_pipe, candidate in readers
                        if candidate_pipe is pipe
                    ),
                    None,
                )
                if (
                    reader is not None
                    and reader in started_readers
                    and reader.is_alive()
                ):
                    continue
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
            if any(reader.is_alive() for reader in started_readers) or (
                writer is not None and writer_started and writer.is_alive()
            ):
                timed_out = True
                stop_tree_and_notify()
        if windows_job is not None:
            _windows_close_handle(windows_job)
            windows_job = None

    assert process is not None
    returncode = (
        124
        if timed_out
        else int(process.returncode if process.returncode is not None else 1)
    )
    return (
        subprocess.CompletedProcess(
            command,
            returncode,
            stdout.value(),
            stderr.value(),
        ),
        timed_out,
    )


def _process_group_is_alive(process: subprocess.Popen[bytes]) -> bool:
    """Return whether a POSIX child remains in the isolated process group."""

    if os.name == "nt":
        return False
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_system_executable(name: str) -> str | None:
    """Resolve a Windows system executable without consulting caller PATH."""

    if os.name != "nt":
        return None
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if length <= 0 or length >= len(buffer):
            return None
        candidate = Path(buffer.value) / name
        if candidate.is_file():
            return str(candidate)
    except (AttributeError, OSError, ValueError):
        return None
    return None


def _windows_create_kill_on_close_job(
    process: subprocess.Popen[bytes],
) -> int | None:
    """Assign a Windows child to a non-breakaway kill-on-close Job Object."""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        information = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        information.BasicLimitInformation.LimitFlags = 0x00002000
        configured = kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(information),
            ctypes.sizeof(information),
        )
        assigned = configured and kernel32.AssignProcessToJobObject(
            handle,
            wintypes.HANDLE(int(process._handle)),
        )
        if not assigned:
            kernel32.CloseHandle(handle)
            return None
        return int(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_resume_main_thread(process: subprocess.Popen[bytes]) -> bool:
    """Resume the sole primary thread of a CREATE_SUSPENDED child."""

    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        class THREADENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD),
                ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG),
                ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Thread32First.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(THREADENTRY32),
        ]
        kernel32.Thread32First.restype = wintypes.BOOL
        kernel32.Thread32Next.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(THREADENTRY32),
        ]
        kernel32.Thread32Next.restype = wintypes.BOOL
        kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenThread.restype = wintypes.HANDLE
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if not snapshot or int(snapshot) == invalid_handle:
            return False
        try:
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            thread_ids: list[int] = []
            if kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                while True:
                    if int(entry.th32OwnerProcessID) == process.pid:
                        thread_ids.append(int(entry.th32ThreadID))
                    entry.dwSize = ctypes.sizeof(entry)
                    if not kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            kernel32.CloseHandle(snapshot)
        # CREATE_SUSPENDED returns before application code can create another
        # thread. Anything except one primary thread is therefore uncertain.
        if len(thread_ids) != 1:
            return False
        thread = kernel32.OpenThread(0x0002, False, thread_ids[0])
        if not thread:
            return False
        try:
            previous_suspend_count = int(kernel32.ResumeThread(thread))
        finally:
            kernel32.CloseHandle(thread)
        return previous_suspend_count == 1
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _windows_job_active_processes(handle: int | None) -> int | None:
    if os.name != "nt" or handle is None:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_longlong),
                ("TotalKernelTime", ctypes.c_longlong),
                ("ThisPeriodTotalUserTime", ctypes.c_longlong),
                ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        information = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        query = ctypes.windll.kernel32.QueryInformationJobObject
        query.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        query.restype = wintypes.BOOL
        ok = query(
            handle,
            1,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        )
        return int(information.ActiveProcesses) if ok else None
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _windows_close_handle(handle: int) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        close = ctypes.windll.kernel32.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        close(handle)
    except (AttributeError, OSError, TypeError, ValueError):
        pass


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    *,
    include_descendants_if_exited: bool = False,
) -> None:
    """Best-effort termination of the exact process and its descendants."""

    if process.poll() is not None and not include_descendants_if_exited:
        return
    if os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        if process.poll() is not None:
            return
    else:
        taskkill = _windows_system_executable("taskkill.exe")
        if taskkill is not None:
            try:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
                if process.poll() is not None:
                    return
            except (OSError, subprocess.SubprocessError):
                pass
    try:
        process.kill()
    except OSError:
        pass


def _git_mask_args(repository_root: Path) -> list[str]:
    """Return a safe Docker mask for directory and linked-worktree markers."""

    root = repository_root.resolve(strict=True)
    marker = root / ".git"
    if is_link_like(marker):
        raise ConfigurationError("refusing a linked .git marker")
    try:
        before = marker.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError(f"repository .git marker is unavailable: {exc}") from exc
    if stat.S_ISDIR(before.st_mode):
        return [
            "--tmpfs",
            "/workspace/.git:rw,nosuid,nodev,noexec,size=1m",
        ]
    if not stat.S_ISREG(before.st_mode):
        raise ConfigurationError("repository .git marker must be a directory or regular worktree file")
    if before.st_size <= 0 or before.st_size > 4096:
        raise ConfigurationError("linked-worktree .git marker has an invalid size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(marker, flags)
        try:
            opened = os.fstat(descriptor)
            encoded = os.read(descriptor, 4097)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        raw = encoded.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"linked-worktree .git marker is unreadable: {exc}") from exc
    if len(encoded) > 4096 or not stat.S_ISREG(opened.st_mode):
        raise ConfigurationError("linked-worktree .git marker has an invalid size or type")
    if (before.st_dev, before.st_ino, before.st_mode, before.st_size) != (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_size,
    ) or (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_size,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
    ):
        raise ConfigurationError("linked-worktree .git marker changed during validation")
    lines = raw.splitlines()
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        raise ConfigurationError("linked-worktree .git marker is malformed")
    gitdir_text = lines[0][len("gitdir: ") :]
    if not gitdir_text or "\x00" in gitdir_text:
        raise ConfigurationError("linked-worktree .git marker is malformed")
    gitdir = Path(gitdir_text)
    if not gitdir.is_absolute():
        gitdir = marker.parent / gitdir
    try:
        resolved_gitdir = gitdir.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(
            f"linked-worktree git directory is unavailable: {exc}"
        ) from exc
    if (
        is_link_like(gitdir)
        or is_link_like(resolved_gitdir)
        or not resolved_gitdir.is_dir()
    ):
        raise ConfigurationError("linked-worktree git directory is not a safe directory")
    return [
        "--mount",
        _docker_bind_mount("/dev/null", "/workspace/.git", readonly=True),
    ]


def _force_remove_container(
    docker: str,
    container_reference: str,
    *,
    environment: dict[str, str],
) -> bool:
    """Remove one exact container name or immutable ID after its client stops.

    Docker creation is asynchronous at the daemon boundary.  A single early
    not-found response is therefore not enough evidence for an uncertain
    name: poll the same exact reference until it is absent on three consecutive
    checks. Any daemon, permission, output, or cleanup timeout is unconfirmed.
    """

    deadline = time.monotonic() + min(10.0, DOCKER_CLEANUP_TIMEOUT_SECONDS)
    consecutive_absent = 0
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            removed, timed_out = _run_bounded_process(
                [docker, "rm", "-f", container_reference],
                cwd=None,
                environment=environment,
                timeout_seconds=max(0.1, min(5.0, remaining)),
                output_limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
            )
        except OSError:
            return False
        if timed_out:
            return False
        if removed.returncode == 0:
            consecutive_absent = 0
        else:
            lowered = removed.stderr.lower()
            explicitly_absent = (
                b"no such container" in lowered or b"no such object" in lowered
            )
            if not explicitly_absent:
                return False
            consecutive_absent += 1
            if consecutive_absent >= 3:
                return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))
    return False


def validate_host() -> None:
    system = host_platform.system().lower()
    machine = host_platform.machine().lower()
    if system != "linux" or machine not in {"x86_64", "amd64"}:
        raise ConfigurationError(
            f"hermetic v2 reuse is restricted to Linux/amd64; host is {host_platform.system()}/{host_platform.machine()}"
        )


def _inspect(
    docker: str,
    image: str,
    *,
    environment: dict[str, str],
) -> dict[str, Any] | None:
    try:
        process, timed_out = _run_bounded_process(
            [docker, "image", "inspect", image],
            cwd=None,
            environment=environment,
            timeout_seconds=DOCKER_INSPECT_TIMEOUT_SECONDS,
            output_limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
        )
    except OSError as exc:
        raise ConfigurationError(f"Docker image inspection could not start: {exc}") from exc
    if timed_out:
        raise ConfigurationError("Docker image inspection timed out")
    if process.returncode != 0:
        return None
    try:
        decoded = _decode_docker_json(
            process.stdout,
            label="Docker image metadata",
        )
    except ConfigurationError as exc:
        raise ConfigurationError(f"Docker returned invalid image metadata: {exc}") from exc
    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
        raise ConfigurationError("Docker returned unexpected image metadata")
    return decoded[0]


def inspect_runtime(
    task: TaskSpec,
    *,
    allow_pull: bool = True,
    use_cache: bool = True,
    repository_root: Path | None = None,
) -> dict[str, Any]:
    # ``use_cache`` remains accepted for source compatibility. Correctness
    # requires an inspection on every call because endpoint-selection files can
    # change without any environment/path string changing.
    _ = use_cache
    validate_host()
    if task.image is None or task.platform != "linux/amd64":
        raise ConfigurationError(f"task {task.name!r}: missing pinned Linux/amd64 runtime")
    _validate_task_docker_environment(task)

    # Resolve and validate the Docker launcher for every call, including an
    # immutable-identity cache hit. A later repository must never inherit a
    # cached attestation and then invoke a repository-controlled PATH shim.
    docker = _docker_path(repository_root)

    docker_environment = _docker_client_environment(docker)
    raw = _inspect(docker, task.image, environment=docker_environment)
    if raw is None and allow_pull:
        try:
            pulled, timed_out = _run_bounded_process(
                [docker, "pull", "--platform", "linux/amd64", task.image],
                cwd=None,
                environment=docker_environment,
                timeout_seconds=DOCKER_PULL_TIMEOUT_SECONDS,
            )
        except OSError as exc:
            raise ConfigurationError(
                f"could not start acquisition of pinned OCI image {task.image}: {exc}"
            ) from exc
        if timed_out:
            raise ConfigurationError(
                f"timed out acquiring pinned OCI image {task.image}"
            )
        if pulled.returncode != 0:
            detail = pulled.stderr.decode("utf-8", errors="replace").strip()
            raise ConfigurationError(f"could not acquire pinned OCI image {task.image}: {detail}")
        raw = _inspect(docker, task.image, environment=docker_environment)
    if raw is None:
        raise ConfigurationError(f"pinned OCI image is not present locally: {task.image}")

    os_name = str(raw.get("Os", "")).lower()
    architecture = str(raw.get("Architecture", "")).lower()
    if os_name != "linux" or architecture != "amd64":
        raise ConfigurationError(f"pinned OCI image resolved to unsupported platform {os_name}/{architecture}")

    requested_digest = task.image.rsplit("@", 1)[1]
    repo_digests = sorted(str(item) for item in raw.get("RepoDigests", []) if isinstance(item, str))
    if not any(item.endswith("@" + requested_digest) for item in repo_digests):
        raise ConfigurationError("Docker image metadata does not attest the requested immutable digest")

    rootfs = raw.get("RootFS")
    if not isinstance(rootfs, dict) or not isinstance(rootfs.get("Layers"), list):
        raise ConfigurationError("Docker image metadata is missing RootFS layer identity")
    config = raw.get("Config")
    if not isinstance(config, dict):
        raise ConfigurationError("Docker image metadata is missing Config identity")
    image_id = raw.get("Id")
    if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
        raise ConfigurationError("Docker image metadata is missing immutable image ID")

    identity = {
        "runtime": "docker",
        "requested_image": task.image,
        "requested_digest": requested_digest,
        "platform": "linux/amd64",
        "image_id": image_id,
        "repo_digests": repo_digests,
        "rootfs": {
            "type": rootfs.get("Type"),
            "layers": list(rootfs["Layers"]),
        },
        "config_sha256": _canonical_sha256(config),
    }
    return identity


def clear_runtime_identity_cache() -> None:
    """Clear process-local OCI attestations; intended for tests/diagnostics."""
    _RUNTIME_IDENTITY_CACHE.clear()


def hermetic_environment(task: TaskSpec) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    runtime = dict(FIXED_CONTAINER_ENV)
    fingerprint: dict[str, dict[str, Any]] = {}
    for name, value in sorted(FIXED_CONTAINER_ENV.items()):
        fingerprint[name] = {"present": True, "sha256": hashlib.sha256(value.encode()).hexdigest()}
    for name in task.env:
        if name in os.environ:
            value = os.environ[name]
            runtime[name] = value
            fingerprint[name] = {
                "present": True,
                "sha256": hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest(),
            }
        else:
            fingerprint[name] = {"present": False}
    return runtime, fingerprint


def _inspect_container_exit_code(
    docker: str,
    container_name: str,
    *,
    environment: dict[str, str],
) -> int:
    try:
        process, timed_out = _run_bounded_process(
            [
                docker,
                "container",
                "inspect",
                "--format",
                "{{json .State}}",
                container_name,
            ],
            cwd=None,
            environment=environment,
            timeout_seconds=DOCKER_INSPECT_TIMEOUT_SECONDS,
            output_limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
        )
    except OSError as exc:
        raise ConfigurationError(
            "Docker container completion attestation could not start"
        ) from exc
    if timed_out:
        raise ConfigurationError("Docker container completion attestation timed out")
    if process.returncode != 0:
        raise ConfigurationError(
            "Docker could not attest the exact container's completion state"
        )
    try:
        state = _decode_docker_json(
            process.stdout,
            label="Docker container state",
        )
    except ConfigurationError as exc:
        raise ConfigurationError("Docker returned invalid container state") from exc
    exit_code = state.get("ExitCode") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict)
        or state.get("Status") != "exited"
        or state.get("Running") is not False
        or state.get("Error") not in (None, "")
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or not 0 <= exit_code <= 255
    ):
        status = state.get("Status") if isinstance(state, dict) else None
        running = state.get("Running") if isinstance(state, dict) else None
        daemon_error = state.get("Error") if isinstance(state, dict) else None
        error_text = str(daemon_error or "")[:500]
        raise ConfigurationError(
            "Docker did not attest one completed exact container execution: "
            f"status={status!r}, running={running!r}, exit_code={exit_code!r}, "
            f"daemon_error={error_text!r}"
        )
    return exit_code


def _remove_created_container(
    docker: str,
    container_id: str,
    *,
    environment: dict[str, str],
) -> bool:
    """Synchronously remove one validated immutable container ID."""

    try:
        process, timed_out = _run_bounded_process(
            [docker, "rm", "-f", container_id],
            cwd=None,
            environment=environment,
            timeout_seconds=DOCKER_CLEANUP_TIMEOUT_SECONDS,
            output_limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
        )
    except OSError:
        return False
    if timed_out:
        return False
    if process.returncode == 0:
        return True
    lowered = process.stderr.lower()
    return b"no such container" in lowered or b"no such object" in lowered


def _run_created_container(
    create_command: list[str],
    *,
    docker: str,
    container_name: str,
    cwd: Path,
    environment: dict[str, str],
    execution_timeout_seconds: float,
    operation_label: str,
    container_environment: bytes | None = None,
    environment_repository_root: Path | None = None,
    environment_file_index: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Create, attach, attest, and remove one exact Docker container.

    Docker client exit codes are never interpreted as task results without a
    matching completed `.State` attestation. Timeouts and transport failures
    are infrastructure errors and therefore cannot invalidate cached success.
    """

    create_command = list(create_command)
    try:
        name_index = create_command.index("--name")
    except ValueError as exc:
        raise ConfigurationError(
            "internal Docker create command is missing --name"
        ) from exc
    if (
        len(create_command) < 2
        or create_command[0] != docker
        or create_command[1] != "create"
        or name_index + 1 >= len(create_command)
        or create_command[name_index + 1] != container_name
    ):
        raise ConfigurationError(
            "internal Docker create command does not match its cleanup identity"
        )
    if container_environment is None:
        if (
            environment_repository_root is not None
            or environment_file_index is not None
        ):
            raise ConfigurationError(
                "internal Docker environment transport is only valid with a payload"
            )
    elif (
        environment_repository_root is None
        or environment_file_index is None
        or environment_file_index <= 0
        or environment_file_index >= len(create_command)
        or create_command[environment_file_index - 1] != "--env-file"
    ):
        raise ConfigurationError(
            "internal Docker environment transport slot is missing or malformed"
        )

    cleanup_required = False
    abnormal_client_reaped: bool | None = None
    cleanup_confirmed = False
    container_id: str | None = None

    def cleanup_reference() -> str:
        return container_id or container_name

    def cleanup_description() -> str:
        if container_id is None:
            return container_name
        return f"{container_name} (id {container_id})"

    def record_cleanup_proof(removed: bool) -> bool:
        nonlocal cleanup_confirmed
        cleanup_confirmed = cleanup_confirmed or (
            removed and abnormal_client_reaped is not False
        )
        return cleanup_confirmed

    def add_unconfirmed_cleanup_note(
        error: BaseException,
        cleanup_error: BaseException | None = None,
    ) -> None:
        detail = (
            "ZeroRun could not confirm cleanup for exact container "
            f"{cleanup_description()}; inspect the Docker daemon before reuse"
        )
        if cleanup_error is not None:
            detail += (
                "; cleanup attempt raised "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        add_note = getattr(error, "add_note", None)
        if callable(add_note):
            add_note(detail)
        else:
            # Python 3.10 has no PEP 678 notes. Preserve the original exception
            # type (especially KeyboardInterrupt/SystemExit) while ensuring the
            # cleanup failure remains visible in its normal traceback text.
            error.args = (*error.args, detail)

    def cleanup_after_abnormal_client(client_reaped: bool) -> None:
        nonlocal abnormal_client_reaped
        abnormal_client_reaped = client_reaped
        removed = _force_remove_container(
            docker,
            cleanup_reference(),
            environment=environment,
        )
        record_cleanup_proof(removed)

    def record_abnormal_create_client(client_reaped: bool) -> None:
        # The create-only environment file is still in scope while the bounded
        # runner delivers this callback. Record process ownership here, then
        # remove the secret transport before doing any daemon cleanup.
        nonlocal abnormal_client_reaped
        abnormal_client_reaped = client_reaped

    def run_create_client() -> tuple[subprocess.CompletedProcess[bytes], bool]:
        if container_environment is None:
            return _run_bounded_process(
                create_command,
                cwd=cwd,
                environment=environment,
                timeout_seconds=DOCKER_INSPECT_TIMEOUT_SECONDS,
                on_timeout=record_abnormal_create_client,
            )
        assert environment_repository_root is not None
        assert environment_file_index is not None
        with _temporary_container_environment_file(
            container_environment,
            repository_root=environment_repository_root,
        ) as environment_file:
            launch = list(create_command)
            launch[environment_file_index] = str(environment_file)
            return _run_bounded_process(
                launch,
                cwd=cwd,
                environment=environment,
                timeout_seconds=DOCKER_INSPECT_TIMEOUT_SECONDS,
                on_timeout=record_abnormal_create_client,
            )

    def force_cleanup_after_exception(error: BaseException) -> None:
        if not cleanup_required or cleanup_confirmed:
            return
        cleanup_error: BaseException | None = None
        try:
            record_cleanup_proof(
                _force_remove_container(
                    docker,
                    cleanup_reference(),
                    environment=environment,
                )
            )
        except BaseException as exc:
            cleanup_error = exc
        if not cleanup_confirmed:
            add_unconfirmed_cleanup_note(error, cleanup_error)

    process: subprocess.CompletedProcess[bytes] | None = None
    timed_out_phase: str | None = None
    infrastructure_error: ConfigurationError | None = None
    attested_exit_code: int | None = None
    try:
        cleanup_required = True
        try:
            created, create_timed_out = run_create_client()
        except OSError as exc:
            raise ConfigurationError(
                f"{operation_label} container creation could not start"
            ) from exc
        if create_timed_out:
            process = created
            timed_out_phase = "container creation"
        elif created.returncode != 0:
            infrastructure_error = ConfigurationError(
                f"{operation_label} container creation failed with Docker "
                f"client exit {created.returncode}"
            )
        else:
            raw_container_id = created.stdout.strip()
            if len(raw_container_id) != 64 or any(
                byte not in b"0123456789abcdef" for byte in raw_container_id
            ):
                infrastructure_error = ConfigurationError(
                    f"{operation_label} Docker create did not return one valid "
                    "immutable container ID"
                )
            else:
                container_id = raw_container_id.decode("ascii")
                try:
                    process, start_timed_out = _run_bounded_process(
                        [docker, "start", "--attach", container_id],
                        cwd=cwd,
                        environment=environment,
                        timeout_seconds=execution_timeout_seconds,
                        on_timeout=cleanup_after_abnormal_client,
                    )
                except OSError as exc:
                    infrastructure_error = ConfigurationError(
                        f"{operation_label} container start/attach could not start"
                    )
                    infrastructure_error.__cause__ = exc
                else:
                    if start_timed_out:
                        timed_out_phase = "container execution"
                    else:
                        try:
                            attested_exit_code = _inspect_container_exit_code(
                                docker,
                                container_id,
                                environment=environment,
                            )
                        except ConfigurationError as exc:
                            infrastructure_error = exc
                        else:
                            if process.returncode != attested_exit_code:
                                infrastructure_error = ConfigurationError(
                                    f"{operation_label} Docker client exit did not "
                                    "match the attested container exit code"
                                )
    except BaseException as exc:
        force_cleanup_after_exception(exc)
        raise

    if cleanup_required and not cleanup_confirmed:
        try:
            if container_id is None:
                # A timed-out, failed, or malformed create has no trusted ID.
                # Only repeated absence of the exact random name is proof that
                # a reaped create client cannot leave a late daemon object.
                removed = _force_remove_container(
                    docker,
                    container_name,
                    environment=environment,
                )
            else:
                removed = _remove_created_container(
                    docker,
                    container_id,
                    environment=environment,
                )
                if not removed:
                    removed = _force_remove_container(
                        docker,
                        container_id,
                        environment=environment,
                    )
            record_cleanup_proof(removed)
        except BaseException as exc:
            force_cleanup_after_exception(exc)
            raise

    cleanup_suffix = ""
    if not cleanup_confirmed:
        cleanup_suffix = (
            "; Docker cleanup could not be confirmed for exact container "
            f"{cleanup_description()}; inspect the daemon before reuse"
        )
    if timed_out_phase is not None:
        timeout_boundary = (
            DOCKER_INSPECT_TIMEOUT_SECONDS
            if timed_out_phase == "container creation"
            else execution_timeout_seconds
        )
        raise ConfigurationError(
            f"{operation_label} {timed_out_phase} exceeded the "
            f"{timeout_boundary:g} second execution boundary"
            + cleanup_suffix
        )
    if infrastructure_error is not None:
        if cleanup_suffix:
            raise ConfigurationError(
                str(infrastructure_error) + cleanup_suffix
            ) from infrastructure_error
        raise infrastructure_error
    if not cleanup_confirmed:
        raise ConfigurationError(operation_label + cleanup_suffix)
    assert process is not None and attested_exit_code is not None
    return subprocess.CompletedProcess(
        create_command,
        attested_exit_code,
        process.stdout,
        process.stderr,
    )


def _docker_execute(
    manifest: Manifest,
    task: TaskSpec,
    runtime_identity: dict[str, Any],
    environment: dict[str, str],
    *,
    repository_root: Path | None = None,
) -> tuple[int, float, bytes, bytes]:
    _validate_task_docker_environment(task)
    reject_sourceless_workspace_bytecode(repository_root or manifest.root)
    docker = _docker_path(repository_root or manifest.root)
    container_name = _new_container_name("task")
    image = str(runtime_identity["requested_image"])
    create_base = [
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
        *DOCKER_RESOURCE_ARGS,
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=512m",
        "--tmpfs",
        "/workspace/.zerorun:rw,nosuid,nodev,noexec,size=1m",
        *_git_mask_args(manifest.root),
        "--mount",
        _docker_bind_mount(manifest.root, "/workspace", readonly=True),
        "--workdir",
        "/workspace",
    ]
    docker_environment = _docker_client_environment(docker)
    environment_payload = _container_environment_bytes(task, environment)
    create_command = [
        *create_base,
        "--env-file",
        "",
        image,
        *task.command,
    ]
    environment_file_index = len(create_base) + 1
    started = time.perf_counter()
    try:
        process = _run_created_container(
            create_command,
            docker=docker,
            container_name=container_name,
            cwd=manifest.root,
            environment=docker_environment,
            execution_timeout_seconds=DOCKER_EXECUTION_TIMEOUT_SECONDS,
            operation_label="hermetic",
            container_environment=environment_payload,
            environment_repository_root=repository_root or manifest.root,
            environment_file_index=environment_file_index,
        )
    except OSError as exc:
        raise ConfigurationError("Docker container execution could not start") from exc
    elapsed = (time.perf_counter() - started) * 1000
    return process.returncode, elapsed, process.stdout, process.stderr
