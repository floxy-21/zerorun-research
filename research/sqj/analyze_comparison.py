"""Strict arithmetic and provenance checks for the controlled comparison."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import statistics

from research.sqj.analyze_campaign import read_json, require, numeric, digest, validate_record, tex, table
from research.sqj.run_frozen_campaign import WORKLOADS, IMAGE
from research.sqj.run_controlled_comparison import LABELS, ORDERS, selector_comparison, TESTMON_LOCK
from tools.product_generalization_benchmark import _validate_shadow_evidence

ROOT = Path(__file__).resolve().parents[2]
ARMS = ("direct", "snapshot", "fast", "testmon")


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def duration(arm, result):
    value = result["request_wall_ms"] if arm in ("snapshot", "fast") else result["wall_ms"]
    require(numeric(value) > 0, "zero request duration")
    return value


def break_even_hit_fraction(hit_fast, nonhit_fast, hit_direct, nonhit_direct):
    """Descriptive two-state cost model; preserve each state's direct cost."""
    for value in (hit_fast, nonhit_fast, hit_direct, nonhit_direct):
        numeric(value)
    miss_penalty = nonhit_fast - nonhit_direct
    hit_saving = hit_direct - hit_fast
    denominator = miss_penalty + hit_saving
    return miss_penalty / denominator if denominator > 0 else None


def validate_source(identity, archive):
    engine = identity["engine_python_source"]
    rows = engine["files"]
    require(len(rows) == engine["file_count"] and len({r["path"] for r in rows}) == len(rows), "invalid engine file denominator")
    require(canonical_digest({"schema": engine["schema"], "files": rows}) == engine["sha256"], "engine digest is not reproducible")
    for row in rows:
        require(row["path"].startswith("zerorun/") and ".." not in Path(row["path"]).parts, "unsafe source archive path")
        path = archive / row["path"]
        require(digest(path) == row["sha256"] and path.stat().st_size == row["size"], "archived source differs from executed source")
    tool = identity["trial_tool"]
    require(digest(archive / tool["path"]) == tool["sha256"], "archived benchmark helper differs")


def validate_workload(raw, item, directory, archive, *, testmon_available=True):
    name, upstream, frozen, _, targets, extras = item
    require(raw["workload"] == name and raw["upstream"] == upstream and raw["commit"] == frozen and raw["targets"] == list(targets), "workload identity changed")
    require(raw["operator_review_claimed"] is False, "unestablished operator review claimed")
    require(raw["source_stable"] is True and raw["source_before"] == raw["source_after"], "source drift")
    validate_source(raw["source_before"], archive)
    rows = raw["rows"]
    require(len(rows) == 14, "missing request denominator")
    require([(r["trajectory"], r["index"]) for r in rows] == [(t, i) for t in (1, 2) for i in range(7)], "duplicate, missing, or reordered request")
    counts = {"whole_task_exit_agreements": 0, "expected_cache_behaviors": 0, "testmon_complete_reconstructions": 0,
              "testmon_uncovered_node_observations": 0, "testmon_mismatched_node_observations": 0,
              "testmon_selected_nodes": 0, "testmon_prior_passed_omitted_nodes": 0, "oracle_node_observations": 0,
              "optimized_hit_requests": 0, "optimized_nonhit_requests": 0}
    require(testmon_available or name == "more-itertools", "unapproved comparator omission")
    active_arms = ARMS if testmon_available else ARMS[:3]
    active_orders = ORDERS if testmon_available else (ARMS[:3], tuple(reversed(ARMS[:3])))
    timing = {a: [] for a in active_arms}
    hit_timing = {a: [] for a in active_arms}
    miss_timing = {a: [] for a in active_arms}
    trajectory_totals = []
    setup_ms = 0.0
    for trajectory, order in enumerate(active_orders, 1):
        prior = {}
        trajectory_root = directory / f"trajectory-{trajectory}"
        setup = read_json(trajectory_root / "setup.json")
        setup_ms += numeric(setup["environment_bootstrap_ms"])
        manifest = read_json(trajectory_root / "manifest.json")
        require(manifest["version"] == 2 and set(manifest["tasks"]) == {"pytest-fast", "pytest-snapshot"}, "altered comparison manifest")
        require(manifest["tasks"]["pytest-fast"] == manifest["tasks"]["pytest-snapshot"], "unequal ZeroRun task settings")
        task = manifest["tasks"]["pytest-fast"]
        require(task["image"] == IMAGE and task["cacheable"] is True and task["result_only"] is True and task["outputs"] == [] and task["cache_streams"] is False, "reuse contract changed")
        require(task["command"][-len(targets):] == list(targets), "comparison target changed")
        trajectory_rows = [r for r in rows if r["trajectory"] == trajectory]
        source_hash = trajectory_rows[0]["source_file_sha256"]
        require(all(r["source_file_sha256"] == source_hash for r in trajectory_rows if r["index"] not in (3, 4)), "repeat/restoration bytes changed")
        require(trajectory_rows[3]["source_file_sha256"] == trajectory_rows[4]["source_file_sha256"] != source_hash, "failure mutation was not repeated")
        for row in trajectory_rows:
            index = row["index"]
            require(row["label"] == LABELS[index] and row["order"] == list(order) and row["workload"] == name, "request identity changed")
            request = trajectory_root / f"request-{index}"
            require(read_json(request / "observation.json") == row, "summary and raw observation disagree")
            invocation = read_json(request / "invocation.json")
            require(invocation["frozen_sha"] == frozen and invocation["targets"] == list(targets), "request invocation differs")
            oracle_raw = read_json(request / "fresh-outcomes.json")
            oracle = _validate_shadow_evidence(oracle_raw)
            require(oracle == row["oracle"]["capture"] and oracle["complete_per_node_outcomes"], "full fresh capture invalid")
            require(oracle["exit_code"] == row["oracle"]["exit_code"], "fresh process/capture exit disagreement")
            numeric(row["oracle"]["wall_ms"])
            counts["oracle_node_observations"] += len(oracle["nodeids"])
            require(set(row["arms"]) == set(active_arms), "missing comparator arm")
            for arm in active_arms:
                result = row["arms"][arm]
                require(read_json(request / (arm + ".json")) == result, "arm receipt differs from summary")
                require(type(result["exit_code"]) is int, "invalid process exit")
                elapsed = duration(arm, result)
                timing[arm].append(elapsed)
                (miss_timing if index in (0, 3, 4) else hit_timing)[arm].append(elapsed)
            expected_exit = 1 if index in (3, 4) else 0
            agreement = all(row["arms"][a]["exit_code"] == oracle["exit_code"] == expected_exit for a in ("direct", "snapshot", "fast"))
            expected_status = "MISS_FAILED" if index in (3, 4) else "MISS_EXECUTED" if index == 0 else "HIT_REUSED"
            behavior = all(row["arms"][a]["status"] == expected_status for a in ("snapshot", "fast"))
            require(row["whole_task_exit_agreement"] is agreement and row["cache_behavior_expected"] is behavior, "claimed agreement not reproducible")
            counts["whole_task_exit_agreements"] += agreement
            counts["expected_cache_behaviors"] += behavior
            if row["arms"]["fast"]["status"] == "HIT_REUSED":
                counts["optimized_hit_requests"] += 1
            else:
                counts["optimized_nonhit_requests"] += 1
            if row["arms"]["fast"]["status"] == "HIT_REUSED":
                require(row["arms"]["fast"]["phase_ms"]["snapshot_prepare"] == 0 and "readonly_hit_path" in row["arms"]["fast"]["phase_ms"], "optimized hit unexpectedly used a snapshot")
            if not testmon_available:
                require(row["testmon_comparison"] == {"unavailable": True, "reason": "separate whole-task block after recorded 600-second Testmon timeout"}, "unavailable comparator mislabeled")
                require(not (request / "testmon.json").exists(), "unexpected Testmon result in omitted-comparator block")
                continue
            testmon_raw = read_json(request / "testmon-outcomes.json")
            selected = _validate_shadow_evidence(testmon_raw)
            require(selected == row["arms"]["testmon"]["capture"], "Testmon selected capture changed")
            require(row["arms"]["testmon"]["cleanup_confirmed"] is True, "Testmon cleanup unconfirmed")
            require(row["arms"]["testmon"]["exit_code"] in (0, 1), "Testmon execution failed before valid comparison")
            comparison, prior = selector_comparison(selected, prior, oracle)
            require(comparison == row["testmon_comparison"], "Testmon reconstruction is not reproducible")
            counts["testmon_complete_reconstructions"] += comparison["complete_reconstruction_matches"]
            counts["testmon_uncovered_node_observations"] += len(comparison["uncovered_node_ids"])
            counts["testmon_mismatched_node_observations"] += len(comparison["mismatched_or_absent_node_ids"])
            counts["testmon_selected_nodes"] += comparison["selected_nodes"]
            counts["testmon_prior_passed_omitted_nodes"] += comparison["prior_passed_omitted_nodes"]
        trajectory_totals.append({a: sum(duration(a, row["arms"][a]) for row in trajectory_rows) for a in active_arms})
    environment = raw["environment"]
    common_setup = numeric(environment["build_ms"]) + setup_ms
    require(environment["provenance"]["runtime_image"] == IMAGE and environment["provenance"]["extra_requirements"] == list(extras), "environment identity changed")
    expected_challenges = counts["whole_task_exit_agreements"] == 14 and counts["expected_cache_behaviors"] == 14
    require(raw["whole_task_challenges_pass"] is expected_challenges, "challenge pass flag contradicts data")
    totals = {a: sum(v) for a, v in timing.items()}
    mean_hit = {a: statistics.mean(v) for a, v in hit_timing.items()}
    mean_miss = {a: statistics.mean(v) for a, v in miss_timing.items()}
    for values in (totals, mean_hit, mean_miss):
        if not testmon_available:
            values["testmon"] = None
    # A descriptive algebraic sensitivity, not an estimated agent repeat rate.
    h, m = mean_hit["fast"], mean_miss["fast"]
    crossover = break_even_hit_fraction(h, m, mean_hit["direct"], mean_miss["direct"])
    return {"workload": name, "frozen_sha": frozen, "targets": list(targets), "requests": 14, **counts,
            "testmon_requests": 14 if testmon_available else 0, "timed_arm_invocations": 14 * len(active_arms),
            "totals_ms": totals, "trajectory_totals_ms": trajectory_totals, "common_setup_ms": common_setup,
            "oracle_validation_ms": sum(r["oracle"]["wall_ms"] for r in rows),
            "hit_mean_ms": mean_hit, "hit_median_ms": {a: statistics.median(v) for a, v in hit_timing.items()},
            "nonhit_mean_ms": mean_miss, "request_speedup": totals["direct"] / totals["fast"],
            "direct_request_min_ms": min(timing["direct"]), "direct_request_max_ms": max(timing["direct"]),
            "snapshot_nonhit_min_ms": min(miss_timing["snapshot"]), "snapshot_nonhit_max_ms": max(miss_timing["snapshot"]),
            "common_setup_inclusive_speedup": (totals["direct"] + common_setup) / (totals["fast"] + common_setup),
            "warm_direct_to_fast_ratio": mean_hit["direct"] / mean_hit["fast"],
            "warm_snapshot_to_fast_ratio": mean_hit["snapshot"] / mean_hit["fast"],
            "warm_testmon_to_fast_ratio": mean_hit["testmon"] / mean_hit["fast"] if testmon_available else None,
            "warm_snapshot_latency_reduction_percent": 100 * (1 - mean_hit["fast"] / mean_hit["snapshot"]),
            "modeled_hit_fraction_crossover": crossover,
            "crossover_model": "state-conditional-direct-and-optimized-affine-cost.v1",
            "whole_task_challenges_pass": expected_challenges, "source_sha256": raw["source_before"]["engine_python_source"]["sha256"]}


def analyze(directory, archive):
    protocol = read_json(directory / "protocol.json")
    require(protocol["workloads"] == [list((w[0], w[1], w[2], w[3], list(w[4]), list(w[5]))) for w in WORKLOADS], "missing/changed workload cohort")
    require(protocol["labels"] == list(LABELS) and protocol["orders"] == [list(o) for o in ORDERS], "changed sequence/order")
    require(protocol["runtime_image"] == IMAGE and protocol["operator_authority_allowed"] is False, "runtime/authority contract changed")
    require(protocol["producer_sha256"] == digest(ROOT / "research/sqj/producers/controlled_comparison-v3.py"), "original comparison producer differs from recorded bytes")
    campaign = read_json(directory / "campaign-summary.json")
    require(campaign["completed"] is False and campaign["testmon_runtime_unchanged"] is True and campaign["operator_authority_receipts_created"] is False, "original timeout campaign/runtime identity changed")
    require([r["workload"] for r in campaign["results"]] == [w[0] for w in WORKLOADS] and all(r["completed"] is True for r in campaign["results"][:4]), "missing original completed subject")
    failed = campaign["results"][4]
    require(failed["completed"] is False and failed["error_type"] == "ConfigurationError" and "timed out after 600s" in failed["error"], "original Testmon timeout was not preserved")
    testmon_setup = read_json(directory / "testmon-setup.json")
    require(testmon_setup["exit_code"] == 0 and testmon_setup["requirements_sha256"] == hashlib.sha256(TESTMON_LOCK.encode()).hexdigest(), "wrong Testmon setup")
    corrected = directory.parent / "comparison-packaging-final"
    correction_protocol = read_json(corrected / "protocol.json")
    correction_summary = read_json(corrected / "campaign-summary.json")
    correction_setup = read_json(corrected / "testmon-setup.json")
    require(correction_setup["exit_code"] == 0 and correction_setup["requirements_sha256"] == hashlib.sha256(TESTMON_LOCK.encode()).hexdigest(), "wrong corrective Testmon setup")
    recovery_producer = digest(ROOT / "research/sqj/producers/controlled_comparison-recovery.py")
    require(correction_protocol["producer_sha256"] == recovery_producer, "corrective producer differs")
    require(correction_protocol["packaging_stock_plugin_warning_only_ignored"] is True and correction_protocol["testmon_omitted_after_recorded_timeout"] is False, "wrong packaging recovery policy")
    require(correction_protocol["testmon_project_compatibility"] is True and correction_protocol["labels"] == list(LABELS) and correction_protocol["orders"] == [list(o) for o in ORDERS], "undocumented correction settings")
    require(correction_protocol["workloads"] == [protocol["workloads"][0]], "corrective subject changed")
    require(correction_summary["completed"] is True and correction_summary["testmon_runtime_unchanged"] is True and correction_summary["operator_authority_receipts_created"] is False, "correction incomplete")
    intermediate = directory.parent / "comparison-packaging-corrected"
    intermediate_protocol = read_json(intermediate / "protocol.json")
    intermediate_raw = read_json(intermediate / "packaging/summary.json")
    require(intermediate_protocol["producer_sha256"] == digest(ROOT / "research/sqj/run_controlled_comparison.py") and len(intermediate_raw["rows"]) == 14, "intermediate packaging warning failure missing")
    require(all("capture" not in r["arms"]["testmon"] for r in intermediate_raw["rows"]), "intermediate failure classification changed")
    more = directory.parent / "comparison-more-whole-task"
    more_protocol = read_json(more / "protocol.json")
    more_summary = read_json(more / "campaign-summary.json")
    require(more_protocol["producer_sha256"] == recovery_producer and more_protocol["testmon_omitted_after_recorded_timeout"] is True, "missing documented more-itertools recovery")
    require(more_protocol["workloads"] == [protocol["workloads"][4]] and more_protocol["labels"] == list(LABELS) and more_protocol["orders"] == [list(ARMS[:3]), list(reversed(ARMS[:3]))], "recovery changed source cohort/sequence/relative orders")
    require(more_protocol["runtime_image"] == IMAGE and more_protocol["operator_authority_allowed"] is False, "recovery changed runtime or authority")
    require(more_summary["completed"] is True and more_summary["testmon_runtime_unchanged"] is True and more_summary["operator_authority_receipts_created"] is False, "whole-task recovery incomplete")
    rows = []
    for item in WORKLOADS:
        chosen = corrected if item[0] == "packaging" else more if item[0] == "more-itertools" else directory
        row = validate_workload(read_json(chosen / item[0] / "summary.json"), item, chosen / item[0], archive, testmon_available=item[0] != "more-itertools")
        row["evidence_directory"] = chosen.name
        rows.append(row)
    original_packaging = read_json(directory / "packaging/summary.json")
    require(len(original_packaging["rows"]) == 14, "original packaging attempt was not preserved")
    original_packaging_errors = sum(r["arms"]["testmon"]["exit_code"] not in (0, 1) for r in original_packaging["rows"])
    require(original_packaging_errors > 0, "correction lacks its recorded infrastructure failure")
    require(len({r["source_sha256"] for r in rows}) == 1, "different engines between subjects")
    return {"schema": "zerorun.controlled-comparison-analysis.v1", "rows": rows,
            "source_sha256": rows[0]["source_sha256"], "producer_sha256": protocol["producer_sha256"],
            "raw_campaign_sha256": digest(directory / "campaign-summary.json"),
            "testmon_shared_install_ms": numeric(testmon_setup["wall_ms"]),
            "packaging_correction": {"original_infrastructure_error_requests": original_packaging_errors,
                "original_attempt_sha256": digest(directory / "packaging/summary.json"),
                "corrected_campaign_sha256": digest(corrected / "campaign-summary.json"),
                "corrected_producer_sha256": correction_protocol["producer_sha256"],
                "additional_testmon_install_ms": numeric(correction_setup["wall_ms"]),
                "intermediate_warning_failure_sha256": digest(intermediate / "packaging/summary.json"),
                "reason": "stock legacy-path plugin, its exact rewrite-warning filter, and documented selection with existing marker filter",
                "all_four_arms_rerun": True, "chosen_by_performance": False},
            "more_itertools_testmon_unavailable": {"error": failed["error"], "original_failure_sha256": digest(directory / "more-itertools-failure.json"),
                "whole_task_recovery_sha256": digest(more / "campaign-summary.json"), "timeout_seconds": 600, "ordinary_helper_timeout_seconds": 900,
                "not_a_matched_timeout_comparison": True, "testmon_ratio_reported": False},
            "total_requests": sum(r["requests"] for r in rows),
            "total_testmon_comparative_requests": sum(r["testmon_requests"] for r in rows),
            "timed_arm_invocations": sum(r["timed_arm_invocations"] for r in rows),
            "all_whole_task_challenges_pass": all(r["whole_task_challenges_pass"] for r in rows),
            "population_inference": False, "agent_effect_measured": False,
            "release_5x_50pct_gate_test": False}


def historical_partial(directory):
    """Retain four completed results AND the failed fifth, without certifying it."""
    campaign = read_json(directory / "campaign-processes.json")
    require(campaign["selected_workloads"] == [w[0] for w in WORKLOADS], "historical cohort changed")
    results = []
    for item, process in zip(WORKLOADS, campaign["rows"], strict=True):
        require(process["workload"] == item[0], "historical process identity changed")
        source = directory / (item[0] + ".json")
        require(digest(source) == process["raw_receipt_sha256"], "historical raw receipt changed")
        raw = read_json(source)
        if process.get("benchmark_error"):
            results.append({"workload": item[0], "completed": False, "benchmark_error": process["benchmark_error"], "partial_rows_retained": len(raw.get("rows", [])), "raw_sha256": digest(source)})
        else:
            results.append({**validate_record(raw, item), "completed": True, "raw_sha256": digest(source)})
    require(len(results) == 5, "missing historical subjects")
    return {"complete_campaign": False, "rows": results, "not_pooled_with_controlled_requests": True}


def numerical_tables(summary):
    rows = summary["rows"]
    def number(value, *, scale=1, places=2):
        return "---" if value is None else f"{value/scale:.{places}f}"
    return "\n\n".join([
        table("Complete seven-request sequence, seconds summed across both separately initialized trajectories. Every seed, failure, and restoration is retained; shared setup and fresh-oracle validation are separate.", "tab:sequence", "lrrrrr", "Library & Direct & Snapshot & Optimized & Testmon & Direct/fast",
              [f"{tex(r['workload'])} & " + " & ".join(number(r['totals_ms'][a], scale=1000) for a in ARMS) + f" & {r['request_speedup']:.2f}" for r in rows]),
        table("Conditional warm requests only (eight per library). Ratios compare mean latency; these are not whole-sequence or product-release speedups.", "tab:warm", "lrrrr", "Library & Fast (ms) & Direct/fast & Snapshot/fast & Testmon/fast",
              [f"{tex(r['workload'])} & {r['hit_mean_ms']['fast']:.0f} & {r['warm_direct_to_fast_ratio']:.2f} & {r['warm_snapshot_to_fast_ratio']:.2f} & {number(r['warm_testmon_to_fast_ratio'])}" for r in rows]),
        table("Fresh checks and selected-outcome accounting. An uncovered node is not a proven wrong result; it means the stated prior-PASS reconstruction does not cover the full fresh result.", "tab:agreement", "lrrrr", r"Library & \shortstack{Whole-task\\checks} & \shortstack{Testmon full\\matches} & Uncovered & Mismatched",
              [f"{tex(r['workload'])} & {r['whole_task_exit_agreements']}/14 & " + (f"{r['testmon_complete_reconstructions']}/14 & {r['testmon_uncovered_node_observations']} & {r['testmon_mismatched_node_observations']}" if r['testmon_requests'] else "--- & --- & ---") for r in rows]),
        table("Explicit setup and validation cost. Testmon's common installation is additionally reported once in the text. No oracle cost is hidden inside the direct timing baseline.", "tab:setup", "lrrr", r"Library & \shortstack{Common\\setup (s)} & \shortstack{Fresh\\validation (s)} & \shortstack{Setup-inclusive\\direct/fast}",
              [f"{tex(r['workload'])} & {r['common_setup_ms']/1000:.2f} & {r['oracle_validation_ms']/1000:.2f} & {r['common_setup_inclusive_speedup']:.2f}" for r in rows]),
        table("Order sensitivity: complete direct/fast ratio within each seven-request trajectory, and descriptive modeled break-even hit fraction. The second trajectory reverses the available arm order (Testmon first for four libraries; optimized first for more-itertools). Values are not population estimates.", "tab:order", "lrrr", r"Library & Direct-first & Reversed order & \shortstack{Modeled\\break-even}",
              [f"{tex(r['workload'])} & {r['trajectory_totals_ms'][0]['direct']/r['trajectory_totals_ms'][0]['fast']:.2f} & {r['trajectory_totals_ms'][1]['direct']/r['trajectory_totals_ms'][1]['fast']:.2f} & " + (f"{100*r['modeled_hit_fraction_crossover']:.1f}\\%" if r['modeled_hit_fraction_crossover'] is not None else "undefined") for r in rows]),
    ])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "research/sqj/evidence/comparison-final-1")
    parser.add_argument("--archive", type=Path, default=ROOT / "research/sqj/source-final")
    parser.add_argument("--output", type=Path, default=ROOT / "research/sqj/generated")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    summary = analyze(args.input, args.archive)
    summary["historical_campaign"] = historical_partial(ROOT / "research/sqj/evidence/campaign-1")
    output = {"comparison-summary.json": json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n", "comparison-tables.tex": numerical_tables(summary) + "\n"}
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    fields = ["workload", "requests", "request_speedup", "common_setup_inclusive_speedup", "warm_direct_to_fast_ratio", "warm_snapshot_to_fast_ratio", "warm_testmon_to_fast_ratio", "modeled_hit_fraction_crossover"]
    writer.writerow(fields)
    writer.writerows([[r[k] for k in fields] for r in summary["rows"]])
    output["comparison-results.csv"] = stream.getvalue()
    if args.check:
        for name, content in output.items():
            require((args.output / name).read_bytes() == content.encode(), "generated result differs: " + name)
    else:
        args.output.mkdir(parents=True, exist_ok=True)
        for name, content in output.items():
            (args.output / name).write_bytes(content.encode())
    print(json.dumps({"validated_requests": summary["total_requests"], "whole_task_challenges_pass": summary["all_whole_task_challenges_pass"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
