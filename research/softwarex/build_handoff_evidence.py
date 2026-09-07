"""Reconcile available controlled-handoff records; absence is never success.

build() is read-only. The CLI writes only its explicit output (or checks it).
No downloaded repository is imported, extracted, executed or authorized here.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys

from research.softwarex.handoff_acquisition_v1 import collect as acquisition
from research.softwarex.handoff_acquisition_recovery_v1 import recover as recovery
from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1 import validate as v1
from research.softwarex.handoff_image_v2 import validate as v2

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = "research/softwarex/evidence/"
ACQUISITION = EVIDENCE + "handoff-acquisition-recovery-v1"
IMAGE = EVIDENCE + "handoff-image-build-v2b"
FAILED_IMAGE = EVIDENCE + "handoff-image-build-v2"
REVISION_RECORDS = EVIDENCE + "application-revision-20260907-v2/record-only"
REVISION_PREFLIGHT = EVIDENCE + "application-revision-preflight-v1/record-only"
HOST_INTERRUPTION = EVIDENCE + "application-revision-20260907-v2/host-interruption.json"
REVISION_DRIVER_SHA = "486e5bd331e0fae780c6792ea4d669b8406cfafced6370f735538a92bd1a52f4"
REVISION_AMENDMENT_SHA = "af90688f3f03c7bd9d19436f3233f981208a05ed1655b0ff2bef17593a80d74c"
PREFLIGHT_DRIVER_SHA = "391af8fd86a605520b66d5b1a094087a31a14e7dcca30d8e97fa009ed5164e34"
PREFLIGHT_AMENDMENT_SHA = "df82a9914742367826886389657066ae00fb41e586de019c4da824892928c72d"
RUNS = {
    "v1_pilot": (EVIDENCE + "handoff-pilot-v1", "v1", "pilot"),
    "v1_main": (EVIDENCE + "handoff-main-v1", "v1", "main"),
    "v2_pilot": (EVIDENCE + "handoff-pilot-v2", "v2", "pilot"),
    "v2_pilot_repeat": (EVIDENCE + "handoff-pilot-v2-repeat-20260907", "v2", "pilot"),
    "v2_main": (EVIDENCE + "handoff-main-v2", "v2", "main"),
    "v2_main_extended": (REVISION_RECORDS + "/handoff-main-repeat-v1", "v2", "main"),
}
FRESH_IMAGE = REVISION_RECORDS + "/handoff-image-build-fresh-v1"
FRESH_PILOT = REVISION_RECORDS + "/handoff-pilot-fresh-image-v1"
OUTPUT = "research/softwarex/generated/handoff-evidence-v1.json"
AGENTS = {"pilot": (EVIDENCE + "agent-producer-pilot-v1", EVIDENCE + "agent-evaluation-pilot-v2"),
          "main": (EVIDENCE + "agent-producer-main-v1", EVIDENCE + "agent-evaluation-main-v1")}
PREVIOUS_AGENT_EVALUATION = EVIDENCE + "agent-evaluation-pilot-v1"
AMENDMENTS = ("research/softwarex/HANDOFF_IMAGE_RECOVERY_AMENDMENT.md",
              "research/softwarex/AGENT_AUTHENTICATION_AMENDMENT.md",
              "research/softwarex/APPLICATION_REVISION_AMENDMENT.md")


def source_inputs(root=ROOT):
    """Bind public study code and record-only inputs, including final source archives.

    Large acquisition parquet files, generated workspaces, image build contexts,
    registry stores and private client homes are deliberately not publication
    inputs. Agent files use the evaluator's exact capture allowlist.
    """
    from research.softwarex.agent_handoff_evaluation_v1.run import FILES
    from research.softwarex.handoff_image_v2.export import EXCLUDED

    root = Path(root)
    paths = {"research/softwarex/build_handoff_evidence.py"}
    verifier = "research/softwarex/verify_application_revision.py"
    if (root / verifier).is_file():
        paths.add(verifier)
    if (root / HOST_INTERRUPTION).is_file():
        paths.add(HOST_INTERRUPTION)
    paths.update(relative for relative in AMENDMENTS if (root / relative).is_file())
    folders = ("handoff_acquisition_v1", "handoff_acquisition_recovery_v1", "handoff_v1",
               "handoff_image_v2", "agent_handoff_v1", "agent_handoff_evaluation_v1")
    for name in folders:
        folder = root / "research/softwarex" / name
        if folder.is_dir():
            h.require(not folder.is_symlink(), "linked handoff source directory")
            paths.update(path.relative_to(root).as_posix() for path in folder.iterdir()
                         if path.suffix in {".py", ".md"} and path.is_file())
    excluded = set(EXCLUDED) | {"data", "workspaces", ".codex", "private", "authority", "authorities"}
    record_dirs = {ACQUISITION, IMAGE, FAILED_IMAGE, FRESH_IMAGE, FRESH_PILOT, REVISION_RECORDS, REVISION_PREFLIGHT,
                   PREVIOUS_AGENT_EVALUATION, *(spec[0] for spec in RUNS.values()),
                   *(spec[1] for spec in AGENTS.values())}
    for relative in sorted(record_dirs):
        h.literal(relative)
        base = root / relative
        if not base.exists():
            continue
        h.require(base.is_dir() and not base.is_symlink(), "invalid handoff evidence directory")
        for current, directories, files in os.walk(base, followlinks=False):
            directories[:] = sorted(name for name in directories if name not in excluded)
            for name in directories:
                h.require(not (Path(current) / name).is_symlink(), "linked handoff evidence directory")
            for name in sorted(files):
                path = Path(current) / name
                if path.suffix in {".json", ".xml", ".log", ".py", ".md", ".txt"} or name == "LICENSE":
                    paths.add(path.relative_to(root).as_posix())
    if (root / ACQUISITION / "receipt.json").is_file():
        acquisition, _ = acquisition_summary(root / ACQUISITION)
        paths.update(ACQUISITION + "/" + case["source_archive"]["path"] for case in acquisition["cases"]
                     if case["source_archive"] is not None)
    for phase, (relative, _) in AGENTS.items():
        base = root / relative
        if not base.exists():
            continue
        h.require(base.is_dir() and not base.is_symlink(), "invalid agent record directory")
        freeze = base / "freeze.json"
        if freeze.is_file():
            paths.add(freeze.relative_to(root).as_posix())
        for index in range(2 if phase == "pilot" else 6):
            for name in FILES:
                path = base / f"case-{index:02d}" / name
                if path.exists():
                    paths.add(path.relative_to(root).as_posix())
    rows = []
    for relative in sorted(paths):
        cursor = root
        for part in h.literal(relative):
            cursor /= part
            h.require(not cursor.is_symlink(), "linked handoff publication input")
        raw = h.ordinary(root / relative, 192 * 1024 * 1024)
        rows.append({"path": relative, "bytes": len(raw), "sha256": h.sha(raw)})
    return rows


def bound(base, row, limit=acquisition.METADATA_LIMIT):
    h.require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
              and type(row["bytes"]) is int, "invalid file binding")
    v1.exact(recovery.file_record(base, row["path"], limit), row, "acquisition file bytes differ")
    return base / row["path"]


def read_bound(base, row):
    return v1.read(bound(base, row))


def filename_record(base, relative):
    return recovery.file_record(base, relative, acquisition.METADATA_LIMIT)


def checked_download(base, relative, row, expected_url, limit):
    report = v1.read(base / (relative + ".download.json"))
    h.require(report.get("status") == "ok" and report.get("url") == expected_url
              and report.get("path") == relative and type(report.get("bytes")) is int
              and report["bytes"] == row["bytes"] and report.get("sha256") == row["sha256"]
              and report.get("limit_bytes") == limit and report.get("attempts") == 1,
              "download record disagrees with selected source")


def acquisition_summary(base):
    base = Path(base)
    plan, receipt = v1.read(base / "plan.json"), v1.read(base / "receipt.json")
    expected = acquisition.protocol_plan()
    v1.exact({k: value for k, value in plan.items() if k not in {"started_utc", "transport_recovery"}},
             {k: value for k, value in expected.items() if k != "started_utc"}, "frozen acquisition rules/source differ")
    h.require(receipt.get("schema") == "zerorun.handoff-acquisition.v1"
              and receipt.get("code_executed") is False and receipt.get("archives_extracted") is False,
              "acquisition scope changed")
    v1.exact(read_bound(base, receipt["plan"]), plan, "acquisition plan binding differs")
    h.require(receipt["plan"]["path"] == "plan.json" and receipt.get("pyarrow_version") == "23.0.1",
              "acquisition parser or plan path changed")
    selection = read_bound(base, receipt["selection"])
    h.require(receipt["selection"]["path"] == "selection.json", "selection path changed")
    ledger = selection["repository_ledger"]
    h.require([row["repo"] for row in ledger] == list(acquisition.SHORTLIST), "shortlist denominator/order differs")
    for row in ledger:
        h.require(type(row["valid_count"]) is int and type(row["candidate_count"]) is int
                  and 0 <= row["valid_count"] <= row["candidate_count"] and type(row["selected"]) is bool,
                  "invalid candidate denominator")
    selected_repos = [row["repo"] for row in ledger if row["valid_count"] >= 3][:10]
    h.require([row["repo"] for row in ledger if row["selected"]] == selected_repos,
              "selection does not retain first eligible shortlist repositories")
    h.require(sum(row["candidate_count"] for row in ledger) == selection["candidate_rows"]
              and sum(row["candidate_count"] - row["valid_count"] for row in ledger) == len(selection["invalid_cases"]),
              "candidate/invalid counts differ")
    manifests, ids, availability = {}, {}, {}
    archive_bytes = 0
    case_rows = []
    h.require(set(receipt["manifests"]) == {"pilot", "main"}, "pilot/main manifest missing")
    for phase in ("pilot", "main"):
        manifest = read_bound(base, receipt["manifests"][phase])
        h.require(receipt["manifests"][phase]["path"] == phase + ".json"
                  and manifest["schema"] == "zerorun.handoff-selection.v1" and manifest["phase"] == phase,
                  "selection manifest identity differs")
        v1.exact(manifest["plan"], receipt["plan"], "manifest plan differs")
        v1.exact(manifest["selection"], receipt["selection"], "manifest selection differs")
        cases = manifest["cases"]
        ids[phase] = [case["case_id"] for case in cases]
        h.require(len(ids[phase]) == len(set(ids[phase]))
                  and ids[phase] == [case["case_id"] for case in selection[phase]], "selected IDs missing/duplicated/reordered")
        selected = {case["case_id"]: case for case in selection[phase]}
        available = 0
        for case in cases:
            chosen = selected[case["case_id"]]
            v1.exact({k: case[k] for k in ("case_id", "repo", "base_commit", "targets")},
                     {k: chosen[k] for k in ("case_id", "repo", "base_commit", "targets")}, "selected case identity differs")
            wrapper = read_bound(base, case["metadata"])
            h.require(case["metadata"]["path"] == f"cases/{case['case_id']}/metadata.json"
                      and wrapper["dataset"] == acquisition.DATASET and wrapper["revision"] == acquisition.REVISION
                      and wrapper["config"] == "default" and wrapper["split"] == "test" and wrapper["partial"] is False
                      and len(wrapper["rows"]) == 1 and wrapper["rows"][0]["truncated_cells"] == [], "case metadata incomplete")
            decoded = wrapper["rows"][0]
            actual_case = acquisition.validate_case(decoded["row_idx"], decoded["row"])
            v1.exact({k: value for k, value in actual_case.items() if k != "row"}, chosen, "decoded metadata differs from frozen selection")
            archive = case["source_archive"]
            if archive is None:
                h.require(case.get("disposition") == "UNAVAILABLE_ACQUISITION" and case.get("error"),
                          "missing archive hidden as available")
            else:
                h.require(archive["path"] == f"cases/{case['case_id']}/source.tar.gz"
                          and case.get("disposition") != "UNAVAILABLE_ACQUISITION", "source archive path/disposition differs")
                bound(base, archive, acquisition.ARCHIVE_LIMIT)
                checked_download(base, archive["path"], archive,
                    f"https://codeload.github.com/{case['repo']}/tar.gz/{case['base_commit']}", acquisition.ARCHIVE_LIMIT)
                available += 1
                archive_bytes += archive["bytes"]
            case_rows.append({"phase": phase, "case_id": case["case_id"], "repo": case["repo"],
                "base_commit": case["base_commit"], "metadata": case["metadata"], "source_archive": archive,
                "archive_available": archive is not None, "test_targets": case["targets"]})
        manifests[phase], availability[phase] = manifest, available
    h.require(not set(ids["main"]) & set(ids["pilot"]), "pilot/main overlap")
    h.require(list(dict.fromkeys(case["repo"] for case in selection["main"])) == selected_repos
              and Counter(case["repo"] for case in selection["main"]) == Counter({repo: 3 for repo in selected_repos}),
              "main case count per selected repository changed")
    for repo in selected_repos:
        cases = [case for case in selection["main"] if case["repo"] == repo]
        h.require(cases == sorted(cases, key=lambda case: acquisition.order(case, "main")), "main hash ordering differs")
    h.require(len(ids["pilot"]) <= 2 and all(case["repo"] in selected_repos for case in selection["pilot"])
              and selection["pilot"] == sorted(selection["pilot"], key=lambda case: acquisition.order(case, "pilot")),
              "pilot selection/order differs")
    target_complete = len(selected_repos) == 10 and len(ids["main"]) == 30 and len(ids["pilot"]) == 2
    h.require(selection["selection_complete"] is target_complete, "target shortfall mislabeled")
    for phase, manifest in manifests.items():
        expected_complete = target_complete and availability[phase] == len(ids[phase])
        h.require(manifest["selection_complete"] is target_complete
                  and manifest["acquisition_complete"] is expected_complete, "manifest completeness misstates original target")
    expected_complete = target_complete and all(availability[phase] == len(ids[phase]) for phase in manifests)
    h.require(receipt["completed"] is expected_complete, "acquisition receipt hides target shortfall")
    parquet_rows = receipt["parquets"]
    h.require([row["path"] for row in parquet_rows] == list(acquisition.PARQUETS), "pinned parquet inventory differs")
    for row in parquet_rows:
        h.require(type(row["bytes"]) is int and 0 < row["bytes"] <= acquisition.PARQUET_LIMIT
                  and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid pinned parquet record")
    second = parquet_rows[1]
    checked_download(base, second["path"], second, recovery.parquet_url(second["path"]), acquisition.PARQUET_LIMIT)
    h.require(v1.read(base / (second["path"] + ".download.json"))["parquet_container_magic_ok"] is True,
              "second download lacks complete-container producer check")
    # The large corpus and partial download are not exported; retain their exact
    # hashes and the original failure rather than claiming to have re-read them.
    initial = base / "initial-attempt"
    prior_receipt = v1.read(initial / "receipt.json")
    prior_plan = read_bound(initial, prior_receipt["plan"])
    v1.exact({k: value for k, value in prior_plan.items() if k != "started_utc"},
             {k: value for k, value in expected.items() if k != "started_utc"}, "initial frozen plan changed")
    h.require(prior_receipt["completed"] is False and recovery.SECOND in prior_receipt.get("error", "")
              and "180-second wall limit" in prior_receipt["error"], "initial timeout not retained")
    transport = plan["transport_recovery"]
    h.require(transport["initial_failure_retained"] is True and transport["resume_used"] is False
              and transport["selection_logic_changed"] is False and transport["archive_limits_changed"] is False
              and transport["second_parquet_deadline_between_reads_seconds"] == 600, "transport amendment scope changed")
    prior_rows = {row["path"]: row for row in transport["initial_attempt_files"]}
    h.require(len(prior_rows) == len(transport["initial_attempt_files"]), "duplicate initial input binding")
    for name in recovery.SMALL:
        bound(initial, prior_rows[name])
    for name in ("dataset-before.json", "source-notices/README.md"):
        bound(base, prior_rows[name])
    for field, name in (("metadata_before", "dataset-before.json"), ("source_notice", "source-notices/README.md")):
        v1.exact(receipt[field], prior_rows[name], "reused metadata/notice binding differs")
        bound(base, receipt[field])
    v1.exact(parquet_rows[0], prior_rows[recovery.FIRST], "reused first parquet identity changed")
    amendment_sources = transport["amendment_sources"]
    h.require({row["path"] for row in amendment_sources} == {"__init__.py", "PROTOCOL.md", "recover.py"},
              "recovery source inventory differs")
    for row in amendment_sources:
        bound(recovery.HERE, row)
    completion = v1.read(base / "recovery-completion.json")
    h.require(completion["schema"] == "zerorun.handoff-acquisition-transport-recovery-completion.v1"
              and completion["original_inputs_unchanged"] is True and completion["original_failure_retained"] is True
              and completion["completed"] is expected_complete and completion["selection_logic_changed"] is False
              and completion["code_executed"] is False and completion["archives_extracted"] is False,
              "recovery completeness/scope differs")
    bound(base, completion["new_receipt"])
    bound(base, completion["new_plan"])
    bound(initial, completion["original_receipt"])
    return {"schema": "zerorun.handoff-acquisition-reconciliation.v1", "reconciled": True,
        "dataset": acquisition.DATASET, "revision": acquisition.REVISION, "recorded_total_corpus_rows": acquisition.TOTAL_ROWS,
        "planned_main_cases": 30, "planned_main_repositories": 10, "selected_main_cases": len(ids["main"]),
        "selected_main_repositories": len(selected_repos), "selected_pilot_cases": len(ids["pilot"]),
        "original_target_complete": target_complete, "recorded_acquisition_completed": receipt["completed"],
        "all_selected_archives_available": all(availability[phase] == len(ids[phase]) for phase in manifests),
        "archive_availability": availability, "selected_archive_bytes_rechecked": archive_bytes,
        "repository_ledger": ledger, "invalid_cases": selection["invalid_cases"], "cases": case_rows,
        "initial_transport_failure_preserved": True, "original_raw_preservation": "producer-reported; initial small records rechecked",
        "parquet_files_rechecked": False, "full_corpus_selection_independently_recomputed": False,
        "parquet_provenance": [{**row, "immutable_url": recovery.parquet_url(row["path"])} for row in parquet_rows],
        "plan": filename_record(base, "plan.json"), "selection": filename_record(base, "selection.json"),
        "receipt": filename_record(base, "receipt.json"),
        "scope": "outcome-independent frozen issue-state selection; source availability is not compatibility or successful execution"}, manifests


def paired_costs(measurements):
    """Summarize already reconciled complete blocks; no missing work is imputed."""
    paired = {key: {arm: sum(row[key][arm] for row in measurements) for arm in ("fresh", "zerorun")}
              for key in ("chain_ms", "consumer_ms", "setup_inclusive_chain_ms")}
    return {"complete_blocks": len(measurements), **paired,
            "cache_hits": sum(row["cache_hit_observed"] is True for row in measurements),
            "fresh_diagnostics_ms": sum(row["fresh_diagnostics_ms"] for row in measurements),
            "oracle_ms": sum(row["oracle_ms"] for row in measurements),
            "chain_saved_fraction": 1 - paired["chain_ms"]["zerorun"] / paired["chain_ms"]["fresh"] if measurements else None,
            "consumer_saved_fraction": 1 - paired["consumer_ms"]["zerorun"] / paired["consumer_ms"]["fresh"] if measurements else None,
            "setup_inclusive_saved_fraction": 1 - paired["setup_inclusive_chain_ms"]["zerorun"] / paired["setup_inclusive_chain_ms"]["fresh"] if measurements else None,
            "setup_inclusive_scope": "Per-arm source/environment preparation only; common dependency/image setup, acquisition and operator review remain separate.",
            "oracle_scope": "Two independent paired-arm fresh checks per complete block; baseline and compatibility checks remain separate."}


def run_summary(directory, version, phase, acquisition_base, manifests, image_build):
    directory = Path(directory)
    if not directory.exists():
        return {"state": "NOT_AVAILABLE", "phase": phase, "version": version,
                "execution_success_claimed": False, "reason": "No run directory is present."}
    if not (directory / "completion.json").is_file():
        return {"state": "INCOMPLETE_RECORD", "phase": phase, "version": version,
                "execution_success_claimed": False, "reason": "No completion receipt is present."}
    if version == "v1":
        raw_root = directory
        reconciliation = v1.validate_saved(directory, acquisition_base)
        inner = reconciliation
    else:
        raw_root = directory / "run"
        reconciliation = v2.validate_saved(directory, image_build, acquisition_base)
        inner = reconciliation["controlled_handoffs"]
    protocol = v1.read(raw_root / "protocol.json")
    v1.exact(protocol["selection"], manifests[phase], "run selected a subset or changed frozen acquisition ledger")
    h.require(protocol["phase"] == phase and protocol["blocks_per_case"] == 2
              and protocol["natural_hit_frequency_study"] is False, "controlled run scope changed")
    completion = v1.read(raw_root / "completion.json")
    rows, complete, measurements = [], [], []
    block_costs = {arm: [] for arm in ("fresh", "zerorun")}
    for case in completion["cases"]:
        row = {"case_id": case["case_id"], "repo": case["repo"], "disposition": case["disposition"]}
        if case["disposition"] == "COMPLETE":
            values = {arm: sum(block["measurements"]["chain_ms"][arm] for block in case["blocks"])
                      for arm in ("fresh", "zerorun")}
            for arm in block_costs:
                block_costs[arm].extend(block["measurements"]["chain_ms"][arm] for block in case["blocks"])
            row.update(complete_paired_blocks=len(case["blocks"]), complete_chain_ms=values,
                       complete_chain_saved_fraction=1 - values["zerorun"] / values["fresh"],
                       complete_paired_costs=paired_costs([block["measurements"] for block in case["blocks"]]))
            measurements.extend(block["measurements"] for block in case["blocks"])
            complete.append(row)
        elif "error" in case:
            row["error"] = case["error"]
        rows.append(row)
    v1.exact({arm: sum(values) for arm, values in block_costs.items()}, inner["complete_paired_chain_ms"],
             "case totals differ from independently reconstructed operation totals")
    h.require([row["case_id"] for row in rows] == [case["case_id"] for case in manifests[phase]["cases"]],
              "run case denominator differs")
    return {"state": "RECONCILED_RECORDED_OUTCOMES", "phase": phase, "version": version,
        "reconciliation": reconciliation, "cases": rows,
        "complete_paired_costs": paired_costs(measurements),
        "all_selected_cases_completed": len(complete) == len(rows) and bool(rows),
        "complete_case_median_chain_saved_fraction": statistics.median(row["complete_chain_saved_fraction"] for row in complete) if complete else None,
        "complete_case_faster_count": sum(row["complete_chain_saved_fraction"] > 0 for row in complete),
        "complete_case_count": len(complete), "selected_case_count": len(rows),
        "campaign_error": completion["campaign_error"],
        "producer_reported_material_correctness_stop": completion["material_correctness_stop"],
        "operation_cost_scope": "cold producer plus consumer in both arms; diagnostics and independent oracles reported separately; incomplete costs never imputed as zero",
        "natural_repeat_prevalence_established": False, "live_agent_speedup_established": False,
        "protocol": filename_record(directory, "protocol.json"), "completion": filename_record(directory, "completion.json")}


def failed_image_summary(directory):
    """Retain a failed preparation with its original code; never certify it usable."""
    directory = Path(directory)
    if not directory.exists():
        return {"state": "NOT_AVAILABLE", "image_success_claimed": False}
    if not (directory / "completion.json").is_file():
        return {"state": "INCOMPLETE_RECORD", "image_success_claimed": False}
    protocol, completion = v1.read(directory / "protocol.json"), v1.read(directory / "completion.json")
    h.require(protocol["schema"] == "zerorun.handoff-image-build.v2"
              and completion["schema"] == "zerorun.handoff-image-build-completion.v2"
              and completion["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")),
              "failed image attempt identity differs")
    h.require(completion["passed"] is False and isinstance(completion["error"], dict)
              and completion["error"].get("type") and completion["error"].get("message")
              and completion["image_publicly_pullable"] is False
              and completion["bit_identical_recipe_rebuild_claimed"] is False,
              "failed image attempt hides its failure or changes claim")
    h.require(protocol["base_image"] == v2.b.BASE_IMAGE and protocol["listen_address"] == "127.0.0.1"
              and protocol["build_network"] == "none" and protocol["daemon_configuration_modified"] is False
              and protocol["paid_services"] is False, "failed preparation scope changed")
    h.validate_runtime_rows(protocol["engine_binding"]["runtime_files"])
    rows = protocol["sources"]
    h.require(len({row["path"] for row in rows}) == len(rows), "duplicate failed-attempt source binding")
    frozen = directory / "attempt-sources"
    h.require({row["path"] for row in rows} == {p.name for p in frozen.iterdir() if p.is_file()},
              "failed-attempt source inventory incomplete")
    for row in rows:
        bound(frozen, row)
    v1.exact(rows, completion["sources_after"], "failed-attempt source changed during preparation")
    h.require(completion["source_unchanged"] is True, "failed preparation source integrity changed")
    clocks = []
    commands = []
    for path in sorted((directory / "commands").glob("*.started.json")):
        start = v1.read(path)
        finished = path.with_name(path.name.replace(".started.json", ".json"))
        if not finished.is_file():
            commands.append({"name": path.name.removesuffix(".started.json"), "state": "INCOMPLETE_RECORD"})
            continue
        row = v1.read(finished)
        v1.exact(start["argv"], row["argv"], "failed preparation command binding differs")
        h.require(type(row["outer_ms"]) in (int, float) and math.isfinite(row["outer_ms"])
                  and row["outer_ms"] >= 0, "invalid retained preparation clock")
        clocks.append(row["outer_ms"])
        commands.append({"name": finished.stem, "state": "RECORDED", "returncode": row["returncode"],
                         "error": row["error"], "outer_ms": row["outer_ms"]})
    setup = completion["setup_outer_ms"]
    h.require(type(setup) in (int, float) and math.isfinite(setup) and setup >= 0, "invalid failed setup clock")
    return {"state": "RECONCILED_RECORDED_PREPARATION_FAILURE", "image_success_claimed": False,
        "error": completion["error"], "setup_outer_ms": setup, "retained_command_outer_ms": sum(clocks),
        "commands": commands, "producer_reported_registry_removed": completion["registry_container_removed"],
        "original_source_files_bound": len(rows), "protocol": filename_record(directory, "protocol.json"),
        "completion": filename_record(directory, "completion.json")}


def agent_summary(prepared, evaluation, phase, manifests, image_build):
    """Keep model production, fresh-fix validation and controlled handoffs distinct."""
    from research.softwarex.agent_handoff_v1 import common as pc
    from research.softwarex.agent_handoff_v1 import validate as pv
    from research.softwarex.agent_handoff_evaluation_v1 import validate as ev
    prepared, evaluation = Path(prepared), Path(evaluation)
    selected = pc.selected(manifests[phase])
    rows = []
    if (prepared / "freeze.json").is_file():
        frozen = v1.read(prepared / "freeze.json")
        v1.exact(frozen["ledger"], manifests[phase], "agent producer changed acquisition ledger")
        v1.exact(frozen["cases"], selected, "agent producer changed prespecified six/two selection")
        h.require(frozen["phase"] == phase, "agent producer phase changed")
    else:
        h.require(not (evaluation / "completion.json").exists(), "agent evaluation lacks producer freeze")
    for index, case in enumerate(selected):
        path = prepared / f"case-{index:02d}" / "session.json"
        row = {"case_id": case["case_id"], "repo": case["repo"], "case_index": index,
               "state": "NOT_AVAILABLE", "patch_correctness_established": False}
        if path.is_file():
            summary = pv.validate_receipt(path, directory=prepared)
            h.require(summary["case_id"] == case["case_id"]
                      and summary["patch_correctness_established"] is False
                      and (summary.get("producer_completed") is not True or summary.get("fresh_oracle_required") is True),
                      "producer response misstates oracle-established correctness")
            row.update(state="RECONCILED_RECORDED_PRODUCER", reconciliation=summary,
                       session=filename_record(prepared, path.relative_to(prepared).as_posix()))
            if summary["model_invocation_attempted"] is False:
                row["pre_invocation_error"] = v1.read(path)["error"]
        rows.append(row)
    companion = {"state": "NOT_AVAILABLE", "resolved_issue_count": None}
    if (evaluation / "completion.json").is_file():
        summary = ev.validate_saved(evaluation, prepared, image_build)
        h.require(summary["selected_cases"] == len(selected) and summary["phase"] == phase
                  and summary["new_model_calls"] == 0 and summary["end_to_end_agent_acceleration"] is False,
                  "agent companion denominator or scope changed")
        v1.exact([row["case_id"] for row in summary["cases"]], [case["case_id"] for case in selected],
                 "agent companion omitted selected outcome")
        companion = {"state": "RECONCILED_RECORDED_AGENT_EVALUATION", "reconciliation": summary,
            "resolved_issue_count": summary["completed_verified_fixes"],
            "complete_paired_costs": paired_costs(summary["block_measurements"]),
            "complete_controlled_chain_ms": {arm: sum(block["chain_ms"][arm] for block in summary["block_measurements"])
                                               for arm in ("fresh", "zerorun")}}
    elif evaluation.exists():
        companion["state"] = "INCOMPLETE_RECORD"
    return {"phase": phase, "planned_cases": 2 if phase == "pilot" else 6, "selected_cases": len(selected),
        "producer_cases": rows,
        "recorded_model_invocations": sum(row.get("reconciliation", {}).get("model_invocation_attempted") is True for row in rows),
        "producer_completed": sum(row.get("reconciliation", {}).get("producer_completed") is True for row in rows),
        "evaluation": companion, "denominator_combined_with_reference_patch_cases": False,
        "natural_repeat_prevalence_established": False, "end_to_end_agent_acceleration_established": False}


def previous_agent_evaluation(directory, prepared, image_build):
    """Reproduce the original frozen checker's refusal; never pool its reported counts."""
    directory = Path(directory)
    if not directory.exists():
        return {"state": "NOT_AVAILABLE", "verified_fix_count": None}
    if not (directory / "completion.json").is_file():
        return {"state": "INCOMPLETE_RECORD", "verified_fix_count": None}
    protocol = v1.read(directory / "protocol.json")
    completion = v1.read(directory / "completion.json")
    h.require(protocol["schema"] == "zerorun.agent-handoff-evaluation-protocol.v1"
              and completion["schema"] == "zerorun.agent-handoff-evaluation-completion.v1"
              and completion["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")),
              "original companion protocol binding differs")
    original = directory / "attempt-sources"
    rows = protocol["sources"]
    h.require({row["path"] for row in rows} == {"__init__.py", "PROTOCOL.md", "run.py", "validate.py", "test_evaluation.py"},
              "original companion source set differs")
    v1.exact(rows, completion["sources_after"], "original companion source changed during execution")
    for row in rows:
        h.bound(original, row)
    package_name = "_zerorun_retained_agent_evaluator_" + h.sha(str(original.resolve()).encode())[:20]
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    observed_error = None
    try:
        spec = importlib.util.spec_from_file_location(package_name, original / "__init__.py", submodule_search_locations=[str(original)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
        validator_spec = importlib.util.spec_from_file_location(package_name + ".validate", original / "validate.py")
        validator = importlib.util.module_from_spec(validator_spec)
        sys.modules[validator_spec.name] = validator
        validator_spec.loader.exec_module(validator)
        try:
            validator.validate_saved(directory, prepared, image_build)
        except ValueError as error:
            observed_error = {"type": type(error).__name__, "message": str(error)}
    finally:
        sys.dont_write_bytecode = previous_bytecode
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(package_name + "."):
                del sys.modules[name]
    h.require(observed_error == {"type": "ValueError", "message": "archived final source bytes differ"},
              "original companion refusal differs from recorded inventory-order issue")
    return {"state": "RETAINED_FROZEN_CHECKER_REFUSAL", "verified_fix_count": None,
            "checker_failure_reproduced_read_only": True, "checker_failure": observed_error,
            "original_source_files_bound": len(rows), "not_pooled_with_repaired_companion": True,
            "protocol": filename_record(directory, "protocol.json"), "completion": filename_record(directory, "completion.json"),
            "scope": "Original checker rejected archive/source ordering; its producer-reported success is not independently certified by this attempt."}


def host_interruption_summary(root):
    """Bind an operator observation without changing frozen experiment records."""
    root = Path(root)
    path = root / HOST_INTERRUPTION
    if not path.is_file():
        return None
    row = v1.read(path)
    h.require(row.get("schema") == "zerorun.softwarex-host-interruption.v1"
              and row.get("basis") == "operator-observed host event",
              "host interruption observation identity differs")
    h.require(all(row.get(key) is False for key in (
        "existing_external_drive_files_deleted", "raw_guest_timestamps_altered",
        "guest_clock_synchronization_requested_during_run", "prospective_amendment_modified",
        "uninterrupted_timing_certified")), "host interruption record changes preservation or timing claim")
    interval = row["resume_command_issued_utc_bounds"]
    h.require(interval.get("exact_resume_instant_established") is False
              and row["pause_observed_utc"] < interval["not_before"] <= interval["not_after"],
              "host interruption command interval is invalid")
    clocks = row["post_resume_clock_observation"]
    h.require(clocks.get("clocks_agreed") is False
              and clocks["guest_reported_utc"] < clocks["host_observed_utc"],
              "host interruption hides the observed guest-clock offset")
    virtualbox = row["virtualbox"]
    h.require(all(re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", virtualbox[key])
                  for key in ("snapshot_uuid", "delta_uuid"))
              and virtualbox.get("exact_delta_file_path_claimed") is False,
              "host interruption storage identity differs")
    return {"basis": row["interpretation"], "interruption_reported": True,
        "uninterrupted_timing_certified": False,
        "pause_utc_operator_reported": row["pause_observed_utc"],
        "resume_command_issued_utc_bounds": interval,
        "post_resume_clock_observation": clocks,
        "host_event_record": filename_record(root, HOST_INTERRUPTION)}


def application_revision_attempt(root, directory, driver_sha, amendment_sha):
    """Require the bound public-checkout wrapper before confirming its provenance."""
    directory = Path(directory)
    missing = {"state": "NOT_AVAILABLE", "sequence_completed": False,
        "checkout_execution_binding_confirmed": False,
        "completed_workload_outcomes_reconciled": False,
        "fresh_real_workload_reproduction_confirmed": False}
    if not directory.exists():
        return missing
    if not all((directory / name).is_file() for name in ("completion.json", "RECORD_MANIFEST.json")):
        return {**missing, "state": "INCOMPLETE_RECORD"}
    from research.softwarex.verify_application_revision import verify
    return verify(root, directory, driver_sha, amendment_sha)


def build(root=ROOT, *, acquisition_path=ACQUISITION, image_path=IMAGE, run_paths=None):
    root = Path(root)
    base = root / acquisition_path
    acq, manifests = acquisition_summary(base)
    paths = {key: spec[0] for key, spec in RUNS.items()}
    if run_paths is not None:
        h.require(set(run_paths) <= set(RUNS), "unrecognized run name")
        paths.update(run_paths)
    runs = {key: {"path": paths[key], **run_summary(root / paths[key], version, phase, base, manifests, root / image_path)}
            for key, (_, version, phase) in RUNS.items()}
    amendments = [filename_record(root, relative) for relative in AMENDMENTS if (root / relative).is_file()]
    recovery_amendment = next((row for row in amendments if row["path"] == AMENDMENTS[0]), None)
    if recovery_amendment is not None:
        runs["v2_pilot"]["timing_context"] = {
            "basis": "Operator-reported VirtualBox pause and host-storage interruption; unchanged original raw records retained.",
            "interruption_reported": True, "uninterrupted_timing_certified": False, "amendment": recovery_amendment}
        runs["v2_pilot_repeat"]["timing_context"] = {
            "basis": "Separate amended repeat of the same pilot cases; no pooling with original measurements.",
            "original_run": "v2_pilot", "uninterrupted_timing_certified": False, "amendment": recovery_amendment}
    h.require(not (root / paths["v2_pilot_repeat"]).exists() or recovery_amendment is not None,
              "separate recovery repeat requires its retained prospective amendment")
    revision_amendment = next((row for row in amendments if row["path"] == AMENDMENTS[2]), None)
    h.require(not any((root / path).exists() for path in (paths["v2_main_extended"], FRESH_IMAGE, FRESH_PILOT))
              or revision_amendment is not None, "application revision requires its prospective amendment")
    interruption = host_interruption_summary(root)
    wrapper_sealed = all((root / REVISION_RECORDS / name).is_file()
                         for name in ("completion.json", "RECORD_MANIFEST.json"))
    h.require(not wrapper_sealed or interruption is not None,
              "completed application wrapper requires the retained host interruption record")
    runs["v2_main_extended"]["timing_context"] = interruption or {
        "basis": "Operator-reported VirtualBox pause at 2026-09-07 08:07:57 UTC after host storage exhaustion; original records retained.",
        "interruption_reported": True, "uninterrupted_timing_certified": False,
        "pause_utc_operator_reported": "2026-09-07T08:07:57Z"}
    if runs["v2_main_extended"]["state"] == "RECONCILED_RECORDED_OUTCOMES":
        extended_protocol = v1.read(root / paths["v2_main_extended"] / "run/protocol.json")
        h.require(extended_protocol["budget_seconds"] == 1200
                  and extended_protocol["execution_seconds"] == 120,
                  "expanded main repeat changed its prospective execution budget")
        runs["v2_main_extended"]["repeat_context"] = {
            "original_run": "v2_main", "same_frozen_selection": True,
            "pooled_with_original": False, "new_independent_subjects_claimed": False,
            "amendment": revision_amendment}
    revision_attempts = {
        "preflight": {"path": REVISION_PREFLIGHT, **application_revision_attempt(
            root, root / REVISION_PREFLIGHT, PREFLIGHT_DRIVER_SHA, PREFLIGHT_AMENDMENT_SHA)},
        "revision": {"path": REVISION_RECORDS, **application_revision_attempt(
            root, root / REVISION_RECORDS, REVISION_DRIVER_SHA, REVISION_AMENDMENT_SHA)}}
    fresh_reproduction = {"image_path": FRESH_IMAGE, "run_path": FRESH_PILOT,
        "state": "NOT_AVAILABLE", "independent_human_replication": False,
        "pooled_with_historical_measurements": False,
        "fresh_real_workload_reproduction_confirmed": revision_attempts["revision"].get(
            "fresh_real_workload_reproduction_confirmed", False),
        "public_source_provenance": revision_attempts["revision"]}
    if (root / FRESH_IMAGE / "completion.json").is_file():
        fresh_completion = v1.read(root / FRESH_IMAGE / "completion.json")
        if fresh_completion.get("passed") is True:
            fresh_reproduction.update(state="RECONCILED_IMAGE_BUILD",
                image=v2.validate_image(root / FRESH_IMAGE),
                pilot=run_summary(root / FRESH_PILOT, "v2", "pilot", base, manifests, root / FRESH_IMAGE),
                amendment=revision_amendment)
            if fresh_reproduction["pilot"]["state"] == "RECONCILED_RECORDED_OUTCOMES":
                fresh_protocol = v1.read(root / FRESH_PILOT / "run/protocol.json")
                h.require(fresh_protocol["budget_seconds"] == 600
                          and fresh_protocol["execution_seconds"] == 120,
                          "fresh pilot changed its prospective execution budget")
        else:
            fresh_reproduction.update(state="RETAINED_IMAGE_BUILD_FAILURE",
                failure=failed_image_summary(root / FRESH_IMAGE), amendment=revision_amendment)
    elif (root / FRESH_IMAGE).exists() or (root / FRESH_PILOT).exists():
        fresh_reproduction["state"] = "INCOMPLETE_RECORD"
    image_directory = root / image_path
    image = {"path": image_path, "state": "NOT_AVAILABLE"}
    if (image_directory / "completion.json").is_file():
        image.update(state="RECONCILED_RECORDED_IMAGE", reconciliation=v2.validate_image(image_directory))
    elif image_directory.exists():
        image["state"] = "INCOMPLETE_RECORD"
    failed_image = {"path": FAILED_IMAGE, **failed_image_summary(root / FAILED_IMAGE)}
    agents = {phase: {"producer_path": paths[0], "evaluation_path": paths[1],
              **agent_summary(root / paths[0], root / paths[1], phase, manifests, root / image_path)}
              for phase, paths in AGENTS.items()}
    previous_agent = {"path": PREVIOUS_AGENT_EVALUATION,
                      **previous_agent_evaluation(root / PREVIOUS_AGENT_EVALUATION, root / AGENTS["pilot"][0], root / image_path)}
    return {"schema": "zerorun.softwarex-handoff-evidence.v1",
        "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "acquisition_path": acquisition_path, "acquisition": acq, "controlled_runs": runs, "image_build": image,
        "earlier_image_preparation": failed_image,
        "available_completed_runs_reconciled": True,
        "reconciled_run_count": sum(value["state"] == "RECONCILED_RECORDED_OUTCOMES" for value in runs.values()),
        "unavailable_or_incomplete_runs": [key for key, value in runs.items() if value["state"] != "RECONCILED_RECORDED_OUTCOMES"],
        "agent_evaluation": agents,
        "earlier_agent_evaluation": previous_agent,
        "fresh_public_source_reproduction": fresh_reproduction,
        "application_revision_attempts": revision_attempts,
        "protocol_amendments": amendments,
        "acceptance_probability_estimated": False, "performance_threshold_imposed": False,
        "scope": "Read-only source/receipt reconciliation; no new model calls, benchmark executions or authorizations."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--acquisition", default=ACQUISITION)
    parser.add_argument("--image-build", default=IMAGE)
    for name in RUNS:
        parser.add_argument("--" + name.replace("_", "-"))
    args = parser.parse_args()
    overrides = {name: getattr(args, name) for name in RUNS if getattr(args, name) is not None}
    result = build(args.root, acquisition_path=args.acquisition, image_path=args.image_build, run_paths=overrides)
    output = args.output or args.root / OUTPUT
    if args.check:
        v1.exact(v1.read(output), result, "saved handoff evidence is stale")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes((json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
    print(json.dumps({"checked": args.check, "output": str(output),
        "selected_main_cases": result["acquisition"]["selected_main_cases"],
        "selected_main_repositories": result["acquisition"]["selected_main_repositories"],
        "unavailable_or_incomplete_runs": result["unavailable_or_incomplete_runs"]}, sort_keys=True))


if __name__ == "__main__":
    main()
