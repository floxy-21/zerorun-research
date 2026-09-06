"""No third-party source execution: tests exercise inert reconstruction."""
import io
import json
from pathlib import Path
import tarfile
from types import SimpleNamespace

import pytest

from research.sqj.strengthening import state_rejoin as s


def call(command="str_replace", path="tests/test_search.py", **fields):
    return {"tool": "str_replace_editor", "index": 27, "call_id": "fixture",
            "arguments": {"command": command, "path": s.PREFIX + path, **fields}}


@pytest.mark.parametrize("path", ["../escape", "/escape", "a//b", "a/./b", "a\\b", "C:x", "", "\x00"])
def test_literal_path_refuses(path):
    with pytest.raises(ValueError):
        s.literal_path(path)


def test_replacement_is_literal_not_execution(tmp_path):
    (tmp_path / "tests").mkdir()
    target = tmp_path / "tests/test_search.py"
    target.write_bytes(b"old")
    text = "__import__('os').system('unexpected')"
    s.apply_editor(tmp_path, call(old_str="old", new_str=text))
    assert target.read_bytes() == text.encode()


@pytest.mark.parametrize("content", [b"missing", b"old old"])
def test_replacement_requires_unique_match(tmp_path, content):
    (tmp_path / "tests").mkdir()
    target = tmp_path / "tests/test_search.py"
    target.write_bytes(content)
    with pytest.raises(ValueError):
        s.apply_editor(tmp_path, call(old_str="old", new_str="new"))
    assert target.read_bytes() == content


@pytest.mark.parametrize("command", ["undo_edit", "insert", "view", "bash"])
def test_refuses_other_editor_operations(tmp_path, command):
    with pytest.raises(ValueError):
        s.apply_editor(tmp_path, call(command))


def test_create_does_not_overwrite(tmp_path):
    target = tmp_path / "test_reproduce_issue.py"
    target.write_bytes(b"user")
    with pytest.raises(FileExistsError):
        s.apply_editor(tmp_path, call("create", "test_reproduce_issue.py", file_text="new"))
    assert target.read_bytes() == b"user"


def test_out_of_scope_editor_path_refused(tmp_path):
    with pytest.raises(ValueError):
        s.apply_editor(tmp_path, call(path="another.py", old_str="a", new_str="b"))


def test_rejoin_includes_new_root_file_and_empty_directories(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_search.py").write_bytes(b"old")
    first = s.tree_identity(tmp_path)
    s.apply_editor(tmp_path, call(old_str="old", new_str="new"))
    assert first != s.tree_identity(tmp_path)
    s.apply_editor(tmp_path, call(old_str="new", new_str="old"))
    assert first == s.tree_identity(tmp_path)
    (tmp_path / "untracked.py").write_bytes(b"addition")
    assert first != s.tree_identity(tmp_path)
    before_directory = s.tree_identity(tmp_path)
    (tmp_path / "empty").mkdir()
    assert before_directory != s.tree_identity(tmp_path)


def test_evidence_hash_mismatch_refused(tmp_path):
    file = tmp_path / "data"
    file.write_bytes(b"one")
    with pytest.raises(ValueError):
        s.read_bound(file, s.digest(b"two"))


def tar_with(name, kind=tarfile.REGTYPE):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        archive.addfile(member, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
    return raw.getvalue()


@pytest.mark.parametrize("name", ["../escape", "other/file", "django-environ-" + s.BASE + "/../escape"])
def test_archive_path_refused(name):
    with pytest.raises(ValueError):
        s.archive_files(tar_with(name))


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE])
def test_archive_special_member_refused(kind):
    with pytest.raises(ValueError):
        s.archive_files(tar_with("django-environ-" + s.BASE + "/file", kind))


def test_incomplete_verdict_refused():
    with pytest.raises(ValueError):
        s.verdict({"nodeids": ["a"], "outcomes": [{}], "complete_per_node_outcomes": False, "exit_code": 0})


def test_empty_collection_retains_exit_five():
    result = s.verdict({"nodeids": [], "outcomes": [], "complete_per_node_outcomes": True, "exit_code": 5})
    assert result == {"exit_code": 5, "node_outcomes": {}}


def test_recorded_reconstruction_all_files_rejoin(tmp_path):
    evidence = s.HERE / "evidence"
    root, plan, receipt = s.reconstruct(evidence, tmp_path)
    assert receipt["full_reconstructed_source_rejoins"]
    assert receipt["seed_source"] == receipt["restored_source"]
    assert receipt["seed_source"] != receipt["changed_source"]
    assert [e["call_index"] for e in receipt["prior_editor_receipts"]] == [19, 21, 22, 24]
    assert (root / "test_reproduce_issue.py").read_text(encoding="utf-8") == plan["prior_recorded_source_mutations"][-1]["arguments"]["new_str"]
    assert (root / "LICENSE.txt").is_file()


def test_save_is_append_only(tmp_path):
    output = tmp_path / "receipt.json"
    s.save(output, {"a": 1})
    with pytest.raises(FileExistsError):
        s.save(output, {"a": 2})
    assert json.loads(output.read_bytes()) == {"a": 1}


@pytest.mark.parametrize("reuse", [True, False])
def test_result_only_driver_requires_real_hit_and_fresh_verdict(tmp_path, reuse):
    root, plan, _ = s.reconstruct(s.HERE / "evidence", tmp_path)
    statuses = iter(["MISS_EXECUTED", "MISS_EXECUTED", "MISS_FAILED", "HIT_REUSED" if reuse else "MISS_EXECUTED"])
    task_names = {"rejoin-target": "target", "rejoin-collection": "collection"}

    def run_task(loaded, task, **kwargs):
        status = next(statuses)
        code = 5 if task == "collection" else 0
        key = s.tree_identity(root)["sha256"] + task
        return SimpleNamespace(exit_code=code, as_dict=lambda: {"status": status, "exit_code": code, "cache_key": key})

    def fresh_capture(bench, work, targets, image, support, request):
        changed = plan["segment"][1]["arguments"]["new_str"].encode() in (work / "tests/test_search.py").read_bytes()
        count = 0 if targets == s.COLLECTION else 15 if changed else 14
        return {"wall_ms": 1, "exit_code": 5 if not count else 0,
                "capture": {"exit_code": 5 if not count else 0, "nodeids": [str(i) for i in range(count)],
                            "outcomes": [{"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}] * count,
                            "complete_per_node_outcomes": True}}

    result = s.run_requests(SimpleNamespace(_SHADOW_PLUGIN="# inert fixture"),
                            SimpleNamespace(run_task=run_task), SimpleNamespace(fresh_capture=fresh_capture),
                            SimpleNamespace(tasks=task_names), root, plan, tmp_path, {})
    assert result["completed"] is reuse
    assert result["seed_repeat_source_equal"]
    assert result["seed_changed_key_different"]
    assert result["seed_repeat_fresh_verdict_equal"]
    assert result["requests"][2]["expected_exit_code"] == 5
    assert not result["operator_authority_created"]
