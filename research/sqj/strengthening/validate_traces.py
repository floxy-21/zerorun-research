"""Reconcile frozen public trace evidence; never execute a recorded action.

This validator intentionally pins the completed observation snapshots. It is
not an acquisition script and cannot silently replace failed or excluded rows.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import stat
from urllib.parse import urlencode

from research.sqj.strengthening import trace_analysis

ROOT = Path(__file__).resolve().parent
DATASET = "nebius/SWE-rebench-openhands-trajectories"
REVISION = "35455389ab51bf5e2306bfd436ef72d0f98bf882"
POPULATION = 67074
SEED = "zerorun-openhands-observation-v1"
COLLECTOR_V1 = "06a63605e186d11949d4e8d2f4a06166150069a00259e4dbfb79e82f186150c8"
COLLECTOR_V2 = "56a32a8e1b27a3f37d889004c558fd14f62f6d2415730ba7cb90aef321ba6907"
ANALYZER = "89682c29f2d8720f6191325d91c5798a10d3f05ac3f7c26741eb784f9c531352"
METADATA = "516bd153891714e44abb999bac398f2f3e20a40ebbd022da4b1540571f914d8c"
NOTICES = {
    "README.md": "90fa586c747d9ed1022c28847439489576049cde7de1fdfb80d24b2a0ae77b32",
    "LICENSE": "9e5f1b3c610b9c2da5c313bf81d577a7d1acec686bdb0384edefa6df0f90cd94",
}
FROZEN = {
    "collect_traces.py": COLLECTOR_V2,
    "producers/collect_traces-v1.py": COLLECTOR_V1,
    "producers/collect_traces-v2.py": COLLECTOR_V2,
    "trace_analysis.py": ANALYZER,
    "producers/trace_analysis-v1.py": ANALYZER,
    "evidence/trace-pilot-analysis-v1.json": "3c5778e28654c867e541c592974f88b6a3dd47ff298d24e09c8b9783ac26f5ce",
    "evidence/trace-cohort-analysis-v1.json": "f020302eb1b287ed7c93e261c735d6c92b317da483b1c9f05a013022d5475c22",
    "evidence/trace-pilot-1/collection.json": "341c404b6c4cec1a3cd77c506a1fb986079f817e8a811358599c7fcd613de151",
    "evidence/trace-pilot-1/selection.json": "92d47dcfcf5d967e8f2398bbe268774890c9a0d9e8180325252ffded668c9bec",
    "evidence/trace-cohort-1/collection.json": "abe10dee8499b8db57be9b2ba6069ff6dd1625468c2f5c51fb30b72ac6cf7d09",
    "evidence/trace-cohort-1/selection.json": "ac7f8b85d21f4859e54478103299cca4fd4ea795109fd519ec7f1bd246787fdc",
    "evidence/trace-cohort-2/collection.json": "406b8a329ebdd710b3313e6a7e7aa761251d0ecb65180a6977c782e115e855b5",
    "evidence/trace-cohort-2/selection.json": "c7e116698b436397e95bed4ebad088e9774126fb3827449db48b84417c09ea0b",
}


class TraceValidationError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise TraceValidationError(message)


def read_file(root, relative):
    path = Path(relative)
    require(bool(path.parts) and not path.is_absolute() and not path.drive
            and all(part not in {".", ".."} for part in path.parts), "nonlocal evidence path")
    cursor = Path(root)
    try:
        for part in path.parts:
            cursor = cursor / part
            details = cursor.lstat()
            require(not stat.S_ISLNK(details.st_mode) and not getattr(details, "st_file_attributes", 0) & 0x400,
                    "linked evidence path")
        require(stat.S_ISREG(details.st_mode), "evidence is not a regular file")
        return cursor.read_bytes()
    except OSError as exc:
        raise TraceValidationError(f"missing/unreadable evidence: {relative}") from exc


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bound_file(root, relative, expected):
    raw = read_file(root, relative)
    require(sha(raw) == expected, f"frozen SHA mismatch: {relative}")
    return raw


def load(raw):
    try:
        return trace_analysis.strict_json(raw)
    except (ValueError, TypeError) as exc:
        raise TraceValidationError("invalid evidence JSON") from exc


def protocol_indices(mode):
    if mode == "pilot":
        return list(range(10))
    require(mode == "cohort", "unknown cohort")
    ranking = sorted(range(10, POPULATION), key=lambda index: hashlib.sha256(f"{SEED}:{index}".encode("utf-8")).digest())
    return sorted(ranking[:128])


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        require(parsed.tzinfo is not None, "timestamp lacks timezone")
        return parsed
    except (TypeError, ValueError) as exc:
        raise TraceValidationError("invalid timestamp") from exc


def audit_collection(directory, *, mode, producer_sha, headers_required):
    """Validate all acquired rows, including ones later excluded by analysis."""
    collection = load(read_file(directory, "collection.json"))
    selection = load(read_file(directory, "selection.json"))
    expected = protocol_indices(mode)
    require(isinstance(collection, dict) and isinstance(selection, dict), "invalid collection documents")
    require(all(collection.get(key) == value for key, value in selection.items()), "selection differs from collection")
    for record in (selection, collection):
        require(record.get("selected_indices") == expected, "selection differs from fixed protocol indices")
        require(record.get("population_rows") == POPULATION and record.get("dataset") == DATASET,
                "dataset/population mismatch")
        require(record.get("selection_mode") == mode and record.get("selection_seed") == SEED, "selection rule mismatch")
        require(record.get("collector_sha256") == producer_sha, "collection producer mismatch")
        require(record.get("observed_revision") == REVISION and record.get("license") == "CC-BY-4.0", "source identity/license mismatch")
        require(record.get("selection_uses_outcomes_or_repeat_counts") is False
                and record.get("immutable_revision_specific_endpoint_claimed") is False, "unsupported selection/revision claim")
    started, completed = instant(collection.get("started_utc")), instant(collection.get("completed_utc"))
    require(started <= completed, "collection timestamps reversed")
    if headers_required:
        require(collection.get("final_metadata_valid") is True, "final source metadata did not validate")
        require(collection.get("workers") == 1 and collection.get("minimum_request_interval_seconds") == 4,
                "recovery retrieval policy changed")
    for label in ("before", "after"):
        raw = bound_file(directory, f"metadata-{label}.json", METADATA)
        metadata = load(raw)
        require(isinstance(metadata, dict) and isinstance(metadata.get("cardData"), dict), "invalid source metadata")
        receipt = collection.get("metadata_" + label)
        require(isinstance(receipt, dict) and receipt.get("sha256") == sha(raw), "metadata receipt mismatch")
        require(metadata.get("sha") == receipt.get("revision") == REVISION, "final metadata revision mismatch")
        require(metadata.get("cardData", {}).get("license") == "cc-by-4.0", "metadata license mismatch")
        require(receipt.get("url") == "https://huggingface.co/api/datasets/" + DATASET, "metadata source URL mismatch")
    source_notices = []
    for name, digest in NOTICES.items():
        relative = "source-notices/" + name
        raw = bound_file(directory, relative, digest)
        notice = {"path": relative, "sha256": digest, "bytes": len(raw),
                  "url": f"https://huggingface.co/datasets/{DATASET}/resolve/{REVISION}/{name}"}
        source_notices.append(notice)
    if headers_required:
        require(collection.get("source_notices") == source_notices, "source notice receipts mismatch")

    entries = collection.get("entries")
    require(isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries), "missing acquisition entries")
    require([entry.get("row_index") for entry in entries] == expected, "missing/duplicate/reordered acquisition entries")
    identities, raw_rows, failures, hashes = [], {}, [], {}
    for entry in entries:
        index = entry["row_index"]
        relative = f"rows/row-{index:05d}.json"
        query = urlencode({"dataset": DATASET, "config": "default", "split": "train", "offset": index, "length": 1})
        require(entry.get("url") == "https://datasets-server.huggingface.co/rows?" + query
                and entry.get("path") == relative, "row request identity mismatch")
        require(started <= instant(entry.get("requested_utc")) <= instant(entry.get("completed_utc")) <= completed,
                "row timestamps outside collection")
        if entry.get("status") != "ok":
            require(entry.get("status") == "unavailable" and isinstance(entry.get("error"), str), "unexplained acquisition failure")
            failures.append({"row_index": index, "error": entry["error"]})
            if "sha256" not in entry:
                require(not (directory / relative).exists(), "unbound raw response behind failed entry")
                continue
        raw = bound_file(directory, relative, entry.get("sha256"))
        require(type(entry.get("bytes")) is int and len(raw) == entry["bytes"], "raw byte length mismatch")
        hashes[index] = sha(raw)
        response = load(raw)
        require(isinstance(response, dict), "invalid raw row response")
        require(response.get("partial") is False and response.get("num_rows_total") == POPULATION, "partial/population response mismatch")
        rows = response.get("rows")
        require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict)
                and rows[0].get("row_idx") == index, "raw row identity mismatch")
        require(rows[0].get("truncated_cells") == entry.get("truncated_cells") == [], "truncated row cannot support complete observation")
        if headers_required:
            require(entry.get("partial") is False and isinstance(entry.get("response_headers"), dict)
                    and entry["response_headers"].get("x-revision") == REVISION,
                    "missing/mismatched dataset-server revision header")
        row = rows[0].get("row")
        require(isinstance(row, dict) and all(isinstance(row.get(key), str) and row[key] for key in ("trajectory_id", "repo", "instance_id")),
                "raw episode identity missing")
        require(type(row.get("resolved")) is int and row["resolved"] in (0, 1), "invalid recorded resolution flag")
        identity = {key: row[key] for key in ("trajectory_id", "repo", "instance_id", "resolved")}
        identities.append({"row_index": index, **identity})
        raw_rows[index] = row
    successful = sum(entry["status"] == "ok" for entry in entries)
    require(collection.get("successful_downloads") == successful and collection.get("unavailable_downloads") == len(failures),
            "acquisition denominator mismatch")
    return {"selected_rows": len(expected), "successful_downloads": successful, "failures": failures,
            "identities": identities, "raw_rows": raw_rows, "raw_sha256": hashes,
            "source_notices": source_notices, "server_revision_headers_checked": headers_required}


def summarize_report(report, identities):
    """Separate row/repository/issue denominators and descriptive repeat windows."""
    episodes = report["episodes"]
    by_index = {episode["row_index"]: episode for episode in episodes}
    excluded = {row["row_index"]: row["reason"] for row in report["unavailable_rows"]}
    require(len(by_index) == len(episodes) and len(excluded) == len(report["unavailable_rows"])
            and not set(by_index) & set(excluded), "invalid analysis partition")
    require(report["selected_rows"] == len(episodes) + len(excluded), "analysis denominator mismatch")
    require(len(identities) == report["selected_rows"] and len({item["row_index"] for item in identities}) == len(identities),
            "summary requires acquired identities for every selected row")
    require({item["row_index"] for item in identities} == set(by_index) | set(excluded), "raw/analysis row partition mismatch")
    require(dict(Counter(excluded.values())) == report["invalid_coverage_reasons"], "exclusion reason denominator mismatch")
    require(report["valid_episodes"] == len(episodes), "valid episode denominator mismatch")
    aggregate = Counter()
    for episode in episodes:
        aggregate.update(episode["totals"])
    require(dict(aggregate) == report["totals"], "episode totals differ from report totals")
    require([pair for episode in episodes for pair in episode["candidate_repeat_pairs"]] == report["candidate_repeat_pairs"],
            "flattened pair ledger differs from episodes")
    category_counts, barrier_occurrences, barrier_incidence = Counter(), Counter(), Counter()
    no_barrier, per_episode, repositories = [], [], defaultdict(list)
    for identity in identities:
        index = identity["row_index"]
        episode = by_index.get(index)
        record = {**identity, "analysis_status": "included" if episode else "excluded",
                  "exclusion_reason": excluded.get(index), "totals": episode["totals"] if episode else None}
        per_episode.append(record)
        repositories[identity["repo"]].append(record)
        if episode is None:
            continue
        require(all(episode[key] == identity[key] for key in ("trajectory_id", "repo", "instance_id")), "analyzed episode identity mismatch")
        require(episode["recorded_resolved"] == identity["resolved"], "recorded episode outcome mismatch")
        events = {event["call_id"]: event for event in episode["events"]}
        require(len(events) == len(episode["events"]), "duplicate event identifiers")
        for pair in episode["candidate_repeat_pairs"]:
            require(pair["row_index"] == index and pair["trajectory_id"] == identity["trajectory_id"], "pair episode identity mismatch")
            require(pair.get("cache_hit_established") is False and pair.get("source_unchanged_established") is False,
                    "observed repetition was relabeled a validated hit")
            require(pair["prior_call_id"] in events and pair["current_call_id"] in events, "pair event is missing")
            prior, current = events[pair["prior_call_id"]], events[pair["current_call_id"]]
            require(prior["command"] == current["command"] == pair["exact_command"], "repeat command mismatch")
            counts = Counter(item["barrier"] for item in pair["intervening_barriers"])
            require(dict(counts) == pair["intervening_barrier_counts"], "repeat barrier ledger mismatch")
            barrier_occurrences.update(counts)
            barrier_incidence.update(counts.keys())
            if not counts:
                category = "no_intervening_barrier"
                no_barrier.append({"row_index": index, "trajectory_id": identity["trajectory_id"],
                    "repo": identity["repo"], "instance_id": identity["instance_id"],
                    "prior_call_id": pair["prior_call_id"], "current_call_id": pair["current_call_id"],
                    "prior_recorded_shell_exit_code": prior["recorded_shell_exit_code"],
                    "current_recorded_shell_exit_code": current["recorded_shell_exit_code"],
                    "both_recorded_shell_exit_zero": prior["recorded_shell_exit_code"] == current["recorded_shell_exit_code"] == 0,
                    "prior_observation_clipped": prior["observation_clipped"], "current_observation_clipped": current["observation_clipped"],
                    "cache_hit_established": False, "source_unchanged_established": False})
            elif counts["editor_reported_mutation"]:
                category = "includes_reported_editor_mutation"
            elif any(key.startswith("editor_") for key in counts):
                category = "other_editor_attempt_without_reported_mutation"
            else:
                category = "other_unmeasured_effects"
            category_counts[category] += 1
    total_pairs = sum(category_counts.values())
    require(total_pairs == report["totals"]["exact_command_repeat_pairs"] == len(report["candidate_repeat_pairs"]), "repeat denominator mismatch")
    per_repository = []
    for repo, records in sorted(repositories.items()):
        included = [record for record in records if record["analysis_status"] == "included"]
        totals = Counter()
        for record in included:
            totals.update(record["totals"])
        per_repository.append({"repo": repo, "selected_episode_rows": len(records), "valid_episode_rows": len(included),
            "excluded_episode_rows": len(records) - len(included),
            "selected_distinct_issues": len({record["instance_id"] for record in records}),
            "valid_distinct_issues": len({record["instance_id"] for record in included}), "totals_over_valid_episodes": dict(sorted(totals.items()))})
    valid_identities = [item for item in identities if item["row_index"] in by_index]
    issue_counts = Counter((item["repo"], item["instance_id"]) for item in identities)
    valid_issue_counts = Counter((item["repo"], item["instance_id"]) for item in valid_identities)
    per_issue = [{"repo": repo, "instance_id": issue, "selected_episode_rows": count,
                  "valid_episode_rows": valid_issue_counts[repo, issue],
                  "excluded_episode_rows": count - valid_issue_counts[repo, issue]}
                 for (repo, issue), count in sorted(issue_counts.items())]
    return {"selected_episode_rows": report["selected_rows"], "valid_episode_rows": len(episodes),
        "excluded_episode_rows": len(excluded), "exclusion_reasons": report["invalid_coverage_reasons"],
        "selected_distinct_trajectories": len({item["trajectory_id"] for item in identities}),
        "selected_distinct_repositories": len(repositories),
        "valid_distinct_repositories": len({item["repo"] for item in valid_identities}),
        "selected_distinct_issues": len(issue_counts), "valid_distinct_issues": len(valid_issue_counts),
        "issues_with_multiple_selected_episodes": sum(count > 1 for count in issue_counts.values()),
        "additional_selected_episodes_of_repeated_issues": sum(count - 1 for count in issue_counts.values()),
        "selected_recorded_resolution_counts": dict(sorted(Counter(str(item["resolved"]) for item in identities).items())),
        "valid_recorded_resolution_counts": dict(sorted(Counter(str(item["resolved"]) for item in valid_identities).items())),
        "totals_over_valid_episodes": report["totals"], "unsupported_bash_reasons": report["unsupported_bash_reasons"],
        "repeat_pairs": total_pairs, "repeat_pair_partition": dict(sorted(category_counts.items())),
        "pairs_containing_each_barrier_kind_overlapping": dict(sorted(barrier_incidence.items())),
        "barrier_occurrences_across_pair_windows_not_unique_events": dict(sorted(barrier_occurrences.items())),
        "no_intervening_barrier_pairs": no_barrier, "no_intervening_barrier_pair_count": len(no_barrier),
        "per_episode": per_episode, "per_repository": per_repository, "per_issue": per_issue,
        "cache_hit_established": False, "source_unchanged_established": False,
        "interpretation": "Recorded exact-command repetitions and barrier categories only; prior test effects, complete state, and cached-result eligibility remain unverified."}


def build_summary(root=ROOT):
    root = Path(root)
    bindings = []
    for relative, expected in FROZEN.items():
        raw = bound_file(root, relative, expected)
        bindings.append({"path": relative, "sha256": expected, "bytes": len(raw)})
    pilot = audit_collection(root / "evidence/trace-pilot-1", mode="pilot", producer_sha=COLLECTOR_V1, headers_required=False)
    first = audit_collection(root / "evidence/trace-cohort-1", mode="cohort", producer_sha=COLLECTOR_V1, headers_required=False)
    main = audit_collection(root / "evidence/trace-cohort-2", mode="cohort", producer_sha=COLLECTOR_V2, headers_required=True)
    require(pilot["successful_downloads"] == 10 and main["successful_downloads"] == 128, "completed primary/pilot acquisition changed")
    require(first["successful_downloads"] == 32 and len(first["failures"]) == 96
            and all("HTTP Error 429" in failure["error"] for failure in first["failures"]), "original rate-limited attempt missing/changed")
    require(all(main["raw_rows"][index] == row for index, row in first["raw_rows"].items()), "recovery changed previously observed episode payload")
    reports = {}
    for label, directory, report_path, audited in (
        ("pilot", "evidence/trace-pilot-1", "evidence/trace-pilot-analysis-v1.json", pilot),
        ("main", "evidence/trace-cohort-2", "evidence/trace-cohort-analysis-v1.json", main),
    ):
        recorded = load(read_file(root, report_path))
        regenerated = trace_analysis.analyze_collection(root / directory)
        require(recorded == regenerated, f"{label} frozen report differs from analyzer rerun")
        reports[label] = summarize_report(recorded, audited["identities"])
    return {"schema": "zerorun.trace-summary.v1", "analysis_kind": "recorded_action_descriptive_only",
        "validator_sha256": sha(Path(__file__).read_bytes()), "source": {"dataset": DATASET, "observed_revision": REVISION,
            "license": "CC-BY-4.0", "population_rows": POPULATION, "main_selected_indices": protocol_indices("cohort"),
            "selection_seed": SEED, "main_headers_match_revision": True,
            "revision_specific_endpoint_claimed": False, "source_notices": main["source_notices"]},
        "frozen_bindings": bindings, "main": reports["main"], "pilot": reports["pilot"],
        "retained_first_attempt": {"selected_rows": 128, "successful_downloads": 32, "http_429_unavailable_rows": 96,
            "failures": first["failures"], "same_protocol_indices": True, "previously_observed_episode_payloads_unchanged": True,
            "same_raw_response_hash_count": sum(main["raw_sha256"][index] == digest for index, digest in first["raw_sha256"].items()),
            "pooled_into_primary_analysis": False},
        "claims": {"recorded_actions_executed": False, "valid_cache_hits_measured": False,
            "agent_speedup_measured": False, "independent_population_generalization_claimed": False,
            "pilot_pooled_with_main": False, "failed_or_excluded_rows_replaced": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "evidence/trace-summary-v1.json")
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    summary = build_summary()
    raw = (json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if options.check:
        require(options.output.is_file() and options.output.read_bytes() == raw, "generated trace summary is missing/stale")
    else:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        with options.output.open("xb") as stream:
            stream.write(raw)
    print(json.dumps({"validated": True, "main_selected": summary["main"]["selected_episode_rows"],
        "main_valid": summary["main"]["valid_episode_rows"], "main_repeat_pairs": summary["main"]["repeat_pairs"],
        "main_no_intervening_barrier_pairs": summary["main"]["no_intervening_barrier_pair_count"]}))


if __name__ == "__main__":
    main()
