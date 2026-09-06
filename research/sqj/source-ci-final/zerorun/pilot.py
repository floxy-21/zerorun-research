from __future__ import annotations

import math
import time
from collections import Counter
from typing import Iterable, Mapping


REUSE_STATUSES = {"HIT_REUSED", "HIT_REPLAYED"}
VERIFICATION_STATUSES = {
    "VERIFY_MATCH",
    "VERIFY_MISMATCH",
    "FORCE_VERIFY_MATCH",
    "FORCE_CONFLICT",
}
NORMAL_FRESH_STATUSES = {
    "MISS_EXECUTED",
    "MISS_FAILED",
    "REJECTED_INPUT_RACE",
    "BYPASS_INVALID_CACHE",
    "BYPASS_UNSUPPORTED_OUTPUT",
}
FORCE_FRESH_STATUSES = {"FORCE_EXECUTED"}
NO_EXECUTION_STOP_STATUSES = {
    "BYPASS_ACTION_BUSY",
    "BYPASS_PROJECT_BUSY",
}
SAFETY_STOP_STATUSES = {
    "VERIFY_MISMATCH",
    "FORCE_CONFLICT",
    "REJECTED_INPUT_RACE",
    "BYPASS_ACTION_BUSY",
    "BYPASS_PROJECT_BUSY",
    "BYPASS_INVALID_CACHE",
    "BYPASS_UNSUPPORTED_OUTPUT",
}
FRESH_EXECUTION_STATUSES = NORMAL_FRESH_STATUSES | VERIFICATION_STATUSES | FORCE_FRESH_STATUSES
PILOT_EVENT_STATUSES = (
    REUSE_STATUSES
    | NORMAL_FRESH_STATUSES
    | VERIFICATION_STATUSES
    | FORCE_FRESH_STATUSES
    | NO_EXECUTION_STOP_STATUSES
)


def _number(event: Mapping[str, object], field: str) -> float:
    value = event.get(field, 0)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        result = ordered[lower]
    else:
        weight = index - lower
        result = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(result, 3)


def build_pilot_report(events: Iterable[Mapping[str, object]]) -> dict[str, object]:
    """Build an aggregate pilot report without exporting repository-sensitive fields.

    The report intentionally omits source contents, file paths, commands,
    environment values, cache keys, reasons, task names, and raw per-event records.
    Legacy `observe` measurements and unknown event types are excluded so the
    report cannot accidentally describe direct baseline work as ZeroRun runtime
    activity.
    """

    all_events = [dict(event) for event in events]
    materialized = [
        event
        for event in all_events
        if str(event.get("status", "UNKNOWN")) in PILOT_EVENT_STATUSES
    ]
    ignored_count = len(all_events) - len(materialized)
    statuses = Counter(str(event.get("status", "UNKNOWN")) for event in materialized)

    reuse_count = sum(statuses[status] for status in REUSE_STATUSES)
    fresh_count = sum(statuses[status] for status in NORMAL_FRESH_STATUSES)
    normal_run_count = reuse_count + fresh_count
    verification_count = sum(statuses[status] for status in VERIFICATION_STATUSES)
    verification_mismatch_count = statuses["VERIFY_MISMATCH"] + statuses["FORCE_CONFLICT"]
    safety_stop_count = sum(statuses[status] for status in SAFETY_STOP_STATUSES)

    normal_wall = [
        _number(event, "wall_ms")
        for event in materialized
        if str(event.get("status", "UNKNOWN")) in REUSE_STATUSES | NORMAL_FRESH_STATUSES
    ]

    saved_ms = round(sum(_number(event, "saved_ms") for event in materialized), 3)
    fresh_compute_ms = round(
        sum(
            _number(event, "execution_ms")
            for event in materialized
            if str(event.get("status", "UNKNOWN")) in FRESH_EXECUTION_STATUSES
        ),
        3,
    )
    reused_prior_execution_reference_ms = round(
        sum(
            _number(event, "execution_ms")
            for event in materialized
            if str(event.get("status", "UNKNOWN")) in REUSE_STATUSES
        ),
        3,
    )
    wall_ms = round(sum(_number(event, "wall_ms") for event in materialized), 3)

    return {
        "schema": "zerorun-pilot-report-v1",
        "generated_at_unix": round(time.time(), 3),
        "privacy": {
            "aggregate_only": True,
            "contains_source_code": False,
            "contains_repository_paths": False,
            "contains_task_names": False,
            "contains_commands": False,
            "contains_environment_values": False,
            "contains_cache_keys": False,
            "contains_reasons": False,
            "contains_raw_events": False,
        },
        "events": {
            "total": len(materialized),
            "ignored_non_pilot_events": ignored_count,
            "normal_runs": normal_run_count,
            "reuse": reuse_count,
            "fresh_or_conservative_fallback": fresh_count,
            "verification": verification_count,
            "verification_mismatch_or_conflict": verification_mismatch_count,
            "safety_stops": safety_stop_count,
            "by_status": dict(sorted(statuses.items())),
        },
        "performance": {
            "reuse_rate": round(reuse_count / normal_run_count, 6) if normal_run_count else None,
            "total_saved_ms": saved_ms,
            "fresh_compute_executed_ms": fresh_compute_ms,
            "reused_prior_execution_reference_ms": reused_prior_execution_reference_ms,
            "total_zerorun_wall_ms": wall_ms,
            "zerorun_wall_p50_ms": _percentile(normal_wall, 0.50),
            "zerorun_wall_p95_ms": _percentile(normal_wall, 0.95),
        },
        "interpretation": {
            "direct_baseline_included": False,
            "note": (
                "This export summarizes configured ZeroRun pilot events only. "
                "fresh_compute_executed_ms counts actual fresh task execution and excludes reuse hits; "
                "reused_prior_execution_reference_ms is reference duration stored on reused hits, not compute consumed by the hit. "
                "Direct-test baseline measurements, customer identity, and any claim about external request counts "
                "must be maintained separately under the agreed pilot protocol."
            ),
        },
    }
