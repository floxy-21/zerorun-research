"""Aggregate all batches of the frozen commercial compatibility smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from tools.commercial_repo_smoke import (
    MIN_SUCCESSFUL_INTEGRATIONS,
    RESULT_SCHEMA,
    _EXPECTED_TOOLS,
    load_receipt,
    load_selection,
)
from zerorun.codex import _SKILL


AGGREGATE_SCHEMA = "zerorun.commercial-compatibility-aggregate.v1"
EXPECTED_BATCH_COUNT = 10
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _validate_pass_proof(row: dict[str, Any], *, engine_version: str) -> None:
    install = row.get("install")
    mcp = row.get("mcp")
    expected_skill = hashlib.sha256(_SKILL.encode("utf-8")).hexdigest()
    if (
        row.get("phase") != "complete"
        or row.get("installed_skill_sha256") != expected_skill
        or not isinstance(install, dict)
        or install.get("schema") != "zerorun-codex-install-v5"
        or install.get("integration_ready") is not True
        or install.get("skill", {}).get("installed") is not True
        or install.get("skill", {}).get("conflict") is not False
        or install.get("mcp", {}).get("registered") is not True
        or not isinstance(mcp, dict)
        or mcp.get("server") != {"name": "zerorun", "version": engine_version}
        or mcp.get("tool_count") != len(_EXPECTED_TOOLS)
        or mcp.get("escape_rejected") is not True
        or mcp.get("legacy_observe_test_rejected") is not True
        or mcp.get("doctor_mode") not in {"observe-only", "task-reuse", "pytest-node-reuse"}
        or mcp.get("stats_mode") not in {"observe-only", "task-reuse", "pytest-node-reuse"}
    ):
        raise ValueError("passing commercial row lacks complete Codex/MCP proof")


def _validate_safe_refusal_proof(row: dict[str, Any]) -> None:
    install = row.get("install")
    proof = row.get("refusal_proof")
    if (
        row.get("phase") != "complete"
        or not isinstance(row.get("reason"), str)
        or not row["reason"]
        or not isinstance(install, dict)
        or install.get("integration_ready") is not False
        or install.get("skill", {}).get("conflict") is not True
        or install.get("skill", {}).get("installed") is not False
        or not isinstance(proof, dict)
        or proof.get("skill_conflict") is not True
        or proof.get("unchanged") is not True
        or proof.get("before") != proof.get("after")
        or proof.get("before", {}).get("kind") not in {
            "link",
            "regular_file",
            "non_regular",
        }
    ):
        raise ValueError("safe-refusal commercial row lacks unchanged-conflict proof")


def aggregate(selection: dict[str, Any], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    if not receipts:
        raise ValueError("no commercial compatibility receipts were supplied")
    expected_batch_count = receipts[0].get("batch_count")
    if (
        not isinstance(expected_batch_count, int)
        or isinstance(expected_batch_count, bool)
        or expected_batch_count != EXPECTED_BATCH_COUNT
    ):
        raise ValueError(
            "receipt batch count does not match the frozen ten-batch protocol"
        )
    expected_batches = set(range(expected_batch_count))
    expected_engine_version = receipts[0].get("engine_version")
    expected_engine_commit = receipts[0].get("engine_commit")
    expected_codex_version = receipts[0].get("codex_version")
    if (
        not isinstance(expected_engine_version, str)
        or not expected_engine_version
        or not isinstance(expected_engine_commit, str)
        or not _COMMIT.fullmatch(expected_engine_commit)
        or not isinstance(expected_codex_version, str)
        or not expected_codex_version
    ):
        raise ValueError("receipt product identity is incomplete or malformed")
    seen_batches: set[int] = set()
    rows: list[dict[str, Any]] = []
    for receipt in receipts:
        if receipt.get("schema") != RESULT_SCHEMA:
            raise ValueError("unsupported commercial compatibility receipt schema")
        if receipt.get("selection_sha256") != selection["corpus_sha256"]:
            raise ValueError("receipt was produced from a different selection")
        if receipt.get("upstream_code_executed") is not False:
            raise ValueError("receipt does not attest that upstream code remained unexecuted")
        if receipt.get("batch_count") != expected_batch_count:
            raise ValueError("receipts disagree on batch count")
        if (
            receipt.get("engine_version") != expected_engine_version
            or receipt.get("engine_commit") != expected_engine_commit
            or receipt.get("codex_version") != expected_codex_version
        ):
            raise ValueError("receipts disagree on product identity")
        batch_index = receipt.get("batch_index")
        if type(batch_index) is not int or batch_index in seen_batches:
            raise ValueError("receipt batch index is invalid or duplicated")
        seen_batches.add(batch_index)
        receipt_rows = receipt.get("rows")
        attempted = receipt.get("attempted")
        if (
            not isinstance(receipt_rows, list)
            or type(attempted) is not int
            or attempted != len(receipt_rows)
        ):
            raise ValueError("receipt attempted count is inconsistent")
        actual_counts = {
            status: sum(
                isinstance(row, dict) and row.get("status") == status
                for row in receipt_rows
            )
            for status in ("pass", "safe_refusal", "failure")
        }
        claimed_counts = receipt.get("counts")
        if (
            not isinstance(claimed_counts, dict)
            or set(claimed_counts) != set(actual_counts)
            or any(type(value) is not int for value in claimed_counts.values())
            or claimed_counts != actual_counts
            or sum(actual_counts.values()) != len(receipt_rows)
        ):
            raise ValueError("receipt outcome counts are inconsistent")
        if any(
            not isinstance(row, dict)
            or type(row.get("selection_index")) is not int
            or row["selection_index"] % expected_batch_count != batch_index
            for row in receipt_rows
        ):
            raise ValueError("receipt contains a row assigned to the wrong batch")
        rows.extend(receipt_rows)
    if seen_batches != expected_batches:
        raise ValueError(f"missing batches: {sorted(expected_batches - seen_batches)}")
    if len(rows) != 100:
        raise ValueError(f"expected 100 attempted rows, received {len(rows)}")
    by_index: dict[int, dict[str, Any]] = {}
    for row in rows:
        index = row.get("selection_index")
        if type(index) is not int or index in by_index:
            raise ValueError("selection index is invalid or duplicated")
        expected = selection["repositories"][index] if 0 <= index < 100 else None
        if expected is None or (row.get("repository"), row.get("commit")) != (
            expected["repository"],
            expected["commit"],
        ):
            raise ValueError(f"receipt identity mismatch at selection index {index}")
        if row.get("status") not in {"pass", "safe_refusal", "failure"}:
            raise ValueError(f"invalid outcome at selection index {index}")
        if row["status"] == "pass":
            _validate_pass_proof(row, engine_version=expected_engine_version)
        elif row["status"] == "safe_refusal":
            _validate_safe_refusal_proof(row)
        elif not isinstance(row.get("reason"), str) or not row["reason"]:
            raise ValueError("failure row lacks a diagnostic reason")
        by_index[index] = row
    if set(by_index) != set(range(100)):
        raise ValueError("not every frozen selection index was attempted exactly once")
    ordered = [by_index[index] for index in range(100)]
    counts = {
        status: sum(row["status"] == status for row in ordered)
        for status in ("pass", "safe_refusal", "failure")
    }
    return {
        "schema": AGGREGATE_SCHEMA,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_sha256": selection["corpus_sha256"],
        "engine_version": expected_engine_version,
        "engine_commit": expected_engine_commit,
        "codex_version": expected_codex_version,
        "attempted_repositories": 100,
        "distinct_repositories": 100,
        "counts": counts,
        "upstream_code_executed": False,
        "legacy_observe_test_rejections": counts["pass"],
        "minimum_successful_integrations": MIN_SUCCESSFUL_INTEGRATIONS,
        "gate_pass": (
            counts["failure"] == 0
            and counts["pass"] >= MIN_SUCCESSFUL_INTEGRATIONS
        ),
        "rows": ordered,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selection = load_selection(args.selection)
    receipts = [load_receipt(path) for path in args.receipt]
    result = aggregate(selection, receipts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in ("attempted_repositories", "counts", "gate_pass")}, indent=2))
    return 0 if result["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
