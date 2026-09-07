from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from .model import ConfigurationError


_MAX_TARGETS = 25_000
_MAX_TOKEN_CHARS = 8_192
_MAX_TOTAL_TOKEN_CHARS = 4 * 1024 * 1024
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
# Exact negative ``-p no:<name>`` pairs only add a plugin-manager deny entry;
# unlike positive ``-p`` forms, they do not import repository-controlled code.
_PYTEST_PLUGIN_NAME = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z"
)
_ZERORUN_EVIDENCE_PLUGIN_MODULES = frozenset(
    {
        "zerorun_collection_plugin",
        "zerorun_qualify_plugin",
        "zerorun_single_pass_plugin",
    }
)
# Pytest automatically pairs ``name`` with ``pytest_name`` when processing a
# negative plugin argument. Keep both registrations non-disableable.
_ZERORUN_EVIDENCE_PLUGINS = _ZERORUN_EVIDENCE_PLUGIN_MODULES | frozenset(
    f"pytest_{name}" for name in _ZERORUN_EVIDENCE_PLUGIN_MODULES
)


def _reject_addopts_plugin_injection(addopts: tuple[str, ...], *, field: str) -> None:
    index = 0
    while index < len(addopts):
        token = addopts[index]
        if token == "-p":
            plugin_argument = addopts[index + 1] if index + 1 < len(addopts) else ""
            plugin_name = (
                plugin_argument.removeprefix("no:")
                if plugin_argument.startswith("no:")
                else ""
            )
            normalized_plugin_name = plugin_name.replace("-", "_")
            if (
                not plugin_name
                or normalized_plugin_name in _ZERORUN_EVIDENCE_PLUGINS
                or _PYTEST_PLUGIN_NAME.fullmatch(plugin_name) is None
            ):
                raise ConfigurationError(
                    f"{field} cannot inject an unreviewed pytest plugin"
                )
            index += 2
            continue
        if token.startswith("-p") or token.startswith("--plugins"):
            raise ConfigurationError(
                f"{field} cannot inject an unreviewed pytest plugin"
            )
        index += 1


def _bounded_string_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_TARGETS:
        raise ConfigurationError(f"{field} must be a bounded string list")
    if (
        not all(
            isinstance(token, str)
            and token
            and len(token) <= _MAX_TOKEN_CHARS
            and "\x00" not in token
            for token in value
        )
        or sum(len(token) for token in value) > _MAX_TOTAL_TOKEN_CHARS
    ):
        raise ConfigurationError(f"{field} contains an invalid or oversized token")
    return tuple(value)


def _reviewed_path_component(
    value: str,
    *,
    field: str,
    allow_selector: bool,
) -> tuple[str, tuple[str, ...]]:
    path_text, separator, selector = value.partition("::")
    if separator and not allow_selector:
        raise ConfigurationError(f"{field} must not contain a pytest node selector")
    if (
        not path_text
        or "\x00" in path_text
        or "\\" in path_text
        or path_text.startswith(("/", "//"))
        or _WINDOWS_DRIVE.match(path_text)
    ):
        raise ConfigurationError(f"{field} must be a cwd-relative POSIX path")
    candidate = PurePosixPath(path_text)
    if (
        candidate.is_absolute()
        or candidate.as_posix() != path_text
        or ".." in candidate.parts
    ):
        raise ConfigurationError(f"{field} must be a canonical in-workspace path")
    if separator and (not selector or selector.startswith(":") or "\x00" in selector):
        raise ConfigurationError(f"{field} contains an invalid pytest node selector")
    return path_text, candidate.parts


def validate_reviewed_pytest_targets(
    targets: object,
    *,
    field: str,
) -> tuple[str, ...]:
    """Validate the positive pytest targets covered by qualification review.

    The reviewed product flow deliberately accepts paths and node selectors,
    not pytest options, response files, or paths that can leave the mounted
    workspace.  Exact target order is evidence and is preserved.
    """

    if (
        not isinstance(targets, tuple)
        or not targets
        or len(targets) > _MAX_TARGETS
        or not all(isinstance(target, str) for target in targets)
        or sum(len(target) for target in targets) > _MAX_TOTAL_TOKEN_CHARS
    ):
        raise ConfigurationError(f"{field} must be a non-empty bounded tuple")
    for index, target in enumerate(targets):
        if (
            not target
            or len(target) > _MAX_TOKEN_CHARS
            or target.startswith(("-", "@"))
        ):
            raise ConfigurationError(
                f"{field} item {index} is not a positive reviewed target"
            )
        _reviewed_path_component(
            target,
            field=f"{field} item {index}",
            allow_selector=True,
        )
    if len(set(targets)) != len(targets):
        raise ConfigurationError(f"{field} must not contain duplicate targets")
    return targets


def validate_collection_invocation_contract(
    invocation: object,
    *,
    expected_targets: tuple[str, ...],
    field: str,
) -> dict[str, Any]:
    """Validate pytest's final, collection-time interpretation of a request."""

    expected_targets = validate_reviewed_pytest_targets(
        expected_targets,
        field=f"{field} expected targets",
    )
    if not isinstance(invocation, dict) or set(invocation) != {
        "rootpath",
        "args",
        "pyargs",
        "addopts",
    }:
        raise ConfigurationError(f"{field} has unexpected or missing fields")
    rootpath = invocation.get("rootpath")
    if not isinstance(rootpath, str) or len(rootpath) > _MAX_TOKEN_CHARS:
        raise ConfigurationError(f"{field} rootpath is invalid")
    _reviewed_path_component(
        rootpath,
        field=f"{field} rootpath",
        allow_selector=False,
    )
    args = _bounded_string_list(invocation.get("args"), field=f"{field} args")
    addopts = _bounded_string_list(
        invocation.get("addopts"),
        field=f"{field} addopts",
    )
    if args != expected_targets:
        raise ConfigurationError(
            f"{field} final pytest arguments do not exactly match reviewed targets"
        )
    if invocation.get("pyargs") is not False:
        raise ConfigurationError(f"{field} cannot use pytest --pyargs resolution")
    if any(token.startswith("@") for token in addopts):
        raise ConfigurationError(f"{field} cannot use pytest argument files")
    _reject_addopts_plugin_injection(addopts, field=field)
    return invocation


def validate_collection_item_paths(
    items: object,
    *,
    expected_targets: tuple[str, ...],
    rootpath: str,
    field: str,
) -> None:
    """Require every collected item to be a real in-target workspace path."""

    targets = validate_reviewed_pytest_targets(
        expected_targets,
        field=f"{field} expected targets",
    )
    _, root_parts = _reviewed_path_component(
        rootpath,
        field=f"{field} rootpath",
        allow_selector=False,
    )
    target_rows = [
        (
            *_reviewed_path_component(
                target,
                field=f"{field} expected target",
                allow_selector=True,
            ),
            "::" in target,
        )
        for target in targets
    ]
    if not isinstance(items, list) or len(items) > _MAX_TARGETS:
        raise ConfigurationError(f"{field} must be a bounded item list")
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigurationError(f"{field} item {index} is invalid")
        path = item.get("path")
        if not isinstance(path, str) or not path or len(path) > _MAX_TOKEN_CHARS:
            raise ConfigurationError(
                f"{field} item {index} has no reviewable workspace path"
            )
        path_text, path_parts = _reviewed_path_component(
            path,
            field=f"{field} item {index} path",
            allow_selector=False,
        )
        if root_parts and path_parts[: len(root_parts)] != root_parts:
            raise ConfigurationError(
                f"{field} item {index} is outside the reported pytest rootpath"
            )
        in_target = False
        for target_text, target_parts, target_has_selector in target_rows:
            if target_has_selector:
                matches = path_text == target_text
            elif target_text == ".":
                matches = True
            else:
                matches = (
                    path_parts == target_parts
                    or path_parts[: len(target_parts)] == target_parts
                )
            if matches:
                in_target = True
                break
        if not in_target:
            raise ConfigurationError(
                f"{field} item {index} is outside the reviewed pytest targets"
            )
