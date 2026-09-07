"""Read-only canonical replay policy for the archived timing analysis.

The original analyzers and measurements remain frozen. Canonical replay uses
CPython 3.12--3.14 and exact JSON equality. Only the top-level inventory of
zero-byte crash remnants is order-insensitive; its rows and all numbers remain
exact. This is not a numerical tolerance or a change to runtime eligibility.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
INVENTORY_FIELD = "unparseable_zero_byte_receipts"
SCHEMA = "zerorun.short-replication-recovered-analysis.v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _runtime_identity():
    return sys.implementation.name, tuple(sys.version_info[:2])


def require_canonical_python():
    implementation, version = _runtime_identity()
    require(implementation == "cpython" and version in {(3, 12), (3, 13), (3, 14)},
            "Canonical manuscript/timing analysis requires CPython 3.12--3.14; "
            "Python 3.10/3.11 float summation differs. ZeroRun runtime and the "
            "account-free quickstart still support Python 3.10+. No numeric tolerance is applied.")
    return {"implementation": implementation, "major_minor": list(version)}


def _validate_json(value):
    kind = type(value)
    if kind in (str, bool, int, type(None)):
        return
    if kind is float:
        require(math.isfinite(value), "nonfinite analysis value refused")
        return
    if kind is list:
        for item in value:
            _validate_json(item)
        return
    if kind is dict:
        require(all(type(key) is str for key in value), "analysis object keys must be strings")
        for item in value.values():
            _validate_json(item)
        return
    raise ValueError("non-JSON analysis value type refused")


def _normalized_inventory(value):
    _validate_json(value)
    require(type(value) is dict and value.get("schema") == SCHEMA,
            "unexpected recovered-analysis schema")
    rows = value.get(INVENTORY_FIELD)
    require(type(rows) is list, "zero-byte inventory must be a list")
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == {"path", "bytes", "sha256", "classification"},
                "zero-byte inventory row fields differ")
        path = row["path"]
        require(type(path) is str and path and path not in seen,
                "zero-byte inventory has an invalid or duplicate path")
        require(type(row["bytes"]) is int and row["bytes"] == 0
                and row["sha256"] == hashlib.sha256(b"").hexdigest()
                and type(row["classification"]) is str and row["classification"],
                "zero-byte inventory metadata differs")
        seen.add(path)
    normalized = deepcopy(value)
    normalized[INVENTORY_FIELD] = sorted(normalized[INVENTORY_FIELD], key=lambda row: row["path"])
    return normalized


def canonical_reconciliation(actual, reference):
    """Return reference ordering only after exact whole-object reconciliation."""
    require_canonical_python()
    actual_normalized, reference_normalized = _normalized_inventory(actual), _normalized_inventory(reference)
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    require(encode(actual_normalized) == encode(reference_normalized),
            "saved independent reconciliation is stale (exact values required; only zero-byte inventory order may differ)")
    return deepcopy(reference)


def reconcile_saved_analysis(actual, root=ROOT):
    """Bind the immutable archived result before applying the narrow policy."""
    require_canonical_python()
    from research.softwarex import analyze_operating_region as operating
    raw = operating.read_regular(Path(root), operating.SAVED)
    require(hashlib.sha256(raw).hexdigest() == operating.PINNED[operating.SAVED],
            "frozen saved reconciliation bytes changed")
    return canonical_reconciliation(actual, operating.strict_json(raw))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True,
                        help="reconcile retained evidence without writing any file")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    runtime = require_canonical_python()
    from research.softwarex import analyze_operating_region as operating
    result, anchors = operating.reconcile(args.root.resolve())
    print(json.dumps({"schema": "zerorun.analysis-reproduction-check.v1", "passed": True,
                      "check_only": True, "runtime": runtime,
                      "order_insensitive_field": INVENTORY_FIELD,
                      "numeric_tolerance_applied": False,
                      "raw_evidence_and_frozen_analyzers_unchanged": True,
                      "anchors_verified": len(anchors), "counts": result["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
