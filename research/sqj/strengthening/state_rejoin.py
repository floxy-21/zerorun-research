"""A bounded real-agent-derived result-only reconstruction, not agent replay.

Recorded shell strings are never executed. Only reviewed pytest argv can run
in the existing pinned Docker lab. No operator authority is created. Private
laboratory cache authentication must not be published. This is a purposive
mechanism case, not a representative benchmark or a TVCache reproduction.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
BASE = "31c8f98343bd871d51c5bf6d73c3573c6cbde72e"
ARCHIVE_SHA = "6264853af7ab12c7265be9b10115007fe4977de9b1636c1d86875ca1a0654425"
METADATA_SHA = "b70d84f366c70456c2c8aeedd1ba675b70b71270b7237282709f90924f02d828"
PLAN_SHA = "a157e97ceac0d2c3f9e334fdfc1395979429dfbdc807bf51d03ad432e87b4cfb"
TRACE_SHA = "16a0438ab2bc234587dc50129320fbb64063b7c90927fcb501f39eeea0c434ae"
IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
PREFIX = "/workspace/joke2k__django-environ__0.6/"
EDIT_PATHS = {"test_reproduce_issue.py", "environ/environ.py", "tests/test_search.py"}
TARGET = ["tests/test_search.py", "-v"]
COLLECTION = ["tests/", "-k", "elasticsearch7", "-v"]
ENGINE_STATE = {".zerorun", ".zerorun.json"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def save(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def read_bound(path, expected):
    if path.is_symlink() or not path.is_file():
        raise ValueError("evidence must be an ordinary file")
    raw = path.read_bytes()
    if digest(raw) != expected:
        raise ValueError("frozen evidence hash mismatch: " + path.name)
    return raw


def literal_path(value):
    if not isinstance(value, str) or not value or any(c in value for c in "\\:\x00"):
        raise ValueError("unsafe source path")
    parts = value.split("/")
    if any(p in {"", ".", ".."} for p in parts) or PurePosixPath(value).is_absolute():
        raise ValueError("noncanonical source path")
    return parts


def archive_files(raw):
    """Read bounded ordinary members without tar extraction or code import."""
    result = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > 100:
            raise ValueError("archive member bound exceeded")
        for member in members:
            parts = literal_path(member.name.rstrip("/"))
            if parts[0] != "django-environ-" + BASE:
                raise ValueError("unexpected archive root")
            if not (member.isfile() or member.isdir()):
                raise ValueError("links and special archive members are refused")
            if len(parts) == 1:
                if not member.isdir():
                    raise ValueError("archive root must be directory")
                continue
            relative = "/".join(parts[1:])
            if relative in result:
                raise ValueError("duplicate archive member")
            if member.isdir():
                result[relative] = None
                continue
            total += member.size
            if member.size < 0 or total > 1024 * 1024:
                raise ValueError("archive byte bound exceeded")
            result[relative] = archive.extractfile(member).read()
    if "LICENSE.txt" not in result or "tests/test_search.py" not in result:
        raise ValueError("required licensed source missing")
    return result


def apply_editor(root, call):
    """Apply only three reviewed literal paths; never interpret shell."""
    if call["tool"] != "str_replace_editor":
        raise ValueError("not an editor operation")
    args = call["arguments"]
    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.startswith(PREFIX):
        raise ValueError("unexpected editor workspace")
    relative = raw_path[len(PREFIX):]
    if relative not in EDIT_PATHS:
        raise ValueError("unreviewed editor path")
    path = root.joinpath(*literal_path(relative))
    cursor = root
    for part in literal_path(relative):
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("link-like editor path")
    if args.get("command") == "create":
        if set(args) != {"command", "path", "file_text"} or not isinstance(args["file_text"], str):
            raise ValueError("malformed create")
        with path.open("xb") as stream:
            stream.write(args["file_text"].encode("utf-8"))
        before = None
    elif args.get("command") == "str_replace":
        if set(args) != {"command", "path", "old_str", "new_str"}:
            raise ValueError("malformed replacement")
        old, new = args["old_str"], args["new_str"]
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError("invalid replacement strings")
        original = path.read_bytes()
        before = digest(original)
        if original.count(old.encode("utf-8")) != 1:
            raise ValueError("replacement requires exactly one old-string match")
        path.write_bytes(original.replace(old.encode("utf-8"), new.encode("utf-8"), 1))
    else:
        raise ValueError("unsupported editor command")
    return {"call_index": call["index"], "call_id": call["call_id"], "path": relative,
            "before_sha256": before, "after_sha256": digest(path.read_bytes())}


def tree_identity(root, *, exclude=()):
    """All descendant names and bytes; no gitignore/generated-file heuristic."""
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in exclude:
            continue
        details = path.lstat()
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
            raise ValueError("link-like source refused")
        if path.is_dir():
            rows.append({"path": relative, "kind": "directory"})
        elif path.is_file():
            raw = path.read_bytes()
            rows.append({"path": relative, "kind": "file", "bytes": len(raw), "sha256": digest(raw)})
        else:
            raise ValueError("special source refused")
    return {"sha256": digest(encoded(rows)), "records": rows,
            "exclusions": sorted(exclude), "metadata_scope": "names, directories and file content; execution normalizes timestamps/modes"}


def reconstruct(evidence, output):
    plan = json.loads(read_bound(evidence / "django-environ-state-rejoin-candidate-v1.json", PLAN_SHA))
    metadata = json.loads(read_bound(evidence / "django-environ-metadata-response-v1.json", METADATA_SHA))
    if metadata["partial"] is not False or len(metadata["rows"]) != 1 or metadata["rows"][0]["truncated_cells"]:
        raise ValueError("metadata is incomplete")
    task = metadata["rows"][0]["row"]
    if task["base_commit"] != BASE or task["instance_id"] != "joke2k__django-environ-322":
        raise ValueError("wrong task metadata")
    files = archive_files(read_bound(evidence / "django-environ-31c8f983-source.tar.gz", ARCHIVE_SHA))
    root = output / "workspace"
    root.mkdir()
    for relative, raw in sorted(files.items()):
        path = root.joinpath(*literal_path(relative))
        if raw is None:
            path.mkdir(exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(raw)
            path.chmod(0o644)
    edits = []
    if [c["index"] for c in plan["prior_recorded_source_mutations"]] != [19, 21, 22, 24]:
        raise ValueError("prior reconstruction operation mismatch")
    for call in plan["prior_recorded_source_mutations"]:
        edits.append(apply_editor(root, call))
    initial = tree_identity(root)
    changed = apply_editor(root, plan["segment"][1])
    changed_identity = tree_identity(root)
    restored = apply_editor(root, plan["segment"][2])
    restored_identity = tree_identity(root)
    if initial != restored_identity or initial == changed_identity:
        raise ValueError("complete reconstructed source does not rejoin")
    receipt = {"base_commit": BASE, "metadata_sha256": METADATA_SHA, "archive_sha256": ARCHIVE_SHA,
               "trace_sha256": TRACE_SHA, "plan_sha256": PLAN_SHA, "prior_editor_receipts": edits,
               "seed_source": initial, "changed_editor": changed, "changed_source": changed_identity,
               "restored_editor": restored, "restored_source": restored_identity,
               "full_reconstructed_source_rejoins": True, "prior_shell_commands_executed": False,
               "scope": "source reconstruction only; original runtime state not reconstructed"}
    save(output / "reconstruction.json", receipt)
    return root, plan, receipt


def verdict(capture):
    rows = dict(zip(capture["nodeids"], capture["outcomes"], strict=True))
    if len(rows) != len(capture["nodeids"]) or not capture["complete_per_node_outcomes"]:
        raise ValueError("incomplete oracle verdict")
    return {"exit_code": capture["exit_code"], "node_outcomes": rows}


def run_requests(bench, api, recovery, loaded, root, plan, output, runtime_identity):
    support = output / "oracle-support"
    support.mkdir()
    (support / "benchmark_shadow_plugin.py").write_text(bench._SHADOW_PLUGIN, encoding="utf-8")
    private = output / "private-cache-authentication-NOT-FOR-PUBLICATION"
    private.mkdir(mode=0o700)
    requests = [("seed-26", "rejoin-target", TARGET, 0, 14),
                ("changed-27-counterfactual-oracle", "rejoin-target", TARGET, 0, 15),
                ("restored-collection-29", "rejoin-collection", COLLECTION, 5, 0),
                ("restored-repeat-30", "rejoin-target", TARGET, 0, 14)]
    prior = sorted(plan["prior_non_mutation_tool_calls"] + plan["prior_recorded_source_mutations"], key=lambda c: c["index"])
    history = [c["call_id"] for c in prior]
    rows = []
    with patch.dict(os.environ, {"ZERORUN_TRUST_ROOT": str(private), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}):
        for index, (label, task_name, targets, expected_exit, expected_nodes) in enumerate(requests):
            if index == 1:
                apply_editor(root, plan["segment"][1])
                history.append(plan["segment"][1]["call_id"])
            if index == 2:
                apply_editor(root, plan["segment"][2])
                history.extend([plan["segment"][2]["call_id"], plan["segment"][3]["call_id"]])
            if index == 3:
                history.append(plan["segment"][4]["call_id"])
            request = output / label
            request.mkdir()
            before = tree_identity(root, exclude=ENGINE_STATE)
            stdout, stderr = io.BytesIO(), io.BytesIO()
            started = time.perf_counter()
            result = api.run_task(loaded, loaded.tasks[task_name], stdout=stdout, stderr=stderr)
            elapsed = (time.perf_counter() - started) * 1000
            product = {**result.as_dict(), "request_wall_ms": elapsed,
                       "stdout": stdout.getvalue().decode(errors="replace"), "stderr": stderr.getvalue().decode(errors="replace")}
            save(request / "product.json", product)
            fresh = recovery.fresh_capture(bench, root, targets, IMAGE, support, request)
            save(request / "fresh.json", fresh)
            current_verdict = verdict(fresh["capture"])
            after = tree_identity(root, exclude=ENGINE_STATE)
            row = {"label": label, "source_before": before, "source_after": after,
                   "history_reference_key": digest(encoded({"prior_event_ids": history, "command": targets})),
                   "history_reference_is_not_tvcache": True, "product": product,
                   "oracle_verdict": current_verdict, "oracle_wall_ms": fresh["wall_ms"],
                   "expected_exit_code": expected_exit, "expected_nodes": expected_nodes,
                   "source_unchanged_by_execution": before == after,
                   "expected_verdict_observed": result.exit_code == expected_exit == fresh["exit_code"]
                       and len(current_verdict["node_outcomes"]) == expected_nodes
                       and all(v == {"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}
                               for v in current_verdict["node_outcomes"].values())}
            save(request / "observation.json", row)
            rows.append(row)
            if not row["source_unchanged_by_execution"] or not row["expected_verdict_observed"]:
                raise ValueError("source drift or oracle mismatch; case stopped")
            if index == 0:
                history.append(plan["segment"][0]["call_id"])
    authorities = list(private.glob("repositories/*/authorities/*.json"))
    statuses = [row["product"]["status"] for row in rows]
    result = {"schema": "zerorun.controlled-agent-source-rejoin.v1", "requests": rows,
              "actual_product_statuses": statuses, "cache_behavior_expected": statuses == ["MISS_EXECUTED", "MISS_EXECUTED", "MISS_FAILED", "HIT_REUSED"],
              "seed_repeat_source_equal": rows[0]["source_before"] == rows[3]["source_before"],
              "seed_changed_source_different": rows[0]["source_before"] != rows[1]["source_before"],
              "seed_repeat_key_equal": rows[0]["product"]["cache_key"] == rows[3]["product"]["cache_key"],
              "seed_changed_key_different": rows[0]["product"]["cache_key"] != rows[1]["product"]["cache_key"],
              "seed_repeat_fresh_verdict_equal": rows[0]["oracle_verdict"] == rows[3]["oracle_verdict"],
              "history_reference_key_diverged": rows[0]["history_reference_key"] != rows[3]["history_reference_key"],
              "operator_authority_created": bool(authorities), "runtime_identity": runtime_identity,
              "runtime_image": IMAGE, "original_environment_reproduced": False,
              "scope": "purposive result-only controlled reconstruction; changed-state oracle is extra instrumentation, not an observed agent test call; no autonomous agent, raw-output equivalence, TVCache comparison or general speedup claim"}
    checks = ("cache_behavior_expected", "seed_repeat_source_equal", "seed_changed_source_different",
              "seed_repeat_key_equal", "seed_changed_key_different", "seed_repeat_fresh_verdict_equal",
              "history_reference_key_diverged")
    result["completed"] = all(result[k] for k in checks) and not authorities
    save(output / "summary.json", result)
    return result


def run_lab(engine, root, plan, output):
    if os.name != "posix":
        raise ValueError("execution requires existing POSIX Docker laboratory")
    sys.path.insert(0, str(engine))
    from tools import product_generalization_benchmark as bench
    from zerorun import api
    from zerorun.manifest import load_manifest
    from research.sqj.strengthening.randomized_replication import load_recovery
    recovery = load_recovery()
    # Existing image is never pulled. Only the established hash-locked runtime
    # setup may acquire wheels, after the separate timing campaign is finished.
    with bench._frozen_dependency_layer(root, runtime_image=IMAGE, extra_requirements=()) as layer:
        setup = bench._materialize_frozen_pytest_environment(root, dependency_layer=layer, runtime_image=IMAGE, extra_requirements=())
        save(output / "dependency-setup.json", setup)
        runtime_identity = tree_identity(root / ".zerorun-env")
        manifest_path = root / ".zerorun.json"
        manifest = json.loads(manifest_path.read_bytes())
        base_task = manifest["tasks"].pop("pytest-generalization")
        inputs = sorted(p.name for p in root.iterdir() if p.name not in ENGINE_STATE)
        base_task.update(cacheable=True, inputs=inputs)
        target_task = dict(base_task, command=base_task["command"] + TARGET)
        collection_task = dict(base_task, command=base_task["command"] + COLLECTION)
        manifest["tasks"] = {"rejoin-target": target_task, "rejoin-collection": collection_task}
        manifest_path.write_bytes(encoded(manifest) + b"\n")
        save(output / "manifest.json", manifest)
        loaded = load_manifest(manifest_path)
        return run_requests(bench, api, recovery, loaded, root, plan, output, runtime_identity)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=HERE / "evidence")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", type=Path)
    parser.add_argument("--execute-reviewed-lab", action="store_true")
    args = parser.parse_args(argv)
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    save(output / "invocation.json", {"utc": datetime.now(timezone.utc).isoformat(),
         "producer_sha256": digest(Path(__file__).read_bytes()), "execute": args.execute_reviewed_lab,
         "purposive_case": True, "full_agent_replay": False})
    try:
        root, plan, _ = reconstruct(args.evidence, output)
        if args.execute_reviewed_lab:
            if args.engine is None:
                raise ValueError("execution requires explicit engine path")
            result = run_lab(args.engine.resolve(strict=True), root, plan, output)
            return int(not result["completed"])
        print("Source reconstruction validated; no test commands executed.")
        return 0
    except Exception as error:
        save(output / "failure.json", {"error_type": type(error).__name__, "error": str(error), "retry_attempted": False})
        raise


if __name__ == "__main__":
    raise SystemExit(main())
