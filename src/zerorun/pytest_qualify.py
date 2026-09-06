from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .fingerprint import read_stable_file_bytes
from .manifest import Manifest
from .model import ConfigurationError, TaskSpec
from .path_safety import (
    atomic_replace_bytes,
    is_link_like,
    private_temporary_directory,
)
from .oci import (
    DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
    DOCKER_RESOURCE_ARGS,
    _container_environment_bytes,
    _docker_client_environment,
    _docker_bind_mount,
    _docker_path,
    _git_mask_args,
    _new_container_name,
    _run_created_container,
    _validate_task_docker_environment,
    hermetic_environment,
    inspect_runtime,
    reject_sourceless_workspace_bytecode,
)
from .pytest_closure import (
    CallSite,
    ExecutedImportEdge,
    LocalImportSurface,
    SymbolClosureAnalysisCache,
    discover_pytest_static_inputs,
    package_ancestor_import_surface,
    resolve_symbol_closure,
    static_import_requests,
    validate_absent_import_paths,
)
from .pytest_collection_contract import (
    validate_collection_invocation_contract,
    validate_collection_item_paths,
    validate_reviewed_pytest_targets,
)
from .pytest_node_key import build_pytest_source_review
from .pytest_profile import validate_pytest_execution_contract
from .pytest_runtime import (
    _COLLECTION_PLUGIN,
    _read_bounded_json,
    _validate_collection_evidence,
    _validate_bounded_json_value,
    collect_pytest,  # Backward-compatible monkeypatch seam; qualification no longer calls it.
    collection_plugin_sha256,
)


_PROFILE_MOUNT = "/zerorun-pytest-qualify"
_PROFILE_OUTPUT_MOUNT = "/zerorun-pytest-qualify-output"
_CONTEXT_FILE = "zerorun_profile_context.py"
_PLUGIN_FILE = "zerorun_qualify_plugin.py"
_COLLECTION_PLUGIN_FILE = "zerorun_collection_plugin.py"
_COLLECTION_OUTPUT_FILE = "collection.json"
_SITE_FILE = "sitecustomize.py"
_SELECTION_FILE = "profile-selection.json"
_CANDIDATE_SCHEMA = "zerorun-pytest-candidate-v1"
_MAX_CANDIDATE_BYTES = 8 * 1024 * 1024
_MAX_PROFILE_SELECTION_BYTES = 8 * 1024 * 1024
_MAX_PROFILE_PROCESSES = 128
_MAX_TOTAL_PROFILE_BYTES = 64 * 1024 * 1024
_MAX_PROFILE_IMPORT_MODULES = 20000
_MAX_PROFILE_IMPORT_ROOTS = 512
_MAX_PROFILE_IMPORT_TEXT = 8192
_MAX_PROFILE_IMPORT_EDGES = 100000
_MAX_PROFILE_IMPORT_FROMLIST = 1024
_PROFILE_NAME_RE = re.compile(r"^profile-[1-9][0-9]*\.json$")
_CONTEXT_SOURCE = '''CURRENT_NODE = "__session__"\n'''


def validate_repository_file_path(
    root: Path,
    supplied: Path,
    *,
    field: str,
) -> Path:
    """Validate an exact lexical repository file path without following links."""

    repository = root.expanduser().resolve(strict=True)
    candidate = Path(os.path.abspath(supplied.expanduser()))
    try:
        relative = candidate.relative_to(repository)
    except ValueError as exc:
        raise ConfigurationError(f"{field} must remain inside the repository") from exc
    if not relative.parts:
        raise ConfigurationError(f"{field} must name a file inside the repository")
    cursor = repository
    for part in relative.parts:
        cursor = cursor / part
        if is_link_like(cursor):
            raise ConfigurationError(
                f"{field} path contains a symbolic link or junction: {cursor}"
            )
    if os.path.normcase(str(candidate.resolve(strict=False))) != os.path.normcase(
        str(candidate)
    ):
        raise ConfigurationError(f"{field} does not resolve to its exact repository path")
    if candidate.exists() and not candidate.is_file():
        raise ConfigurationError(f"{field} must be a regular file: {candidate}")
    return candidate


def validate_unlinked_file_path(supplied: Path, *, field: str) -> Path:
    candidate = Path(os.path.abspath(supplied.expanduser()))
    for component in (*reversed(candidate.parents), candidate):
        if is_link_like(component):
            raise ConfigurationError(
                f"{field} path contains a symbolic link or junction: {component}"
            )
    return candidate


_PLUGIN_SOURCE = r'''from __future__ import annotations
import json
import os
from pathlib import Path
import pytest
import sitecustomize as _zr_site
import zerorun_profile_context as _ctx


def pytest_configure(config):
    _zr_site.mark_pytest_plugin_loaded()
    _zr_site.assert_profile_intact("pytest_configure")


def pytest_collection_modifyitems(config, items):
    collected = [str(item.nodeid) for item in items]
    selection_path = os.environ.get("ZERORUN_PROFILE_SELECTION")
    if not selection_path:
        _zr_site.mark_profile_collection(collected, collected)
        return
    try:
        raw = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    except BaseException as exc:
        raise RuntimeError("ZeroRun qualification selection is unreadable") from exc
    if (
        not isinstance(raw, dict)
        or set(raw) != {"expected_nodeids", "selected_nodeids"}
        or not isinstance(raw.get("expected_nodeids"), list)
        or not isinstance(raw.get("selected_nodeids"), list)
        or not all(isinstance(value, str) and value for value in raw["expected_nodeids"])
        or not all(isinstance(value, str) and value for value in raw["selected_nodeids"])
        or len(raw["expected_nodeids"]) != len(set(raw["expected_nodeids"]))
        or len(raw["selected_nodeids"]) != len(set(raw["selected_nodeids"]))
    ):
        raise RuntimeError("ZeroRun qualification selection is malformed")
    if collected != raw["expected_nodeids"]:
        raise RuntimeError("ZeroRun qualification collection identity changed")
    selected_set = set(raw["selected_nodeids"])
    if not selected_set.issubset(set(collected)):
        raise RuntimeError("ZeroRun qualification selection is not a collection subset")
    selected = [item for item in items if str(item.nodeid) in selected_set]
    deselected = [item for item in items if str(item.nodeid) not in selected_set]
    if [str(item.nodeid) for item in selected] != raw["selected_nodeids"]:
        raise RuntimeError("ZeroRun qualification selection order changed")
    _zr_site.mark_profile_collection(collected, raw["selected_nodeids"])
    items[:] = selected
    if deselected:
        config.hook.pytest_deselected(items=deselected)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item, nextitem):
    _zr_site.assert_profile_intact("pytest_runtest_protocol:start")
    previous = _ctx.CURRENT_NODE
    _ctx.CURRENT_NODE = str(item.nodeid)
    _zr_site.mark_node_started(str(item.nodeid))
    try:
        yield
    finally:
        _zr_site.mark_node_completed(str(item.nodeid))
        _ctx.CURRENT_NODE = previous
        _zr_site.mark_node_boundary()
        _zr_site.assert_profile_intact("pytest_runtest_protocol:end")


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_fixture_setup(fixturedef, request):
    scope = str(getattr(fixturedef, "scope", ""))
    if scope == "function":
        yield
        return
    previous = _ctx.CURRENT_NODE
    _ctx.CURRENT_NODE = "__session__"
    try:
        _zr_site.mark_non_function_fixture(
            getattr(fixturedef, "func", None),
            scope,
            str(getattr(fixturedef, "argname", "<unknown>")),
        )
        yield
    finally:
        _ctx.CURRENT_NODE = previous


def pytest_sessionfinish(session, exitstatus):
    _zr_site.assert_profile_intact("pytest_sessionfinish")
'''

_SITE_SOURCE = r'''from __future__ import annotations
import atexit
import ast
import builtins
import dis
import hashlib
import json
import os
import pathlib
import stat
import sys
import _thread
import threading
import zerorun_profile_context as _ctx

_ROOT = "/workspace"
_OUT_DIR = "/zerorun-pytest-qualify-output"
_SUPPORT_DIR = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
_EPHEMERAL_ROOT = "/tmp"
_DYNAMIC = frozenset(("globals", "eval", "exec", "__import__"))
_DYNAMIC_LOAD_OPS = frozenset(("LOAD_GLOBAL", "LOAD_NAME"))
_BUCKETS = {}
_CODE_META = {}
_INTEGRITY_VIOLATIONS = []
_UNCERTAINTIES = {}
_NON_FUNCTION_FIXTURES = set()
_PYTEST_PLUGIN_LOADED = False
_COLLECTION_NODEIDS = None
_SELECTED_NODEIDS = None
_STARTED_NODEIDS = set()
_COMPLETED_NODEIDS = set()
_FILESYSTEM_MUTATIONS = {}
_EXECUTED_IMPORTS = {}
_EPHEMERAL_SNAPSHOTS_BEFORE = {}
_ORIGINAL_IMPORT = builtins.__import__
_ORIGINAL_THREAD_START = threading.Thread.start
_ORIGINAL_THREAD_JOIN = threading.Thread.join
_ORIGINAL_THREAD_IS_ALIVE = threading.Thread.is_alive
_ORIGINAL_THREAD_BOOTSTRAP = threading.Thread._bootstrap
_ORIGINAL_THREAD_BOOTSTRAP_INNER = threading.Thread._bootstrap_inner
_THREAD_PROFILE_BOOTSTRAP_CODES = (
    _ORIGINAL_THREAD_BOOTSTRAP.__code__,
    _ORIGINAL_THREAD_BOOTSTRAP_INNER.__code__,
)
_ORIGINAL_THREADING_SETPROFILE = threading.setprofile
_ORIGINAL_THREADING_GETPROFILE = getattr(threading, "getprofile", None)
_ORIGINAL_THREADING_SETPROFILE_ALL = getattr(
    threading, "setprofile_all_threads", None
)
_ORIGINAL_SYS_SETPROFILE = sys.setprofile
_ORIGINAL_LOWLEVEL_THREAD_START = _thread.start_new_thread
_ORIGINAL_LOWLEVEL_THREAD_START_ALIAS = getattr(_thread, "start_new", None)
_ORIGINAL_OS_STAT = os.stat
_ORIGINAL_OS_LSTAT = os.lstat
_ORIGINAL_OS_ACCESS = os.access
_ORIGINAL_OS_FSTAT = os.fstat
_ORIGINAL_OS_LISTDIR = os.listdir
_ORIGINAL_OS_READLINK = os.readlink
_ORIGINAL_OS_SCANDIR = os.scandir
_ORIGINAL_OPEN = open
_ORIGINAL_OS_PATH_EXISTS = os.path.exists
_ORIGINAL_OS_PATH_LEXISTS = os.path.lexists
_ORIGINAL_OS_PATH_ISFILE = os.path.isfile
_ORIGINAL_OS_PATH_ISDIR = os.path.isdir
_ORIGINAL_PATH_STAT = pathlib.Path.stat
_ORIGINAL_PATH_LSTAT = pathlib.Path.lstat
_ORIGINAL_PATH_EXISTS = pathlib.Path.exists
_ORIGINAL_PATH_IS_FILE = pathlib.Path.is_file
_ORIGINAL_PATH_IS_DIR = pathlib.Path.is_dir
_OS_BACKEND = sys.modules.get("posix") or sys.modules.get("nt")
_ORIGINAL_BACKEND_STAT = getattr(_OS_BACKEND, "stat", None)
_ORIGINAL_BACKEND_LSTAT = getattr(_OS_BACKEND, "lstat", None)
_ORIGINAL_BACKEND_ACCESS = getattr(_OS_BACKEND, "access", None)
_ORIGINAL_GET_IDENT = threading.get_ident
_ORIGINAL_CURRENT_THREAD = threading.current_thread
_MAIN_THREAD_ID = _ORIGINAL_GET_IDENT()
_THREAD_RECORDS = {}
_THREAD_SEQUENCE = 0
_THREAD_OBSERVATIONS = {}
_THREAD_REGISTRY_LOCK = threading.RLock()
_SOURCE_IMPORT_LOCK = threading.RLock()
_THREAD_LOCAL = threading.local()
_SEEN_CODE_BY_BUCKET = {}
_IMPORT_INSTRUCTION_META = {}
_ASSERTION_REWRITE_IMPORT_OFFSETS = {}
_SOURCE_IMPORT_SPANS = {}
_SOURCE_IMPORT_ROW_COUNT = 0
_SOURCE_IMPORT_BYTE_COUNT = 0
_SOURCE_IMPORT_NODE_COUNT = 0
_MAX_THREADS = 10000
_MONITORING = getattr(sys, "monitoring", None)
_ORIGINAL_MONITORING_USE_TOOL_ID = getattr(_MONITORING, "use_tool_id", None)
_ORIGINAL_MONITORING_FREE_TOOL_ID = getattr(_MONITORING, "free_tool_id", None)
_ORIGINAL_MONITORING_CLEAR_TOOL_ID = getattr(_MONITORING, "clear_tool_id", None)
_ORIGINAL_MONITORING_GET_TOOL = getattr(_MONITORING, "get_tool", None)
_ORIGINAL_MONITORING_SET_EVENTS = getattr(_MONITORING, "set_events", None)
_ORIGINAL_MONITORING_GET_EVENTS = getattr(_MONITORING, "get_events", None)
_ORIGINAL_MONITORING_SET_LOCAL_EVENTS = getattr(
    _MONITORING, "set_local_events", None
)
_ORIGINAL_MONITORING_REGISTER_CALLBACK = getattr(
    _MONITORING, "register_callback", None
)
_ORIGINAL_MONITORING_RESTART_EVENTS = getattr(_MONITORING, "restart_events", None)
_MONITORING_ENABLED = False
_MONITORING_TOOL_ID = None
_MONITORING_EVENTS = 0
_MONITORING_CALLBACKS = ()
_MONITORING_NAME = "zerorun-pytest-qualify"
_MAX_BUCKETS = 25000
_MAX_SITES = 100000
_MAX_FIXTURES = 10000
_MAX_IMPORT_MODULES = 20000
_MAX_IMPORT_SEARCH_ROOTS = 512
_MAX_IMPORT_TEXT = 8192
_MAX_IMPORT_EDGES = 100000
_MAX_IMPORT_FROMLIST = 1024
_MAX_FILESYSTEM_MUTATIONS = 10000
_MAX_EPHEMERAL_SNAPSHOT_ENTRIES = 50000
_MAX_EPHEMERAL_SNAPSHOT_BYTES = 64 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_MAX_SOURCE_IMPORT_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_IMPORT_NODES = 1000000
_SITE_COUNT = 0
_SEEN_CODE_COUNT = 0
_IMPORT_EDGE_COUNT = 0
_INITIAL_META_PATH = tuple(sys.meta_path)
_INITIAL_PATH_HOOKS = tuple(sys.path_hooks)
_INITIAL_SITE_PREFIXES = tuple(
    dict.fromkeys(
        os.path.normpath(value).replace("\\", "/")
        for value in sys.path
        if isinstance(value, str)
        and os.path.isabs(value)
        and (
            "/site-packages/" in os.path.normpath(value).replace("\\", "/").casefold()
            or os.path.normpath(value).replace("\\", "/").casefold().endswith(
                "/site-packages"
            )
            or "/dist-packages/" in os.path.normpath(value).replace("\\", "/").casefold()
            or os.path.normpath(value).replace("\\", "/").casefold().endswith(
                "/dist-packages"
            )
        )
    )
)
_IMPORT_MODULES = set(
    name
    for name in sys.modules
    if isinstance(name, str)
    and name
    and len(name) <= _MAX_IMPORT_TEXT
    and all(part.isidentifier() for part in name.split("."))
)
_IMPORT_PROJECT_SEARCH_ROOTS = set()


def mark_pytest_plugin_loaded():
    global _PYTEST_PLUGIN_LOADED
    _PYTEST_PLUGIN_LOADED = True


def assert_profile_intact(label):
    if builtins.__import__ is not _guarded_import:
        message = "%s: builtins.__import__ guard was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if sys.setprofile is not _guarded_sys_setprofile:
        message = "%s: sys.setprofile API was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if _MONITORING_ENABLED:
        try:
            owner = _ORIGINAL_MONITORING_GET_TOOL(_MONITORING_TOOL_ID)
            events = _ORIGINAL_MONITORING_GET_EVENTS(_MONITORING_TOOL_ID)
            callbacks_intact = True
            for event, callback in _MONITORING_CALLBACKS:
                previous = _ORIGINAL_MONITORING_REGISTER_CALLBACK(
                    _MONITORING_TOOL_ID,
                    event,
                    callback,
                )
                callbacks_intact = callbacks_intact and previous is callback
            intact = (
                owner == _MONITORING_NAME
                and events == _MONITORING_EVENTS
                and len(_MONITORING_CALLBACKS) == 2
                and callbacks_intact
            )
        except BaseException:
            intact = False
        if not intact:
            message = "%s: sys.monitoring profiler was replaced" % label
            if message not in _INTEGRITY_VIOLATIONS:
                _INTEGRITY_VIOLATIONS.append(message)
        guarded_monitoring_apis = (
            ("use_tool_id", _monitoring_use_tool_id),
            ("free_tool_id", _monitoring_free_tool_id),
            ("clear_tool_id", _monitoring_clear_tool_id),
            ("set_events", _monitoring_set_events),
            ("set_local_events", _monitoring_set_local_events),
            ("register_callback", _monitoring_register_callback),
            ("restart_events", _monitoring_restart_events),
        )
        for api_name, expected in guarded_monitoring_apis:
            if getattr(_MONITORING, api_name, None) is not expected:
                message = "%s: sys.monitoring.%s guard was replaced" % (
                    label,
                    api_name,
                )
                if message not in _INTEGRITY_VIOLATIONS:
                    _INTEGRITY_VIOLATIONS.append(message)
        readonly_monitoring_apis = (
            ("get_tool", _ORIGINAL_MONITORING_GET_TOOL),
            ("get_events", _ORIGINAL_MONITORING_GET_EVENTS),
        )
        for api_name, expected in readonly_monitoring_apis:
            if getattr(_MONITORING, api_name, None) is not expected:
                message = "%s: sys.monitoring.%s was replaced" % (label, api_name)
                if message not in _INTEGRITY_VIOLATIONS:
                    _INTEGRITY_VIOLATIONS.append(message)
    elif sys.getprofile() is not _profile:
        message = "%s: sys.setprofile hook was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if not _MONITORING_ENABLED and (
        _ORIGINAL_THREADING_GETPROFILE is None
        or _ORIGINAL_THREADING_GETPROFILE() is not _profile
    ):
        message = "%s: threading child profile hook was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.setprofile is not _threading_setprofile:
        message = "%s: threading.setprofile guard was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if (
        _ORIGINAL_THREADING_SETPROFILE_ALL is not None
        and threading.setprofile_all_threads is not _threading_setprofile_all_threads
    ):
        message = "%s: threading.setprofile_all_threads guard was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.Thread.start is not _thread_start:
        message = "%s: threading.Thread.start hook was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.Thread.join is not _thread_join:
        message = "%s: threading.Thread.join hook was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.Thread.is_alive is not _ORIGINAL_THREAD_IS_ALIVE:
        message = "%s: threading.Thread.is_alive was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.Thread._bootstrap is not _ORIGINAL_THREAD_BOOTSTRAP:
        message = "%s: threading.Thread._bootstrap was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.Thread._bootstrap_inner is not _ORIGINAL_THREAD_BOOTSTRAP_INNER:
        message = "%s: threading.Thread._bootstrap_inner was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if _thread.start_new_thread is not _lowlevel_thread_start:
        message = "%s: _thread.start_new_thread hook was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if (
        _ORIGINAL_LOWLEVEL_THREAD_START_ALIAS is not None
        and getattr(_thread, "start_new", None) is not _lowlevel_thread_start
    ):
        message = "%s: _thread.start_new hook was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.get_ident is not _ORIGINAL_GET_IDENT:
        message = "%s: threading.get_ident was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    if threading.current_thread is not _ORIGINAL_CURRENT_THREAD:
        message = "%s: threading.current_thread was replaced" % label
        if message not in _INTEGRITY_VIOLATIONS:
            _INTEGRITY_VIOLATIONS.append(message)
    metadata_apis = (
        ("os.stat", os.stat, _metadata_stat),
        ("os.lstat", os.lstat, _metadata_lstat),
        ("os.access", os.access, _metadata_access),
        ("os.path.exists", os.path.exists, _metadata_path_exists),
        ("os.path.lexists", os.path.lexists, _metadata_path_lexists),
        ("os.path.isfile", os.path.isfile, _metadata_path_isfile),
        ("os.path.isdir", os.path.isdir, _metadata_path_isdir),
        ("Path.stat", pathlib.Path.stat, _metadata_path_stat),
        ("Path.lstat", pathlib.Path.lstat, _metadata_path_lstat),
        ("Path.exists", pathlib.Path.exists, _metadata_pathlib_exists),
        ("Path.is_file", pathlib.Path.is_file, _metadata_pathlib_is_file),
        ("Path.is_dir", pathlib.Path.is_dir, _metadata_pathlib_is_dir),
    )
    if _OS_BACKEND is not None:
        metadata_apis += (
            ("backend.stat", getattr(_OS_BACKEND, "stat", None), _metadata_stat),
            ("backend.lstat", getattr(_OS_BACKEND, "lstat", None), _metadata_lstat),
            ("backend.access", getattr(_OS_BACKEND, "access", None), _metadata_access),
        )
    for api_name, observed, expected in metadata_apis:
        if observed is not expected:
            message = "%s: %s metadata guard was replaced" % (label, api_name)
            if message not in _INTEGRITY_VIOLATIONS:
                _INTEGRITY_VIOLATIONS.append(message)


def mark_profile_collection(collection_nodeids, selected_nodeids):
    global _COLLECTION_NODEIDS, _SELECTED_NODEIDS
    collection = tuple(str(value) for value in collection_nodeids)
    selected = tuple(str(value) for value in selected_nodeids)
    if _COLLECTION_NODEIDS is not None and _COLLECTION_NODEIDS != collection:
        _INTEGRITY_VIOLATIONS.append("qualification collection changed within one process")
    if _SELECTED_NODEIDS is not None and _SELECTED_NODEIDS != selected:
        _INTEGRITY_VIOLATIONS.append("qualification selection changed within one process")
    _COLLECTION_NODEIDS = collection
    _SELECTED_NODEIDS = selected


def mark_node_started(nodeid):
    nodeid = str(nodeid)
    _STARTED_NODEIDS.add(nodeid)
    try:
        if os.path.abspath(os.getcwd()) != os.path.abspath(_ROOT):
            _mark_uncertainty("cwd-not-restored-at-node-start", nodeid)
    except BaseException:
        _mark_uncertainty("cwd-unreadable-at-node-start", nodeid)


def mark_node_boundary():
    """Retain an explicit pytest boundary hook for integrity checks."""


def mark_node_completed(nodeid):
    nodeid = str(nodeid)
    try:
        if os.path.abspath(os.getcwd()) != os.path.abspath(_ROOT):
            _mark_uncertainty("cwd-not-restored-at-node-completion", nodeid)
    except BaseException:
        _mark_uncertainty("cwd-unreadable-at-node-completion", nodeid)
    _finalize_thread_bucket(nodeid)
    _finalize_ephemeral_bucket(nodeid)
    _COMPLETED_NODEIDS.add(nodeid)


def _project_relative(filename):
    if not isinstance(filename, str) or not filename.endswith(".py"):
        return None
    normalized = filename.replace("\\", "/")
    if normalized == _SUPPORT_DIR or normalized.startswith(_SUPPORT_DIR.rstrip("/") + "/"):
        return None
    root_prefix = _ROOT.rstrip("/") + "/"
    comparable = normalized.casefold() if os.name == "nt" else normalized
    comparable_root = root_prefix.casefold() if os.name == "nt" else root_prefix
    if comparable.startswith(comparable_root + ".zerorun-env/"):
        return None
    if comparable.startswith(comparable_root):
        return normalized[len(root_prefix):]
    return None


def _code_metadata(code):
    code_id = id(code)
    cached = _CODE_META.get(code_id)
    if cached is not None and cached[0] is code:
        return cached[1]
    relative = _project_relative(code.co_filename)
    names = tuple(sorted(name for name in code.co_names if isinstance(name, str)))
    try:
        dynamic = any(
            instruction.opname in _DYNAMIC_LOAD_OPS and instruction.argval in _DYNAMIC
            for instruction in dis.get_instructions(code)
        )
    except BaseException:
        dynamic = bool(_DYNAMIC.intersection(code.co_names))
    metadata = (relative, int(code.co_firstlineno), str(code.co_name), names, dynamic)
    # CodeType equality intentionally ignores co_filename.  Key by object id
    # and retain the exact object so equal bytecode from distinct project files
    # cannot borrow one another's path metadata (and ids cannot be recycled).
    if len(_CODE_META) >= _MAX_SITES:
        _mark_uncertainty("profiler-code-metadata-boundary-exceeded")
    else:
        _CODE_META[code_id] = (code, metadata)
    return metadata


def _import_instruction_metadata(code, instruction_offset):
    code_id = id(code)
    record = _IMPORT_INSTRUCTION_META.get(code_id)
    cached = record[1] if record is not None and record[0] is code else None
    if cached is None:
        if len(_IMPORT_INSTRUCTION_META) >= _MAX_SITES:
            return None
        try:
            cached = {}
            active_line = None
            for instruction in dis.get_instructions(code):
                if type(instruction.starts_line) is int:
                    active_line = instruction.starts_line
                positions = getattr(instruction, "positions", None)
                position_line = getattr(positions, "lineno", None)
                cached[int(instruction.offset)] = (
                    str(instruction.opname),
                    instruction.argval,
                    position_line if type(position_line) is int else active_line,
                    getattr(positions, "end_lineno", None),
                    getattr(positions, "col_offset", None),
                    getattr(positions, "end_col_offset", None),
                )
        except BaseException:
            return None
        _IMPORT_INSTRUCTION_META[code_id] = (code, cached)
    return cached.get(int(instruction_offset))


def _source_path_identity(relative):
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("invalid project-relative source path")
    cursor = _ROOT
    for part in relative.split("/"):
        cursor = os.path.join(cursor, part)
        details = _ORIGINAL_OS_LSTAT(cursor)
        if stat.S_ISLNK(details.st_mode):
            raise ValueError("source path contains a link")
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or details.st_size > _MAX_EVIDENCE_BYTES
    ):
        raise ValueError("source is not a bounded regular file")
    identity = (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
        details.st_nlink,
        details.st_mode,
    )
    return cursor, identity


def _source_identity_without_ctime(identity):
    # Windows exposes incompatible st_ctime_ns values for path and descriptor
    # stat calls. Compare ctime within each observation method, and all other
    # identity fields across path/descriptor observations.
    return identity[:4] + identity[5:]


def _pytest_rewrite_insertion_line(tree):
    """Reproduce the pinned pytest rewriter's source insertion position."""
    body = getattr(tree, "body", None)
    if not isinstance(body, list) or not body:
        return None
    item = None
    expect_docstring = True
    for item in body:
        is_docstring = (
            expect_docstring
            and isinstance(item, ast.Expr)
            and isinstance(item.value, ast.Constant)
            and isinstance(item.value.value, str)
        )
        if is_docstring:
            if "PYTEST_DONT_REWRITE" in item.value.value:
                return None
            expect_docstring = False
            continue
        if (
            isinstance(item, ast.ImportFrom)
            and int(item.level or 0) == 0
            and item.module == "__future__"
        ):
            continue
        break
    if item is None:
        return None
    if isinstance(item, ast.FunctionDef) and item.decorator_list:
        line = getattr(item.decorator_list[0], "lineno", None)
    else:
        line = getattr(item, "lineno", None)
    return line if type(line) is int and line > 0 else None


def _source_import_spans(relative):
    """Return bounded, immutable AST import and rewrite-position evidence.

    Python 3.10 bytecode exposes line starts but not the end-line and column
    positions added to ``dis.Instruction`` in Python 3.11.  Recover those
    fields only from an exact, regular, non-link project source file.  The
    same stable parse binds pytest's assertion-rewrite insertion line on the
    explicitly supported PEP 669 runtimes.  Callers still require exact,
    unique matches, so ambiguity is fail-closed rather than guessed.
    """
    global _SOURCE_IMPORT_BYTE_COUNT
    global _SOURCE_IMPORT_NODE_COUNT
    global _SOURCE_IMPORT_ROW_COUNT
    with _SOURCE_IMPORT_LOCK:
        if relative in _SOURCE_IMPORT_SPANS:
            cached = _SOURCE_IMPORT_SPANS[relative]
            if cached is None:
                return None
            cached_identity, cached_rows, _cached_rewrite_line = cached
            try:
                _path, current_identity = _source_path_identity(relative)
            except BaseException:
                current_identity = None
            if current_identity != cached_identity:
                _SOURCE_IMPORT_SPANS[relative] = None
                return None
            return cached_rows
        if len(_SOURCE_IMPORT_SPANS) >= _MAX_SITES:
            return None
        result = None
        try:
            cursor, identity = _source_path_identity(relative)
            if _SOURCE_IMPORT_BYTE_COUNT + identity[2] > _MAX_SOURCE_IMPORT_BYTES:
                raise ValueError("aggregate source byte boundary exceeded")
            # Reserve bytes before parsing. Failed/ambiguous attempts retain
            # their reservation so adversarial churn cannot reset the bound.
            _SOURCE_IMPORT_BYTE_COUNT += identity[2]
            with _ORIGINAL_OPEN(cursor, "rb") as handle:
                opened = _ORIGINAL_OS_FSTAT(handle.fileno())
                opened_identity = (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_ctime_ns,
                    opened.st_nlink,
                    opened.st_mode,
                )
                if _source_identity_without_ctime(
                    opened_identity
                ) != _source_identity_without_ctime(identity):
                    raise ValueError("source identity changed before read")
                payload = handle.read(_MAX_EVIDENCE_BYTES + 1)
                after = _ORIGINAL_OS_FSTAT(handle.fileno())
            if len(payload) > _MAX_EVIDENCE_BYTES:
                raise ValueError("source byte boundary exceeded")
            if len(payload) != opened.st_size:
                raise ValueError("source read was incomplete")
            _path, final_identity = _source_path_identity(relative)
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
                after.st_nlink,
                after.st_mode,
            )
            if (
                after_identity != opened_identity
                or final_identity != identity
                or _source_identity_without_ctime(
                    final_identity
                ) != _source_identity_without_ctime(after_identity)
            ):
                raise ValueError("source identity changed during read")
            tree = ast.parse(payload, filename=relative, mode="exec")
            rewrite_line = _pytest_rewrite_insertion_line(tree)
            rows = []
            pending = [tree]
            visited = 0
            while pending:
                node = pending.pop()
                visited += 1
                _SOURCE_IMPORT_NODE_COUNT += 1
                if (
                    visited > _MAX_SITES
                    or _SOURCE_IMPORT_NODE_COUNT > _MAX_SOURCE_IMPORT_NODES
                ):
                    raise ValueError("source AST boundary exceeded")
                pending.extend(ast.iter_child_nodes(node))
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                position = (
                    getattr(node, "lineno", None),
                    getattr(node, "end_lineno", None),
                    getattr(node, "col_offset", None),
                    getattr(node, "end_col_offset", None),
                )
                if any(type(value) is not int for value in position):
                    raise ValueError("source import has no exact span")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        rows.append((*position, alias.name, (), 0))
                else:
                    rows.append(
                        (
                            *position,
                            node.module or "",
                            tuple(alias.name for alias in node.names),
                            int(node.level or 0),
                        )
                    )
                if (
                    len(rows) > _MAX_IMPORT_EDGES
                    or _SOURCE_IMPORT_ROW_COUNT + len(rows) > _MAX_IMPORT_EDGES
                ):
                    raise ValueError("source import boundary exceeded")
            rows = tuple(rows)
            result = (identity, rows, rewrite_line)
        except BaseException:
            result = None
        _SOURCE_IMPORT_SPANS[relative] = result
        if result is None:
            return None
        _SOURCE_IMPORT_ROW_COUNT += len(result[1])
        return result[1]


def _python310_import_span(relative, lineno, name, fromlist, level):
    rows = _source_import_spans(relative)
    if rows is None:
        return None, False
    normalized_fromlist = () if fromlist is None else tuple(fromlist)
    matches = [
        row[:4]
        for row in rows
        if row[0] == lineno
        and row[4] == name
        and row[5] == normalized_fromlist
        and row[6] == level
    ]
    if len(matches) == 1:
        return matches[0], True
    return None, True


def _source_rewrite_insertion_line(relative):
    rows = _source_import_spans(relative)
    if rows is None:
        return None, False
    cached = _SOURCE_IMPORT_SPANS.get(relative)
    if not isinstance(cached, tuple) or len(cached) != 3:
        return None, False
    return cached[2], True


def _zero_width_instruction(row):
    return (
        isinstance(row, tuple)
        and len(row) == 6
        and type(row[2]) is int
        and row[2] > 0
        and row[3] == row[2]
        and row[4] == 0
        and row[5] == 0
    )


def _pytest_rewrite_import_offsets(code, relative):
    code_id = id(code)
    cached = _ASSERTION_REWRITE_IMPORT_OFFSETS.get(code_id)
    if cached is not None and cached[0] is code:
        if not cached[1]:
            return ()
        # Revalidate the immutable source identity for the second member of
        # the pair (and any later lookup).  A cached bytecode result must not
        # survive concurrent source replacement during module execution.
        _rewrite_line, source_parsed = _source_rewrite_insertion_line(relative)
        return cached[1] if source_parsed else ()
    result = ()
    try:
        if (
            sys.implementation.name != "cpython"
            or sys.version_info[:2]
            not in {(3, 10), (3, 11), (3, 12), (3, 13), (3, 14)}
            or code.co_firstlineno != 1
            or code.co_name != "<module>"
        ):
            raise ValueError("unsupported assertion-rewrite bytecode runtime")
        metadata_record = _IMPORT_INSTRUCTION_META.get(code_id)
        if metadata_record is None or metadata_record[0] is not code:
            raise ValueError("instruction metadata is unavailable")
        metadata = metadata_record[1]
        if not isinstance(metadata, dict):
            raise ValueError("instruction metadata is malformed")
        instructions = tuple(sorted(metadata.items()))

        def matches(index, expected):
            if index < 0 or index + len(expected) > len(instructions):
                return False
            rows = instructions[index : index + len(expected)]
            for (_offset, row), (opnames, argval) in zip(rows, expected):
                if (
                    row[0] not in opnames
                    or row[1] != argval
                    or (argval == 0 and type(row[1]) is not int)
                ):
                    return False
            return True

        load_zero = frozenset(
            ("LOAD_CONST",)
            if sys.version_info[:2] != (3, 14)
            else ("LOAD_SMALL_INT",)
        )
        stack_swap = (
            (frozenset(("ROT_TWO",)), None)
            if sys.version_info[:2] == (3, 10)
            else (frozenset(("SWAP",)), 2)
        )
        builtins_pattern = (
            (load_zero, 0),
            (frozenset(("LOAD_CONST",)), None),
            (frozenset(("IMPORT_NAME",)), "builtins"),
            (frozenset(("STORE_NAME",)), "@py_builtins"),
        )
        rewrite_pattern = (
            (load_zero, 0),
            (frozenset(("LOAD_CONST",)), None),
            (frozenset(("IMPORT_NAME",)), "_pytest.assertion.rewrite"),
            (frozenset(("IMPORT_FROM",)), "assertion"),
            stack_swap,
            (frozenset(("POP_TOP",)), None),
            (frozenset(("IMPORT_FROM",)), "rewrite"),
            (frozenset(("STORE_NAME",)), "@pytest_ar"),
            (frozenset(("POP_TOP",)), None),
        )
        starts = [
            index
            for index in range(len(instructions))
            if matches(index, builtins_pattern)
            and matches(index + len(builtins_pattern), rewrite_pattern)
        ]
        if len(starts) != 1:
            raise ValueError("assertion-rewrite import pair is not unique")
        start = starts[0]
        pair = instructions[start : start + len(builtins_pattern) + len(rewrite_pattern)]
        if sys.version_info[:2] == (3, 10):
            rewrite_line = pair[0][1][2]
            if not all(
                type(row[2]) is int
                and row[2] == rewrite_line
                and row[3:] == (None, None, None)
                for _, row in pair
            ):
                raise ValueError("assertion-rewrite import pair has an invalid line")
            position = None
        else:
            position = pair[0][1][2:]
            rewrite_line = position[0]
            if not all(
                _zero_width_instruction(row) and row[2:] == position
                for _, row in pair
            ):
                raise ValueError("assertion-rewrite import pair has an invalid span")
        source_rewrite_line, source_parsed = _source_rewrite_insertion_line(relative)
        if not source_parsed or source_rewrite_line != rewrite_line:
            raise ValueError("assertion-rewrite import pair is not source-position bound")
        source_rows = _source_import_spans(relative)
        if source_rows is None or (
            position is not None
            and any(
                row[:4] == position
                and row[4] in {"builtins", "_pytest.assertion.rewrite"}
                and row[5] == ()
                and row[6] == 0
                for row in source_rows
            )
        ):
            raise ValueError("assertion-rewrite import pair collides with source")
        reserved_stores = [
            (offset, row[1])
            for offset, row in instructions
            if row[0] == "STORE_NAME" and row[1] in {"@py_builtins", "@pytest_ar"}
        ]
        expected_stores = [
            (pair[3][0], "@py_builtins"),
            (pair[11][0], "@pytest_ar"),
        ]
        if reserved_stores != expected_stores:
            raise ValueError("assertion-rewrite reserved stores are not exact")
        result = (
            (pair[2][0], "builtins"),
            (pair[6][0], "_pytest.assertion.rewrite"),
        )
    except BaseException:
        result = ()
    if len(_ASSERTION_REWRITE_IMPORT_OFFSETS) < _MAX_SITES:
        _ASSERTION_REWRITE_IMPORT_OFFSETS[code_id] = (code, result)
    return result


def _pytest_rewrite_execution_is_authentic(frame, code, relative):
    try:
        namespace = frame.f_globals
        module_name = namespace.get("__name__")
        module = sys.modules.get(module_name)
        if type(module) is not type(sys):
            return False
        module_namespace = object.__getattribute__(module, "__dict__")
        if module_namespace is not namespace:
            return False
        loader = namespace.get("__loader__")
        spec = namespace.get("__spec__")
        origin = object.__getattribute__(spec, "origin")
        module_file = namespace.get("__file__")
        if (
            _project_relative(origin) != relative
            or _project_relative(module_file) != relative
            or _project_relative(code.co_filename) != relative
        ):
            return False
        rewrite_module = sys.modules.get("_pytest.assertion.rewrite")
        if type(rewrite_module) is not type(sys):
            return False
        rewrite_namespace = object.__getattribute__(rewrite_module, "__dict__")
        expected_loader_type = rewrite_namespace.get("AssertionRewritingHook")
        rewrite_spec = rewrite_namespace.get("__spec__")
        rewrite_origin = object.__getattribute__(rewrite_spec, "origin")
        if (
            not isinstance(expected_loader_type, type)
            or type(loader) is not expected_loader_type
            or _immutable_external_path(rewrite_origin) is None
        ):
            return False
        exec_module = object.__getattribute__(expected_loader_type, "__dict__").get(
            "exec_module"
        )
        exec_code = getattr(exec_module, "__code__", None)
        if exec_code is None or _immutable_external_path(exec_code.co_filename) is None:
            return False
        caller = frame.f_back
        return (
            caller is not None
            and caller.f_code is exec_code
            and caller.f_locals.get("self") is loader
            and caller.f_locals.get("module") is module
        )
    except BaseException:
        return False


def _is_pytest_assertion_rewrite_import(
    frame,
    code,
    relative,
    instruction_offset,
    name,
    fromlist,
    level,
):
    normalized_fromlist = () if fromlist is None else tuple(fromlist)
    if level != 0 or normalized_fromlist or name not in {
        "builtins",
        "_pytest.assertion.rewrite",
    }:
        return False
    return (
        (int(instruction_offset), name)
        in _pytest_rewrite_import_offsets(code, relative)
        and _pytest_rewrite_execution_is_authentic(frame, code, relative)
    )


def _record_executed_import(frame, name, globals_map, locals_map, fromlist, level):
    global _IMPORT_EDGE_COUNT
    relative, firstlineno, code_name, _names, _dynamic = _code_metadata(frame.f_code)
    if relative is None:
        return
    owner = _current_bucket()
    metadata = _import_instruction_metadata(frame.f_code, frame.f_lasti)
    if metadata is None or metadata[0] != "IMPORT_NAME":
        _mark_uncertainty("dynamic-or-unattributed-import-call", owner)
        return
    opname, instruction_name, lineno, end_lineno, col_offset, end_col_offset = metadata
    if not isinstance(fromlist, (tuple, type(None))):
        _mark_uncertainty("incomplete-import-edge-attribution:fromlist", owner)
        return
    if type(level) is not int or level < 0 or level > _MAX_IMPORT_TEXT:
        _mark_uncertainty("incomplete-import-edge-attribution:level", owner)
        return
    if not isinstance(name, str) or instruction_name != name:
        _mark_uncertainty("incomplete-import-edge-attribution:module", owner)
        return
    normalized_fromlist = () if fromlist is None else tuple(fromlist)
    if _is_pytest_assertion_rewrite_import(
        frame,
        frame.f_code,
        relative,
        frame.f_lasti,
        name,
        normalized_fromlist,
        level,
    ):
        # Pytest assertion rewriting injects one authenticated, paired import
        # sequence after a module docstring and any __future__ imports.  These
        # exact offsets have no source AST edge and both targets belong to the
        # pinned external pytest runtime.  All partial, forged, explicit, or
        # version-unknown shapes remain ordinary executed edges and fail closed.
        return
    if any(
        type(value) is not int
        for value in (lineno, end_lineno, col_offset, end_col_offset)
    ):
        if sys.version_info[:2] == (3, 10):
            span, source_parsed = _python310_import_span(
                relative,
                lineno,
                name,
                fromlist,
                level,
            )
        else:
            span, source_parsed = None, False
        if span is not None:
            lineno, end_lineno, col_offset, end_col_offset = span
        else:
            _mark_uncertainty("incomplete-import-edge-attribution:position", owner)
            return
    if (
        opname != "IMPORT_NAME"
        or (
            len(fromlist or ()) > _MAX_IMPORT_FROMLIST
            or not all(
                isinstance(value, str)
                and value
                and len(value) <= _MAX_IMPORT_TEXT
                and (value == "*" or value.isidentifier())
                for value in (fromlist or ())
            )
        )
        or len(name) > _MAX_IMPORT_TEXT
        or (name and not all(part.isidentifier() for part in name.split(".")))
        or (not name and level == 0)
        or lineno <= 0
        or end_lineno < lineno
        or col_offset < 0
        or end_col_offset < 0
    ):
        _mark_uncertainty("incomplete-import-edge-attribution", owner)
        return
    if owner not in _EXECUTED_IMPORTS and len(_EXECUTED_IMPORTS) >= _MAX_BUCKETS:
        _mark_uncertainty("import-edge-bucket-boundary-exceeded", owner)
        return
    rows = _EXECUTED_IMPORTS.setdefault(owner, set())
    edge = (
        relative,
        firstlineno,
        code_name,
        int(frame.f_lasti),
        lineno,
        end_lineno,
        col_offset,
        end_col_offset,
        name,
        normalized_fromlist,
        level,
    )
    if edge in rows:
        return
    if _IMPORT_EDGE_COUNT >= _MAX_IMPORT_EDGES:
        _mark_uncertainty("import-edge-boundary-exceeded", owner)
        return
    rows.add(edge)
    _IMPORT_EDGE_COUNT += 1


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    owner = _current_bucket()
    if getattr(_THREAD_LOCAL, "import_attribution_active", False):
        _mark_uncertainty("reentrant-import-attribution", owner)
        return _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    _THREAD_LOCAL.import_attribution_active = True
    try:
        try:
            try:
                caller = sys._getframe(1)
            except ValueError:
                caller = None
            if caller is not None:
                _record_executed_import(
                    caller,
                    name,
                    globals,
                    locals,
                    fromlist,
                    level,
                )
        except BaseException as exc:
            _mark_uncertainty(
                "incomplete-import-edge-attribution:exception:"
                + type(exc).__name__,
                owner,
            )
    finally:
        _THREAD_LOCAL.import_attribution_active = False
    # Delegate exactly once and preserve the original arguments, return value,
    # and exception semantics.  The wrapper exists only in qualification runs.
    return _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)


def _current_bucket():
    """Resolve a project call to the owning pytest node.

    The pytest/main thread uses the explicit protocol context.  A managed
    ``threading.Thread`` receives an immutable owner before it can begin.  Any
    other thread is session-attributed and therefore cannot accidentally lend
    a node reusable evidence.
    """
    try:
        ident = _ORIGINAL_GET_IDENT()
        if ident == _MAIN_THREAD_ID:
            return str(getattr(_ctx, "CURRENT_NODE", "__session__"))
        binding = getattr(_THREAD_LOCAL, "owner_binding", None)
        if (
            isinstance(binding, tuple)
            and len(binding) == 2
            and binding[0] == ident
            and isinstance(binding[1], str)
            and binding[1]
        ):
            return binding[1]
        with _THREAD_REGISTRY_LOCK:
            matches = []
            for record in _THREAD_RECORDS.values():
                thread = record.get("thread")
                try:
                    thread_ident = object.__getattribute__(thread, "_ident")
                except BaseException:
                    continue
                if thread_ident == ident:
                    matches.append(record)
            if matches:
                record = max(matches, key=lambda row: int(row.get("sequence", 0)))
                owner = record.get("owner")
                if isinstance(owner, str) and owner:
                    _THREAD_LOCAL.owner_binding = (ident, owner)
                    return owner
    except BaseException:
        pass
    return "__session__"


def _record_code(code, frame, bucket=None):
    global _SITE_COUNT
    global _SEEN_CODE_COUNT
    relative, firstlineno, name, candidate_names, dynamic = _code_metadata(code)
    if relative is None:
        return
    bucket = _current_bucket() if bucket is None else str(bucket)
    if bucket in _UNCERTAINTIES and any(
        not kind.startswith("ephemeral-filesystem-mutation:")
        for kind in _UNCERTAINTIES[bucket]
    ):
        # This bucket is already permanently fresh-required. Continuing to
        # trace it cannot recover reuse authority and can make one expensive
        # concurrent or subprocess test dominate qualification time.
        return
    seen_codes = _SEEN_CODE_BY_BUCKET.setdefault(bucket, {})
    code_id = id(code)
    if seen_codes.get(code_id) is code:
        return
    if len(seen_codes) >= _MAX_SITES or _SEEN_CODE_COUNT >= _MAX_SITES:
        _mark_uncertainty("profiler-code-identity-boundary-exceeded", bucket)
        return
    seen_codes[code_id] = code
    _SEEN_CODE_COUNT += 1
    globals_map = frame.f_globals
    names = tuple(name for name in candidate_names if name in globals_map)
    if bucket not in _BUCKETS and len(_BUCKETS) >= _MAX_BUCKETS:
        _mark_uncertainty("profiler-bucket-boundary-exceeded")
        return
    rows = _BUCKETS.setdefault(bucket, set())
    site = (relative, firstlineno, name, names, dynamic)
    if site not in rows:
        if _SITE_COUNT >= _MAX_SITES:
            _mark_uncertainty("profiler-call-site-boundary-exceeded")
            return
        rows.add(site)
        _SITE_COUNT += 1


def _profile(frame, event, arg):
    if event != "call":
        return
    if _ORIGINAL_GET_IDENT() == _MAIN_THREAD_ID:
        _record_code(frame.f_code, frame)
        return

    # ``threading.setprofile`` installs this bootstrap callback in each child.
    # Replace it immediately with an owner-closed, per-thread distinct-code
    # filter.  CPython still emits a profile event for every call, but the hot
    # path becomes one event comparison and one set lookup; metadata/closure
    # extraction runs only for each distinct project code object.
    owner = _current_bucket()
    local_seen = {}

    def child_profile(child_frame, child_event, child_arg):
        if child_event != "call":
            return
        if owner in _UNCERTAINTIES:
            # Once the node is irreversibly fresh-required, further child
            # tracing cannot restore authority and may be disabled safely.
            _set_current_profile(None)
            return
        code = child_frame.f_code
        code_id = id(code)
        if local_seen.get(code_id) is code:
            return
        if len(local_seen) >= _MAX_SITES:
            _mark_uncertainty("thread-profiler-code-boundary-exceeded", owner)
            _set_current_profile(None)
            return
        # Retain the exact code object while its integer identity is cached;
        # this prevents id reuse and avoids CodeType's filename-blind equality.
        local_seen[code_id] = code
        _record_code(code, child_frame, bucket=owner)

    _set_current_profile(child_profile)
    child_profile(frame, event, arg)


_CHILD_PROFILE_CODE = next(
    value
    for value in _profile.__code__.co_consts
    if isinstance(value, type(_profile.__code__)) and value.co_name == "child_profile"
)


def _set_current_profile(callback):
    _ORIGINAL_SYS_SETPROFILE(callback)


def _guarded_sys_setprofile(callback):
    try:
        caller = sys._getframe(1)
        expected = None if _MONITORING_ENABLED else _profile
        if (
            any(caller.f_code is code for code in _THREAD_PROFILE_BOOTSTRAP_CODES)
            and callback is expected
        ):
            return _set_current_profile(callback)
    except BaseException:
        pass
    owner = _current_bucket()
    _mark_uncertainty("thread-profile-hook-changed", owner)
    return _ORIGINAL_SYS_SETPROFILE(callback)


def _threading_setprofile(callback):
    owner = _current_bucket()
    _mark_uncertainty("threading-profile-configuration-changed", owner)
    if owner != "__session__":
        _mark_uncertainty("threading-profile-configuration-changed", "__session__")
    return _ORIGINAL_THREADING_SETPROFILE(callback)


def _threading_setprofile_all_threads(callback):
    owner = _current_bucket()
    _mark_uncertainty("threading-profile-configuration-changed", owner)
    if owner != "__session__":
        _mark_uncertainty("threading-profile-configuration-changed", "__session__")
    return _ORIGINAL_THREADING_SETPROFILE_ALL(callback)


def _mark_monitoring_configuration_change(api_name):
    owner = _current_bucket()
    reason = "monitoring-configuration-changed:" + str(api_name)
    _mark_uncertainty(reason, owner)
    if owner != "__session__":
        _mark_uncertainty(reason, "__session__")


def _monitoring_use_tool_id(tool_id, name):
    if tool_id == _MONITORING_TOOL_ID:
        _mark_monitoring_configuration_change("use_tool_id")
    return _ORIGINAL_MONITORING_USE_TOOL_ID(tool_id, name)


def _monitoring_free_tool_id(tool_id):
    if tool_id == _MONITORING_TOOL_ID:
        _mark_monitoring_configuration_change("free_tool_id")
    return _ORIGINAL_MONITORING_FREE_TOOL_ID(tool_id)


def _monitoring_clear_tool_id(tool_id):
    if tool_id == _MONITORING_TOOL_ID:
        _mark_monitoring_configuration_change("clear_tool_id")
    return _ORIGINAL_MONITORING_CLEAR_TOOL_ID(tool_id)


def _monitoring_set_events(tool_id, event_set):
    if tool_id == _MONITORING_TOOL_ID:
        _mark_monitoring_configuration_change("set_events")
    return _ORIGINAL_MONITORING_SET_EVENTS(tool_id, event_set)


def _monitoring_set_local_events(tool_id, code, event_set):
    if tool_id == _MONITORING_TOOL_ID:
        _mark_monitoring_configuration_change("set_local_events")
    return _ORIGINAL_MONITORING_SET_LOCAL_EVENTS(tool_id, code, event_set)


def _monitoring_register_callback(tool_id, event, callback):
    if tool_id == _MONITORING_TOOL_ID:
        _mark_monitoring_configuration_change("register_callback")
    return _ORIGINAL_MONITORING_REGISTER_CALLBACK(tool_id, event, callback)


def _monitoring_restart_events():
    _mark_monitoring_configuration_change("restart_events")
    return _ORIGINAL_MONITORING_RESTART_EVENTS()


def _monitor_project_execution(code, instruction_offset):
    """Attribute project starts/resumes while disabling immutable external code.

    ``sys.monitoring.DISABLE`` is deliberately returned only for a code object
    whose immutable filename is outside the reviewed project.  Project events
    stay enabled across pytest nodes, avoiding the global ``restart_events``
    operation which would otherwise re-arm every pytest/stdlib code location.
    Cached project metadata and the per-bucket seen-code set reject repeated
    starts/resumes before frame recovery.  The bucket remains part of the key:
    the same function or generator must still be attributed once to every node
    (and managed-thread owner) in which it executes.
    """
    owner = None
    try:
        code_id = id(code)
        cached = _CODE_META.get(code_id)
        if cached is not None and cached[0] is code:
            metadata = cached[1]
            if metadata[0] is None:
                return _MONITORING.DISABLE
            owner = _current_bucket()
            if _SEEN_CODE_BY_BUCKET.get(owner, {}).get(code_id) is code:
                return None
        else:
            filename = getattr(code, "co_filename", None)
            if not isinstance(filename, str):
                _mark_uncertainty("monitoring-code-classification-failed")
                return None
            if _project_relative(filename) is None:
                return _MONITORING.DISABLE
            owner = _current_bucket()
        if owner is None:
            _mark_uncertainty("monitoring-code-classification-failed")
            return None
    except BaseException:
        _mark_uncertainty("monitoring-code-classification-failed")
        return None
    try:
        frame = sys._getframe(1)
        if frame.f_code is not code:
            _mark_uncertainty("monitoring-frame-attribution-failed", owner)
        else:
            _record_code(code, frame, bucket=owner)
    except BaseException:
        _mark_uncertainty("monitoring-callback-failed", owner)
    return None


def mark_non_function_fixture(function, scope, argname):
    global _SITE_COUNT
    code = getattr(function, "__code__", None)
    if code is None:
        return
    relative, firstlineno, name, candidate_names, dynamic = _code_metadata(code)
    if relative is None:
        return
    globals_map = getattr(function, "__globals__", {})
    names = tuple(name for name in candidate_names if name in globals_map)
    site = (relative, firstlineno, name, names, dynamic)
    rows = _BUCKETS.setdefault("__session__", set())
    if site not in rows:
        if _SITE_COUNT >= _MAX_SITES:
            _mark_uncertainty("profiler-call-site-boundary-exceeded")
        else:
            rows.add(site)
            _SITE_COUNT += 1
    if len(_NON_FUNCTION_FIXTURES) >= _MAX_FIXTURES:
        _mark_uncertainty("profiler-fixture-boundary-exceeded")
    else:
        _NON_FUNCTION_FIXTURES.add((relative, firstlineno, name, scope, argname))


def _mark_uncertainty(kind, bucket=None):
    try:
        owner = _current_bucket() if bucket is None else str(bucket)
        _UNCERTAINTIES.setdefault(owner, set()).add(str(kind))
    except BaseException:
        pass


def _thread_start(self, *args, **kwargs):
    global _THREAD_SEQUENCE
    owner = _current_bucket()
    try:
        bootstrap = getattr(getattr(self, "_bootstrap", None), "__func__", None)
        bootstrap_inner = getattr(
            getattr(self, "_bootstrap_inner", None), "__func__", None
        )
        if (
            bootstrap is not _ORIGINAL_THREAD_BOOTSTRAP
            or bootstrap_inner is not _ORIGINAL_THREAD_BOOTSTRAP_INNER
        ):
            _mark_uncertainty("thread-bootstrap-replaced", owner)
        key = id(self)
        with _THREAD_REGISTRY_LOCK:
            if len(_THREAD_RECORDS) >= _MAX_THREADS and key not in _THREAD_RECORDS:
                _mark_uncertainty("thread-evidence-boundary-exceeded", owner)
            else:
                _THREAD_SEQUENCE += 1
                _THREAD_RECORDS[key] = {
                    "thread": self,
                    "owner": owner,
                    "sequence": _THREAD_SEQUENCE,
                    "joined": False,
                    "start_returned": False,
                    "start_failed": False,
                }
                if owner in _COMPLETED_NODEIDS:
                    _mark_uncertainty("thread-start-after-node-completion", owner)
                    _mark_uncertainty(
                        "thread-start-after-node-completion", "__session__"
                    )
    except BaseException:
        _mark_uncertainty("thread-owner-attribution-failed", owner)
        return _ORIGINAL_THREAD_START(self, *args, **kwargs)
    previous_managed_start_depth = getattr(
        _THREAD_LOCAL, "managed_start_depth", 0
    )
    try:
        _THREAD_LOCAL.managed_start_depth = previous_managed_start_depth + 1
        result = _ORIGINAL_THREAD_START(self, *args, **kwargs)
    except BaseException:
        with _THREAD_REGISTRY_LOCK:
            record = _THREAD_RECORDS.get(id(self))
            if record is not None:
                record["start_failed"] = True
        _mark_uncertainty("thread-start-failed", owner)
        raise
    finally:
        try:
            _THREAD_LOCAL.managed_start_depth = previous_managed_start_depth
        except BaseException:
            _mark_uncertainty("thread-start-context-restore-failed", owner)
    with _THREAD_REGISTRY_LOCK:
        record = _THREAD_RECORDS.get(id(self))
        if record is not None:
            record["start_returned"] = True
    return result


def _lowlevel_thread_start(function, args, kwargs=None):
    owner = _current_bucket()
    _mark_uncertainty("unmanaged-thread-start", owner)
    if owner != "__session__":
        _mark_uncertainty("unmanaged-thread-start", "__session__")
    if kwargs is None:
        return _ORIGINAL_LOWLEVEL_THREAD_START(function, args)
    return _ORIGINAL_LOWLEVEL_THREAD_START(function, args, kwargs)


def _thread_join(self, *args, **kwargs):
    result = _ORIGINAL_THREAD_JOIN(self, *args, **kwargs)
    try:
        terminated = not _ORIGINAL_THREAD_IS_ALIVE(self)
        with _THREAD_REGISTRY_LOCK:
            record = _THREAD_RECORDS.get(id(self))
            if record is not None and terminated:
                record["joined"] = True
    except BaseException:
        with _THREAD_REGISTRY_LOCK:
            record = _THREAD_RECORDS.get(id(self))
            owner = (
                record.get("owner")
                if record is not None and record.get("thread") is self
                else _current_bucket()
            )
        _mark_uncertainty("thread-join-attribution-failed", owner)
    return result


def _finalize_thread_bucket(bucket):
    try:
        with _THREAD_REGISTRY_LOCK:
            records = [
                record
                for record in _THREAD_RECORDS.values()
                if record.get("owner") == bucket
            ]
            started = len(records)
            joined = sum(bool(record.get("joined")) for record in records)
            start_failed = sum(bool(record.get("start_failed")) for record in records)
            alive = 0
            for record in records:
                try:
                    alive += int(bool(_ORIGINAL_THREAD_IS_ALIVE(record["thread"])))
                except BaseException:
                    alive += 1
                    _mark_uncertainty("thread-liveness-check-failed", bucket)
            if start_failed:
                _mark_uncertainty("thread-start-failed", bucket)
            if alive:
                _mark_uncertainty("thread-alive-at-node-completion", bucket)
                if bucket != "__session__":
                    _mark_uncertainty(
                        "thread-alive-across-node-boundary", "__session__"
                    )
            if joined != started:
                _mark_uncertainty("thread-not-joined-before-node-completion", bucket)
            if started:
                _THREAD_OBSERVATIONS[bucket] = {
                    "started": started,
                    "joined": joined,
                    "terminated": started - alive,
                    "start_failed": start_failed,
                    "complete_before_boundary": (
                        alive == 0 and joined == started and start_failed == 0
                    ),
                }
    except BaseException:
        _mark_uncertainty("thread-lifecycle-evidence-failed", bucket)


def _project_stack_initiated_observation():
    """Return true only for repository code observing repository state.

    Pytest and importlib necessarily read the checkout while collecting it;
    those reads are already covered by collection and source identities.  A
    project frame which asks builtins/pathlib/os to observe another repository
    path is different: without a reviewed data/directory dependency model that
    value could change while every recorded Python call stays identical.
    """
    try:
        frame = sys._getframe(1)
    except BaseException:
        return False
    crossed_import_loader = False
    for _ in range(64):
        frame = getattr(frame, "f_back", None)
        if frame is None:
            return False
        filename = str(getattr(getattr(frame, "f_code", None), "co_filename", ""))
        normalized = filename.replace("\\", "/")
        if normalized.startswith("<frozen importlib"):
            crossed_import_loader = True
        if _project_relative(filename) is not None:
            return not crossed_import_loader
    return False


def _observes_project_path(raw):
    if raw is None:
        raw = os.getcwd()
    if isinstance(raw, int):
        return False
    try:
        value = os.fsdecode(os.fspath(raw))
        candidate = os.path.abspath(value)
        root = os.path.abspath(_ROOT)
        if os.path.commonpath((root, candidate)) != root:
            return False
        relative = os.path.relpath(candidate, root).replace("\\", "/")
    except BaseException:
        return False
    return not (
        relative == ".zerorun-env"
        or relative.startswith(".zerorun-env/")
        or relative == ".zerorun"
        or relative.startswith(".zerorun/")
        or relative == ".git"
        or relative.startswith(".git/")
    )


def _path_is_within(parent, candidate):
    try:
        return os.path.normcase(os.path.commonpath((parent, candidate))) == os.path.normcase(parent)
    except BaseException:
        return False


def _classify_linux_file_descriptor(raw):
    """Resolve an open file descriptor through procfs with identity bracketing."""
    if (
        sys.platform != "linux"
        or type(raw) is not int
        or raw < 0
        or _ORIGINAL_GET_IDENT() != _MAIN_THREAD_ID
    ):
        return "unknown", "file-descriptor"
    try:
        with _THREAD_REGISTRY_LOCK:
            if any(
                _ORIGINAL_THREAD_IS_ALIVE(record["thread"])
                for record in _THREAD_RECORDS.values()
            ):
                return "unknown", "concurrent-file-descriptor"
    except BaseException:
        return "unknown", "unverifiable-file-descriptor-concurrency"
    proc_path = "/proc/self/fd/" + str(raw)
    try:
        before = _ORIGINAL_OS_FSTAT(raw)
        if not stat.S_ISREG(before.st_mode):
            return "unknown", "non-regular-file-descriptor"
        target = _ORIGINAL_OS_READLINK(proc_path)
        after = _ORIGINAL_OS_FSTAT(raw)
    except BaseException:
        return "unknown", "unresolved-file-descriptor"
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or stat.S_IFMT(before.st_mode) != stat.S_IFMT(after.st_mode)
        or not isinstance(target, str)
        or not target.startswith("/")
        or target.endswith(" (deleted)")
        or "\x00" in target
        or len(target) > _MAX_IMPORT_TEXT
    ):
        return "unknown", "unstable-file-descriptor"
    try:
        lexical = os.path.abspath(target)
        _THREAD_LOCAL.import_path_classification = (
            getattr(_THREAD_LOCAL, "import_path_classification", 0) + 1
        )
        try:
            resolved = os.path.realpath(lexical)
        finally:
            _THREAD_LOCAL.import_path_classification -= 1
    except BaseException:
        return "unknown", "unresolvable-file-descriptor"
    ephemeral = os.path.abspath(_EPHEMERAL_ROOT)
    if _path_is_within(ephemeral, lexical) and _path_is_within(ephemeral, resolved):
        rendered = os.path.relpath(lexical, ephemeral).replace("\\", "/")
        identity = "%s#%d:%d" % (rendered, before.st_dev, before.st_ino)
        if len(identity) > _MAX_IMPORT_TEXT:
            return "unknown", "oversized-file-descriptor-identity"
        return "ephemeral", identity
    root = os.path.abspath(_ROOT)
    if _path_is_within(root, lexical) or _path_is_within(root, resolved):
        return "project", "file-descriptor:" + target
    return "external", "file-descriptor:" + target


def _classify_mutation_path(raw, dir_fd=None):
    """Classify one mutation target without treating symlink escapes as safe."""
    if dir_fd not in (None, -1):
        return "unknown", "dir-fd"
    if isinstance(raw, int):
        return _classify_linux_file_descriptor(raw)
    if raw is None:
        return "unknown", "file-descriptor"
    try:
        value = os.fsdecode(os.fspath(raw))
        if not value or "\x00" in value or len(value) > _MAX_IMPORT_TEXT:
            return "unknown", "invalid-path"
        lexical = os.path.abspath(value)
        root = os.path.abspath(_ROOT)
        support = os.path.abspath(_SUPPORT_DIR)
        output = os.path.abspath(_OUT_DIR)
        ephemeral = os.path.abspath(_EPHEMERAL_ROOT)
        _THREAD_LOCAL.import_path_classification = (
            getattr(_THREAD_LOCAL, "import_path_classification", 0) + 1
        )
        try:
            resolved = os.path.realpath(lexical)
        finally:
            _THREAD_LOCAL.import_path_classification -= 1
    except BaseException:
        return "unknown", "unresolvable-path"
    if _path_is_within(support, lexical) or _path_is_within(output, lexical):
        return "control", lexical
    if _path_is_within(support, resolved) or _path_is_within(output, resolved):
        return "control", resolved
    if _path_is_within(root, lexical):
        return "project", os.path.relpath(lexical, root).replace("\\", "/")
    if _path_is_within(root, resolved):
        return "project", os.path.relpath(resolved, root).replace("\\", "/")
    if _path_is_within(ephemeral, lexical) and _path_is_within(ephemeral, resolved):
        return "ephemeral", os.path.relpath(lexical, ephemeral).replace("\\", "/")
    return "external", lexical.replace("\\", "/")


def _ephemeral_tree_snapshot():
    """Hash the bounded tmpfs content/type/mode view, excluding timestamps."""
    root = os.path.abspath(_EPHEMERAL_ROOT)
    rows = []
    pending = [(".", root)]
    total_bytes = 0
    try:
        while pending:
            relative, directory = pending.pop()
            details = _ORIGINAL_OS_LSTAT(directory)
            if not stat.S_ISDIR(details.st_mode):
                return None
            rows.append((relative, "directory", stat.S_IMODE(details.st_mode), ""))
            if len(rows) > _MAX_EPHEMERAL_SNAPSHOT_ENTRIES:
                return None
            with _ORIGINAL_OS_SCANDIR(directory) as iterator:
                entries = sorted(tuple(iterator), key=lambda entry: entry.name)
            for entry in reversed(entries):
                name = entry.name
                if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                    return None
                path = os.path.join(directory, name)
                child_relative = name if relative == "." else relative + "/" + name
                child = _ORIGINAL_OS_LSTAT(path)
                mode = stat.S_IMODE(child.st_mode)
                if stat.S_ISDIR(child.st_mode):
                    pending.append((child_relative, path))
                    continue
                if stat.S_ISLNK(child.st_mode):
                    target = _ORIGINAL_OS_READLINK(path)
                    rows.append((child_relative, "symlink", mode, str(target)))
                    continue
                if not stat.S_ISREG(child.st_mode):
                    return None
                digest = hashlib.sha256()
                with _ORIGINAL_OPEN(path, "rb") as handle:
                    before = _ORIGINAL_OS_FSTAT(handle.fileno())
                    if (
                        before.st_dev != child.st_dev
                        or before.st_ino != child.st_ino
                        or before.st_size != child.st_size
                        or stat.S_IFMT(before.st_mode) != stat.S_IFMT(child.st_mode)
                    ):
                        return None
                    while True:
                        block = handle.read(1024 * 1024)
                        if not block:
                            break
                        total_bytes += len(block)
                        if total_bytes > _MAX_EPHEMERAL_SNAPSHOT_BYTES:
                            return None
                        digest.update(block)
                    after = _ORIGINAL_OS_FSTAT(handle.fileno())
                if (
                    after.st_dev != before.st_dev
                    or after.st_ino != before.st_ino
                    or after.st_size != before.st_size
                    or after.st_mtime_ns != before.st_mtime_ns
                    or stat.S_IMODE(after.st_mode) != stat.S_IMODE(before.st_mode)
                ):
                    return None
                rows.append((child_relative, "file", mode, digest.hexdigest()))
        encoded = json.dumps(sorted(rows), separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except BaseException as exc:
        _THREAD_LOCAL.ephemeral_snapshot_error = type(exc).__name__
        return None


def _finalize_ephemeral_bucket(bucket):
    before = _EPHEMERAL_SNAPSHOTS_BEFORE.get(bucket)
    if before is None:
        return
    after = _ephemeral_tree_snapshot()
    if after is None or after != before:
        _mark_uncertainty("ephemeral-filesystem-state-not-restored", bucket)
        return
    kinds = _UNCERTAINTIES.get(bucket)
    if isinstance(kinds, set):
        kinds.difference_update(
            kind
            for kind in tuple(kinds)
            if kind.startswith("ephemeral-filesystem-mutation:")
        )
        if not kinds:
            _UNCERTAINTIES.pop(bucket, None)


def _record_filesystem_mutation(event, targets):
    bucket = _current_bucket()
    rows = _FILESYSTEM_MUTATIONS.setdefault(bucket, set())
    if sum(len(values) for values in _FILESYSTEM_MUTATIONS.values()) >= _MAX_FILESYSTEM_MUTATIONS:
        _mark_uncertainty("filesystem-mutation-boundary-exceeded", bucket)
        return
    scopes = set()
    for raw, dir_fd in targets:
        scope, rendered = _classify_mutation_path(raw, dir_fd)
        scopes.add(scope)
        rows.add((event, scope, rendered))
    if scopes == {"ephemeral"}:
        # This is recoverable only after a separate exact sibling reprofile
        # proves the rest of the collection does not require the side effect.
        if bucket not in _EPHEMERAL_SNAPSHOTS_BEFORE:
            before = _ephemeral_tree_snapshot()
            if before is None:
                failure = getattr(
                    _THREAD_LOCAL,
                    "ephemeral_snapshot_error",
                    "unknown-error",
                )
                _mark_uncertainty(
                    "ephemeral-filesystem-snapshot-failed:" + str(failure),
                    bucket,
                )
            else:
                _EPHEMERAL_SNAPSHOTS_BEFORE[bucket] = before
        _mark_uncertainty("ephemeral-filesystem-mutation:" + event, bucket)
    elif "project" in scopes:
        _mark_uncertainty("project-filesystem-mutation:" + event, bucket)
    elif "control" in scopes:
        _mark_uncertainty("profiler-control-filesystem-mutation:" + event, bucket)
    else:
        _mark_uncertainty("unscoped-filesystem-mutation:" + event, bucket)


def _mark_project_metadata_observation(operation, raw):
    if getattr(_THREAD_LOCAL, "import_path_classification", 0) > 0:
        return
    if (
        _project_stack_initiated_observation()
        and _observes_project_path(raw)
    ):
        _mark_uncertainty("project-metadata-observation:" + operation)


def _metadata_stat(path, *args, **kwargs):
    _mark_project_metadata_observation("stat", path)
    return _ORIGINAL_OS_STAT(path, *args, **kwargs)


def _metadata_lstat(path, *args, **kwargs):
    _mark_project_metadata_observation("lstat", path)
    return _ORIGINAL_OS_LSTAT(path, *args, **kwargs)


def _metadata_access(path, *args, **kwargs):
    _mark_project_metadata_observation("access", path)
    return _ORIGINAL_OS_ACCESS(path, *args, **kwargs)


def _metadata_path_exists(path):
    _mark_project_metadata_observation("exists", path)
    return _ORIGINAL_OS_PATH_EXISTS(path)


def _metadata_path_lexists(path):
    _mark_project_metadata_observation("lexists", path)
    return _ORIGINAL_OS_PATH_LEXISTS(path)


def _metadata_path_isfile(path):
    _mark_project_metadata_observation("isfile", path)
    return _ORIGINAL_OS_PATH_ISFILE(path)


def _metadata_path_isdir(path):
    _mark_project_metadata_observation("isdir", path)
    return _ORIGINAL_OS_PATH_ISDIR(path)


def _metadata_path_stat(path, *args, **kwargs):
    _mark_project_metadata_observation("stat", path)
    return _ORIGINAL_PATH_STAT(path, *args, **kwargs)


def _metadata_path_lstat(path, *args, **kwargs):
    _mark_project_metadata_observation("lstat", path)
    return _ORIGINAL_PATH_LSTAT(path, *args, **kwargs)


def _metadata_pathlib_exists(path, *args, **kwargs):
    _mark_project_metadata_observation("exists", path)
    return _ORIGINAL_PATH_EXISTS(path, *args, **kwargs)


def _metadata_pathlib_is_file(path, *args, **kwargs):
    _mark_project_metadata_observation("is_file", path)
    return _ORIGINAL_PATH_IS_FILE(path, *args, **kwargs)


def _metadata_pathlib_is_dir(path, *args, **kwargs):
    _mark_project_metadata_observation("is_dir", path)
    return _ORIGINAL_PATH_IS_DIR(path, *args, **kwargs)


def _import_object_identity(value):
    cls = value if isinstance(value, type) else type(value)
    return "%s.%s" % (
        str(getattr(cls, "__module__", "")),
        str(getattr(cls, "__qualname__", "")),
    )


def _immutable_external_path(value):
    if not isinstance(value, str) or not value or len(value) > _MAX_IMPORT_TEXT:
        return None
    if not os.path.isabs(value):
        return None
    normalized = os.path.normpath(value).replace("\\", "/")
    root = os.path.normpath(_ROOT).replace("\\", "/")
    support = os.path.normpath(_SUPPORT_DIR).replace("\\", "/")
    managed_dependency_root = root.rstrip("/") + "/.zerorun-env"
    if (
        normalized.casefold() == managed_dependency_root.casefold()
        or normalized.casefold().startswith(
            managed_dependency_root.casefold() + "/"
        )
    ):
        # The managed environment is an explicit recursive static input. Its
        # bytes (including pytest's assertion-rewrite implementation) are
        # source-reviewed and re-fingerprinted with every authority pass.
        return normalized
    if (
        normalized.casefold() == root.casefold()
        or normalized.casefold().startswith(root.casefold() + "/")
        or normalized.casefold() == support.casefold()
        or normalized.casefold().startswith(support.casefold() + "/")
    ):
        return None
    lowered = normalized.casefold()
    if any(
        lowered == prefix.casefold()
        or lowered.startswith(prefix.rstrip("/").casefold() + "/")
        for prefix in _INITIAL_SITE_PREFIXES
    ):
        # The exact interpreter-initial site roots are part of the controlled
        # startup environment. A later path merely named ``site-packages`` is
        # not accepted by this rule.
        return normalized
    prefixes = tuple(
        dict.fromkeys(
            os.path.normpath(prefix).replace("\\", "/")
            for prefix in (sys.prefix, sys.base_prefix, sys.exec_prefix, "/zerorun-env")
            if isinstance(prefix, str) and prefix
        )
    )
    if not any(
        normalized.casefold() == prefix.casefold()
        or normalized.casefold().startswith(prefix.rstrip("/").casefold() + "/")
        for prefix in prefixes
    ):
        return None
    return normalized


def _external_module_observations():
    """Return only successfully loaded modules backed by the frozen runtime."""
    rows = []
    module_type = type(sys)
    for name in sorted(_IMPORT_MODULES):
        if name == "__main__":
            continue
        module = sys.modules.get(name)
        if type(module) is not module_type:
            continue
        try:
            namespace = object.__getattribute__(module, "__dict__")
        except BaseException:
            _mark_import_environment_uncertain("module-namespace-unreadable:" + name)
            continue
        if not isinstance(namespace, dict):
            _mark_import_environment_uncertain("module-namespace-malformed:" + name)
            continue
        spec = namespace.get("__spec__")
        origin = None
        locations = None
        loader = None
        if spec is not None:
            try:
                origin = object.__getattribute__(spec, "origin")
                locations = object.__getattribute__(
                    spec, "submodule_search_locations"
                )
                loader = object.__getattribute__(spec, "loader")
            except BaseException:
                _mark_import_environment_uncertain("module-spec-unreadable:" + name)
                continue
        loader_identity = _import_object_identity(loader) if loader is not None else ""
        if origin == "built-in" and loader_identity == "_frozen_importlib.BuiltinImporter":
            rows.append((name, origin))
            continue
        if origin == "frozen" and loader_identity == "_frozen_importlib.FrozenImporter":
            rows.append((name, origin))
            continue
        normalized_origin = _immutable_external_path(origin)
        if normalized_origin is not None and loader_identity in {
            "_frozen_importlib_external.SourceFileLoader",
            "_frozen_importlib_external.ExtensionFileLoader",
            "_frozen_importlib_external.SourcelessFileLoader",
            "zipimport.zipimporter",
        }:
            if (
                normalized_origin.startswith("/zerorun-env/")
                or "/site-packages/" in normalized_origin
                or "/dist-packages/" in normalized_origin
            ):
                provenance = "dependency"
            else:
                provenance = "stdlib"
            rows.append((name, provenance))
            continue
        if (
            origin is None
            and locations is not None
            and loader_identity
            in {
                "_frozen_importlib_external.NamespaceLoader",
                "_frozen_importlib_external._NamespacePath",
            }
        ):
            try:
                normalized_locations = tuple(
                    _immutable_external_path(location) for location in locations
                )
            except BaseException:
                _mark_import_environment_uncertain("namespace-spec-unreadable:" + name)
                continue
            if normalized_locations and all(normalized_locations):
                rows.append((name, "namespace"))
                continue
        # Workspace and profiler-support modules are covered by the project
        # closure. Any other origin implies unsupported import machinery.
        project_origin = False
        if isinstance(origin, str):
            normalized = os.path.normpath(origin).replace("\\", "/")
            root = os.path.normpath(_ROOT).replace("\\", "/")
            support = os.path.normpath(_SUPPORT_DIR).replace("\\", "/")
            project_origin = (
                normalized == root
                or normalized.startswith(root + "/")
                or normalized == support
                or normalized.startswith(support + "/")
            )
        if not project_origin:
            _mark_import_environment_uncertain("unsupported-module-origin:" + name)
    return rows


def _stable_external_toplevels():
    """Inventory frozen-runtime and managed-dependency top-level names.

    This is intentionally not a general ``sys.path`` scan. The stdlib name set
    belongs to the pinned interpreter, while dependency directories must be an
    interpreter-initial site root or the recursively source-reviewed managed
    ``.zerorun-env`` tree. Arbitrary paths added by project code remain an
    import-environment uncertainty.
    """
    rows = set()
    stdlib_names = getattr(sys, "stdlib_module_names", ())
    if not isinstance(stdlib_names, (set, frozenset)):
        _mark_import_environment_uncertain("stdlib-name-inventory-unavailable")
    else:
        for name in stdlib_names:
            if (
                isinstance(name, str)
                and name
                and len(name) <= _MAX_IMPORT_TEXT
                and name.isidentifier()
            ):
                rows.add((name, "stdlib-name"))

    root = os.path.normpath(_ROOT).replace("\\", "/")
    managed_site = root.rstrip("/") + "/.zerorun-env/site-packages"
    allowed_sites = {
        managed_site.casefold(): managed_site,
        **{prefix.casefold(): prefix for prefix in _INITIAL_SITE_PREFIXES},
    }
    for raw in tuple(sys.path):
        if not isinstance(raw, str) or not raw or not os.path.isabs(raw):
            continue
        normalized = os.path.normpath(raw).replace("\\", "/")
        canonical = allowed_sites.get(normalized.casefold())
        if canonical is None:
            continue
        try:
            entries = _ORIGINAL_OS_LISTDIR(raw)
        except BaseException:
            _mark_import_environment_uncertain("dependency-inventory-unreadable")
            continue
        if not isinstance(entries, list) or len(entries) > _MAX_IMPORT_MODULES:
            _mark_import_environment_uncertain("dependency-inventory-boundary-exceeded")
            continue
        for entry in entries:
            if not isinstance(entry, str) or not entry or len(entry) > _MAX_IMPORT_TEXT:
                _mark_import_environment_uncertain("dependency-inventory-entry-invalid")
                continue
            candidate = os.path.join(raw, entry)
            name = None
            try:
                if _ORIGINAL_OS_PATH_ISDIR(candidate):
                    name = entry
                elif _ORIGINAL_OS_PATH_ISFILE(candidate):
                    if entry.endswith(".py"):
                        name = entry[:-3]
                    elif entry.endswith((".so", ".pyd")):
                        name = entry.split(".", 1)[0]
            except BaseException:
                _mark_import_environment_uncertain("dependency-inventory-entry-unreadable")
                continue
            if isinstance(name, str) and name.isidentifier():
                rows.add((name, "dependency-inventory"))
    return sorted(rows)


def _mark_import_environment_uncertain(reason):
    _mark_uncertainty("import-environment:" + reason, "__session__")


def _record_import_search_path(raw):
    if not isinstance(raw, (list, tuple)):
        _mark_import_environment_uncertain("search-path-is-not-a-sequence")
        return
    root = os.path.normpath(_ROOT).replace("\\", "/")
    support = os.path.normpath(_SUPPORT_DIR).replace("\\", "/")
    immutable_prefixes = tuple(
        dict.fromkeys(
            os.path.normpath(value).replace("\\", "/")
            for value in (
                sys.prefix,
                sys.base_prefix,
                sys.exec_prefix,
                support,
                "/zerorun-env",
            )
            if isinstance(value, str) and value
        )
    )
    initial_site_prefixes = tuple(
        prefix.casefold().rstrip("/") for prefix in _INITIAL_SITE_PREFIXES
    )
    for value in raw:
        if not isinstance(value, str) or len(value) > _MAX_IMPORT_TEXT:
            _mark_import_environment_uncertain("invalid-search-path-entry")
            continue
        normalized = (
            root
            if value in {"", "."}
            else os.path.normpath(value).replace("\\", "/")
        )
        if normalized.casefold() == support.casefold() or normalized.casefold().startswith(
            support.casefold().rstrip("/") + "/"
        ):
            continue
        if normalized == root or normalized.startswith(root + "/"):
            relative = os.path.relpath(normalized, root).replace("\\", "/")
            if relative == ".zerorun-env" or relative.startswith(
                ".zerorun-env/"
            ):
                # The frozen dependency tree is already a shared static input;
                # it is not a project shadow root.
                continue
            if relative in {".git", ".zerorun"} or relative.startswith(
                (".git/", ".zerorun/")
            ):
                _mark_import_environment_uncertain("mutable-state-search-path")
                continue
            if (
                relative.startswith("../")
                or relative.startswith("/")
                or "\\" in relative
            ):
                _mark_import_environment_uncertain("invalid-project-search-root")
                continue
            if len(_IMPORT_PROJECT_SEARCH_ROOTS) >= _MAX_IMPORT_SEARCH_ROOTS:
                _mark_import_environment_uncertain("search-root-boundary-exceeded")
                continue
            _IMPORT_PROJECT_SEARCH_ROOTS.add(relative)
            continue
        if os.path.isabs(value) and any(
            normalized.casefold() == prefix.casefold()
            or normalized.casefold().startswith(prefix.casefold() + "/")
            for prefix in immutable_prefixes
        ):
            continue
        lowered = normalized.casefold()
        if os.path.isabs(value) and any(
            lowered == prefix or lowered.startswith(prefix + "/")
            for prefix in initial_site_prefixes
        ):
            continue
        _mark_import_environment_uncertain("unsupported-search-path:" + normalized)


def _record_import_machinery(meta_path, path_hooks):
    if not isinstance(meta_path, (list, tuple)) or not isinstance(
        path_hooks, (list, tuple)
    ):
        _mark_import_environment_uncertain("malformed-import-machinery")
        return
    for finder in meta_path:
        if any(finder is original for original in _INITIAL_META_PATH):
            continue
        if _import_object_identity(finder) == "_pytest.assertion.rewrite.AssertionRewritingHook":
            module = sys.modules.get("_pytest.assertion.rewrite")
            if type(module) is type(sys):
                try:
                    namespace = object.__getattribute__(module, "__dict__")
                    expected = namespace.get("AssertionRewritingHook")
                    spec = namespace.get("__spec__")
                    origin = object.__getattribute__(spec, "origin")
                except BaseException:
                    expected = None
                    origin = None
                if type(finder) is expected and _immutable_external_path(origin):
                    continue
        _mark_import_environment_uncertain(
            "unsupported-meta-path-finder:" + _import_object_identity(finder)
        )
    if len(path_hooks) != len(_INITIAL_PATH_HOOKS) or any(
        current is not original
        for current, original in zip(path_hooks, _INITIAL_PATH_HOOKS)
    ):
        _mark_import_environment_uncertain("path-hooks-changed")


def _reject_preexisting_project_modules():
    observed = 0
    for module in tuple(sys.modules.values()):
        if type(module) is not type(sys):
            continue
        try:
            namespace = object.__getattribute__(module, "__dict__")
            origin = namespace.get("__file__")
            if not isinstance(origin, str):
                spec = namespace.get("__spec__")
                origin = object.__getattribute__(spec, "origin")
        except BaseException:
            continue
        if _project_relative(origin) is None:
            continue
        observed += 1
        if observed > _MAX_IMPORT_MODULES:
            _mark_uncertainty("preexisting-project-module-boundary-exceeded", "__session__")
            return
    if observed:
        _mark_uncertainty("project-module-loaded-before-import-profiler", "__session__")


def _record_import_observation(args):
    if not isinstance(args, tuple) or len(args) < 5:
        _mark_import_environment_uncertain("malformed-audit-event")
        return
    name = args[0]
    if (
        not isinstance(name, str)
        or not name
        or len(name) > _MAX_IMPORT_TEXT
        or not all(part.isidentifier() for part in name.split("."))
    ):
        _mark_import_environment_uncertain("invalid-module-name")
    elif name not in _IMPORT_MODULES:
        if len(_IMPORT_MODULES) >= _MAX_IMPORT_MODULES:
            _mark_import_environment_uncertain("module-boundary-exceeded")
        else:
            _IMPORT_MODULES.add(name)
    # CPython's second import audit phase reports the resolved filename and
    # ``None`` for search/machinery fields.  The first phase is the one that
    # attests the actual lookup environment.
    if args[2] is not None or args[3] is not None or args[4] is not None:
        _record_import_search_path(args[2])
        _record_import_machinery(args[3], args[4])


def _internal_source_span_observation():
    """Recognize only the profiler's exact internal source-read stacks."""
    try:
        frame = sys._getframe(1).f_back
        chains = (
            (
                _source_import_spans.__code__,
                _python310_import_span.__code__,
                _record_executed_import.__code__,
                _guarded_import.__code__,
            ),
            (
                _source_import_spans.__code__,
                _source_rewrite_insertion_line.__code__,
                _pytest_rewrite_import_offsets.__code__,
                _is_pytest_assertion_rewrite_import.__code__,
                _record_executed_import.__code__,
                _guarded_import.__code__,
            ),
        )
        for chain in chains:
            cursor = frame
            for expected in chain:
                if cursor is None or cursor.f_code is not expected:
                    break
                cursor = cursor.f_back
            else:
                return (
                    cursor is not None
                    and _project_relative(cursor.f_code.co_filename) is not None
                )
        return False
    except BaseException:
        return False


def _audit(event, args):
    if event == "import":
        _record_import_observation(args)
        return
    if event == "open" and _internal_source_span_observation():
        # This read reconstructs missing Python 3.10 bytecode positions.  It
        # can neither expose file bytes to project code nor be authorized by a
        # spoofable flag: the complete profiler call chain is identity-checked.
        return
    if event == "sys.setprofile":
        try:
            caller = sys._getframe(1)
            invoker = caller.f_back
            if (
                caller.f_code is _set_current_profile.__code__
                and invoker is not None
                and any(
                    invoker.f_code is code
                    for code in (
                        _profile.__code__,
                        _CHILD_PROFILE_CODE,
                        _guarded_sys_setprofile.__code__,
                    )
                )
            ):
                return
            if any(
                caller.f_code is code for code in _THREAD_PROFILE_BOOTSTRAP_CODES
            ):
                return
        except BaseException:
            pass
        owner = _current_bucket()
        _mark_uncertainty("thread-profile-hook-changed", owner)
        return
    if event in {"_thread.start_new_thread", "_thread.start_joinable_thread"}:
        # ``threading.Thread.start`` is owned by the wrapper above.  A raw
        # low-level thread has no stable node owner/lifecycle object, so its
        # project calls become session-attributed and reuse fails closed.
        try:
            managed_start = (
                getattr(_THREAD_LOCAL, "managed_start_depth", 0) > 0
                and sys._getframe(1).f_code is _ORIGINAL_THREAD_START.__code__
            )
        except BaseException:
            managed_start = False
        if not managed_start:
            owner = _current_bucket()
            _mark_uncertainty("unmanaged-thread-start", owner)
            if owner != "__session__":
                _mark_uncertainty("unmanaged-thread-start", "__session__")
        return
    if event in {
        "subprocess.Popen",
        "os.system",
        "os.fork",
        "os.forkpty",
        "pty.spawn",
        "os.posix_spawn",
    } or event.startswith("os.exec") or event.startswith("os.spawn"):
        _mark_uncertainty(event)
        return
    project_mutation_events = {
        "os.chmod",
        "os.chown",
        "os.link",
        "os.mkdir",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.symlink",
        "os.truncate",
        "os.utime",
    }
    if event in project_mutation_events:
        if _project_stack_initiated_observation():
            if event in {"os.rename", "os.link"}:
                targets = (
                    (args[0] if len(args) > 0 else None, args[2] if len(args) > 2 else None),
                    (args[1] if len(args) > 1 else None, args[3] if len(args) > 3 else None),
                )
            elif event == "os.symlink":
                targets = (
                    (args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None),
                )
            else:
                dir_fd_index = {
                    "os.chmod": 2,
                    "os.chown": 3,
                    "os.mkdir": 2,
                    "os.remove": 1,
                    "os.rmdir": 1,
                    "os.utime": 3,
                }.get(event)
                targets = (
                    (
                        args[0] if args else None,
                        (
                            args[dir_fd_index]
                            if dir_fd_index is not None and len(args) > dir_fd_index
                            else None
                        ),
                    ),
                )
            _record_filesystem_mutation(event, targets)
        return
    if event == "open" and _project_stack_initiated_observation():
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        writing_mode = isinstance(mode, str) and any(
            marker in mode for marker in ("w", "a", "x", "+")
        )
        writing_flags = isinstance(flags, int) and bool(
            flags
            & (
                getattr(os, "O_WRONLY", 0)
                | getattr(os, "O_RDWR", 0)
                | getattr(os, "O_CREAT", 0)
                | getattr(os, "O_TRUNC", 0)
                | getattr(os, "O_APPEND", 0)
            )
        )
        if writing_mode or writing_flags:
            _record_filesystem_mutation(
                "open",
                ((args[0] if args else None, None),),
            )
            return
    project_observation_events = {
        "open",
        "os.listdir",
        "os.scandir",
        "os.walk",
        "glob.glob",
        "glob.glob/2",
    }
    if event not in project_observation_events:
        return
    if not _project_stack_initiated_observation():
        return
    if event == "open" and args and _observes_project_path(args[0]):
        _mark_uncertainty("project-file-observation:open")
    elif event in {"os.listdir", "os.scandir", "os.walk", "glob.glob", "glob.glob/2"}:
        observed = args[0] if args else None
        if _observes_project_path(observed):
            _mark_uncertainty("project-directory-observation:" + event)


def _dump():
    try:
        assert_profile_intact("atexit")
        _finalize_thread_bucket("__session__")
        _finalize_ephemeral_bucket("__session__")
        _record_import_search_path(tuple(sys.path))
        _record_import_machinery(tuple(sys.meta_path), tuple(sys.path_hooks))
        external_modules = _external_module_observations()
        stable_external_toplevels = _stable_external_toplevels()
        payload = {
            "loaded": True,
            "pid": os.getpid(),
            "pytest_plugin_loaded": _PYTEST_PLUGIN_LOADED,
            "integrity_violations": list(_INTEGRITY_VIOLATIONS),
            "collection_nodeids": list(_COLLECTION_NODEIDS or ()),
            "selected_nodeids": list(_SELECTED_NODEIDS or ()),
            "started_nodeids": sorted(_STARTED_NODEIDS),
            "completed_nodeids": sorted(_COMPLETED_NODEIDS),
            "uncertainties": {
                key: sorted(values) for key, values in sorted(_UNCERTAINTIES.items())
            },
            "thread_observations": {
                key: value for key, value in sorted(_THREAD_OBSERVATIONS.items())
            },
            "non_function_fixtures": [
                list(row) for row in sorted(_NON_FUNCTION_FIXTURES)
            ],
            "filesystem_mutations": {
                key: [list(row) for row in sorted(rows)]
                for key, rows in sorted(_FILESYSTEM_MUTATIONS.items())
            },
            "import_observations": {
                "external_modules": [list(row) for row in external_modules],
                "stable_external_toplevels": [
                    list(row) for row in stable_external_toplevels
                ],
                "project_search_roots": sorted(_IMPORT_PROJECT_SEARCH_ROOTS),
            },
            "executed_imports": {
                key: [
                    [*row[:9], list(row[9]), row[10]]
                    for row in sorted(rows)
                ]
                for key, rows in sorted(_EXECUTED_IMPORTS.items())
            },
            "buckets": {
                key: [list(row) for row in sorted(rows)]
                for key, rows in sorted(_BUCKETS.items())
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_EVIDENCE_BYTES:
            payload = {
                "loaded": True,
                "pid": os.getpid(),
                "pytest_plugin_loaded": _PYTEST_PLUGIN_LOADED,
                "integrity_violations": list(_INTEGRITY_VIOLATIONS),
                "collection_nodeids": [],
                "selected_nodeids": [],
                "started_nodeids": [],
                "completed_nodeids": [],
                "uncertainties": {
                    "__session__": ["profiler-evidence-byte-boundary-exceeded"]
                },
                "thread_observations": {},
                "non_function_fixtures": [],
                "filesystem_mutations": {},
                "import_observations": {
                    "external_modules": [],
                    "stable_external_toplevels": [],
                    "project_search_roots": [],
                },
                "executed_imports": {},
                "buckets": {},
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        output = os.path.join(_OUT_DIR, "profile-%s.json" % os.getpid())
        with open(output, "wb") as handle:
            handle.write(encoded)
    except BaseException:
        pass


if _MONITORING is not None:
    monitoring_acquired = False
    try:
        _MONITORING_TOOL_ID = _MONITORING.PROFILER_ID
        start_event = getattr(_MONITORING.events, "PY_START", None)
        resume_event = getattr(_MONITORING.events, "PY_RESUME", None)
        if (
            type(start_event) is not int
            or type(resume_event) is not int
            or start_event <= 0
            or resume_event <= 0
            or start_event == resume_event
        ):
            raise RuntimeError("monitoring start/resume events are unavailable")
        _MONITORING_EVENTS = start_event | resume_event
        _MONITORING_CALLBACKS = (
            (start_event, _monitor_project_execution),
            # A generator/coroutine can start in one pytest node and resume in
            # another. PY_START alone cannot attribute the later execution.
            (resume_event, _monitor_project_execution),
        )
        _ORIGINAL_MONITORING_USE_TOOL_ID(_MONITORING_TOOL_ID, _MONITORING_NAME)
        monitoring_acquired = True
        for event, callback in _MONITORING_CALLBACKS:
            previous_callback = _ORIGINAL_MONITORING_REGISTER_CALLBACK(
                _MONITORING_TOOL_ID,
                event,
                callback,
            )
            if previous_callback is not None:
                raise RuntimeError("monitoring tool unexpectedly had a callback")
        _ORIGINAL_MONITORING_SET_EVENTS(_MONITORING_TOOL_ID, _MONITORING_EVENTS)
        _MONITORING_ENABLED = True
    except BaseException:
        if monitoring_acquired:
            try:
                _ORIGINAL_MONITORING_SET_EVENTS(_MONITORING_TOOL_ID, 0)
                for event, _ in _MONITORING_CALLBACKS:
                    _ORIGINAL_MONITORING_REGISTER_CALLBACK(
                        _MONITORING_TOOL_ID,
                        event,
                        None,
                    )
                _ORIGINAL_MONITORING_FREE_TOOL_ID(_MONITORING_TOOL_ID)
            except BaseException:
                pass
        _MONITORING_ENABLED = False

threading.Thread.start = _thread_start
threading.Thread.join = _thread_join
os.stat = _metadata_stat
os.lstat = _metadata_lstat
os.access = _metadata_access
os.path.exists = _metadata_path_exists
os.path.lexists = _metadata_path_lexists
os.path.isfile = _metadata_path_isfile
os.path.isdir = _metadata_path_isdir
pathlib.Path.stat = _metadata_path_stat
pathlib.Path.lstat = _metadata_path_lstat
pathlib.Path.exists = _metadata_pathlib_exists
pathlib.Path.is_file = _metadata_pathlib_is_file
pathlib.Path.is_dir = _metadata_pathlib_is_dir
if _OS_BACKEND is not None:
    if _ORIGINAL_BACKEND_STAT is not None:
        _OS_BACKEND.stat = _metadata_stat
    if _ORIGINAL_BACKEND_LSTAT is not None:
        _OS_BACKEND.lstat = _metadata_lstat
    if _ORIGINAL_BACKEND_ACCESS is not None:
        _OS_BACKEND.access = _metadata_access
sys.setprofile = _guarded_sys_setprofile
threading.setprofile = _threading_setprofile
if _ORIGINAL_THREADING_SETPROFILE_ALL is not None:
    threading.setprofile_all_threads = _threading_setprofile_all_threads
_thread.start_new_thread = _lowlevel_thread_start
if _ORIGINAL_LOWLEVEL_THREAD_START_ALIAS is not None:
    # CPython 3.10 exposes ``start_new`` as a second module attribute pointing
    # at the original builtin.  Rebinding only ``start_new_thread`` leaves that
    # retained alias able to bypass the fail-closed wrapper.
    _thread.start_new = _lowlevel_thread_start
if _MONITORING_ENABLED:
    _MONITORING.use_tool_id = _monitoring_use_tool_id
    _MONITORING.free_tool_id = _monitoring_free_tool_id
    _MONITORING.clear_tool_id = _monitoring_clear_tool_id
    _MONITORING.set_events = _monitoring_set_events
    _MONITORING.set_local_events = _monitoring_set_local_events
    _MONITORING.register_callback = _monitoring_register_callback
    _MONITORING.restart_events = _monitoring_restart_events
    _ORIGINAL_THREADING_SETPROFILE(None)
else:
    # Conservative compatibility path for Python <=3.11 (and for an occupied
    # monitoring tool slot).  ``_record_code`` performs a constant-time seen-
    # code rejection before metadata work, retaining normal fine-grained child
    # call attribution without repeating closure extraction millions of times.
    _ORIGINAL_THREADING_SETPROFILE(_profile)
    _ORIGINAL_SYS_SETPROFILE(_profile)
_record_import_search_path(tuple(sys.path))
sys.addaudithook(_audit)
_reject_preexisting_project_modules()
builtins.__import__ = _guarded_import
atexit.register(_dump)
'''


def profiler_sha256() -> str:
    payload = (_CONTEXT_SOURCE + _PLUGIN_SOURCE + _SITE_SOURCE).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _static_inputs(root: Path, targets: tuple[str, ...]) -> tuple[str, ...]:
    return discover_pytest_static_inputs(root, targets=targets)


class _StaticImportUncertain(Exception):
    pass


class _StaticImportFallbackSession:
    """Share immutable static-import observations inside one qualification.

    This is deliberately request-local.  A qualification may project hundreds
    of collected nodes from the same source files; resolving every dotted
    import's complete project-shadow surface for every node is both redundant
    and extremely expensive.  Existing source bytes and local-import surfaces
    are safe to share only while building one candidate because all observed
    source bytes and negative candidates are independently revalidated before
    the candidate is written.  A later qualification always creates a new
    session.
    """

    def __init__(
        self,
        root: Path,
        *,
        closure_analysis_cache: SymbolClosureAnalysisCache | None = None,
    ) -> None:
        self.root = root.resolve(strict=True)
        if closure_analysis_cache is None:
            closure_analysis_cache = SymbolClosureAnalysisCache(self.root)
        else:
            closure_analysis_cache.validate_root(self.root)
        self.closure_analysis_cache = closure_analysis_cache
        self.source_bytes: dict[str, bytes] = {}
        self.source_bytes_total = 0
        self.direct_analyses: dict[
            tuple[str, str, str, tuple[str, ...]],
            tuple[tuple[str, ...], tuple[str, ...], str | None],
        ] = {}

    def validate_root(self, root: Path) -> None:
        resolved = root if root == self.root else root.resolve(strict=True)
        if resolved != self.root:
            raise ConfigurationError(
                "static import analysis session cannot span project roots"
            )

    def read_source(self, path: Path, *, relative: str) -> str:
        cached = self.source_bytes.get(relative)
        if cached is None:
            try:
                cached = read_stable_file_bytes(path)
            except (OSError, ConfigurationError) as exc:
                raise _StaticImportUncertain(
                    f"could not statically inspect {relative}: {exc}"
                ) from exc
            if (
                len(self.source_bytes) >= _MAX_PROFILE_IMPORT_MODULES
                or self.source_bytes_total + len(cached) > _MAX_TOTAL_PROFILE_BYTES
            ):
                raise _StaticImportUncertain(
                    "static import source snapshot exceeds its structural boundary"
                )
            self.source_bytes[relative] = cached
            self.source_bytes_total += len(cached)
        try:
            return cached.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _StaticImportUncertain(
                f"could not statically inspect {relative}: {exc}"
            ) from exc

    def import_surface(
        self,
        module: str,
        *,
        search_roots: tuple[str, ...],
    ) -> LocalImportSurface:
        return self.closure_analysis_cache.import_surface(
            module,
            search_roots=search_roots,
        )

    def revalidate(self) -> None:
        """Prove that every cached source still has the exact analyzed bytes."""

        for relative, expected in self.source_bytes.items():
            candidate = self.root / relative
            try:
                _reject_symlink_chain(self.root, candidate)
                current = read_stable_file_bytes(candidate)
            except (OSError, ConfigurationError, _StaticImportUncertain) as exc:
                raise ConfigurationError(
                    f"static import source changed during qualification: {relative}"
                ) from exc
            if current != expected:
                raise ConfigurationError(
                    f"static import source changed during qualification: {relative}"
                )


def _reject_symlink_chain(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _StaticImportUncertain("test/import source escapes project root") from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if is_link_like(cursor):
            raise _StaticImportUncertain(
                f"test/import source resolves through a link or reparse point: {cursor}"
            )


def _collection_target_qualname(
    relative: str,
    item: dict[str, Any],
) -> tuple[str, ...]:
    """Resolve an ordinary pytest Function row to one lexical AST target."""

    nodeid = item.get("nodeid")
    item_name = item.get("name")
    if (
        item.get("item_type") != "_pytest.python.Function"
        or not isinstance(nodeid, str)
        or not isinstance(item_name, str)
        or not item_name
    ):
        raise _StaticImportUncertain(
            "collection item is not an ordinary Python test function"
        )
    prefix = relative + "::"
    leaf_suffix = "::" + item_name
    if not nodeid.startswith(prefix):
        raise _StaticImportUncertain(
            "collection node id does not match its reported source path"
        )
    remainder = nodeid[len(prefix) :]
    if remainder == item_name:
        raw_ancestors: tuple[str, ...] = ()
    elif remainder.endswith(leaf_suffix):
        raw_ancestors = tuple(remainder[: -len(leaf_suffix)].split("::"))
    else:
        raise _StaticImportUncertain(
            "collection node id does not identify its reported item name"
        )

    def base_name(value: str) -> str:
        # Pytest appends the canonical parameter id to Function/Class names.
        # Python identifiers themselves cannot contain ``[``, so this is
        # unambiguous only when the suffix is a complete bracketed id.
        if "[" not in value:
            return value
        base, marker, suffix = value.partition("[")
        if not marker or not base or not suffix.endswith("]"):
            raise _StaticImportUncertain(
                "collection parameterized name is not lexically reviewable"
            )
        return base

    result = tuple(base_name(value) for value in (*raw_ancestors, item_name))
    if not result or not all(value.isidentifier() for value in result):
        raise _StaticImportUncertain(
            "collection node id does not map to Python identifiers"
        )
    return result


class _ExecutionSurfacePruner(ast.NodeTransformer):
    """Keep import-time setup plus one exact collected function body.

    Function bodies are dormant at module/class definition time.  Their
    decorators, defaults and annotations are not, so those header expressions
    remain in the projected tree.  Class bodies execute during import and are
    therefore always traversed.  A uniquely selected test body is retained;
    nested function bodies remain dormant unless separately observed by the
    dynamic profiler.
    """

    def __init__(self, target: tuple[str, ...] | None) -> None:
        self.target = target
        self.scope: tuple[str, ...] = ()
        self.found = 0
        self.selected_body_nodes: set[int] = set()

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef:
        qualified = (*self.scope, node.name)
        node.decorator_list = [self.visit(value) for value in node.decorator_list]
        node.args = self.visit(node.args)
        if node.returns is not None:
            node.returns = self.visit(node.returns)
        if hasattr(node, "type_params"):
            node.type_params = [self.visit(value) for value in node.type_params]
        if qualified != self.target:
            node.body = []
            return node
        self.found += 1
        previous = self.scope
        self.scope = qualified
        try:
            node.body = [self.visit(value) for value in node.body]
        finally:
            self.scope = previous
        for statement in node.body:
            self.selected_body_nodes.update(id(value) for value in ast.walk(statement))
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        node.decorator_list = [self.visit(value) for value in node.decorator_list]
        node.bases = [self.visit(value) for value in node.bases]
        node.keywords = [self.visit(value) for value in node.keywords]
        if hasattr(node, "type_params"):
            node.type_params = [self.visit(value) for value in node.type_params]
        previous = self.scope
        self.scope = (*self.scope, node.name)
        try:
            node.body = [self.visit(value) for value in node.body]
        finally:
            self.scope = previous
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        # Lambda defaults execute when the lambda is created; its body does not.
        node.args = self.visit(node.args)
        return node


def _project_static_execution_surface(
    tree: ast.Module,
    *,
    relative: str,
    item: dict[str, Any] | None,
) -> tuple[ast.Module, set[int]]:
    if item is None:
        target = None
    else:
        target = _collection_target_qualname(relative, item)
    pruner = _ExecutionSurfacePruner(target)
    projected = pruner.visit(tree)
    if not isinstance(projected, ast.Module):
        raise _StaticImportUncertain("test source projection is malformed")
    if target is not None and pruner.found != 1:
        raise _StaticImportUncertain(
            "collection node does not map to exactly one lexical test function"
        )
    return projected, pruner.selected_body_nodes


_STATIC_OPERATION_EVENTS = {
    "open": "open",
    "chmod": "os.chmod",
    "mkdir": "os.mkdir",
    "remove": "os.remove",
    "rename": "os.rename",
    "replace": "os.rename",
    "rmdir": "os.rmdir",
    "symlink": "os.symlink",
    "truncate": "os.truncate",
    "unlink": "os.remove",
    "utime": "os.utime",
}


def _direct_static_import_analysis(
    session: _StaticImportFallbackSession,
    path: Path,
    *,
    relative: str,
    project_collection_surface: bool,
    selected_item: dict[str, Any] | None,
    proven_ephemeral_mutations: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """Inspect one source surface, memoized within one qualification snapshot."""

    selected_identity = (
        json.dumps(selected_item, sort_keys=True, separators=(",", ":"))
        if selected_item is not None
        else ""
    )
    cache_key = (
        relative,
        "projected" if project_collection_surface else "whole",
        selected_identity,
        proven_ephemeral_mutations if selected_item is not None else (),
    )
    cached = session.direct_analyses.get(cache_key)
    if cached is not None:
        return cached

    try:
        source = session.read_source(path, relative=relative)
        tree = ast.parse(source, filename=relative)
        if project_collection_surface:
            tree, selected_body_nodes = _project_static_execution_surface(
                tree,
                relative=relative,
                item=selected_item,
            )
        else:
            selected_body_nodes = set()
        remaining_mutation_proofs: dict[str, int] = {}
        if selected_item is not None:
            for event in proven_ephemeral_mutations:
                remaining_mutation_proofs[event] = (
                    remaining_mutation_proofs.get(event, 0) + 1
                )

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                dynamic = (
                    isinstance(func, ast.Name)
                    and func.id in {"__import__", "eval", "exec"}
                ) or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "import_module"
                )
                if dynamic:
                    raise _StaticImportUncertain(
                        f"dynamic import/evaluation in {relative}"
                    )
                unsafe_name = (
                    func.id
                    if isinstance(func, ast.Name)
                    and func.id
                    in {
                        "open",
                        "stat",
                        "lstat",
                        "access",
                        "exists",
                        "lexists",
                        "isfile",
                        "isdir",
                        "getattr",
                        "setattr",
                        "delattr",
                        "compile",
                        "globals",
                        "locals",
                        "vars",
                        "chmod",
                        "mkdir",
                        "remove",
                        "rename",
                        "rmdir",
                        "symlink",
                        "touch",
                        "truncate",
                        "unlink",
                        "utime",
                    }
                    else None
                )
                unsafe_attribute = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    and func.attr
                    in {
                        "open",
                        "stat",
                        "lstat",
                        "access",
                        "exists",
                        "lexists",
                        "is_file",
                        "is_dir",
                        "isfile",
                        "isdir",
                        "read_text",
                        "read_bytes",
                        "glob",
                        "rglob",
                        "iterdir",
                        "listdir",
                        "walk",
                        "chmod",
                        "mkdir",
                        "remove",
                        "rename",
                        "replace",
                        "rmdir",
                        "symlink",
                        "touch",
                        "truncate",
                        "unlink",
                        "utime",
                    }
                    else None
                )
                if unsafe_name or unsafe_attribute:
                    operation = unsafe_name or unsafe_attribute
                    event = _STATIC_OPERATION_EVENTS.get(operation)
                    if (
                        id(node) in selected_body_nodes
                        and event is not None
                        and remaining_mutation_proofs.get(event, 0) > 0
                    ):
                        remaining_mutation_proofs[event] -= 1
                        continue
                    raise _StaticImportUncertain(
                        f"untracked dynamic or file-system operation {operation!r} "
                        f"in {relative}"
                    )
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "path"
                and isinstance(node.value, ast.Name)
                and node.value.id == "sys"
            ):
                raise _StaticImportUncertain(f"sys.path access in {relative}")
        try:
            required_modules, optional_modules = static_import_requests(
                session.root,
                relative,
                tree,
            )
        except ConfigurationError as exc:
            raise _StaticImportUncertain(str(exc)) from exc
        result = required_modules, optional_modules, None
    except (SyntaxError, _StaticImportUncertain) as exc:
        result = (), (), (
            f"could not statically inspect {relative}: {exc}"
            if isinstance(exc, SyntaxError)
            else str(exc)
        )
    if len(session.direct_analyses) >= _MAX_PROFILE_IMPORT_MODULES:
        return (), (), "static import analysis exceeds its structural boundary"
    session.direct_analyses[cache_key] = result
    return result


def _static_import_fallback(
    root: Path,
    relative: str,
    *,
    external_modules: tuple[str, ...] = (),
    import_search_roots: tuple[str, ...] = (".", "src"),
    collection_item: dict[str, Any] | None = None,
    proven_ephemeral_mutations: tuple[str, ...] = (),
    analysis_session: _StaticImportFallbackSession | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    root = root.resolve(strict=True)
    if analysis_session is None:
        analysis_session = _StaticImportFallbackSession(root)
    else:
        analysis_session.validate_root(root)
    candidate = root / relative
    try:
        _reject_symlink_chain(root, candidate)
        source_path = candidate.resolve(strict=True)
        source_path.relative_to(root)
    except (OSError, ValueError):
        return (), (), "collection test path is missing or outside the project"
    if is_link_like(candidate) or not source_path.is_file() or source_path.suffix != ".py":
        return (), (), "collection test path is not a regular Python file"

    pending: list[tuple[Path, dict[str, Any] | None]] = [
        (source_path, collection_item)
    ]
    visited: set[Path] = set()
    files: set[str] = set()
    absent_files: set[str] = set()
    external_toplevels = {
        module.split(".", maxsplit=1)[0] for module in external_modules
    }
    try:
        while pending:
            path, selected_item = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            relative_path = path.relative_to(root).as_posix()
            files.add(relative_path)
            (
                required_modules,
                optional_modules,
                direct_uncertainty,
            ) = _direct_static_import_analysis(
                analysis_session,
                path,
                relative=relative_path,
                project_collection_surface=collection_item is not None,
                selected_item=selected_item,
                proven_ephemeral_mutations=proven_ephemeral_mutations,
            )
            if direct_uncertainty is not None:
                raise _StaticImportUncertain(direct_uncertainty)
            for module, must_resolve in (
                *((name, True) for name in required_modules),
                *((name, False) for name in optional_modules),
            ):
                try:
                    surface = analysis_session.import_surface(
                        module,
                        search_roots=tuple(import_search_roots),
                    )
                except ConfigurationError as exc:
                    raise _StaticImportUncertain(str(exc)) from exc
                absent_files.update(surface.absent_files)
                if (
                    must_resolve
                    and not surface.resolved_locally
                    and module.split(".", maxsplit=1)[0]
                    not in external_toplevels
                ):
                    raise _StaticImportUncertain(
                        f"external or unresolved import {module!r} requires fresh execution"
                    )
                for imported_relative in surface.existing_files:
                    imported = root / imported_relative
                    files.add(imported_relative)
                    if imported.suffix == ".py" and imported not in visited:
                        pending.append((imported, None))
    except _StaticImportUncertain as exc:
        return (), (), str(exc)

    if not files:
        return (), (), "static import closure is empty"
    return tuple(sorted(files)), tuple(sorted(absent_files)), None


def _validate_pytest_task(task: TaskSpec) -> None:
    if not task.result_only or not task.closure_reviewed:
        raise ConfigurationError(
            "pytest qualification requires a result-only, closure-reviewed hermetic v2 carrier task"
        )
    if task.unsafe_effects:
        raise ConfigurationError("pytest qualification refuses tasks with unsafe effects")
    if task.image is None or task.platform != "linux/amd64":
        raise ConfigurationError("pytest qualification requires a pinned Linux/amd64 OCI task")
    validate_pytest_execution_contract(
        task,
        base_args=(),
        field="pytest qualification task",
    )


def _validate_import_observations(
    raw: object,
    *,
    label: str,
) -> tuple[
    tuple[tuple[str, str], ...],
    tuple[tuple[str, str], ...],
    tuple[str, ...],
]:
    if not isinstance(raw, dict) or set(raw) != {
        "external_modules",
        "stable_external_toplevels",
        "project_search_roots",
    }:
        raise ConfigurationError(f"{label} is malformed")
    raw_modules = raw.get("external_modules")
    raw_stable_toplevels = raw.get("stable_external_toplevels")
    raw_roots = raw.get("project_search_roots")
    if (
        not isinstance(raw_modules, list)
        or len(raw_modules) > _MAX_PROFILE_IMPORT_MODULES
        or not isinstance(raw_stable_toplevels, list)
        or len(raw_stable_toplevels) > _MAX_PROFILE_IMPORT_MODULES
        or not isinstance(raw_roots, list)
        or len(raw_roots) > _MAX_PROFILE_IMPORT_ROOTS
    ):
        raise ConfigurationError(f"{label} exceeds its structural boundary")
    modules: list[tuple[str, str]] = []
    allowed_provenance = {"built-in", "frozen", "stdlib", "dependency", "namespace"}
    for row in raw_modules:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not row[0]
            or len(row[0]) > _MAX_PROFILE_IMPORT_TEXT
            or not all(piece.isidentifier() for piece in row[0].split("."))
            or row[1] not in allowed_provenance
        ):
            raise ConfigurationError(f"{label} contains an invalid module row")
        modules.append((row[0], str(row[1])))
    if modules != sorted(set(modules)):
        raise ConfigurationError(f"{label} module rows are not canonical")

    stable_toplevels: list[tuple[str, str]] = []
    for row in raw_stable_toplevels:
        if (
            not isinstance(row, list)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not row[0]
            or len(row[0]) > _MAX_PROFILE_IMPORT_TEXT
            or not row[0].isidentifier()
            or row[1] not in {"stdlib-name", "dependency-inventory"}
        ):
            raise ConfigurationError(
                f"{label} contains an invalid stable top-level row"
            )
        stable_toplevels.append((row[0], str(row[1])))
    if stable_toplevels != sorted(set(stable_toplevels)):
        raise ConfigurationError(
            f"{label} stable top-level rows are not canonical"
        )

    roots: list[str] = []
    for root in raw_roots:
        if (
            not isinstance(root, str)
            or not root
            or len(root) > _MAX_PROFILE_IMPORT_TEXT
            or "\\" in root
            or root.startswith(("/", "-", "@"))
        ):
            raise ConfigurationError(f"{label} contains an invalid project search root")
        pieces = tuple(root.split("/"))
        if root != "." and any(piece in {"", ".", ".."} for piece in pieces):
            raise ConfigurationError(f"{label} contains a non-canonical project search root")
        if root in {".git", ".zerorun", ".zerorun-env"} or root.startswith(
            (".git/", ".zerorun/", ".zerorun-env/")
        ):
            raise ConfigurationError(f"{label} contains a mutable state search root")
        roots.append(root)
    if roots != sorted(set(roots)):
        raise ConfigurationError(f"{label} project search roots are not canonical")
    return tuple(modules), tuple(stable_toplevels), tuple(roots)


def _validate_executed_imports(
    raw: object,
    *,
    label: str,
) -> dict[str, tuple[ExecutedImportEdge, ...]]:
    if not isinstance(raw, dict) or len(raw) > 25000:
        raise ConfigurationError(f"{label} is malformed")
    result: dict[str, tuple[ExecutedImportEdge, ...]] = {}
    total = 0
    for bucket, rows in raw.items():
        if not isinstance(bucket, str) or not bucket or not isinstance(rows, list):
            raise ConfigurationError(f"{label} is malformed")
        total += len(rows)
        if total > _MAX_PROFILE_IMPORT_EDGES:
            raise ConfigurationError(f"{label} exceeds its structural boundary")
        decoded: list[ExecutedImportEdge] = []
        identities: dict[tuple[str, int, str, int], ExecutedImportEdge] = {}
        for row in rows:
            if (
                not isinstance(row, list)
                or len(row) != 11
                or not isinstance(row[0], str)
                or not row[0]
                or len(row[0]) > _MAX_PROFILE_IMPORT_TEXT
                or not row[0].endswith(".py")
                or "\\" in row[0]
                or row[0].startswith(("/", "-", "@"))
                or any(piece in {"", ".", ".."} for piece in row[0].split("/"))
                or type(row[1]) is not int
                or row[1] <= 0
                or not isinstance(row[2], str)
                or not row[2]
                or len(row[2]) > _MAX_PROFILE_IMPORT_TEXT
                or any(type(row[index]) is not int for index in range(3, 8))
                or row[3] < 0
                or row[4] <= 0
                or row[5] < row[4]
                or row[6] < 0
                or row[7] < 0
                or not isinstance(row[8], str)
                or len(row[8]) > _MAX_PROFILE_IMPORT_TEXT
                or (
                    row[8]
                    and not all(piece.isidentifier() for piece in row[8].split("."))
                )
                or not isinstance(row[9], list)
                or len(row[9]) > _MAX_PROFILE_IMPORT_FROMLIST
                or any(
                    not isinstance(value, str)
                    or not value
                    or len(value) > _MAX_PROFILE_IMPORT_TEXT
                    or (value != "*" and not value.isidentifier())
                    for value in row[9]
                )
                or type(row[10]) is not int
                or row[10] < 0
                or row[10] > _MAX_PROFILE_IMPORT_TEXT
                or (row[10] == 0 and not row[8])
            ):
                raise ConfigurationError(f"{label} contains an invalid import edge")
            edge = ExecutedImportEdge(
                path=row[0],
                firstlineno=row[1],
                code_name=row[2],
                instruction_offset=row[3],
                lineno=row[4],
                end_lineno=row[5],
                col_offset=row[6],
                end_col_offset=row[7],
                module=row[8],
                fromlist=tuple(row[9]),
                level=row[10],
            )
            identity = (
                edge.path,
                edge.firstlineno,
                edge.code_name,
                edge.instruction_offset,
            )
            previous = identities.get(identity)
            if previous is not None and previous != edge:
                raise ConfigurationError(
                    f"{label} contains conflicting import-edge attribution"
                )
            identities[identity] = edge
            decoded.append(edge)
        if decoded != sorted(set(decoded)):
            raise ConfigurationError(f"{label} import edges are not canonical")
        result[bucket] = tuple(decoded)
    return result


def _docker_profile(
    manifest: Manifest,
    task: TaskSpec,
    *,
    targets: tuple[str, ...],
    expected_collection_nodeids: tuple[str, ...] | None = None,
    selected_nodeids: tuple[str, ...] | None = None,
    timeout_seconds: float = 900.0,
) -> tuple[dict[str, Any], float, int, str]:
    if (expected_collection_nodeids is None) != (selected_nodeids is None):
        raise ConfigurationError(
            "pytest qualification isolation requires both collection and selection"
        )
    if expected_collection_nodeids is not None:
        expected = list(expected_collection_nodeids)
        selected = list(selected_nodeids or ())
        if (
            not expected
            or not selected
            or not all(isinstance(value, str) and value for value in expected)
            or not all(isinstance(value, str) and value for value in selected)
            or len(expected) != len(set(expected))
            or len(selected) != len(set(selected))
            or not set(selected).issubset(set(expected))
            or selected != [value for value in expected if value in set(selected)]
        ):
            raise ConfigurationError(
                "pytest qualification isolation selection is not an ordered collection subset"
            )
    # A redirected ``__pycache__`` does not hide legacy top-level
    # ``module.pyc`` files from ``SourcelessFileLoader``.  Qualification must
    # share the product runtime's explicit refusal policy so observation can
    # never be based on bytecode for which there are no reviewable source
    # bytes.  Recheck after execution to catch a persistent concurrent write.
    reject_sourceless_workspace_bytecode(manifest.root)
    docker = _docker_path(manifest.root)
    runtime = inspect_runtime(task, repository_root=manifest.root)
    environment, _ = hermetic_environment(task)
    image = str(runtime["requested_image"])

    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-pytest-qualify-"
    ) as temporary:
        support = temporary / "support"
        profile_output = temporary / "output"
        support.mkdir()
        profile_output.mkdir()
        (support / _CONTEXT_FILE).write_text(_CONTEXT_SOURCE, encoding="utf-8")
        (support / _PLUGIN_FILE).write_text(_PLUGIN_SOURCE, encoding="utf-8")
        (support / _SITE_FILE).write_text(_SITE_SOURCE, encoding="utf-8")
        if expected_collection_nodeids is None:
            # Use the runtime adapter's exact collection plugin in the same
            # profiled pytest process.  This removes a second collect-only OCI
            # launch without creating a second collection contract.
            (support / _COLLECTION_PLUGIN_FILE).write_text(
                _COLLECTION_PLUGIN,
                encoding="utf-8",
            )
        if expected_collection_nodeids is not None:
            selection_payload = {
                "expected_nodeids": list(expected_collection_nodeids),
                "selected_nodeids": list(selected_nodeids or ()),
            }
            selection_bytes = json.dumps(
                selection_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(selection_bytes) > _MAX_PROFILE_SELECTION_BYTES:
                raise ConfigurationError(
                    "pytest qualification isolation selection exceeds the byte boundary"
                )
            (support / _SELECTION_FILE).write_bytes(selection_bytes)
        for path in support.iterdir():
            path.chmod(0o644)
        support.chmod(0o755)
        profile_output.chmod(0o777)

        container_name = _new_container_name("qualify")
        command = [
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
            "--mount",
            _docker_bind_mount(support, _PROFILE_MOUNT, readonly=True),
            "--mount",
            _docker_bind_mount(profile_output, _PROFILE_OUTPUT_MOUNT),
            "--workdir",
            "/workspace",
        ]
        _validate_task_docker_environment(task)
        command.extend(["--env", f"PYTHONPATH={_PROFILE_MOUNT}"])
        if expected_collection_nodeids is None:
            command.extend(
                [
                    "--env",
                    (
                        "ZERORUN_COLLECTION_OUTPUT="
                        f"{_PROFILE_OUTPUT_MOUNT}/{_COLLECTION_OUTPUT_FILE}"
                    ),
                ]
            )
        else:
            command.extend(
                [
                    "--env",
                    f"ZERORUN_PROFILE_SELECTION={_PROFILE_MOUNT}/{_SELECTION_FILE}",
                ]
            )
        command.extend(
            [
                image,
                *task.command,
                "-p",
                "zerorun_qualify_plugin",
                *(
                    ("-p", "zerorun_collection_plugin")
                    if expected_collection_nodeids is None
                    else ()
                ),
                *targets,
            ]
        )
        docker_environment = _docker_client_environment(docker)
        container_environment = _container_environment_bytes(task, environment)
        started = time.perf_counter()

        try:
            image_index = command.index(image)
            launch = [
                *command[:image_index],
                "--env-file",
                "",
                *command[image_index:],
            ]
            process = _run_created_container(
                launch,
                docker=docker,
                container_name=container_name,
                cwd=manifest.root,
                environment=docker_environment,
                execution_timeout_seconds=timeout_seconds,
                operation_label="profiled pytest",
                container_environment=container_environment,
                environment_repository_root=manifest.root,
                environment_file_index=image_index + 1,
            )
        except OSError as exc:
            raise ConfigurationError(
                f"profiled pytest Docker process could not start: {exc}"
            ) from exc
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        reject_sourceless_workspace_bytecode(manifest.root)
        outputs: list[Path] = []
        collection_output: Path | None = None
        total_profile_bytes = 0
        try:
            with os.scandir(profile_output) as entries:
                for entry in entries:
                    if entry.name == _COLLECTION_OUTPUT_FILE:
                        if (
                            expected_collection_nodeids is not None
                            or collection_output is not None
                            or not entry.is_file(follow_symlinks=False)
                        ):
                            raise ConfigurationError(
                                "pytest call profiler collection output is malformed"
                            )
                        info = entry.stat(follow_symlinks=False)
                        if info.st_size > DOCKER_METADATA_OUTPUT_LIMIT_BYTES:
                            raise ConfigurationError(
                                "pytest call profiler collection evidence exceeds the byte boundary"
                            )
                        total_profile_bytes += info.st_size
                        if total_profile_bytes > _MAX_TOTAL_PROFILE_BYTES:
                            raise ConfigurationError(
                                "pytest call profiler exceeded the aggregate evidence boundary"
                            )
                        collection_output = Path(entry.path)
                        continue
                    if len(outputs) >= _MAX_PROFILE_PROCESSES:
                        raise ConfigurationError(
                            "pytest call profiler exceeded the process evidence boundary"
                        )
                    if (
                        not _PROFILE_NAME_RE.fullmatch(entry.name)
                        or not entry.is_file(follow_symlinks=False)
                    ):
                        raise ConfigurationError(
                            "pytest call profiler output contains an unexpected entry"
                        )
                    info = entry.stat(follow_symlinks=False)
                    if info.st_size > DOCKER_METADATA_OUTPUT_LIMIT_BYTES:
                        raise ConfigurationError(
                            f"pytest call profile exceeds the byte boundary: {entry.name}"
                        )
                    total_profile_bytes += info.st_size
                    if total_profile_bytes > _MAX_TOTAL_PROFILE_BYTES:
                        raise ConfigurationError(
                            "pytest call profiler exceeded the aggregate evidence boundary"
                        )
                    outputs.append(Path(entry.path))
        except OSError as exc:
            raise ConfigurationError(
                f"pytest call profiler outputs are unreadable: {exc}"
            ) from exc
        outputs.sort(key=lambda path: path.name)
        collection: dict[str, Any] | None = None
        if expected_collection_nodeids is None:
            if collection_output is None:
                raise ConfigurationError(
                    "profiled pytest run produced no exact collection evidence"
                )
            try:
                raw_collection = _read_bounded_json(
                    collection_output,
                    label="profiled pytest collection evidence",
                    limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
                )
            except ConfigurationError as exc:
                raise ConfigurationError(
                    f"profiled pytest collection evidence is unreadable: {exc}"
                ) from exc
            collection = _validate_collection_evidence(
                raw_collection,
                label="profiled pytest collection evidence",
                expected_targets=targets,
            )
        if not outputs:
            detail = (process.stdout + process.stderr).decode("utf-8", errors="replace")[-6000:]
            raise ConfigurationError(
                f"profiled pytest run produced no call evidence ({process.returncode}): {detail}"
            )
        merged: dict[str, dict[str, str]] = {}
        encoded_site_cache: dict[tuple[str, int, str, tuple[str, ...], bool], str] = {}
        merged_uncertainties: dict[str, set[str]] = {}
        merged_thread_observations: dict[str, dict[str, int | bool]] = {}
        merged_filesystem_mutations: dict[
            str, set[tuple[str, str, str]]
        ] = {}
        non_function_fixtures: set[str] = set()
        integrity_violations: list[str] = []
        collection_attestations: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        started_nodeids: set[str] = set()
        completed_nodeids: set[str] = set()
        merged_external_modules: set[tuple[str, str]] = set()
        merged_stable_external_toplevels: set[tuple[str, str]] = set()
        merged_project_search_roots: set[str] = set()
        merged_executed_imports: dict[
            str,
            dict[tuple[str, int, str, int], ExecutedImportEdge],
        ] = {}
        pytest_plugin_processes = 0
        for output in outputs:
            try:
                row = _read_bounded_json(
                    output,
                    label=f"pytest call profile {output.name}",
                    limit_bytes=DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
                )
            except ConfigurationError as exc:
                raise ConfigurationError(f"pytest call profile is unreadable: {exc}") from exc
            if (
                not isinstance(row, dict)
                or set(row)
                != {
                    "loaded",
                    "pid",
                    "pytest_plugin_loaded",
                    "integrity_violations",
                    "collection_nodeids",
                    "selected_nodeids",
                    "started_nodeids",
                    "completed_nodeids",
                    "uncertainties",
                    "thread_observations",
                    "non_function_fixtures",
                    "filesystem_mutations",
                    "import_observations",
                    "executed_imports",
                    "buckets",
                }
                or row.get("loaded") is not True
                or not isinstance(row.get("pid"), int)
                or isinstance(row.get("pid"), bool)
                or row.get("pid", 0) <= 0
                or output.name != f"profile-{row.get('pid')}.json"
            ):
                raise ConfigurationError(
                    f"pytest call profiler process evidence is malformed: {output.name}"
                )
            _validate_bounded_json_value(
                row,
                label=f"pytest call profile {output.name}",
            )
            plugin_loaded = row.get("pytest_plugin_loaded")
            if not isinstance(plugin_loaded, bool):
                raise ConfigurationError(
                    f"pytest call profiler plugin evidence is malformed: {output.name}"
                )
            pytest_plugin_processes += int(plugin_loaded)
            (
                external_modules,
                stable_external_toplevels,
                project_search_roots,
            ) = _validate_import_observations(
                row.get("import_observations"),
                label=f"pytest import observation evidence {output.name}",
            )
            if plugin_loaded:
                merged_external_modules.update(external_modules)
                merged_stable_external_toplevels.update(
                    stable_external_toplevels
                )
                merged_project_search_roots.update(project_search_roots)
            process_import_edges = _validate_executed_imports(
                row.get("executed_imports"),
                label=f"pytest executed import evidence {output.name}",
            )
            for bucket, edges in process_import_edges.items():
                target_edges = merged_executed_imports.setdefault(bucket, {})
                for edge in edges:
                    identity = (
                        edge.path,
                        edge.firstlineno,
                        edge.code_name,
                        edge.instruction_offset,
                    )
                    previous = target_edges.get(identity)
                    if previous is not None and previous != edge:
                        merged_uncertainties.setdefault(bucket, set()).add(
                            "conflicting-import-edge-attribution"
                        )
                        continue
                    target_edges[identity] = edge
            attestation_lists: list[list[str]] = []
            for field in (
                "collection_nodeids",
                "selected_nodeids",
                "started_nodeids",
                "completed_nodeids",
            ):
                values = row.get(field)
                if (
                    not isinstance(values, list)
                    or not all(isinstance(value, str) and value for value in values)
                    or len(values) != len(set(values))
                ):
                    raise ConfigurationError(
                        f"pytest call profiler selection evidence is malformed: {output.name}"
                    )
                attestation_lists.append(values)
            collection_values, selected_values, started_values, completed_values = (
                attestation_lists
            )
            if plugin_loaded:
                collection_attestations.append(
                    (tuple(collection_values), tuple(selected_values))
                )
            started_nodeids.update(started_values)
            completed_nodeids.update(completed_values)
            raw_violations = row.get("integrity_violations", [])
            if not isinstance(raw_violations, list) or not all(
                isinstance(item, str) for item in raw_violations
            ):
                raise ConfigurationError(
                    f"pytest call profiler integrity evidence is malformed: {output.name}"
                )
            integrity_violations.extend(
                f"{output.name}: {item}" for item in raw_violations
            )
            raw_uncertainties = row.get("uncertainties", {})
            if not isinstance(raw_uncertainties, dict):
                raise ConfigurationError(
                    f"pytest call profiler uncertainty evidence is malformed: {output.name}"
                )
            for bucket, kinds in raw_uncertainties.items():
                if (
                    not isinstance(bucket, str)
                    or not isinstance(kinds, list)
                    or not all(isinstance(kind, str) for kind in kinds)
                ):
                    raise ConfigurationError(
                        f"pytest call profiler uncertainty evidence is malformed: {output.name}"
                    )
                merged_uncertainties.setdefault(bucket, set()).update(kinds)
            raw_thread_observations = row.get("thread_observations", {})
            if not isinstance(raw_thread_observations, dict):
                raise ConfigurationError(
                    f"pytest thread lifecycle evidence is malformed: {output.name}"
                )
            for bucket, observation in raw_thread_observations.items():
                expected_thread_fields = {
                    "started",
                    "joined",
                    "terminated",
                    "start_failed",
                    "complete_before_boundary",
                }
                if (
                    not isinstance(bucket, str)
                    or not bucket
                    or not isinstance(observation, dict)
                    or set(observation) != expected_thread_fields
                    or any(
                        type(observation.get(field)) is not int
                        or observation.get(field, -1) < 0
                        for field in (
                            "started",
                            "joined",
                            "terminated",
                            "start_failed",
                        )
                    )
                    or type(observation.get("complete_before_boundary")) is not bool
                    or observation.get("started", 0) <= 0
                    or observation.get("started", 0) > 10000
                    or observation.get("joined", 0) > observation.get("started", 0)
                    or observation.get("terminated", 0) > observation.get("started", 0)
                    or observation.get("start_failed", 0) > observation.get("started", 0)
                    or observation.get("complete_before_boundary")
                    is not (
                        observation.get("joined") == observation.get("started")
                        and observation.get("terminated") == observation.get("started")
                        and observation.get("start_failed") == 0
                    )
                ):
                    raise ConfigurationError(
                        f"pytest thread lifecycle evidence is malformed: {output.name}"
                    )
                target = merged_thread_observations.setdefault(
                    bucket,
                    {
                        "started": 0,
                        "joined": 0,
                        "terminated": 0,
                        "start_failed": 0,
                        "complete_before_boundary": True,
                    },
                )
                for field in ("started", "joined", "terminated", "start_failed"):
                    target[field] = int(target[field]) + int(observation[field])
                target["complete_before_boundary"] = bool(
                    target["complete_before_boundary"]
                    and observation["complete_before_boundary"]
                )
                if (
                    observation["complete_before_boundary"] is False
                    and not any(
                        str(reason).startswith("thread-")
                        for reason in raw_uncertainties.get(bucket, [])
                    )
                ):
                    raise ConfigurationError(
                        f"pytest incomplete thread lifecycle lacks uncertainty: {output.name}"
                    )
            raw_fixtures = row.get("non_function_fixtures", [])
            if not isinstance(raw_fixtures, list):
                raise ConfigurationError(
                    f"pytest fixture evidence is malformed: {output.name}"
                )
            for fixture in raw_fixtures:
                if (
                    not isinstance(fixture, list)
                    or len(fixture) != 5
                    or not isinstance(fixture[0], str)
                    or not isinstance(fixture[1], int)
                    or not all(isinstance(value, str) for value in fixture[2:])
                ):
                    raise ConfigurationError(
                        f"pytest fixture evidence is malformed: {output.name}"
                    )
                non_function_fixtures.add(
                    json.dumps(fixture, sort_keys=True, separators=(",", ":"))
                )
            raw_mutations = row.get("filesystem_mutations", {})
            if not isinstance(raw_mutations, dict):
                raise ConfigurationError(
                    f"pytest filesystem mutation evidence is malformed: {output.name}"
                )
            mutation_count = 0
            for bucket, rows in raw_mutations.items():
                if not isinstance(bucket, str) or not bucket or not isinstance(rows, list):
                    raise ConfigurationError(
                        f"pytest filesystem mutation evidence is malformed: {output.name}"
                    )
                target = merged_filesystem_mutations.setdefault(bucket, set())
                for mutation in rows:
                    mutation_count += 1
                    if (
                        mutation_count > _MAX_PROFILE_IMPORT_MODULES
                        or not isinstance(mutation, list)
                        or len(mutation) != 3
                        or not all(isinstance(value, str) for value in mutation)
                        or not mutation[0]
                        or mutation[1]
                        not in {"project", "ephemeral", "control", "external", "unknown"}
                        or not mutation[2]
                        or len(mutation[2]) > _MAX_PROFILE_IMPORT_TEXT
                    ):
                        raise ConfigurationError(
                            f"pytest filesystem mutation evidence is malformed: {output.name}"
                        )
                    target.add((mutation[0], mutation[1], mutation[2]))
            raw_buckets = row.get("buckets", {})
            if not isinstance(raw_buckets, dict):
                raise ConfigurationError(
                    f"pytest call profiler bucket evidence is malformed: {output.name}"
                )
            has_project_calls = False
            for bucket, rows in raw_buckets.items():
                if not isinstance(bucket, str) or not isinstance(rows, list):
                    raise ConfigurationError(
                        f"pytest call profiler bucket evidence is malformed: {output.name}"
                    )
                target = merged.setdefault(bucket, {})
                for site in rows:
                    if (
                        not isinstance(site, list)
                        or len(site) != 5
                        or not isinstance(site[0], str)
                        or not isinstance(site[1], int)
                        or not isinstance(site[2], str)
                        or not isinstance(site[3], list)
                        or not all(isinstance(name, str) for name in site[3])
                        or not isinstance(site[4], bool)
                    ):
                        raise ConfigurationError(
                            f"pytest call profiler call-site evidence is malformed: {output.name}"
                        )
                    has_project_calls = True
                    site_key = (
                        site[0],
                        site[1],
                        site[2],
                        tuple(site[3]),
                        site[4],
                    )
                    encoded_site = encoded_site_cache.get(site_key)
                    if encoded_site is None:
                        encoded_site = json.dumps(
                            site,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        encoded_site_cache[site_key] = encoded_site
                    target[encoded_site] = encoded_site
            if not plugin_loaded and has_project_calls:
                merged_uncertainties.setdefault("__session__", set()).add(
                    "unattributed-child-process-project-calls"
                )
            if not plugin_loaded and raw_thread_observations:
                merged_uncertainties.setdefault("__session__", set()).add(
                    "unattributed-child-process-thread-observation"
                )
            if not plugin_loaded and raw_mutations:
                merged_uncertainties.setdefault("__session__", set()).add(
                    "unattributed-child-process-filesystem-mutation"
                )
            if not plugin_loaded and process_import_edges:
                merged_uncertainties.setdefault("__session__", set()).add(
                    "unattributed-child-process-import-edge"
                )
        if pytest_plugin_processes < 1:
            raise ConfigurationError(
                "pytest qualification plugin did not attest loading in any profiled pytest process"
            )
        if integrity_violations:
            raise ConfigurationError(
                "pytest call profiler integrity check failed: "
                + "; ".join(sorted(set(integrity_violations))[:20])
            )
        if expected_collection_nodeids is not None:
            expected_attestation = (
                tuple(expected_collection_nodeids),
                tuple(selected_nodeids or ()),
            )
            if not collection_attestations or any(
                attestation != expected_attestation
                for attestation in collection_attestations
            ):
                raise ConfigurationError(
                    "pytest qualification isolation collection attestation is incomplete"
                )
            expected_execution = set(selected_nodeids or ())
            if (
                started_nodeids != expected_execution
                or completed_nodeids != expected_execution
            ):
                raise ConfigurationError(
                    "pytest qualification isolation did not execute the exact selected nodes"
                )
            unexpected_buckets = set(merged) - {
                "__session__",
                *(selected_nodeids or ()),
            }
            unexpected_buckets.update(
                set(merged_thread_observations)
                - {"__session__", *(selected_nodeids or ())}
            )
            unexpected_buckets.update(
                set(merged_executed_imports)
                - {"__session__", *(selected_nodeids or ())}
            )
            if unexpected_buckets:
                raise ConfigurationError(
                    "pytest qualification isolation observed an unattributed node bucket"
                )
        payload = {
            "loaded": True,
            "process_count": len(outputs),
            "pytest_plugin_process_count": pytest_plugin_processes,
            "integrity_violations": [],
            "collection_attested": bool(collection_attestations),
            "started_nodeids": sorted(started_nodeids),
            "completed_nodeids": sorted(completed_nodeids),
            "uncertainties": {
                bucket: sorted(kinds)
                for bucket, kinds in sorted(merged_uncertainties.items())
            },
            "thread_observations": {
                bucket: observation
                for bucket, observation in sorted(merged_thread_observations.items())
            },
            "non_function_fixtures": [
                json.loads(fixture) for fixture in sorted(non_function_fixtures)
            ],
            "filesystem_mutations": {
                bucket: [list(row) for row in sorted(rows)]
                for bucket, rows in sorted(merged_filesystem_mutations.items())
            },
            "import_observations": {
                "external_modules": [
                    list(row) for row in sorted(merged_external_modules)
                ],
                "stable_external_toplevels": [
                    list(row)
                    for row in sorted(merged_stable_external_toplevels)
                ],
                "project_search_roots": sorted(merged_project_search_roots),
            },
            "executed_imports": {
                bucket: [
                    [
                        edge.path,
                        edge.firstlineno,
                        edge.code_name,
                        edge.instruction_offset,
                        edge.lineno,
                        edge.end_lineno,
                        edge.col_offset,
                        edge.end_col_offset,
                        edge.module,
                        list(edge.fromlist),
                        edge.level,
                    ]
                    for edge in sorted(edges.values())
                ]
                for bucket, edges in sorted(merged_executed_imports.items())
            },
            "buckets": {
                bucket: [json.loads(item) for item in sorted(rows)]
                for bucket, rows in sorted(merged.items())
            },
        }
        if collection is not None:
            collection_nodeids = tuple(collection["nodeids"])
            expected_attestation = (collection_nodeids, collection_nodeids)
            if not collection_attestations or any(
                attestation != expected_attestation
                for attestation in collection_attestations
            ):
                raise ConfigurationError(
                    "profiled pytest collection evidence disagrees with profiler attestation"
                )
            if process.returncode == 0 and (
                started_nodeids != set(collection_nodeids)
                or completed_nodeids != set(collection_nodeids)
            ):
                raise ConfigurationError(
                    "profiled pytest baseline did not execute the exact collected nodes"
                )
            payload["collection"] = collection
        tail = (process.stdout + process.stderr).decode("utf-8", errors="replace")[-4000:]
    return payload, elapsed_ms, process.returncode, tail


def _decode_sites(
    raw: object,
    *,
    cache: dict[
        tuple[tuple[str, int, str, tuple[str, ...], bool], ...],
        tuple[CallSite, ...],
    ]
    | None = None,
) -> tuple[CallSite, ...]:
    if not isinstance(raw, list):
        return ()
    normalized_rows: set[tuple[str, int, str, tuple[str, ...], bool]] = set()
    for row in raw:
        if not isinstance(row, list) or len(row) != 5:
            continue
        path, firstlineno, name, globals_raw, dynamic = row
        if (
            not isinstance(path, str)
            or not isinstance(firstlineno, int)
            or not isinstance(name, str)
            or not isinstance(globals_raw, list)
            or not all(isinstance(item, str) for item in globals_raw)
            or not isinstance(dynamic, bool)
        ):
            continue
        normalized_rows.add(
            (
                path,
                firstlineno,
                name,
                tuple(sorted(set(globals_raw))),
                dynamic,
            )
        )
    key = tuple(sorted(normalized_rows))
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    result = tuple(
        CallSite(
            path=path,
            firstlineno=firstlineno,
            name=name,
            globals=globals_names,
            dynamic_globals=dynamic,
        )
        for path, firstlineno, name, globals_names, dynamic in key
    )
    if cache is not None:
        cache[key] = result
    return result


def _closure_row(
    root: Path,
    sites: tuple[CallSite, ...],
    *,
    analysis_cache: SymbolClosureAnalysisCache | None = None,
    external_modules: tuple[str, ...] = (),
    import_search_roots: tuple[str, ...] = (".", "src"),
    executed_imports: tuple[ExecutedImportEdge, ...] | None = None,
    row_cache: dict[
        tuple[tuple[CallSite, ...], tuple[ExecutedImportEdge, ...] | None],
        tuple[
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            int,
        ],
    ]
    | None = None,
) -> dict[str, Any]:
    cache_key = (sites, executed_imports)
    cached = row_cache.get(cache_key) if row_cache is not None else None
    if cached is not None:
        (
            selectors,
            fallback_files,
            absent_files,
            unresolved_imports,
            observed_call_count,
        ) = cached
        return {
            "selectors": list(selectors),
            "fallback_files": list(fallback_files),
            "absent_files": list(absent_files),
            "unresolved_imports": list(unresolved_imports),
            "observed_call_count": observed_call_count,
        }
    closure = resolve_symbol_closure(
        root,
        sites,
        analysis_cache=analysis_cache,
        external_modules=external_modules,
        import_search_roots=import_search_roots,
        executed_imports=executed_imports,
    )
    normalized = (
        tuple(closure.selectors),
        tuple(closure.fallback_files),
        tuple(closure.absent_files),
        tuple(closure.unresolved_imports),
        len(closure.call_sites),
    )
    if row_cache is not None:
        row_cache[cache_key] = normalized
    return {
        "selectors": list(normalized[0]),
        "fallback_files": list(normalized[1]),
        "absent_files": list(normalized[2]),
        "unresolved_imports": list(normalized[3]),
        "observed_call_count": normalized[4],
    }


_CLOSURE_SET_FIELDS = ("selectors", "fallback_files", "absent_files")


def _hoist_common_reviewed_closure(
    session_closure: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
) -> None:
    """Factor only the exact shared negative surface into session closure.

    Runtime fingerprints positive session authority separately from node-local
    review authority: changing the former requires explicit requalification,
    while changing the latter safely makes only the affected node fresh.
    Therefore selectors and fallback files must remain byte-for-byte node-local
    even when every reviewable node shares them.  Negative import candidates
    are global fail-closed guards, so moving their exact intersection preserves
    both each effective dependency set and the authorization boundary while
    avoiding an identical serialized copy in every node row.  Fresh-required
    rows are deliberately not rewritten.
    """

    def exact_set(row: dict[str, Any], field: str, *, label: str) -> set[str]:
        raw = row.get(field)
        if (
            not isinstance(raw, list)
            or any(not isinstance(item, str) or not item for item in raw)
            or len(raw) != len(set(raw))
        ):
            raise ConfigurationError(
                f"pytest qualification {label}.{field} is not a canonical string array"
            )
        return set(raw)

    session_sets = {
        field: exact_set(session_closure, field, label="session closure")
        for field in _CLOSURE_SET_FIELDS
    }
    reviewable = [
        (nodeid, row)
        for nodeid, row in sorted(nodes.items())
        if row.get("reviewable") is True
        and row.get("fresh_required") is not True
    ]
    if len(reviewable) < 2:
        return

    node_sets: dict[str, dict[str, set[str]]] = {}
    for nodeid, row in reviewable:
        node_sets[nodeid] = {
            field: exact_set(row, field, label=f"node {nodeid!r}")
            for field in _CLOSURE_SET_FIELDS
        }
    common_absent = set.intersection(
        *(node_sets[nodeid]["absent_files"] for nodeid, _row in reviewable)
    )
    # Preserve the existing present-vs-absent diagnostic boundary for any
    # malformed internal row.  A path that is positive anywhere remains
    # node-local negative evidence and fails through the existing validator.
    positive_paths = set(session_sets["fallback_files"])
    for nodeid, _row in reviewable:
        positive_paths.update(node_sets[nodeid]["fallback_files"])
    common_absent.difference_update(positive_paths)

    session_closure["absent_files"] = sorted(
        session_sets["absent_files"] | common_absent
    )
    for nodeid, row in reviewable:
        row["absent_files"] = sorted(
            node_sets[nodeid]["absent_files"] - common_absent
        )


def _partition_profile_uncertainties(
    profile: dict[str, Any],
    *,
    nodeids: tuple[str, ...],
) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    raw = profile.get("uncertainties", {})
    if not isinstance(raw, dict) or not all(
        isinstance(bucket, str)
        and isinstance(kinds, list)
        and all(isinstance(kind, str) and kind for kind in kinds)
        for bucket, kinds in raw.items()
    ):
        raise ConfigurationError(
            "pytest call profiler produced malformed uncertainty evidence"
        )
    known = set(nodeids)
    node_uncertainties: dict[str, tuple[str, ...]] = {}
    global_uncertainties: set[str] = set(raw.get("__session__", []))
    for bucket, kinds in raw.items():
        if bucket == "__session__":
            continue
        normalized = tuple(sorted(set(kinds)))
        if bucket in known:
            if normalized:
                node_uncertainties[bucket] = normalized
            continue
        global_uncertainties.update(
            f"unattributed uncertainty bucket {bucket!r}: {kind}"
            for kind in normalized
        )
    return node_uncertainties, tuple(sorted(global_uncertainties))


def qualify_pytest_candidate(
    manifest: Manifest,
    *,
    task_name: str,
    targets: tuple[str, ...],
    output: Path,
) -> dict[str, Any]:
    output = validate_repository_file_path(
        manifest.root,
        output,
        field="pytest candidate output",
    )
    if task_name not in manifest.tasks:
        raise ConfigurationError(f"unknown pytest qualification task {task_name!r}")
    task = manifest.tasks[task_name]
    _validate_pytest_task(task)
    targets = validate_reviewed_pytest_targets(
        targets,
        field="pytest qualification targets",
    )

    static_inputs = _static_inputs(manifest.root, targets)
    runtime = inspect_runtime(task, repository_root=manifest.root)
    _, environment_fingerprint = hermetic_environment(task)
    profile, profile_ms, exit_code, output_tail = _docker_profile(
        manifest,
        task,
        targets=targets,
    )
    collection = profile.pop("collection", None)
    if not isinstance(collection, dict):
        raise ConfigurationError(
            "profiled pytest baseline produced no exact collection contract"
        )
    collection_ms = 0.0
    invocation = validate_collection_invocation_contract(
        collection.get("invocation") if isinstance(collection, dict) else None,
        expected_targets=targets,
        field="pytest qualification collection invocation",
    )
    validate_collection_item_paths(
        collection.get("items") if isinstance(collection, dict) else None,
        expected_targets=targets,
        rootpath=str(invocation["rootpath"]),
        field="pytest qualification collection",
    )
    current_nodeids = tuple(collection["nodeids"])
    if exit_code != 0:
        raise ConfigurationError(
            f"profiled pytest baseline did not pass (exit {exit_code}); candidate not generated: {output_tail}"
        )
    initial_profile_ms = profile_ms
    initial_uncertainties = profile.get("uncertainties", {})
    initial_thread_observations = profile.get("thread_observations", {})
    initial_filesystem_mutations = profile.get("filesystem_mutations", {})
    initial_node_uncertainties, _ = _partition_profile_uncertainties(
        profile,
        nodeids=current_nodeids,
    )
    isolated_nodeids = tuple(
        nodeid for nodeid in current_nodeids if nodeid in initial_node_uncertainties
    )
    sibling_nodeids = tuple(
        nodeid for nodeid in current_nodeids if nodeid not in initial_node_uncertainties
    )
    isolation: dict[str, Any] = {
        "attempted": False,
        "isolated_fresh_nodeids": list(isolated_nodeids),
        "sibling_node_count": len(sibling_nodeids),
        "result": "not-needed",
        "profile_ms": 0.0,
        "exit_code": None,
        "uncertainties": {},
    }
    forced_sibling_uncertainties: tuple[str, ...] = ()
    if isolated_nodeids and sibling_nodeids:
        isolation["attempted"] = True
        try:
            sibling_profile, sibling_ms, sibling_exit_code, sibling_tail = (
                _docker_profile(
                    manifest,
                    task,
                    targets=targets,
                    expected_collection_nodeids=current_nodeids,
                    selected_nodeids=sibling_nodeids,
                )
            )
        except ConfigurationError as exc:
            isolation["result"] = "failed-closed"
            isolation["failure"] = str(exc)[:1000]
            forced_sibling_uncertainties = (
                "clean sibling reprofile could not be proven complete",
            )
        else:
            profile_ms += sibling_ms
            isolation["profile_ms"] = round(sibling_ms, 3)
            isolation["exit_code"] = sibling_exit_code
            sibling_node_uncertainties, sibling_global_uncertainties = (
                _partition_profile_uncertainties(
                    sibling_profile,
                    nodeids=sibling_nodeids,
                )
            )
            sibling_uncertainties = sibling_profile.get("uncertainties", {})
            isolation["uncertainties"] = {
                bucket: sorted(set(kinds))
                for bucket, kinds in sorted(sibling_uncertainties.items())
            }
            if sibling_exit_code != 0:
                isolation["result"] = "failed-closed"
                isolation["failure"] = (
                    f"clean sibling reprofile exited {sibling_exit_code}: "
                    + sibling_tail[-500:]
                )[:1000]
                forced_sibling_uncertainties = (
                    "clean sibling reprofile did not pass",
                )
            elif sibling_node_uncertainties or sibling_global_uncertainties:
                isolation["result"] = "uncertain-failed-closed"
                forced_sibling_uncertainties = tuple(
                    sorted(
                        {
                            *sibling_global_uncertainties,
                            *(
                                f"{nodeid}: {kind}"
                                for nodeid, kinds in sibling_node_uncertainties.items()
                                for kind in kinds
                            ),
                        }
                    )
                )
            else:
                isolation["result"] = "clean-siblings-reprofiled"
                profile = sibling_profile
    elif isolated_nodeids:
        isolation["result"] = "all-nodes-isolated-fresh"

    buckets = profile.get("buckets")
    if not isinstance(buckets, dict):
        raise ConfigurationError("pytest call profiler produced no bucket map")
    raw_executed_imports = profile.get("executed_imports")
    executed_imports_by_bucket = (
        None
        if raw_executed_imports is None
        else _validate_executed_imports(
            raw_executed_imports,
            label="pytest call profiler executed import evidence",
        )
    )
    raw_import_observations = profile.get(
        "import_observations",
        {
            "external_modules": [],
            "stable_external_toplevels": [],
            "project_search_roots": [],
        },
    )
    (
        external_module_rows,
        stable_external_toplevel_rows,
        observed_project_search_roots,
    ) = (
        _validate_import_observations(
            raw_import_observations,
            label="pytest call profiler import observation evidence",
        )
    )
    external_modules = tuple(
        row[0]
        for row in (*external_module_rows, *stable_external_toplevel_rows)
    )
    # Root/src are the product's historically supported import roots. The
    # profiler may add exact workspace sys.path entries; taking the union is
    # conservative because it can only add shadow candidates or ambiguity.
    import_search_roots = tuple(
        sorted({".", "src", *observed_project_search_roots})
    )
    effective_node_uncertainties, session_uncertainties = (
        _partition_profile_uncertainties(
            profile,
            nodeids=current_nodeids,
        )
    )
    if forced_sibling_uncertainties:
        session_uncertainties = tuple(
            sorted(set(session_uncertainties + forced_sibling_uncertainties))
        )
    if executed_imports_by_bucket is not None:
        unexpected_import_buckets = set(executed_imports_by_bucket) - {
            "__session__",
            *current_nodeids,
        }
        if unexpected_import_buckets:
            session_uncertainties = tuple(
                sorted(
                    set(session_uncertainties)
                    | {
                        "unattributed executed import bucket: " + bucket
                        for bucket in unexpected_import_buckets
                    }
                )
            )
    if not isinstance(initial_thread_observations, dict) or any(
        not isinstance(bucket, str)
        or bucket not in {"__session__", *current_nodeids}
        or not isinstance(observation, dict)
        for bucket, observation in initial_thread_observations.items()
    ):
        raise ConfigurationError(
            "pytest call profiler produced malformed thread lifecycle evidence"
        )
    closure_analysis_cache = SymbolClosureAnalysisCache(manifest.root)
    decoded_sites_cache: dict[
        tuple[tuple[str, int, str, tuple[str, ...], bool], ...],
        tuple[CallSite, ...],
    ] = {}
    closure_row_cache: dict[
        tuple[tuple[CallSite, ...], tuple[ExecutedImportEdge, ...] | None],
        tuple[
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            int,
        ],
    ] = {}
    session_sites = _decode_sites(
        buckets.get("__session__", []),
        cache=decoded_sites_cache,
    )
    session_closure = _closure_row(
        manifest.root,
        session_sites,
        analysis_cache=closure_analysis_cache,
        external_modules=external_modules,
        import_search_roots=import_search_roots,
        executed_imports=(
            None
            if executed_imports_by_bucket is None
            else executed_imports_by_bucket.get("__session__", ())
        ),
        row_cache=closure_row_cache,
    )
    session_import_uncertainties = tuple(
        str(value) for value in session_closure.pop("unresolved_imports", [])
    )
    if session_import_uncertainties:
        session_uncertainties = tuple(
            sorted(
                set(session_uncertainties)
                | {
                    f"unresolved session import surface: {value}"
                    for value in session_import_uncertainties
                }
            )
        )
    if not session_closure["selectors"] and not session_closure["fallback_files"]:
        session_closure = {
            "selectors": [],
            "fallback_files": list(static_inputs),
            "absent_files": [],
            "observed_call_count": 0,
            "candidate_fallback_reason": "no project session calls were observed; using static pytest/config inputs as conservative candidate fallback",
        }

    nodes: dict[str, dict[str, Any]] = {}
    static_fallback_cache: dict[
        tuple[str, str, tuple[str, ...]],
        tuple[tuple[str, ...], tuple[str, ...], str | None],
    ] = {}
    static_fallback_analysis_session = _StaticImportFallbackSession(
        manifest.root,
        closure_analysis_cache=closure_analysis_cache,
    )
    ancestor_surface_cache: dict[str, LocalImportSurface] = {}
    raw_filesystem_mutations = profile.get("filesystem_mutations", {})
    if not isinstance(raw_filesystem_mutations, dict):
        raise ConfigurationError(
            "pytest call profiler produced malformed filesystem mutation evidence"
        )
    collection_items = {
        str(item.get("nodeid")): item
        for item in collection["items"]
        if isinstance(item, dict) and isinstance(item.get("nodeid"), str)
    }
    for nodeid in current_nodeids:
        item = collection_items[nodeid]
        if nodeid in initial_node_uncertainties:
            reasons = list(initial_node_uncertainties[nodeid])
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": (
                    "thread/subprocess attribution is incomplete during qualification: "
                    + ", ".join(reasons)
                ),
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
                "profiling_uncertainties": reasons,
            }
            continue
        node_uncertainties = effective_node_uncertainties.get(nodeid, ())
        if session_uncertainties or node_uncertainties:
            reasons = sorted(set(session_uncertainties + node_uncertainties))
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": (
                    "thread/subprocess attribution is incomplete during qualification: "
                    + ", ".join(reasons)
                ),
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
                "profiling_uncertainties": reasons,
            }
            continue
        if item.get("parameter_identity_supported") is not True:
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": "collection parameter identity is unsupported",
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
            }
            continue
        item_path = str(item["path"])
        try:
            ancestor_surface = ancestor_surface_cache.get(item_path)
            if ancestor_surface is None:
                ancestor_surface = package_ancestor_import_surface(
                    manifest.root,
                    item_path,
                )
                ancestor_surface_cache[item_path] = ancestor_surface
        except (ConfigurationError, OSError) as exc:
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": f"collection package ancestry is uncertain: {exc}",
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
            }
            continue
        relative = item.get("path")
        if isinstance(relative, str) and relative:
            mutation_rows = raw_filesystem_mutations.get(nodeid, [])
            if not isinstance(mutation_rows, list):
                raise ConfigurationError(
                    "pytest call profiler produced malformed node mutation evidence"
                )
            mutation_scopes: dict[str, set[str]] = {}
            mutation_event_counts: dict[str, int] = {}
            for mutation in mutation_rows:
                if (
                    not isinstance(mutation, list)
                    or len(mutation) != 3
                    or not all(isinstance(value, str) for value in mutation)
                ):
                    raise ConfigurationError(
                        "pytest call profiler produced malformed node mutation evidence"
                    )
                event, scope, _path = mutation
                mutation_scopes.setdefault(event, set()).add(scope)
                mutation_event_counts[event] = mutation_event_counts.get(event, 0) + 1
            proven_ephemeral_mutations = tuple(
                event
                for event in sorted(mutation_event_counts)
                if mutation_scopes[event] == {"ephemeral"}
                for _ in range(mutation_event_counts[event])
            )
            fallback_key = (relative, nodeid, proven_ephemeral_mutations)
            cached_fallback = static_fallback_cache.get(fallback_key)
            if cached_fallback is None:
                cached_fallback = _static_import_fallback(
                    manifest.root,
                    relative,
                    external_modules=external_modules,
                    import_search_roots=import_search_roots,
                    collection_item=item,
                    proven_ephemeral_mutations=proven_ephemeral_mutations,
                    analysis_session=static_fallback_analysis_session,
                )
                static_fallback_cache[fallback_key] = cached_fallback
            (
                collected_source_files,
                collected_source_absences,
                collected_source_uncertainty,
            ) = cached_fallback
        else:
            collected_source_files = ()
            collected_source_absences = ()
            collected_source_uncertainty = (
                "collection item has no stable project path"
            )
        if collected_source_uncertainty is not None:
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": (
                    "collected test source import surface is not fully reviewable: "
                    + collected_source_uncertainty
                ),
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
            }
            continue
        sites = _decode_sites(
            buckets.get(nodeid, []),
            cache=decoded_sites_cache,
        )
        if not sites:
            if collected_source_files:
                merged_fallbacks = tuple(
                    sorted(
                        set(collected_source_files)
                        | set(ancestor_surface.existing_files)
                    )
                )
                merged_absences = tuple(
                    sorted(
                        set(collected_source_absences)
                        | set(ancestor_surface.absent_files)
                    )
                )
                nodes[nodeid] = {
                    "reviewable": True,
                    "fresh_required": False,
                    "reason": None,
                    "selectors": [],
                    "fallback_files": list(merged_fallbacks),
                    "absent_files": list(merged_absences),
                    "observed_call_count": 0,
                    "candidate_fallback_reason": (
                        "no node-attributed project calls were observed; using the test "
                        "module plus recursively resolved local Python imports as a "
                        "conservative whole-file candidate closure"
                    ),
                }
            else:
                reason = "no node-attributed project call sites were observed"
                nodes[nodeid] = {
                    "reviewable": False,
                    "fresh_required": True,
                    "reason": reason,
                    "selectors": [],
                    "fallback_files": [],
                    "absent_files": [],
                }
            continue
        row = _closure_row(
            manifest.root,
            sites,
            analysis_cache=closure_analysis_cache,
            external_modules=external_modules,
            import_search_roots=import_search_roots,
            executed_imports=(
                None
                if executed_imports_by_bucket is None
                else executed_imports_by_bucket.get(nodeid, ())
            ),
            row_cache=closure_row_cache,
        )
        unresolved_imports = tuple(
            str(value) for value in row.pop("unresolved_imports", [])
        )
        if unresolved_imports:
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": (
                    "import resolution is not yet fully reviewable: "
                    + ", ".join(unresolved_imports)
                ),
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
            }
            continue
        collected_positive_files = set(collected_source_files) | set(
            ancestor_surface.existing_files
        )
        if executed_imports_by_bucket is None:
            # Legacy profiles do not prove which imports executed, so retain
            # the historical whole-file recursive fallback.
            row["fallback_files"] = sorted(
                set(row["fallback_files"]) | collected_positive_files
            )
        else:
            # With complete executed-edge evidence, the collected test file is
            # the only true whole-file fallback from the recursive static scan.
            # Project imports that actually ran are already represented by
            # ``row`` (or the shared session closure) as module-state/callable
            # selectors.  Do not reintroduce statically discoverable but
            # unexecuted targets here: doing so would make a dormant import in
            # an unchanged test/module invalidate every node when only the
            # target changes.  Package ancestors are known to execute during
            # collection and retain definition-time projections.  Absence and
            # source-race guards from the complete static scan remain below.
            projected_import_files = {
                path
                for path in ancestor_surface.existing_files
                if path != relative
                and path.endswith(".py")
                and path not in row["fallback_files"]
            }
            row["selectors"] = sorted(
                set(row["selectors"])
                | {f"{path}::@module-state" for path in projected_import_files}
            )
            row["fallback_files"] = sorted(
                set(row["fallback_files"])
                | {
                    path
                    for path in {relative, *ancestor_surface.existing_files}
                    if path == relative or not path.endswith(".py")
                }
            )
        row["absent_files"] = sorted(
            set(row["absent_files"])
            | set(collected_source_absences)
            | set(ancestor_surface.absent_files)
        )
        if not row["selectors"] and not row["fallback_files"]:
            nodes[nodeid] = {
                "reviewable": False,
                "fresh_required": True,
                "reason": "observed calls could not be mapped to a conservative project closure",
                "selectors": [],
                "fallback_files": [],
                "absent_files": [],
            }
            continue
        nodes[nodeid] = {
            "reviewable": True,
            "fresh_required": False,
            "selectors": row["selectors"],
            "fallback_files": row["fallback_files"],
            "absent_files": row["absent_files"],
            "observed_call_count": row["observed_call_count"],
        }

    _hoist_common_reviewed_closure(session_closure, nodes)
    all_absent_import_paths = {
        str(path)
        for path in session_closure.get("absent_files", [])
    }
    for row in nodes.values():
        all_absent_import_paths.update(
            str(path) for path in row.get("absent_files", [])
        )
    validate_absent_import_paths(manifest.root, all_absent_import_paths)
    closure_analysis_cache.revalidate()
    static_fallback_analysis_session.revalidate()
    current_profiler_sha256 = profiler_sha256()
    source_review = build_pytest_source_review(
        manifest.root,
        static_inputs=static_inputs,
        session_closure=session_closure,
        nodes=nodes,
        profiler_sha256=current_profiler_sha256,
        collection_plugin_sha256=collection_plugin_sha256(),
    )
    closure_analysis_cache.revalidate()
    static_fallback_analysis_session.revalidate()
    payload: dict[str, Any] = {
        "schema": _CANDIDATE_SCHEMA,
        "authorizes_reuse": False,
        "candidate_only": True,
        "generated_from_observation": True,
        "task": task_name,
        "base_args": [],
        "targets": list(targets),
        "static_inputs": list(static_inputs),
        "session_closure": session_closure,
        "nodes": nodes,
        "source_review": source_review,
        "baseline": {
            "exit_code": exit_code,
            "collection_node_count": len(current_nodeids),
            "collection_sha256": collection["collection_sha256"],
            "collection_ms": round(collection_ms, 3),
            "collection_fused_with_profile": True,
            "profile_ms": round(profile_ms, 3),
            "initial_profile_ms": round(initial_profile_ms, 3),
        },
        "runtime_identity": runtime,
        "environment_fingerprint": environment_fingerprint,
        "profiler_sha256": current_profiler_sha256,
        "profiling_uncertainties": {
            bucket: sorted(set(kinds))
            for bucket, kinds in sorted(initial_uncertainties.items())
        },
        "thread_observations": {
            bucket: observation
            for bucket, observation in sorted(initial_thread_observations.items())
        },
        "filesystem_mutations": {
            bucket: rows
            for bucket, rows in sorted(initial_filesystem_mutations.items())
        }
        if isinstance(initial_filesystem_mutations, dict)
        else {},
        "import_observations": {
            "external_modules": [list(row) for row in external_module_rows],
            "stable_external_toplevels": [
                list(row) for row in stable_external_toplevel_rows
            ],
            "project_search_roots": list(observed_project_search_roots),
        },
        "executed_imports": (
            raw_executed_imports if isinstance(raw_executed_imports, dict) else {}
        ),
        "executed_import_evidence_complete": executed_imports_by_bucket is not None,
        "uncertainty_isolation": isolation,
        "non_function_fixtures": profile.get("non_function_fixtures", []),
        "review": {
            "required": True,
            "independence_review_sha256": None,
            "reason": (
                "dynamic observation, including completed thread lifecycle evidence, "
                "is candidate evidence, not proof of complete dependency closure, "
                "semantic determinism, or test-node independence"
            ),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["candidate_sha256"] = hashlib.sha256(encoded).hexdigest()
    output = validate_repository_file_path(
        manifest.root,
        output,
        field="pytest candidate output",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    validate_absent_import_paths(manifest.root, all_absent_import_paths)
    closure_analysis_cache.revalidate()
    static_fallback_analysis_session.revalidate()
    # Never write through the existing candidate entry. A repository can
    # preseed an ordinary-looking hard link there; atomic replacement changes
    # only that directory entry and cannot truncate the other linked file.
    atomic_replace_bytes(
        output,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        mode=0o600,
    )
    return payload


def activate_pytest_candidate(
    candidate_path: Path,
    *,
    expected_candidate_sha256: str,
    independence_review_sha256: str,
    output: Path,
) -> dict[str, Any]:
    """Convert an explicitly reviewed candidate into the runtime profile schema.

    This function intentionally requires two user/reviewer supplied immutable
    digests. It never silently promotes generated observation evidence.
    """
    if len(expected_candidate_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_candidate_sha256):
        raise ConfigurationError("expected candidate SHA-256 is malformed")
    if len(independence_review_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in independence_review_sha256):
        raise ConfigurationError("independence review SHA-256 is malformed")
    candidate_path = validate_unlinked_file_path(
        candidate_path,
        field="pytest candidate",
    )
    output = validate_unlinked_file_path(output, field="pytest profile output")
    if output.exists():
        raise ConfigurationError(
            f"pytest profile output already exists; refusing to overwrite: {output}"
        )
    candidate = _read_bounded_json(
        candidate_path,
        label="pytest candidate",
        limit_bytes=_MAX_CANDIDATE_BYTES,
    )
    _validate_bounded_json_value(candidate, label="pytest candidate")
    if not isinstance(candidate, dict) or candidate.get("schema") != _CANDIDATE_SCHEMA:
        raise ConfigurationError("pytest candidate has unsupported schema")
    if candidate.get("authorizes_reuse") is not False or candidate.get("candidate_only") is not True:
        raise ConfigurationError("pytest candidate unexpectedly claims reuse authority")
    claimed = candidate.get("candidate_sha256")
    unsigned = dict(candidate)
    unsigned.pop("candidate_sha256", None)
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
    actual = hashlib.sha256(encoded).hexdigest()
    if claimed != actual or actual != expected_candidate_sha256:
        raise ConfigurationError("pytest candidate digest does not match explicit review target")

    profile = {
        "schema": 1,
        "task": candidate["task"],
        "base_args": candidate["base_args"],
        "targets": candidate["targets"],
        "static_inputs": candidate["static_inputs"],
        "session_closure": {
            "selectors": candidate["session_closure"].get("selectors", []),
            "fallback_files": candidate["session_closure"].get("fallback_files", []),
            "absent_files": candidate["session_closure"].get("absent_files", []),
        },
        "nodes": {
            nodeid: {
                "reviewable": bool(row.get("reviewable")),
                "fresh_required": bool(row.get("fresh_required")),
                "reason": row.get("reason"),
                "selectors": row.get("selectors", []),
                "fallback_files": row.get("fallback_files", []),
                "absent_files": row.get("absent_files", []),
            }
            for nodeid, row in candidate["nodes"].items()
        },
        "independence_review_sha256": independence_review_sha256,
    }
    if isinstance(candidate.get("source_review"), dict):
        profile["source_review"] = candidate["source_review"]
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(profile, indent=2, sort_keys=True) + "\n")
    except FileExistsError as exc:
        raise ConfigurationError(
            f"pytest profile output already exists; refusing to overwrite: {output}"
        ) from exc
    return profile
