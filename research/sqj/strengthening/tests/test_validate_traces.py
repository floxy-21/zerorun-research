"""Small synthetic provenance fixtures; never execute recorded tool actions."""
from collections import Counter
from copy import deepcopy
import json
from urllib.parse import urlencode

import pytest

from research.sqj.strengthening import trace_analysis as analyzer
from research.sqj.strengthening import validate_traces as validator


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return raw


@pytest.fixture
def collection(tmp_path, monkeypatch):
    """Two tiny rows exercise receipt semantics, not the frozen 128-row sample."""
    monkeypatch.setattr(validator, "protocol_indices", lambda mode: [10, 11])
    metadata = {"sha": validator.REVISION, "cardData": {"license": "cc-by-4.0"}}
    raw = write_json(tmp_path / "metadata-before.json", metadata)
    write_json(tmp_path / "metadata-after.json", metadata)
    monkeypatch.setattr(validator, "METADATA", validator.sha(raw))
    notices, notice_hashes = [], {}
    for name in ("README.md", "LICENSE"):
        path = tmp_path / "source-notices" / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(name.encode())
        notice_hashes[name] = validator.sha(path.read_bytes())
        notices.append({"path": "source-notices/" + name, "sha256": notice_hashes[name], "bytes": len(name),
                        "url": f"https://huggingface.co/datasets/{validator.DATASET}/resolve/{validator.REVISION}/{name}"})
    monkeypatch.setattr(validator, "NOTICES", notice_hashes)
    selection = {"selected_indices": [10, 11], "population_rows": validator.POPULATION,
        "dataset": validator.DATASET, "selection_mode": "cohort", "selection_seed": validator.SEED,
        "collector_sha256": validator.COLLECTOR_V2, "observed_revision": validator.REVISION,
        "license": "CC-BY-4.0", "selection_uses_outcomes_or_repeat_counts": False,
        "immutable_revision_specific_endpoint_claimed": False}
    write_json(tmp_path / "selection.json", selection)
    receipt = {"sha256": validator.METADATA, "revision": validator.REVISION,
               "url": "https://huggingface.co/api/datasets/" + validator.DATASET}
    manifest = {**selection, "started_utc": "2026-09-06T10:00:00+00:00", "completed_utc": "2026-09-06T10:01:00+00:00",
        "final_metadata_valid": True, "workers": 1, "minimum_request_interval_seconds": 4,
        "metadata_before": receipt, "metadata_after": deepcopy(receipt), "source_notices": notices,
        "successful_downloads": 2, "unavailable_downloads": 0, "entries": []}
    for index in (10, 11):
        row = {"trajectory_id": f"t{index}", "repo": "o/r", "instance_id": f"o__r-{index}", "resolved": 0}
        relative = f"rows/row-{index:05d}.json"
        raw = write_json(tmp_path / relative, {"partial": False, "num_rows_total": validator.POPULATION,
                         "rows": [{"row_idx": index, "truncated_cells": [], "row": row}]})
        query = urlencode({"dataset": validator.DATASET, "config": "default", "split": "train", "offset": index, "length": 1})
        manifest["entries"].append({"row_index": index, "path": relative,
            "url": "https://datasets-server.huggingface.co/rows?" + query, "sha256": validator.sha(raw), "bytes": len(raw),
            "requested_utc": "2026-09-06T10:00:10+00:00", "completed_utc": "2026-09-06T10:00:11+00:00",
            "status": "ok", "partial": False, "truncated_cells": [], "response_headers": {"x-revision": validator.REVISION}})
    write_json(tmp_path / "collection.json", manifest)
    return tmp_path, manifest


def audit(directory):
    return validator.audit_collection(directory, mode="cohort", producer_sha=validator.COLLECTOR_V2, headers_required=True)


def test_protocol_sample_is_exact_disjoint_and_outcome_independent():
    indices = validator.protocol_indices("cohort")
    assert len(indices) == len(set(indices)) == 128
    assert indices == sorted(indices)
    assert indices[0] == 470
    assert not set(indices) & set(validator.protocol_indices("pilot"))
    with pytest.raises(validator.TraceValidationError):
        validator.protocol_indices("replacement")


@pytest.mark.parametrize("path", ["", "..", "../outside", "/absolute", "C:/absolute", "C:relative"])
def test_nonlocal_paths_rejected(tmp_path, path):
    with pytest.raises(validator.TraceValidationError):
        validator.read_file(tmp_path, path)


def test_missing_and_modified_bound_file_rejected(tmp_path):
    with pytest.raises(validator.TraceValidationError):
        validator.bound_file(tmp_path, "missing", "0" * 64)
    (tmp_path / "present").write_bytes(b"changed")
    with pytest.raises(validator.TraceValidationError):
        validator.bound_file(tmp_path, "present", validator.sha(b"original"))


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'not JSON'])
def test_strict_json_rejects_ambiguity(raw):
    with pytest.raises(validator.TraceValidationError):
        validator.load(raw)


def test_complete_synthetic_receipts_validate(collection):
    directory, _ = collection
    checked = audit(directory)
    assert checked["selected_rows"] == checked["successful_downloads"] == 2
    assert len(checked["identities"]) == 2
    assert checked["failures"] == []


@pytest.mark.parametrize("change", [
    lambda m: m.update(selected_indices=[10, 12]),
    lambda m: m.update(final_metadata_valid=False),
    lambda m: m.update(collector_sha256="0" * 64),
    lambda m: m.update(successful_downloads=1),
    lambda m: m.update(source_notices=[]),
    lambda m: m["metadata_after"].update(revision="different"),
    lambda m: m["entries"].reverse(),
    lambda m: m["entries"][0].update(response_headers={}),
    lambda m: m["entries"][0].update(bytes=1),
    lambda m: m["entries"][0].update(path="../other"),
    lambda m: m["entries"][0].update(requested_utc="2026-09-06T10:00:10"),
    lambda m: m["entries"][0].update(completed_utc="2026-09-06T09:00:00+00:00"),
])
def test_corrupt_receipts_fail_closed(collection, change):
    directory, manifest = collection
    change(manifest)
    write_json(directory / "collection.json", manifest)
    with pytest.raises(validator.TraceValidationError):
        audit(directory)


@pytest.mark.parametrize("change", [
    lambda r: r.update(partial=True),
    lambda r: r.update(num_rows_total=1),
    lambda r: r["rows"][0].update(row_idx=77),
    lambda r: r["rows"][0].update(truncated_cells=["trajectory"]),
    lambda r: r["rows"][0]["row"].update(resolved=True),
    lambda r: r.update(rows=[None]),
])
def test_even_rehashed_malformed_raw_rows_rejected(collection, change):
    directory, manifest = collection
    entry = manifest["entries"][0]
    path = directory / entry["path"]
    response = json.loads(path.read_bytes())
    change(response)
    raw = write_json(path, response)
    entry.update(sha256=validator.sha(raw), bytes=len(raw))
    write_json(directory / "collection.json", manifest)
    with pytest.raises(validator.TraceValidationError):
        audit(directory)


def test_failure_cannot_hide_unbound_response(collection):
    directory, manifest = collection
    entry = manifest["entries"][0]
    entry.update(status="unavailable", error="HTTP Error 429")
    entry.pop("sha256")
    manifest.update(successful_downloads=1, unavailable_downloads=1)
    write_json(directory / "collection.json", manifest)
    with pytest.raises(validator.TraceValidationError, match="unbound"):
        audit(directory)


def action(identifier, name="execute_bash", arguments=None, content=None):
    arguments = {"command": "pytest tests/"} if arguments is None else arguments
    content = "2 passed in 0.1s\n[Command finished with exit code 0]" if content is None else content
    return [{"role": "assistant", "tool_calls": [{"id": identifier, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
            {"role": "tool", "name": name, "tool_call_id": identifier, "content": content}]


def report_fixture():
    episodes, identities = [], []
    barriers = [[], action("edit", "str_replace_editor", {"command": "str_replace", "path": "/a", "old_str": "x", "new_str": "y"}, "The file /a has been edited."),
                action("edit", "str_replace_editor", {"command": "str_replace", "path": "/a", "old_str": "x", "new_str": "y"}, "ERROR: no replacement"),
                action("shell", arguments={"command": "unknown_tool"})]
    for index, between in enumerate(barriers):
        identity = {"row_index": index, "trajectory_id": f"t{index}", "repo": "o/r", "instance_id": "o__r-1", "resolved": 0}
        identities.append(identity)
        row = {**identity, "trajectory": action("one") + between + action("two"), "exit_status": "submit"}
        episodes.append(analyzer.analyze_episode(row, index))
    identities.append({"row_index": 4, "trajectory_id": "t4", "repo": "x/y", "instance_id": "x__y-2", "resolved": 1})
    totals = Counter()
    for episode in episodes:
        totals.update(episode["totals"])
    return {"selected_rows": 5, "valid_episodes": 4, "episodes": episodes,
        "unavailable_rows": [{"row_index": 4, "reason": "unmatched_call_or_observation"}],
        "invalid_coverage_reasons": {"unmatched_call_or_observation": 1}, "totals": dict(totals),
        "unsupported_bash_reasons": {"not_direct_pytest": 1},
        "candidate_repeat_pairs": [p for e in episodes for p in e["candidate_repeat_pairs"]]}, identities


def test_summary_preserves_exclusions_distinct_units_and_nonhit_pairs():
    report, identities = report_fixture()
    summary = validator.summarize_report(report, identities)
    assert summary["selected_episode_rows"] == 5
    assert summary["valid_episode_rows"] == 4
    assert summary["selected_distinct_repositories"] == summary["selected_distinct_issues"] == 2
    assert summary["valid_distinct_repositories"] == summary["valid_distinct_issues"] == 1
    assert summary["issues_with_multiple_selected_episodes"] == 1
    assert summary["additional_selected_episodes_of_repeated_issues"] == 3
    assert summary["per_episode"][-1]["totals"] is None
    assert summary["per_repository"][-1]["valid_episode_rows"] == 0
    assert summary["repeat_pair_partition"] == {"no_intervening_barrier": 1, "includes_reported_editor_mutation": 1,
        "other_editor_attempt_without_reported_mutation": 1, "other_unmeasured_effects": 1}
    pair = summary["no_intervening_barrier_pairs"][0]
    assert pair["both_recorded_shell_exit_zero"]
    assert pair["cache_hit_established"] is pair["source_unchanged_established"] is False


@pytest.mark.parametrize("change", [
    lambda r: r.update(valid_episodes=99),
    lambda r: r["unavailable_rows"].append(deepcopy(r["unavailable_rows"][0])),
    lambda r: r.update(invalid_coverage_reasons={}),
    lambda r: r["totals"].update(direct_pytest_calls=99),
    lambda r: r["candidate_repeat_pairs"].pop(),
    lambda r: r["episodes"][0]["candidate_repeat_pairs"][0].update(cache_hit_established=True),
    lambda r: r["episodes"][0].update(recorded_resolved=1),
    lambda r: r["episodes"][0]["events"].append(deepcopy(r["episodes"][0]["events"][0])),
    lambda r: r["episodes"][0]["candidate_repeat_pairs"][0].update(intervening_barrier_counts={"invented": 1}),
])
def test_summary_rejects_denominator_and_interpretation_changes(change):
    report, identities = report_fixture()
    change(report)
    with pytest.raises(validator.TraceValidationError):
        validator.summarize_report(report, identities)


def test_duplicate_raw_identity_rejected():
    report, identities = report_fixture()
    identities[-1] = deepcopy(identities[0])
    with pytest.raises(validator.TraceValidationError):
        validator.summarize_report(report, identities)
