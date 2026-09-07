from __future__ import annotations

import ast
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import threading
from types import MappingProxyType
from typing import Any

from .fingerprint import (
    _cache_path_key,
    _security_path_key,
    command_sources,
    execution_directory_records,
    expand_expected_absent_inputs,
    expand_inputs,
    read_stable_file_bytes,
    validate_relative,
)
from .model import ConfigurationError, Manifest, TaskSpec
from .oci import hermetic_environment
from .path_safety import is_link_like


def _content_only(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item.pop("mtime_ns", None)
        cleaned.append(item)
    return cleaned


def _symbol_content_only(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove diagnostic source positions from reviewed-symbol action identity.

    Line ranges help explain a selector, but moving an unchanged definition up
    or down in a file does not change that definition's behavior. The selector,
    projection kind and projected-content SHA remain in the action key.
    """
    cleaned: list[dict[str, Any]] = []
    for record in records:
        item = dict(record)
        item.pop("start_line", None)
        item.pop("end_line", None)
        cleaned.append(item)
    return cleaned


def _node_start_line(node: ast.AST) -> int:
    lines = [int(getattr(node, "lineno", 0) or 0)]
    for decorator in getattr(node, "decorator_list", ()):
        lines.append(int(getattr(decorator, "lineno", 0) or 0))
    return min(line for line in lines if line > 0)


def _qualified_symbols(tree: ast.AST) -> dict[str, tuple[ast.AST, ...]]:
    """Index every definition for a qualified name in source order.

    Python typing overloads and conditional definitions legitimately create
    multiple AST nodes with the same qualified name. A reviewed selector must
    not pick one arbitrarily: fingerprinting every matching definition is the
    conservative representation because a change to any candidate declaration
    or implementation invalidates the action key.
    """
    symbols: dict[str, list[ast.AST]] = {}

    def walk(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = ".".join((*prefix, node.name))
                symbols.setdefault(qualified, []).append(node)
                if isinstance(node, ast.ClassDef):
                    walk(node.body, (*prefix, node.name))

    walk(getattr(tree, "body", []))
    return {name: tuple(nodes) for name, nodes in symbols.items()}


def _function_header(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Any]:
    return {
        "kind": type(node).__name__,
        "name": node.name,
        "args": ast.dump(node.args, include_attributes=False),
        "decorators": [ast.dump(item, include_attributes=False) for item in node.decorator_list],
        "returns": ast.dump(node.returns, include_attributes=False) if node.returns else None,
        "type_comment": node.type_comment,
    }


def _class_header(node: ast.ClassDef) -> dict[str, Any]:
    return {
        "kind": "ClassDef",
        "name": node.name,
        "bases": [ast.dump(item, include_attributes=False) for item in node.bases],
        "keywords": [ast.dump(item, include_attributes=False) for item in node.keywords],
        "decorators": [ast.dump(item, include_attributes=False) for item in node.decorator_list],
    }


def _json_safe_ast_scalar(value: Any) -> Any:
    """Encode scalar AST fields deterministically without accepting arbitrary objects."""
    if value is Ellipsis:
        return {"python_literal": "ellipsis"}
    if isinstance(value, bytes):
        return {"python_literal": "bytes", "hex": value.hex()}
    if isinstance(value, complex):
        return {
            "python_literal": "complex",
            "real": repr(value.real),
            "imag": repr(value.imag),
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ConfigurationError(
        f"unsupported Python AST scalar while fingerprinting reviewed source: {type(value).__name__}"
    )


def _definition_time_value(value: Any) -> Any:
    """Project import/class/function definition-time behavior without function bodies.

    Function bodies are not executed when their definition is created, so they
    may be fingerprinted separately as reviewed symbols. Function defaults,
    annotations and decorators do execute at definition time and are retained.
    Class bodies execute immediately, so their non-method state and nested
    definition-time state are retained recursively while method bodies are
    omitted.
    """
    if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return {"definition": _function_header(value)}
    if isinstance(value, ast.ClassDef):
        return {
            "definition": _class_header(value),
            "body": [_definition_time_value(item) for item in value.body],
        }
    if isinstance(value, ast.AST):
        return {
            "kind": type(value).__name__,
            "fields": {
                field: _definition_time_value(child)
                for field, child in ast.iter_fields(value)
            },
        }
    if isinstance(value, list):
        return [_definition_time_value(item) for item in value]
    return _json_safe_ast_scalar(value)


def _definition_projection(body: list[ast.stmt]) -> list[Any]:
    return [_definition_time_value(node) for node in body]


def _state_projection(body: list[ast.stmt]) -> list[Any]:
    """Project all executable definition-time state, excluding function bodies."""
    return _definition_projection(body)


def _module_projection(tree: ast.Module) -> str:
    return json.dumps(_definition_projection(tree.body), sort_keys=True, separators=(",", ":"))


def _module_state_projection(tree: ast.Module) -> str:
    return json.dumps(_state_projection(tree.body), sort_keys=True, separators=(",", ":"))


def _class_definition_payload(node: ast.ClassDef) -> dict[str, Any]:
    return {
        "header": _class_header(node),
        "body_definition_state": _definition_projection(node.body),
    }


def _class_projection(node: ast.ClassDef) -> str:
    return json.dumps(_class_definition_payload(node), sort_keys=True, separators=(",", ":"))


def _class_state_projection(node: ast.ClassDef) -> str:
    payload = {
        "header": _class_header(node),
        "body_state": _state_projection(node.body),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _target_names(target: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts:
            names.update(_target_names(item))
    return names


def _direct_bound_names(node: ast.stmt) -> tuple[set[str], bool]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}, False
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}, False
    if isinstance(node, ast.ImportFrom):
        star = any(alias.name == "*" for alias in node.names)
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}, star
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets: list[ast.AST]
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            targets = [node.target]
        names: set[str] = set()
        for target in targets:
            names.update(_target_names(target))
        return names, False
    return set(), False


def _nested_bound_names(node: ast.stmt) -> tuple[set[str], bool]:
    names, star = _direct_bound_names(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return names, star
    child_bodies: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list):
            child_bodies.append([item for item in value if isinstance(item, ast.stmt)])
    handlers = getattr(node, "handlers", None)
    if isinstance(handlers, list):
        for handler in handlers:
            body = getattr(handler, "body", None)
            if isinstance(body, list):
                child_bodies.append([item for item in body if isinstance(item, ast.stmt)])
    for body in child_bodies:
        for child in body:
            child_names, child_star = _nested_bound_names(child)
            names.update(child_names)
            star = star or child_star
    return names, star


def _binding_index(tree: ast.Module) -> tuple[dict[str, tuple[Any, ...]], tuple[str, ...]]:
    indexed: dict[str, list[Any]] = {}
    star_imports: list[str] = []
    for node in tree.body:
        bound, star = _nested_bound_names(node)
        if star:
            star_imports.append(ast.dump(node, include_attributes=False))
        if not bound:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            payload: Any = _function_header(node)
        elif isinstance(node, ast.ClassDef):
            payload = _class_definition_payload(node)
        else:
            payload = ast.dump(node, include_attributes=False)
        for name in bound:
            indexed.setdefault(name, []).append(payload)
    return {name: tuple(values) for name, values in indexed.items()}, tuple(star_imports)


def _binding_projection(
    bindings: dict[str, tuple[Any, ...]],
    star_imports: tuple[str, ...],
    name: str,
) -> str:
    matches = bindings.get(name, ())
    if not matches and not star_imports:
        raise ConfigurationError(f"reviewed module binding not found: {name}")
    return json.dumps(
        {"name": name, "bindings": list(matches), "star_imports": list(star_imports)},
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class _LoadedSymbolMatch:
    kind: str
    start_line: int
    end_line: int | None
    source: str | None
    class_projection: str | None
    class_state_projection: str | None


@dataclass(frozen=True)
class _LoadedSymbolSource:
    content_sha256: str
    line_count: int
    module_projection: str
    module_state_projection: str
    matches: Mapping[str, tuple[_LoadedSymbolMatch, ...]]
    binding_projections: Mapping[str, str]
    star_imports: tuple[str, ...]


@dataclass(frozen=True)
class _SymbolProjection:
    kind: str
    start_line: int
    end_line: int
    sha256: str


_SYMBOL_ANALYSIS_CACHE_MAX_ENTRIES = 128
_SYMBOL_ANALYSIS_CACHE_MAX_WEIGHT = 32 * 1024 * 1024
_SYMBOL_PROJECTION_CACHE_MAX_ENTRIES = 16_384
_SYMBOL_PROJECTION_CACHE_MAX_WEIGHT = 8 * 1024 * 1024
_SYMBOL_CACHE_LOCK = threading.RLock()
_SYMBOL_ANALYSIS_CACHE: OrderedDict[
    str, tuple[int, _LoadedSymbolSource]
] = OrderedDict()
_SYMBOL_PROJECTION_CACHE: OrderedDict[
    tuple[str, str, str], tuple[int, _SymbolProjection]
] = OrderedDict()
_SYMBOL_ANALYSIS_CACHE_WEIGHT = 0
_SYMBOL_PROJECTION_CACHE_WEIGHT = 0


def _reset_symbol_content_caches_after_fork() -> None:
    """Discard inherited cache state without touching a possibly locked mutex."""

    global _SYMBOL_CACHE_LOCK
    global _SYMBOL_ANALYSIS_CACHE, _SYMBOL_PROJECTION_CACHE
    global _SYMBOL_ANALYSIS_CACHE_WEIGHT, _SYMBOL_PROJECTION_CACHE_WEIGHT
    _SYMBOL_CACHE_LOCK = threading.RLock()
    _SYMBOL_ANALYSIS_CACHE = OrderedDict()
    _SYMBOL_PROJECTION_CACHE = OrderedDict()
    _SYMBOL_ANALYSIS_CACHE_WEIGHT = 0
    _SYMBOL_PROJECTION_CACHE_WEIGHT = 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_symbol_content_caches_after_fork)


def clear_symbol_content_caches() -> None:
    """Clear bounded process-local parse/projection hints (primarily for tests)."""

    global _SYMBOL_ANALYSIS_CACHE_WEIGHT, _SYMBOL_PROJECTION_CACHE_WEIGHT
    with _SYMBOL_CACHE_LOCK:
        _SYMBOL_ANALYSIS_CACHE.clear()
        _SYMBOL_PROJECTION_CACHE.clear()
        _SYMBOL_ANALYSIS_CACHE_WEIGHT = 0
        _SYMBOL_PROJECTION_CACHE_WEIGHT = 0


def _analysis_weight(loaded: _LoadedSymbolSource) -> int:
    weight = (
        len(loaded.module_projection.encode("utf-8"))
        + len(loaded.module_state_projection.encode("utf-8"))
        + sum(len(value.encode("utf-8")) for value in loaded.binding_projections.values())
        + sum(len(value.encode("utf-8")) for value in loaded.star_imports)
    )
    for name, matches in loaded.matches.items():
        weight += len(name.encode("utf-8"))
        for match in matches:
            weight += len(match.kind.encode("utf-8")) + 64
            for value in (
                match.source,
                match.class_projection,
                match.class_state_projection,
            ):
                if value is not None:
                    weight += len(value.encode("utf-8"))
    return weight


def _analysis_cache_get(content_sha256: str) -> _LoadedSymbolSource | None:
    with _SYMBOL_CACHE_LOCK:
        cached = _SYMBOL_ANALYSIS_CACHE.get(content_sha256)
        if cached is None:
            return None
        _SYMBOL_ANALYSIS_CACHE.move_to_end(content_sha256)
        return cached[1]


def _analysis_cache_put(loaded: _LoadedSymbolSource) -> _LoadedSymbolSource:
    global _SYMBOL_ANALYSIS_CACHE_WEIGHT
    weight = _analysis_weight(loaded)
    if weight > _SYMBOL_ANALYSIS_CACHE_MAX_WEIGHT:
        return loaded
    with _SYMBOL_CACHE_LOCK:
        existing = _SYMBOL_ANALYSIS_CACHE.pop(loaded.content_sha256, None)
        if existing is not None:
            _SYMBOL_ANALYSIS_CACHE_WEIGHT -= existing[0]
        _SYMBOL_ANALYSIS_CACHE[loaded.content_sha256] = (weight, loaded)
        _SYMBOL_ANALYSIS_CACHE_WEIGHT += weight
        while (
            len(_SYMBOL_ANALYSIS_CACHE) > _SYMBOL_ANALYSIS_CACHE_MAX_ENTRIES
            or _SYMBOL_ANALYSIS_CACHE_WEIGHT > _SYMBOL_ANALYSIS_CACHE_MAX_WEIGHT
        ):
            _, (removed_weight, _) = _SYMBOL_ANALYSIS_CACHE.popitem(last=False)
            _SYMBOL_ANALYSIS_CACHE_WEIGHT -= removed_weight
    return loaded


def _projection_cache_get(
    key: tuple[str, str, str],
) -> _SymbolProjection | None:
    with _SYMBOL_CACHE_LOCK:
        cached = _SYMBOL_PROJECTION_CACHE.get(key)
        if cached is None:
            return None
        _SYMBOL_PROJECTION_CACHE.move_to_end(key)
        return cached[1]


def _projection_cache_put(
    key: tuple[str, str, str],
    projection: _SymbolProjection,
) -> _SymbolProjection:
    global _SYMBOL_PROJECTION_CACHE_WEIGHT
    weight = sum(len(item.encode("utf-8")) for item in key) + 160
    if weight > _SYMBOL_PROJECTION_CACHE_MAX_WEIGHT:
        return projection
    with _SYMBOL_CACHE_LOCK:
        existing = _SYMBOL_PROJECTION_CACHE.pop(key, None)
        if existing is not None:
            _SYMBOL_PROJECTION_CACHE_WEIGHT -= existing[0]
        _SYMBOL_PROJECTION_CACHE[key] = (weight, projection)
        _SYMBOL_PROJECTION_CACHE_WEIGHT += weight
        while (
            len(_SYMBOL_PROJECTION_CACHE) > _SYMBOL_PROJECTION_CACHE_MAX_ENTRIES
            or _SYMBOL_PROJECTION_CACHE_WEIGHT > _SYMBOL_PROJECTION_CACHE_MAX_WEIGHT
        ):
            _, (removed_weight, _) = _SYMBOL_PROJECTION_CACHE.popitem(last=False)
            _SYMBOL_PROJECTION_CACHE_WEIGHT -= removed_weight
    return projection


@dataclass
class FingerprintSession:
    """Caches file reads only within one explicit source-snapshot pass.

    A session must never span task execution or an independently observable
    request.  The batch runner creates one session before execution and a new
    session afterward, then compares every action fingerprint before it returns
    a hit or publishes a successful miss.
    """

    root: Path
    path_records: dict[str, dict[str, Any]]
    symbol_sources: dict[str, _LoadedSymbolSource]

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        self.path_records = {}
        self.symbol_sources = {}
        # Every cache below is scoped to this one explicit snapshot.  Runtime
        # preflight and final verification construct independent sessions, so
        # absence, membership, path and content are all re-observed before a
        # result can be reused or published.
        self.relative_paths: dict[str, Path] = {}
        root_cache_key = _cache_path_key(self.root)
        self.root_security_key = _security_path_key(self.root)
        self.directory_resolutions: dict[str, str] = {
            root_cache_key: str(self.root)
        }
        self.path_lstats: dict[str, os.stat_result | None] = {}
        self.directory_entries: dict[str, tuple[str, ...]] = {}
        self.pattern_expansions: dict[
            str, tuple[tuple[tuple[str, Any], ...], ...]
        ] = {}
        self.state_dir_resolved = (self.root / ".zerorun").resolve(strict=False)
        self.state_dir_security_key = _security_path_key(self.state_dir_resolved)
        self.snapshot_lock = threading.RLock()

    def validate_root(self, root: Path) -> None:
        candidate = root
        if candidate != self.root:
            candidate = candidate.resolve(strict=True)
        if candidate != self.root:
            raise ConfigurationError("fingerprint session cannot span project roots")

    def validate_relative(self, root: Path, raw: str, *, field: str) -> Path:
        self.validate_root(root)
        with self.snapshot_lock:
            return validate_relative(
                root,
                raw,
                field=field,
                resolved_root=self.root,
                validation_cache=self.relative_paths,
                directory_resolution_cache=self.directory_resolutions,
                lstat_cache=self.path_lstats,
                state_dir_resolved=self.state_dir_resolved,
                root_security_key=self.root_security_key,
                state_dir_security_key=self.state_dir_security_key,
            )

    def expand_inputs(
        self,
        root: Path,
        patterns: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        self.validate_root(root)
        return expand_inputs(
            root,
            patterns,
            path_record_cache=self.path_records,
            validation_cache=self.relative_paths,
            directory_resolution_cache=self.directory_resolutions,
            lstat_cache=self.path_lstats,
            directory_entries_cache=self.directory_entries,
            pattern_expansion_cache=self.pattern_expansions,
            resolved_root=self.root,
            state_dir_resolved=self.state_dir_resolved,
            root_security_key=self.root_security_key,
            state_dir_security_key=self.state_dir_security_key,
            cache_lock=self.snapshot_lock,
        )

    def expand_expected_absent_inputs(
        self,
        root: Path,
        patterns: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Fingerprint reviewed expected absences inside this one snapshot."""

        self.validate_root(root)
        with self.snapshot_lock:
            return expand_expected_absent_inputs(
                root,
                patterns,
                path_record_cache=self.path_records,
                validation_cache=self.relative_paths,
                directory_resolution_cache=self.directory_resolutions,
                lstat_cache=self.path_lstats,
                directory_entries_cache=self.directory_entries,
                pattern_expansion_cache=self.pattern_expansions,
                resolved_root=self.root,
                state_dir_resolved=self.state_dir_resolved,
                root_security_key=self.root_security_key,
                state_dir_security_key=self.state_dir_security_key,
            )


def _load_symbol_source(
    root: Path,
    root_resolved: Path,
    path_text: str,
) -> _LoadedSymbolSource:
    validate_relative(root, path_text, field="input_symbol")
    path = root / path_text
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"input symbol source is missing or outside project: {path_text}") from exc
    if is_link_like(path) or not path.is_file():
        raise ConfigurationError(f"input symbol source must be a regular file: {path_text}")
    try:
        source_bytes = read_stable_file_bytes(path)
    except OSError as exc:
        raise ConfigurationError(f"could not read input symbol source {path_text}: {exc}") from exc
    content_sha256 = hashlib.sha256(source_bytes).hexdigest()
    cached = _analysis_cache_get(content_sha256)
    if cached is not None:
        return cached
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"could not read input symbol source {path_text}: {exc}") from exc
    try:
        tree = ast.parse(source, filename=path_text)
    except SyntaxError as exc:
        raise ConfigurationError(f"could not parse input symbol source {path_text}: {exc}") from exc
    # Keep the decoded raw newline spelling in source-range projections.  This
    # deliberately makes LF and CRLF definitions distinct action fingerprints;
    # the process cache must never normalize byte-distinct reviewed source.
    lines = tuple(source.splitlines(keepends=True))
    bindings, star_imports = _binding_index(tree)
    immutable_matches: dict[str, tuple[_LoadedSymbolMatch, ...]] = {}
    for name, nodes in _qualified_symbols(tree).items():
        rows: list[_LoadedSymbolMatch] = []
        for node in nodes:
            end = getattr(node, "end_lineno", None)
            start = _node_start_line(node)
            rows.append(
                _LoadedSymbolMatch(
                    kind=type(node).__name__,
                    start_line=start,
                    end_line=end if isinstance(end, int) else None,
                    source=(
                        "".join(lines[start - 1 : end])
                        if isinstance(end, int)
                        else None
                    ),
                    class_projection=(
                        _class_projection(node)
                        if isinstance(node, ast.ClassDef)
                        else None
                    ),
                    class_state_projection=(
                        _class_state_projection(node)
                        if isinstance(node, ast.ClassDef)
                        else None
                    ),
                )
            )
        immutable_matches[name] = tuple(rows)
    binding_projections = {
        name: _binding_projection(bindings, star_imports, name)
        for name in bindings
    }
    loaded = _LoadedSymbolSource(
        content_sha256=content_sha256,
        line_count=len(lines),
        module_projection=_module_projection(tree),
        module_state_projection=_module_state_projection(tree),
        matches=MappingProxyType(immutable_matches),
        binding_projections=MappingProxyType(binding_projections),
        star_imports=star_imports,
    )
    return _analysis_cache_put(loaded)


def _single_class_match(
    matches: Mapping[str, tuple[_LoadedSymbolMatch, ...]],
    class_name: str,
    selector: str,
) -> _LoadedSymbolMatch:
    nodes = matches.get(class_name, ())
    if len(nodes) != 1 or nodes[0].kind != "ClassDef":
        raise ConfigurationError(f"reviewed input class is ambiguous or not found: {selector}")
    return nodes[0]


def _symbol_record_from_loaded(
    *,
    selector: str,
    path_text: str,
    symbol: str,
    loaded: _LoadedSymbolSource,
) -> dict[str, Any]:
    cache_key = (loaded.content_sha256, path_text, symbol)
    cached = _projection_cache_get(cache_key)
    if cached is not None:
        return {
            "selector": selector,
            "path": path_text,
            "symbol": symbol,
            "kind": cached.kind,
            "start_line": cached.start_line,
            "end_line": cached.end_line,
            "sha256": cached.sha256,
        }

    matches = loaded.matches
    if symbol == "@module":
        payload = loaded.module_projection
        kind = "module-projection"
        start_line = 1
        end_line = loaded.line_count
    elif symbol == "@module-state":
        payload = loaded.module_state_projection
        kind = "module-state-projection"
        start_line = 1
        end_line = loaded.line_count
    elif symbol.startswith("@binding:"):
        binding_name = symbol.split(":", 1)[1]
        if not binding_name:
            raise ConfigurationError(f"empty reviewed module binding selector: {selector}")
        payload = loaded.binding_projections.get(binding_name)
        if payload is None:
            if not loaded.star_imports:
                raise ConfigurationError(
                    f"reviewed module binding not found: {binding_name}"
                )
            payload = json.dumps(
                {
                    "name": binding_name,
                    "bindings": [],
                    "star_imports": list(loaded.star_imports),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        kind = "module-binding-projection"
        start_line = 1
        end_line = loaded.line_count
    elif symbol.endswith(".@class-state"):
        class_name = symbol[: -len(".@class-state")]
        node = _single_class_match(matches, class_name, selector)
        payload = node.class_state_projection
        if payload is None:
            raise ConfigurationError(
                f"reviewed input class is ambiguous or not found: {selector}"
            )
        kind = "class-state-projection"
        start_line = node.start_line
        if node.end_line is None:
            raise ConfigurationError(f"Python parser did not expose a complete class range for {selector}")
        end_line = node.end_line
    elif symbol.endswith(".@class"):
        class_name = symbol[: -len(".@class")]
        node = _single_class_match(matches, class_name, selector)
        payload = node.class_projection
        if payload is None:
            raise ConfigurationError(
                f"reviewed input class is ambiguous or not found: {selector}"
            )
        kind = "class-projection"
        start_line = node.start_line
        if node.end_line is None:
            raise ConfigurationError(f"Python parser did not expose a complete class range for {selector}")
        end_line = node.end_line
    else:
        nodes = matches.get(symbol, ())
        if not nodes:
            raise ConfigurationError(f"reviewed input symbol not found: {selector}")
        ranges: list[tuple[int, int, str, str]] = []
        for node in nodes:
            if node.end_line is None or node.source is None:
                raise ConfigurationError(f"Python parser did not expose a complete source range for {selector}")
            ranges.append(
                (
                    node.start_line,
                    node.end_line,
                    node.kind,
                    node.source,
                )
            )
        start_line = min(item[0] for item in ranges)
        end_line = max(item[1] for item in ranges)
        if len(ranges) == 1:
            kind = ranges[0][2]
            payload = ranges[0][3]
        else:
            kind = "multi-definition"
            payload = json.dumps(
                [
                    {"kind": node_kind, "source": source}
                    for _, _, node_kind, source in ranges
                ],
                sort_keys=True,
                separators=(",", ":"),
            )

    projection = _projection_cache_put(
        cache_key,
        _SymbolProjection(
            kind=kind,
            start_line=start_line,
            end_line=end_line,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        ),
    )
    return {
        "selector": selector,
        "path": path_text,
        "symbol": symbol,
        "kind": projection.kind,
        "start_line": projection.start_line,
        "end_line": projection.end_line,
        "sha256": projection.sha256,
    }


def _symbol_records(
    root: Path,
    selectors: tuple[str, ...],
    *,
    session: FingerprintSession | None = None,
) -> list[dict[str, Any]]:
    """Fingerprint reviewed symbols with one read/parse/binding-index per file."""
    if not selectors:
        return []
    if session is not None:
        session.validate_root(root)
        root_resolved = session.root
        loaded_by_path = session.symbol_sources
    else:
        root_resolved = root.resolve(strict=True)
        loaded_by_path: dict[str, _LoadedSymbolSource] = {}
    records: list[dict[str, Any]] = []
    for selector in selectors:
        if "::" not in selector:
            raise ConfigurationError(
                f"invalid input_symbols selector {selector!r}; expected relative.py::qualified.symbol"
            )
        path_text, symbol = selector.rsplit("::", 1)
        if not path_text or not symbol:
            raise ConfigurationError(
                f"invalid input_symbols selector {selector!r}; expected relative.py::qualified.symbol"
            )
        loaded = loaded_by_path.get(path_text)
        if loaded is None:
            loaded = _load_symbol_source(root, root_resolved, path_text)
            loaded_by_path[path_text] = loaded
        records.append(
            _symbol_record_from_loaded(
                selector=selector,
                path_text=path_text,
                symbol=symbol,
                loaded=loaded,
            )
        )
    return records


def _symbol_record(root: Path, selector: str) -> dict[str, Any]:
    """Compatibility wrapper for callers/tests that fingerprint one selector."""
    return _symbol_records(root, (selector,))[0]


def task_fingerprint_v2(
    manifest: Manifest,
    task: TaskSpec,
    runtime_identity: dict[str, Any],
    *,
    environment_fingerprint: dict[str, dict[str, Any]] | None = None,
    session: FingerprintSession | None = None,
) -> tuple[str, dict[str, Any]]:
    if manifest.version != 2:
        raise ConfigurationError("hermetic fingerprint requires manifest version 2")
    if not task.result_only or task.outputs:
        raise ConfigurationError(f"task {task.name!r}: hermetic reuse requires result_only with no outputs")
    if environment_fingerprint is None:
        _, environment_fingerprint = hermetic_environment(task)
    if session is not None:
        session.validate_root(manifest.root)
        for pattern in task.inputs:
            session.validate_relative(manifest.root, pattern, field="input")
    else:
        for pattern in task.inputs:
            validate_relative(manifest.root, pattern, field="input")
    input_records = _content_only(
        session.expand_inputs(manifest.root, task.inputs)
        if session is not None
        else expand_inputs(manifest.root, task.inputs)
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
            raise ConfigurationError(f"task {task.name!r}: hermetic source closure cannot include .zerorun state")
        if record_path == ".git" or record_path.startswith(".git/"):
            raise ConfigurationError(f"task {task.name!r}: hermetic source closure cannot include .git state")

    command_source_records = _content_only(
        command_sources(
            task.command,
            manifest.root,
            path_record_cache=session.path_records if session is not None else None,
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
        _symbol_records(manifest.root, task.input_symbols, session=session)
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
