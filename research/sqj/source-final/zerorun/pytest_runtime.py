from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .fingerprint import (
    expand_expected_absent_inputs,
    expand_inputs,
    read_stable_file_bytes,
)
from .hermetic_batch_key import FingerprintSession
from .hermetic_store import _load_result_entry
from .hermetic_key import _content_only
from .manifest import load_manifest
from .bounded_json import JsonLimits, loads_bounded_json
from .model import ConfigurationError, Manifest, TaskSpec
from .path_safety import (
    is_link_like as _path_is_link_like,
    private_temporary_directory,
)
from .oci import (
    DOCKER_METADATA_OUTPUT_LIMIT_BYTES,
    DOCKER_EXECUTION_TIMEOUT_SECONDS,
    DOCKER_RESOURCE_ARGS,
    _container_environment_bytes,
    _docker_client_environment,
    _docker_bind_mount,
    _docker_path,
    _git_mask_args,
    _new_container_name,
    _run_bounded_process,
    _run_created_container,
    _validate_task_docker_environment,
    hermetic_environment,
    inspect_runtime,
    reject_sourceless_workspace_bytecode,
)
from .pytest_node_cache import (
    NodeCacheContext,
    initial_fresh_nodeids,
    prepare_node_cache,
    publish_verified_successes,
    verify_node_cache_snapshot,
)
from .pytest_closure import discover_pytest_static_inputs
from .pytest_collection_contract import (
    validate_collection_invocation_contract,
    validate_collection_item_paths,
    validate_reviewed_pytest_targets,
)
from .pytest_node_key import build_pytest_source_review
from .pytest_profile import (
    PytestProfile,
    load_pytest_profile,
    validate_pytest_execution_contract,
)
from .store import Store
from .trust import (
    CacheTrustSession,
    cache_payload_is_trusted,
    manifest_sha256,
    sign_cache_payload,
)


_SUPPORT_MOUNT = "/zerorun-pytest"
_COLLECTION_OUTPUT = f"{_SUPPORT_MOUNT}/collection.json"

_SINGLE_PASS_INPUT = f"{_SUPPORT_MOUNT}/selection-input.json"
_SINGLE_PASS_OUTPUT = f"{_SUPPORT_MOUNT}/selection-output.json"
_COLLECTION_SNAPSHOT = "pytest-collection-v1.json"
_SUITE_RESULT_SCHEMA = "zerorun-pytest-suite-result-v2"
_SUITE_RESULT_DIRECTORY = "pytest-suite-results-v2"

PYTEST_MAX_COLLECTION_NODES = 25_000
PYTEST_MAX_NODEID_CHARS = 8_192
PYTEST_MAX_TOTAL_NODEID_CHARS = 4 * 1024 * 1024
PYTEST_MAX_ITEM_TEXT_CHARS = 65_536
PYTEST_MAX_FIXTURES_PER_ITEM = 4_096
PYTEST_MAX_UNCERTAINTIES_PER_ITEM = 1_024
PYTEST_MAX_JSON_DEPTH = 32
PYTEST_MAX_JSON_MEMBERS = 250_000
PYTEST_EVIDENCE_LIMIT_BYTES = DOCKER_METADATA_OUTPUT_LIMIT_BYTES
PYTEST_REQUEST_LIMIT_BYTES = 2 * DOCKER_METADATA_OUTPUT_LIMIT_BYTES
PYTEST_SNAPSHOT_LIMIT_BYTES = DOCKER_METADATA_OUTPUT_LIMIT_BYTES + 1024 * 1024
_GIT_DIFF_LIMIT_BYTES = DOCKER_METADATA_OUTPUT_LIMIT_BYTES
_GIT_REVISION_TIMEOUT_SECONDS = 10.0

# Process-local only. A Codex MCP server is long-lived, while a new process
# simply falls back to the canonical full fingerprint path. Entries are created
# only after a fully successful reviewed-node verification.
_VERIFIED_ACTION_SNAPSHOTS: dict[tuple[str, str], dict[str, Any]] = {}
_SUITE_ENGINE_ACTION_SHA256: str | None = None

_SINGLE_PASS_PLUGIN = r'''from __future__ import annotations
import enum
import hashlib
import inspect
import json
import math
import os
import stat
from pathlib import Path


_SELECTION_PAYLOAD = None
_EXECUTED_NODEIDS = []
_MAX_COLLECTION_NODES = 25000
_MAX_NODEID_CHARS = 8192
_MAX_TOTAL_NODEID_CHARS = 4 * 1024 * 1024
_MAX_VALUE_CHARS = 65536
_MAX_CONTAINER_ITEMS = 4096
_MAX_CANONICAL_DEPTH = 32
_MAX_CANONICAL_VALUES = 16384
_MAX_ITEM_BYTES = 256 * 1024
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_INVOCATION_ARGS = 4096
_MAX_INVOCATION_CHARS = 4 * 1024 * 1024

# Capture the concrete pathlib implementation and its operations before any
# repository plugin is imported.  Evidence paths are always expressed against
# pytest's process cwd (/workspace), never a mutable/nested config.rootpath.
_PATH_TYPE = type(Path.cwd())
_PATH_RESOLVE = Path.resolve
_PATH_RELATIVE_TO = Path.relative_to
_PATH_AS_POSIX = Path.as_posix
_PATH_IS_ABSOLUTE = Path.is_absolute
_PATH_JOINPATH = Path.joinpath
_OS_LSTAT = os.lstat
_STAT_ISLNK = stat.S_ISLNK
_STAT_ISREG = stat.S_ISREG
_INVOCATION_CWD = _PATH_RESOLVE(Path.cwd(), strict=True)


def _workspace_relative_path(raw):
    if type(raw) is not _PATH_TYPE:
        return None
    try:
        resolved = _PATH_RESOLVE(raw, strict=True)
        relative = _PATH_RELATIVE_TO(resolved, _INVOCATION_CWD)
        rendered = _PATH_AS_POSIX(relative)
    except (OSError, RuntimeError, ValueError):
        return None
    if rendered == ".":
        return "."
    if (
        not rendered
        or rendered.startswith("/")
        or "\\" in rendered
        or any(part in {"", ".", ".."} for part in rendered.split("/"))
    ):
        return None
    return rendered


def _regular_workspace_file(raw):
    if type(raw) is not _PATH_TYPE:
        return None
    try:
        lexical = (
            raw
            if _PATH_IS_ABSOLUTE(raw)
            else _PATH_JOINPATH(_INVOCATION_CWD, raw)
        )
        lexical_relative = _PATH_RELATIVE_TO(lexical, _INVOCATION_CWD)
        lexical_rendered = _PATH_AS_POSIX(lexical_relative)
        if (
            not lexical_rendered
            or lexical_rendered == "."
            or lexical_rendered.startswith("/")
            or "\\" in lexical_rendered
            or any(
                part in {"", ".", ".."}
                for part in lexical_rendered.split("/")
            )
        ):
            return None
        cursor = _INVOCATION_CWD
        final_mode = None
        for part in lexical_rendered.split("/"):
            cursor = _PATH_JOINPATH(cursor, part)
            final_mode = _OS_LSTAT(cursor).st_mode
            if _STAT_ISLNK(final_mode):
                return None
        if final_mode is None or not _STAT_ISREG(final_mode):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return _workspace_relative_path(raw)


def _invocation(config):
    rootpath = _workspace_relative_path(getattr(config, "rootpath", None))
    raw_args = getattr(config, "args", None)
    option = getattr(config, "option", None)
    pyargs = getattr(option, "pyargs", None)
    try:
        raw_addopts = config.getini("addopts")
    except Exception as exc:
        raise RuntimeError("ZeroRun cannot attest pytest addopts") from exc
    if (
        rootpath is None
        or not isinstance(raw_args, list)
        or not isinstance(raw_addopts, list)
        or type(pyargs) is not bool
        or len(raw_args) > _MAX_INVOCATION_ARGS
        or len(raw_addopts) > _MAX_INVOCATION_ARGS
    ):
        raise RuntimeError("ZeroRun pytest invocation evidence is unsupported")
    args = list(raw_args)
    addopts = list(raw_addopts)
    if (
        not all(
            isinstance(value, str)
            and value
            and len(value) <= _MAX_NODEID_CHARS
            and "\x00" not in value
            for value in args
        )
        or not all(
            isinstance(value, str)
            and value
            and len(value) <= _MAX_NODEID_CHARS
            and "\x00" not in value
            for value in addopts
        )
        or sum(map(len, args)) + sum(map(len, addopts))
        > _MAX_INVOCATION_CHARS
    ):
        raise RuntimeError("ZeroRun pytest invocation evidence exceeds its boundary")
    return {
        "rootpath": rootpath,
        "args": args,
        "pyargs": pyargs,
        "addopts": addopts,
    }


def _unsupported(reason, value):
    cls = type(value)
    return {"supported": False, "reason": reason, "type": "%s.%s" % (cls.__module__, cls.__qualname__)}


def _canonical(value, seen=None, depth=0, budget=None):
    if seen is None:
        seen = set()
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > _MAX_CANONICAL_DEPTH or budget[0] > _MAX_CANONICAL_VALUES:
        return _unsupported("parameter exceeds structural complexity limits", value)
    if value is None:
        return {"supported": True, "value": ["none", None]}
    if isinstance(value, bool):
        return {"supported": True, "value": ["bool", value]}
    if isinstance(value, int):
        if value.bit_length() > _MAX_VALUE_CHARS:
            return _unsupported("integer parameter is oversized", value)
        return {"supported": True, "value": ["int", value]}
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_CHARS:
            return _unsupported("string parameter is oversized", value)
        return {"supported": True, "value": ["str", value]}
    if isinstance(value, float):
        if not math.isfinite(value):
            return _unsupported("non-finite float parameter", value)
        return {"supported": True, "value": ["float", value]}
    if isinstance(value, bytes):
        if len(value) > _MAX_VALUE_CHARS:
            return _unsupported("bytes parameter is oversized", value)
        return {"supported": True, "value": ["bytes", value.hex()]}
    if isinstance(value, type):
        if value.__module__ != "builtins":
            return _unsupported(
                "non-builtin type parameter requires source-bound canonicalizer",
                value,
            )
        return {
            "supported": True,
            "value": ["builtin-type", value.__module__, value.__qualname__],
        }
    identity = id(value)
    if identity in seen:
        return _unsupported("cyclic parameter value", value)
    if isinstance(value, tuple):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("tuple parameter is oversized", value)
        seen.add(identity)
        try:
            rows = [_canonical(item, seen, depth + 1, budget) for item in value]
        finally:
            seen.remove(identity)
        if not all(row.get("supported") is True for row in rows):
            return _unsupported("tuple contains unsupported parameter value", value)
        return {"supported": True, "value": ["tuple", [row["value"] for row in rows]]}
    if isinstance(value, list):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("list parameter is oversized", value)
        seen.add(identity)
        try:
            rows = [_canonical(item, seen, depth + 1, budget) for item in value]
        finally:
            seen.remove(identity)
        if not all(row.get("supported") is True for row in rows):
            return _unsupported("list contains unsupported parameter value", value)
        return {"supported": True, "value": ["list", [row["value"] for row in rows]]}
    if isinstance(value, (set, frozenset)):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("set parameter is oversized", value)
        seen.add(identity)
        try:
            rows = [_canonical(item, seen, depth + 1, budget) for item in value]
        finally:
            seen.remove(identity)
        if not all(row.get("supported") is True for row in rows):
            return _unsupported(
                "set contains unsupported parameter value",
                value,
            )
        ordered = sorted(
            (row["value"] for row in rows),
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":")
            ),
        )
        return {
            "supported": True,
            "value": [
                "frozenset" if isinstance(value, frozenset) else "set",
                ordered,
            ],
        }
    if isinstance(value, dict):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("dict parameter is oversized", value)
        seen.add(identity)
        try:
            rows = []
            for key, item in value.items():
                key_row = _canonical(key, seen, depth + 1, budget)
                value_row = _canonical(item, seen, depth + 1, budget)
                if (
                    key_row.get("supported") is not True
                    or value_row.get("supported") is not True
                ):
                    return _unsupported(
                        "dict contains unsupported parameter value",
                        value,
                    )
                rows.append([key_row["value"], value_row["value"]])
        finally:
            seen.remove(identity)
        rows.sort(
            key=lambda item: json.dumps(
                item[0], sort_keys=True, separators=(",", ":")
            )
        )
        return {"supported": True, "value": ["dict", rows]}
    cls = type(value)
    try:
        source = inspect.getsourcefile(cls)
    except (OSError, TypeError):
        source = None
    if source:
        try:
            root = Path.cwd().resolve()
            source_path = Path(source).resolve()
            relative = source_path.relative_to(root).as_posix()
        except (OSError, ValueError):
            relative = None
        if relative is not None and source_path.is_file():
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            class_identity = [
                "project-class",
                cls.__module__,
                cls.__qualname__,
                relative,
                source_sha256,
            ]
            if isinstance(value, enum.Enum):
                return {
                    "supported": True,
                    "value": ["project-enum", class_identity, value.name],
                }
            state = getattr(value, "__dict__", None)
            slot_names = []
            for base_cls in cls.__mro__:
                raw_slots = base_cls.__dict__.get("__slots__", ())
                if isinstance(raw_slots, str):
                    raw_slots = (raw_slots,)
                if isinstance(raw_slots, (tuple, list)):
                    slot_names.extend(
                        slot
                        for slot in raw_slots
                        if isinstance(slot, str)
                        and slot not in {"__dict__", "__weakref__"}
                    )
            if any(hasattr(value, slot) for slot in slot_names):
                return _unsupported(
                    "project parameter object has slot state requiring an explicit canonicalizer",
                    value,
                )
            if isinstance(state, dict):
                seen.add(identity)
                try:
                    state_row = _canonical(state, seen, depth + 1, budget)
                finally:
                    seen.remove(identity)
                if state_row.get("supported") is True:
                    return {
                        "supported": True,
                        "value": ["project-object", class_identity, state_row["value"]],
                    }
    return _unsupported("parameter type requires explicit deterministic canonicalizer", value)


def _item_path(item, config):
    raw = getattr(item, "path", None)
    return _regular_workspace_file(raw)


def _item_row(item, config):
    nodeid = str(item.nodeid)
    if not nodeid or len(nodeid) > _MAX_NODEID_CHARS or "\x00" in nodeid:
        raise RuntimeError("ZeroRun pytest node id exceeds the evidence boundary")
    callspec = getattr(item, "callspec", None)
    params = {}
    supported = True
    uncertainty = []
    if callspec is not None:
        raw = getattr(callspec, "params", {})
        if not isinstance(raw, dict):
            supported = False
            uncertainty.append("callspec.params is not a dict")
        else:
            for name in sorted(raw):
                row = _canonical(raw[name])
                params[name] = row
                if row.get("supported") is not True:
                    supported = False
                    uncertainty.append("%s: %s" % (name, row.get("reason", "unsupported parameter")))
    row = {
        "nodeid": nodeid,
        "item_type": "%s.%s" % (type(item).__module__, type(item).__qualname__),
        "path": _item_path(item, config),
        "name": str(getattr(item, "name", "")),
        "fixturenames": sorted(set(str(name) for name in (getattr(item, "fixturenames", ()) or ()) if isinstance(name, str))),
        "callspec_params": params,
        "parameter_identity_supported": supported,
        "parameter_identity_uncertainty": sorted(set(uncertainty)),
    }
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_ITEM_BYTES:
        raise RuntimeError("ZeroRun pytest item evidence exceeds the per-item boundary")
    row["identity_sha256"] = hashlib.sha256(encoded).hexdigest()
    return row


def _collection(items, config):
    if len(items) > _MAX_COLLECTION_NODES:
        raise RuntimeError("ZeroRun pytest collection exceeds the node-count boundary")
    if sum(len(str(item.nodeid)) for item in items) > _MAX_TOTAL_NODEID_CHARS:
        raise RuntimeError("ZeroRun pytest collection exceeds the node-id boundary")
    rows = [_item_row(item, config) for item in items]
    payload = {
        "invocation": _invocation(config),
        "items": rows,
        "nodeids": [row["nodeid"] for row in rows],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("ZeroRun pytest collection evidence exceeds the byte boundary")
    payload["collection_sha256"] = hashlib.sha256(encoded).hexdigest()
    return payload


def _write_selection_output(payload):
    output_path = os.environ.get("ZERORUN_SELECTION_OUTPUT")
    if not output_path:
        raise RuntimeError("ZeroRun single-pass output path is missing")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("ZeroRun pytest selection evidence exceeds the byte boundary")
    Path(output_path).write_bytes(encoded)


def pytest_collection_modifyitems(config, items):
    global _SELECTION_PAYLOAD
    input_path = os.environ.get("ZERORUN_SELECTION_INPUT")
    output_path = os.environ.get("ZERORUN_SELECTION_OUTPUT")
    if not input_path or not output_path:
        raise RuntimeError("ZeroRun single-pass selection paths are missing")
    encoded_request = Path(input_path).read_bytes()
    if len(encoded_request) > _MAX_REQUEST_BYTES:
        raise RuntimeError("ZeroRun pytest selection request exceeds the byte boundary")
    request = json.loads(encoded_request.decode("utf-8"))
    current = _collection(list(items), config)
    baseline = request.get("baseline_collection")
    requested = request.get("fresh_nodeids", [])
    current_ids = current["nodeids"]
    current_set = set(current_ids)

    baseline_ids = baseline.get("nodeids") if isinstance(baseline, dict) else None
    baseline_items = baseline.get("items") if isinstance(baseline, dict) else None
    baseline_valid = (
        isinstance(baseline_ids, list)
        and isinstance(baseline_items, list)
        and len(baseline_ids) == len(baseline_items)
        and all(isinstance(nodeid, str) and nodeid for nodeid in baseline_ids)
        and len(baseline_ids) == len(set(baseline_ids))
        and all(
            isinstance(row, dict)
            and row.get("nodeid") == nodeid
            and isinstance(row.get("identity_sha256"), str)
            for nodeid, row in zip(baseline_ids, baseline_items)
        )
    )
    requested_valid = (
        baseline_valid
        and isinstance(requested, list)
        and all(isinstance(nodeid, str) for nodeid in requested)
        and len(requested) == len(set(requested))
        and set(requested).issubset(set(baseline_ids))
    )
    exact_baseline = (
        baseline_valid
        and baseline.get("collection_sha256") == current.get("collection_sha256")
        and baseline_ids == current_ids
    )
    if requested_valid:
        requested_set = set(requested)
        proposed_reuse = set(baseline_ids) - requested_set
        baseline_by_node = {
            row["nodeid"]: row for row in baseline_items
        }
        current_by_node = {
            row["nodeid"]: row for row in current["items"]
        }
        shared = set(baseline_ids).intersection(current_set)
        baseline_shared_order = [
            nodeid for nodeid in baseline_ids if nodeid in shared
        ]
        current_shared_order = [
            nodeid for nodeid in current_ids if nodeid in shared
        ]
        order_preserved = baseline_shared_order == current_shared_order
        aligned_reuse = {
            nodeid
            for nodeid in proposed_reuse.intersection(current_set)
            if current_by_node[nodeid].get("identity_sha256")
            == baseline_by_node[nodeid].get("identity_sha256")
        }
        if exact_baseline:
            fresh_set = requested_set
            mode = "single-pass-incremental"
            reason = None
        elif order_preserved and aligned_reuse:
            # A collection edit is not itself a reason to discard unrelated
            # verified work.  Only nodes proposed as cache hits by the host,
            # still present in the same relative order, and carrying the exact
            # structural item identity may be omitted.  Every added, changed,
            # unknown, or explicitly requested node executes in this process.
            fresh_set = current_set - aligned_reuse
            mode = "per-node-aligned-incremental"
            reason = None
        else:
            fresh_set = current_set
            mode = "whole-target-fresh"
            reason = (
                "collection alignment found no order-preserving structurally identical reuse"
            )
    else:
        fresh_set = current_set
        mode = "whole-target-fresh"
        reason = "baseline collection or requested selection was invalid"

    fresh_items = [item for item in items if str(item.nodeid) in fresh_set]
    reused_items = [item for item in items if str(item.nodeid) not in fresh_set]
    if reused_items:
        config.hook.pytest_deselected(items=reused_items)
    items[:] = fresh_items
    payload = {
        "mode": mode,
        "reason": reason,
        "collection": current,
        "fresh_nodeids": [str(item.nodeid) for item in fresh_items],
        "reused_nodeids": [str(item.nodeid) for item in reused_items],
        "executed_nodeids": [],
    }
    _SELECTION_PAYLOAD = payload
    _write_selection_output(payload)


def pytest_runtest_logstart(nodeid, location):
    _EXECUTED_NODEIDS.append(str(nodeid))


def pytest_sessionfinish(session, exitstatus):
    if _SELECTION_PAYLOAD is not None:
        _SELECTION_PAYLOAD["executed_nodeids"] = list(_EXECUTED_NODEIDS)
        _write_selection_output(_SELECTION_PAYLOAD)
'''


_COLLECTION_PLUGIN = r'''from __future__ import annotations
import enum
import hashlib
import inspect
import json
import math
import os
import stat
from pathlib import Path


_MAX_COLLECTION_NODES = 25000
_MAX_NODEID_CHARS = 8192
_MAX_TOTAL_NODEID_CHARS = 4 * 1024 * 1024
_MAX_VALUE_CHARS = 65536
_MAX_CONTAINER_ITEMS = 4096
_MAX_CANONICAL_DEPTH = 32
_MAX_CANONICAL_VALUES = 16384
_MAX_ITEM_BYTES = 256 * 1024
_MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
_MAX_INVOCATION_ARGS = 4096
_MAX_INVOCATION_CHARS = 4 * 1024 * 1024

_PATH_TYPE = type(Path.cwd())
_PATH_RESOLVE = Path.resolve
_PATH_RELATIVE_TO = Path.relative_to
_PATH_AS_POSIX = Path.as_posix
_PATH_IS_ABSOLUTE = Path.is_absolute
_PATH_JOINPATH = Path.joinpath
_OS_LSTAT = os.lstat
_STAT_ISLNK = stat.S_ISLNK
_STAT_ISREG = stat.S_ISREG
_INVOCATION_CWD = _PATH_RESOLVE(Path.cwd(), strict=True)


def _workspace_relative_path(raw):
    if type(raw) is not _PATH_TYPE:
        return None
    try:
        resolved = _PATH_RESOLVE(raw, strict=True)
        relative = _PATH_RELATIVE_TO(resolved, _INVOCATION_CWD)
        rendered = _PATH_AS_POSIX(relative)
    except (OSError, RuntimeError, ValueError):
        return None
    if rendered == ".":
        return "."
    if (
        not rendered
        or rendered.startswith("/")
        or "\\" in rendered
        or any(part in {"", ".", ".."} for part in rendered.split("/"))
    ):
        return None
    return rendered


def _regular_workspace_file(raw):
    if type(raw) is not _PATH_TYPE:
        return None
    try:
        lexical = (
            raw
            if _PATH_IS_ABSOLUTE(raw)
            else _PATH_JOINPATH(_INVOCATION_CWD, raw)
        )
        lexical_relative = _PATH_RELATIVE_TO(lexical, _INVOCATION_CWD)
        lexical_rendered = _PATH_AS_POSIX(lexical_relative)
        if (
            not lexical_rendered
            or lexical_rendered == "."
            or lexical_rendered.startswith("/")
            or "\\" in lexical_rendered
            or any(
                part in {"", ".", ".."}
                for part in lexical_rendered.split("/")
            )
        ):
            return None
        cursor = _INVOCATION_CWD
        final_mode = None
        for part in lexical_rendered.split("/"):
            cursor = _PATH_JOINPATH(cursor, part)
            final_mode = _OS_LSTAT(cursor).st_mode
            if _STAT_ISLNK(final_mode):
                return None
        if final_mode is None or not _STAT_ISREG(final_mode):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return _workspace_relative_path(raw)


def _invocation(config):
    rootpath = _workspace_relative_path(getattr(config, "rootpath", None))
    raw_args = getattr(config, "args", None)
    option = getattr(config, "option", None)
    pyargs = getattr(option, "pyargs", None)
    try:
        raw_addopts = config.getini("addopts")
    except Exception as exc:
        raise RuntimeError("ZeroRun cannot attest pytest addopts") from exc
    if (
        rootpath is None
        or not isinstance(raw_args, list)
        or not isinstance(raw_addopts, list)
        or type(pyargs) is not bool
        or len(raw_args) > _MAX_INVOCATION_ARGS
        or len(raw_addopts) > _MAX_INVOCATION_ARGS
    ):
        raise RuntimeError("ZeroRun pytest invocation evidence is unsupported")
    args = list(raw_args)
    addopts = list(raw_addopts)
    if (
        not all(
            isinstance(value, str)
            and value
            and len(value) <= _MAX_NODEID_CHARS
            and "\x00" not in value
            for value in args
        )
        or not all(
            isinstance(value, str)
            and value
            and len(value) <= _MAX_NODEID_CHARS
            and "\x00" not in value
            for value in addopts
        )
        or sum(map(len, args)) + sum(map(len, addopts))
        > _MAX_INVOCATION_CHARS
    ):
        raise RuntimeError("ZeroRun pytest invocation evidence exceeds its boundary")
    return {
        "rootpath": rootpath,
        "args": args,
        "pyargs": pyargs,
        "addopts": addopts,
    }


def _unsupported(reason, value):
    cls = type(value)
    return {"supported": False, "reason": reason, "type": "%s.%s" % (cls.__module__, cls.__qualname__)}


def _canonical(value, seen=None, depth=0, budget=None):
    if seen is None:
        seen = set()
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > _MAX_CANONICAL_DEPTH or budget[0] > _MAX_CANONICAL_VALUES:
        return _unsupported("parameter exceeds structural complexity limits", value)
    if value is None:
        return {"supported": True, "value": ["none", None]}
    if isinstance(value, bool):
        return {"supported": True, "value": ["bool", value]}
    if isinstance(value, int):
        if value.bit_length() > _MAX_VALUE_CHARS:
            return _unsupported("integer parameter is oversized", value)
        return {"supported": True, "value": ["int", value]}
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_CHARS:
            return _unsupported("string parameter is oversized", value)
        return {"supported": True, "value": ["str", value]}
    if isinstance(value, float):
        if not math.isfinite(value):
            return _unsupported("non-finite float parameter", value)
        return {"supported": True, "value": ["float", value]}
    if isinstance(value, bytes):
        if len(value) > _MAX_VALUE_CHARS:
            return _unsupported("bytes parameter is oversized", value)
        return {"supported": True, "value": ["bytes", value.hex()]}
    if isinstance(value, type):
        if value.__module__ != "builtins":
            return _unsupported(
                "non-builtin type parameter requires source-bound canonicalizer",
                value,
            )
        return {
            "supported": True,
            "value": ["builtin-type", value.__module__, value.__qualname__],
        }
    identity = id(value)
    if identity in seen:
        return _unsupported("cyclic parameter value", value)
    if isinstance(value, tuple):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("tuple parameter is oversized", value)
        seen.add(identity)
        try:
            rows = [_canonical(item, seen, depth + 1, budget) for item in value]
        finally:
            seen.remove(identity)
        if not all(row.get("supported") is True for row in rows):
            return _unsupported("tuple contains unsupported parameter value", value)
        return {"supported": True, "value": ["tuple", [row["value"] for row in rows]]}
    if isinstance(value, list):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("list parameter is oversized", value)
        seen.add(identity)
        try:
            rows = [_canonical(item, seen, depth + 1, budget) for item in value]
        finally:
            seen.remove(identity)
        if not all(row.get("supported") is True for row in rows):
            return _unsupported("list contains unsupported parameter value", value)
        return {"supported": True, "value": ["list", [row["value"] for row in rows]]}
    if isinstance(value, (set, frozenset)):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("set parameter is oversized", value)
        seen.add(identity)
        try:
            rows = [_canonical(item, seen, depth + 1, budget) for item in value]
        finally:
            seen.remove(identity)
        if not all(row.get("supported") is True for row in rows):
            return _unsupported(
                "set contains unsupported parameter value",
                value,
            )
        ordered = sorted(
            (row["value"] for row in rows),
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":")
            ),
        )
        return {
            "supported": True,
            "value": [
                "frozenset" if isinstance(value, frozenset) else "set",
                ordered,
            ],
        }
    if isinstance(value, dict):
        if len(value) > _MAX_CONTAINER_ITEMS:
            return _unsupported("dict parameter is oversized", value)
        seen.add(identity)
        try:
            rows = []
            for key, item in value.items():
                key_row = _canonical(key, seen, depth + 1, budget)
                value_row = _canonical(item, seen, depth + 1, budget)
                if (
                    key_row.get("supported") is not True
                    or value_row.get("supported") is not True
                ):
                    return _unsupported(
                        "dict contains unsupported parameter value",
                        value,
                    )
                rows.append([key_row["value"], value_row["value"]])
        finally:
            seen.remove(identity)
        rows.sort(
            key=lambda item: json.dumps(
                item[0], sort_keys=True, separators=(",", ":")
            )
        )
        return {"supported": True, "value": ["dict", rows]}
    cls = type(value)
    try:
        source = inspect.getsourcefile(cls)
    except (OSError, TypeError):
        source = None
    if source:
        try:
            root = Path.cwd().resolve()
            source_path = Path(source).resolve()
            relative = source_path.relative_to(root).as_posix()
        except (OSError, ValueError):
            relative = None
        if relative is not None and source_path.is_file():
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            class_identity = [
                "project-class",
                cls.__module__,
                cls.__qualname__,
                relative,
                source_sha256,
            ]
            if isinstance(value, enum.Enum):
                return {
                    "supported": True,
                    "value": ["project-enum", class_identity, value.name],
                }
            state = getattr(value, "__dict__", None)
            slot_names = []
            for base_cls in cls.__mro__:
                raw_slots = base_cls.__dict__.get("__slots__", ())
                if isinstance(raw_slots, str):
                    raw_slots = (raw_slots,)
                if isinstance(raw_slots, (tuple, list)):
                    slot_names.extend(
                        slot
                        for slot in raw_slots
                        if isinstance(slot, str)
                        and slot not in {"__dict__", "__weakref__"}
                    )
            if any(hasattr(value, slot) for slot in slot_names):
                return _unsupported(
                    "project parameter object has slot state requiring an explicit canonicalizer",
                    value,
                )
            if isinstance(state, dict):
                seen.add(identity)
                try:
                    state_row = _canonical(state, seen, depth + 1, budget)
                finally:
                    seen.remove(identity)
                if state_row.get("supported") is True:
                    return {
                        "supported": True,
                        "value": ["project-object", class_identity, state_row["value"]],
                    }
    return _unsupported("parameter type requires explicit deterministic canonicalizer", value)


def _item_path(item, config):
    raw = getattr(item, "path", None)
    return _regular_workspace_file(raw)


def _item_row(item, config):
    nodeid = str(item.nodeid)
    if not nodeid or len(nodeid) > _MAX_NODEID_CHARS or "\x00" in nodeid:
        raise RuntimeError("ZeroRun pytest node id exceeds the evidence boundary")
    callspec = getattr(item, "callspec", None)
    params = {}
    supported = True
    uncertainty = []
    if callspec is not None:
        raw = getattr(callspec, "params", {})
        if not isinstance(raw, dict):
            supported = False
            uncertainty.append("callspec.params is not a dict")
        else:
            for name in sorted(raw):
                row = _canonical(raw[name])
                params[name] = row
                if row.get("supported") is not True:
                    supported = False
                    uncertainty.append("%s: %s" % (name, row.get("reason", "unsupported parameter")))
    row = {
        "nodeid": nodeid,
        "item_type": "%s.%s" % (type(item).__module__, type(item).__qualname__),
        "path": _item_path(item, config),
        "name": str(getattr(item, "name", "")),
        "fixturenames": sorted(set(str(name) for name in (getattr(item, "fixturenames", ()) or ()) if isinstance(name, str))),
        "callspec_params": params,
        "parameter_identity_supported": supported,
        "parameter_identity_uncertainty": sorted(set(uncertainty)),
    }
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_ITEM_BYTES:
        raise RuntimeError("ZeroRun pytest item evidence exceeds the per-item boundary")
    row["identity_sha256"] = hashlib.sha256(encoded).hexdigest()
    return row


def pytest_collection_finish(session):
    output = os.environ.get("ZERORUN_COLLECTION_OUTPUT")
    if not output:
        return
    if len(session.items) > _MAX_COLLECTION_NODES:
        raise RuntimeError("ZeroRun pytest collection exceeds the node-count boundary")
    if sum(len(str(item.nodeid)) for item in session.items) > _MAX_TOTAL_NODEID_CHARS:
        raise RuntimeError("ZeroRun pytest collection exceeds the node-id boundary")
    rows = [_item_row(item, session.config) for item in session.items]
    payload = {
        "invocation": _invocation(session.config),
        "items": rows,
        "nodeids": [row["nodeid"] for row in rows],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("ZeroRun pytest collection evidence exceeds the byte boundary")
    payload["collection_sha256"] = hashlib.sha256(encoded).hexdigest()
    final = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(final) > _MAX_EVIDENCE_BYTES:
        raise RuntimeError("ZeroRun pytest collection evidence exceeds the byte boundary")
    Path(output).write_bytes(final)
'''


def collection_plugin_sha256() -> str:
    return hashlib.sha256(_COLLECTION_PLUGIN.encode("utf-8")).hexdigest()


def _prepare_mountpoints(root: Path, *, store: Store | None = None) -> None:
    root = root.resolve(strict=True)
    reject_sourceless_workspace_bytecode(root)
    # Validate both ordinary checkouts and Git's regular-file linked-worktree
    # marker. The resulting Docker mount always masks the marker itself.
    _git_mask_args(root)
    if store is not None and store.root != root:
        raise ConfigurationError("pytest store cannot span project roots")
    store = store or Store(root)
    store.ensure()
    state = store.state
    if os.name != "nt":
        state.chmod(0o700)


_ENV_FILE_PLACEHOLDER = "__ZERORUN_DOCKER_ENV_FILE__"


def _docker_base(
    manifest: Manifest,
    task: TaskSpec,
    *,
    runtime_identity: dict[str, Any],
    environment: dict[str, str],
) -> tuple[list[str], dict[str, str], bytes]:
    _validate_task_docker_environment(task)
    # Repeat the sourceless-bytecode preflight immediately before every pytest
    # container construction. The outer request check cannot cover a file
    # created during a slow trust/collection phase.
    reject_sourceless_workspace_bytecode(manifest.root)
    docker = _docker_path(manifest.root)
    container_name = _new_container_name("pytest")
    image = str(runtime_identity["requested_image"])
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
        "--workdir",
        "/workspace",
    ]
    command.extend(["--env-file", _ENV_FILE_PLACEHOLDER])
    docker_environment = _docker_client_environment(docker)
    command.append(image)
    return command, docker_environment, _container_environment_bytes(task, environment)


def _run_docker_process(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    container_environment: bytes,
) -> subprocess.CompletedProcess[bytes]:
    try:
        name = command[command.index("--name") + 1]
    except (ValueError, IndexError) as exc:
        raise ConfigurationError(
            "internal Docker command is missing its exact container name"
        ) from exc
    try:
        environment_file_index = command.index(_ENV_FILE_PLACEHOLDER)
    except ValueError as exc:
        raise ConfigurationError(
            "internal pytest Docker command is missing its environment slot"
        ) from exc
    try:
        process = _run_created_container(
            command,
            docker=command[0],
            container_name=name,
            cwd=cwd,
            environment=environment,
            execution_timeout_seconds=DOCKER_EXECUTION_TIMEOUT_SECONDS,
            operation_label="pytest",
            container_environment=container_environment,
            environment_repository_root=cwd,
            environment_file_index=environment_file_index,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"pytest Docker process could not start: {exc}"
        ) from exc
    return process


def _pytest_command(task: TaskSpec, profile: PytestProfile) -> list[str]:
    validate_reviewed_pytest_targets(
        profile.targets,
        field="generic pytest adapter v1 targets",
    )
    command = validate_pytest_execution_contract(
        task,
        base_args=profile.base_args,
        field="generic pytest adapter v1",
    )
    return list(command)


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_link_like(path: Path) -> bool:
    try:
        return _path_is_link_like(path)
    except OSError as exc:
        raise ConfigurationError(
            f"cannot safely inspect pytest evidence path {path}: {exc}"
        ) from exc


def _read_bounded_json(
    path: Path,
    *,
    label: str,
    limit_bytes: int,
) -> object:
    """Read one stable regular JSON file without following an evidence link."""

    if _is_link_like(path):
        raise ConfigurationError(f"{label} must not be a symbolic link or junction")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ConfigurationError(f"{label} is unreadable: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ConfigurationError(f"{label} must be a regular file")
        if before.st_size > limit_bytes:
            raise ConfigurationError(
                f"{label} exceeds the {limit_bytes}-byte evidence limit"
            )
        chunks: list[bytes] = []
        remaining = limit_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(raw) > limit_bytes:
        raise ConfigurationError(
            f"{label} exceeds the {limit_bytes}-byte evidence limit"
        )
    if (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_ctime_ns", None),
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_ctime_ns", None),
    ) or len(raw) != before.st_size:
        raise ConfigurationError(f"{label} changed while it was being read")
    try:
        current = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ConfigurationError(f"{label} changed while it was being read") from exc
    if (
        _is_link_like(path)
        or not stat.S_ISREG(current.st_mode)
        or (
            current.st_dev,
            current.st_ino,
            current.st_mode,
            current.st_size,
            current.st_mtime_ns,
        )
        != (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
    ):
        raise ConfigurationError(f"{label} changed while it was being read")
    return loads_bounded_json(
        raw,
        label=label,
        limits=JsonLimits(
            max_bytes=limit_bytes,
            max_depth=PYTEST_MAX_JSON_DEPTH,
            max_values=PYTEST_MAX_JSON_MEMBERS,
            max_object_members=PYTEST_MAX_JSON_MEMBERS,
            max_structural_tokens=PYTEST_MAX_JSON_MEMBERS * 2,
            max_number_chars=256,
            max_string_chars=PYTEST_MAX_ITEM_TEXT_CHARS,
            max_total_string_chars=limit_bytes,
        ),
    )


def _is_bounded_nonnegative_number(
    value: object,
    *,
    maximum: float = 1_000_000_000_000_000.0,
) -> bool:
    if type(value) not in {int, float}:
        return False
    try:
        converted = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(converted) and 0.0 <= converted <= maximum


def _write_bounded_json(
    path: Path,
    payload: object,
    *,
    label: str,
    limit_bytes: int,
) -> None:
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConfigurationError(f"{label} cannot be encoded safely: {exc}") from exc
    if len(encoded) > limit_bytes:
        raise ConfigurationError(
            f"{label} exceeds the {limit_bytes}-byte evidence limit"
        )
    path.write_bytes(encoded)


def _prepare_evidence_output(path: Path) -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o666,
        )
    except OSError as exc:
        raise ConfigurationError(
            f"could not create private pytest evidence output {path}: {exc}"
        ) from exc
    os.close(descriptor)
    if os.name != "nt":
        path.chmod(0o666)


def _validate_nodeid_sequence(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > PYTEST_MAX_COLLECTION_NODES:
        raise ConfigurationError(f"{label} has too many node ids or is not an array")
    if (
        any(
            not isinstance(nodeid, str)
            or not nodeid
            or len(nodeid) > PYTEST_MAX_NODEID_CHARS
            or "\x00" in nodeid
            for nodeid in value
        )
        or len(value) != len(set(value))
        or sum(len(nodeid) for nodeid in value) > PYTEST_MAX_TOTAL_NODEID_CHARS
    ):
        raise ConfigurationError(f"{label} violates pytest node-id limits")
    return value


def _validate_bounded_json_value(
    value: object,
    *,
    label: str,
    depth: int = 0,
    budget: list[int] | None = None,
) -> None:
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > PYTEST_MAX_JSON_DEPTH or budget[0] > PYTEST_MAX_JSON_MEMBERS:
        raise ConfigurationError(f"{label} exceeds structural complexity limits")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationError(f"{label} contains a non-finite number")
        return
    if isinstance(value, str):
        if len(value) > PYTEST_MAX_ITEM_TEXT_CHARS or "\x00" in value:
            raise ConfigurationError(f"{label} contains an oversized or NUL string")
        return
    if isinstance(value, list):
        if len(value) > PYTEST_MAX_JSON_MEMBERS:
            raise ConfigurationError(f"{label} contains an oversized array")
        for item in value:
            _validate_bounded_json_value(
                item,
                label=label,
                depth=depth + 1,
                budget=budget,
            )
        return
    if isinstance(value, dict):
        if len(value) > PYTEST_MAX_JSON_MEMBERS:
            raise ConfigurationError(f"{label} contains an oversized object")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or len(key) > PYTEST_MAX_ITEM_TEXT_CHARS
                or "\x00" in key
            ):
                raise ConfigurationError(f"{label} contains an invalid object key")
            _validate_bounded_json_value(
                item,
                label=label,
                depth=depth + 1,
                budget=budget,
            )
        return
    raise ConfigurationError(f"{label} contains an unsupported JSON value")


def _validate_collection_evidence(
    payload: object,
    *,
    label: str,
    expected_targets: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{label} must be an object")
    if set(payload) != {
        "invocation",
        "items",
        "nodeids",
        "collection_sha256",
    }:
        raise ConfigurationError(f"{label} has unexpected or missing fields")
    invocation = validate_collection_invocation_contract(
        payload.get("invocation"),
        expected_targets=expected_targets,
        field=f"{label} invocation",
    )
    items = payload.get("items")
    nodeids = payload.get("nodeids")
    digest = payload.get("collection_sha256")
    try:
        nodeids = _validate_nodeid_sequence(nodeids, label=f"{label} nodeids")
    except ConfigurationError as exc:
        raise ConfigurationError(f"{label} is structurally invalid: {exc}") from exc
    if not isinstance(items, list) or len(items) != len(nodeids) or not _is_lower_hex(digest, 64):
        raise ConfigurationError(f"{label} is structurally invalid")
    validate_collection_item_paths(
        items,
        expected_targets=expected_targets,
        rootpath=invocation["rootpath"],
        field=f"{label} items",
    )
    expected_item_fields = {
        "nodeid",
        "item_type",
        "path",
        "name",
        "fixturenames",
        "callspec_params",
        "parameter_identity_supported",
        "parameter_identity_uncertainty",
        "identity_sha256",
    }
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != expected_item_fields:
            raise ConfigurationError(f"{label} item {index} has an invalid shape")
        if (
            item.get("nodeid") != nodeids[index]
            or not isinstance(item.get("item_type"), str)
            or len(item.get("item_type", "")) > PYTEST_MAX_ITEM_TEXT_CHARS
            or not isinstance(item.get("name"), str)
            or len(item.get("name", "")) > PYTEST_MAX_ITEM_TEXT_CHARS
            or not (
                item.get("path") is None
                or (
                    isinstance(item.get("path"), str)
                    and len(item.get("path", "")) <= PYTEST_MAX_ITEM_TEXT_CHARS
                    and "\x00" not in item.get("path", "")
                )
            )
            or not isinstance(item.get("fixturenames"), list)
            or len(item.get("fixturenames", [])) > PYTEST_MAX_FIXTURES_PER_ITEM
            or not all(isinstance(name, str) for name in item.get("fixturenames", []))
            or any(
                len(name) > PYTEST_MAX_ITEM_TEXT_CHARS or "\x00" in name
                for name in item.get("fixturenames", [])
            )
            or item.get("fixturenames")
            != sorted(set(item.get("fixturenames", [])))
            or not isinstance(item.get("callspec_params"), dict)
            or not isinstance(item.get("parameter_identity_supported"), bool)
            or not isinstance(item.get("parameter_identity_uncertainty"), list)
            or len(item.get("parameter_identity_uncertainty", []))
            > PYTEST_MAX_UNCERTAINTIES_PER_ITEM
            or not all(
                isinstance(reason, str)
                for reason in item.get("parameter_identity_uncertainty", [])
            )
            or any(
                len(reason) > PYTEST_MAX_ITEM_TEXT_CHARS or "\x00" in reason
                for reason in item.get("parameter_identity_uncertainty", [])
            )
            or item.get("parameter_identity_uncertainty")
            != sorted(set(item.get("parameter_identity_uncertainty", [])))
            or not _is_lower_hex(item.get("identity_sha256"), 64)
        ):
            raise ConfigurationError(f"{label} item {index} is structurally invalid")
        _validate_bounded_json_value(
            item["callspec_params"],
            label=f"{label} item {index} callspec parameters",
        )
        unsigned_item = dict(item)
        claimed_identity = unsigned_item.pop("identity_sha256")
        encoded_item = json.dumps(
            unsigned_item, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if hashlib.sha256(encoded_item).hexdigest() != claimed_identity:
            raise ConfigurationError(f"{label} item {index} identity digest mismatch")
    canonical = json.dumps(
        {"invocation": invocation, "items": items, "nodeids": nodeids},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(canonical).hexdigest() != digest:
        raise ConfigurationError(f"{label} digest mismatch")
    return payload


def collect_pytest(
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    *,
    runtime_identity: dict[str, Any],
    environment: dict[str, str],
) -> tuple[dict[str, Any], float]:
    base, docker_environment, container_environment = _docker_base(
        manifest, task, runtime_identity=runtime_identity, environment=environment
    )
    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-pytest-collection-"
    ) as support:
        plugin = support / "zerorun_collection_plugin.py"
        plugin.write_text(_COLLECTION_PLUGIN, encoding="utf-8")
        plugin.chmod(0o644)
        output = support / "collection.json"
        _prepare_evidence_output(output)
        support.chmod(0o755)
        command = base[:-1] + [
            "--mount",
            _docker_bind_mount(support, _SUPPORT_MOUNT, readonly=True),
            "--mount",
            _docker_bind_mount(output, _COLLECTION_OUTPUT),
            "--env",
            f"ZERORUN_COLLECTION_OUTPUT={_COLLECTION_OUTPUT}",
            "--env",
            f"PYTHONPATH={_SUPPORT_MOUNT}",
            base[-1],
            *_pytest_command(task, profile),
            "-p",
            "zerorun_collection_plugin",
            "--collect-only",
            *profile.targets,
        ]
        started = time.perf_counter()
        process = _run_docker_process(
            command,
            cwd=manifest.root,
            environment=docker_environment,
            container_environment=container_environment,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if process.returncode not in {0, 5}:
            detail = (process.stdout + process.stderr).decode("utf-8", errors="replace")[-6000:]
            raise ConfigurationError(
                f"pytest collection failed in pinned runtime ({process.returncode}): {detail}"
            )
        try:
            payload = _read_bounded_json(
                output,
                label="pytest collection evidence",
                limit_bytes=PYTEST_EVIDENCE_LIMIT_BYTES,
            )
        except ConfigurationError as exc:
            raise ConfigurationError(f"pytest collection evidence is unreadable: {exc}") from exc
    validated = _validate_collection_evidence(
        payload,
        label="pytest collection evidence",
        expected_targets=profile.targets,
    )
    return validated, elapsed_ms


def execute_nodes(
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    nodeids: list[str],
    *,
    runtime_identity: dict[str, Any],
    environment: dict[str, str],
) -> tuple[int, float, bytes, bytes]:
    """Compatibility fresh executor.

    Large production selections use execute_single_pass() so node ids are
    transported through a mounted JSON file rather than the host argv.
    """
    if not nodeids:
        return 0, 0.0, b"", b""
    _validate_nodeid_sequence(nodeids, label="pytest execution nodeids")
    base, docker_environment, container_environment = _docker_base(
        manifest, task, runtime_identity=runtime_identity, environment=environment
    )
    command = base + [*_pytest_command(task, profile), *nodeids]
    started = time.perf_counter()
    process = _run_docker_process(
        command,
        cwd=manifest.root,
        environment=docker_environment,
        container_environment=container_environment,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return process.returncode, elapsed_ms, process.stdout, process.stderr


def execute_single_pass(
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    *,
    baseline_collection: dict[str, Any] | None,
    fresh_nodeids: list[str],
    runtime_identity: dict[str, Any],
    environment: dict[str, str],
) -> tuple[int, float, bytes, bytes, dict[str, Any]]:
    """Collect once, fail-closed select, and execute misses in one pytest process."""
    base, docker_environment, container_environment = _docker_base(
        manifest, task, runtime_identity=runtime_identity, environment=environment
    )
    with private_temporary_directory(
        Path(tempfile.gettempdir()), prefix="zerorun-pytest-single-pass-"
    ) as support:
        plugin = support / "zerorun_single_pass_plugin.py"
        plugin.write_text(_SINGLE_PASS_PLUGIN, encoding="utf-8")
        plugin.chmod(0o644)
        if baseline_collection is not None:
            baseline_collection = _validate_collection_evidence(
                baseline_collection,
                label="pytest single-pass baseline collection",
                expected_targets=profile.targets,
            )
        requested_nodeids = _validate_nodeid_sequence(
            list(dict.fromkeys(fresh_nodeids)),
            label="pytest single-pass requested nodeids",
        )
        request = {
            "baseline_collection": baseline_collection,
            "fresh_nodeids": requested_nodeids,
        }
        selection_input = support / "selection-input.json"
        _write_bounded_json(
            selection_input,
            request,
            label="pytest single-pass request",
            limit_bytes=PYTEST_REQUEST_LIMIT_BYTES,
        )
        selection_input.chmod(0o644)
        output = support / "selection-output.json"
        _prepare_evidence_output(output)
        support.chmod(0o755)
        command = base[:-1] + [
            "--mount",
            _docker_bind_mount(support, _SUPPORT_MOUNT, readonly=True),
            "--mount",
            _docker_bind_mount(output, _SINGLE_PASS_OUTPUT),
            "--env",
            f"ZERORUN_SELECTION_INPUT={_SINGLE_PASS_INPUT}",
            "--env",
            f"ZERORUN_SELECTION_OUTPUT={_SINGLE_PASS_OUTPUT}",
            "--env",
            f"PYTHONPATH={_SUPPORT_MOUNT}",
            base[-1],
            *_pytest_command(task, profile),
            "-p",
            "zerorun_single_pass_plugin",
            *profile.targets,
        ]
        started = time.perf_counter()
        process = _run_docker_process(
            command,
            cwd=manifest.root,
            environment=docker_environment,
            container_environment=container_environment,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        try:
            payload = _read_bounded_json(
                output,
                label="pytest single-pass selection evidence",
                limit_bytes=PYTEST_EVIDENCE_LIMIT_BYTES,
            )
        except ConfigurationError as exc:
            detail = (process.stdout + process.stderr).decode(
                "utf-8", errors="replace"
            )[-6000:]
            raise ConfigurationError(
                "pytest single-pass execution produced no valid bounded selection "
                f"evidence ({process.returncode}): {exc}; {detail}"
            ) from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("pytest single-pass selection evidence must be an object")
    if set(payload) != {
        "mode",
        "reason",
        "collection",
        "fresh_nodeids",
        "reused_nodeids",
        "executed_nodeids",
    }:
        raise ConfigurationError(
            "pytest single-pass selection evidence has unexpected or missing fields"
        )
    collection = payload.get("collection")
    fresh = payload.get("fresh_nodeids")
    reused = payload.get("reused_nodeids")
    executed = payload.get("executed_nodeids")
    mode = payload.get("mode")
    if not isinstance(collection, dict):
        raise ConfigurationError("pytest single-pass selection evidence is malformed")
    collection = _validate_collection_evidence(
        collection,
        label="pytest single-pass collection evidence",
        expected_targets=profile.targets,
    )
    try:
        fresh = _validate_nodeid_sequence(fresh, label="pytest single-pass fresh nodeids")
        reused = _validate_nodeid_sequence(reused, label="pytest single-pass reused nodeids")
        executed = _validate_nodeid_sequence(
            executed,
            label="pytest single-pass executed nodeids",
        )
    except ConfigurationError as exc:
        raise ConfigurationError(
            f"pytest single-pass selection evidence is malformed: {exc}"
        ) from exc
    if (
        mode not in {
            "single-pass-incremental",
            "per-node-aligned-incremental",
            "whole-target-fresh",
        }
        or set(fresh).intersection(reused)
        or set(fresh).union(reused) != set(collection.get("nodeids", []))
        or not set(executed).issubset(fresh)
        or not (
            payload.get("reason") is None
            or (
                isinstance(payload.get("reason"), str)
                and 0 < len(payload["reason"]) <= PYTEST_MAX_ITEM_TEXT_CHARS
            )
        )
    ):
        raise ConfigurationError("pytest single-pass selection evidence is malformed")
    current_ids = list(collection["nodeids"])
    requested = list(dict.fromkeys(fresh_nodeids))
    expected_fresh = [nodeid for nodeid in current_ids if nodeid in set(requested)]
    if mode == "single-pass-incremental":
        if (
            not isinstance(baseline_collection, dict)
            or collection.get("collection_sha256")
            != baseline_collection.get("collection_sha256")
            or current_ids != baseline_collection.get("nodeids")
            or not set(requested).issubset(current_ids)
            or fresh != expected_fresh
            or reused != [nodeid for nodeid in current_ids if nodeid not in set(requested)]
            or payload.get("reason") is not None
        ):
            raise ConfigurationError(
                "pytest single-pass incremental evidence does not match the exact request"
            )
    elif mode == "per-node-aligned-incremental":
        if not isinstance(baseline_collection, dict):
            raise ConfigurationError(
                "pytest per-node alignment has no baseline collection"
            )
        baseline_ids = list(baseline_collection["nodeids"])
        baseline_by_node = {
            str(item["nodeid"]): item
            for item in baseline_collection["items"]
        }
        current_by_node = {
            str(item["nodeid"]): item
            for item in collection["items"]
        }
        requested_set = set(requested)
        proposed_reuse = set(baseline_ids) - requested_set
        shared = set(baseline_ids).intersection(current_ids)
        order_preserved = (
            [nodeid for nodeid in baseline_ids if nodeid in shared]
            == [nodeid for nodeid in current_ids if nodeid in shared]
        )
        expected_reused = [
            nodeid
            for nodeid in current_ids
            if nodeid in proposed_reuse
            and current_by_node[nodeid]["identity_sha256"]
            == baseline_by_node[nodeid]["identity_sha256"]
        ]
        expected_fresh_aligned = [
            nodeid for nodeid in current_ids if nodeid not in set(expected_reused)
        ]
        if (
            collection.get("collection_sha256")
            == baseline_collection.get("collection_sha256")
            or not set(requested).issubset(set(baseline_ids))
            or not order_preserved
            or not expected_reused
            or fresh != expected_fresh_aligned
            or reused != expected_reused
            or payload.get("reason") is not None
        ):
            raise ConfigurationError(
                "pytest per-node alignment evidence does not match the exact request"
            )
    elif (
        fresh != current_ids
        or reused
        or not isinstance(payload.get("reason"), str)
    ):
        raise ConfigurationError(
            "pytest whole-target-fresh evidence does not cover the exact collection"
        )
    if process.returncode == 0 and executed != fresh:
        raise ConfigurationError(
            "pytest single-pass successful execution did not attest every selected node"
        )
    effective_code = process.returncode
    if effective_code == 5 and not fresh:
        effective_code = 0
    return effective_code, elapsed_ms, process.stdout, process.stderr, payload


def _collection_guard_sha256(
    root: Path,
    profile: PytestProfile,
    *,
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    session: FingerprintSession | None = None,
    guarded_paths: set[str] | None = None,
) -> str:
    """Hash persisted collection-sensitive targets plus immutable contracts.

    Node/session/static closure fingerprints are checked separately by the node
    cache. This guard exists to make reuse of the previous exact collection
    snapshot safe without starting pytest merely to recollect unchanged targets.
    """
    root = root.resolve(strict=True)
    # Ordinary adjacent ``__pycache__`` entries are made unreachable by the
    # isolated container pycache prefix, but legacy/sourceless ``module.pyc``
    # files remain importable directly from ``sys.path``.  Scan on every
    # independently populated authoritative guard pass (never reuse this
    # result across passes) so creation during a slow trust attestation turns
    # the candidate hit into a conservative fresh/refusal path.
    reject_sourceless_workspace_bytecode(root)
    excluded_roots = {
        ".git",
        ".zerorun",
        ".zerorun-env",
        ".pytest_cache",
        "__pycache__",
    }
    if session is not None:
        session.validate_root(root)
    reviewed_targets = validate_reviewed_pytest_targets(
        profile.targets,
        field="pytest collection guard targets",
    )
    filesystem_targets = tuple(
        dict.fromkeys(target.partition("::")[0] for target in reviewed_targets)
    )
    records = (
        session.expand_inputs(root, filesystem_targets)
        if session is not None
        else expand_inputs(root, filesystem_targets)
    )
    rows: list[dict[str, Any]] = []
    for record in records:
        relative = record.get("path")
        if isinstance(relative, str) and set(Path(relative).parts).intersection(
            excluded_roots
        ):
            continue
        record_type = record.get("type")
        mode = record.get("mode")
        if (
            not isinstance(relative, str)
            or type(mode) is not int
            or mode < 0
        ):
            raise ConfigurationError(
                "pytest collection guard encountered unavailable or unsupported target: "
                + str(relative or record.get("pattern"))
            )
        if record_type == "directory":
            if guarded_paths is not None:
                guarded_paths.add(relative)
            rows.append(
                {"path": relative, "type": "directory", "mode": mode}
            )
            continue
        if (
            record_type != "file"
            or type(record.get("size")) is not int
            or record.get("size", -1) < 0
            or not isinstance(record.get("sha256"), str)
        ):
            raise ConfigurationError(
                "pytest collection guard encountered unavailable or unsupported target: "
                + str(relative or record.get("pattern"))
            )
        rows.append(
            {
                "path": relative,
                "type": "file",
                "mode": mode,
                "size": record["size"],
                "sha256": record["sha256"],
            }
        )
        if guarded_paths is not None:
            guarded_paths.add(relative)

    payload = {
        "schema": 3,
        "targets": list(reviewed_targets),
        "records": sorted(rows, key=lambda row: row["path"]),
        "profile_sha256": profile.profile_sha256,
        "collection_plugin_sha256": collection_plugin_sha256(),
        "runtime_identity": runtime_identity,
        "environment_fingerprint": environment_fingerprint,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verified_action_snapshot_key(
    root: Path,
    profile: PytestProfile,
) -> tuple[str, str]:
    return (str(root.resolve(strict=True)), profile.profile_sha256)


def _profile_input_guard_sha256(
    root: Path,
    task: TaskSpec,
    profile: PytestProfile,
    *,
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    session: FingerprintSession | None = None,
    guarded_paths: set[str] | None = None,
) -> str:
    """Conservative whole-file guard for every reviewed pytest action input.

    Selector projections remain the canonical persistent action identity. This
    guard is only a process-local acceleration: hashing the entire containing
    file is intentionally broader, so an irrelevant source edit can cause a
    false miss but can never authorize a false hit.
    """
    root = root.resolve(strict=True)
    if session is not None:
        session.validate_root(root)
    selector_files: set[str] = set()
    input_patterns: set[str] = set(profile.static_inputs)
    absent_patterns: set[str] = set()

    def include_closure(row: dict[str, Any]) -> None:
        for selector in row.get("selectors", []):
            if not isinstance(selector, str) or "::" not in selector:
                raise ConfigurationError(
                    "pytest verified-action guard encountered an invalid selector"
                )
            selector_files.add(selector.split("::", 1)[0])
        for pattern in row.get("fallback_files", []):
            if not isinstance(pattern, str) or not pattern:
                raise ConfigurationError(
                    "pytest verified-action guard encountered an invalid fallback"
                )
            input_patterns.add(pattern)
        for pattern in row.get("absent_files", []):
            if not isinstance(pattern, str) or not pattern:
                raise ConfigurationError(
                    "pytest verified-action guard encountered an invalid negative import candidate"
                )
            absent_patterns.add(pattern)

    include_closure(profile.session_closure)
    for row in profile.nodes.values():
        if row.get("reviewable") is True and row.get("fresh_required") is not True:
            include_closure(row)

    patterns = tuple(sorted(selector_files | input_patterns))
    records = _content_only(
        session.expand_inputs(root, patterns)
        if session is not None
        else expand_inputs(root, patterns)
    )
    for record in records:
        if record.get("type") in {"missing", "symlink", "unsupported"}:
            raise ConfigurationError(
                "pytest verified-action guard input became unavailable or unsupported: "
                + str(record.get("path") or record.get("pattern"))
            )
        relative = record.get("path")
        if guarded_paths is not None and isinstance(relative, str):
            guarded_paths.add(relative)

    absent_patterns_tuple = tuple(sorted(absent_patterns))
    absent_records = _content_only(
        session.expand_expected_absent_inputs(root, absent_patterns_tuple)
        if session is not None
        else expand_expected_absent_inputs(root, absent_patterns_tuple)
    )
    expected_absent_records = [
        {"pattern": path, "type": "missing"}
        for path in sorted(absent_patterns)
    ]
    if absent_records != expected_absent_records:
        raise ConfigurationError(
            "pytest verified-action negative import candidate became available or unsupported"
        )
    if guarded_paths is not None:
        guarded_paths.update(absent_patterns)

    payload = {
        "schema": 2,
        "profile_sha256": profile.profile_sha256,
        "task": {
            "name": task.name,
            "command": list(task.command),
            "image": task.image,
            "platform": task.platform,
            "result_only": task.result_only,
            "closure_reviewed": task.closure_reviewed,
            "unsafe_effects": list(task.unsafe_effects),
        },
        "records": records,
        "absent_records": absent_records,
        "runtime_identity": runtime_identity,
        "environment_fingerprint": environment_fingerprint,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _remember_verified_action_snapshot(
    root: Path,
    task: TaskSpec,
    profile: PytestProfile,
    collection: dict[str, Any],
    *,
    collection_guard_sha256: str | None,
    input_guard_sha256: str | None,
    reference_execution_ms: float,
) -> None:
    key = _verified_action_snapshot_key(root, profile)
    if (
        not isinstance(collection_guard_sha256, str)
        or not isinstance(input_guard_sha256, str)
        or reference_execution_ms < 0
    ):
        _VERIFIED_ACTION_SNAPSHOTS.pop(key, None)
        return
    _VERIFIED_ACTION_SNAPSHOTS[key] = {
        "schema": 1,
        "collection_sha256": collection.get("collection_sha256"),
        "collection_guard_sha256": collection_guard_sha256,
        "input_guard_sha256": input_guard_sha256,
        "runtime_image": task.image,
        "nodeids": list(collection.get("nodeids", [])),
        "reference_execution_ms": float(reference_execution_ms),
    }


def _try_verified_action_snapshot(
    root: Path,
    task: TaskSpec,
    profile: PytestProfile,
    collection: dict[str, Any],
    *,
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    session: FingerprintSession,
) -> dict[str, Any] | None:
    key = _verified_action_snapshot_key(root, profile)
    saved = _VERIFIED_ACTION_SNAPSHOTS.get(key)
    if not isinstance(saved, dict) or saved.get("schema") != 1:
        return None
    nodeids = list(collection.get("nodeids", []))
    if (
        saved.get("runtime_image") != task.image
        or saved.get("collection_sha256") != collection.get("collection_sha256")
        or saved.get("nodeids") != nodeids
    ):
        _VERIFIED_ACTION_SNAPSHOTS.pop(key, None)
        return None
    try:
        collection_guard = _collection_guard_sha256(
            root,
            profile,
            runtime_identity=runtime_identity,
            environment_fingerprint=environment_fingerprint,
            session=session,
        )
        input_guard = _profile_input_guard_sha256(
            root,
            task,
            profile,
            runtime_identity=runtime_identity,
            environment_fingerprint=environment_fingerprint,
            session=session,
        )
    except ConfigurationError:
        _VERIFIED_ACTION_SNAPSHOTS.pop(key, None)
        return None
    if (
        collection_guard != saved.get("collection_guard_sha256")
        or input_guard != saved.get("input_guard_sha256")
    ):
        _VERIFIED_ACTION_SNAPSHOTS.pop(key, None)
        return None
    return saved


def _canonical_sha256(value: object, *, label: str) -> str:
    def json_value(item: object) -> object:
        if isinstance(item, Mapping):
            return {str(key): json_value(member) for key, member in item.items()}
        if isinstance(item, (list, tuple)):
            return [json_value(member) for member in item]
        return item

    try:
        encoded = json.dumps(
            json_value(value),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ConfigurationError(f"{label} is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _suite_result_path(store: Store, profile: PytestProfile) -> Path:
    directory = store.managed_directory(
        store.state / _SUITE_RESULT_DIRECTORY
    )
    return store.managed_file(directory / f"{profile.profile_sha256}.json")


def _read_small_stable_file(path: Path, *, limit: int) -> bytes | None:
    if limit <= 0 or _is_link_like(path):
        return None
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOINHERIT", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit:
            return None
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        identity = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_size,
            value.st_mtime_ns,
            getattr(value, "st_ctime_ns", 0),
        )
        if (
            len(raw) > limit
            or len(raw) != opened.st_size
            or identity(after) != identity(opened)
            or _is_link_like(path)
            or not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino, current.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
        ):
            return None
        return raw
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _canonical_guard_paths(value: object) -> tuple[str, ...] | None:
    if (
        not isinstance(value, list)
        or len(value) > PYTEST_MAX_JSON_MEMBERS
    ):
        return None
    paths: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item
            or len(item) > PYTEST_MAX_ITEM_TEXT_CHARS
            or "\x00" in item
            or "\\" in item
            or item.startswith("/")
            or item.endswith("/")
            or "//" in item
            or (item != "." and any(part in {"", ".", ".."} for part in item.split("/")))
            or (item != "." and ":" in item.split("/", 1)[0])
        ):
            return None
        paths.append(item)
    if paths != sorted(set(paths)):
        return None
    return tuple(paths)


def _git_hint_environment() -> dict[str, str]:
    keep = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "HOME",
    )
    environment = {name: os.environ[name] for name in keep if name in os.environ}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "LC_ALL": "C",
        }
    )
    return environment


def _trusted_git_executable(root: Path) -> Path | None:
    discovered = shutil.which("git")
    if discovered is None:
        return None
    try:
        git = Path(discovered).expanduser().resolve(strict=True)
        git.relative_to(root.resolve(strict=True))
    except ValueError:
        return git
    except OSError:
        return None
    return None


def _run_git_hint(
    root: Path,
    arguments: list[str],
    *,
    limit: int,
) -> bytes | None:
    git = _trusted_git_executable(root)
    if git is None:
        return None
    common = [
        str(git),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "diff.external=",
        "-C",
        str(root),
    ]
    try:
        process, timed_out = _run_bounded_process(
            [*common, *arguments],
            cwd=None,
            environment=_git_hint_environment(),
            timeout_seconds=_GIT_REVISION_TIMEOUT_SECONDS,
            output_limit_bytes=limit,
        )
    except (OSError, RuntimeError, ValueError):
        return None
    if (
        timed_out
        or process.returncode != 0
        or process.stderr
        or len(process.stdout) >= limit
    ):
        return None
    return process.stdout


def _repository_revision_hint(
    root: Path,
    *,
    guard_paths: list[str] | None = None,
) -> dict[str, Any] | None:
    """Return exact signed Git identity used only to reject stale receipts."""

    marker = root / ".git"
    if _is_link_like(marker) or not (marker.is_dir() or marker.is_file()):
        return None
    raw = _run_git_hint(
        root,
        ["rev-parse", "HEAD^{commit}", "HEAD^{tree}"],
        limit=1024,
    )
    if raw is None:
        return None
    try:
        rows = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        return None
    if (
        len(rows) != 2
        or len(rows[0]) not in {40, 64}
        or len(rows[1]) != len(rows[0])
        or not _is_lower_hex(rows[0], len(rows[0]))
        or not _is_lower_hex(rows[1], len(rows[1]))
    ):
        return None
    canonical_paths = _canonical_guard_paths(guard_paths or [])
    if canonical_paths is None:
        return None
    return {
        "schema": 1,
        "commit": rows[0],
        "tree": rows[1],
        "guard_paths": list(canonical_paths),
    }


def _validated_repository_revision_hint(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "commit",
        "tree",
        "guard_paths",
    }:
        return None
    commit = value.get("commit")
    tree = value.get("tree")
    paths = _canonical_guard_paths(value.get("guard_paths"))
    if (
        type(value.get("schema")) is not int
        or value.get("schema") != 1
        or not isinstance(commit, str)
        or len(commit) not in {40, 64}
        or not _is_lower_hex(commit, len(commit))
        or not isinstance(tree, str)
        or len(tree) != len(commit)
        or not _is_lower_hex(tree, len(tree))
        or paths is None
    ):
        return None
    return {
        "schema": 1,
        "commit": commit,
        "tree": tree,
        "guard_paths": list(paths),
    }


def _parse_git_raw_changed_paths(raw: bytes) -> tuple[str, ...] | None:
    if not raw:
        return ()
    if not raw.endswith(b"\x00"):
        return None
    fields = raw[:-1].split(b"\x00")
    if len(fields) % 2:
        return None
    paths: list[str] = []
    for index in range(0, len(fields), 2):
        header = fields[index].split(b" ")
        path_raw = fields[index + 1]
        if len(header) != 5:
            return None
        old_mode, new_mode, old_oid, new_oid, status_code = header
        if (
            not old_mode.startswith(b":")
            or len(old_mode) != 7
            or any(character not in b"01234567" for character in old_mode[1:])
            or len(new_mode) != 6
            or any(character not in b"01234567" for character in new_mode)
            or len(old_oid) not in {40, 64}
            or len(new_oid) != len(old_oid)
            or any(character not in b"0123456789abcdef" for character in old_oid)
            or any(character not in b"0123456789abcdef" for character in new_oid)
            or status_code not in {b"A", b"D", b"M", b"T", b"U"}
        ):
            return None
        try:
            path = path_raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if _canonical_guard_paths([path]) is None:
            return None
        paths.append(path)
    return tuple(paths)


def _path_intersects_guard_scope(path: str, guards: tuple[str, ...]) -> bool:
    return any(
        guard == "."
        or path == guard
        or path.startswith(guard + "/")
        or guard.startswith(path + "/")
        for guard in guards
    )


def _repository_hint_allows_guard_validation(
    root: Path,
    saved_value: object,
) -> bool:
    """Use a bounded raw diff as rejection only; ``True`` still hashes all guards."""

    if saved_value is None:
        return True
    saved = _validated_repository_revision_hint(saved_value)
    if saved is None:
        return False
    current = _repository_revision_hint(root)
    if current is None:
        return False
    if (
        current["commit"] == saved["commit"]
        and current["tree"] == saved["tree"]
    ):
        return True
    raw = _run_git_hint(
        root,
        [
            "diff",
            "--raw",
            "-z",
            "--no-abbrev",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "--ignore-submodules=none",
            saved["commit"],
            current["commit"],
            "--",
        ],
        limit=_GIT_DIFF_LIMIT_BYTES,
    )
    if raw is None:
        return False
    changed = _parse_git_raw_changed_paths(raw)
    if changed is None:
        return False
    current_after = _repository_revision_hint(root)
    if (
        current_after is None
        or current_after["commit"] != current["commit"]
        or current_after["tree"] != current["tree"]
    ):
        return False
    guards = tuple(saved["guard_paths"])
    return not any(
        _path_intersects_guard_scope(path, guards) for path in changed
    )


@contextmanager
def _suite_result_lock(store: Store, profile: PytestProfile):
    locks = store.managed_directory(store.state / "locks")
    locks.mkdir(parents=True, exist_ok=True)
    lock = store.managed_file(
        locks / f"pytest-suite-{profile.profile_sha256}.lock"
    )
    try:
        descriptor = os.open(
            lock,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0),
            0o600,
        )
    except OSError as exc:
        raise ConfigurationError("pytest suite-result lock is unavailable") from exc
    locked = False
    try:
        opened = os.fstat(descriptor)
        current = lock.stat(follow_symlinks=False)
        if (
            _is_link_like(lock)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ConfigurationError(
                "pytest suite-result lock is not one stable regular file"
            )
        if os.name == "nt":
            import msvcrt

            if opened.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ConfigurationError(
                    "another request owns pytest suite-result publication"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ConfigurationError(
                    "another request owns pytest suite-result publication"
                ) from exc
        locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


def _suite_action_model_sha256() -> str:
    """Version the exact aggregate result-identity contract.

    A suite receipt is an optimization over independently keyed schema-9
    pytest-node results.  Changing that action model or either guard contract
    requires a new token and therefore makes older aggregate receipts inert.
    """

    return hashlib.sha256(
        b"pytest-suite-result-v2:composed-node-results-v1:node-action-schema-9-merkle-v1:"
        b"whole-file-input-guard-v1:collection-target-guard-v2:"
        b"bounded-revision-rejection-v1"
    ).hexdigest()


def _suite_engine_action_sha256() -> str:
    """Bind receipts to the source modules that define action semantics."""

    global _SUITE_ENGINE_ACTION_SHA256
    if _SUITE_ENGINE_ACTION_SHA256 is not None:
        return _SUITE_ENGINE_ACTION_SHA256
    package = Path(__file__).resolve(strict=True).parent
    # Hash the complete installed Python package, not a hand-maintained subset:
    # link classification, bounded JSON, manifest parsing, and trust semantics
    # are transitive parts of the acceptance proof too.
    names = tuple(sorted(path.name for path in package.glob("*.py")))
    if not names:
        raise ConfigurationError("pytest suite engine sources are unavailable")
    rows = [
        {
            "name": name,
            "sha256": hashlib.sha256(
                read_stable_file_bytes(package / name)
            ).hexdigest(),
        }
        for name in names
    ]
    _SUITE_ENGINE_ACTION_SHA256 = _canonical_sha256(
        rows,
        label="pytest suite engine action sources",
    )
    return _SUITE_ENGINE_ACTION_SHA256


def _semantic_suite_runtime_identity(task: TaskSpec) -> dict[str, str] | None:
    if task.image is None or task.platform != "linux/amd64":
        return None
    try:
        requested_digest = task.image.rsplit("@", 1)[1]
    except (AttributeError, IndexError):
        return None
    if (
        not requested_digest.startswith("sha256:")
        or not _is_lower_hex(requested_digest[7:], 64)
    ):
        return None
    return {
        "runtime": "oci-image-digest-v1",
        "requested_image": task.image,
        "requested_digest": requested_digest,
        "platform": task.platform,
    }


def _suite_task_contract_sha256(task: TaskSpec) -> str:
    return _canonical_sha256(
        {
            "name": task.name,
            "command": list(task.command),
            "inputs": list(task.inputs),
            "outputs": list(task.outputs),
            "env": list(task.env),
            "cacheable": task.cacheable,
            "unsafe_effects": list(task.unsafe_effects),
            "cache_streams": task.cache_streams,
            "result_normalizer": task.result_normalizer,
            "image": task.image,
            "platform": task.platform,
            "result_only": task.result_only,
            "closure_reviewed": task.closure_reviewed,
            "input_symbols": list(task.input_symbols),
        },
        label="pytest task contract",
    )


def _validated_suite_runtime_identity(
    value: object,
    *,
    task: TaskSpec,
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or set(value) != {
        "runtime",
        "requested_image",
        "requested_digest",
        "platform",
        "image_id",
        "repo_digests",
        "rootfs",
        "config_sha256",
    }:
        return None
    requested_digest = (
        task.image.rsplit("@", 1)[1] if task.image and "@" in task.image else None
    )
    repo_digests = value.get("repo_digests")
    rootfs = value.get("rootfs")
    if (
        value.get("runtime") != "docker"
        or value.get("requested_image") != task.image
        or value.get("requested_digest") != requested_digest
        or value.get("platform") != task.platform
        or not isinstance(value.get("requested_digest"), str)
        or not str(value.get("requested_digest", "")).startswith("sha256:")
        or not _is_lower_hex(str(value.get("requested_digest"))[7:], 64)
        or not isinstance(value.get("image_id"), str)
        or not str(value.get("image_id")).startswith("sha256:")
        or not _is_lower_hex(str(value.get("image_id"))[7:], 64)
        or not _is_lower_hex(value.get("config_sha256"), 64)
        or not isinstance(repo_digests, list)
        or repo_digests != sorted(set(repo_digests))
        or not repo_digests
        or not all(
            isinstance(item, str)
            and len(item) <= PYTEST_MAX_ITEM_TEXT_CHARS
            and item.endswith("@" + str(requested_digest))
            for item in repo_digests
        )
        or not isinstance(rootfs, dict)
        or set(rootfs) != {"type", "layers"}
        or not isinstance(rootfs.get("type"), str)
        or not isinstance(rootfs.get("layers"), list)
        or len(rootfs.get("layers", [])) > PYTEST_MAX_JSON_MEMBERS
        or not all(
            isinstance(layer, str)
            and len(layer) <= PYTEST_MAX_ITEM_TEXT_CHARS
            for layer in rootfs.get("layers", [])
        )
    ):
        return None
    return value


def _suite_guard_snapshot(
    root: Path,
    task: TaskSpec,
    profile: PytestProfile,
    *,
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    session: FingerprintSession | None = None,
) -> dict[str, Any]:
    """Take one complete aggregate guard pass without cross-pass memoization."""

    discovered_before = discover_pytest_static_inputs(
        root,
        targets=profile.targets,
    )
    session = session or FingerprintSession(root)
    guarded_paths: set[str] = set()
    collection_guard = _collection_guard_sha256(
        root,
        profile,
        runtime_identity=runtime_identity,
        environment_fingerprint=environment_fingerprint,
        session=session,
        guarded_paths=guarded_paths,
    )
    action_guard = _profile_input_guard_sha256(
        root,
        task,
        profile,
        runtime_identity=runtime_identity,
        environment_fingerprint=environment_fingerprint,
        session=session,
        guarded_paths=guarded_paths,
    )
    discovered_after = discover_pytest_static_inputs(
        root,
        targets=profile.targets,
    )
    if discovered_before != discovered_after:
        raise ConfigurationError(
            "pytest discovery surface changed during suite-result validation"
        )
    return {
        "static_inputs": list(discovered_after),
        "static_inputs_sha256": _canonical_sha256(
            list(discovered_after),
            label="pytest discovered static inputs",
        ),
        "guard_paths": sorted(guarded_paths),
        "collection_guard_sha256": collection_guard,
        "action_guard_sha256": action_guard,
    }


def _validate_suite_result_payload(
    payload: object,
    *,
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    environment_fingerprint: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "kind",
        "authorizes_reuse",
        "task",
        "task_contract_sha256",
        "manifest_sha256",
        "profile_sha256",
        "source_review_sha256",
        "profiler_sha256",
        "collection_plugin_sha256",
        "single_pass_plugin_sha256",
        "action_model_sha256",
        "engine_action_sha256",
        "targets",
        "repository_revision_hint",
        "semantic_runtime_identity",
        "observed_runtime_identity_sha256",
        "environment_fingerprint",
        "collection",
        "guards",
        "node_results",
        "node_results_sha256",
        "composition",
        "result_count",
        "all_nodes_verified",
        "reference_execution_ms",
        "exit_code",
        "created_at",
        "provenance",
    }:
        return None
    try:
        _validate_bounded_json_value(payload, label="pytest suite result")
        collection = _validate_collection_evidence(
            payload.get("collection"),
            label="pytest suite result collection",
            expected_targets=profile.targets,
        )
    except ConfigurationError:
        return None
    if profile.source_review is None:
        return None
    try:
        from .pytest_qualify import profiler_sha256

        current_profiler_sha256 = profiler_sha256()
    except (ConfigurationError, OSError, RuntimeError):
        return None
    semantic_runtime_identity = _semantic_suite_runtime_identity(task)
    nodeids = list(collection["nodeids"])
    rows = payload.get("node_results")
    composition = payload.get("composition")
    guards = payload.get("guards")
    revision_hint = _validated_repository_revision_hint(
        payload.get("repository_revision_hint")
    )
    expected_nodes = {
        nodeid
        for nodeid, row in profile.nodes.items()
        if isinstance(row, Mapping)
        and row.get("reviewable") is True
        and row.get("fresh_required") is not True
    }
    if (
        payload.get("schema") != _SUITE_RESULT_SCHEMA
        or payload.get("kind") != "hermetic-composed-result-only-suite"
        or payload.get("authorizes_reuse") is not True
        or payload.get("task") != task.name
        or payload.get("task_contract_sha256")
        != _suite_task_contract_sha256(task)
        or payload.get("manifest_sha256") != manifest_sha256(manifest)
        or payload.get("profile_sha256") != profile.profile_sha256
        or payload.get("source_review_sha256")
        != _canonical_sha256(
            profile.source_review,
            label="pytest profile source review",
        )
        or payload.get("profiler_sha256") != current_profiler_sha256
        or profile.source_review.get("profiler_sha256")
        != current_profiler_sha256
        or payload.get("collection_plugin_sha256")
        != collection_plugin_sha256()
        or profile.source_review.get("collection_plugin_sha256")
        != collection_plugin_sha256()
        or payload.get("single_pass_plugin_sha256")
        != hashlib.sha256(_SINGLE_PASS_PLUGIN.encode("utf-8")).hexdigest()
        or payload.get("action_model_sha256")
        != _suite_action_model_sha256()
        or payload.get("engine_action_sha256")
        != _suite_engine_action_sha256()
        or payload.get("targets") != list(profile.targets)
        or not (
            payload.get("repository_revision_hint") is None
            or _validated_repository_revision_hint(
                payload.get("repository_revision_hint")
            )
            is not None
        )
        or semantic_runtime_identity is None
        or payload.get("semantic_runtime_identity")
        != semantic_runtime_identity
        or not _is_lower_hex(
            payload.get("observed_runtime_identity_sha256"), 64
        )
        or payload.get("environment_fingerprint")
        != environment_fingerprint
        or set(nodeids) != set(profile.nodes)
        or set(nodeids) != expected_nodes
        or not isinstance(guards, dict)
        or set(guards) != {
            "static_inputs",
            "static_inputs_sha256",
            "guard_paths",
            "collection_guard_sha256",
            "action_guard_sha256",
        }
        or guards.get("static_inputs") != list(profile.static_inputs)
        or _canonical_guard_paths(guards.get("guard_paths")) is None
        or (
            revision_hint is not None
            and revision_hint.get("guard_paths") != guards.get("guard_paths")
        )
        or any(
            not _is_lower_hex(guards.get(name), 64)
            for name in (
                "static_inputs_sha256",
                "collection_guard_sha256",
                "action_guard_sha256",
            )
        )
        or not isinstance(rows, list)
        or len(rows) != len(nodeids)
        or not isinstance(composition, dict)
        or set(composition) != {"fresh_nodes", "reused_nodes"}
        or type(composition.get("fresh_nodes")) is not int
        or type(composition.get("reused_nodes")) is not int
        or composition.get("fresh_nodes", -1) < 0
        or composition.get("reused_nodes", -1) < 0
        or composition.get("fresh_nodes", 0)
        + composition.get("reused_nodes", 0)
        != len(nodeids)
        or type(payload.get("result_count")) is not int
        or payload.get("result_count") != len(nodeids)
        or payload.get("all_nodes_verified") is not True
        or not _is_lower_hex(payload.get("node_results_sha256"), 64)
        or _canonical_sha256(rows, label="pytest suite node results")
        != payload.get("node_results_sha256")
        or not _is_bounded_nonnegative_number(
            payload.get("reference_execution_ms")
        )
        or type(payload.get("exit_code")) is not int
        or payload.get("exit_code") != 0
        or not _is_bounded_nonnegative_number(payload.get("created_at"))
    ):
        return None
    for index, row in enumerate(rows):
        if (
            not isinstance(row, dict)
            or set(row)
            != {"nodeid", "key", "metadata_sha256", "disposition"}
            or row.get("nodeid") != nodeids[index]
            or not _is_lower_hex(row.get("key"), 64)
            or not _is_lower_hex(row.get("metadata_sha256"), 64)
            or row.get("disposition") not in {"HIT_REUSED", "MISS_VERIFIED"}
        ):
            return None
    if composition != {
        "fresh_nodes": sum(
            row["disposition"] == "MISS_VERIFIED" for row in rows
        ),
        "reused_nodes": sum(
            row["disposition"] == "HIT_REUSED" for row in rows
        ),
    }:
        return None
    return payload


def _try_signed_suite_result(
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    *,
    store: Store,
    manifest_digest: str,
) -> dict[str, Any] | None:
    """Return a root-local aggregate hit after two independent full guards."""

    path = _suite_result_path(store, profile)
    if not path.is_file() or _is_link_like(path):
        return None
    try:
        raw = _read_bounded_json(
            path,
            label="persisted pytest suite result",
            limit_bytes=PYTEST_SNAPSHOT_LIMIT_BYTES,
        )
    except (ConfigurationError, OSError):
        return None
    # The signed hint is never acceptance authority. A strict, bounded raw Git
    # diff may cheaply reject a known relevant revision change. Irrelevant or
    # same-tree revisions still proceed through both complete source guards;
    # malformed output, history loss, timeout, overflow, or a HEAD race misses.
    if isinstance(raw, dict) and not _repository_hint_allows_guard_validation(
        manifest.root, raw.get("repository_revision_hint")
    ):
        return None
    try:
        trust = CacheTrustSession.open(
            manifest.root,
            manifest_digest=manifest_digest,
            profile_digest=profile.profile_sha256,
            create_secret=False,
        )
    except ConfigurationError:
        return None
    try:
        loaded_before = load_pytest_profile(profile.path, manifest)
        if loaded_before.profile_sha256 != profile.profile_sha256:
            trust.abort()
            return None
        _, environment_fingerprint = hermetic_environment(task)
        payload = _validate_suite_result_payload(
            raw,
            manifest=manifest,
            task=task,
            profile=profile,
            environment_fingerprint=environment_fingerprint,
        )
        if payload is None or not trust.payload_is_trusted(
            payload,
            manifest_digest=manifest_digest,
            profile_digest=profile.profile_sha256,
        ):
            trust.abort()
            return None
        runtime_identity = payload["semantic_runtime_identity"]
        first = _suite_guard_snapshot(
            manifest.root,
            task,
            profile,
            runtime_identity=runtime_identity,
            environment_fingerprint=environment_fingerprint,
        )
        if first != payload["guards"]:
            trust.abort()
            return None
        # Close the slow authority phase before the final source pass. A
        # repository mutation that occurs inside Git/manifest/key attestation
        # is therefore observed by the independent guard below rather than
        # escaping after the last source hash.
        trust.verify_unchanged()
        _, final_environment_fingerprint = hermetic_environment(task)
        if final_environment_fingerprint != environment_fingerprint:
            return None
        second = _suite_guard_snapshot(
            manifest.root,
            task,
            profile,
            runtime_identity=runtime_identity,
            environment_fingerprint=final_environment_fingerprint,
        )
        if second != first:
            return None
        loaded_after = load_pytest_profile(profile.path, manifest)
        if (
            loaded_after.profile_sha256 != profile.profile_sha256
            or manifest_sha256(manifest) != manifest_digest
        ):
            return None
        return payload
    except (ConfigurationError, OSError, RuntimeError):
        trust.abort()
        return None


def _save_signed_suite_result_unlocked(
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    collection: dict[str, Any],
    *,
    decisions: list[Any],
    runtime_identity: dict[str, Any],
    environment_fingerprint: dict[str, dict[str, Any]],
    expected_guards: dict[str, Any],
    reference_execution_ms: float,
    store: Store,
    manifest_digest: str,
) -> bool:
    """Publish one aggregate receipt only from already verified node results."""

    if (
        _validated_suite_runtime_identity(runtime_identity, task=task) is None
        or not _is_bounded_nonnegative_number(reference_execution_ms)
    ):
        return False
    semantic_runtime_identity = _semantic_suite_runtime_identity(task)
    if semantic_runtime_identity is None:
        return False
    try:
        collection = _validate_collection_evidence(
            collection,
            label="pytest suite-result collection",
            expected_targets=profile.targets,
        )
    except ConfigurationError:
        return False
    try:
        if (
            load_pytest_profile(profile.path, manifest).profile_sha256
            != profile.profile_sha256
        ):
            return False
    except (ConfigurationError, OSError):
        return False
    try:
        current_guards = _suite_guard_snapshot(
            manifest.root,
            task,
            profile,
            runtime_identity=semantic_runtime_identity,
            environment_fingerprint=environment_fingerprint,
        )
    except (ConfigurationError, OSError, RuntimeError):
        return False
    if current_guards != expected_guards:
        return False
    by_node = {decision.nodeid: decision for decision in decisions}
    nodeids = list(collection.get("nodeids", []))
    expected_nodes = {
        nodeid
        for nodeid, row in profile.nodes.items()
        if isinstance(row, Mapping)
        and row.get("reviewable") is True
        and row.get("fresh_required") is not True
    }
    if (
        len(by_node) != len(nodeids)
        or set(by_node) != set(nodeids)
        or set(nodeids) != set(profile.nodes)
        or set(nodeids) != expected_nodes
    ):
        return False
    try:
        trust = CacheTrustSession.open(
            manifest.root,
            manifest_digest=manifest_digest,
            profile_digest=profile.profile_sha256,
            create_secret=False,
        )
    except ConfigurationError:
        return False
    rows: list[dict[str, str]] = []
    try:
        for nodeid in nodeids:
            decision = by_node[nodeid]
            if (
                decision.status not in {"HIT_REUSED", "MISS_VERIFIED"}
                or decision.cache_task is None
                or decision.key is None
                or decision.fingerprint is None
            ):
                trust.abort()
                return False
            metadata = _load_result_entry(
                store,
                decision.cache_task,
                decision.key,
                decision.fingerprint,
                result_identity=decision.result_identity,
                trust_session=trust,
            )
            if metadata is None:
                trust.abort()
                return False
            rows.append(
                {
                    "nodeid": nodeid,
                    "key": decision.key,
                    "disposition": decision.status,
                    "metadata_sha256": _canonical_sha256(
                        metadata,
                        label="pytest node result metadata",
                    ),
                }
            )
        from .pytest_qualify import profiler_sha256

        payload: dict[str, Any] = {
            "schema": _SUITE_RESULT_SCHEMA,
            "kind": "hermetic-composed-result-only-suite",
            "authorizes_reuse": True,
            "task": task.name,
            "task_contract_sha256": _suite_task_contract_sha256(task),
            "manifest_sha256": manifest_digest,
            "profile_sha256": profile.profile_sha256,
            "source_review_sha256": _canonical_sha256(
                profile.source_review,
                label="pytest profile source review",
            ),
            "profiler_sha256": profiler_sha256(),
            "collection_plugin_sha256": collection_plugin_sha256(),
            "single_pass_plugin_sha256": hashlib.sha256(
                _SINGLE_PASS_PLUGIN.encode("utf-8")
            ).hexdigest(),
            "action_model_sha256": _suite_action_model_sha256(),
            "engine_action_sha256": _suite_engine_action_sha256(),
            "targets": list(profile.targets),
            "repository_revision_hint": _repository_revision_hint(
                manifest.root,
                guard_paths=list(current_guards["guard_paths"]),
            ),
            "semantic_runtime_identity": semantic_runtime_identity,
            "observed_runtime_identity_sha256": _canonical_sha256(
                runtime_identity,
                label="observed pytest runtime identity",
            ),
            "environment_fingerprint": environment_fingerprint,
            "collection": collection,
            "guards": current_guards,
            "node_results": rows,
            "node_results_sha256": _canonical_sha256(
                rows,
                label="pytest suite node results",
            ),
            "composition": {
                "fresh_nodes": sum(
                    decision.status == "MISS_VERIFIED"
                    for decision in decisions
                ),
                "reused_nodes": sum(
                    decision.status == "HIT_REUSED"
                    for decision in decisions
                ),
            },
            "result_count": len(rows),
            "all_nodes_verified": True,
            "reference_execution_ms": float(reference_execution_ms),
            "exit_code": 0,
            "created_at": time.time(),
        }
        payload["provenance"] = trust.sign_payload(
            payload,
            manifest_digest=manifest_digest,
            profile_digest=profile.profile_sha256,
        )
        # Close authority before the independent publication snapshot.  The
        # receipt is deliberately replaced only after every fallible check so
        # a failed/stale writer cannot delete or overwrite an older valid
        # receipt.  A later lookup still performs its own two full passes.
        trust.verify_unchanged()
        final_runtime_identity = inspect_runtime(
            task, repository_root=manifest.root
        )
        _, final_environment_fingerprint = hermetic_environment(task)
        final_guards = _suite_guard_snapshot(
            manifest.root,
            task,
            profile,
            runtime_identity=semantic_runtime_identity,
            environment_fingerprint=final_environment_fingerprint,
        )
        if (
            final_runtime_identity != runtime_identity
            or final_environment_fingerprint != environment_fingerprint
            or final_guards != current_guards
            or final_guards != expected_guards
            or load_pytest_profile(profile.path, manifest).profile_sha256
            != profile.profile_sha256
            or manifest_sha256(manifest) != manifest_digest
        ):
            return False
        path = _suite_result_path(store, profile)
        directory = store.managed_directory(path.parent)
        directory.mkdir(parents=True, exist_ok=True)
        directory = store.managed_directory(directory)
        with private_temporary_directory(
            store.state,
            prefix="zerorun-pytest-suite-result-",
        ) as temporary:
            staged = temporary / "receipt.json"
            _write_bounded_json(
                staged,
                payload,
                label="pytest suite result",
                limit_bytes=PYTEST_SNAPSHOT_LIMIT_BYTES,
            )
            if os.name != "nt":
                staged.chmod(0o600)
            os.replace(staged, path)
        return True
    except (ConfigurationError, OSError, RuntimeError):
        trust.abort()
        return False


def _save_signed_suite_result(*args: Any, **kwargs: Any) -> bool:
    profile = args[2] if len(args) > 2 else kwargs.get("profile")
    store = kwargs.get("store")
    if not isinstance(profile, PytestProfile) or not isinstance(store, Store):
        raise ConfigurationError("pytest suite-result publication arguments are invalid")
    try:
        with _suite_result_lock(store, profile):
            return _save_signed_suite_result_unlocked(*args, **kwargs)
    except ConfigurationError:
        return False


def _collection_snapshot_path(root: Path) -> Path:
    return root / ".zerorun" / _COLLECTION_SNAPSHOT


def _snapshot_manifest(manifest_or_root: Manifest | Path) -> Manifest:
    if isinstance(manifest_or_root, Manifest):
        return manifest_or_root
    root = Path(manifest_or_root).expanduser().resolve(strict=True)
    return load_manifest(root / ".zerorun.json")


def _load_collection_snapshot(
    manifest_or_root: Manifest | Path,
    profile: PytestProfile,
    *,
    manifest_digest: str | None = None,
    store: Store | None = None,
    trust_session_provider: Callable[[], CacheTrustSession] | None = None,
) -> dict[str, Any] | None:
    try:
        manifest = _snapshot_manifest(manifest_or_root)
        manifest_digest = manifest_digest or manifest_sha256(manifest)
    except ConfigurationError:
        return None
    if store is not None and store.root != manifest.root:
        return None
    store = store or Store(manifest.root)
    path = store.managed_file(_collection_snapshot_path(manifest.root))
    if not path.is_file():
        return None
    try:
        payload = _read_bounded_json(
            path,
            label="persisted pytest collection snapshot",
            limit_bytes=PYTEST_SNAPSHOT_LIMIT_BYTES,
        )
    except ConfigurationError:
        return None
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema",
            "profile_sha256",
            "collection",
            "collection_guard_sha256",
            "provenance",
        }
        or payload.get("schema") != 2
        or payload.get("profile_sha256") != profile.profile_sha256
        or not isinstance(payload.get("collection"), dict)
        or not (
            payload.get("collection_guard_sha256") is None
            or _is_lower_hex(payload.get("collection_guard_sha256"), 64)
        )
    ):
        return None
    try:
        trusted = (
            trust_session_provider().payload_is_trusted(
                payload,
                manifest_digest=manifest_digest,
                profile_digest=profile.profile_sha256,
            )
            if trust_session_provider is not None
            else cache_payload_is_trusted(
                manifest.root,
                payload,
                manifest_digest=manifest_digest,
                profile_digest=profile.profile_sha256,
            )
        )
    except ConfigurationError:
        return None
    if not trusted:
        return None
    try:
        payload["collection"] = _validate_collection_evidence(
            payload["collection"],
            label="persisted pytest collection snapshot",
            expected_targets=profile.targets,
        )
    except ConfigurationError:
        return None
    return payload


def _save_collection_snapshot(
    manifest_or_root: Manifest | Path,
    profile: PytestProfile,
    collection: dict[str, Any],
    *,
    collection_guard_sha256: str | None = None,
    manifest_digest: str | None = None,
    store: Store | None = None,
) -> None:
    manifest = _snapshot_manifest(manifest_or_root)
    manifest_digest = manifest_digest or manifest_sha256(manifest)
    root = manifest.root
    collection = _validate_collection_evidence(
        collection,
        label="pytest collection snapshot",
        expected_targets=profile.targets,
    )
    if collection_guard_sha256 is not None and not _is_lower_hex(
        collection_guard_sha256, 64
    ):
        raise ConfigurationError("pytest collection guard digest is malformed")
    if store is not None and store.root != root:
        raise ConfigurationError("pytest store cannot span project roots")
    store = store or Store(root)
    store.ensure()
    path = store.managed_file(_collection_snapshot_path(root))
    payload = {
        "schema": 2,
        "profile_sha256": profile.profile_sha256,
        "collection": collection,
        "collection_guard_sha256": collection_guard_sha256,
    }
    payload["provenance"] = sign_cache_payload(
        root,
        payload,
        manifest_digest=manifest_digest,
        profile_digest=profile.profile_sha256,
    )
    temp = store.managed_file(path.with_suffix(".tmp"))
    _write_bounded_json(
        temp,
        payload,
        label="pytest collection snapshot",
        limit_bytes=PYTEST_SNAPSHOT_LIMIT_BYTES,
    )
    os.replace(temp, path)

def _profile_refresh_summary(
    decisions, *, unknown_nodeids=(), unknown_reason: str | None = None
) -> tuple[bool, int, list[str]]:
    reasons = {
            str(decision.reason)
            for decision in decisions
            if decision.status == "MISS_UNCERTAIN"
            and isinstance(decision.reason, str)
            and decision.reason.startswith("node action identity is uncertain:")
    }
    count = sum(
        1
        for decision in decisions
        if decision.status == "MISS_UNCERTAIN"
        and isinstance(decision.reason, str)
        and decision.reason.startswith("node action identity is uncertain:")
    )
    unknown = sorted(set(str(nodeid) for nodeid in unknown_nodeids))
    if unknown:
        reasons.add(
            unknown_reason
            or f"collection contains {len(unknown)} node(s) absent from the reviewed profile"
        )
        count += len(unknown)
    ordered_reasons = sorted(reasons)
    return bool(ordered_reasons), count, ordered_reasons


def _context(
    repository_root: Path,
    task: TaskSpec,
    profile: PytestProfile,
    collection: dict[str, Any],
) -> tuple[NodeCacheContext, dict[str, Any], dict[str, str]]:
    runtime = inspect_runtime(task, repository_root=repository_root)
    environment, environment_fingerprint = hermetic_environment(task)
    context = NodeCacheContext(
        runtime_identity=runtime,
        environment_fingerprint=environment_fingerprint,
        collection_contract_sha256=str(collection["collection_sha256"]),
        collection_plugin_sha256=collection_plugin_sha256(),
        independence_review_sha256=profile.independence_review_sha256,
        profile_sha256=profile.profile_sha256,
    )
    return context, runtime, environment


def _source_review_coverage(
    root: Path,
    profile: PytestProfile,
    *,
    session: FingerprintSession,
) -> tuple[bool, set[str], str | None]:
    """Revalidate qualification-time review authority before any reuse.

    Shared source/configuration drift invalidates the whole profile. Node
    closure drift invalidates only that node, which preserves independent
    unaffected reuse without ever publishing under stale review authority.
    """

    expected = profile.source_review
    if expected is None:
        return (
            False,
            set(),
            "pytest profile predates source-review anchoring; explicit requalification is required",
        )
    try:
        # Local import avoids the module-level qualifier/runtime dependency
        # cycle while still binding profiles to the exact active profiler.
        from .pytest_qualify import profiler_sha256

        current = build_pytest_source_review(
            root,
            static_inputs=profile.static_inputs,
            session_closure=profile.session_closure,
            nodes=profile.nodes,
            profiler_sha256=profiler_sha256(),
            collection_plugin_sha256=collection_plugin_sha256(),
            session=session,
            fail_on_node_error=False,
        )
    except (OSError, RuntimeError, ConfigurationError) as exc:
        return False, set(), f"pytest source-review validation failed: {exc}"

    shared_fields = (
        "schema",
        "profiler_sha256",
        "collection_plugin_sha256",
        "static_inputs_sha256",
        "session_closure_sha256",
    )
    changed = [
        field for field in shared_fields if current.get(field) != expected.get(field)
    ]
    if changed:
        return (
            False,
            set(),
            "pytest shared source-review authority changed ("
            + ", ".join(changed)
            + "); explicit requalification is required",
        )
    expected_nodes = expected.get("node_closure_sha256", {})
    current_nodes = current.get("node_closure_sha256", {})
    covered = {
        nodeid
        for nodeid, digest in expected_nodes.items()
        if current_nodes.get(nodeid) == digest
    }
    return True, covered, None


def _recovery_result(
    manifest: Manifest,
    task: TaskSpec,
    profile: PytestProfile,
    collection: dict[str, Any],
    *,
    runtime: dict[str, Any],
    environment: dict[str, str],
    reason: str,
    wall_started: float,
    store: Store,
) -> dict[str, Any]:
    code, execution_ms, stdout, stderr, selection = execute_single_pass(
        manifest,
        task,
        profile,
        baseline_collection=None,
        fresh_nodeids=[],
        runtime_identity=runtime,
        environment=environment,
    )
    current = selection["collection"]
    nodeids = list(current["nodeids"])
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    payload = {
        "status": "PYTEST_FRESH_RECOVERY" if code == 0 else "REJECTED_INPUT_RACE",
        "exit_code": code,
        "wall_ms": round(wall_ms, 3),
        "execution_ms": round(execution_ms, 3),
        "saved_ms": 0.0,
        "verified_saved_seconds": 0.0,
        "conservative_no_zerorun_ms": round(wall_ms, 3),
        "conservative_no_zerorun_seconds": round(wall_ms / 1000.0, 3),
        "effective_speedup": 1.0,
        "saved_percent": 0.0,
        "reuse_percent": 0.0,
        "reused_nodes": 0,
        "fresh_nodes": len(nodeids),
        "total_nodes": len(nodeids),
        "published_nodes": 0,
        "unknown_nodes": 0,
        "reason": reason,
        "collection_sha256": current.get("collection_sha256"),
        "stdout_tail": stdout[-4000:].decode("utf-8", errors="replace"),
        "stderr_tail": stderr[-4000:].decode("utf-8", errors="replace"),
        "reuse_authorized": False,
    }
    # This path exists because reuse authority or a final source check failed.
    # Its fresh result is returned to the caller but never publishes node,
    # collection, aggregate, or process-local cache evidence.
    store.append_event({"task": task.name, **payload})
    return payload

def run_pytest_profile(manifest: Manifest, profile: PytestProfile) -> dict[str, Any]:
    wall_started = time.perf_counter()
    task = manifest.tasks[profile.task_name]
    # Validate the effective pytest invocation before consulting any cached
    # suite result.  Otherwise an explicit config-selection option could
    # bypass the profile's bound static-input inventory on a host-only hit.
    _pytest_command(task, profile)
    cache_manifest_digest = manifest_sha256(manifest)
    store = Store(manifest.root)
    store.ensure()
    _prepare_mountpoints(manifest.root, store=store)

    suite_result = _try_signed_suite_result(
        manifest,
        task,
        profile,
        store=store,
        manifest_digest=cache_manifest_digest,
    )
    if suite_result is not None:
        reference_execution_ms = float(
            suite_result["reference_execution_ms"]
        )
        wall_ms = (time.perf_counter() - wall_started) * 1000.0
        conservative_no_zerorun_ms = wall_ms + reference_execution_ms
        effective_speedup = (
            conservative_no_zerorun_ms / wall_ms if wall_ms > 0 else 1.0
        )
        saved_percent = (
            reference_execution_ms / conservative_no_zerorun_ms * 100.0
            if conservative_no_zerorun_ms > 0
            else 0.0
        )
        collection = suite_result["collection"]
        node_count = len(collection["nodeids"])
        payload = {
            "status": "PYTEST_INCREMENTAL_PASS",
            "exit_code": 0,
            "wall_ms": round(wall_ms, 3),
            "execution_ms": 0.0,
            "saved_ms": round(reference_execution_ms, 3),
            "verified_saved_seconds": round(
                reference_execution_ms / 1000.0, 3
            ),
            "conservative_no_zerorun_ms": round(
                conservative_no_zerorun_ms, 3
            ),
            "conservative_no_zerorun_seconds": round(
                conservative_no_zerorun_ms / 1000.0, 3
            ),
            "effective_speedup": round(effective_speedup, 3),
            "saved_percent": round(saved_percent, 3),
            "reuse_percent": 100.0,
            "avoided_execution_reference_ms": round(
                reference_execution_ms, 3
            ),
            "collection_overhead_ms": 0.0,
            "single_pass_wall_ms": 0.0,
            "selection_mode": "zero-miss-elided",
            "fast_path": "signed-suite-result",
            "reused_nodes": node_count,
            "fresh_nodes": 0,
            "published_nodes": 0,
            "unknown_nodes": 0,
            "reviewed_nodes": node_count,
            "total_nodes": node_count,
            "reuse_authorized": True,
            "profile_sha256": profile.profile_sha256,
            "profile_refresh_required": False,
            "profile_refresh_node_count": 0,
            "profile_refresh_reasons": [],
            "collection_sha256": collection["collection_sha256"],
            "stdout_tail": "",
            "stderr_tail": "",
        }
        store.append_event({"task": task.name, **payload})
        return payload

    current_profile = load_pytest_profile(profile.path, manifest)
    if current_profile.profile_sha256 != profile.profile_sha256:
        raise ConfigurationError(
            "pytest profile changed during suite-result validation"
        )

    lookup_trust: CacheTrustSession | None = None
    lookup_trust_rejected = False

    def lookup_trust_session() -> CacheTrustSession:
        """Share one exact attestation across snapshot and cache lookup."""

        nonlocal lookup_trust
        if lookup_trust_rejected:
            raise ConfigurationError(
                "cache lookup trust drift was already detected in this request"
            )
        if lookup_trust is None:
            lookup_trust = CacheTrustSession.open(
                manifest.root,
                manifest_digest=cache_manifest_digest,
                profile_digest=profile.profile_sha256,
                create_secret=False,
            )
        return lookup_trust

    context_stub, runtime, environment = _context(
        manifest.root,
        task,
        profile,
        {"collection_sha256": "0" * 64},
    )
    try:
        static_discovery_covered = (
            discover_pytest_static_inputs(
                manifest.root,
                targets=profile.targets,
            )
            == profile.static_inputs
        )
    except (OSError, RuntimeError, ConfigurationError):
        # Discovery uncertainty cannot authorize reuse. The request still
        # executes the whole target so ordinary pytest behavior is preserved,
        # but no result is reused or published until the profile is refreshed.
        static_discovery_covered = False
    pre_execution_session = FingerprintSession(manifest.root)
    (
        source_review_shared_covered,
        source_review_covered_nodes,
        source_review_failure_reason,
    ) = _source_review_coverage(
        manifest.root,
        profile,
        session=pre_execution_session,
    )
    shared_review_authority_covered = (
        static_discovery_covered and source_review_shared_covered
    )

    def node_review_authority_covered(nodeid: str) -> bool:
        row = profile.nodes.get(nodeid)
        if not isinstance(row, Mapping):
            return False
        if row.get("fresh_required") is True:
            return True
        return (
            row.get("reviewable") is True
            and nodeid in source_review_covered_nodes
        )

    has_reuse_candidate = any(
        row.get("reviewable") is True
        and row.get("fresh_required") is not True
        and nodeid in source_review_covered_nodes
        for nodeid, row in profile.nodes.items()
    )
    snapshot = (
        _load_collection_snapshot(
            manifest,
            profile,
            manifest_digest=cache_manifest_digest,
            store=store,
            trust_session_provider=lookup_trust_session,
        )
        if shared_review_authority_covered and has_reuse_candidate
        else None
    )
    baseline_collection = (
        dict(snapshot["collection"]) if snapshot is not None else None
    )

    if (
        baseline_collection is not None
        and shared_review_authority_covered
        and all(
            node_review_authority_covered(nodeid)
            for nodeid in baseline_collection.get("nodeids", [])
        )
    ):
        verified_snapshot = _try_verified_action_snapshot(
            manifest.root,
            task,
            profile,
            baseline_collection,
            runtime_identity=context_stub.runtime_identity,
            environment_fingerprint=context_stub.environment_fingerprint,
            session=pre_execution_session,
        )
        had_verified_snapshot = verified_snapshot is not None
        verified_snapshot_trusted = False
        if verified_snapshot is not None:
            try:
                if lookup_trust is None:
                    raise ConfigurationError(
                        "verified action snapshot has no HMAC trust session"
                    )
                lookup_trust.verify_unchanged()
            except ConfigurationError:
                # Nothing authenticated earlier in this request may escape
                # after trust drift. Recollect and execute the whole target.
                lookup_trust_rejected = True
                lookup_trust = None
            else:
                lookup_trust = None
                # The process-local snapshot is only a hint. Its first source
                # pass must bracket the potentially slow trust verification
                # with a second, independently populated fingerprint session.
                # Otherwise a source mutation triggered from inside the trust
                # check could escape as a stale hit.
                try:
                    final_runtime = inspect_runtime(
                        task, repository_root=manifest.root
                    )
                    final_environment, final_environment_fingerprint = (
                        hermetic_environment(task)
                    )
                    runtime = final_runtime
                    environment = final_environment
                    final_static_inputs = discover_pytest_static_inputs(
                        manifest.root,
                        targets=profile.targets,
                    )
                    final_snapshot = _try_verified_action_snapshot(
                        manifest.root,
                        task,
                        profile,
                        baseline_collection,
                        runtime_identity=final_runtime,
                        environment_fingerprint=final_environment_fingerprint,
                        session=FingerprintSession(manifest.root),
                    )
                    final_profile = load_pytest_profile(profile.path, manifest)
                    final_manifest_sha256 = manifest_sha256(manifest)
                except (ConfigurationError, OSError, RuntimeError):
                    final_snapshot = None
                if (
                    final_snapshot is not None
                    and final_runtime == context_stub.runtime_identity
                    and final_environment_fingerprint
                    == context_stub.environment_fingerprint
                    and final_static_inputs == profile.static_inputs
                    and final_profile.profile_sha256 == profile.profile_sha256
                    and final_manifest_sha256 == cache_manifest_digest
                ):
                    verified_snapshot = final_snapshot
                    verified_snapshot_trusted = True
        if had_verified_snapshot and not verified_snapshot_trusted:
            # A failed post-trust snapshot invalidates every authority
            # decision derived from the earlier view. Execute the current
            # target fresh and publish nothing until a new request observes a
            # stable reviewed source surface end to end.
            _VERIFIED_ACTION_SNAPSHOTS.pop(
                _verified_action_snapshot_key(manifest.root, profile),
                None,
            )
            lookup_trust_rejected = True
            baseline_collection = None
            shared_review_authority_covered = False
            source_review_shared_covered = False
            source_review_failure_reason = (
                "post-trust verified-action snapshot changed; fresh execution required"
            )
            source_review_covered_nodes = set()
        if verified_snapshot is not None and verified_snapshot_trusted:
            cached_reference_ms = max(
                0.0,
                float(verified_snapshot.get("reference_execution_ms", 0.0) or 0.0),
            )
            wall_ms = (time.perf_counter() - wall_started) * 1000.0
            conservative_no_zerorun_ms = wall_ms + cached_reference_ms
            effective_speedup = (
                conservative_no_zerorun_ms / wall_ms if wall_ms > 0 else 1.0
            )
            saved_percent = (
                cached_reference_ms / conservative_no_zerorun_ms * 100.0
                if conservative_no_zerorun_ms > 0
                else 0.0
            )
            nodeids = list(baseline_collection.get("nodeids", []))
            payload = {
                "status": "PYTEST_INCREMENTAL_PASS",
                "exit_code": 0,
                "wall_ms": round(wall_ms, 3),
                "execution_ms": 0.0,
                "saved_ms": round(cached_reference_ms, 3),
                "verified_saved_seconds": round(
                    cached_reference_ms / 1000.0, 3
                ),
                "conservative_no_zerorun_ms": round(
                    conservative_no_zerorun_ms, 3
                ),
                "conservative_no_zerorun_seconds": round(
                    conservative_no_zerorun_ms / 1000.0, 3
                ),
                "effective_speedup": round(effective_speedup, 3),
                "saved_percent": round(saved_percent, 3),
                "reuse_percent": 100.0,
                "avoided_execution_reference_ms": round(
                    cached_reference_ms, 3
                ),
                "collection_overhead_ms": 0.0,
                "single_pass_wall_ms": 0.0,
                "selection_mode": "zero-miss-elided",
                "fast_path": "verified-action-snapshot",
                "reused_nodes": len(nodeids),
                "fresh_nodes": 0,
                "published_nodes": 0,
                "unknown_nodes": 0,
                "reviewed_nodes": len(nodeids),
                "total_nodes": len(nodeids),
                "reuse_authorized": True,
                "profile_sha256": profile.profile_sha256,
                "collection_sha256": baseline_collection[
                    "collection_sha256"
                ],
                "stdout_tail": "",
                "stderr_tail": "",
            }
            store.append_event({"task": task.name, **payload})
            return payload

    requested_fresh: list[str] = []
    baseline_decisions = []
    baseline_unknown: list[str] = []
    baseline_rows: list[dict[str, Any]] = []
    guard_before: str | None = None
    guard_matches_snapshot = False

    if baseline_collection is not None and shared_review_authority_covered:
        baseline_ids = list(baseline_collection.get("nodeids", []))
        baseline_rows = [
            profile.nodes[nodeid]
            for nodeid in baseline_ids
            if node_review_authority_covered(nodeid)
        ]
        baseline_unknown = [
            nodeid
            for nodeid in baseline_ids
            if not node_review_authority_covered(nodeid)
        ]
        baseline_context = NodeCacheContext(
            runtime_identity=context_stub.runtime_identity,
            environment_fingerprint=context_stub.environment_fingerprint,
            collection_contract_sha256=str(
                baseline_collection["collection_sha256"]
            ),
            collection_plugin_sha256=context_stub.collection_plugin_sha256,
            independence_review_sha256=context_stub.independence_review_sha256,
            profile_sha256=context_stub.profile_sha256,
        )
        baseline_decisions = prepare_node_cache(
            manifest.root,
            task,
            node_rows=baseline_rows,
            session_closure=profile.session_closure,
            static_inputs=profile.static_inputs,
            collection_items=list(baseline_collection["items"]),
            context=baseline_context,
            store=store,
            session=pre_execution_session,
            trust_session_provider=lookup_trust_session,
        )
        requested_fresh = list(
            dict.fromkeys(
                initial_fresh_nodeids(baseline_decisions) + baseline_unknown
            )
        )
        try:
            guard_before = _collection_guard_sha256(
                manifest.root,
                profile,
                runtime_identity=runtime,
                environment_fingerprint=context_stub.environment_fingerprint,
                session=pre_execution_session,
            )
        except ConfigurationError:
            guard_before = None
        snapshot_guard = (
            snapshot.get("collection_guard_sha256")
            if snapshot is not None
            else None
        )
        guard_matches_snapshot = (
            isinstance(snapshot_guard, str)
            and guard_before == snapshot_guard
        )

        if (
            guard_matches_snapshot
            and not requested_fresh
            and not baseline_unknown
            and baseline_decisions
        ):
            fast_path_failure_reason: str | None = None
            try:
                if lookup_trust is None:
                    raise ConfigurationError(
                        "verified cache hits have no lookup trust session"
                    )
                # The first canonical node/source/collection pass was built
                # with pre_execution_session above. Close the shared HMAC
                # trust session now, then take the second canonical pass with
                # a new FingerprintSession. This ordering detects mutations
                # that occur from inside the potentially slow trust check.
                lookup_trust.verify_unchanged()
            except ConfigurationError as exc:
                fast_path_failure_reason = (
                    "cache lookup trust drift; fresh execution required: "
                    f"{exc}"
                )
            finally:
                lookup_trust = None

            if fast_path_failure_reason is None:
                try:
                    final_runtime = inspect_runtime(
                        task, repository_root=manifest.root
                    )
                    final_environment, final_environment_fingerprint = (
                        hermetic_environment(task)
                    )
                    runtime = final_runtime
                    environment = final_environment
                    final_static_inputs = discover_pytest_static_inputs(
                        manifest.root,
                        targets=profile.targets,
                    )
                    final_context = NodeCacheContext(
                        runtime_identity=final_runtime,
                        environment_fingerprint=final_environment_fingerprint,
                        collection_contract_sha256=str(
                            baseline_collection["collection_sha256"]
                        ),
                        collection_plugin_sha256=collection_plugin_sha256(),
                        independence_review_sha256=profile.independence_review_sha256,
                        profile_sha256=profile.profile_sha256,
                    )
                    fast_final_session = FingerprintSession(manifest.root)
                    verified_fast = verify_node_cache_snapshot(
                        manifest.root,
                        task,
                        decisions=baseline_decisions,
                        node_rows=baseline_rows,
                        session_closure=profile.session_closure,
                        static_inputs=profile.static_inputs,
                        collection_items=list(baseline_collection["items"]),
                        final_context=final_context,
                        session=fast_final_session,
                    )
                    guard_after = _collection_guard_sha256(
                        manifest.root,
                        profile,
                        runtime_identity=final_runtime,
                        environment_fingerprint=final_environment_fingerprint,
                        session=fast_final_session,
                    )
                    input_guard = _profile_input_guard_sha256(
                        manifest.root,
                        task,
                        profile,
                        runtime_identity=final_runtime,
                        environment_fingerprint=final_environment_fingerprint,
                        session=fast_final_session,
                    )
                    final_profile = load_pytest_profile(profile.path, manifest)
                    final_manifest_sha256 = manifest_sha256(manifest)
                except (ConfigurationError, OSError, RuntimeError) as exc:
                    fast_path_failure_reason = (
                        "post-trust source/action verification failed; "
                        f"fresh execution required: {exc}"
                    )
                else:
                    fast_hits = [
                        decision
                        for decision in verified_fast
                        if decision.status == "HIT_REUSED"
                    ]
                    if (
                        guard_after != guard_before
                        or len(fast_hits) != len(baseline_ids)
                        or any(
                            decision.status == "REJECTED_INPUT_RACE"
                            for decision in verified_fast
                        )
                        or final_runtime != context_stub.runtime_identity
                        or final_environment_fingerprint
                        != context_stub.environment_fingerprint
                        or final_static_inputs != profile.static_inputs
                        or final_profile.profile_sha256 != profile.profile_sha256
                        or final_manifest_sha256 != cache_manifest_digest
                    ):
                        fast_path_failure_reason = (
                            "post-trust source/action identity changed; "
                            "fresh execution required"
                        )

            if fast_path_failure_reason is None:
                cached_reference_ms = sum(
                    float(
                        (decision.cached or {}).get(
                            "execution_ms", 0.0
                        )
                        or 0.0
                    )
                    for decision in fast_hits
                )
                wall_ms = (
                    time.perf_counter() - wall_started
                ) * 1000.0
                saved_ms = max(0.0, cached_reference_ms)
                conservative_no_zerorun_ms = wall_ms + saved_ms
                effective_speedup = (
                    conservative_no_zerorun_ms / wall_ms
                    if wall_ms > 0
                    else 1.0
                )
                saved_percent = (
                    saved_ms / conservative_no_zerorun_ms * 100.0
                    if conservative_no_zerorun_ms > 0
                    else 0.0
                )
                payload = {
                    "status": "PYTEST_INCREMENTAL_PASS",
                    "exit_code": 0,
                    "wall_ms": round(wall_ms, 3),
                    "execution_ms": 0.0,
                    "saved_ms": round(saved_ms, 3),
                    "verified_saved_seconds": round(
                        saved_ms / 1000.0, 3
                    ),
                    "conservative_no_zerorun_ms": round(
                        conservative_no_zerorun_ms, 3
                    ),
                    "conservative_no_zerorun_seconds": round(
                        conservative_no_zerorun_ms / 1000.0, 3
                    ),
                    "effective_speedup": round(
                        effective_speedup, 3
                    ),
                    "saved_percent": round(saved_percent, 3),
                    "reuse_percent": 100.0,
                    "avoided_execution_reference_ms": round(
                        cached_reference_ms, 3
                    ),
                    "collection_overhead_ms": 0.0,
                    "single_pass_wall_ms": 0.0,
                    "selection_mode": "zero-miss-elided",
                    "reused_nodes": len(fast_hits),
                    "fresh_nodes": 0,
                    "published_nodes": 0,
                    "unknown_nodes": 0,
                    "reviewed_nodes": len(baseline_rows),
                    "total_nodes": len(baseline_ids),
                    "reuse_authorized": True,
                    "profile_sha256": profile.profile_sha256,
                    "collection_sha256": baseline_collection[
                        "collection_sha256"
                    ],
                    "stdout_tail": "",
                    "stderr_tail": "",
                }
                # A process may begin with only persisted HMAC evidence (for
                # example after an MCP worker restart). Once every node key has
                # been independently recomputed, every entry authenticated,
                # and the post-trust source guards agree, retain the same
                # conservative process-local aggregate proof a seed creates.
                _remember_verified_action_snapshot(
                    manifest.root,
                    task,
                    profile,
                    baseline_collection,
                    collection_guard_sha256=guard_after,
                    input_guard_sha256=input_guard,
                    reference_execution_ms=cached_reference_ms,
                )
                store.append_event({"task": task.name, **payload})
                return payload

            # Any failed trust or post-trust source pass invalidates the whole
            # reviewed baseline for this request. Run the complete current
            # target and prohibit publication; selectively retaining old hits
            # here would reintroduce the race this branch is meant to close.
            lookup_trust_rejected = True
            requested_fresh = list(baseline_ids)
            baseline_collection = None
            shared_review_authority_covered = False
            source_review_shared_covered = False
            source_review_failure_reason = fast_path_failure_reason
            source_review_covered_nodes = set()
            for decision in baseline_decisions:
                if decision.key is not None:
                    decision.cached = None
                    decision.status = "MISS_KEYED"
                    decision.reason = fast_path_failure_reason

    # The cheap persisted guard is only strong enough to authorize the
    # zero-container all-hit fast path. If it changed, the single-pass plugin
    # independently recollects the suite and may align only order-preserving
    # nodes whose structural identities still match. Added/changed nodes run
    # fresh. A changed static discovery surface, shared source-review anchor,
    # or qualification tool identity disables alignment entirely until the
    # reviewed profile is refreshed. Per-node anchor drift blocks only that
    # node and is included in the explicit fresh request below.
    execution_baseline = (
        baseline_collection if shared_review_authority_covered else None
    )
    execution_fresh = (
        requested_fresh if execution_baseline is not None else []
    )

    code, single_pass_ms, stdout, stderr, selection = execute_single_pass(
        manifest,
        task,
        profile,
        baseline_collection=execution_baseline,
        fresh_nodeids=execution_fresh,
        runtime_identity=runtime,
        environment=environment,
    )
    current_collection = selection["collection"]
    current_ids = list(current_collection["nodeids"])
    actual_fresh = list(selection["fresh_nodeids"])
    actual_reused = list(selection["reused_nodeids"])
    baseline_collection_accepted = (
        execution_baseline is not None
        and selection.get("mode") == "single-pass-incremental"
        and current_collection.get("collection_sha256")
        == execution_baseline.get("collection_sha256")
        and current_ids == list(execution_baseline.get("nodeids", []))
    )
    if not shared_review_authority_covered:
        # The reviewed profile does not describe the complete current pytest
        # discovery surface. Treat every node as unknown: execute all of them,
        # authorize no cache lookup, and publish no new entries.
        reviewed_rows = []
        unknown = list(current_ids)
        decisions = []
    elif baseline_collection_accepted:
        # These decisions were computed immediately before the pytest process
        # from the exact collection contract that the plugin just revalidated.
        # Recomputing them here is redundant. The independent final snapshot
        # below still recomputes every action key and rejects any request-local
        # source/runtime race before a hit is returned or a miss is published.
        reviewed_rows = baseline_rows
        unknown = baseline_unknown
        decisions = baseline_decisions
    else:
        reviewed_rows = [
            profile.nodes[nodeid]
            for nodeid in current_ids
            if node_review_authority_covered(nodeid)
        ]
        unknown = [
            nodeid
            for nodeid in current_ids
            if not node_review_authority_covered(nodeid)
        ]
        current_context = NodeCacheContext(
            runtime_identity=context_stub.runtime_identity,
            environment_fingerprint=context_stub.environment_fingerprint,
            collection_contract_sha256=str(
                current_collection["collection_sha256"]
            ),
            collection_plugin_sha256=context_stub.collection_plugin_sha256,
            independence_review_sha256=context_stub.independence_review_sha256,
            profile_sha256=context_stub.profile_sha256,
        )
        decisions = prepare_node_cache(
            manifest.root,
            task,
            node_rows=reviewed_rows,
            session_closure=profile.session_closure,
            static_inputs=profile.static_inputs,
            collection_items=list(current_collection["items"]),
            context=current_context,
            store=store,
            session=FingerprintSession(manifest.root),
            trust_session=lookup_trust,
            trust_session_provider=(
                lookup_trust_session if not lookup_trust_rejected else None
            ),
            allow_cache_lookup=not lookup_trust_rejected,
        )

    # Close the shared lookup authority before the final source/runtime pass.
    # Reuse has already been provisional inside the single pytest process; if
    # trust drifts, a whole-target recovery run replaces that provisional
    # result. Hashing before this slow attestation would leave a mutation
    # triggered from inside verify_unchanged invisible to the return path.
    lookup_trust_error: ConfigurationError | None = None
    if lookup_trust is not None:
        try:
            lookup_trust.verify_unchanged()
        except ConfigurationError as exc:
            lookup_trust_error = exc
        finally:
            lookup_trust = None
    elif actual_reused:
        lookup_trust_error = ConfigurationError(
            "selected reused nodes have no cache lookup trust session"
        )

    if lookup_trust_error is not None or (
        lookup_trust_rejected and bool(actual_reused)
    ):
        try:
            recovery_runtime = inspect_runtime(task, repository_root=manifest.root)
            recovery_environment, _ = hermetic_environment(task)
        except (ConfigurationError, OSError, RuntimeError):
            recovery_runtime = runtime
            recovery_environment = environment
        detail = (
            "pytest cache lookup authority changed during request; "
            "provisional reuse abandoned"
        )
        if lookup_trust_error is not None:
            detail += f"; cache lookup trust drift: {lookup_trust_error}"
        else:
            detail += "; cache lookup trust drift was detected before execution"
        return _recovery_result(
            manifest,
            task,
            profile,
            current_collection,
            runtime=recovery_runtime,
            environment=recovery_environment,
            reason=detail,
            wall_started=wall_started,
            store=store,
        )

    final_validation_error: ConfigurationError | None = None
    try:
        final_runtime = inspect_runtime(task, repository_root=manifest.root)
        final_environment, final_environment_fingerprint = hermetic_environment(task)
        final_static_inputs = discover_pytest_static_inputs(
            manifest.root,
            targets=profile.targets,
        )
        final_context = NodeCacheContext(
            runtime_identity=final_runtime,
            environment_fingerprint=final_environment_fingerprint,
            collection_contract_sha256=str(
                current_collection["collection_sha256"]
            ),
            collection_plugin_sha256=collection_plugin_sha256(),
            independence_review_sha256=profile.independence_review_sha256,
            profile_sha256=profile.profile_sha256,
        )
        final_verification_session = FingerprintSession(manifest.root)
        verified = verify_node_cache_snapshot(
            manifest.root,
            task,
            decisions=decisions,
            node_rows=reviewed_rows,
            session_closure=profile.session_closure,
            static_inputs=profile.static_inputs,
            collection_items=list(current_collection["items"]),
            final_context=final_context,
            session=final_verification_session,
        )
        final_guard = _collection_guard_sha256(
            manifest.root,
            profile,
            runtime_identity=final_runtime,
            environment_fingerprint=final_environment_fingerprint,
            session=final_verification_session,
        )
        final_profile = load_pytest_profile(profile.path, manifest)
        final_manifest_sha256 = manifest_sha256(manifest)
    except (ConfigurationError, OSError, RuntimeError) as exc:
        final_validation_error = ConfigurationError(
            f"post-trust source/action verification failed: {exc}"
        )
        final_runtime = runtime
        final_environment = environment
        final_environment_fingerprint = context_stub.environment_fingerprint
        final_verification_session = FingerprintSession(manifest.root)
        verified = []
        final_guard = None
    else:
        runtime = final_runtime
        environment = final_environment
        if (
            final_runtime != context_stub.runtime_identity
            or final_environment_fingerprint
            != context_stub.environment_fingerprint
            or final_static_inputs != profile.static_inputs
            or final_profile.profile_sha256 != profile.profile_sha256
            or final_manifest_sha256 != cache_manifest_digest
            or (bool(actual_reused) and final_guard != guard_before)
        ):
            final_validation_error = ConfigurationError(
                "post-trust runtime/environment/profile/source surface changed"
            )
    verified_by_node = {decision.nodeid: decision for decision in verified}
    unsafe_reuse = [
        nodeid
        for nodeid in actual_reused
        if nodeid not in verified_by_node
        or verified_by_node[nodeid].status != "HIT_REUSED"
    ]
    final_rejected = any(
        decision.status == "REJECTED_INPUT_RACE" for decision in verified
    )
    if actual_reused and (
        final_validation_error is not None or final_rejected or unsafe_reuse
    ):
        detail = (
            "pytest source/action identity changed during request; "
            "provisional reuse abandoned"
        )
        if unsafe_reuse:
            detail += f"; {len(unsafe_reuse)} selected reused nodes were not final verified hits"
        if final_validation_error is not None:
            detail += f"; {final_validation_error}"
        return _recovery_result(
            manifest,
            task,
            profile,
            current_collection,
            runtime=final_runtime,
            environment=final_environment,
            reason=detail,
            wall_started=wall_started,
            store=store,
        )
    if final_validation_error is not None or final_rejected:
        # No cached outcome influenced this execution, so a second execution
        # would add no safety. Keep the already-fresh result but discard every
        # publication candidate and aggregate authority derived from the
        # unstable final snapshot.
        verified = []
        verified_by_node = {}
        shared_review_authority_covered = False
        source_review_shared_covered = False

    successful = set(actual_fresh) if code == 0 else set()
    per_node = single_pass_ms / len(actual_fresh) if actual_fresh else 0.0
    published = (
        publish_verified_successes(
            manifest.root,
            verified,
            successful_nodeids=successful,
            execution_ms_by_node={
                nodeid: per_node for nodeid in actual_fresh
            },
            store=store,
            manifest_digest=cache_manifest_digest,
        )
        if code == 0
        else 0
    )
    reused = [
        verified_by_node[nodeid]
        for nodeid in actual_reused
        if nodeid in verified_by_node
        and verified_by_node[nodeid].status == "HIT_REUSED"
    ]
    cached_reference_ms = sum(
        float((decision.cached or {}).get("execution_ms", 0.0) or 0.0)
        for decision in reused
    )
    wall_ms = (time.perf_counter() - wall_started) * 1000.0
    saved_ms = max(0.0, cached_reference_ms)
    conservative_no_zerorun_ms = wall_ms + saved_ms
    effective_speedup = (
        conservative_no_zerorun_ms / wall_ms if wall_ms > 0 else 1.0
    )
    saved_percent = (
        saved_ms / conservative_no_zerorun_ms * 100.0
        if conservative_no_zerorun_ms > 0
        else 0.0
    )
    reuse_percent = (
        len(reused) / len(current_ids) * 100.0 if current_ids else 0.0
    )
    status = (
        "PYTEST_INCREMENTAL_PASS"
        if code == 0
        else "PYTEST_INCREMENTAL_FAIL"
    )
    authority_blocked_current = [
        nodeid
        for nodeid in current_ids
        if nodeid in profile.nodes
        and not node_review_authority_covered(nodeid)
    ]
    if not static_discovery_covered:
        unknown_refresh_reason = (
            "pytest static discovery surface changed; refresh the reviewed profile "
            "before reuse"
        )
    elif not source_review_shared_covered:
        unknown_refresh_reason = source_review_failure_reason or (
            "pytest shared source-review authority changed; explicit requalification "
            "is required"
        )
    elif authority_blocked_current:
        unknown_refresh_reason = (
            "pytest node source-review authority changed; explicit requalification "
            "is required before reuse or publication"
        )
    else:
        unknown_refresh_reason = None
    (
        profile_refresh_required,
        profile_refresh_node_count,
        profile_refresh_reasons,
    ) = _profile_refresh_summary(
        verified,
        unknown_nodeids=unknown,
        unknown_reason=unknown_refresh_reason,
    )
    payload = {
        "status": status,
        "exit_code": code,
        "wall_ms": round(wall_ms, 3),
        "execution_ms": round(single_pass_ms, 3),
        "saved_ms": round(saved_ms, 3),
        "verified_saved_seconds": round(saved_ms / 1000.0, 3),
        "conservative_no_zerorun_ms": round(
            conservative_no_zerorun_ms, 3
        ),
        "conservative_no_zerorun_seconds": round(
            conservative_no_zerorun_ms / 1000.0, 3
        ),
        "effective_speedup": round(effective_speedup, 3),
        "saved_percent": round(saved_percent, 3),
        "reuse_percent": round(reuse_percent, 3),
        "avoided_execution_reference_ms": round(
            cached_reference_ms, 3
        ),
        "collection_overhead_ms": 0.0,
        "single_pass_wall_ms": round(single_pass_ms, 3),
        "selection_mode": selection.get("mode"),
        "reused_nodes": len(reused),
        "fresh_nodes": len(actual_fresh),
        "published_nodes": published,
        "unknown_nodes": len(unknown),
        "reviewed_nodes": len(reviewed_rows),
        "total_nodes": len(current_ids),
        "reuse_authorized": bool(reused),
        "profile_sha256": profile.profile_sha256,
        "profile_refresh_required": bool(
            code == 0 and profile_refresh_required
        ),
        "profile_refresh_node_count": (
            profile_refresh_node_count if code == 0 else 0
        ),
        "profile_refresh_reasons": (
            profile_refresh_reasons if code == 0 else []
        ),
        "collection_sha256": current_collection["collection_sha256"],
        "stdout_tail": stdout[-4000:].decode(
            "utf-8", errors="replace"
        ),
        "stderr_tail": stderr[-4000:].decode(
            "utf-8", errors="replace"
        ),
    }
    if code == 0:
        try:
            final_guard = _collection_guard_sha256(
                manifest.root,
                profile,
                runtime_identity=final_runtime,
                environment_fingerprint=final_environment_fingerprint,
                session=final_verification_session,
            )
        except ConfigurationError:
            final_guard = None
        _save_collection_snapshot(
            manifest,
            profile,
            current_collection,
            collection_guard_sha256=final_guard,
            manifest_digest=cache_manifest_digest,
            store=store,
        )

        all_nodes_verified = (
            not unknown
            and len(verified_by_node) == len(current_ids)
            and all(
                verified_by_node[nodeid].status
                in {"HIT_REUSED", "MISS_VERIFIED"}
                and verified_by_node[nodeid].key is not None
                and verified_by_node[nodeid].fingerprint is not None
                for nodeid in current_ids
            )
            and not profile_refresh_required
        )
        if all_nodes_verified:
            try:
                input_guard = _profile_input_guard_sha256(
                    manifest.root,
                    task,
                    profile,
                    runtime_identity=final_runtime,
                    environment_fingerprint=final_environment_fingerprint,
                    session=final_verification_session,
                )
            except ConfigurationError:
                input_guard = None
            fresh_reference_ms = (
                per_node * len(actual_fresh)
                if actual_fresh
                else 0.0
            )
            _remember_verified_action_snapshot(
                manifest.root,
                task,
                profile,
                current_collection,
                collection_guard_sha256=final_guard,
                input_guard_sha256=input_guard,
                reference_execution_ms=(
                    cached_reference_ms + fresh_reference_ms
                ),
            )
            semantic_runtime_identity = _semantic_suite_runtime_identity(task)
            if semantic_runtime_identity is not None:
                try:
                    expected_suite_guards = _suite_guard_snapshot(
                        manifest.root,
                        task,
                        profile,
                        runtime_identity=semantic_runtime_identity,
                        environment_fingerprint=final_environment_fingerprint,
                        session=final_verification_session,
                    )
                except (ConfigurationError, OSError, RuntimeError):
                    expected_suite_guards = None
                if expected_suite_guards is not None:
                    composition_decisions = [
                        verified_by_node[nodeid] for nodeid in current_ids
                    ]
                    _save_signed_suite_result(
                        manifest,
                        task,
                        profile,
                        current_collection,
                        decisions=composition_decisions,
                        runtime_identity=final_runtime,
                        environment_fingerprint=final_environment_fingerprint,
                        expected_guards=expected_suite_guards,
                        reference_execution_ms=(
                            cached_reference_ms + fresh_reference_ms
                        ),
                        store=store,
                        manifest_digest=cache_manifest_digest,
                    )
        else:
            _VERIFIED_ACTION_SNAPSHOTS.pop(
                _verified_action_snapshot_key(manifest.root, profile),
                None,
            )
    store.append_event({"task": task.name, **payload})
    return payload

def run_pytest_from_paths(manifest_path: Path, profile_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    profile = load_pytest_profile(profile_path, manifest)
    return run_pytest_profile(manifest, profile)
