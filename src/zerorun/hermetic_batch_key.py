from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .fingerprint import (
    command_sources,
    execution_directory_records,
)
from .hermetic_key import (
    FingerprintSession as _BaseFingerprintSession,
    _content_only,
    _load_symbol_source,
    _symbol_content_only,
    _symbol_record_from_loaded,
    task_fingerprint_v2 as _task_fingerprint_v2,
)
from .model import ConfigurationError, Manifest, TaskSpec
from .oci import hermetic_environment


class FingerprintSession(_BaseFingerprintSession):
    """Batch snapshot with selector-projection reuse inside one source pass.

    The base session already shares file content and parsed Python sources. A
    large reviewed suite can still request the same exact selector from several
    tasks, so this session additionally memoizes the immutable projection record
    for that selector during the same explicit snapshot. A new session is still
    mandatory for the final verification pass.
    """

    def __init__(self, root: Path):
        super().__init__(root)
        self.symbol_records: dict[str, dict[str, Any]] = {}
        # Request-local pytest caches. These never span an observable source
        # snapshot: prepare and final verification each construct a new
        # FingerprintSession. They only eliminate duplicate projection work
        # shared by many node keys in the same snapshot.
        self.pytest_closure_fingerprints: dict[
            tuple[tuple[str, ...], tuple[str, ...]],
            tuple[str, dict[str, Any]],
        ] = {}
        self.pytest_closure_failures: dict[
            tuple[tuple[str, ...], tuple[str, ...]],
            str,
        ] = {}
        self.pytest_static_input_records: dict[
            tuple[str, ...], list[dict[str, Any]]
        ] = {}


def _symbol_records_cached(
    root: Path,
    selectors: tuple[str, ...],
    *,
    session: FingerprintSession,
) -> list[dict[str, Any]]:
    if not selectors:
        return []
    session.validate_root(root)
    root_resolved = session.root
    records: list[dict[str, Any]] = []
    for selector in selectors:
        cached = session.symbol_records.get(selector)
        if cached is not None:
            records.append(cached)
            continue
        if "::" not in selector:
            raise ConfigurationError(
                f"invalid input_symbols selector {selector!r}; expected relative.py::qualified.symbol"
            )
        path_text, symbol = selector.rsplit("::", 1)
        if not path_text or not symbol:
            raise ConfigurationError(
                f"invalid input_symbols selector {selector!r}; expected relative.py::qualified.symbol"
            )
        loaded = session.symbol_sources.get(path_text)
        if loaded is None:
            loaded = _load_symbol_source(root, root_resolved, path_text)
            session.symbol_sources[path_text] = loaded
        record = _symbol_record_from_loaded(
            selector=selector,
            path_text=path_text,
            symbol=symbol,
            loaded=loaded,
        )
        session.symbol_records[selector] = record
        records.append(record)
    return records


def task_fingerprint_v2(
    manifest: Manifest,
    task: TaskSpec,
    runtime_identity: dict[str, Any],
    *,
    environment_fingerprint: dict[str, dict[str, Any]] | None = None,
    session: FingerprintSession | None = None,
) -> tuple[str, dict[str, Any]]:
    """Produce the exact v2 action key while sharing selector projections.

    Calls without a batch session delegate to the canonical implementation. The
    optimized path intentionally duplicates only the payload assembly so the
    resulting key remains byte-for-byte equivalent to hermetic_key.task_fingerprint_v2.
    """
    if session is None:
        return _task_fingerprint_v2(
            manifest,
            task,
            runtime_identity,
            environment_fingerprint=environment_fingerprint,
        )
    if manifest.version != 2:
        raise ConfigurationError("hermetic fingerprint requires manifest version 2")
    if not task.result_only or task.outputs:
        raise ConfigurationError(
            f"task {task.name!r}: hermetic reuse requires result_only with no outputs"
        )
    if environment_fingerprint is None:
        _, environment_fingerprint = hermetic_environment(task)
    for pattern in task.inputs:
        session.validate_relative(manifest.root, pattern, field="input")
    session.validate_root(manifest.root)

    input_records = _content_only(
        session.expand_inputs(manifest.root, task.inputs)
    )
    for record in input_records:
        record_type = record.get("type")
        record_path = str(record.get("path", ""))
        if record_type in {"missing", "symlink", "unsupported"}:
            raise ConfigurationError(
                f"task {task.name!r}: hermetic source closure contains unsupported {record_type}: "
                f"{record_path or record.get('pattern')}"
            )
        if record_path == ".zerorun" or record_path.startswith(".zerorun/"):
            raise ConfigurationError(
                f"task {task.name!r}: hermetic source closure cannot include .zerorun state"
            )
        if record_path == ".git" or record_path.startswith(".git/"):
            raise ConfigurationError(
                f"task {task.name!r}: hermetic source closure cannot include .git state"
            )

    command_source_records = _content_only(
        command_sources(
            task.command,
            manifest.root,
            path_record_cache=session.path_records,
        )
    )
    for record in command_source_records:
        record_type = record.get("type")
        record_path = str(record.get("path", ""))
        if record_type in {"missing", "symlink", "unsupported"}:
            raise ConfigurationError(
                f"task {task.name!r}: hermetic command source contains unsupported "
                f"{record_type}: {record_path or record.get('pattern')}"
            )
        if record_path == ".zerorun" or record_path.startswith(".zerorun/"):
            raise ConfigurationError(
                f"task {task.name!r}: hermetic command source cannot include .zerorun state"
            )
        if record_path == ".git" or record_path.startswith(".git/"):
            raise ConfigurationError(
                f"task {task.name!r}: hermetic command source cannot include .git state"
            )

    symbol_records = _symbol_content_only(
        _symbol_records_cached(manifest.root, task.input_symbols, session=session)
    )
    execution_paths = {
        str(record["path"])
        for record in (*input_records, *command_source_records)
        if isinstance(record.get("path"), str)
    }
    execution_paths.update(
        selector.rsplit("::", 1)[0] for selector in task.input_symbols
    )
    execution_directories = execution_directory_records(
        manifest.root,
        execution_paths,
    )
    payload: dict[str, Any] = {
        "schema": 8 if symbol_records else 7,
        "kind": "hermetic-result-only",
        "task": task.name,
        "command": list(task.command),
        "command_sources": command_source_records,
        "inputs": input_records,
        "input_symbols": symbol_records,
        "execution_directories": execution_directories,
        "environment": environment_fingerprint,
        "runtime_identity": runtime_identity,
        "policy": {
            "cacheable": task.cacheable,
            "result_only": task.result_only,
            "closure_reviewed": task.closure_reviewed,
            "unsafe_effects": list(task.unsafe_effects),
            "network": "none",
            "checkout": "read-only",
            "execution_workspace": "private-exact-reviewed-closure-v1",
            "workspace_metadata": "content-and-mode-preserved-times-zeroed-v1",
            "symbol_closure": "operator-reviewed" if symbol_records else "none",
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), payload
