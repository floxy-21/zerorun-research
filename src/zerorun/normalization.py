from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

from .model import ConfigurationError, TaskSpec


# Pytest's final terminal line commonly includes a wall duration. It is not a
# test result, and an opted-in normalized task must emit a stable replacement
# on both execution and replay. The expression is deliberately limited to
# lines that contain a pytest outcome token.
_PYTEST_DURATION_LINE = re.compile(
    rb"(?m)^(?=[^\r\n]*\b(?:passed|failed|skipped|error)\b)([^\r\n]*?) in \d+(?:\.\d+)?s(\r?)$"
)
_NON_SEMANTIC_JUNIT_ATTRIBUTES = {"time", "timestamp", "hostname"}


def _normalize_pytest_stream(stream: bytes) -> bytes:
    return _PYTEST_DURATION_LINE.sub(rb"\1 in <duration>\2", stream)


def _normalize_pytest_junit(path: Path, *, workspace_root: Path) -> None:
    try:
        tree = ElementTree.parse(path)
    except ElementTree.ParseError as exc:
        raise ConfigurationError(f"pytest-junit-v1 could not parse declared XML output {path.name}: {exc}") from exc

    root = tree.getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ConfigurationError(
            f"pytest-junit-v1 requires a pytest JUnit testsuite document, got root {root.tag!r} for {path.name}"
        )
    workspace_variants = {
        str(workspace_root),
        str(workspace_root).replace("\\", "/"),
    }

    def normalize_text(value: str | None) -> str | None:
        if value is None:
            return None
        for workspace in workspace_variants:
            value = value.replace(workspace, "<workspace>")
        return value

    for element in tree.iter():
        for attribute in _NON_SEMANTIC_JUNIT_ATTRIBUTES:
            element.attrib.pop(attribute, None)
        for attribute, value in list(element.attrib.items()):
            element.attrib[attribute] = normalize_text(value) or ""
        element.text = normalize_text(element.text)
        element.tail = normalize_text(element.tail)
    ElementTree.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True, short_empty_elements=True)


def normalize_successful_result(
    task: TaskSpec, *, output_root: Path, stdout: bytes, stderr: bytes
) -> tuple[bytes, bytes]:
    """Apply an explicit result normalizer after a successful isolated task.

    The default is exact byte preservation. ``pytest-junit-v1`` is a narrow,
    opt-in contract for reports whose only non-deterministic fields are timing
    and host metadata. Any other XML or stream divergence still reaches fresh
    verification and quarantines the cache.
    """
    if task.result_normalizer is None:
        return stdout, stderr
    if task.result_normalizer != "pytest-junit-v1":
        raise ConfigurationError(f"unsupported result normalizer: {task.result_normalizer}")

    for raw in task.outputs:
        path = output_root / raw
        # Missing or non-file outputs remain the runner's normal unsupported-
        # output decision; do not obscure it as a normalizer failure.
        if path.is_file():
            _normalize_pytest_junit(path, workspace_root=output_root)
    return _normalize_pytest_stream(stdout), _normalize_pytest_stream(stderr)
