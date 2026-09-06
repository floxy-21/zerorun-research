from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from .model import ConfigurationError


@dataclass(frozen=True)
class JsonLimits:
    max_bytes: int
    max_depth: int
    max_values: int
    max_object_members: int
    max_structural_tokens: int
    max_number_chars: int
    max_string_chars: int
    max_total_string_chars: int
    allow_floats: bool = True


def _preflight(raw: bytes, *, label: str, limits: JsonLimits) -> None:
    if len(raw) > limits.max_bytes:
        raise ConfigurationError(
            f"{label} exceeds the {limits.max_bytes}-byte safety limit"
        )
    depth = 0
    structural_tokens = 0
    in_string = False
    escaped = False
    encoded_string_bytes = 0
    max_encoded_string_bytes = (limits.max_string_chars * 6) + 2
    for byte in raw:
        if in_string:
            encoded_string_bytes += 1
            if encoded_string_bytes > max_encoded_string_bytes:
                raise ConfigurationError(f"{label} contains an overlong JSON string")
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
            encoded_string_bytes = 0
        elif byte in (0x7B, 0x5B):  # { [
            depth += 1
            structural_tokens += 1
            if depth > limits.max_depth:
                raise ConfigurationError(
                    f"{label} exceeds the JSON depth limit of {limits.max_depth}"
                )
        elif byte in (0x7D, 0x5D):  # } ]
            depth -= 1
            structural_tokens += 1
            if depth < 0:
                break
        elif byte in (0x2C, 0x3A):  # comma, colon
            structural_tokens += 1
        if structural_tokens > limits.max_structural_tokens:
            raise ConfigurationError(f"{label} contains too many structural members")


def loads_bounded_json(raw: bytes, *, label: str, limits: JsonLimits) -> object:
    """Decode one bounded, unambiguous JSON value or fail closed."""

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len(pairs) > limits.max_object_members:
            raise ConfigurationError(f"{label} contains an object with too many members")
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ConfigurationError(
                    f"{label} contains duplicate JSON member {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> object:
        raise ConfigurationError(f"{label} contains non-finite JSON constant {value}")

    def parse_integer(value: str) -> int:
        if len(value) > limits.max_number_chars:
            raise ConfigurationError(f"{label} contains an overlong JSON integer")
        try:
            return int(value)
        except ValueError as exc:
            raise ConfigurationError(f"{label} contains an invalid JSON integer") from exc

    def parse_float(value: str) -> float:
        if len(value) > limits.max_number_chars:
            raise ConfigurationError(f"{label} contains an overlong JSON number")
        try:
            result = float(value)
        except ValueError as exc:
            raise ConfigurationError(f"{label} contains an invalid JSON number") from exc
        if not math.isfinite(result):
            raise ConfigurationError(f"{label} contains a non-finite JSON number")
        if not limits.allow_floats:
            raise ConfigurationError(f"{label} contains an unsupported JSON number")
        return result

    try:
        _preflight(raw, label=label, limits=limits)
        decoded = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            parse_float=parse_float,
            parse_int=parse_integer,
            object_pairs_hook=strict_object,
        )
        stack: list[tuple[object, int]] = [(decoded, 1)]
        values = 0
        total_string_chars = 0
        while stack:
            value, depth = stack.pop()
            values += 1
            if values > limits.max_values:
                raise ConfigurationError(f"{label} contains too many JSON values")
            if depth > limits.max_depth:
                raise ConfigurationError(
                    f"{label} exceeds the JSON depth limit of {limits.max_depth}"
                )
            if isinstance(value, dict):
                if len(value) > limits.max_object_members:
                    raise ConfigurationError(
                        f"{label} contains an object with too many members"
                    )
                for key, child in value.items():
                    total_string_chars += len(key)
                    if len(key) > limits.max_string_chars or "\x00" in key:
                        raise ConfigurationError(
                            f"{label} contains an invalid or overlong object key"
                        )
                    stack.append((child, depth + 1))
            elif isinstance(value, list):
                stack.extend((child, depth + 1) for child in value)
            elif isinstance(value, str):
                total_string_chars += len(value)
                if len(value) > limits.max_string_chars or "\x00" in value:
                    raise ConfigurationError(
                        f"{label} contains an invalid or overlong string"
                    )
            elif isinstance(value, float) and not math.isfinite(value):
                raise ConfigurationError(f"{label} contains a non-finite JSON number")
            elif value is not None and not isinstance(value, (str, int, float, bool)):
                raise ConfigurationError(f"{label} contains a value outside JSON")
            if total_string_chars > limits.max_total_string_chars:
                raise ConfigurationError(
                    f"{label} exceeds the aggregate JSON string-size limit"
                )
        return decoded
    except ConfigurationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
        OverflowError,
    ) as exc:
        raise ConfigurationError(f"could not parse {label}: {exc}") from exc
