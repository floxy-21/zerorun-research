"""Read-only evidence reconciliation; never runs issue tests, Git or models."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from . import run as r

h, hv, pv, producer = r.h, r.hv, r.pv, r.producer
DISPOSITIONS = {"COMPLETE", "NOT_RUN_CORRECTNESS_STOP", "NOT_RUN_BUDGET", "PRODUCER_UNAVAILABLE",
                "PRODUCER_NOT_EVALUABLE", "MATERIAL_CORRECTNESS_STOP", "INCOMPLETE_OR_UNSUPPORTED",
                "NOT_RUN_CAMPAIGN_FAILURE"}


def same(actual, expected, message):
    h.require(h.encoded(actual) == h.encoded(expected), message)


def check_archived_final_source(raw, captured_inventory):
    # Producer inventory uses Path ordering; archive inventory uses path-string
    # ordering. Compare the same complete rows in one order, retaining every
    # path, kind, byte count and hash. This matches the producer validator and
    # reconstruction checks without changing the captured evidence.
    same(pv.source_archive_inventory(raw), sorted(captured_inventory, key=lambda row: row["path"]),
         "archived final source bytes differ")


def optional_oracle(root, name, capture):
    path = root / (name + ".json")
    if not path.is_file():
        return None
    row = hv.read(path)
    start = hv.read(root / (name + ".started.json"))
    h.require(row["operation"] == start["operation"] == name, "partial oracle identity differs")
    if row["error"] is not None:
        h.require(row["result"] is None and isinstance(row["error"], dict), "partial oracle hides result")
        return None
    return hv.oracle(root, name, capture)


def milliseconds(value):
    h.require(type(value) in (int, float) and math.isfinite(value) and value >= 0, "invalid descriptive time")
    return value


def validate_saved(directory, prepared, image_build=None):
    directory, prepared = Path(directory), Path(prepared)
    p, result = hv.read(directory / "protocol.json"), hv.read(directory / "completion.json")
    h.require(p["schema"] == "zerorun.agent-handoff-evaluation-protocol.v1"
              and result["schema"] == "zerorun.agent-handoff-evaluation-completion.v1", "companion receipt schema")
    h.require(result["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")), "companion protocol binding differs")
    frozen_raw = h.bound(prepared, p["producer_freeze"])
    frozen = h.strict(frozen_raw)
    cases = producer.c.selected(frozen["ledger"])
    same(cases, p["cases"], "companion selected cases changed")
    same(cases, frozen["cases"], "producer selection changed")
    h.require(p["phase"] == frozen["phase"] and p["planned_cases"] == (2 if frozen["phase"] == "pilot" else 6), "agent denominator changed")
    h.require(type(p["budget_seconds"]) is int and 1 <= p["budget_seconds"] <= 7200, "campaign budget changed")
    r.cutoff_seconds(p["cutoff_utc"])  # Parse the historical cutoff; do not rerun anything.
    same(r.input_inventory(prepared, len(cases)), p["producer_inputs"], "producer evidence input bytes changed")
    for key, folder in (("sources", r.HERE), ("handoff_sources", h.HERE), ("producer_sources", producer.HERE)):
        same(p[key], r.sources(folder), "frozen companion/helper source files changed")
        same(p[key], result[key + "_after"], "before/after companion source mismatch")
    same(p["producer_inputs"], result["producer_inputs_after"], "producer evidence changed during evaluation")
    h.validate_runtime_rows(p["engine"]["runtime_files"])
    same(p["engine"]["runtime_files"], result["runtime_after"], "runtime source changed")
    same(p["runtime_attestation"], result["runtime_attestation_after"], "runtime image changed")
    h.require(p["runtime_attestation"]["requested_image"] == p["runtime_image"]
              and p["execution_seconds"] == 120 and p["new_model_calls"] == result["new_model_calls"] == 0
              and p["mcp_authority_created"] is False and result["mcp_authority_files_found"] == 0
              and p["reference_patch_applied"] is False and p["natural_hit_frequency_study"] is False
              and p["agent_and_reference_patch_denominators_combined"] is False,
              "runtime/execution/authority/claim scope changed")
    if p["image_deployment"] is not None:
        from research.softwarex.handoff_image_v2 import build_image as ib
        from research.softwarex.handoff_image_v2.validate import validate_image
        h.require(image_build is not None, "image-build evidence required")
        image = validate_image(image_build)
        same(image, p["image_deployment"], "derived image evidence changed")
        same(p["image_sources"], ib.source_inventory(), "derived-image adapter source changed")
        same(p["image_sources"], result["image_sources_after"], "image source drift")
        h.require(p["runtime_image"] == image["image"]["requested"]
                  and p["runtime_attestation"]["image_id"] == image["image"]["image_id"]
                  and p["runtime_attestation"]["config_sha256"] == image["image"]["config_sha256"], "wrong derived execution image")
    else:
        h.require(p["runtime_image"] == h.IMAGE and p["image_sources"] is result["image_sources_after"] is None,
                  "unexpected alternate image")
    rows, blocks, additional_blocks, costs, material_stop, budget_stop = [], [], [], [], False, False
    for index, case in enumerate(cases):
        root = directory / "cases" / case["case_id"]
        row = hv.read(root / "completion.json")
        h.require(row["case_id"] == case["case_id"] and row["repo"] == case["repo"]
                  and row["case_index"] == index and row["disposition"] in DISPOSITIONS, "companion case identity/disposition")
        if material_stop:
            h.require(row["disposition"] == "NOT_RUN_CORRECTNESS_STOP", "continued after material mismatch")
        if budget_stop:
            h.require(row["disposition"] == "NOT_RUN_BUDGET", "continued after campaign budget stop")
        material_stop = material_stop or row["disposition"] == "MATERIAL_CORRECTNESS_STOP"
        budget_stop = budget_stop or row["disposition"] == "NOT_RUN_BUDGET"
        if row["producer"] is not None:
            actual_producer = pv.validate_receipt(prepared / f"case-{index:02d}" / "session.json", directory=prepared)
            same(actual_producer, row["producer"], "producer summary changed")
        else:
            actual_producer = {}
        case_costs = {"case_id": case["case_id"], "reconstruction_ms": None, "baseline_setup_ms": None,
                      "baseline_oracle_ms": None, "final_compatibility_oracle_ms": None,
                      "model_session_outer_seconds": None}
        if row["producer"] is not None:
            producer_receipt = hv.read(prepared / f"case-{index:02d}" / "session.json")
            if "outer_seconds" in producer_receipt:
                case_costs["model_session_outer_seconds"] = milliseconds(producer_receipt["outer_seconds"])
        if row["disposition"] in {"NOT_RUN_CORRECTNESS_STOP", "NOT_RUN_BUDGET", "PRODUCER_UNAVAILABLE", "PRODUCER_NOT_EVALUABLE", "NOT_RUN_CAMPAIGN_FAILURE"}:
            h.require(not any((root / name).exists() for name in ("reconstruction.json", "baseline-oracle.started.json", "compatibility-oracle.started.json", "block-0", "block-1")),
                      "skipped/not-evaluable producer nevertheless executed")
        if row["disposition"] == "PRODUCER_UNAVAILABLE":
            h.require(not (prepared / f"case-{index:02d}" / "session.json").exists() and row["producer"] is None,
                      "available producer silently skipped")
        if row["disposition"] == "PRODUCER_NOT_EVALUABLE":
            h.require(actual_producer and (actual_producer["model_invocation_attempted"] is not True or actual_producer["boundary_pass"] is not True),
                      "evaluable producer reclassified without cause")
        if (root / "reconstruction.json").is_file():
            reconstruction = hv.read(root / "reconstruction.json")
            prep = hv.read(prepared / f"case-{index:02d}" / "preparation.json")
            session = hv.read(prepared / f"case-{index:02d}" / "session.json")
            h.require(actual_producer.get("boundary_pass") is True and actual_producer.get("model_invocation_attempted") is True
                      and reconstruction["case_id"] == case["case_id"]
                      and reconstruction["reference_source_patch_applied"] is False
                      and reconstruction["full_final_snapshot_used"] is True, "invalid reconstructed source provenance")
            same(reconstruction["producer"], actual_producer, "reconstruction producer differs")
            same(reconstruction["base_inventory"], prep["before"], "baseline source differs from producer input")
            same(reconstruction["final_inventory"], session["after"], "final source differs from captured output")
            same(reconstruction["base_archive"], prep["source_archive"], "base archive binding differs")
            same(reconstruction["final_archive"], session["final_source"], "final archive binding differs")
            h.require(reconstruction["public_test_patch_sha256"] == prep["test_patch"]["sha256"], "test patch binding differs")
            case_costs["reconstruction_ms"] = milliseconds(reconstruction["reconstruction_outer_ms"])
            same(prep["source_archive"]["sha256"], case["source_archive"]["sha256"], "selected base archive differs")
            # Sidecar bytes are independently checked even after workspaces are
            # pruned. Reconciliation does not execute Git to reconstruct again.
            raw = h.bound(prepared / f"case-{index:02d}", session["final_source"], r.MAX_BYTES)
            check_archived_final_source(raw, session["after"])
        baseline = optional_oracle(root, "baseline-oracle", "baseline-capture")
        final = optional_oracle(root, "compatibility-oracle", "compatibility-capture")
        for name, field in (("baseline-oracle", "baseline_oracle_ms"), ("compatibility-oracle", "final_compatibility_oracle_ms")):
            if (root / (name + ".json")).is_file():
                case_costs[field] = milliseconds(hv.read(root / (name + ".json"))["outer_ms"])
        if (root / "baseline-source-after.json").is_file():
            setup = hv.read(root / "baseline-setup.json")
            case_costs["baseline_setup_ms"] = milliseconds(setup["setup_outer_ms"])
            if h.encoded(setup["before"]) != h.encoded(hv.read(root / "baseline-source-after.json")):
                h.require(row["disposition"] == "MATERIAL_CORRECTNESS_STOP" and row["error"] == {
                    "type": "MaterialMismatch", "message": "baseline source changed during independent fresh test"}
                    and final is None, "baseline source drift not retained as a correctness stop")
        actual_classification = r.fresh_classification(actual_producer, baseline, final)
        same(row["classification"], actual_classification, "fresh-fix classification differs from raw evidence")
        if row["disposition"] == "COMPLETE":
            h.require(final is not None and actual_classification["final_fresh_pass"] is True and row["error"] is None,
                      "complete controlled case lacks passing final oracle")
            expected = final["result"]["verdict"]
            actual_blocks = [hv.block(root / ("block-" + str(i)), case["case_id"], i, expected) for i in (0, 1)]
            controlled = {"case_id": case["case_id"], "repo": case["repo"], "disposition": "COMPLETE",
                          "blocks": actual_blocks, "all_blocks_complete": True, "agent_session": False}
            same(hv.read(root / "controlled-completion.json"), controlled, "controlled chain aggregation differs")
            blocks.extend(actual_blocks)
        elif final is not None and actual_classification["final_fresh_pass"]:
            for i in (0, 1):
                if (root / ("block-" + str(i)) / "summary.json").is_file():
                    additional_blocks.append(hv.block(root / ("block-" + str(i)), case["case_id"], i, final["result"]["verdict"]))
        rows.append(row)
        costs.append(case_costs)
    same(rows, result["cases"], "selected case aggregation differs")
    h.require(result["selected_cases"] == len(cases) and result["planned_cases"] == p["planned_cases"]
              and result["complete_controlled_cases"] == sum(row["disposition"] == "COMPLETE" for row in rows)
              and result["completed_verified_fixes"] == sum(row["classification"]["completed_verified_fix"] for row in rows)
              and result["final_fresh_passes"] == sum(row["classification"]["final_fresh_pass"] for row in rows)
              and result["material_correctness_stop"] is material_stop
              and result["inputs_and_sources_unchanged"] is True and result["all_selected_outcomes_retained"] is True
              and result["natural_hit_frequency"] is False, "final companion aggregation/claim differs")
    return {"schema": "zerorun.agent-handoff-reconciliation.v1", "reconciled": True,
            "selected_cases": len(cases), "planned_cases": p["planned_cases"], "phase": p["phase"],
            "producer_completed": sum(row["classification"]["producer_completed"] for row in rows),
            "final_fresh_passes": result["final_fresh_passes"], "completed_verified_fixes": result["completed_verified_fixes"],
            "complete_controlled_cases": result["complete_controlled_cases"], "paired_blocks": len(blocks),
            "block_measurements": [block["measurements"] for block in blocks],
            "additional_complete_blocks_in_incomplete_cases": len(additional_blocks),
            "additional_block_measurements": [block["measurements"] for block in additional_blocks],
            "descriptive_setup_and_oracle_costs": costs,
            "cases": rows, "campaign_error": result["campaign_error"], "material_correctness_stop": material_stop,
            "new_model_calls": 0, "natural_hit_frequency": False, "end_to_end_agent_acceleration": False}


def validate_receipt(path, *, directory=None, prepared=None, image_build=None):
    h.require(prepared is not None, "original producer evidence directory required")
    return validate_saved(directory or Path(path).parent, prepared, image_build)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--prepared", type=Path, required=True)
    p.add_argument("--image-build", type=Path)
    args = p.parse_args()
    print(json.dumps(validate_saved(args.directory, args.prepared, args.image_build), sort_keys=True))


if __name__ == "__main__":
    main()
