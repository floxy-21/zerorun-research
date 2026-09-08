"""Strict read-only reconciliation of one separately assisted SQLGlot repair.

This checker accepts only the exact sealed supplemental campaign. It never
executes application code, Git, containers, models, or the driver's run function.
The original six-case model result remains five verified fixes out of six.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from types import ModuleType

from research.softwarex.handoff_v1 import run as h, validate as v

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = "research/softwarex/evidence/agent-application-053-sqlglot-supplement-v1/record-only"
MANIFEST_SHA = "014e024adc3a70661eedb2891d61c61efeb57936bfcffebf6e13525d707b8da6"
DRIVER_SHA = "429e1ff8e834bcc4433651ed830fecbf722e9150ac56347efb0038f2fc0de81a"
PROTOCOL_SHA = "d25a1f2c32a0ec9367e189981a7adc07102d21dbf62893786b98a200cafb2625"
COMPLETION_SHA = "dd4db77e67ba92ba9e42d1196242ffb04849c59058aaa2b38a5032996bf7a594"
ARCHIVE_SHA = "bbf788801619c220a0c69e27d1e8d43762d502dbb9876eea534a69a8e2559fd9"
MAX_BYTES = 192 * 1024 * 1024
FAILED_NODES = ["tests/dialects/test_duckdb.py::TestDuckDB::test_duckdb",
                "tests/dialects/test_snowflake.py::TestSnowflake::test_timestamps"]
ISSUE_NODE = "tests/test_expressions.py::TestExpressions::test_transform_with_parent_mutation"


def exact_inventory(directory):
    """Require all 96 original files, no links, omissions or unrecorded additions."""
    directory = Path(directory)
    h.require(directory.is_dir() and not directory.is_symlink()
              and not getattr(directory.lstat(), "st_file_attributes", 0) & 0x400, "linked supplemental root refused")
    raw = h.ordinary(directory / "RECORD_MANIFEST.json", MAX_BYTES)
    h.require(h.sha(raw) == MANIFEST_SHA, "supplement sealed manifest differs")
    manifest = h.strict(raw)
    h.require(set(manifest) == {"files", "workspaces_included"}
              and manifest["workspaces_included"] is False, "supplement export scope differs")
    rows = manifest["files"]
    h.require(isinstance(rows, list) and len(rows) == 96, "supplement file denominator differs")
    names = []
    for row in rows:
        h.require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}, "supplement inventory row differs")
        h.bound(directory, row, MAX_BYTES); names.append(row["path"])
    actual = []
    for current, dirs, files in os.walk(directory, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(current) / name
            h.require(not path.is_symlink() and not getattr(path.lstat(), "st_file_attributes", 0) & 0x400,
                      "linked supplemental evidence refused")
        for name in files:
            relative = (Path(current) / name).relative_to(directory).as_posix()
            if relative != "RECORD_MANIFEST.json": actual.append(relative)
    h.require(len(names) == len(set(names)) and sorted(actual) == sorted(names), "supplement capture inventory is not exhaustive")
    return [*rows, {"path": "RECORD_MANIFEST.json", "bytes": len(raw), "sha256": MANIFEST_SHA}]


def verify(directory):
    directory = Path(directory)
    inventory = exact_inventory(directory)
    for name, expected in (("driver.py", DRIVER_SHA), ("protocol.json", PROTOCOL_SHA),
                           ("completion.json", COMPLETION_SHA)):
        h.require(h.sha(h.ordinary(directory / name, MAX_BYTES)) == expected, "frozen supplement source/receipt differs")
    # Only this pinned, reviewed driver is loaded. compile/exec avoids creating
    # bytecode in the immutable capture. Its run/main entry points are not called.
    module = ModuleType("zerorun_frozen_sqlglot_supplement_checker")
    module.__file__ = str(directory / "driver.py")
    exec(compile(h.ordinary(directory / "driver.py"), module.__file__, "exec"), module.__dict__)
    observed = module.check(directory)
    h.require(observed["supplement_passed"] is True and observed["same_original_99_node_ids"] is True
              and observed["original_exit_code"] == 1 and observed["repaired_exit_code"] == 0
              and observed["repaired_passes"] == 99 and observed["original_failed_nodes"] == FAILED_NODES,
              "supplement classification differs from the recorded full targets")
    original = v.oracle(directory, "original-oracle", "original-capture")["result"]["verdict"]
    repaired = v.oracle(directory, "repaired-oracle", "repaired-capture")["result"]["verdict"]
    h.require(len(original["nodes"]) == len(repaired["nodes"]) == 99
              and set(original["nodes"]) == set(repaired["nodes"]), "original full node set changed")
    h.require(original["nodes"][ISSUE_NODE]["call"] == repaired["nodes"][ISSUE_NODE]["call"] == "passed",
              "issue-specific transform result changed")
    return {**observed, "record_manifest_sha256": MANIFEST_SHA, "record_files": len(inventory),
            "recovered_archive_sha256": ARCHIVE_SHA, "driver_sha256": DRIVER_SHA,
            "original_passes": sum(row["call"] == "passed" for row in original["nodes"].values()),
            "issue_specific_transform_regression_already_passed": True,
            "changed_production_paths": sorted(module.CHANGED), "original_agent_patch_preserved": True,
            "original_test_configuration_and_targets_preserved": True, "independent_human_repair_claimed": False,
            "consumer_evaluated": False, "performance_benefit_claimed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=ROOT / DIRECTORY)
    args = parser.parse_args()
    print(json.dumps(verify(args.directory), indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
