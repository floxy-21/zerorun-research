from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ALLOWED_UNSAFE_EFFECTS = {
    "network",
    "clock",
    "randomness",
    "interactive",
    "ipc",
    "daemon",
    "external-write",
}

ALLOWED_RESULT_NORMALIZERS = {"pytest-junit-v1"}
PINNED_OCI_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")


class ConfigurationError(ValueError):
    """Raised when a manifest cannot satisfy the fail-closed contract."""


@dataclass(frozen=True)
class TaskSpec:
    name: str
    command: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    env: tuple[str, ...] = ()
    cacheable: bool = False
    unsafe_effects: tuple[str, ...] = ()
    cache_streams: bool = True
    result_normalizer: str | None = None
    image: str | None = None
    platform: str | None = None
    result_only: bool = False
    closure_reviewed: bool = False
    input_symbols: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any], *, version: int = 1) -> "TaskSpec":
        if version not in (1, 2):
            raise ConfigurationError(f"unsupported manifest version: {version}")

        common_fields = {
            "command", "inputs", "outputs", "env", "cacheable", "unsafe_effects",
            "cache_streams", "result_normalizer",
        }
        v2_fields = common_fields | {
            "image", "platform", "result_only", "closure_reviewed", "input_symbols",
        }
        unknown_fields = set(raw) - (v2_fields if version == 2 else common_fields)
        if unknown_fields:
            raise ConfigurationError(f"task {name!r}: unknown fields: {sorted(unknown_fields)}")

        command = raw.get("command")
        inputs = raw.get("inputs")
        outputs = raw.get("outputs")

        if not isinstance(command, list) or not command or not all(isinstance(x, str) and x for x in command):
            raise ConfigurationError(f"task {name!r}: command must be a non-empty string array")
        if not isinstance(inputs, list) or not inputs or not all(isinstance(x, str) and x for x in inputs):
            raise ConfigurationError(f"task {name!r}: inputs must be a non-empty string array")
        if not isinstance(outputs, list) or not all(isinstance(x, str) and x for x in outputs):
            raise ConfigurationError(f"task {name!r}: outputs must be a string array")

        result_only = raw.get("result_only") is True
        closure_reviewed = raw.get("closure_reviewed") is True
        if version == 1 and not outputs:
            raise ConfigurationError(f"task {name!r}: outputs must be a non-empty string array")
        if version == 2 and outputs:
            raise ConfigurationError(f"task {name!r}: hermetic v2 result-only tasks cannot declare outputs")
        if version == 2 and not result_only:
            raise ConfigurationError(f"task {name!r}: hermetic v2 tasks must set result_only to true")

        input_symbols_raw = raw.get("input_symbols", []) if version == 2 else []
        if not isinstance(input_symbols_raw, list) or not all(
            isinstance(item, str) and item and "::" in item for item in input_symbols_raw
        ):
            raise ConfigurationError(
                f"task {name!r}: input_symbols must be an array of 'relative.py::qualified.symbol' selectors"
            )

        env = raw.get("env", [])
        if not isinstance(env, list) or not all(isinstance(x, str) and x for x in env):
            raise ConfigurationError(f"task {name!r}: env must be a string array")

        effects = raw.get("unsafe_effects", [])
        if not isinstance(effects, list) or not all(isinstance(x, str) for x in effects):
            raise ConfigurationError(f"task {name!r}: unsafe_effects must be a string array")
        unknown = set(effects) - ALLOWED_UNSAFE_EFFECTS
        if unknown:
            raise ConfigurationError(f"task {name!r}: unknown unsafe effects: {sorted(unknown)}")

        cacheable = raw.get("cacheable") is True
        cache_streams = raw.get("cache_streams") is True
        result_normalizer = raw.get("result_normalizer")

        image: str | None = None
        target_platform: str | None = None
        if version == 1:
            if cacheable and not cache_streams:
                raise ConfigurationError(
                    f"task {name!r}: cacheable tasks must set cache_streams to true so replay preserves stdout and stderr"
                )
            if result_normalizer is not None and result_normalizer not in ALLOWED_RESULT_NORMALIZERS:
                raise ConfigurationError(
                    f"task {name!r}: result_normalizer must be one of {sorted(ALLOWED_RESULT_NORMALIZERS)}"
                )
            if result_normalizer is not None and not cacheable:
                raise ConfigurationError(f"task {name!r}: result_normalizer requires cacheable: true")
        else:
            if result_normalizer is not None:
                raise ConfigurationError(f"task {name!r}: result_normalizer is unsupported in hermetic v2")
            if cache_streams:
                raise ConfigurationError(
                    f"task {name!r}: hermetic v2 result-only tasks must set cache_streams to false"
                )
            image = raw.get("image")
            if not isinstance(image, str) or not PINNED_OCI_RE.fullmatch(image):
                raise ConfigurationError(
                    f"task {name!r}: image must be an immutable OCI reference pinned as name@sha256:<64 lowercase hex>"
                )
            target_platform = raw.get("platform")
            if target_platform != "linux/amd64":
                raise ConfigurationError(f"task {name!r}: hermetic v2 supports only platform linux/amd64")
            if not closure_reviewed:
                raise ConfigurationError(
                    f"task {name!r}: hermetic v2 requires closure_reviewed: true; ZeroRun does not infer source-closure completeness"
                )
            if cacheable and effects:
                raise ConfigurationError(
                    f"task {name!r}: cacheable hermetic v2 tasks cannot declare unsafe effects"
                )

        return cls(
            name=name,
            command=tuple(command),
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            env=tuple(sorted(set(env))),
            cacheable=cacheable,
            unsafe_effects=tuple(sorted(set(effects))),
            cache_streams=cache_streams,
            result_normalizer=result_normalizer,
            image=image,
            platform=target_platform,
            result_only=result_only,
            closure_reviewed=closure_reviewed,
            input_symbols=tuple(dict.fromkeys(input_symbols_raw)),
        )


@dataclass(frozen=True)
class Manifest:
    root: Path
    path: Path
    tasks: dict[str, TaskSpec]
    version: int = 1
    # Exact bytes parsed by load_manifest. In-memory setup-only manifests leave
    # this unset and are never eligible for authority or cache provenance.
    source_sha256: str | None = None


@dataclass
class RunResult:
    """A status receipt, with execution provenance determined by ``status``.

    Whole-task ``wall_ms`` measures the current runner interval, not client or
    model latency. On HIT_REUSED, ``execution_ms`` is the stored historical
    execution duration; it does not imply that tests ran during this request.
    ``saved_ms`` is a nonnegative per-hit estimate, not net workflow savings.
    Phase timings are diagnostics and are not an exhaustive time partition.
    """

    task: str
    status: str
    exit_code: int
    wall_ms: float
    execution_ms: float
    cache_key: str | None = None
    reason: str | None = None
    restored_outputs: list[str] = field(default_factory=list)
    verified: bool = False
    saved_ms: float = 0.0
    phase_ms: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "status": self.status,
            "exit_code": self.exit_code,
            "wall_ms": round(self.wall_ms, 3),
            "execution_ms": round(self.execution_ms, 3),
            "saved_ms": round(self.saved_ms, 3),
            "cache_key": self.cache_key,
            "reason": self.reason,
            "restored_outputs": self.restored_outputs,
            "verified": self.verified,
            "phase_ms": {name: round(value, 3) for name, value in sorted(self.phase_ms.items())},
        }
