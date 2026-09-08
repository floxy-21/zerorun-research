"""Read-only reconstruction of the recovered V5 and corrected-fixture V6 records.

Frozen record manifests bind retained bytes. The unchanged V1 validator then
reconstructs every paired result from raw captures and operation clocks. This
does not recreate missing host observations or execute repository code.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
import tarfile

from research.softwarex.handoff_v1 import run as h, validate as v

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = "research/softwarex/evidence/handoff-acquisition-recovery-v1"
PREFIX = "research/softwarex/evidence/application-revision-20260907-"
ORIGINAL_LEDGER = "4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997"
CORRECTED_LEDGER = "e6453b256f80e0eb79d280acc6bb8a143c006e56854f1702300cdc6550ab9ee2"
PINS = {
    5: ("655551afa10a317c654a4dc730fe40f401dc2234b7b02f2303747cdfd9c5ca4c", 1331),
    6: ("6cc44af207e90630762b3dcb788d5331287419a861d7d20fbaba63c5ccb1a5a1", 1381),
}
CORRECTION_MANIFEST = "5db5842ffd0ed1da3354e2bc63d334ff502f93ff0a3b85e93e1b956c42d61373"
LAUNCH_MANIFESTS = {
    "preflight-records": ("8733f9c1ff16b81d6605e7f47648ad7925e5216d58a7ba82cd6ddc91c31ab761", 902),
    "preflight-audit": ("b4f6203834ec04e07cefd849b8a342d7b85df9313bfd9f6784bdf3dfcd3338cb", 3),
    "product-smoke": ("68d17d9e386ba59f0c87a7ddc0538401fb79e853f0c628cb137cd6c086abf85a", 17),
}
IMAGE = "127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465"
IMAGE_COMPLETION_SHA = "ffce2613a7b7305df307b3b811c6b8ed6c8f818af7cf0addbd4534bd34f75045"
HISTORICAL_SKIPS = [
    "test/testNestedStructures.py::TestCppNestedStructures::test_struct_inside_declaration",
    "test/testNestedStructures.py::TestCppNestedStructures::test_struct_inside_definition",
]
LIZARD = "terryyin__lizard-191"
TARGET = "test/test_languages/testCAndCPP.py"
UPSTREAM_COMMIT = "67d87968e9fecd459c9a9a1dcb01cf6ceac5721d"
UPSTREAM_SHA = "34e839761f3a09f1193bab78318ca46cbd3819b4650a470057f0f918307372de"


def typedef_ast(text):
    tree = ast.parse(text)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Test_Big"]
    h.require(len(classes) == 1, "upstream Test_Big absent/duplicate")
    methods = [node for node in classes[0].body if isinstance(node, ast.FunctionDef) and node.name == "test_typedef"]
    h.require(len(methods) == 1, "upstream typedef regression absent/duplicate")
    return ast.dump(methods[0], include_attributes=False)


def upstream_reference(directory, corrected_target):
    receipt = v.read(directory / "retrieval.json")
    url = "https://raw.githubusercontent.com/terryyin/lizard/" + UPSTREAM_COMMIT + "/" + TARGET
    h.require(receipt["schema"] == "zerorun.public-upstream-fixture-reference.v1"
              and receipt["url"] == receipt["resolved_url"] == url and receipt["http_status"] == 200
              and receipt["upstream_commit"] == UPSTREAM_COMMIT
              and receipt["file"] == {"path": "testCAndCPP.py", "bytes": 37120, "sha256": UPSTREAM_SHA},
              "upstream commit/file receipt differs")
    raw = h.bound(directory, receipt["file"])
    h.require(typedef_ast(raw.decode("utf-8")) == typedef_ast(corrected_target),
              "corrected typedef method is not identical to pinned upstream method")
    return {"url": url, "commit": UPSTREAM_COMMIT, "file": receipt["file"],
        "corrected_method_ast_matches": True, "retrieval_sha256": h.sha(h.ordinary(directory / "retrieval.json")),
        "scope": "Public commit source retrieved during recovery audit; original diagnostic records remain separate."}


def manifest(directory, expected_sha, expected_count):
    """Require an exhaustive, link-free, canonical record inventory."""
    directory = Path(directory)
    h.require(directory.is_dir() and not directory.is_symlink()
              and not getattr(directory.lstat(), "st_file_attributes", 0) & 0x400,
              "record directory absent/linked")
    raw = h.ordinary(directory / "RECORD_MANIFEST.json")
    h.require(h.sha(raw) == expected_sha, "frozen record manifest changed")
    parsed = h.strict(raw)
    h.require(set(parsed) == {"files"} and isinstance(parsed["files"], list)
              and len(parsed["files"]) == expected_count, "record manifest denominator changed")
    names = []
    for row in parsed["files"]:
        h.bound(directory, row, h.MAX_ARCHIVE_BYTES)
        names.append(row["path"])
    h.require(len(set(names)) == len(names) and "RECORD_MANIFEST.json" not in names,
              "duplicate or recursive record manifest")
    actual = set()
    for current, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs + files:
            path = Path(current) / name
            info = path.lstat()
            h.require(not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400,
                      "linked record tree")
            if name in dirs:
                h.require(stat.S_ISDIR(info.st_mode), "non-directory record parent")
            else:
                h.require(stat.S_ISREG(info.st_mode), "special record file")
                actual.add(path.relative_to(directory).as_posix())
    h.require(actual == set(names) | {"RECORD_MANIFEST.json"}, "unlisted or missing record files")
    return {"files_verified": len(names), "manifest_sha256": h.sha(raw), "all_record_bytes_verified": True}


def comparable(actual, expected):
    """Only tolerate final-bit platform differences in recomputed float sums."""
    if type(actual) is float and type(expected) is float:
        h.require(math.isfinite(actual) and math.isfinite(expected)
                  and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-7), "recomputed clock differs")
    elif isinstance(actual, dict) and isinstance(expected, dict):
        h.require(set(actual) == set(expected), "recomputed fields differ")
        for key in actual:
            comparable(actual[key], expected[key])
    elif isinstance(actual, list) and isinstance(expected, list):
        h.require(len(actual) == len(expected), "recomputed list length differs")
        for a, b in zip(actual, expected):
            comparable(a, b)
    else:
        h.require(type(actual) is type(expected) and actual == expected, "recomputed value differs")


def apply_target_patch(original, patch):
    """Apply only an ordinary textual diff for the one reviewed Lizard target."""
    h.require(h.patch_paths(patch) == [TARGET], "fixture patch changes another target")
    source, lines = original.splitlines(keepends=True), patch.splitlines(keepends=True)
    result, cursor, index, hunks = [], 0, 0, 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("@@ "):
            h.require(line.startswith(("diff --git ", "index ", "--- a/", "+++ b/")), "unsupported patch header")
            index += 1
            continue
        match = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@[^\n]*\n?", line)
        h.require(match is not None, "invalid patch hunk")
        start, count = int(match[1]) - 1, int(match[2] or 1)
        h.require(cursor <= start <= len(source), "overlapping or out-of-range patch")
        result.extend(source[cursor:start]); cursor = start
        consumed, produced = 0, 0
        index += 1
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            h.require(line and line[0] in " +-", "unsupported patch line")
            if line[0] in " -":
                h.require(cursor < len(source) and source[cursor] == line[1:], "patch context differs")
                cursor += 1; consumed += 1
            if line[0] in " +":
                result.append(line[1:]); produced += 1
            index += 1
        h.require(consumed == count and produced == int(match[4] or 1), "patch line count differs")
        hunks += 1
    h.require(hunks > 0, "patch has no hunks")
    return "".join(result + source[cursor:])


def corrected_acquisition(original, corrected):
    original, corrected = Path(original), Path(corrected)
    integrity = manifest(corrected, CORRECTION_MANIFEST, 53)
    old_bytes, new_bytes = h.ordinary(original / "main.json"), h.ordinary(corrected / "main.json")
    h.require(h.sha(old_bytes) == ORIGINAL_LEDGER and h.sha(new_bytes) == CORRECTED_LEDGER,
              "original/corrected selection identity differs")
    h.require(h.ordinary(corrected / "main.original.json") == old_bytes, "original ledger not preserved")
    old, new = h.strict(old_bytes), h.strict(new_bytes)
    correction = v.read(corrected / "FIXTURE_CORRECTION.json")
    expected = deepcopy(old)
    expected["zerorun_fixture_correction"] = correction
    h.require(len(old["cases"]) == len(new["cases"]) == 24, "corrected denominator differs")
    changed = []
    for prior, current in zip(old["cases"], new["cases"]):
        v.exact({k: val for k, val in prior.items() if k != "metadata"},
                {k: val for k, val in current.items() if k != "metadata"}, "case order/targets/archive changed")
        for item in ("source_archive", "metadata"):
            h.bound(original, prior[item], h.MAX_ARCHIVE_BYTES)
            h.bound(corrected, current[item], h.MAX_ARCHIVE_BYTES)
        if prior["metadata"] != current["metadata"]:
            changed.append(prior["case_id"])
            h.require(prior["case_id"] == LIZARD, "another case metadata changed")
            old_raw = h.bound(original, prior["metadata"])
            h.require(h.ordinary(corrected / ("cases/" + LIZARD + "/metadata.original.json")) == old_raw,
                      "original Lizard metadata not retained")
            old_meta, new_meta = h.strict(old_raw), h.strict(h.bound(corrected, current["metadata"]))
            old_row, new_row = old_meta["rows"][0]["row"], new_meta["rows"][0]["row"]
            expected_meta = deepcopy(old_meta)
            expected_meta["rows"][0]["row"]["test_patch"] = new_row["test_patch"]
            expected_meta["rows"][0]["row"]["zerorun_fixture_correction"] = {
                **correction, "original_metadata_sha256": prior["metadata"]["sha256"],
                "original_test_patch": old_row["test_patch"]}
            v.exact(new_meta, expected_meta, "fixture metadata changed unrelated fields or runtime patch")
            archive = h.bound(original, prior["source_archive"], h.MAX_ARCHIVE_BYTES)
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                members = [m for m in tar.getmembers() if m.name.endswith("/" + TARGET)]
                h.require(len(members) == 1 and members[0].isfile(), "Lizard source target absent/ambiguous")
                target = tar.extractfile(members[0]).read().decode("utf-8")
            before = apply_target_patch(target, old_row["test_patch"])
            after = apply_target_patch(target, new_row["test_patch"])
            marker = "        self.assertEqual(2, result[0].cyclomatic_complexity)\n"
            start = before.index("    def test_typedef(", before.index("class Test_Big("))
            end = before.find("\n    def ", start + 1)
            end = len(before) if end < 0 else end
            h.require(before[start:end].count(marker) == 1, "ambiguous historical typedef assertion")
            offset = before.index(marker, start)
            fixed = before[:offset] + before[offset:].replace(marker, marker.replace("Equal(2,", "Equal(3,"), 1)
            h.require(after == fixed, "derived target is not exactly one expected-value correction")
            upstream = upstream_reference(corrected.parent / "upstream-fixture-reference", after)
            for row in expected["cases"]:
                if row["case_id"] == LIZARD:
                    row["metadata"] = current["metadata"]
    h.require(changed == [LIZARD], "fixture correction absent or duplicated")
    v.exact(new, expected, "corrected selection has unrelated changes")
    return {**integrity, "original_ledger_sha256": ORIGINAL_LEDGER, "corrected_ledger_sha256": CORRECTED_LEDGER,
        "changed_cases": changed, "exact_single_assertion_change_verified": True,
        "case_ids_order_targets_base_archives_and_runtime_patches_unchanged": True,
        "fixture_correction": correction, "upstream_reference": upstream,
        "upstream_diagnostic_bytes_rechecked": False}


def nonfailing_capture(directory, allowed_skips):
    raw = v.read(directory / "raw-outcomes.json")
    h.require(set(raw) == {"schema", "exit_code", "nodeids", "nodeid_sha256", "outcomes"}
              and raw["schema"] == "zerorun.benchmark-independent-pytest-shadow.v1"
              and type(raw["exit_code"]) is int and raw["exit_code"] == 0, "failed or malformed fresh capture")
    nodes, outcomes = raw["nodeids"], raw["outcomes"]
    h.require(nodes and len(nodes) == len(set(nodes)) == len(outcomes)
              and raw["nodeid_sha256"] == h.sha(h.encoded({"nodeids": nodes})), "fresh node inventory differs")
    skipped = []
    for node, value in zip(nodes, outcomes):
        h.require(isinstance(node, str) and node and set(value) == {"setup", "call", "teardown", "wasxfail"}
                  and value["setup"] == value["teardown"] == "passed"
                  and value["call"] in {"passed", "skipped"} and value["wasxfail"] is False,
                  "fresh capture has failure/incomplete outcome")
        if value["call"] == "skipped":
            skipped.append(node)
    v.exact(skipped, allowed_skips, "unexpected or missing historical skip")
    h.require(v.read(directory / "runner.json")["exit_code"] == 0, "runner exit differs")
    return {"exit_code": 0, "nodes": dict(zip(nodes, outcomes))}


def launch_checks(directory, records, selection):
    directory, records = Path(directory), Path(records)
    inventories = {name: manifest(directory / name, *pin) for name, pin in LAUNCH_MANIFESTS.items()}
    audit = v.read(directory / "preflight-audit/completion.json")
    prior = v.read(directory / "preflight-records/completion.json")
    v.exact(audit, v.read(records / "provenance/preflight-completion.json"), "launch/audit capture differs")
    h.require(audit["original_completion_sha256"] == h.sha(h.ordinary(directory / "preflight-records/completion.json"))
              and prior["passed"] is False and prior["public_sources_unchanged"] is True
              and prior["runtime_attestation_unchanged"] is True, "preliminary refusal/source status not retained")
    h.require(audit["passed"] is True and len(audit["cases"]) == len(prior["cases"]) == 24,
              "preflight denominator differs")
    for index, (case, row, preliminary) in enumerate(zip(selection["cases"], audit["cases"], prior["cases"])):
        label = f"case-{index:02d}-{case['case_id']}"
        base = directory / "preflight-records" / label
        skipped = HISTORICAL_SKIPS if case["case_id"] == "terryyin__lizard-174" else []
        verdict = nonfailing_capture(base, skipped)
        v.exact(row["verdict"], verdict, "preflight verdict not reconstructed")
        v.exact(row["skipped_nodeids"], skipped, "preflight skips hidden")
        h.require(row["case_id"] == preliminary["case_id"] == case["case_id"] and row["passed"] is True
                  and row["nodes"] == len(verdict["nodes"])
                  and row["capture_sha256"] == h.sha(h.ordinary(base / "raw-outcomes.json")), "preflight case binding differs")
        source = v.read(base / "source-preparation.json")["identity"]
        h.require(source["sha256"] == h.sha(h.encoded(source["rows"])) == row["source_sha256"], "preflight source hash differs")
        after_path = base / "source-after.json"
        if after_path.is_file():
            v.exact(source, v.read(after_path), "preflight source changed")
        else:
            h.require(case["case_id"] == "terryyin__lizard-174" and preliminary["passed"] is False,
                      "preflight source-after record unexpectedly missing")
        h.require(preliminary["passed"] is (not bool(skipped)), "unexpected preliminary refusal")
        for command in base.glob("command-*.json"):
            if command.name.endswith(".started.json"):
                continue
            entry = v.read(command)
            start = v.read(command.with_name(command.stem + ".started.json"))
            v.exact(entry["argv"], start["argv"], "preflight command changed")
            stdout, stderr = h.bound(base, entry["stdout"]), h.bound(base, entry["stderr"])
            if entry["returncode"] != 0:
                # The final inspect intentionally proves the removed container
                # no longer exists; it is not a failed workload execution.
                removed = v.read(base / "command-005.json")
                ident = entry["argv"][-1]
                h.require(command.name == "command-006.json" and entry["returncode"] == 1
                          and entry["argv"][1:3] == ["container", "inspect"]
                          and removed["returncode"] == 0 and removed["argv"][-1] == ident
                          and "rm" in removed["argv"] and stdout == b"[]\n"
                          and stderr.decode() == "Error response from daemon: No such container: " + ident + "\n",
                          "preflight command failed outside expected cleanup absence probe")
    first = nonfailing_capture(directory / "preflight-records/lizard-first", [])
    h.require(len(first["nodes"]) == 102 and audit["first_case"]["nodes"] == 102,
              "Lizard confirmatory node count differs")
    smoke = directory / "product-smoke"
    p, c = v.read(smoke / "protocol.json"), v.read(smoke / "completion.json")
    fresh = v.oracle(smoke, "oracle", "oracle-capture")["result"]["verdict"]
    v.exact(fresh, p["expected_fresh_verdict"], "product smoke fresh oracle differs")
    producer, consumer, diagnostic = (v.operation(smoke, name)["result"] for name in ("producer", "consumer", "diagnostics"))
    h.require(c["passed"] is True and c["error"] is None and c["performance_claims"] is False
              and producer["status"] == "MISS_EXECUTED" and consumer["status"] == "HIT_REUSED"
              and diagnostic["status"] == "VERIFY_MATCH"
              and producer["cache_key"] == consumer["cache_key"]
              and producer["exit_code"] == consumer["exit_code"] == diagnostic["exit_code"] == fresh["exit_code"] == 0,
              "product smoke reuse/fresh semantics differ")
    return {"inventories": inventories, "preflight_cases": 24, "lizard_confirmatory_nodes": 102,
        "historical_skipped_nodes_retained": HISTORICAL_SKIPS, "preliminary_extra_skip_constraint_refusal_preserved": True,
        "preflight_source_after_missing": ["terryyin__lizard-174"],
        "preflight_missing_source_after_scope": "Preliminary extra skip constraint stopped its after-capture; subsequent audit reports unchanged source. Every timed V6 arm has an independently reconciled before/after inventory.",
        "product_handoff_fresh_check_reconciled": True}


def descriptive_variability(completion):
    """Describe validated completed cases without treating blocks as subjects."""
    rows = []
    phases = {name: {"operations": 0, "outer_ms": 0.0, "reported_wall_ms": 0.0, "reported_phase_ms": {}}
              for name in ("producer", "consumer", "diagnostics")}
    for case in completion["cases"]:
        if case["disposition"] != "COMPLETE":
            continue
        blocks = case["blocks"]
        costs = {arm: {operation: sum(b["arms"][arm][operation]["outer_ms"] for b in blocks)
                       for operation in ("producer", "consumer")} for arm in ("fresh", "zerorun")}
        totals = {arm: sum(costs[arm].values()) for arm in costs}
        fractions = [b["measurements"]["chain_saved_fraction"] for b in blocks]
        rows.append({"case_id": case["case_id"], "repo": case["repo"], "chain_ms": totals,
            "chain_saved_fraction": 1 - totals["zerorun"] / totals["fresh"],
            "block_chain_saved_fractions": fractions, "operation_outer_ms": costs,
            "block_direction_disagrees": any(f > 0 for f in fractions) and any(f < 0 for f in fractions)})
        for block in blocks:
            for operation, sums in phases.items():
                op = block["arms"]["zerorun"][operation]
                sums["operations"] += 1
                sums["outer_ms"] += op["outer_ms"]
                sums["reported_wall_ms"] += op["result"]["wall_ms"]
                for name, value in op["result"]["phase_ms"].items():
                    h.require(type(value) in (int, float) and math.isfinite(value) and value >= 0,
                              "invalid reported runtime phase clock")
                    sums["reported_phase_ms"][name] = sums["reported_phase_ms"].get(name, 0.0) + value
    total = sum(row["chain_ms"]["fresh"] for row in rows)
    for row in rows:
        row["fresh_chain_cost_share"] = row["chain_ms"]["fresh"] / total
    fractions = [f for row in rows for f in row["block_chain_saved_fractions"]]
    return {"complete_cases": len(rows), "complete_blocks": len(fractions),
        "case_faster_count": sum(r["chain_saved_fraction"] > 0 for r in rows),
        "case_slower_count": sum(r["chain_saved_fraction"] < 0 for r in rows),
        "block_faster_count": sum(f > 0 for f in fractions), "block_slower_count": sum(f < 0 for f in fractions),
        "case_block_direction_disagreements": sum(r["block_direction_disagrees"] for r in rows),
        "case_median_chain_saved_fraction": statistics.median(r["chain_saved_fraction"] for r in rows) if rows else None,
        "cases_descending_fresh_cost": sorted(rows, key=lambda r: r["chain_ms"]["fresh"], reverse=True),
        "zerorun_reported_operation_phases": phases,
        "scope": "Descriptive fixed-cohort clocks. Nested runtime phase clocks are not additive partitions; hit execution_ms is retained seed execution, not hit latency. Two counterbalanced blocks per purposively selected case do not establish population confidence intervals or causal bottlenecks."}


def verify(directory, original_acquisition, *, version=6, corrected=None, launch=None):
    h.require(type(version) is int and version in PINS, "unsupported recovered cohort")
    directory, original_acquisition = Path(directory), Path(original_acquisition)
    integrity = manifest(directory, *PINS[version])
    correction = None
    if version == 6:
        corrected = Path(corrected or directory.parent / "corrected-acquisition")
        launch = Path(launch or directory.parent / "launch-evidence")
        correction = corrected_acquisition(original_acquisition, corrected)
    acquisition = corrected if version == 6 else original_acquisition
    p, c = v.read(directory / "provenance/protocol.json"), v.read(directory / "provenance/completion.json")
    inner = v.validate_saved(directory / "run", acquisition)
    saved = v.read(directory / "provenance/independent-guest-reconciliation.json")
    comparable(inner, saved)
    raw_p, raw_c = v.read(directory / "run/protocol.json"), v.read(directory / "run/completion.json")
    expected_ledger = CORRECTED_LEDGER if version == 6 else ORIGINAL_LEDGER
    h.require(p["schema"] == f"zerorun.full-cohort-compatible-image.v{version}"
              and c["schema"] == f"zerorun.full-cohort-compatible-image-completion.v{version}"
              and p["ledger_sha256"] == raw_p["selection_sha256"] == expected_ledger,
              "recovered cohort identity differs")
    h.require(inner["selected_cases"] == 24 and inner["selected_repositories"] == 8
              and p["execution_seconds"] == raw_p["execution_seconds"] == 120
              and p["operational_guard_seconds"] == raw_p["budget_seconds"] == 14400
              and raw_p["blocks_per_case"] == 2 and p["former_20_minute_cutoff_used"] is False,
              "selected denominator or execution contract changed")
    h.require(c["all_24_attempted"] is True and c["error"] is None and c["source_unchanged"] is True
              and c["guest_reconciliation_passed"] is True and c["material_correctness_stop"] is False
              and raw_c["campaign_error"] is None and inner["material_correctness_stop"] is False
              and not any(row["disposition"].startswith("NOT_RUN") or row["disposition"] == "UNAVAILABLE_ACQUISITION"
                          for row in raw_c["cases"]), "recovered campaign has unattempted cases/integrity failure")
    expected_dispositions = {"COMPLETE": 24} if version == 6 else {"COMPLETE": 23, "INCOMPLETE_OR_UNSUPPORTED": 1}
    v.exact({k: n for k, n in inner["dispositions"].items() if n}, expected_dispositions, "retained dispositions changed")
    if version == 5:
        refused = [r for r in raw_c["cases"] if r["disposition"] != "COMPLETE"]
        h.require(refused[0]["case_id"] == LIZARD, "original Lizard refusal missing")
    v.exact(c["cases"], [{"case_id": r["case_id"], "disposition": r["disposition"], "error": r.get("error")}
                          for r in raw_c["cases"]], "outer case ledger differs")
    h.require(c["complete_paired_cases"] == inner["complete_cases"], "outer completed count differs")
    for row in p["bindings"]:
        h.bound(directory / "provenance", row)
    h.validate_runtime_rows(p["engine_binding"]["runtime_files"])
    v.exact(p["engine_binding"], raw_p["engine"], "inner runtime binding differs")
    v.exact(p["runtime_attestation_before"], c["runtime_attestation_after"], "runtime image changed")
    v.exact(p["public_source_before"], {
        "harness": {"clean": True, "commit": "0528905a52b74df78aa4e5a09219df34620282dd"},
        "engine": {"clean": True, "commit": "ebf2884df12573d63f45813200e0675288d12096"}}, "public source pointer changed")
    image_record = directory / "provenance/image-build-completion.json"
    h.require(h.sha(h.ordinary(image_record)) == IMAGE_COMPLETION_SHA, "historical image completion differs")
    image = v.read(image_record)
    v.exact(p["image"], image["result"]["image"], "deployment image differs from build receipt")
    h.require(p["image"]["requested"] == raw_p["runtime_image"] == IMAGE
              and p["runtime_attestation_before"]["requested_image"] == IMAGE
              and p["image_setup_in_chain_ratio"] is False, "paired image or setup scope changed")
    launch_result = launch_checks(launch, directory, raw_p["selection"]) if version == 6 else None
    image_directory = directory.parent.parent / "compatible-image-repair-v2"
    image_audit = {"state": "NOT_AVAILABLE", "image_build_commands_independently_rechecked": False}
    if image_directory.exists():
        from research.softwarex.validate_compatible_image import verify as verify_image
        image_audit = verify_image(image_directory)
        v.exact(image_audit["image"], p["image"], "audited image build is not the measured deployment")
        h.require(image_audit["completion_sha256"] == IMAGE_COMPLETION_SHA, "audited image completion differs")
    return {"schema": f"zerorun.recovered-controlled-handoff-reconciliation.v{version}",
        "reconciled": True, "record_integrity": integrity, "controlled_handoffs": inner,
        "descriptive_variability": descriptive_variability(raw_c),
        "all_selected_cases_attempted": True, "all_selected_cases_completed": version == 6,
        "fixture_corrected_repeat": version == 6, "fixture_correction": correction,
        "preflight_reconciliation": launch_result, "runtime_version": "0.5.1",
        "image": p["image"], "historical_common_image_setup_seconds": p["common_image_setup_seconds"],
        "image_setup_newly_incurred_in_this_repeat": False,
        "image_build_commands_independently_rechecked": image_audit["image_build_commands_independently_rechecked"],
        "compatible_image_preparation": image_audit,
        "host_continuity": {"state": "ORIGINAL_HOST_MONITOR_NOT_RECOVERED",
            "sampled_continuity_checks_passed": False, "uninterrupted_timing_certified": False,
            "basis": "Recovered guest records do not replace the missing original host observations."},
        "independent_human_replication": False, "pooled_with_historical_measurements": False,
        "natural_repeat_prevalence_established": False, "live_agent_speedup_established": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--version", type=int, choices=(5, 6), default=6)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.root / (PREFIX + "v" + str(args.version)) / "record-only",
                    args.root / ORIGINAL, version=args.version)
    raw = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if args.output:
        with args.output.open("xb") as stream:
            stream.write(raw)
    else:
        print(raw.decode(), end="")


if __name__ == "__main__":
    main()
