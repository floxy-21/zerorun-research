from __future__ import annotations

import ast
import glob
import hashlib
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Iterable

from .fingerprint import read_stable_file_bytes
from .model import ConfigurationError
from .path_safety import is_link_like


_DYNAMIC_GLOBAL_NAMES = frozenset({"globals", "eval", "exec", "__import__"})
_INTERPRETER_GLOBALS = frozenset(
    {
        "__name__",
        "__file__",
        "__cached__",
        "__package__",
        "__loader__",
        "__spec__",
        "__builtins__",
        "__doc__",
    }
)

_PYTEST_CONFIG_INPUT_NAMES = (
    # Keep this in pytest 9.1's exact locate_config precedence.  The first
    # four names select a configuration even when the file is empty, so mere
    # creation can change rootdir, collection, and addopts semantics.
    "pytest.toml",
    ".pytest.toml",
    "pytest.ini",
    ".pytest.ini",
    "pyproject.toml",
    "tox.ini",
    "setup.cfg",
)

_PYTEST_STATIC_INPUT_CANDIDATES = (
    ".zerorun-env",
    *_PYTEST_CONFIG_INPUT_NAMES,
    "setup.py",
    ".python-version",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "test-requirements.txt",
)

_PYTEST_ANCESTOR_INPUT_NAMES = (*_PYTEST_CONFIG_INPUT_NAMES, "setup.py")
_PYTEST_ANCESTOR_INPUT_PRECEDENCE = {
    name: index for index, name in enumerate(_PYTEST_ANCESTOR_INPUT_NAMES)
}
_MAX_REQUEST_LOCAL_IMPORT_SURFACES = 20_000


def discover_pytest_static_inputs(
    root: Path,
    *,
    targets: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Return the complete current pytest/config discovery surface.

    Candidate generation and runtime share this inventory. A newly added
    ``conftest.py`` or root configuration file therefore cannot sit outside a
    previously reviewed profile while per-node alignment reuses old results.
    Content is fingerprinted separately in every node key.
    """

    root = root.resolve(strict=True)
    if targets is not None:
        return _discover_targeted_pytest_static_inputs(root, targets)
    result = [
        relative
        for relative in _PYTEST_STATIC_INPUT_CANDIDATES
        if (root / relative).exists()
    ]
    conftest_paths: list[Path] = []
    ancestor_input_paths: list[Path] = []

    def raise_walk_error(error: OSError) -> None:
        # os.walk otherwise skips unreadable directories. Discovery uncertainty
        # must propagate so the runtime can disable reuse rather than silently
        # accepting an incomplete conftest inventory.
        raise error

    for current, directories, filenames in os.walk(
        root,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        # These root-level trees were already excluded from the result below.
        # Pruning them before descent avoids walking Git objects and mutable
        # ZeroRun state on every request without changing the inventory.
        if Path(current) == root:
            directories[:] = [
                name
                for name in directories
                if name not in {".git", ".zerorun", ".zerorun-env"}
            ]
        directories.sort()
        for name in _PYTEST_ANCESTOR_INPUT_NAMES:
            if name in filenames:
                ancestor_input_paths.append(Path(current) / name)
        if "conftest.py" in filenames:
            conftest_paths.append(Path(current) / "conftest.py")
    for path in sorted(
        ancestor_input_paths,
        key=lambda candidate: (
            candidate.parent.relative_to(root).as_posix(),
            _PYTEST_ANCESTOR_INPUT_PRECEDENCE[candidate.name],
        ),
    ):
        # Pytest starts at each selected target's directory and searches its
        # ancestors. Inventorying every in-repository candidate is a safe
        # conservative superset when this target-independent helper cannot yet
        # know which configured target will be selected.
        result.append(path.relative_to(root).as_posix())
    for path in sorted(conftest_paths):
        # Preserve the lexical project path here. Resolving first would make a
        # conftest symlink that escapes the project disappear from discovery,
        # exactly when the runtime must fail closed. The downstream static
        # input fingerprinter performs the link/path-safety validation.
        relative = path.relative_to(root).as_posix()
        if (
            relative.startswith(".git/")
            or relative.startswith(".zerorun/")
            or relative.startswith(".zerorun-env/")
        ):
            continue
        result.append(relative)
    return tuple(dict.fromkeys(result))


def _target_path(root: Path, target: str) -> Path:
    if not isinstance(target, str) or not target:
        raise ConfigurationError("pytest static discovery target is invalid")
    path_text = target.split("::", maxsplit=1)[0]
    if (
        not path_text
        or "\\" in path_text
        or path_text.startswith(("/", "-", "@"))
    ):
        raise ConfigurationError("pytest static discovery target is not a project path")
    pieces = tuple(path_text.split("/"))
    if any(piece in {"", ".."} for piece in pieces):
        raise ConfigurationError("pytest static discovery target is not canonical")
    candidate = root.joinpath(*pieces)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError("pytest static discovery target escapes project root") from exc
    cursor = root
    for piece in pieces:
        cursor = cursor / piece
        if is_link_like(cursor):
            raise ConfigurationError(
                "pytest static discovery target contains a symbolic link or junction: "
                + path_text
            )
    return candidate


def _ancestor_directories(root: Path, start: Path) -> tuple[Path, ...]:
    try:
        start.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError("pytest discovery ancestor escapes project root") from exc
    rows: list[Path] = []
    current = start
    while True:
        rows.append(current)
        if current == root:
            break
        current = current.parent
    return tuple(rows)


def _discover_targeted_pytest_static_inputs(
    root: Path,
    targets: tuple[str, ...],
) -> tuple[str, ...]:
    """Mirror pytest 9.1's target-sensitive config/conftest search surface.

    Existing candidates are returned, while repeated discovery before/after an
    authority decision detects creation of any previously missing candidate.
    Every possible in-workspace path inspected by ``determine_setup`` is a
    conservative member, including its multi-target fallback search.
    """

    if not targets or not all(isinstance(target, str) and target for target in targets):
        raise ConfigurationError("pytest static discovery targets are invalid")

    # Environment/build inputs remain root-scoped. Pytest configuration and
    # conftest discovery below are restricted to paths reachable from targets.
    result = [
        relative
        for relative in _PYTEST_STATIC_INPUT_CANDIDATES
        if relative not in {*_PYTEST_CONFIG_INPUT_NAMES, "setup.py"}
        and (root / relative).exists()
    ]
    target_rows: list[tuple[Path, bool]] = []
    setup_dirs: list[Path] = []
    for target in targets:
        candidate = _target_path(root, target)
        try:
            exists = candidate.exists()
            is_directory = exists and candidate.is_dir()
            is_file = exists and candidate.is_file()
        except OSError as exc:
            raise ConfigurationError(
                f"cannot inspect pytest static discovery target {target!r}: {exc}"
            ) from exc
        if exists and not (is_directory or is_file):
            raise ConfigurationError(
                f"pytest static discovery target is not a regular file or directory: {target}"
            )
        if exists:
            setup_dirs.append(candidate if is_directory else candidate.parent)
        target_rows.append((candidate, is_directory))

    # pytest's get_common_ancestor falls back to the invocation directory when
    # every target is missing. Qualification will subsequently fail collection,
    # but retaining root discovery here preserves its exact setup behavior.
    if setup_dirs:
        common_text = os.path.commonpath([str(path) for path in setup_dirs])
        common = Path(common_text)
        if common.is_file():
            common = common.parent
    else:
        common = root
    search_starts = [common]
    if setup_dirs != [common]:
        # This is pytest 9.1's second locate_config(dirs) branch. Inventory all
        # rows even if the first valid config makes later rows unreachable;
        # that conservative superset keeps content/creation changes fail-safe.
        search_starts.extend(setup_dirs)

    config_paths: set[Path] = set()
    for start in search_starts:
        for directory in _ancestor_directories(root, start):
            for name in _PYTEST_ANCESTOR_INPUT_NAMES:
                candidate = directory / name
                if candidate.exists():
                    config_paths.add(candidate)
    for path in sorted(
        config_paths,
        key=lambda candidate: (
            candidate.parent.relative_to(root).as_posix(),
            _PYTEST_ANCESTOR_INPUT_PRECEDENCE[candidate.name],
        ),
    ):
        result.append(path.relative_to(root).as_posix())

    conftest_paths: set[Path] = set()
    for candidate, is_directory in target_rows:
        base = candidate if is_directory else candidate.parent
        for directory in _ancestor_directories(root, base):
            conftest = directory / "conftest.py"
            if conftest.exists():
                conftest_paths.add(conftest)
        if not is_directory:
            continue

        def raise_walk_error(error: OSError) -> None:
            raise error

        for current, directories, filenames in os.walk(
            candidate,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            current_path = Path(current)
            retained_directories: list[str] = []
            for name in sorted(directories):
                nested = current_path / name
                if name in {".git", ".zerorun", ".zerorun-env"}:
                    continue
                if is_link_like(nested):
                    raise ConfigurationError(
                        "pytest target discovery contains a symbolic link or junction: "
                        + nested.relative_to(root).as_posix()
                    )
                retained_directories.append(name)
            directories[:] = retained_directories
            if "conftest.py" in filenames:
                conftest_paths.add(current_path / "conftest.py")

    for path in sorted(conftest_paths):
        result.append(path.relative_to(root).as_posix())
    return tuple(dict.fromkeys(result))


@dataclass(frozen=True, order=True)
class CallSite:
    path: str
    firstlineno: int
    name: str
    globals: tuple[str, ...] = ()
    dynamic_globals: bool = False


@dataclass(frozen=True, order=True)
class ExecutedImportEdge:
    """One fixed-target import operation observed during qualification.

    Rows are normal ``IMPORT_NAME`` operations.  The bytecode identity, source
    range, and operands are candidate-generation evidence only; they never
    authorize reuse on their own.
    """

    path: str
    firstlineno: int
    code_name: str
    instruction_offset: int
    lineno: int
    end_lineno: int
    col_offset: int
    end_col_offset: int
    module: str
    fromlist: tuple[str, ...]
    level: int


@dataclass(frozen=True)
class SymbolClosure:
    fallback_files: tuple[str, ...]
    selectors: tuple[str, ...]
    absent_files: tuple[str, ...]
    unresolved_imports: tuple[str, ...]
    call_sites: tuple[CallSite, ...]


@dataclass(frozen=True)
class LocalImportSurface:
    existing_files: tuple[str, ...]
    absent_files: tuple[str, ...]
    resolved_locally: bool


def _reject_import_candidate_link(
    root: Path,
    candidate: Path,
    *,
    verified_components: set[Path] | None = None,
) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError(
            f"local import candidate escapes project root: {candidate}"
        ) from exc
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if verified_components is not None and cursor in verified_components:
            continue
        if is_link_like(cursor):
            raise ConfigurationError(
                f"local import candidate contains a symbolic link or junction: {relative.as_posix()}"
            )
        if verified_components is not None:
            # This set is scoped to one explicit negative-snapshot pass.  A
            # later publication guard starts with a new set and re-observes
            # every shared ancestor before it can authorize anything.
            verified_components.add(cursor)


def resolve_local_import_surface(
    root: Path,
    module: str,
    *,
    search_roots: Iterable[str] | None = None,
) -> LocalImportSurface:
    """Bind every project ``sys.path`` root that can satisfy a dotted import.

    Missing candidates are first-class evidence: creating one later can shadow
    an installed dependency without changing the importing source.  Every
    prefix is included because importing ``a.b`` first resolves ``a`` and may
    also traverse namespace packages.
    """

    root = root.resolve(strict=True)
    pieces = tuple(module.split("."))
    if not pieces or any(not piece or not piece.isidentifier() for piece in pieces):
        raise ConfigurationError(f"local import name is not statically reviewable: {module!r}")
    raw_search_roots = tuple(search_roots or (".", "src"))
    if not raw_search_roots:
        raise ConfigurationError("local import search-root evidence is empty")
    module_roots: list[Path] = []
    for relative_root in raw_search_roots:
        if (
            not isinstance(relative_root, str)
            or not relative_root
            or "\\" in relative_root
            or relative_root.startswith(("/", "-", "@"))
        ):
            raise ConfigurationError("local import search root is invalid")
        parts = () if relative_root == "." else tuple(relative_root.split("/"))
        if any(part in {"", ".", ".."} for part in parts):
            raise ConfigurationError("local import search root is not canonical")
        if relative_root in {".git", ".zerorun", ".zerorun-env"} or relative_root.startswith(
            (".git/", ".zerorun/", ".zerorun-env/")
        ):
            raise ConfigurationError("local import search root enters mutable state")
        candidate_root = root.joinpath(*parts)
        _reject_import_candidate_link(root, candidate_root)
        if candidate_root not in module_roots:
            module_roots.append(candidate_root)
    existing: set[str] = set()
    absent: set[str] = set()
    terminal_roots: set[Path] = set()
    for module_root in module_roots:
        for depth in range(1, len(pieces) + 1):
            base = module_root.joinpath(*pieces[:depth])
            candidates = (base.with_suffix(".py"), base / "__init__.py")
            existing_at_prefix: list[Path] = []
            if not base.exists() and not is_link_like(base):
                absent.add(base.relative_to(root).as_posix())
            for candidate in candidates:
                _reject_import_candidate_link(root, candidate)
                relative = candidate.relative_to(root).as_posix()
                try:
                    details = candidate.stat(follow_symlinks=False)
                except FileNotFoundError:
                    absent.add(relative)
                    continue
                except OSError as exc:
                    raise ConfigurationError(
                        f"cannot inspect local import candidate {relative}: {exc}"
                    ) from exc
                if not stat.S_ISREG(details.st_mode):
                    raise ConfigurationError(
                        f"local import candidate is not a regular file: {relative}"
                    )
                existing.add(relative)
                existing_at_prefix.append(candidate)
            native_patterns = (
                base.with_name(base.name + "*.so"),
                base / "__init__*.so",
            )
            for native_pattern in native_patterns:
                pattern_relative = native_pattern.relative_to(root).as_posix()
                native_matches = [Path(match) for match in glob.glob(str(native_pattern))]
                if not native_matches:
                    absent.add(pattern_relative)
                    continue
                for native in native_matches:
                    _reject_import_candidate_link(root, native)
                    try:
                        details = native.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise ConfigurationError(
                            f"cannot inspect local native import candidate {native}: {exc}"
                        ) from exc
                    if not stat.S_ISREG(details.st_mode):
                        raise ConfigurationError(
                            "local native import candidate is not a regular file: "
                            + native.relative_to(root).as_posix()
                        )
                    existing.add(native.relative_to(root).as_posix())
                    existing_at_prefix.append(native)
            if len(existing_at_prefix) > 1:
                raise ConfigurationError(
                    f"local import {module!r} resolves to both module and package"
                )
            if depth == len(pieces) and existing_at_prefix:
                terminal_roots.add(module_root)
    if len(terminal_roots) > 1:
        raise ConfigurationError(
            f"local import {module!r} is ambiguous across project module roots"
        )
    return LocalImportSurface(
        existing_files=tuple(sorted(existing)),
        absent_files=tuple(sorted(absent)),
        resolved_locally=bool(terminal_roots),
    )


def _source_package(root: Path, relative: str) -> str:
    path = root / relative
    module_root = root
    src = root / "src"
    try:
        path.relative_to(src)
    except ValueError:
        pass
    else:
        if not (src / "__init__.py").is_file():
            module_root = src
    names: list[str] = []
    cursor = path.parent
    while cursor != module_root and cursor != root.parent:
        init = cursor / "__init__.py"
        if not init.is_file():
            break
        names.append(cursor.name)
        cursor = cursor.parent
    return ".".join(reversed(names))


def _relative_import_module(
    package: str,
    *,
    level: int,
    module: str | None,
) -> str:
    if level <= 0:
        return module or ""
    if not package:
        raise ConfigurationError("relative import is outside a reviewed package")
    parts = package.split(".")
    ascend = level - 1
    if ascend >= len(parts):
        raise ConfigurationError("relative import escapes reviewed package")
    prefix = parts[: len(parts) - ascend]
    if module:
        prefix.extend(module.split("."))
    return ".".join(prefix)


def _literal_pytest_plugins(tree: ast.Module) -> tuple[str, ...]:
    plugins: set[str] = set()
    for node in tree.body:
        value: ast.AST | None = None
        targets: tuple[ast.AST, ...] = ()
        if isinstance(node, ast.Assign):
            value = node.value
            targets = tuple(node.targets)
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = (node.target,)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "pytest_plugins":
                raise ConfigurationError("pytest_plugins declaration is dynamic")
        if value is None or not any(
            isinstance(target, ast.Name) and target.id == "pytest_plugins"
            for target in targets
        ):
            continue
        try:
            literal = ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError) as exc:
            raise ConfigurationError("pytest_plugins declaration is not literal") from exc
        if isinstance(literal, str):
            values = (literal,)
        elif isinstance(literal, (tuple, list)) and all(
            isinstance(item, str) for item in literal
        ):
            values = tuple(literal)
        else:
            raise ConfigurationError("pytest_plugins declaration is not a string list")
        if any(
            not item or any(not part.isidentifier() for part in item.split("."))
            for item in values
        ):
            raise ConfigurationError("pytest_plugins declaration contains an invalid module")
        plugins.update(values)
    return tuple(sorted(plugins))


def _type_checking_guard_ids(tree: ast.Module) -> frozenset[int]:
    """Identify canonical, unshadowed ``typing.TYPE_CHECKING`` guards."""

    guard_names: set[str] = set()
    typing_aliases: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.ImportFrom) and statement.module == "typing":
            for alias in statement.names:
                if alias.name == "TYPE_CHECKING":
                    guard_names.add(alias.asname or alias.name)
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                if alias.name == "typing":
                    typing_aliases.add(alias.asname or "typing")

    assigned: set[str] = set()

    def collect_target(target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            assigned.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                collect_target(element)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                collect_target(target)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assigned.add(node.name)
        elif isinstance(node, ast.arg):
            assigned.add(node.arg)
    guard_names.difference_update(assigned)
    typing_aliases.difference_update(assigned)

    def is_guard(test: ast.AST) -> bool:
        return (
            isinstance(test, ast.Name)
            and test.id in guard_names
        ) or (
            isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id in typing_aliases
        )

    guarded: set[int] = set()

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if is_guard(node.test):
                guarded.add(id(node))
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

    Visitor().visit(tree)
    return frozenset(guarded)


def _type_checking_import_ids(tree: ast.Module) -> frozenset[int]:
    """Identify imports guarded by an unshadowed ``typing.TYPE_CHECKING``.

    Whole-file fallback retains these as optional shadow surfaces.  The exact
    executed-edge projection can omit the false branch entirely because both
    its guard and the containing reviewed source projection remain hashed.
    """

    guard_ids = _type_checking_guard_ids(tree)
    guarded: set[int] = set()

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            if id(node) in guard_ids:
                for statement in node.body:
                    guarded.update(
                        id(candidate)
                        for candidate in ast.walk(statement)
                        if isinstance(candidate, (ast.Import, ast.ImportFrom))
                    )
                for statement in node.orelse:
                    self.visit(statement)
                return
            self.generic_visit(node)

    Visitor().visit(tree)
    return frozenset(guarded)


def _import_node_requests(
    root: Path,
    relative: str,
    node: ast.Import | ast.ImportFrom,
) -> tuple[set[str], set[str]]:
    required: set[str] = set()
    optional_fromlist: set[str] = set()
    if isinstance(node, ast.Import):
        required.update(alias.name for alias in node.names)
        return required, optional_fromlist

    # ``from __future__`` is a compiler directive, not a runtime module
    # resolution request.
    if int(node.level or 0) == 0 and node.module == "__future__":
        return required, optional_fromlist
    base = _relative_import_module(
        _source_package(root, relative),
        level=int(node.level or 0),
        module=node.module,
    )
    if base:
        required.add(base)
    for alias in node.names:
        if alias.name == "*":
            continue
        optional_fromlist.add(f"{base}.{alias.name}" if base else alias.name)
    return required, optional_fromlist


def static_import_requests(
    root: Path,
    relative: str,
    tree: ast.Module,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    required: set[str] = set(_literal_pytest_plugins(tree))
    optional_fromlist: set[str] = set()
    type_checking_imports = _type_checking_import_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if (
                isinstance(function, ast.Name)
                and function.id in {"__import__", "eval", "exec"}
            ) or (
                isinstance(function, ast.Attribute)
                and function.attr
                in {
                    "import_module",
                    "find_spec",
                    "spec_from_file_location",
                    "module_from_spec",
                    "exec_module",
                }
            ):
                raise ConfigurationError(
                    f"dynamic/custom import resolution in {relative}"
                )
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr in {"path", "meta_path", "path_hooks", "path_importer_cache"}
        ):
            raise ConfigurationError(f"custom import path access in {relative}")
        if isinstance(node, ast.Attribute) and node.attr == "__import__":
            raise ConfigurationError(
                f"import function observation or mutation in {relative}"
            )
        if isinstance(node, ast.Import):
            node_required, node_optional = _import_node_requests(
                root,
                relative,
                node,
            )
            if id(node) in type_checking_imports:
                optional_fromlist.update(node_required)
            else:
                required.update(node_required)
            optional_fromlist.update(node_optional)
        elif isinstance(node, ast.ImportFrom):
            node_required, node_optional = _import_node_requests(
                root,
                relative,
                node,
            )
            if id(node) in type_checking_imports:
                optional_fromlist.update(node_required)
            else:
                required.update(node_required)
            optional_fromlist.update(node_optional)
    return tuple(sorted(required)), tuple(sorted(optional_fromlist))


def package_ancestor_import_surface(root: Path, relative: str) -> LocalImportSurface:
    """Bind ``__init__`` creation/removal along one collected item's path."""

    root = root.resolve(strict=True)
    path = root / relative
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError("collection item path escapes project root") from exc
    existing: set[str] = set()
    absent: set[str] = set()
    cursor = path.parent
    while cursor != root:
        init = cursor / "__init__.py"
        _reject_import_candidate_link(root, init)
        rendered = init.relative_to(root).as_posix()
        try:
            details = init.stat(follow_symlinks=False)
        except FileNotFoundError:
            absent.add(rendered)
        except OSError as exc:
            raise ConfigurationError(
                f"cannot inspect package ancestor candidate {rendered}: {exc}"
            ) from exc
        else:
            if not stat.S_ISREG(details.st_mode):
                raise ConfigurationError(
                    f"package ancestor candidate is not a regular file: {rendered}"
                )
            existing.add(rendered)
        cursor = cursor.parent
    return LocalImportSurface(
        existing_files=tuple(sorted(existing)),
        absent_files=tuple(sorted(absent)),
        resolved_locally=True,
    )


def validate_absent_import_paths(root: Path, absent_files: Iterable[str]) -> None:
    """Recheck a captured negative import surface immediately before publish."""

    root = root.resolve(strict=True)
    patterns = tuple(sorted(set(absent_files)))
    if not patterns:
        return

    # A fresh session is mandatory on every invocation.  Its path, lstat and
    # directory-scan caches collapse thousands of sibling shadow candidates
    # only inside this one negative-snapshot pass; the later publication guard
    # receives an independent session and observes any intervening creation.
    from .hermetic_batch_key import FingerprintSession

    session = FingerprintSession(root)
    glob_patterns: list[str] = []
    verified_components: set[Path] = set()
    for relative in patterns:
        candidate = root / relative
        if glob.has_magic(relative):
            # Generated extension-module candidates contain magic only in the
            # basename.  Explicitly inspect the literal parent so permission
            # failures and a directory replaced by a regular file cannot be
            # mistaken for absence by glob's intentionally forgiving API.
            parent_relative = Path(relative).parent.as_posix()
            lexical_parent = root / parent_relative
            _reject_import_candidate_link(
                root,
                lexical_parent,
                verified_components=verified_components,
            )
            parent = session.validate_relative(
                root,
                parent_relative,
                field="negative local import pattern parent",
            )
            try:
                parent_details = parent.stat(follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                raise ConfigurationError(
                    f"cannot inspect negative local import pattern {relative}: {exc}"
                ) from exc
            if not stat.S_ISDIR(parent_details.st_mode):
                raise ConfigurationError(
                    "negative local import pattern parent is not a directory: "
                    + relative
                )
            glob_patterns.append(relative)
            continue

        _reject_import_candidate_link(
            root,
            candidate,
            verified_components=verified_components,
        )
        candidate = session.validate_relative(
            root,
            relative,
            field="negative local import candidate",
        )
        try:
            candidate.stat(follow_symlinks=False)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as exc:
            raise ConfigurationError(
                f"cannot inspect negative local import candidate {relative}: {exc}"
            ) from exc
        raise ConfigurationError(
            f"negative local import candidate changed during qualification: {relative}"
        )

    if glob_patterns:
        records = session.expand_inputs(root, tuple(glob_patterns))
        observed_missing = {
            str(record.get("pattern"))
            for record in records
            if record.get("type") == "missing"
            and isinstance(record.get("pattern"), str)
        }
        changed = next(
            (
                str(record.get("path") or record.get("pattern") or "<unknown>")
                for record in records
                if record.get("type") != "missing"
            ),
            None,
        )
        if changed is not None or observed_missing != set(glob_patterns):
            raise ConfigurationError(
                "negative local import candidate changed during qualification: "
                + (changed or "snapshot membership")
            )


@dataclass(frozen=True)
class _AstSymbol:
    qualified: str
    node: ast.AST
    start_line: int
    definition_line: int
    end_line: int
    class_ancestors: tuple[str, ...]


@dataclass(frozen=True)
class _SourceIdentity:
    resolved_path: Path
    stat_token: tuple[int, ...]
    sha256: str


@dataclass(frozen=True)
class _SourceAnalysis:
    identity: _SourceIdentity
    tree: ast.Module | None
    symbols: tuple[_AstSymbol, ...]
    bound_names: frozenset[str]
    has_star_import: bool


def _source_stat_token(details: os.stat_result) -> tuple[int, ...]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_mode,
        details.st_size,
        details.st_mtime_ns,
        getattr(details, "st_ctime_ns", 0),
    )


def _stable_source_identity(path: Path) -> tuple[bytes, _SourceIdentity]:
    """Capture bytes and filesystem identity from one stable regular file."""

    before = path.stat(follow_symlinks=False)
    if is_link_like(path) or not stat.S_ISREG(before.st_mode):
        raise ConfigurationError(f"symbol-closure source is not a regular file: {path}")
    contents = read_stable_file_bytes(path)
    after = path.stat(follow_symlinks=False)
    if is_link_like(path) or _source_stat_token(before) != _source_stat_token(after):
        raise ConfigurationError(
            f"symbol-closure source changed while it was being captured: {path}"
        )
    return contents, _SourceIdentity(
        resolved_path=path,
        stat_token=_source_stat_token(after),
        sha256=hashlib.sha256(contents).hexdigest(),
    )


class SymbolClosureAnalysisCache:
    """Request-local exact-source cache for closure ASTs and module bindings.

    The cache is deliberately not persisted or shared between qualification
    requests. Every analyzed source is bound to its resolved file identity and
    byte digest, and callers must revalidate the cache before publishing any
    candidate derived from it.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        self._analyses: dict[Path, _SourceAnalysis] = {}
        self._aliases: dict[str, Path] = {}
        self._import_requests: dict[
            tuple[str, str],
            tuple[tuple[str, ...], tuple[str, ...]],
        ] = {}
        self._import_surfaces: dict[
            tuple[str, tuple[str, ...]],
            LocalImportSurface,
        ] = {}

    def validate_root(self, root: Path) -> None:
        if root.resolve(strict=True) != self.root:
            raise ConfigurationError(
                "symbol-closure analysis cache cannot be shared across project roots"
            )

    def _resolve_source(self, relative: str) -> Path | None:
        lexical = self.root / relative
        try:
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return None
        if is_link_like(lexical) or not resolved.is_file() or resolved.suffix != ".py":
            return None
        previous = self._aliases.get(relative)
        if previous is not None and previous != resolved:
            raise ConfigurationError(
                f"symbol-closure source changed during qualification: {relative}"
            )
        self._aliases[relative] = resolved
        return resolved

    def analyze(self, relative: str) -> _SourceAnalysis | None:
        resolved = self._resolve_source(relative)
        if resolved is None:
            return None
        cached = self._analyses.get(resolved)
        if cached is not None:
            try:
                current = resolved.stat(follow_symlinks=False)
            except OSError as exc:
                raise ConfigurationError(
                    f"symbol-closure source changed during qualification: {relative}"
                ) from exc
            if (
                is_link_like(resolved)
                or _source_stat_token(current) != cached.identity.stat_token
            ):
                raise ConfigurationError(
                    f"symbol-closure source changed during qualification: {relative}"
                )
            return cached

        try:
            contents, identity = _stable_source_identity(resolved)
        except (ConfigurationError, OSError) as exc:
            raise ConfigurationError(
                f"symbol-closure source changed during qualification: {relative}"
            ) from exc
        try:
            source = contents.decode("utf-8")
            tree, symbols = _index_symbols(source, relative)
        except (UnicodeDecodeError, SyntaxError):
            analysis = _SourceAnalysis(
                identity=identity,
                tree=None,
                symbols=(),
                bound_names=frozenset(),
                has_star_import=False,
            )
        else:
            bound_names, has_star_import = _module_bindings(tree)
            analysis = _SourceAnalysis(
                identity=identity,
                tree=tree,
                symbols=symbols,
                bound_names=frozenset(bound_names),
                has_star_import=has_star_import,
            )
        self._analyses[resolved] = analysis
        return analysis

    def import_requests(
        self,
        relative: str,
        analysis: _SourceAnalysis,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return exact static imports once per analyzed source snapshot."""

        if analysis.tree is None:
            raise ConfigurationError(
                f"cannot inspect imports from unparseable source: {relative}"
            )
        key = (relative, analysis.identity.sha256)
        cached = self._import_requests.get(key)
        if cached is not None:
            return cached
        if len(self._import_requests) >= _MAX_REQUEST_LOCAL_IMPORT_SURFACES:
            raise ConfigurationError(
                "symbol-closure import analysis exceeds its structural boundary"
            )
        cached = static_import_requests(self.root, relative, analysis.tree)
        self._import_requests[key] = cached
        return cached

    def import_surface(
        self,
        module: str,
        *,
        search_roots: tuple[str, ...],
    ) -> LocalImportSurface:
        """Resolve one immutable project-shadow surface per request/root set."""

        key = (module, search_roots)
        cached = self._import_surfaces.get(key)
        if cached is not None:
            return cached
        if len(self._import_surfaces) >= _MAX_REQUEST_LOCAL_IMPORT_SURFACES:
            raise ConfigurationError(
                "symbol-closure import surface exceeds its structural boundary"
            )
        cached = resolve_local_import_surface(
            self.root,
            module,
            search_roots=search_roots,
        )
        self._import_surfaces[key] = cached
        return cached

    def revalidate(self) -> None:
        """Re-read every analyzed source and reject any identity mutation."""

        for relative, expected_path in sorted(self._aliases.items()):
            try:
                current = (self.root / relative).resolve(strict=True)
            except OSError as exc:
                raise ConfigurationError(
                    f"symbol-closure source changed during qualification: {relative}"
                ) from exc
            if current != expected_path or is_link_like(self.root / relative):
                raise ConfigurationError(
                    f"symbol-closure source changed during qualification: {relative}"
                )

        for resolved, analysis in sorted(
            self._analyses.items(), key=lambda item: str(item[0])
        ):
            try:
                _contents, current = _stable_source_identity(resolved)
            except (ConfigurationError, OSError) as exc:
                relative = resolved.relative_to(self.root).as_posix()
                raise ConfigurationError(
                    f"symbol-closure source changed during qualification: {relative}"
                ) from exc
            if current != analysis.identity:
                relative = resolved.relative_to(self.root).as_posix()
                raise ConfigurationError(
                    f"symbol-closure source changed during qualification: {relative}"
                )


def _node_start_line(node: ast.AST) -> int:
    lines = [int(getattr(node, "lineno", 0) or 0)]
    for decorator in getattr(node, "decorator_list", ()):
        lines.append(int(getattr(decorator, "lineno", 0) or 0))
    return min(line for line in lines if line > 0)


def _index_symbols(source: str, filename: str) -> tuple[ast.Module, tuple[_AstSymbol, ...]]:
    tree = ast.parse(source, filename=filename)
    indexed: list[_AstSymbol] = []

    def walk(
        body: list[ast.stmt],
        prefix: tuple[str, ...] = (),
        class_ancestors: tuple[str, ...] = (),
    ) -> None:
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            qualified_parts = (*prefix, node.name)
            qualified = ".".join(qualified_parts)
            end_line = getattr(node, "end_lineno", None)
            if not isinstance(end_line, int):
                continue
            indexed.append(
                _AstSymbol(
                    qualified=qualified,
                    node=node,
                    start_line=_node_start_line(node),
                    definition_line=int(node.lineno),
                    end_line=end_line,
                    class_ancestors=class_ancestors,
                )
            )
            if isinstance(node, ast.ClassDef):
                walk(node.body, qualified_parts, (*class_ancestors, qualified))

    walk(tree.body)
    return tree, tuple(indexed)


def _resolve_call(symbols: tuple[_AstSymbol, ...], site: CallSite) -> _AstSymbol | None:
    exact = [
        item
        for item in symbols
        if site.firstlineno in {item.start_line, item.definition_line}
        and getattr(item.node, "name", None) == site.name
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    enclosing = [
        item
        for item in symbols
        if item.start_line <= site.firstlineno <= item.end_line
        and getattr(item.node, "name", None) == site.name
    ]
    if not enclosing:
        return None
    enclosing.sort(key=lambda item: (item.end_line - item.start_line, item.start_line))
    if len(enclosing) >= 2 and (
        enclosing[0].end_line - enclosing[0].start_line
        == enclosing[1].end_line - enclosing[1].start_line
    ):
        return None
    return enclosing[0]


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        result: set[str] = set()
        for item in target.elts:
            result.update(_target_names(item))
        return result
    return set()


def _direct_bound_names(node: ast.stmt) -> tuple[set[str], bool]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}, False
    if isinstance(node, ast.Import):
        return {alias.asname or alias.name.split(".", 1)[0] for alias in node.names}, False
    if isinstance(node, ast.ImportFrom):
        return {alias.asname or alias.name for alias in node.names if alias.name != "*"}, any(
            alias.name == "*" for alias in node.names
        )
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
        names: set[str] = set()
        for target in targets:
            names.update(_target_names(target))
        return names, False
    return set(), False


def _nested_bound_names(node: ast.stmt) -> tuple[set[str], bool]:
    names, star = _direct_bound_names(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return names, star
    for field in ("body", "orelse", "finalbody"):
        body = getattr(node, field, None)
        if isinstance(body, list):
            for child in body:
                if isinstance(child, ast.stmt):
                    child_names, child_star = _nested_bound_names(child)
                    names.update(child_names)
                    star = star or child_star
    handlers = getattr(node, "handlers", None)
    if isinstance(handlers, list):
        for handler in handlers:
            body = getattr(handler, "body", None)
            if isinstance(body, list):
                for child in body:
                    if isinstance(child, ast.stmt):
                        child_names, child_star = _nested_bound_names(child)
                        names.update(child_names)
                        star = star or child_star
    return names, star


def _module_bindings(tree: ast.Module) -> tuple[set[str], bool]:
    names: set[str] = set()
    star = False
    for node in tree.body:
        node_names, node_star = _nested_bound_names(node)
        names.update(node_names)
        star = star or node_star
    return names, star


def _inside_resolved_outer(
    symbols: tuple[_AstSymbol, ...],
    site: CallSite,
    resolved_matches: tuple[_AstSymbol, ...],
) -> bool:
    resolved_ids = {id(item) for item in resolved_matches}
    return any(
        id(item) in resolved_ids
        and isinstance(item.node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.start_line <= site.firstlineno <= item.end_line
        for item in symbols
    )


def _module_state_covers_lambda(tree: ast.Module, site: CallSite) -> bool:
    """Recognize one lambda in top-level executable module state only.

    The existing @module-state selector already fingerprints that exact
    top-level statement. Function and class bodies are deliberately excluded
    here so unresolved nested lambdas continue to fail closed.
    """
    if site.name != "<lambda>":
        return False

    matches: list[ast.Lambda] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Lambda(self, node: ast.Lambda) -> None:
            if int(getattr(node, "lineno", 0) or 0) == site.firstlineno:
                matches.append(node)
            self.generic_visit(node)

    visitor = Visitor()
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        visitor.visit(statement)
    return len(matches) == 1


class _SelectedImportProjection(ast.NodeVisitor):
    """Collect only import/runtime machinery represented by exact selectors."""

    def __init__(
        self,
        tree: ast.Module,
        *,
        selected_symbols: frozenset[int],
        executed_lambda_lines: frozenset[int],
    ) -> None:
        self._selected_symbols = selected_symbols
        self._executed_lambda_lines = executed_lambda_lines
        self._type_checking_guards = _type_checking_guard_ids(tree)
        self._inside_selected_body = 0
        self.imports: list[ast.Import | ast.ImportFrom] = []
        self.uncertainties: set[str] = set()

    def _visit_function_header(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self.visit(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        for value in getattr(node, "type_params", ()):
            self.visit(value)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._visit_function_header(node)
        if id(node) not in self._selected_symbols and not self._inside_selected_body:
            return
        self._inside_selected_body += 1
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._inside_selected_body -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Class bodies, bases, decorators, and type parameters execute while the
        # class binding is created.  Method bodies remain dormant unless their
        # exact callable selector was observed.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword)
        for value in getattr(node, "type_params", ()):
            self.visit(value)
        for statement in node.body:
            self.visit(statement)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)
        line = int(getattr(node, "lineno", 0) or 0)
        if self._inside_selected_body or line in self._executed_lambda_lines:
            self.visit(node.body)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        if id(node) in self._type_checking_guards:
            # Only canonical, unshadowed typing.TYPE_CHECKING is known false in
            # the pinned interpreter.  Keep the else branch, if any.
            for statement in node.orelse:
                self.visit(statement)
            return
        for statement in node.body:
            self.visit(statement)
        for statement in node.orelse:
            self.visit(statement)

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.imports.append(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Name) and function.id in {
            "__import__",
            "eval",
            "exec",
        }:
            self.uncertainties.add("dynamic/custom import or evaluation")
        elif isinstance(function, ast.Attribute) and function.attr in {
            "import_module",
            "find_spec",
            "spec_from_file_location",
            "module_from_spec",
            "exec_module",
        }:
            self.uncertainties.add("dynamic/custom import machinery")
        elif (
            isinstance(function, ast.Name)
            and function.id in {"setattr", "delattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "__import__"
        ):
            self.uncertainties.add("import function mutation")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == "__import__":
            self.uncertainties.add("import function observation or mutation")
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "sys"
            and node.attr in {"path", "meta_path", "path_hooks", "path_importer_cache"}
        ):
            self.uncertainties.add("custom import path access")
        self.generic_visit(node)


def _edge_matches_import(
    edge: ExecutedImportEdge,
    node: ast.Import | ast.ImportFrom,
) -> bool:
    if (
        int(getattr(node, "lineno", 0) or 0) != edge.lineno
        or int(getattr(node, "end_lineno", 0) or 0) != edge.end_lineno
        or int(getattr(node, "col_offset", -1)) != edge.col_offset
        or int(getattr(node, "end_col_offset", -1)) != edge.end_col_offset
    ):
        return False
    if isinstance(node, ast.Import):
        return (
            edge.level == 0
            and not edge.fromlist
            and any(alias.name == edge.module for alias in node.names)
        )
    return (
        edge.module == (node.module or "")
        and edge.level == int(node.level or 0)
        and edge.fromlist == tuple(alias.name for alias in node.names)
    )


def _projected_import_requests(
    root: Path,
    relative: str,
    analysis: _SourceAnalysis,
    *,
    file_sites: tuple[CallSite, ...],
    selected_symbols: frozenset[int],
    edges: tuple[ExecutedImportEdge, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve only normal imports proven to execute in one selector bucket."""

    if analysis.tree is None:
        raise ConfigurationError(
            f"cannot project imports from unparseable source: {relative}"
        )
    tree = analysis.tree
    collector = _SelectedImportProjection(
        tree,
        selected_symbols=selected_symbols,
        executed_lambda_lines=frozenset(
            site.firstlineno for site in file_sites if site.name == "<lambda>"
        ),
    )
    collector.visit(tree)
    if collector.uncertainties:
        raise ConfigurationError(
            f"selected import projection in {relative} is uncertain: "
            + ", ".join(sorted(collector.uncertainties))
        )

    site_identities = {
        (site.firstlineno, site.name)
        for site in file_sites
    }
    required: set[str] = set(_literal_pytest_plugins(tree))
    optional: set[str] = set()
    for edge in edges:
        if (edge.firstlineno, edge.code_name) not in site_identities:
            raise ConfigurationError(
                f"executed import edge has no observed caller in {relative}"
            )
        matches = [
            node for node in collector.imports if _edge_matches_import(edge, node)
        ]
        if len(matches) == 1:
            node_required, node_optional = _import_node_requests(
                root,
                relative,
                matches[0],
            )
            required.update(node_required)
            optional.update(node_optional)
            continue
        if len(matches) != 1:
            raise ConfigurationError(
                "executed import edge does not map to exactly one selected import in "
                f"{relative}:{edge.firstlineno}:{edge.code_name}:"
                f"{edge.instruction_offset} for {edge.module!r} "
                f"fromlist={edge.fromlist!r} level={edge.level}"
            )
    return tuple(sorted(required)), tuple(sorted(optional))


def resolve_symbol_closure(
    root: Path,
    call_sites: Iterable[CallSite],
    *,
    analysis_cache: SymbolClosureAnalysisCache | None = None,
    external_modules: Iterable[str] = (),
    import_search_roots: Iterable[str] | None = None,
    executed_imports: Iterable[ExecutedImportEdge] | None = None,
) -> SymbolClosure:
    """Resolve observed project calls to exact symbol/state selectors or whole-file fallbacks.

    This is a candidate-generation primitive only. Observing a call is not proof
    that the observed set is a complete dependency closure and never authorizes
    reuse by itself.
    """
    root = root.resolve(strict=True)
    if analysis_cache is None:
        analysis_cache = SymbolClosureAnalysisCache(root)
    else:
        analysis_cache.validate_root(root)
    attested_external_toplevels: set[str] = set()
    for module in external_modules:
        if (
            not isinstance(module, str)
            or not module
            or any(not piece.isidentifier() for piece in module.split("."))
        ):
            raise ConfigurationError("external import observation is malformed")
        attested_external_toplevels.add(module.split(".", maxsplit=1)[0])
    normalized_search_roots = tuple(import_search_roots or (".", "src"))
    sites = tuple(sorted(set(call_sites)))
    precise_import_projection = executed_imports is not None
    import_edges = tuple(sorted(set(executed_imports or ())))
    edges_by_path: dict[str, list[ExecutedImportEdge]] = {}
    edge_offsets: dict[tuple[str, int, str, int], ExecutedImportEdge] = {}
    malformed_edge_reasons: set[str] = set()
    for edge in import_edges:
        if not isinstance(edge, ExecutedImportEdge):
            raise ConfigurationError("executed import edge evidence is malformed")
        path_parts = edge.path.split("/")
        valid_module = (
            not edge.module
            or all(piece.isidentifier() for piece in edge.module.split("."))
        )
        if (
            not edge.path
            or not edge.path.endswith(".py")
            or "\\" in edge.path
            or edge.path.startswith("/")
            or any(piece in {"", ".", ".."} for piece in path_parts)
            or type(edge.firstlineno) is not int
            or edge.firstlineno <= 0
            or not isinstance(edge.code_name, str)
            or not edge.code_name
            or type(edge.instruction_offset) is not int
            or edge.instruction_offset < 0
            or type(edge.lineno) is not int
            or edge.lineno <= 0
            or type(edge.end_lineno) is not int
            or edge.end_lineno < edge.lineno
            or type(edge.col_offset) is not int
            or edge.col_offset < 0
            or type(edge.end_col_offset) is not int
            or edge.end_col_offset < 0
            or not valid_module
            or not isinstance(edge.fromlist, tuple)
            or any(
                not isinstance(name, str)
                or not name
                or (name != "*" and not name.isidentifier())
                for name in edge.fromlist
            )
            or type(edge.level) is not int
            or edge.level < 0
            or (edge.level == 0 and not edge.module)
        ):
            raise ConfigurationError("executed import edge evidence is malformed")
        identity = (
            edge.path,
            edge.firstlineno,
            edge.code_name,
            edge.instruction_offset,
        )
        previous = edge_offsets.get(identity)
        if previous is not None and previous != edge:
            malformed_edge_reasons.add(
                "conflicting executed import edge at "
                f"{edge.path}:{edge.firstlineno}:{edge.code_name}:{edge.instruction_offset}"
            )
        edge_offsets[identity] = edge
        edges_by_path.setdefault(edge.path, []).append(edge)

    # CPython's monitoring API does not promise a PY_START callback for module
    # bodies on every supported interpreter, while IMPORT_NAME attribution is
    # taken synchronously from the executing caller frame.  An exact import
    # edge whose caller is the module code object therefore proves that the
    # module state executed even when the redundant call-site event is absent.
    # Reconstruct only that uniquely identifiable surface.  Missing function,
    # lambda, generator, or other caller events continue to fail closed below.
    if precise_import_projection:
        observed_paths = {site.path for site in sites}
        synthetic_module_sites = {
            CallSite(path=path, firstlineno=1, name="<module>")
            for path, path_edges in edges_by_path.items()
            if path not in observed_paths
            and path_edges
            and all(
                edge.firstlineno == 1 and edge.code_name == "<module>"
                for edge in path_edges
            )
        }
        if synthetic_module_sites:
            sites = tuple(sorted({*sites, *synthetic_module_sites}))
    grouped: dict[str, list[CallSite]] = {}
    for site in sites:
        grouped.setdefault(site.path, []).append(site)

    fallback_files: set[str] = set()
    selectors: set[str] = set()
    absent_files: set[str] = set()
    unresolved_imports: set[str] = set(malformed_edge_reasons)
    selected_symbol_ids: dict[str, frozenset[int]] = {}
    for relative, file_sites in sorted(grouped.items()):
        analysis = analysis_cache.analyze(relative)
        if analysis is None or analysis.tree is None:
            fallback_files.add(relative)
            continue
        tree = analysis.tree
        symbols = analysis.symbols
        bound_names = analysis.bound_names
        has_star_import = analysis.has_star_import
        resolved_for_file = tuple(
            match
            for site in file_sites
            if site.name != "<module>"
            for match in [_resolve_call(symbols, site)]
            if match is not None
        )
        # Preserve every structurally resolved callable identity even if a
        # separate global/dynamic-access check below forces the origin file to
        # whole-file fallback.  This is used only to map already observed
        # IMPORT_NAME edges; it never upgrades that file to symbol-level reuse.
        selected_for_file: set[int] = {
            id(match.node) for match in resolved_for_file
        }
        file_selectors: set[str] = {f"{relative}::@module-state"}
        unresolved = has_star_import
        for site in file_sites:
            if site.dynamic_globals or _DYNAMIC_GLOBAL_NAMES.intersection(site.globals):
                unresolved = True
                break
            for global_name in site.globals:
                if global_name in _INTERPRETER_GLOBALS:
                    continue
                if global_name not in bound_names:
                    unresolved = True
                    break
                file_selectors.add(f"{relative}::@binding:{global_name}")
            if unresolved:
                break
            if site.name == "<module>":
                continue
            match = _resolve_call(symbols, site)
            if match is None:
                if _inside_resolved_outer(symbols, site, resolved_for_file):
                    continue
                if _module_state_covers_lambda(tree, site):
                    continue
                unresolved = True
                break
            for class_name in match.class_ancestors:
                file_selectors.add(f"{relative}::{class_name}.@class-state")
            if isinstance(match.node, ast.ClassDef):
                file_selectors.add(f"{relative}::{match.qualified}.@class-state")
            else:
                file_selectors.add(f"{relative}::{match.qualified}")
            selected_for_file.add(id(match.node))

        # Retain every exact symbol match even when another site/global forces
        # this source file to a whole-file fallback.  Complete executed-import
        # evidence can still use those matches to locate imports that actually
        # ran without pretending the file itself is symbol-reviewable.
        selected_symbol_ids[relative] = frozenset(selected_for_file)
        if unresolved:
            fallback_files.add(relative)
        else:
            selectors.update(file_selectors)

    observed_paths = set(grouped)
    for edge_path in sorted(set(edges_by_path) - observed_paths):
        unresolved_imports.add(
            f"executed import edge has no observed project call surface:{edge_path}"
        )

    import_pending = list(
        sorted(
            fallback_files
            | {selector.split("::", 1)[0] for selector in selectors}
        )
    )
    import_visited_projection: set[str] = set()
    import_visited_whole: set[str] = set()
    while import_pending:
        relative = import_pending.pop()
        whole_file = relative in fallback_files
        if whole_file:
            if relative in import_visited_whole:
                continue
            import_visited_whole.add(relative)
        else:
            if (
                relative in import_visited_projection
                or relative in import_visited_whole
            ):
                continue
            import_visited_projection.add(relative)
        analysis = analysis_cache.analyze(relative)
        if analysis is None or analysis.tree is None:
            unresolved_imports.add(f"unparseable:{relative}")
            continue
        try:
            if precise_import_projection:
                required, optional = _projected_import_requests(
                    root,
                    relative,
                    analysis,
                    file_sites=tuple(grouped.get(relative, ())),
                    selected_symbols=selected_symbol_ids.get(relative, frozenset()),
                    edges=tuple(edges_by_path.get(relative, ())),
                )
            else:
                required, optional = analysis_cache.import_requests(
                    relative,
                    analysis,
                )
        except ConfigurationError as exc:
            unresolved_imports.add(str(exc))
            continue
        for module, must_resolve in (
            *((name, True) for name in required),
            *((name, False) for name in optional),
        ):
            try:
                surface = analysis_cache.import_surface(
                    module,
                    search_roots=normalized_search_roots,
                )
            except ConfigurationError as exc:
                unresolved_imports.add(f"{module}:{exc}")
                continue
            absent_files.update(surface.absent_files)
            if (
                must_resolve
                and not surface.resolved_locally
                and module.split(".", maxsplit=1)[0]
                not in attested_external_toplevels
            ):
                unresolved_imports.add(f"external-or-unresolved:{module}")
            for imported in surface.existing_files:
                if (
                    precise_import_projection
                    and imported.endswith(".py")
                    and imported not in fallback_files
                ):
                    # Importing a Python module executes its definition-time
                    # state, not every dormant function body.  The exact
                    # executed edge above proves this import happened; project
                    # functions that subsequently execute are independently
                    # observed call selectors.  This remains valid when the
                    # *origin* is a true whole-file fallback: that file's full
                    # source is still fingerprinted, while only imports proven
                    # to have run extend its dependency graph.  Any imported
                    # target independently forced whole through its own call
                    # evidence still wins and removes this selector below.
                    imported_selector = f"{imported}::@module-state"
                    newly_projected = imported_selector not in selectors
                    selectors.add(imported_selector)
                    selected_symbol_ids.setdefault(imported, frozenset())
                    if newly_projected and imported not in import_visited_projection:
                        import_pending.append(imported)
                    continue
                newly_whole = imported not in fallback_files
                fallback_files.add(imported)
                if (
                    imported.endswith(".py")
                    and (
                        imported not in import_visited_whole
                        or newly_whole
                    )
                ):
                    import_pending.append(imported)

    selectors = {
        selector
        for selector in selectors
        if selector.split("::", 1)[0] not in fallback_files
    }
    return SymbolClosure(
        fallback_files=tuple(sorted(fallback_files)),
        selectors=tuple(sorted(selectors)),
        absent_files=tuple(sorted(absent_files)),
        unresolved_imports=tuple(sorted(unresolved_imports)),
        call_sites=sites,
    )
