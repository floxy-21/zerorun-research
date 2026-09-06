"""Independently reconcile an interrupted campaign and explicit recovery.

Raw original receipts are never edited or replaced. Complete planned blocks
form one clearly labeled completed-block analysis. All additional interrupted
attempt observations and costs are reported separately and as cost sensitivity.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import statistics

from research.sqj.strengthening import analyze_replication as base


class CrashView:
    """Read-only parser view; byte-truncated receipts remain in the artifact.

    Zero-byte records contain no parseable measurement and cannot assert a
    successful observation. A next request with ONLY an empty invocation has
    no execution evidence; report it separately, outside measured requests.
    """
    def __init__(self, path, unavailable):
        self.path = Path(path)
        self.unavailable = unavailable

    def __truediv__(self, part):
        return CrashView(self.path / part, self.unavailable)

    def __str__(self):
        return str(self.path)

    def __getattr__(self, name):
        return getattr(self.path, name)

    def exists(self):
        return self.path not in self.unavailable and self.path.exists()

    def glob(self, pattern):
        return (CrashView(p, self.unavailable) for p in self.path.glob(pattern) if p not in self.unavailable)


def interrupted_parser_view(original):
    unavailable, truncated, unflushed = set(), [], []
    for block in original.glob("*/trajectory-*"):
        if (block / "block-summary.json").exists():
            continue
        for request in block.glob("request-*"):
            files = list(request.iterdir())
            for file in files:
                if file.is_file() and file.suffix == ".json" and file.stat().st_size == 0:
                    unavailable.add(file)
                    truncated.append({"path": file.relative_to(original).as_posix(), "bytes": 0, "sha256": base.digest(file),
                                      "classification": "zero-byte crash-flush; no inferable result or duration"})
            if len(files) == 1 and files[0].name == "invocation.json" and files[0] in unavailable:
                unavailable.add(request)
                unflushed.append(request.relative_to(original).as_posix())
    return CrashView(original, unavailable), truncated, unflushed


def inventory(root):
    rows = []
    excluded = {"private-cache-authentication-NOT-FOR-PUBLICATION", "workspace", "workspaces", ".zerorun", ".zerorun-env"}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root)
        if excluded.intersection(relative.parts):
            continue
        base.require(path.is_file() and not path.is_symlink(), "nonordinary original receipt")
        rows.append({"path": relative.as_posix(), "sha256": base.digest(path), "bytes": path.stat().st_size})
    return rows


def analyze(original, recovery, engine_archive, sqj=base.SQJ):
    original = Path(original)
    recovery = Path(recovery)
    parser_view, truncated, unflushed = interrupted_parser_view(original)
    old = base.analyze(parser_view, engine_archive, sqj)
    original_protocol = base.read_json(original / "protocol.json")
    base.require(old["missing_campaign_summary"], "not an interrupted original campaign")
    p = base.read_json(recovery / "recovery-protocol.json")
    base.same(p["schema"], "zerorun.short-replication-recovery-protocol.v1", "wrong recovery protocol")
    base.same(p["frozen_driver_sha256"], base.DRIVER_SHA256, "changed frozen driver")
    base.same(p["producer_sha256"], base.digest(Path(__file__).with_name("run_replication_recovery.py")), "changed recovery runner")
    base.same(p["original_protocol_sha256"], base.digest(original / "protocol.json"), "original protocol binding mismatch")
    base.same(p["original_evidence_inventory"], inventory(original), "original receipts dropped or changed")
    base.same(p["incident_sha256"], base.digest(recovery / "external-incident.txt"), "external incident differs")
    base.require(p["selection_uses_performance"] is False and p["operator_authority_allowed"] is False, "invalid recovery selection/authority")
    base.same(p["protected_source_before"], original_protocol["protected_source_identity"], "recovery protected sources changed")
    base.same(p["engine_identity_before"], original_protocol["engine_identity"], "recovery engine changed")
    original_ends = []
    for path in original.glob("*/trajectory-*/request-*/*.json"):
        if path.name not in {"direct.json", "snapshot.json", "fast.json", "fresh.json"} or path.stat().st_size == 0:
            continue
        row = base.read_json(path)
        if "ended_utc" in row:
            original_ends.append(base.timestamp(row["ended_utc"]))
    if original_ends:
        base.require(base.timestamp(p["frozen_utc"]) >= max(original_ends), "recovery amendment predates original observed work")
    pending, done, interrupted = [], [], []
    for scheduled, checked in zip(base.expected_schedule(), old["subjects"], strict=True):
        base.require(not checked["errors"], "original material failure cannot become infrastructure recovery")
        existing = {b["trajectory"]: b for b in checked["blocks"]}
        blocks = []
        for block in scheduled["trajectories"]:
            checked_block = existing.get(block["trajectory"])
            identity = {"workload": scheduled["workload"], **block}
            if checked_block is not None and checked_block["completed"]:
                done.append(identity)
            else:
                blocks.append(block)
                if checked_block is not None:
                    interrupted.append(identity)
        if blocks:
            pending.append({"workload": scheduled["workload"], "trajectories": blocks})
    base.same(p["completed_original_blocks"], done, "original block selection differs")
    base.same(p["interrupted_original_blocks"], interrupted, "interrupted original blocks hidden")
    base.same(p["recovery_schedule"], pending, "recovery changed planned block selection")
    base.require(bool(pending) and bool(interrupted), "no interruption to recover")
    completion = base.read_json(recovery / "recovery-completion.json")
    base.same(completion["schema"], "zerorun.short-replication-recovery-completion.v1", "wrong recovered completion schema")
    base.same(completion["recovery_protocol_sha256"], base.digest(recovery / "recovery-protocol.json"), "completion protocol differs")
    base.same(completion["protected_source_after"], original_protocol["protected_source_identity"], "post-recovery protected source drift")
    base.same(completion["engine_identity_after"], original_protocol["engine_identity"], "post-recovery engine drift")
    base.require(completion["original_evidence_unchanged"] is True and completion["operator_authority_receipts_created"] is False
                 and completion["uninterrupted_original_campaign"] is False, "dishonest completion/source/authority claim")
    recovered = {}
    for scheduled in pending:
        name = scheduled["workload"]
        item = next(item for item in base.WORKLOADS if item[0] == name)
        checked = base.inspect_workload(recovery / name, item, scheduled, original_protocol, sqj)
        base.require(not checked["errors"], "recovery contains material failure")
        recovered[name] = checked
    base.same([row["workload"] for row in completion["results"]], [s["workload"] for s in pending], "recovery completion omitted workload")
    success = True
    for declared, scheduled in zip(completion["results"], pending, strict=True):
        checked = recovered[scheduled["workload"]]
        valid = checked["complete_blocks"] == len(scheduled["trajectories"]) and checked["observed_complete_requests"] == 7 * len(scheduled["trajectories"])
        base.same(declared["completed"], valid, "false recovery workload completion")
        base.same(declared["requests"], checked["observed_complete_requests"], "recovery request count differs")
        success &= valid
    base.same(completion["completed"], success, "false recovered campaign completion")
    subjects, compact, extra_attempts = [], [], []
    all_fresh = all_nodes = all_hits = 0
    for old_subject, scheduled in zip(old["subjects"], base.expected_schedule(), strict=True):
        name = scheduled["workload"]
        old_complete = [b for b in old_subject["blocks"] if b["completed"]]
        new_subject = recovered.get(name)
        new_complete = new_subject["blocks"] if new_subject else []
        complete_blocks = sorted(old_complete + new_complete, key=lambda b: b["trajectory"])
        base.require(len({b["trajectory"] for b in complete_blocks}) == len(complete_blocks), "duplicate completed attempt")
        if success:
            base.same([b["trajectory"] for b in complete_blocks], list(range(1, 7)), "not all planned blocks completed")
        observations = []
        old_ids = {b["trajectory"] for b in old_complete}
        for block in complete_blocks:
            root = original if block["trajectory"] in old_ids else recovery
            for i in range(7):
                row = base.read_json(root / name / f"trajectory-{block['trajectory']}/request-{i}/observation.json")
                if root == recovery:
                    base.require(base.timestamp(row["arms"][row["order"][0]]["started_utc"]) >= base.timestamp(p["frozen_utc"]), "recovery observation precedes amendment")
                observations.append(row)
        fresh = sum(r["whole_task_exit_agreement"] for r in observations)
        nodes = sum(len(r["oracle"]["capture"]["nodeids"]) for r in observations)
        hits = sum(r["arms"]["fast"]["status"] == "HIT_REUSED" for r in observations)
        all_fresh += fresh
        all_nodes += nodes
        all_hits += hits
        totals = {arm: sum(b["observed_totals_ms"][arm] for b in complete_blocks) for arm in (*base.ARMS, "oracle")}
        aborted = [b for b in old_subject["blocks"] if not b["completed"]]
        aborted_cost = {arm: sum(b["observed_totals_ms"][arm] + b["partial_request_outer_totals_ms"][arm] for b in aborted) for arm in totals}
        for block in aborted:
            extra_attempts.append({"workload": name, **block, "policy": "retained outside completed-block estimator; measured costs included in all-attempt cost sensitivity"})
        median = {arm: statistics.median(r["arms"][arm]["request_wall_ms"] for r in observations if r["index"] in (1, 2, 5, 6)) for arm in base.ARMS} if success else None
        ratio = totals["direct"] / totals["fast"] if success else None
        setup_ratio = old_subject["headline_performance"]["setup_inclusive_direct_to_fast_ratio"] if old_subject["headline_performance"] else None
        summary = {"workload": name, "completed": success, "complete_blocks_of_six": len(complete_blocks),
            "requests_of_42": len(observations), "fresh_agreements": fresh,
            "direct_to_fast_ratio": ratio, "setup_inclusive_direct_to_fast_ratio": setup_ratio,
            "block_direct_to_fast_range": [min(b["direct_to_fast_ratio"] for b in complete_blocks), max(b["direct_to_fast_ratio"] for b in complete_blocks)] if success else None,
            "warm_hit_median_ms": median,
            "median_hit_latency_reduction_percent_vs_snapshot": 100 * (1 - median["fast"] / median["snapshot"]) if median else None}
        compact.append(summary)
        subjects.append({**summary, "blocks": complete_blocks, "completed_block_totals_ms": totals,
            "additional_interrupted_attempt_measured_totals_ms": aborted_cost,
            "all_recorded_attempt_direct_to_fast_ratio": (totals["direct"] + aborted_cost["direct"]) / (totals["fast"] + aborted_cost["fast"]) if success else None,
            "setup_inclusive_unavailable_reason": "Original workload-level dependency setup receipt was not flushed before host crash; no fabricated setup duration." if setup_ratio is None else None})
    counts = {"complete_blocks": sum(s["complete_blocks_of_six"] for s in compact), "observed_complete_requests": sum(s["requests_of_42"] for s in compact),
        "fresh_agreements": all_fresh, "expected_cache_behaviors": sum(s["requests_of_42"] for s in compact), "optimized_hits": all_hits, "fresh_node_observations": all_nodes}
    if success:
        base.same([counts[k] for k in ("complete_blocks", "observed_complete_requests", "fresh_agreements", "optimized_hits")], [24, 168, 168, 96], "recovered denominator mismatch")
    return {"schema": "zerorun.short-replication-recovered-analysis.v1", "completed": success,
        "uninterrupted_original_campaign": False, "original_analysis": old,
        "recovery_protocol_sha256": base.digest(recovery / "recovery-protocol.json"), "recovery_completion_sha256": base.digest(recovery / "recovery-completion.json"),
        "original_protocol_sha256": base.digest(original / "protocol.json"), "source_hashes_verified": True,
        "counts": counts, "subjects": subjects, "interrupted_attempts": extra_attempts,
        "unparseable_zero_byte_receipts": truncated, "unflushed_invocation_only_directories": unflushed,
        "additional_interrupted_complete_requests": sum(b["requests_observed"] for b in extra_attempts),
        "additional_incomplete_requests": sum(len(b["partial_requests"]) for b in extra_attempts),
        "paper_summary": {"all_planned_requests_reconciled": success, "subjects": compact,
            "denominator": "24 completed planned blocks, with post-crash restarted block explicitly identified; additional interrupted observations retained separately",
            "interpretation": "Post-observation infrastructure recovery amendment, not uninterrupted fulfillment of original no-retry protocol. No population inference."},
        "unmeasured_interruption_cost": "Active work without a completed timing receipt and VM recovery wall time are not reconstructed; all-attempt ratios cover recorded request costs only."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--engine-archive", type=Path, default=base.SQJ / "source-final")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    result = analyze(args.original, args.recovery, args.engine_archive)
    import json
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        if args.check:
            base.same(args.output.read_text(encoding="utf-8"), encoded, "recovered analysis differs")
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
    else:
        print(encoded, end="")
    return int(not result["completed"])


if __name__ == "__main__":
    raise SystemExit(main())
