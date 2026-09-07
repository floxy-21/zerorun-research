"""Bounded post-hoc cost analysis of existing measurements; never run tests.

The frozen protocol specifies inclusion and equations. This module neither
changes eligibility nor estimates a real-agent hit rate. --check is read-only.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import statistics

from research.softwarex import analysis_reproduction

ROOT = Path(__file__).resolve().parents[2]
HERE = "research/softwarex"
SQJ = "research/sqj"
EVIDENCE = SQJ + "/strengthening/evidence"
ORIGINAL = EVIDENCE + "/short-randomized-replication-v1"
RECOVERY = EVIDENCE + "/short-randomized-recovery-v1"
SAVED = EVIDENCE + "/replication-analysis-v1.json"
OUTPUT = HERE + "/generated/operating-region-v1.json"
SUBJECTS = ("click", "pycparser", "colorama", "packaging")
HITS = (1, 2, 5, 6)
LABELS = ("seed", "repeat-1", "repeat-2", "failing-test-added", "failure-repeated", "restored", "repeat-restored")
PHASE_ORDER = ("container ls", "create", "container inspect", "start", "container inspect", "container rm", "container inspect")
HIT_COMPONENTS = ("runtime_inspect_initial", "environment", "fingerprint_initial", "lock_acquire", "cache_lookup", "fingerprint_final", "event_append")
ROUNDING_TOLERANCE_MS = 0.01
PINNED = {
    SAVED: "287c3f79beb90ac12913a9b6002e8c02c5bdd8fcda2bbf3644b0118661f5ac0b",
    ORIGINAL + "/protocol.json": "6bcadb632a509bf12ba49449f069585a5836c795e761e66f875da4c24d7081ec",
    RECOVERY + "/recovery-protocol.json": "7eea714d987ae1923c5c526b49a9363f0c37d2ea2fca3c0ab86b66dda1f08eae",
    SQJ + "/strengthening/analyze_replication.py": "70a36cc5c77d3e600a0e5ad151af86c3dbf3ebfae5b17004c5c995c0e6e294a4",
    SQJ + "/strengthening/analyze_recovered_replication.py": "b51e5fd3c33459a23cff57c41031bc32b37b1dbc52dc3469f8a99217f95426c9",
    HERE + "/OPERATING_REGION.md": "0b8898ba76d6e0962ebdff19771d2c98ae2a3055ba6a97fc9ede905823f5a6bf",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number(value, *, positive=False):
    require(type(value) in (int, float) and math.isfinite(value)
            and (value > 0 if positive else value >= 0), "invalid finite nonnegative time")
    return value


def finite_signed(value):
    require(type(value) in (int, float) and math.isfinite(value), "invalid finite signed quantity")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def same(actual, expected, message):
    require(canonical(actual) == canonical(expected), message)


def close(actual, expected, message):
    require(math.isclose(finite_signed(actual), finite_signed(expected), rel_tol=1e-10, abs_tol=1e-6), message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def floating(value):
        return finite_signed(float(value))

    def invalid(value):
        raise ValueError("nonfinite JSON value: " + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_float=floating, parse_constant=invalid)


def safe_name(name):
    require(isinstance(name, str) and name and "\\" not in name and ":" not in name, "unsafe input path")
    path = PurePosixPath(name)
    require(not path.is_absolute() and all(part not in {"", ".", ".."} for part in name.split("/")), "noncanonical input path")
    return path


def read_regular(root, name):
    relative = safe_name(name)
    cursor = Path(root)
    for part in relative.parts:
        cursor = cursor / part
        info = cursor.lstat()
        require(not cursor.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400, "linked/reparse input refused")
    require(cursor.is_file() and info.st_size <= 64 * 1024 * 1024, "nonordinary or oversized input")
    raw = cursor.read_bytes()
    after = cursor.stat()
    require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "input changed during reading")
    return raw


def file_record(root, name):
    raw = read_regular(root, name)
    return {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def anchored_inputs(root):
    records = []
    for name, expected in sorted(PINNED.items()):
        record = file_record(root, name)
        require(record["sha256"] == expected, "frozen anchor changed: " + name)
        records.append(record)
    return records


def crossover(d_h, f_h, d_m, f_m, *, added_cost_per_request=0):
    """Equality of two affine mixtures; do not clamp infeasible crossings."""
    for value in (d_h, f_h, d_m, f_m):
        number(value)
    added = finite_signed(added_cost_per_request)
    penalty = finite_signed(f_m - d_m)
    saving = finite_signed(d_h - f_h)
    denominator = finite_signed(penalty + saving)
    intercept = finite_signed(penalty + added)
    if denominator == 0:
        return {"nonhit_penalty_ms": penalty, "hit_saving_ms": saving, "denominator_ms": 0,
                "added_cost_per_request_ms": added, "equality_hit_fraction": None, "interior_crossover": False,
                "regime": "equal_everywhere" if intercept == 0 else "reuse_slower_everywhere" if intercept > 0 else "reuse_faster_everywhere"}
    equality = finite_signed(intercept / denominator)
    return {"nonhit_penalty_ms": penalty, "hit_saving_ms": saving, "denominator_ms": denominator,
            "added_cost_per_request_ms": added, "equality_hit_fraction": equality,
            "interior_crossover": 0 < equality < 1, "equality_in_unit_interval": 0 <= equality <= 1,
            "regime": "reuse_faster_above_equality" if denominator > 0 else "reuse_faster_below_equality"}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "timing timestamp lacks timezone")
    return parsed


def direct_components(arm):
    outer = number(arm["request_wall_ms"], positive=True)
    phases = arm["docker_subprocess_phases"]
    same([phase["phase"] for phase in phases], list(PHASE_ORDER), "direct lifecycle phases changed")
    begin, end = timestamp(arm["started_utc"]), timestamp(arm["ended_utc"])
    prior = begin
    control = start = 0.0
    for phase in phases:
        phase_begin, phase_end = timestamp(phase["started_utc"]), timestamp(phase["ended_utc"])
        require(prior <= phase_begin <= phase_end <= end, "overlapping or escaped direct interval")
        prior = phase_end
        elapsed = number(phase["wall_ms"])
        if phase["phase"] == "start":
            start += elapsed
        else:
            control += elapsed
    residual = outer - control - start
    require(residual >= -ROUNDING_TOLERANCE_MS, "direct phase sum exceeds outer clock")
    return {"outer_ms": outer, "docker_start_including_attached_execution_ms": start,
            "other_docker_control_ms": control, "unattributed_outer_residual_ms": residual}


def hit_components(arm):
    require(arm["status"] == "HIT_REUSED", "hit attribution requested for non-hit")
    outer = number(arm["request_wall_ms"], positive=True)
    phases = arm["phase_ms"]
    allowed = set(HIT_COMPONENTS) | {"readonly_hit_path", "snapshot_prepare", "snapshot_cleanup", "docker_execution"}
    require(set(phases) <= allowed and set(HIT_COMPONENTS) <= set(phases), "unknown or missing hit timing component")
    require(all(number(phases.get(name, 0)) == 0 for name in ("snapshot_prepare", "snapshot_cleanup", "docker_execution")), "hit performed staging or execution")
    number(phases["readonly_hit_path"])
    require(phases["readonly_hit_path"] <= outer + ROUNDING_TOLERANCE_MS, "inclusive hit interval exceeds outer clock")
    components = {name: number(phases[name]) for name in HIT_COMPONENTS}
    residual = outer - sum(components.values())
    require(residual >= -ROUNDING_TOLERANCE_MS, "exclusive hit components exceed outer clock")
    return {"outer_ms": outer, "two_fingerprints_ms": components["fingerprint_initial"] + components["fingerprint_final"],
            "initial_runtime_inspection_ms": components["runtime_inspect_initial"],
            "other_named_exclusive_ms": sum(components[name] for name in ("environment", "lock_acquire", "cache_lookup", "event_append")),
            "unattributed_outer_residual_ms": residual,
            "inclusive_readonly_aggregate_not_added_ms": phases["readonly_hit_path"]}


def attribution(records, *, hit=False):
    require(records, "empty timing attribution")
    totals = {key: sum(record[key] for record in records) for key in records[0]}
    fractions = {key.removesuffix("_ms"): value / totals["outer_ms"] for key, value in totals.items()
                 if key not in {"outer_ms", "inclusive_readonly_aggregate_not_added_ms"}}
    close(sum(fractions.values()), 1, "exclusive attribution does not reconcile")
    return {"requests": len(records), "totals_ms": totals, "fractions_of_outer": fractions,
            "scope": "optimized hits only; inclusive readonly aggregate excluded" if hit else "direct helper only; start is not pure test execution"}


def summarize_rows(rows):
    require(rows and len(rows) % 7 == 0, "incomplete seven-state block")
    identities = [(row["trajectory"], row["index"]) for row in rows]
    require(len(set(identities)) == len(rows), "duplicate selected request")
    for row in rows:
        index = row["index"]
        require(type(index) is int and 0 <= index < 7, "invalid request index")
        expected = "HIT_REUSED" if index in HITS else "MISS_EXECUTED" if index == 0 else "MISS_FAILED"
        require(row["label"] == LABELS[index] and row["arms"]["fast"]["status"] == expected,
                "request category or expected status changed")
        require(row["whole_task_exit_agreement"] is True and row["cache_behavior_expected"] is True, "unsuccessful request in completed estimator")
        for arm in ("direct", "fast"):
            require(row["arms"][arm]["completed"] is True, "incomplete selected arm")
            number(row["arms"][arm]["request_wall_ms"], positive=True)
    for block in {row["trajectory"] for row in rows}:
        same(sorted(row["index"] for row in rows if row["trajectory"] == block), list(range(7)), "missing state within block")
    hits = [row for row in rows if row["index"] in HITS]
    misses = [row for row in rows if row["index"] not in HITS]
    blocks = len(rows) // 7
    same([len(hits), len(misses)], [4 * blocks, 3 * blocks], "category denominator changed")
    means = {arm + "_" + kind: statistics.mean(row["arms"][arm]["request_wall_ms"] for row in selected)
             for arm in ("direct", "fast") for kind, selected in (("hit", hits), ("nonhit", misses))}
    totals = {arm: sum(row["arms"][arm]["request_wall_ms"] for row in rows) for arm in ("direct", "fast")}
    p = len(hits) / len(rows)
    for arm in totals:
        close(len(rows) * (p * means[arm + "_hit"] + (1 - p) * means[arm + "_nonhit"]), totals[arm], "mixture cannot reconstruct full totals")
    return {"requests": len(rows), "hit_category_requests": len(hits), "nonhit_category_requests": len(misses),
            "constructed_hit_fraction": p, "category_mean_ms": means, "completed_request_totals_ms": totals,
            "observed_mixture_direct_to_fast_ratio": totals["direct"] / totals["fast"],
            "crossover": crossover(means["direct_hit"], means["fast_hit"], means["direct_nonhit"], means["fast_nonhit"]),
            "hit_cost_attribution": attribution([hit_components(row["arms"]["fast"]) for row in hits], hit=True),
            "direct_cost_attribution": attribution([direct_components(row["arms"]["direct"]) for row in rows])}


def reconcile(root):
    analysis_reproduction.require_canonical_python()
    require(Path(analysis_reproduction.__file__).resolve() ==
            (root / HERE / "analysis_reproduction.py").resolve(),
            "analysis reproduction policy imported from another checkout")
    anchors = anchored_inputs(root)
    module = importlib.import_module("research.sqj.strengthening.analyze_recovered_replication")
    require(Path(module.__file__).resolve() == (root / SQJ / "strengthening/analyze_recovered_replication.py").resolve(), "reconciliation imported from another checkout")
    require(Path(module.base.__file__).resolve() == (root / SQJ / "strengthening/analyze_replication.py").resolve(), "base reconciliation imported from another checkout")
    helper = importlib.import_module("tools.product_generalization_benchmark")
    require(Path(helper.__file__).resolve() == (root / "tools/product_generalization_benchmark.py").resolve(), "shadow validator imported from another checkout")
    require(read_regular(root, "tools/product_generalization_benchmark.py") ==
            read_regular(root, SQJ + "/source-final/tools/product_generalization_benchmark.py"),
            "executed shadow-validation helper differs from archived source")
    result = module.analyze(root / ORIGINAL, root / RECOVERY, root / SQJ / "source-final", sqj=root / SQJ)
    saved = strict_json(read_regular(root, SAVED))
    result = analysis_reproduction.canonical_reconciliation(result, saved)
    require(result["completed"] is True and result["uninterrupted_original_campaign"] is False, "wrong campaign completion/scope")
    same({key: result["counts"][key] for key in ("complete_blocks", "observed_complete_requests", "fresh_agreements", "expected_cache_behaviors", "optimized_hits")},
         {"complete_blocks": 24, "observed_complete_requests": 168, "fresh_agreements": 168, "expected_cache_behaviors": 168, "optimized_hits": 96}, "frozen overall denominator differs")
    return result, anchors


def inventory(root, reconciled):
    names = set(PINNED) | {HERE + "/analyze_operating_region.py", HERE + "/analysis_reproduction.py", RECOVERY + "/external-incident.txt",
                           "tools/product_generalization_benchmark.py"}
    for directory in (ORIGINAL, RECOVERY):
        for parent, dirs, files in os.walk(root / directory, followlinks=False):
            require(not set(dirs) & {"workspace", "workspaces", ".zerorun", ".zerorun-env", "private-cache-authentication-NOT-FOR-PUBLICATION"}, "private/live workspace in public evidence")
            for name in dirs:
                path = Path(parent) / name
                info = path.lstat()
                require(not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400, "linked receipt directory")
            names.update((Path(parent) / name).relative_to(root).as_posix() for name in files if name.endswith(".json"))
    protocol = strict_json(read_regular(root, ORIGINAL + "/protocol.json"))
    for item in protocol["protected_source_identity"]["files"]:
        prefix, relative = item["path"].split("/", 1)
        name = SQJ + "/source-final/" + relative if prefix == "engine" else SQJ + "/" + relative
        record = file_record(root, name)
        same([record["bytes"], record["sha256"]], [item["bytes"], item["sha256"]], "protected experiment source changed")
        names.add(name)
    for name in ("analyze_campaign.py", "analyze_comparison.py", "run_frozen_campaign.py", "strengthening/randomized_replication.py", "strengthening/run_replication_recovery.py"):
        names.add(SQJ + "/" + name)
    return [file_record(root, name) for name in sorted(names)]


def analyze(root=ROOT):
    root = Path(root).resolve()
    reconciled, anchors = reconcile(root)
    same([subject["workload"] for subject in reconciled["subjects"]], list(SUBJECTS), "subject order changed")
    sources_before = inventory(root, reconciled)
    subjects = []
    for subject, old in zip(reconciled["subjects"], reconciled["original_analysis"]["subjects"], strict=True):
        name = subject["workload"]
        require(old["workload"] == name and subject["completed"] is True, "subject source or completion differs")
        old_ids = {block["trajectory"] for block in old["blocks"] if block["completed"]}
        same([block["trajectory"] for block in subject["blocks"]], list(range(1, 7)), "completed block selection differs")
        rows, blocks = [], []
        for block in subject["blocks"]:
            block_id = block["trajectory"]
            study = ORIGINAL if block_id in old_ids else RECOVERY
            paths = [f"{study}/{name}/trajectory-{block_id}/request-{index}/observation.json" for index in range(7)]
            selected = [strict_json(read_regular(root, path)) for path in paths]
            for index, row in enumerate(selected):
                same([row["workload"], row["trajectory"], row["index"], row["order"]], [name, block_id, index, block["order"]], "raw selected request identity differs")
            summary = summarize_rows(selected)
            for arm in ("direct", "fast"):
                close(summary["completed_request_totals_ms"][arm], block["observed_totals_ms"][arm], "block total disagrees with independent analysis")
            close(summary["observed_mixture_direct_to_fast_ratio"], block["direct_to_fast_ratio"], "block ratio disagrees with independent analysis")
            blocks.append({"trajectory": block_id, "order": block["order"], "study": study.rsplit("/", 1)[1], "observation_paths": paths, **summary})
            rows.extend(selected)
        summary = summarize_rows(rows)
        close(summary["observed_mixture_direct_to_fast_ratio"], subject["direct_to_fast_ratio"], "subject ratio disagrees with independent analysis")
        for arm in ("direct", "fast"):
            close(summary["completed_request_totals_ms"][arm], subject["completed_block_totals_ms"][arm], "subject total disagrees with independent analysis")
        crossovers = [block["crossover"]["equality_hit_fraction"] for block in blocks]
        finite = [value for value in crossovers if value is not None]
        subjects.append({"workload": name, "blocks": blocks, **summary,
                         "block_crossover_range": [min(finite), max(finite)] if finite else None,
                         "block_crossover_range_is_confidence_interval": False,
                         "blocks_without_unique_crossover": len(crossovers) - len(finite),
                         "interrupted_attempt_sensitivity": {"additional_measured_totals_ms": subject["additional_interrupted_attempt_measured_totals_ms"],
                             "all_recorded_attempt_direct_to_fast_ratio": subject["all_recorded_attempt_direct_to_fast_ratio"],
                             "crossover_not_recomputed_from_incomplete_strata": True},
                         "setup_inclusive_direct_to_fast_ratio": subject["setup_inclusive_direct_to_fast_ratio"],
                         "setup_inclusive_unavailable_reason": subject["setup_inclusive_unavailable_reason"]})
    sources_after = inventory(root, reconciled)
    same(sources_before, sources_after, "input changed during operating-region analysis")
    return {"schema": "zerorun.softwarex-operating-region.v1", "completed": True,
            "analysis_kind": "post-hoc fixed-category descriptive sensitivity, not preregistered inference",
            "new_measurements": False, "runtime_changed": False, "real_agent_hit_frequency_estimated": False,
            "population_inference": False, "novel_caching_algorithm_claimed": False,
            "counts": {"subjects": 4, "blocks": 24, "requests": 168, "hit_category": 96, "nonhit_category": 72},
            "constructed_hit_fraction": 4 / 7,
            "equations": {"direct": "p*D_h+(1-p)*D_m", "optimized": "p*F_h+(1-p)*F_m",
                "crossover": "(F_m-D_m)/[(F_m-D_m)+(D_h-F_h)]",
                "extra_deployment_cost": "replace numerator by F_m-D_m+A/N; A and N are unmeasured",
                "common_setup": "equal common setup allocations cancel from the crossover"},
            "subjects": subjects,
            "interruption": {"uninterrupted_original_campaign": False,
                "additional_interrupted_complete_requests": reconciled["additional_interrupted_complete_requests"],
                "additional_incomplete_requests": reconciled["additional_incomplete_requests"],
                "unparseable_zero_byte_receipts": reconciled["unparseable_zero_byte_receipts"],
                "unflushed_invocation_only_directories": reconciled["unflushed_invocation_only_directories"],
                "unmeasured_interruption_cost": reconciled["unmeasured_interruption_cost"]},
            "binding": {"anchors": anchors, "input_files": sources_before,
                "input_inventory_sha256": hashlib.sha256(canonical(sources_before).encode()).hexdigest(),
                "source_guard": "Exact original/recovery values and protected source validation before derivation; only the declared unordered zero-byte incident inventory may differ in row order.",
                "canonical_replay_policy": {"implementation": "CPython", "supported_versions": ["3.12", "3.13", "3.14"],
                    "order_insensitive_field": "unparseable_zero_byte_receipts", "zero_byte_inventory_order_only": True,
                    "numeric_tolerance_applied": False},
                "rounding_tolerance_ms": ROUNDING_TOLERANCE_MS},
            "limitations": ["Nonhits preserve one successful seed and two failures per block; other mixtures can differ.",
                "Four seen convenience subjects on one shared-host VM; block ranges are not confidence intervals.",
                "Completed blocks and separately recorded recovery are retained; no imputation of missing work.",
                "Inclusive product aggregates are not added; direct start includes attached execution and lifecycle overhead.",
                "No measured deployment cost, autonomous-agent benefit, token savings, CPU/energy savings, or commercial demand."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT)
    args = parser.parse_args(argv)
    result = analyze()
    raw = encoded(result)
    if args.check:
        require(args.output.read_bytes() == raw, "saved operating-region analysis differs")
    else:
        with args.output.open("xb") as stream:
            stream.write(raw)
    print(json.dumps({"completed": True, "subjects": len(result["subjects"]), "requests": result["counts"]["requests"], "check_only": args.check}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
