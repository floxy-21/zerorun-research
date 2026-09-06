from __future__ import annotations

from .model import ConfigurationError, TaskSpec


# These errors arise from projecting a previously reviewed source selector onto
# a changed checkout. They are not manifest-shape errors: the safe response is
# to deny reuse and execute the deterministic task fresh without publishing a
# result under an uncertain key.
_SOURCE_PROJECTION_PREFIXES = (
    "reviewed module binding not found:",
    "reviewed input symbol not found:",
    "reviewed input class is ambiguous or not found:",
    "input symbol source is missing or outside project:",
    "input symbol source must be a regular file:",
    "could not read input symbol source ",
    "could not parse input symbol source ",
    "Python parser did not expose a complete source range for ",
    "Python parser did not expose a complete class range for ",
    "unsupported Python AST scalar while fingerprinting reviewed source:",
)


def is_source_projection_uncertainty(task: TaskSpec, exc: BaseException) -> bool:
    if not isinstance(exc, ConfigurationError) or not task.input_symbols:
        return False
    text = str(exc)
    return any(text.startswith(prefix) for prefix in _SOURCE_PROJECTION_PREFIXES)
