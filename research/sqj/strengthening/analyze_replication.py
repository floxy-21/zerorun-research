"""Independent, fail-closed reconciliation of randomized short-subject evidence.

No experiments execute here. Incomplete/failed blocks remain visible, and no
headline performance estimate is emitted from a favorable complete subset.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
from itertools import permutations
import json
from pathlib import Path, PurePosixPath
import re
import statistics

from research.sqj.analyze_campaign import read_json, require, numeric, digest
from research.sqj.analyze_comparison import validate_source
from research.sqj.run_frozen_campaign import WORKLOADS, IMAGE
from tools.product_generalization_benchmark import _validate_shadow_evidence


SQJ = Path(__file__).resolve().parents[1]
SEED = "zerorun-short-replication-v1"
ARMS = ("direct", "snapshot", "fast")
COHORT = ("packaging", "pycparser", "colorama", "click")
LABELS = ("seed", "repeat-1", "repeat-2", "failing-test-added", "failure-repeated", "restored", "repeat-restored")
DRIVER_SHA256 = "eaa420f858dc07349a42ae6be36aba795ba9d57387d5be795e6e3552f5ca0a4a"
RECOVERY_SHA256 = "2fb6b23f5bae6e2f1f2f8681bc9f492be704f43d56f69c20c6c17ad95d9aa2d9"
RESOURCE_ARGS = ["--cpus", "2", "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "512"]
PHASE_ORDER = ["container ls", "create", "container inspect", "start", "container inspect", "container rm", "container inspect"]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def same(actual, expected, message):
    require(canonical(actual) == canonical(expected), message)


def expected_schedule():
    def rank(namespace, values):
        return hashlib.sha256("\0".join((SEED, namespace, *values)).encode()).hexdigest()
    return [{"workload": name, "trajectories": [
        {"trajectory": i, "order": list(order)} for i, order in enumerate(
            sorted(permutations(ARMS), key=lambda order: rank("arms:" + name, order)), 1)]}
        for name in sorted(COHORT, key=lambda name: rank("workload", (name,)))]


def expected_state(index):
    return {"index": index, "label": LABELS[index], "expected_exit_code": int(index in (3, 4)),
            "expected_cache_status": "MISS_FAILED" if index in (3, 4) else "MISS_EXECUTED" if index == 0 else "HIT_REUSED",
            "source_state": "added-failure" if index in (3, 4) else "original"}


def hash_value(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), label)
    return value


def safe_source_path(root, name):
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in name, "unsafe source identity path")
    path = root.joinpath(*relative.parts)
    require(path.is_file() and not path.is_symlink(), "missing/link-like archived source: " + name)
    return path


def validate_protected(identity, engine_archive, sqj):
    rows = identity["files"]
    require(isinstance(rows, list) and rows, "empty protected source inventory")
    require(len({row["path"] for row in rows}) == len(rows), "duplicate protected source")
    same(hashlib.sha256(canonical(rows).encode()).hexdigest(), identity["sha256"], "protected source aggregate digest mismatch")
    expected = {"engine/" + path.relative_to(engine_archive).as_posix()
                for path in (engine_archive / "zerorun").rglob("*.py")}
    expected.add("engine/tools/product_generalization_benchmark.py")
    expected.update("sqj/producers/" + path.name for path in (sqj / "producers").glob("*.py"))
    expected.update(("sqj/run_controlled_comparison.py", "sqj/run_frozen_campaign.py"))
    require({row["path"] for row in rows} == expected, "protected source inventory is incomplete or expanded")
    for row in rows:
        prefix, relative = row["path"].split("/", 1)
        path = safe_source_path(engine_archive if prefix == "engine" else sqj, relative)
        same(digest(path), row["sha256"], "protected source bytes differ: " + row["path"])
        same(path.stat().st_size, row["bytes"], "protected source size differs")


def validate_protocol(protocol, engine_archive, sqj=SQJ):
    require(protocol["schema"] == "zerorun.randomized-short-replication.v1", "wrong replication schema")
    same(protocol["seed"], SEED, "changed seed")
    same(protocol["schedule"], expected_schedule(), "changed/duplicated/unbalanced frozen schedule")
    same(protocol["request_states"], [expected_state(i) for i in range(7)], "changed seven-request states")
    selected = {item[0]: item for item in WORKLOADS if item[0] in COHORT}
    same(protocol["workloads"], [selected[row["workload"]] for row in expected_schedule()], "changed cohort or frozen target")
    same(protocol["runtime_image"], IMAGE, "changed pinned image")
    same(protocol["docker_resource_args"], RESOURCE_ARGS, "changed resource limits")
    for name, expected in {"execution_timeout_seconds": 900, "expected_trajectories": 24,
        "expected_requests": 168, "expected_timed_arm_invocations": 504, "expected_fresh_oracles": 168,
        "expected_hit_requests_per_cache_arm": 96, "expected_nonhit_requests_per_cache_arm": 72,
        "warmups_removed": 0, "operator_authority_allowed": False, "operator_review_claimed": False,
        "cache_authentication_private_fixture_only": True, "testmon_in_this_replication": False}.items():
        same(protocol[name], expected, "changed protocol policy: " + name)
    same(protocol["producer_sha256"], DRIVER_SHA256, "unrecognized replication producer")
    same(digest(sqj / "strengthening/randomized_replication.py"), DRIVER_SHA256, "local replication producer bytes differ")
    same(protocol["frozen_recovery_producer_sha256"], RECOVERY_SHA256, "unrecognized frozen recovery helper")
    same(digest(sqj / "producers/controlled_comparison-recovery.py"), RECOVERY_SHA256, "frozen recovery helper bytes differ")
    require("original five-subject evidence retained" in protocol["more_itertools"], "long subject omission boundary missing")
    same(protocol["engine_identity"]["schema"], "zerorun.product-generalization-source-identity.v1", "source identity schema changed")
    same(protocol["engine_identity"]["engine_python_source"]["schema"], "zerorun.engine-python-source-identity.v1", "engine hash schema changed")
    validate_source(protocol["engine_identity"], engine_archive)
    validate_protected(protocol["protected_source_identity"], engine_archive, sqj)


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    require(parsed.tzinfo is not None, "naive measurement timestamp")
    return parsed


def validate_timing(receipt, arm):
    require(receipt["completed"] is True, "incomplete arm used as a completed request")
    outer = numeric(receipt["request_wall_ms"])
    require(outer > 0, "nonpositive outer request duration")
    require(type(receipt["exit_code"]) is int, "invalid process exit type")
    start, end = timestamp(receipt["started_utc"]), timestamp(receipt["ended_utc"])
    require(end >= start, "backward request timestamp")
    for field in ("host_before", "host_after"):
        require(isinstance(receipt[field], dict) and "utc" in receipt[field], "missing host telemetry")
        timestamp(receipt[field]["utc"])
    require(numeric(receipt["wall_ms"]) <= outer + 0.1, "helper duration exceeds complete outer duration")
    phases = receipt["docker_subprocess_phases"]
    require(isinstance(phases, list), "missing lifecycle phase list")
    total = 0.0
    by_phase = defaultdict(float)
    previous = start
    if arm in ("direct", "oracle"):
        same([phase["phase"] for phase in phases], PHASE_ORDER, "incomplete/changed Docker lifecycle phases")
        for phase in phases:
            begin, finish = timestamp(phase["started_utc"]), timestamp(phase["ended_utc"])
            require(start <= begin <= finish <= end and previous <= begin, "phase intervals overlap or escape request")
            previous = finish
            duration = numeric(phase["wall_ms"])
            total += duration
            by_phase[phase["phase"]] += duration
            same(phase["timeout_seconds"], 900 if phase["phase"] == "start" else 60, "phase timeout differs")
            require(type(phase["exit_code"]) is int and "error_type" not in phase, "failed subprocess hidden in successful arm")
        require(total <= outer + 0.1, "lifecycle phase sum exceeds outer request clock")
        same(phases[3]["exit_code"], receipt["exit_code"], "docker start and pytest exit differ")
        require(all(phases[i]["exit_code"] == 0 for i in (0, 1, 2, 4, 5)) and phases[-1]["exit_code"] != 0,
                "Docker lifecycle did not attest create/execute/cleanup")
    else:
        same(phases, [], "unexpected direct lifecycle telemetry on ZeroRun arm")
        require(isinstance(receipt["phase_ms"], dict) and receipt["phase_ms"], "missing product phase timings")
        for value in receipt["phase_ms"].values():
            numeric(value)
    return {"outer_ms": outer, "subprocess_sum_ms": total,
            "outside_subprocess_ms": outer - total if arm in ("direct", "oracle") else None,
            "subprocess_by_phase_ms": dict(by_phase)}


def validate_manifest(manifest, name, trajectory, targets, sqj=SQJ):
    names = {arm: f"replication-{name}-{trajectory}-{arm}" for arm in ARMS[1:]}
    same(manifest["version"], 2, "manifest version changed")
    require(set(manifest["tasks"]) == set(names.values()), "cache namespaces changed/shared")
    snapshot, fast = (manifest["tasks"][names[arm]] for arm in ARMS[1:])
    same(snapshot, fast, "unequal ZeroRun task settings")
    for key, value in {"image": IMAGE, "cacheable": True, "result_only": True, "outputs": [],
        "cache_streams": False, "env": [], "unsafe_effects": [], "closure_reviewed": True, "platform": "linux/amd64"}.items():
        same(fast[key], value, "changed cache execution contract: " + key)
    same(fast["command"], ["sh", ".zerorun-env/bin/python", "-m", "pytest", "-p", "no:cacheprovider", *targets], "changed execution command")
    require(fast["inputs"] == sorted(set(fast["inputs"])) and ".zerorun-env" in fast["inputs"], "invalid input boundary")
    original = read_json(sqj / f"evidence/comparison-final-1/{name}/trajectory-1/manifest.json")
    same(fast["inputs"], original["tasks"]["pytest-fast"]["inputs"], "input boundary differs from original frozen subject")


def validate_observation(row, request, item, block, seed_hash, seed_capture):
    name, upstream, commit, classification, targets, extras = item
    index = row["index"]
    require(type(index) is int and 0 <= index < 7, "bad request index")
    same({key: row[key] for key in ("workload", "trajectory", "order", "label")},
         {"workload": name, "trajectory": block["trajectory"], "order": block["order"], "label": LABELS[index]}, "request identity/order changed")
    hash_value(row["source_file_sha256"], "invalid source-state hash")
    expected = expected_state(index)
    same(sorted(path.name for path in request.glob("*.json")), sorted([
        "invocation.json", "observation.json", "direct.json", "snapshot.json", "fast.json", "fresh.json", "fresh-outcomes.json"]),
        "unexpected or missing request JSON receipt")
    invocation = read_json(request / "invocation.json")
    same(invocation, {**{key: row[key] for key in ("workload", "trajectory", "index", "label", "order", "source_file_sha256")},
         "arms": {}, "frozen_sha": commit, "targets": targets, "expected": expected}, "invocation differs from planned/raw request")
    require(set(row["arms"]) == set(ARMS), "missing or extra timing arm")
    timings = {}
    for arm in ARMS:
        same(read_json(request / (arm + ".json")), row["arms"][arm], "arm receipt disagrees with observation")
        timings[arm] = validate_timing(row["arms"][arm], arm)
    same(read_json(request / "fresh.json"), row["oracle"], "fresh receipt differs from observation")
    timings["oracle"] = validate_timing(row["oracle"], "oracle")
    capture = _validate_shadow_evidence(read_json(request / "fresh-outcomes.json"))
    same(capture, row["oracle"]["capture"], "fresh capture differs from normalized oracle")
    require(capture["complete_per_node_outcomes"], "fresh oracle incomplete")
    same(capture["exit_code"], row["oracle"]["exit_code"], "oracle process/capture exit disagree")
    agreement = all(row["arms"][arm]["exit_code"] == capture["exit_code"] == expected["expected_exit_code"] for arm in ARMS)
    behavior = all(row["arms"][arm]["status"] == expected["expected_cache_status"] for arm in ARMS[1:])
    same(row["whole_task_exit_agreement"], agreement, "claimed fresh agreement is false")
    same(row["cache_behavior_expected"], behavior, "claimed cache behavior is false")
    if row["arms"]["fast"]["status"] == "HIT_REUSED":
        require(numeric(row["arms"]["fast"]["phase_ms"]["snapshot_prepare"]) == 0, "optimized hit unexpectedly builds a snapshot")
        require("readonly_hit_path" in row["arms"]["fast"]["phase_ms"], "optimized hit lacks readonly-path evidence")
    ordered = [row["arms"][arm] for arm in block["order"]] + [row["oracle"]]
    require(all(timestamp(left["ended_utc"]) <= timestamp(right["started_utc"])
                for left, right in zip(ordered, ordered[1:])), "measured arm chronology contradicts scheduled order")
    if index not in (3, 4):
        same(row["source_file_sha256"], seed_hash, "repeat/restoration source differs from seed")
    else:
        require(row["source_file_sha256"] != seed_hash, "failure request did not change source")
    if agreement:
        require(seed_capture["nodeids"], "fresh seed collection is empty")
        expected_failure = targets[0] + "::test_zerorun_controlled_added_failure"
        same(capture["failing_nodeids"], [expected_failure] if index in (3, 4) else [], "unexpected failing nodes")
        expected_nodes = set(seed_capture["nodeids"]) | ({expected_failure} if index in (3, 4) else set())
        require(set(capture["nodeids"]) == expected_nodes, "fresh collected node set changed unexpectedly")
    return {"index": index, "agreement": agreement, "behavior": behavior, "timings": timings,
            "fresh_nodes": len(capture["nodeids"]), "optimized_hit": row["arms"]["fast"]["status"] == "HIT_REUSED"}


def spread(values):
    return {"n": len(values), "minimum": min(values), "median": statistics.median(values),
            "maximum": max(values), "mean": statistics.mean(values),
            "sample_standard_deviation": statistics.stdev(values) if len(values) > 1 else None} if values else None


def block_statistics(rows, validated, block):
    totals = {arm: sum(result["timings"][arm]["outer_ms"] for result in validated) for arm in (*ARMS, "oracle")}
    hit = {arm: [row["arms"][arm]["request_wall_ms"] for row in rows if row["index"] in (1, 2, 5, 6)] for arm in ARMS}
    phases = {arm: defaultdict(float) for arm in ("direct", "oracle")}
    product = {arm: defaultdict(float) for arm in ARMS[1:]}
    for result, row in zip(validated, rows, strict=True):
        for arm in phases:
            for name, elapsed in result["timings"][arm]["subprocess_by_phase_ms"].items():
                phases[arm][name] += elapsed
        for arm in product:
            for name, elapsed in row["arms"][arm]["phase_ms"].items():
                product[arm][name] += elapsed
    return {**block, "requests_observed": len(rows), "observed_totals_ms": totals,
        "direct_to_fast_ratio": totals["direct"] / totals["fast"] if len(rows) == 7 else None,
        "snapshot_to_fast_ratio": totals["snapshot"] / totals["fast"] if len(rows) == 7 else None,
        "warm_hit_median_ms": {arm: statistics.median(values) if values else None for arm, values in hit.items()},
        "subprocess_phase_totals_ms": {arm: dict(values) for arm, values in phases.items()},
        "product_phase_totals_ms_not_additive": {arm: dict(values) for arm, values in product.items()},
        "product_phase_note": "Nested product aggregates are not a disjoint wall-time partition."}


def inspect_workload(directory, item, scheduled, protocol, sqj=SQJ):
    name, upstream, commit, classification, targets, extras = item
    summary_path = directory / "summary.json"
    raw = read_json(summary_path) if summary_path.exists() else None
    missing, errors, observed_rows, blocks, receipts = [], [], [], [], []
    planned_names = {f"trajectory-{i}" for i in range(1, 7)}
    require({path.name for path in directory.glob("trajectory-*")} <= planned_names, "unexpected trajectory directory")
    previous_completed = True
    previous_oracle_end = timestamp(protocol["frozen_utc"])
    for block in scheduled["trajectories"]:
        destination = directory / f"trajectory-{block['trajectory']}"
        if not destination.exists():
            missing.append(destination.name)
            previous_completed = False
            continue
        require(previous_completed, "later block exists after failed/missing block")
        same(read_json(destination / "block-plan.json"), block, "block plan differs from frozen schedule")
        block_receipt = read_json(destination / "block-summary.json") if (destination / "block-summary.json").exists() else None
        existing = {path.name for path in destination.glob("request-*")}
        require(existing <= {f"request-{i}" for i in range(7)}, "unexpected request directory")
        indices = [i for i in range(7) if f"request-{i}" in existing]
        same(indices, list(range(len(indices))), "missing/reordered request prefix")
        rows, validated, partial, partial_receipts = [], [], [], []
        seed_hash = seed_capture = None
        setup_ms = None
        if (destination / "manifest.json").exists():
            validate_manifest(read_json(destination / "manifest.json"), name, block["trajectory"], targets, sqj)
            setup = read_json(destination / "setup.json")
            for key, value in {"isolated_result_cache": True, "shared_hardlinks": False,
                               "private_byte_copy": True, "read_only_mode_and_content_verified": True}.items():
                same(setup[key], value, "changed environment materialization contract: " + key)
            setup_ms = numeric(setup["environment_bootstrap_ms"])
        elif indices:
            raise ValueError("requests have no manifest")
        for index in indices:
            request = destination / f"request-{index}"
            if not (request / "observation.json").exists():
                require(index == indices[-1], "later requests follow an incomplete request")
                partial.append(request.name)
                for arm in (*ARMS, "fresh"):
                    if (request / (arm + ".json")).exists():
                        receipt_path = request / (arm + ".json")
                        receipt = read_json(receipt_path)
                        require(type(receipt.get("completed")) is bool, "partial arm lacks completion status")
                        if receipt["completed"]:
                            validate_timing(receipt, "oracle" if arm == "fresh" else arm)
                        else:
                            numeric(receipt["request_wall_ms"])
                        partial_receipts.append({"path": str(receipt_path), "sha256": digest(receipt_path),
                            "arm": "oracle" if arm == "fresh" else arm, "completed": receipt["completed"],
                            "request_wall_ms": receipt["request_wall_ms"], "error_type": receipt.get("error_type")})
                        if receipt.get("completed") is False:
                            errors.append({"path": str(receipt_path), "receipt": receipt})
                continue
            row = read_json(request / "observation.json")
            same(row["index"], index, "observation directory/index mismatch")
            if index == 0:
                seed_hash = row["source_file_sha256"]
                seed_capture = _validate_shadow_evidence(read_json(request / "fresh-outcomes.json"))
            checked = validate_observation(row, request, item, block, seed_hash, seed_capture)
            require(previous_oracle_end <= timestamp(row["arms"][block["order"][0]]["started_utc"]),
                    "request chronology precedes frozen protocol or preceding request")
            previous_oracle_end = timestamp(row["oracle"]["ended_utc"])
            rows.append(row)
            validated.append(checked)
        if len(rows) > 4:
            same(rows[3]["source_file_sha256"], rows[4]["source_file_sha256"], "repeated failure bytes differ")
        if (destination / "restoration.json").exists():
            restored = read_json(destination / "restoration.json")
            require(restored["restored"] is True and restored["source_sha256"] == restored["original_sha256"], "source was not restored")
            if seed_hash:
                same(restored["original_sha256"], seed_hash, "restoration does not match seed")
        elif block_receipt is not None and block_receipt.get("completed") is True:
            raise ValueError("completed block lacks restoration proof")
        complete = len(rows) == 7 and all(row["agreement"] and row["behavior"] for row in validated) and block_receipt is not None and block_receipt.get("completed") is True
        if block_receipt:
            same({key: block_receipt[key] for key in ("trajectory", "order")}, block, "block receipt identity differs")
            same(block_receipt["requests"], len(rows), "block request count differs")
            if block_receipt["completed"] is True:
                require(complete, "block falsely claims completion")
            else:
                require(block_receipt["completed"] is False and block_receipt.get("retry_attempted") is False, "invalid failed block/retry receipt")
                errors.append({"path": str(destination / "block-summary.json"), "receipt": block_receipt})
            receipts.append(block_receipt)
        else:
            missing.append(destination.name + "/block-summary.json")
        stat = block_statistics(rows, validated, block)
        stat.update(completed=complete, partial_requests=partial, partial_arm_receipts=partial_receipts,
            partial_request_outer_totals_ms={arm: sum(record["request_wall_ms"] for record in partial_receipts if record["arm"] == arm) for arm in (*ARMS, "oracle")},
            environment_setup_ms=setup_ms)
        if not complete:
            stat["direct_to_fast_ratio"] = stat["snapshot_to_fast_ratio"] = None
        blocks.append(stat)
        observed_rows.extend(rows)
        previous_completed = complete
    completed = len(blocks) == 6 and all(block["completed"] for block in blocks) and raw is not None
    if raw is not None:
        same([raw["workload"], raw["upstream"], raw["commit"], raw["targets"]], [name, upstream, commit, targets], "workload identity changed")
        same(raw["rows"], observed_rows, "workload summary dropped/reordered/altered raw observations")
        same(raw["blocks"], receipts, "workload summary dropped/altered block receipts")
        same(raw["source_before"], protocol["engine_identity"], "workload executed a different source identity")
        same(raw["source_after"], raw["source_before"], "workload source drift")
        require(raw["source_stable"] is True and raw["operator_review_claimed"] is False, "invalid source/authority claim")
        same(raw["completed"], completed, "workload completion claim differs")
        same(raw["environment"]["provenance"]["runtime_image"], IMAGE, "environment image differs")
        same(raw["environment"]["provenance"]["extra_requirements"], extras, "environment extra requirements differ")
        numeric(raw["environment"]["build_ms"])
    else:
        missing.append("summary.json")
    complete_blocks = [block for block in blocks if block["completed"]]
    performance = None
    if completed:
        totals = {arm: sum(block["observed_totals_ms"][arm] for block in blocks) for arm in (*ARMS, "oracle")}
        common_setup = raw["environment"]["build_ms"] + sum(block["environment_setup_ms"] for block in blocks)
        influence = [{"trajectory_removed_for_diagnostic_only": block["trajectory"],
            "direct_to_fast_ratio": (totals["direct"] - block["observed_totals_ms"]["direct"]) / (totals["fast"] - block["observed_totals_ms"]["fast"])} for block in blocks]
        performance = {"all_six_blocks_included": True, "totals_ms": totals,
            "common_environment_setup_ms": common_setup,
            "setup_inclusive_direct_to_fast_ratio": (totals["direct"] + common_setup) / (totals["fast"] + common_setup),
            "direct_to_fast_ratio": totals["direct"] / totals["fast"],
            "snapshot_to_fast_ratio": totals["snapshot"] / totals["fast"],
            "block_direct_to_fast_spread": spread([block["direct_to_fast_ratio"] for block in blocks]),
            "block_snapshot_to_fast_spread": spread([block["snapshot_to_fast_ratio"] for block in blocks]),
            "warm_hit_median_ms": {arm: statistics.median(row["arms"][arm]["request_wall_ms"] for row in observed_rows if row["index"] in (1, 2, 5, 6)) for arm in ARMS},
            "leave_one_block_out_influence": influence, "observations_removed": 0}
    return {"workload": name, "completed": completed, "planned_blocks": 6,
        "complete_blocks": len(complete_blocks), "observed_complete_requests": len(observed_rows),
        "fresh_agreements": sum(row["whole_task_exit_agreement"] for row in observed_rows),
        "expected_cache_behaviors": sum(row["cache_behavior_expected"] for row in observed_rows),
        "optimized_hits": sum(row["arms"]["fast"]["status"] == "HIT_REUSED" for row in observed_rows),
        "fresh_node_observations": sum(len(row["oracle"]["capture"]["nodeids"]) for row in observed_rows),
        "blocks": blocks, "headline_performance": performance, "missing": missing, "errors": errors}


def original_outlier_diagnostics(sqj=SQJ):
    specs = [("comparison-final-1/colorama", 1, 6, "direct"),
        ("comparison-final-1/pycparser", 1, 4, "oracle"),
        ("comparison-final-1/click", 1, 6, "oracle"),
        ("comparison-more-whole-task/more-itertools", 1, 0, "direct"),
        ("comparison-more-whole-task/more-itertools", 2, 2, "direct"),
        ("comparison-more-whole-task/more-itertools", 2, 3, "snapshot")]
    observations = []
    for prefix, trajectory, index, arm in specs:
        path = sqj / "evidence" / prefix / f"trajectory-{trajectory}/request-{index}/observation.json"
        row = read_json(path)
        result = row["oracle"] if arm == "oracle" else row["arms"][arm]
        matches = re.findall(r" in ([0-9]+(?:\.[0-9]+)?)s", result.get("stdout_tail", result.get("stdout", "")))
        require(matches, "original pytest duration summary unavailable")
        wall = numeric(result["request_wall_ms"] if arm == "snapshot" else result["wall_ms"]) / 1000
        observations.append({"path": "research/sqj/" + path.relative_to(sqj).as_posix(),
            "sha256": digest(path), "arm": arm, "outer_or_original_helper_seconds": wall,
            "pytest_reported_seconds": float(matches[-1]), "outside_pytest_reported_seconds": wall - float(matches[-1])})
    color_path = sqj / "evidence/comparison-final-1/colorama/summary.json"
    color = read_json(color_path)
    direct = sum(row["arms"]["direct"]["wall_ms"] for row in color["rows"])
    fast = sum(row["arms"]["fast"]["request_wall_ms"] for row in color["rows"])
    pick = next(row for row in color["rows"] if row["trajectory"] == 1 and row["index"] == 6)
    same(pick, read_json(sqj / observations[0]["path"].removeprefix("research/sqj/")), "original Colorama summary differs from observation")
    more_path = sqj / "evidence/comparison-more-whole-task/more-itertools/summary.json"
    more = read_json(more_path)
    gap = sum(row["arms"]["fast"]["request_wall_ms"] - row["arms"]["snapshot"]["request_wall_ms"] for row in more["rows"])
    more_pick = next(row for row in more["rows"] if row["trajectory"] == 2 and row["index"] == 3)
    same(more_pick, read_json(sqj / observations[-1]["path"].removeprefix("research/sqj/")), "original more-itertools summary differs from observation")
    return {"observations": observations, "original_observations_removed": 0,
        "colorama": {"summary_path": "research/sqj/evidence/comparison-final-1/colorama/summary.json",
            "summary_sha256": digest(color_path), "aggregate_direct_to_fast": direct / fast,
            "single_direct_observation_fraction": pick["arms"]["direct"]["wall_ms"] / direct,
            "leave_one_paired_request_out_diagnostic_only": (direct - pick["arms"]["direct"]["wall_ms"]) / (fast - pick["arms"]["fast"]["request_wall_ms"])},
        "more_itertools": {"summary_path": "research/sqj/evidence/comparison-more-whole-task/more-itertools/summary.json",
            "summary_sha256": digest(more_path), "fast_minus_snapshot_seconds": gap / 1000,
            "single_request_fraction_of_gap": (more_pick["arms"]["fast"]["request_wall_ms"] - more_pick["arms"]["snapshot"]["request_wall_ms"]) / gap},
        "interpretation": "Elapsed time outside pytest's printed summary is not localized or identified as CPU time; no outlier is excluded."}


def analyze(directory, engine_archive, sqj=SQJ):
    protocol = read_json(directory / "protocol.json")
    validate_protocol(protocol, engine_archive, sqj)
    campaign_path = directory / "campaign-summary.json"
    campaign = read_json(campaign_path) if campaign_path.exists() else None
    results, workload_failures = [], []
    require({path.name for path in directory.glob("*-failure.json")} <= {name + "-failure.json" for name in COHORT},
            "unexpected workload failure receipt")
    for scheduled in expected_schedule():
        item = next(item for item in WORKLOADS if item[0] == scheduled["workload"])
        result = inspect_workload(directory / item[0], item, scheduled, protocol, sqj)
        failure_path = directory / (item[0] + "-failure.json")
        if failure_path.exists():
            failure = read_json(failure_path)
            require(failure["completed"] is False and failure["retry_attempted"] is False and failure["workload"] == item[0], "invalid workload failure receipt")
            workload_failures.append({"path": str(failure_path), "receipt": failure})
            require(not result["completed"], "completed workload also has a failure receipt")
        results.append(result)
    completed = campaign is not None and all(row["completed"] for row in results) and not workload_failures
    if campaign is not None:
        require(campaign["schema"] == "zerorun.randomized-short-replication-summary.v1", "wrong campaign summary schema")
        require(campaign["operator_authority_receipts_created"] is False and campaign["protected_source_unchanged"] is True, "campaign source/authority boundary failed")
        same(campaign["protected_source_after"], protocol["protected_source_identity"], "campaign protected source changed")
        same([row["workload"] for row in campaign["results"]], [row["workload"] for row in expected_schedule()], "campaign dropped/reordered workload")
        for actual, checked in zip(campaign["results"], results, strict=True):
            same(actual["completed"], checked["completed"], "campaign workload completion differs")
            if "requests" in actual:
                same(actual["requests"], checked["observed_complete_requests"], "campaign workload request count differs")
            else:
                require(any(error["receipt"] == actual for error in workload_failures), "unpreserved campaign workload error")
        same(campaign["completed"], completed, "campaign falsely claims completion")
    counts = {field: sum(row[field] for row in results) for field in ("complete_blocks", "observed_complete_requests", "fresh_agreements", "expected_cache_behaviors", "optimized_hits", "fresh_node_observations")}
    if completed:
        same([counts["complete_blocks"], counts["observed_complete_requests"], counts["fresh_agreements"], counts["expected_cache_behaviors"], counts["optimized_hits"]], [24, 168, 168, 168, 96], "complete campaign denominator mismatch")
    compact = []
    for row in results:
        performance = row["headline_performance"]
        compact.append({"workload": row["workload"], "completed": row["completed"],
            "complete_blocks_of_six": row["complete_blocks"], "requests_of_42": row["observed_complete_requests"],
            "fresh_agreements": row["fresh_agreements"],
            "direct_to_fast_ratio": performance["direct_to_fast_ratio"] if performance else None,
            "setup_inclusive_direct_to_fast_ratio": performance["setup_inclusive_direct_to_fast_ratio"] if performance else None,
            "block_direct_to_fast_range": [performance["block_direct_to_fast_spread"][key] for key in ("minimum", "maximum")] if performance else None,
            "warm_hit_median_ms": performance["warm_hit_median_ms"] if performance else None,
            "median_hit_latency_reduction_percent_vs_snapshot": 100 * (1 - performance["warm_hit_median_ms"]["fast"] / performance["warm_hit_median_ms"]["snapshot"]) if performance else None})
    return {"schema": "zerorun.randomized-short-replication-analysis.v1", "completed": completed,
        "protocol_sha256": digest(directory / "protocol.json"), "campaign_summary_sha256": digest(campaign_path) if campaign else None,
        "source_hashes_verified": True, "producer_sha256": DRIVER_SHA256, "counts": counts,
        "planned": {"subjects": 4, "blocks": 24, "requests": 168, "timed_arms": 504, "fresh_oracles": 168},
        "subjects": results, "workload_failures": workload_failures,
        "paper_summary": {"all_planned_requests_reconciled": completed, "subjects": compact,
            "denominator": "four previously observed short subjects, six seven-request blocks each",
            "interpretation": "Conditional hit latency and complete sequence cost are separate; all original and newly observed negative results remain."},
        "missing_campaign_summary": campaign is None,
        "scope": "Seen short-subject replication, not a new holdout or replacement of original five-subject data.",
        "uncertainty": "Per-subject complete-block spread and leave-one-block influence; no nested-node or population confidence claim.",
        "original_outlier_diagnostics": original_outlier_diagnostics(sqj)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--engine-archive", type=Path, default=SQJ / "source-final")
    parser.add_argument("--sqj-root", type=Path, default=SQJ)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.directory, args.engine_archive, args.sqj_root)
    except (ValueError, KeyError, TypeError, OSError) as error:
        result = {"schema": "zerorun.randomized-short-replication-analysis.v1", "completed": False,
                  "validation_error": type(error).__name__ + ": " + str(error)}
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        if args.check:
            require(args.output.read_text(encoding="utf-8") == encoded, "saved replication analysis differs")
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
    else:
        print(encoded, end="")
    return 0 if result.get("completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
