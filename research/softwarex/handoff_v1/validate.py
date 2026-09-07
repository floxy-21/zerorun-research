"""Read-only reconstruction of controlled handoff receipts, not new execution."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import run as h


def read(path):
    return h.strict(h.ordinary(path))


def exact(actual, expected, message):
    h.require(h.encoded(actual) == h.encoded(expected), message)


def operation(root, name):
    row = read(root / (name + ".json"))
    start = read(root / (name + ".started.json"))
    h.require(row.get("operation") == start.get("operation") == name, "operation name mismatch")
    h.require(row.get("error") is None and isinstance(row.get("result"), dict), "complete operation has error/no result")
    ms = row.get("outer_ms")
    h.require(type(ms) in (float, int) and math.isfinite(ms) and ms > 0, "invalid complete operation time")
    return row


def oracle(root, name, capture_folder):
    row = operation(root, name)
    result = row["result"]
    raw = h.bound(root / capture_folder, result["raw_outcomes"])
    capture = read(root / capture_folder / result["raw_outcomes"]["path"])
    # The frozen helper adds validation fields. Reconcile every original field.
    h.require(capture.get("schema") == "zerorun.benchmark-independent-pytest-shadow.v1", "unexpected raw oracle schema")
    h.require(set(capture) == {"schema", "exit_code", "nodeids", "nodeid_sha256", "outcomes"}, "unexpected raw oracle fields")
    exact(capture, {k: result["capture"][k] for k in capture}, "raw/captured oracle differs")
    h.require(capture["nodeid_sha256"] == h.sha(h.encoded({"nodeids": capture["nodeids"]})), "raw oracle node hash mismatch")
    nodes, values = capture["nodeids"], capture["outcomes"]
    h.require(isinstance(nodes, list) and isinstance(values, list) and nodes
              and len(nodes) == len(values) == len(set(nodes))
              and all(isinstance(n, str) and n and "\x00" not in n for n in nodes), "invalid raw oracle node inventory")
    allowed = {None, "passed", "failed", "skipped"}
    for value in values:
        h.require(isinstance(value, dict) and set(value) == {"setup", "call", "teardown", "wasxfail"}
                  and all(value[k] in allowed for k in ("setup", "call", "teardown"))
                  and type(value["wasxfail"]) is bool and value["setup"] is not None
                  and (value["setup"] != "passed" or value["call"] is not None), "incomplete raw test outcomes")
    expected = {"exit_code": capture["exit_code"], "nodes": dict(zip(nodes, values))}
    exact(result["verdict"], expected, "oracle verdict is stale")
    h.require(type(capture["exit_code"]) is int and capture["exit_code"] == result["runner"]["exit_code"], "oracle exit disagreement")
    plugin = root / capture_folder / "support/benchmark_shadow_plugin.py"
    h.require(h.sha(h.ordinary(plugin)) == result["plugin_sha256"], "oracle plugin changed")
    return row


def block(root, case_id, index, expected):
    saved = read(root / "summary.json")
    h.require(saved["block"] == index and saved["order"] == h.order(case_id, index), "block identity/order changed")
    arms = {}
    for name in ("fresh", "zerorun"):
        base = root / name
        arm = read(base / "summary.json")
        setup = read(base / "setup.json")
        setup_ms = setup.get("setup_outer_ms")
        h.require(type(setup_ms) in (float, int) and math.isfinite(setup_ms) and setup_ms >= 0, "invalid arm preparation cost")
        producer, consumer = operation(base, "producer"), operation(base, "consumer")
        actual_oracle = oracle(base, "oracle", "oracle-capture")
        exact(actual_oracle["result"]["verdict"], expected, "paired oracle mismatch")
        for op in (producer, consumer):
            h.require(type(op["result"]["exit_code"]) is int and op["result"]["exit_code"] == expected["exit_code"], "chain status/fresh mismatch")
        diagnostics = None
        if name == "zerorun":
            h.require(producer["result"]["status"] == "MISS_EXECUTED", "producer cost is not cold cacheable seed")
            h.require(consumer["result"]["status"] in {"HIT_REUSED", "MISS_EXECUTED"}, "unsupported complete consumer status")
            if consumer["result"]["status"] == "HIT_REUSED":
                h.require(isinstance(consumer["result"]["cache_key"], str)
                          and consumer["result"]["cache_key"] == producer["result"]["cache_key"], "hit/seed key mismatch")
            diagnostics = operation(base, "diagnostics")
            h.require(type(diagnostics["result"]["exit_code"]) is int
                      and diagnostics["result"]["exit_code"] == expected["exit_code"]
                      and diagnostics["result"]["status"] in {"VERIFY_MATCH", "MISS_EXECUTED"}, "fresh diagnostic route not reconciled")
        source = setup["before"]
        h.require(source["sha256"] == h.sha(h.encoded(source["rows"])), "source inventory hash changed")
        exact(read(base / "source-after.json"), source, "source drift hidden by saved pass flag")
        row = {"arm": name, "producer": producer, "consumer": consumer, "oracle": actual_oracle,
               "diagnostics": diagnostics, "setup_outer_ms": setup["setup_outer_ms"],
               "source_sha256": source["sha256"], "source_unchanged": True}
        exact(arm, row, "saved arm summary differs from operation records")
        arms[name] = row
    h.require(arms["fresh"]["source_sha256"] == arms["zerorun"]["source_sha256"], "paired input identity differs")
    actual = {"block": index, "order": h.order(case_id, index), "arms": arms, "measurements": h.summarize_block(arms)}
    exact(saved, actual, "saved block aggregation is stale")
    return actual


def validate_saved(directory, acquisition_base=None):
    directory = Path(directory)
    protocol, completion = read(directory / "protocol.json"), read(directory / "completion.json")
    h.require(protocol.get("schema") == "zerorun.controlled-handoff-run.v1"
              and completion.get("schema") == "zerorun.controlled-handoff-completion.v1", "unsupported study receipt")
    h.require(completion["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")), "protocol binding changed")
    cases = h.validate_ledger(protocol["selection"])
    h.require(protocol["selection_sha256"] == protocol["selection_file"]["sha256"], "selection file identity mismatch")
    h.require(protocol["phase"] == protocol["selection"]["phase"] == completion["phase"], "phase mismatch")
    h.require(protocol["model_calls"] == completion["new_model_calls"] == 0
              and protocol["mcp_authorities_created"] is False
              and completion["mcp_authority_files_found"] == 0, "study changed model/authority scope")
    h.require(completion["study_source_unchanged"] is completion["runtime_source_unchanged"] is True, "source integrity failed")
    sources = protocol["study_sources"]
    h.require(isinstance(sources, list) and sources and len({r["path"] for r in sources}) == len(sources), "study source inventory absent/duplicate")
    for row in sources:
        h.bound(h.HERE, row)
    if acquisition_base is not None:
        acquisition_base = Path(acquisition_base)
        raw = h.bound(acquisition_base, protocol["selection_file"])
        exact(h.strict(raw), protocol["selection"], "frozen selection differs from acquisition ledger")
        for case in cases:
            if case.get("disposition") != "UNAVAILABLE_ACQUISITION":
                h.metadata_row(h.bound(acquisition_base, case["metadata"]), case)
                h.bound(acquisition_base, case["source_archive"], h.MAX_ARCHIVE_BYTES)
    summaries = []
    full_blocks = []
    additional_blocks = []
    allowed = {"COMPLETE", "UNAVAILABLE_ACQUISITION", "NOT_RUN_BUDGET", "NOT_RUN_CORRECTNESS_STOP",
               "NOT_RUN_CAMPAIGN_FAILURE", "INCOMPLETE_OR_UNSUPPORTED", "MATERIAL_CORRECTNESS_STOP"}
    for case in cases:
        base = directory / "cases" / case["case_id"]
        saved = read(base / "completion.json")
        h.require(saved["case_id"] == case["case_id"] and saved["repo"] == case["repo"]
                  and saved["disposition"] in allowed, "case identity/disposition invalid")
        if saved["disposition"] == "COMPLETE":
            expected = oracle(base, "compatibility-oracle", "compatibility-capture")["result"]["verdict"]
            h.require(expected["exit_code"] == 0 and any(v["call"] == "passed" for v in expected["nodes"].values()), "compatibility not established")
            actual = {"case_id": case["case_id"], "repo": case["repo"], "disposition": "COMPLETE",
                "blocks": [block(base / ("block-" + str(i)), case["case_id"], i, expected) for i in (0, 1)],
                "all_blocks_complete": True, "agent_session": False}
            exact(saved, actual, "case aggregation is stale")
            full_blocks.extend(actual["blocks"])
        elif (base / "compatibility-oracle.json").is_file():
            # Preserve successfully completed earlier blocks even if a later
            # block failed. They are not pooled into complete-case estimates.
            raw_compatibility = read(base / "compatibility-oracle.json")
            if raw_compatibility.get("error") is None:
                for i in (0, 1):
                    if (base / ("block-" + str(i)) / "summary.json").is_file():
                        expected = oracle(base, "compatibility-oracle", "compatibility-capture")["result"]["verdict"]
                        additional_blocks.append(block(base / ("block-" + str(i)), case["case_id"], i, expected))
        summaries.append(saved)
    exact(completion["cases"], summaries, "completion ledger differs from individual cases")
    count = sum(c["disposition"] == "COMPLETE" for c in summaries)
    h.require(completion["selected_cases"] == protocol["selected_cases"] == len(cases)
              and completion["complete_cases"] == count and completion["all_assigned_outcomes_retained"] is True, "case denominator inconsistent")
    disposition_counts = {name: sum(c["disposition"] == name for c in summaries) for name in sorted(allowed)}
    totals = {arm: sum(b["measurements"]["chain_ms"][arm] for b in full_blocks) for arm in ("fresh", "zerorun")}
    recorded_ms, recorded_operations, failed_operations = 0.0, 0, 0
    for path in sorted((directory / "cases").rglob("*.started.json"), key=lambda p: p.as_posix()):
        completed = path.with_name(path.name.replace(".started.json", ".json"))
        if completed.is_file():
            row = read(completed)
            ms = row.get("outer_ms")
            h.require(type(ms) in (float, int) and math.isfinite(ms) and ms >= 0, "invalid retained operation timing")
            recorded_ms += ms
            recorded_operations += 1
            failed_operations += int(row.get("error") is not None)
    return {"schema": "zerorun.controlled-handoff-reconciliation.v1", "reconciled": True,
        "phase": protocol["phase"], "planned_cases": protocol["planned_cases"], "selected_cases": len(cases),
        "selected_repositories": len({c["repo"] for c in cases}),
        "complete_repositories": len({c["repo"] for c in summaries if c["disposition"] == "COMPLETE"}),
        "complete_cases": count, "complete_blocks": len(full_blocks), "dispositions": disposition_counts,
        "additional_completed_blocks_from_incomplete_cases": len(additional_blocks),
        "all_recorded_operation_ms": recorded_ms, "recorded_operations": recorded_operations,
        "failed_operations": failed_operations,
        "all_recorded_cost_scope": "sum of retained outer operation clocks including compatibility/oracles/diagnostics/failures, not an end-to-end treatment ratio; missing work not imputed",
        "complete_paired_chain_ms": totals,
        "complete_pair_chain_saved_fraction": 1 - totals["zerorun"] / totals["fresh"] if totals["fresh"] else None,
        "material_correctness_stop": completion["material_correctness_stop"],
        "acquisition_bytes_rechecked": acquisition_base is not None,
        "receipt_sha256": h.sha(h.ordinary(directory / "completion.json")),
        "scope": "controlled issue-state handoffs, not model tasks or natural repeat prevalence; incomplete costs excluded from ratios but dispositions retained"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--acquisition-base", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_saved(args.directory, args.acquisition_base), sort_keys=True))


if __name__ == "__main__":
    main()
