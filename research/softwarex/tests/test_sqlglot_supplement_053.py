"""Offline assertions against the immutable assisted repair, with adversarial edits."""
from pathlib import Path
import hashlib
import json
import shutil

import pytest

from research.softwarex import sqlglot_supplement_053 as s

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / s.DIRECTORY


def test_all_original_targets_pass_only_in_the_separate_assisted_followup():
    before = {p.relative_to(EVIDENCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in EVIDENCE.rglob("*") if p.is_file()}
    result = s.verify(EVIDENCE)
    assert result["supplement_passed"] and result["same_original_99_node_ids"]
    assert result["repaired_passes"] == 99 and result["original_passes"] == 97
    assert len(result["original_failed_nodes"]) == 2
    assert result["original_primary_verified_fixes"] == 5 and result["original_selected_cases"] == 6
    assert result["new_model_calls"] == 0 and not result["consumer_evaluated"]
    assert not result["performance_benefit_claimed"] and not result["independent_human_repair_claimed"]
    assert result["original_agent_patch_preserved"] and result["original_test_configuration_and_targets_preserved"]
    assert result["issue_specific_transform_regression_already_passed"]
    after = {p.relative_to(EVIDENCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in EVIDENCE.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.parametrize("mutation", ["oracle", "driver", "patch", "completion", "missing", "extra", "rehashed-manifest"])
def test_tamper_missing_and_extra_records_are_rejected(tmp_path, mutation):
    records = tmp_path / "records"
    shutil.copytree(EVIDENCE, records)
    targets = {"oracle": "repaired-oracle.json", "driver": "driver.py", "patch": "production-followup.patch",
               "completion": "completion.json"}
    if mutation in targets:
        (records / targets[mutation]).write_bytes(b"altered\n")
    elif mutation == "missing":
        (records / "protocol.json").unlink()
    elif mutation == "extra":
        (records / "unrecorded.txt").write_text("unrecorded", encoding="utf-8")
    else:
        path = records / "repaired-oracle.json"
        path.write_bytes(b"altered\n")
        manifest = json.loads((records / "RECORD_MANIFEST.json").read_text(encoding="utf-8"))
        row = next(row for row in manifest["files"] if row["path"] == "repaired-oracle.json")
        row.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        (records / "RECORD_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        s.verify(records)


def test_exact_inventory_binds_both_source_archives_and_both_full_oracles():
    rows = s.exact_inventory(EVIDENCE)
    names = {row["path"] for row in rows}
    assert len(rows) == len(names) == 97
    assert {"prepared/case-02/base-source.tar.gz", "prepared/case-02/final-source.tar.gz",
            "original-oracle.json", "repaired-oracle.json", "production-followup.patch",
            "SUPPLEMENT_PROTOCOL.md", "driver.py", "RECORD_MANIFEST.json"} <= names


def test_unavailable_evidence_is_not_a_success(tmp_path):
    with pytest.raises(ValueError):
        s.verify(tmp_path / "unavailable")
