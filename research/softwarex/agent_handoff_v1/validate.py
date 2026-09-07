"""Read-only reconciliation of one producer receipt; never executes model/code."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as c


def bound(base, record, maximum=32 * 1024 * 1024):
    c.require(isinstance(record, dict) and set(record) == {"path", "bytes", "sha256"}, "binding shape")
    name = record["path"]
    c.require(isinstance(name, str) and name and "/" not in name and "\\" not in name and name not in {".", ".."}, "case-local file binding required")
    path = base / name
    c.require(path.is_file() and not path.is_symlink() and path.stat().st_size <= maximum, "bounded ordinary evidence file required")
    raw = path.read_bytes()
    c.require(type(record["bytes"]) is int and len(raw) == record["bytes"] and c.sha(raw) == record["sha256"], "file binding mismatch")
    return raw


def source_archive_inventory(raw):
    rows, total = [], 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = archive.getmembers()
        c.require(len(members) <= 20000, "final source member bound")
        names = set()
        for member in members:
            c.require(member.name.startswith("agent-final/"), "final archive prefix")
            name = member.name[len("agent-final/"):].rstrip("/")
            parts = name.split("/")
            c.require(name and all(part not in {"", ".", "..", ".git"} for part in parts)
                      and not any(char in name for char in "\\:\x00\r\n") and name not in names,
                      "unsafe or duplicate final source member")
            names.add(name)
            c.require(member.isfile() or member.isdir(), "final source special member")
            if member.isdir():
                rows.append({"path": name, "kind": "directory"})
            else:
                total += member.size
                c.require(0 <= member.size <= 32 * 1024 * 1024 and total <= 160 * 1024 * 1024, "expanded final source bound")
                content = archive.extractfile(member).read()
                rows.append({"path": name, "kind": "file", "bytes": len(content), "sha256": c.sha(content)})
    return sorted(rows, key=lambda row: row["path"])


def validate_receipt(path, *, directory=None):
    path = Path(path)
    case_dir = path.parent
    output = Path(directory) if directory is not None else case_dir.parent
    freeze_raw = (output / "freeze.json").read_bytes()
    freeze = c.strict(freeze_raw)
    c.require(freeze.get("schema") == "zerorun.agent-producer-freeze.v1", "freeze schema")
    c.require(freeze["cases"] == c.selected(freeze["ledger"]), "selected cases differ from fixed rule")
    c.require(freeze["model_requested"] == c.MODEL and freeze["reasoning_effort"] == c.EFFORT,
              "requested model/effort changed")
    c.require(freeze["timeout_seconds"] == c.SECONDS and freeze["output_limit_bytes"] == c.OUTPUT_LIMIT,
              "prospective limits changed")
    c.require(freeze["reference_patch_supplied_to_model"] is False and freeze["mcp_authority_created"] is False,
              "producer authority/reference scope changed")
    for name in ("common.py", "run.py", "validate.py", "PROTOCOL.md"):
        c.require(c.sha((HERE / name).read_bytes()) == freeze["study_sources"][name]["sha256"], "frozen study source mismatch")
    prep = c.strict((case_dir / "preparation.json").read_bytes())
    session = c.strict(path.read_bytes())
    started = c.strict((case_dir / "started.json").read_bytes())
    c.require(started == {key: session[key] for key in started}, "started/session mismatch")
    c.require(started["freeze_sha256"] == c.sha(freeze_raw), "run did not bind freeze")
    index = started["case_index"]
    c.require(type(index) is int and 0 <= index < len(freeze["cases"]), "invalid selected case index")
    selected = freeze["cases"][index]
    prep_raw = (case_dir / "preparation.json").read_bytes()
    prep_binding = freeze["preparations"][index]
    c.require(len(prep_raw) == prep_binding["bytes"] and c.sha(prep_raw) == prep_binding["sha256"], "preparation receipt changed after freeze")
    c.require(prep["case_id"] == session["case_id"] == selected["case_id"] and prep["repo"] == selected["repo"], "case identity mismatch")
    for prior_index in range(index):
        prior_path = output / f"case-{prior_index:02d}" / "session.json"
        prior = validate_receipt(prior_path, directory=output)
        c.require(prior["boundary_pass"] is True, "producer continued after boundary failure")
    if session["model_invocation_attempted"] is False:
        c.require(isinstance(session.get("error"), str) and session["error"], "noninvocation requires retained error")
        return {"case_id": selected["case_id"], "model_invocation_attempted": False,
                "boundary_pass": session["boundary_pass"] is True, "producer_completed": False,
                "patch_correctness_established": False}
    c.require(prep["ready"] is True and prep["reference_patch_applied"] is False, "invalid producer preparation")
    c.require(prep["targets"] == selected["targets"], "changed validation targets")
    metadata_raw = bound(case_dir, prep["metadata"])
    c.require(len(metadata_raw) == selected["metadata"]["bytes"] and c.sha(metadata_raw) == selected["metadata"]["sha256"], "selected metadata changed")
    metadata = c.strict(metadata_raw)
    rows = [item["row"] for item in metadata["rows"] if item.get("row", {}).get("instance_id") == selected["case_id"]]
    c.require(metadata.get("partial") is False and len(rows) == 1, "selected metadata row unavailable")
    row = rows[0]
    c.require(row["repo"] == selected["repo"] and row["base_commit"] == selected["base_commit"]
              and row["problem_statement"] == prep["public_issue"], "issue identity differs from original metadata")
    test_patch = bound(case_dir, prep["test_patch"])
    c.require(test_patch == row["test_patch"].encode() and c.sha(row["patch"].encode()) == prep["reference_patch_sha256"], "benchmark patch binding differs")
    client = freeze["client"]
    c.require(client["node"]["sha256"] == c.NODE_SHA and client["native"]["sha256"] == c.NATIVE_SHA
              and client["launcher"]["sha256"] == c.LAUNCHER_SHA,
              "client installation anchor mismatch")
    c.require(session.get("client_version") == "codex-cli 0.153.3", "actual CLI version differs")
    expected_prompt = c.prompt(prep["public_issue"], prep["targets"], client["test_python"]["path"]).encode()
    c.require(bound(case_dir, prep["prompt"]) == expected_prompt and session["prompt_sha256"] == c.sha(expected_prompt), "prompt binding differs")
    expected_command = c.command(client["launcher"]["path"], prep["workspace"], client["test_python"]["path"])
    c.require(session["command"] == prep["command"] == expected_command, "command/policy changed")
    required = {"stdout", "stderr", "returncode", "timed_out", "truncated", "analysis", "after", "git_after", "scope", "final_source", "client_after"}
    if not required <= session.keys():
        c.require(isinstance(session.get("error"), str) and session["error"] and session["boundary_pass"] is False,
                  "incomplete attempt must retain error and stop campaign")
        for key in ("stdout", "stderr", "tracked_patch", "final_source"):
            if key in session:
                bound(case_dir, session[key], 192 * 1024 * 1024 if key == "final_source" else c.OUTPUT_LIMIT + 1024)
        return {"case_id": selected["case_id"], "model_invocation_attempted": True, "boundary_pass": False,
                "producer_completed": False, "patch_correctness_established": False, "capture_incomplete": True,
                "error": session["error"]}
    raw = bound(case_dir, session["stdout"], c.OUTPUT_LIMIT + 1024)
    bound(case_dir, session["stderr"], c.OUTPUT_LIMIT + 1024)
    analysis = c.analyze_events(raw, session["returncode"], session["timed_out"], session["truncated"])
    c.require(analysis == session["analysis"], "raw event analysis differs")
    scope = c.changes(prep["before"], session["after"], prep["test_paths"])
    c.require(scope == session["scope"], "source-scope analysis differs")
    actual_boundary = analysis["boundary_pass"] and scope["source_scope_pass"] and session["git_after"] == prep["git_before"] and session["error"] is None
    c.require(actual_boundary is session["boundary_pass"], "boundary outcome differs")
    if session["git_after"] == prep["git_before"]:
        bound(case_dir, session["tracked_patch"])
    else:
        c.require("tracked_patch" not in session and session.get("tracked_patch_unavailable") == "Protected Git metadata changed; no post-model Git command executed.", "unsafe post-model Git handling")
    archive = bound(case_dir, session["final_source"], 192 * 1024 * 1024)
    c.require(source_archive_inventory(archive) == sorted(session["after"], key=lambda row: row["path"]), "final snapshot and source inventory differ")
    c.require(session["client_after"] == client, "post-model installation identity differs")
    return {"case_id": selected["case_id"], "model_invocation_attempted": True,
            "boundary_pass": actual_boundary, "producer_completed": analysis["cli_completed"],
            "source_patch_present": scope["source_patch_present"], "usage": analysis["usage"],
            "requested_model": c.MODEL, "observed_model_identifiers": analysis["observed_model_identifiers"],
            "patch_correctness_established": False, "fresh_oracle_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    print(json.dumps(validate_receipt(args.receipt), sort_keys=True))


if __name__ == "__main__":
    main()
