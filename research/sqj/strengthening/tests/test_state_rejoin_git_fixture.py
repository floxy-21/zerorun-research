"""Git fixture correction tests; original producer and baseline guard remain frozen."""
import json

import pytest

from research.sqj.strengthening import state_rejoin_v2 as p
from research.sqj.strengthening import run_recovered_state_rejoin_v2 as w
from research.sqj.strengthening import analyze_state_rejoin as a
from research.sqj.strengthening.tests.test_analyze_state_rejoin import case, bound, write
from research.sqj.strengthening.tests.test_recovered_state_rejoin import recovered
from tools.product_generalization_benchmark import _plain_git_mask_args


def test_real_git_fixture_satisfies_unchanged_plain_baseline_guard(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "reviewed.py").write_text("x = 1\n")
    before = p.tree_identity(root)
    p.initialize_lab_git(root, tmp_path)
    assert _plain_git_mask_args(root) == ["--tmpfs", "/workspace/.git:rw,nosuid,nodev,noexec,size=1m"]
    assert p.tree_identity(root, exclude={".git"})["records"] == before["records"]
    assert not (root / ".git/hooks").exists()
    receipt = json.loads((tmp_path / "git-fixture.json").read_bytes())
    assert receipt["exit_code"] == receipt["canonical_root_exit_code"] == 0
    assert receipt["templates_disabled"] and not receipt["history_reproduced"]


def test_git_initialization_refuses_existing_metadata(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / ".git").mkdir()
    with pytest.raises(ValueError, match="unexpectedly has Git metadata"):
        p.initialize_lab_git(root, tmp_path)


@pytest.fixture
def corrected(recovered):
    root, prior = recovered
    case = root / "case"
    invocation = a.read_json(case / "invocation.json")
    invocation["producer_sha256"] = a.RUNNER_V2_SHA
    write(case / "invocation.json", invocation)
    summary = a.read_json(case / "summary.json")
    for row in summary["requests"]:
        for field in ("source_before", "source_after"):
            row[field] = a.identity(row[field]["records"], {".git", ".zerorun", ".zerorun.json"})
        write(case / row["label"] / "observation.json", row)
    write(case / "summary.json", summary)
    write(case / "git-fixture.json", {"schema": "zerorun.state-rejoin-git-fixture.v1", "exit_code": 0,
        "canonical_root_exit_code": 0, "templates_disabled": True, "network_used": False,
        "history_reproduced": False, "ordinary_git_directory": True,
        "git_metadata_excluded_from_task_inputs": True, "git_metadata_masked_by_existing_container_contract": True,
        "command": ["git", "init", "--template=", "--initial-branch=main", "/fixture/workspace"],
        "canonical_root": "/fixture/workspace", "stdout": "fixture", "stderr": ""})
    protocol = a.read_json(root / "protocol.json")
    protocol["schema"] = "zerorun.recovered-state-rejoin.protocol.v2"
    protocol["runner"] = w.file_record(w.HERE / "state_rejoin_v2.py", label="strengthening/state_rejoin_v2.py")
    protocol["wrapper"] = w.file_record(w.HERE / "run_recovered_state_rejoin_v2.py", label="strengthening/run_recovered_state_rejoin_v2.py")
    write(root / "protocol.json", protocol)
    completion = a.read_json(root / "completion.json")
    completion["schema"] = "zerorun.recovered-state-rejoin.completion.v2"
    completion["protocol_sha256"] = a.digest(root / "protocol.json")
    completion["runner_after"] = protocol["runner"]
    completion["wrapper_after"] = protocol["wrapper"]
    completion["case_summary_file"] = w.file_record(case / "summary.json", label="case/summary.json")
    write(root / "completion.json", completion)
    return root, prior


def test_strict_analysis_accepts_corrected_fixture_with_explicit_exclusion(corrected):
    root, prior = corrected
    result = a.analyze(root, replication_directory=prior)
    assert result["completed"], result["errors"]
    assert result["git_fixture_correction"] and result["case_producer_sha256"] == a.RUNNER_V2_SHA
    assert result["fresh_outcomes"] == [14, 15, 0, 14]


@pytest.mark.parametrize("key,value", [("templates_disabled", False), ("network_used", True),
    ("git_metadata_masked_by_existing_container_contract", False), ("history_reproduced", True)])
def test_corrected_fixture_does_not_waive_contract(corrected, key, value):
    root, prior = corrected
    receipt = a.read_json(root / "case/git-fixture.json")
    receipt[key] = value
    write(root / "case/git-fixture.json", receipt)
    assert not a.analyze(root, replication_directory=prior)["completed"]
