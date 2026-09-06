"""Independent offline reconciliation of one controlled source-rejoin case.

Does not import the case producer or execute recorded code. A completed result
requires all four requests and wrapper provenance; incomplete receipts are
retained as errors, never converted into a favorable partial case.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

from research.sqj.analyze_campaign import read_json, require, numeric, digest
from research.sqj.strengthening.analyze_replication import canonical, same, hash_value, timestamp
from research.sqj.strengthening import analyze_replication

HERE = Path(__file__).resolve().parent
BASE = "31c8f98343bd871d51c5bf6d73c3573c6cbde72e"
RUNNER_SHA = "70732ee170c789ddcf8e66b8d8ecb2f147ec1a5c3e294d5b8d0e8d2ba6449db1"
WRAPPER_SHA = "8d9b689619293df2e015eeb34cf585063a1a6e0287f86bdeda11e913f6a329f0"
BINDINGS = {
    "django-environ-31c8f983-source.tar.gz": "6264853af7ab12c7265be9b10115007fe4977de9b1636c1d86875ca1a0654425",
    "django-environ-metadata-response-v1.json": "b70d84f366c70456c2c8aeedd1ba675b70b71270b7237282709f90924f02d828",
    "django-environ-state-rejoin-candidate-v1.json": "a157e97ceac0d2c3f9e334fdfc1395979429dfbdc807bf51d03ad432e87b4cfb",
    "trace-cohort-2/rows/row-51126.json": "16a0438ab2bc234587dc50129320fbb64063b7c90927fcb501f39eeea0c434ae",
}
IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
PREFIX = "/workspace/joke2k__django-environ__0.6/"
TARGET = ["tests/test_search.py", "-v"]
COLLECTION = ["tests/", "-k", "elasticsearch7", "-v"]
LABELS = ["seed-26", "changed-27-counterfactual-oracle", "restored-collection-29", "restored-repeat-30"]
STATUSES = ["MISS_EXECUTED", "MISS_EXECUTED", "MISS_FAILED", "HIT_REUSED"]
COUNTS = [14, 15, 0, 14]
CODES = [0, 0, 5, 0]
PASS = {"setup": "passed", "call": "passed", "teardown": "passed", "wasxfail": False}
METADATA_SCOPE = "names, directories and file content; execution normalizes timestamps/modes"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def path_parts(name):
    require(isinstance(name, str) and name and not any(c in name for c in "\\:\x00"), "unsafe identity path")
    parts = name.split("/")
    require(not PurePosixPath(name).is_absolute() and not any(p in ("", ".", "..") for p in parts), "noncanonical identity path")
    return parts


def identity(records, exclusions=()):
    # The experiment enumerates sorted POSIX Path objects, which compare path
    # components rather than slash-containing strings (pkg/ before pkg-1/).
    records = sorted(records, key=lambda row: PurePosixPath(row["path"]))
    return {"records": records, "sha256": sha(canonical(records).encode()),
            "exclusions": sorted(exclusions), "metadata_scope": METADATA_SCOPE}


def validate_identity(value, exclusions=()):
    records = value["records"]
    require(isinstance(records, list) and records, "empty source inventory")
    require(len({r["path"] for r in records}) == len(records), "duplicate inventory path")
    for row in records:
        path_parts(row["path"])
        require(row["path"].split("/")[0] not in exclusions, "excluded state included in inventory")
        if row["kind"] == "file":
            require(set(row) == {"path", "kind", "bytes", "sha256"}, "malformed file inventory")
            require(type(row["bytes"]) is int and row["bytes"] >= 0, "invalid inventory byte count")
            hash_value(row["sha256"], "invalid file digest")
        else:
            same(row["kind"], "directory", "unexpected inventory kind")
            require(set(row) == {"path", "kind"}, "malformed directory inventory")
    same(value, identity(records, exclusions), "source inventory aggregate or scope mismatch")


def memory_identity(files):
    rows = [{"path": name, "kind": "directory"} if value is None else
            {"path": name, "kind": "file", "bytes": len(value), "sha256": sha(value)}
            for name, value in files.items()]
    return identity(rows)


def edit_memory(files, call):
    args = call["arguments"]
    require(call["tool"] == "str_replace_editor" and args["path"].startswith(PREFIX), "unreviewed edit")
    name = args["path"][len(PREFIX):]
    require(name in {"test_reproduce_issue.py", "environ/environ.py", "tests/test_search.py"}, "unreviewed edit path")
    original = files.get(name)
    if args["command"] == "create":
        require(name not in files, "create overwrites source")
        files[name] = args["file_text"].encode()
    else:
        require(args["command"] == "str_replace" and isinstance(original, bytes), "invalid replacement")
        old = args["old_str"].encode()
        require(old and original.count(old) == 1, "nonunique recorded edit")
        files[name] = original.replace(old, args["new_str"].encode(), 1)
    return {"call_index": call["index"], "call_id": call["call_id"], "path": name,
            "before_sha256": sha(original) if original is not None else None, "after_sha256": sha(files[name])}


def frozen_material(evidence=HERE / "evidence"):
    for name, expected in BINDINGS.items():
        path = evidence / name
        require(path.is_file() and not path.is_symlink(), "missing/link-like frozen evidence")
        same(digest(path), expected, "frozen evidence bytes differ: " + name)
    plan = read_json(evidence / "django-environ-state-rejoin-candidate-v1.json")
    metadata = read_json(evidence / "django-environ-metadata-response-v1.json")
    require(metadata["partial"] is False and len(metadata["rows"]) == 1 and not metadata["rows"][0]["truncated_cells"], "incomplete task metadata")
    task = metadata["rows"][0]["row"]
    same(task["base_commit"], BASE, "base commit differs")
    same(task["instance_id"], "joke2k__django-environ-322", "task identity differs")
    raw = read_json(evidence / "trace-cohort-2/rows/row-51126.json")
    require(raw["partial"] is False and len(raw["rows"]) == 1 and not raw["rows"][0]["truncated_cells"], "incomplete raw trace")
    trace = raw["rows"][0]["row"]
    same(trace["instance_id"], task["instance_id"], "trace/metadata task mismatch")
    same(trace["trajectory_id"], plan["source_evidence"]["trajectory_id"], "trace identity mismatch")
    calls = [c for m in trace["trajectory"] if m["role"] == "assistant" for c in (m.get("tool_calls") or [])]
    observations = {m["tool_call_id"]: m["content"] for m in trace["trajectory"] if m["role"] == "tool"}
    selected = plan["prior_non_mutation_tool_calls"] + plan["prior_recorded_source_mutations"] + plan["segment"]
    same(sorted(c["index"] for c in selected), [i for i in range(31) if i != 15], "recorded case silently omits an operation")
    for entry in selected:
        original = calls[entry["index"]]
        same(original["id"], entry["call_id"], "recorded call id mismatch")
        same(original["function"]["name"], entry["tool"], "recorded tool mismatch")
        same(json.loads(original["function"]["arguments"]), entry["arguments"], "recorded arguments mismatch")
        same(sha(observations[original["id"]].encode()), entry["observation_sha256"], "recorded observation hash mismatch")
    files = {}
    with tarfile.open(fileobj=io.BytesIO((evidence / "django-environ-31c8f983-source.tar.gz").read_bytes()), mode="r:gz") as archive:
        for member in archive.getmembers():
            parts = path_parts(member.name.rstrip("/"))
            require(parts[0] == "django-environ-" + BASE and (member.isdir() or member.isfile()), "unexpected archive material")
            if len(parts) == 1:
                continue
            name = "/".join(parts[1:])
            require(name not in files, "duplicate source archive entry")
            files[name] = archive.extractfile(member).read() if member.isfile() else None
            for i in range(1, len(parts) - 1):
                files.setdefault("/".join(parts[1:i + 1]), None)
    receipts = [edit_memory(files, c) for c in plan["prior_recorded_source_mutations"]]
    seed = memory_identity(files)
    changed_edit = edit_memory(files, plan["segment"][1])
    changed = memory_identity(files)
    restored_edit = edit_memory(files, plan["segment"][2])
    same(memory_identity(files), seed, "in-memory source restoration failed")
    require(changed != seed, "edit does not change source")
    return plan, {"base_commit": BASE, "metadata_sha256": BINDINGS["django-environ-metadata-response-v1.json"],
        "archive_sha256": BINDINGS["django-environ-31c8f983-source.tar.gz"],
        "trace_sha256": BINDINGS["trace-cohort-2/rows/row-51126.json"],
        "plan_sha256": BINDINGS["django-environ-state-rejoin-candidate-v1.json"],
        "prior_editor_receipts": receipts, "seed_source": seed, "changed_editor": changed_edit,
        "changed_source": changed, "restored_editor": restored_edit, "restored_source": seed,
        "full_reconstructed_source_rejoins": True, "prior_shell_commands_executed": False,
        "scope": "source reconstruction only; original runtime state not reconstructed"}


def capture_verdict(raw, expected_count, code):
    require(set(raw) == {"schema", "exit_code", "nodeids", "nodeid_sha256", "outcomes"}, "raw capture fields changed")
    same(raw["schema"], "zerorun.benchmark-independent-pytest-shadow.v1", "wrong fresh capture schema")
    same(raw["exit_code"], code, "wrong fresh capture exit")
    nodes, outcomes = raw["nodeids"], raw["outcomes"]
    require(isinstance(nodes, list) and len(nodes) == expected_count == len(outcomes) == len(set(nodes)), "fresh node denominator mismatch")
    require(all(isinstance(n, str) and n.startswith("tests/test_search.py::") and "\x00" not in n for n in nodes), "unexpected fresh node target")
    same(raw["nodeid_sha256"], sha(canonical({"nodeids": nodes}).encode()), "fresh node digest mismatch")
    for outcome in outcomes:
        same(outcome, PASS, "fresh node did not pass completely")
    return {"exit_code": code, "node_outcomes": dict(zip(nodes, outcomes, strict=True))}


def validate_case(case, evidence=HERE / "evidence"):
    plan, expected_reconstruction = frozen_material(evidence)
    require(not (case / "failure.json").exists(), "runner failure receipt retained; case cannot pass")
    same(digest(HERE / "state_rejoin.py"), RUNNER_SHA, "frozen case producer changed")
    invocation = read_json(case / "invocation.json")
    for key, expected in {"producer_sha256": RUNNER_SHA, "execute": True, "purposive_case": True, "full_agent_replay": False}.items():
        same(invocation[key], expected, "invocation policy differs: " + key)
    same(read_json(case / "reconstruction.json"), expected_reconstruction, "reconstruction differs from independently applied recorded source edits")
    summary = read_json(case / "summary.json")
    same(summary["schema"], "zerorun.controlled-agent-source-rejoin.v1", "wrong summary schema")
    same(summary["runtime_image"], IMAGE, "runtime image differs")
    same(summary["operator_authority_created"], False, "operator authority claimed")
    same(summary["original_environment_reproduced"], False, "original environment equivalence claimed")
    require("counterfactual" in LABELS[1] and "extra instrumentation" in summary["scope"] and "no autonomous agent" in summary["scope"], "case scope missing")
    runtime = summary["runtime_identity"]
    validate_identity(runtime)
    runtime_rows = [{**r, "path": ".zerorun-env/" + r["path"]} for r in runtime["records"]]
    runtime_rows.insert(0, {"path": ".zerorun-env", "kind": "directory"})
    require(any(r["path"] == "bin/python" and r["kind"] == "file" for r in runtime["records"]), "materialized runtime launcher missing")
    setup = read_json(case / "dependency-setup.json")
    for key, expected in {"isolated_result_cache": True, "shared_hardlinks": False, "private_byte_copy": True,
                          "read_only_mode_and_content_verified": True}.items():
        same(setup[key], expected, "dependency materialization differs: " + key)
    numeric(setup["environment_bootstrap_ms"])
    manifest = read_json(case / "manifest.json")
    same(manifest["version"], 2, "wrong manifest version")
    require(set(manifest["tasks"]) == {"rejoin-target", "rejoin-collection"}, "changed task set")
    seed_records = expected_reconstruction["seed_source"]["records"]
    expected_inputs = sorted({r["path"].split("/")[0] for r in seed_records} | {".zerorun-env"})
    for task_name, targets in (("rejoin-target", TARGET), ("rejoin-collection", COLLECTION)):
        task = manifest["tasks"][task_name]
        for key, expected in {"inputs": expected_inputs, "command": ["sh", ".zerorun-env/bin/python", "-m", "pytest", "-p", "no:cacheprovider", *targets],
            "image": IMAGE, "platform": "linux/amd64", "cacheable": True, "result_only": True, "closure_reviewed": True,
            "outputs": [], "cache_streams": False, "env": [], "unsafe_effects": []}.items():
            same(task[key], expected, "manifest contract differs: " + key)
    same([row["label"] for row in summary["requests"]], LABELS, "missing/reordered case request")
    history = [c["call_id"] for c in sorted(plan["prior_non_mutation_tool_calls"] + plan["prior_recorded_source_mutations"], key=lambda c: c["index"])]
    rows, verdicts = [], []
    for i, label in enumerate(LABELS):
        path = case / label
        row = read_json(path / "observation.json")
        same(row, summary["requests"][i], "summary and request observation differ")
        product, fresh = read_json(path / "product.json"), read_json(path / "fresh.json")
        same(row["product"], product, "product raw/observation mismatch")
        if i == 1:
            history.append(plan["segment"][1]["call_id"])
        elif i == 2:
            history.extend([plan["segment"][2]["call_id"], plan["segment"][3]["call_id"]])
        elif i == 3:
            history.append(plan["segment"][4]["call_id"])
        targets = COLLECTION if i == 2 else TARGET
        same(row["history_reference_key"], sha(canonical({"prior_event_ids": history, "command": targets}).encode()), "history reference does not match recorded event IDs")
        same(row["history_reference_is_not_tvcache"], True, "history reference mislabeled")
        source = expected_reconstruction["changed_source" if i == 1 else "seed_source"]
        expected_source = identity(source["records"] + runtime_rows, {".zerorun", ".zerorun.json"})
        for key in ("source_before", "source_after"):
            validate_identity(row[key], {".zerorun", ".zerorun.json"})
            same(row[key], expected_source, "materialized source/runtime changed or omitted")
        for key, value in {"source_unchanged_by_execution": True, "expected_verdict_observed": True,
                           "expected_exit_code": CODES[i], "expected_nodes": COUNTS[i]}.items():
            same(row[key], value, "request claim mismatch: " + key)
        same(product["status"], STATUSES[i], "unexpected actual cache status")
        same(product["exit_code"], CODES[i], "unexpected product exit")
        hash_value(product["cache_key"], "invalid actual action key")
        require(numeric(product["request_wall_ms"]) > 0, "nonpositive product duration")
        require(isinstance(product["stdout"], str) and isinstance(product["stderr"], str), "missing product stream receipt")
        if i == 3:
            same(product["stdout"], "", "hit unexpectedly replays output")
            same(product["stderr"], "", "hit unexpectedly replays error output")
        raw_capture = read_json(path / "fresh-outcomes.json")
        verdict = capture_verdict(raw_capture, COUNTS[i], CODES[i])
        same(fresh["capture"], {**raw_capture, "complete_per_node_outcomes": True, "failing_nodeids": [], "all_nodes_non_failing": True}, "fresh raw/validated capture mismatch")
        same(fresh["exit_code"], CODES[i], "fresh subprocess exit mismatch")
        same(fresh["runner"], "independent-docker-plain-pytest", "fresh runner changed")
        same(fresh["instrumented"], True, "fresh run is not instrumented")
        require(isinstance(fresh["stdout_tail"], str) and isinstance(fresh["stderr_tail"], str), "missing fresh output receipts")
        same(row["oracle_wall_ms"], numeric(fresh["wall_ms"]), "fresh wall duration mismatch")
        same(row["oracle_verdict"], verdict, "fresh verdict mismatch")
        verdicts.append(verdict)
        rows.append({"request": label, "actual_status": product["status"], "exit_code": CODES[i], "fresh_nodes": COUNTS[i],
            "counterfactual_instrumentation": i == 1, "product_wall_ms": product["request_wall_ms"], "fresh_oracle_wall_ms": fresh["wall_ms"],
            "source_sha256": row["source_before"]["sha256"], "action_key": product["cache_key"],
            "raw_output_equivalence_claimed": False})
        if i == 0:
            history.append(plan["segment"][0]["call_id"])
    same(verdicts[0], verdicts[3], "seed and restored per-node outcomes differ")
    changed_nodes = set(verdicts[1]["node_outcomes"]) - set(verdicts[0]["node_outcomes"])
    require(len(changed_nodes) == 1 and "elasticsearch7" in next(iter(changed_nodes)) and set(verdicts[0]["node_outcomes"]).issubset(verdicts[1]["node_outcomes"]), "counterfactual node is not the one recorded edit")
    same(rows[0]["action_key"], rows[3]["action_key"], "restored action key differs")
    require(rows[0]["action_key"] != rows[1]["action_key"] and rows[2]["action_key"] not in {rows[0]["action_key"], rows[1]["action_key"]}, "changed content/command did not invalidate key")
    same(summary["actual_product_statuses"], STATUSES, "summary status vector differs")
    for key in ("completed", "cache_behavior_expected", "seed_repeat_source_equal", "seed_changed_source_different", "seed_repeat_key_equal",
                "seed_changed_key_different", "seed_repeat_fresh_verdict_equal", "history_reference_key_diverged"):
        same(summary[key], True, "completed summary check missing: " + key)
    require(not list(case.glob("private-cache-authentication-NOT-FOR-PUBLICATION/repositories/*/authorities/*.json")), "operator authority artifact exists")
    return {"rows": rows, "requests": 4, "fresh_outcomes": COUNTS, "optimized_hits": 1,
            "source_rejoin_verified": True, "materialized_runtime_sha256": runtime["sha256"],
            "summary_sha256": digest(case / "summary.json"), "original_environment_reproduced": False,
            "autonomous_agent_evaluated": False, "tvcache_compared": False, "population_speedup_claimed": False,
            "counterfactual_request_retained": True}


def compact_table(rows):
    lines = ["| Request | Observed status | Exit | Fresh test nodes |", "|---|---|---:|---:|"]
    lines += [f"| {r['request']} | {r['actual_status']} | {r['exit_code']} | {r['fresh_nodes']} |" for r in rows]
    return "\n".join(lines)


def file_record(path, label):
    require(path.is_file() and not path.is_symlink(), "missing/link-like bound source: " + label)
    return {"path": label, "sha256": digest(path), "bytes": path.stat().st_size}


def validate_wrapper(directory, replication_directory, engine_archive=HERE.parent / "source-final", evidence=HERE / "evidence"):
    require(replication_directory is not None, "completed replication directory required")
    protocol, completion = read_json(directory / "protocol.json"), read_json(directory / "completion.json")
    same(protocol["schema"], "zerorun.bound-state-rejoin.protocol.v1", "wrong wrapper protocol")
    same(completion["schema"], "zerorun.bound-state-rejoin.completion.v1", "wrong wrapper completion")
    same(completion["protocol_sha256"], digest(directory / "protocol.json"), "wrapper protocol hash differs")
    same(protocol["preflight_errors"], [], "wrapper preflight failed")
    same(completion["postflight_errors"], [], "wrapper postflight failed")
    same(protocol["operator_authorization_permitted"], False, "wrapper permits operator authority")
    same(completion["outcome"], {"return_code": 0, "error_type": None, "error": None, "runner_invoked": True}, "wrapper runner failed/not invoked")
    same(completion["completed"], True, "wrapper incomplete")
    check_keys = {"runner_hash_expected", "replication_helper_hash_expected", "recovery_helper_hash_expected", "replication_completed",
                  "protected_matches_replication", "core_matches_replication", "engine_matches_replication"}
    same(protocol["checks"], {k: True for k in check_keys}, "wrapper preflight checks changed")
    after_keys = {"protected_source_unchanged", "core_source_unchanged", "engine_identity_unchanged", "helper_files_unchanged",
                  "wrapper_unchanged", "runner_unchanged", "evidence_files_unchanged", "case_completed", "no_operator_authority"}
    same(completion["checks"], {k: True for k in after_keys}, "wrapper completion checks failed")
    for name, filename in (("runner", "state_rejoin.py"), ("wrapper", "run_bound_state_rejoin.py")):
        record = file_record(HERE / filename, "strengthening/" + filename)
        same(protocol[name], record, "bound producer bytes differ")
        same(completion[name + "_after"], record, "producer changed during case")
    same(protocol["runner"]["sha256"], RUNNER_SHA, "unrecognized frozen runner")
    same(protocol["wrapper"]["sha256"], WRAPPER_SHA, "unrecognized frozen wrapper")
    helpers = [(HERE / "randomized_replication.py", "strengthening/randomized_replication.py"),
               (HERE.parent / "producers/controlled_comparison-recovery.py", "sqj/producers/controlled_comparison-recovery.py"),
               (engine_archive / "tools/product_generalization_benchmark.py", "engine/tools/product_generalization_benchmark.py"),
               (engine_archive / "ci/generalization-runtime-requirements.txt", "engine/ci/generalization-runtime-requirements.txt")]
    records = [file_record(path, label) for path, label in helpers]
    same(protocol["helper_files"], records, "helper bytes differ")
    same(completion["helper_files_after"], records, "helper bytes changed")
    recorded_evidence = protocol["evidence_files"]
    require(len(recorded_evidence) == len(BINDINGS) and {r["path"] for r in recorded_evidence} == set(BINDINGS), "missing/duplicate evidence binding")
    for row in recorded_evidence:
        same(row, file_record(evidence / row["path"], row["path"]), "evidence binding differs")
        same(row["sha256"], BINDINGS[row["path"]], "evidence not frozen candidate")
    same(completion["evidence_files_after"], recorded_evidence, "evidence changed during case")
    previous = read_json(replication_directory / "protocol.json")
    previous_completion = read_json(replication_directory / "campaign-summary.json")
    analyze_replication.validate_protocol(previous, engine_archive)
    for field, filename in (("protocol_file", "protocol.json"), ("completion_file", "campaign-summary.json")):
        row = protocol["replication"][field]
        same(row["sha256"], digest(replication_directory / filename), "prior replication receipt bytes differ")
        same(row["bytes"], (replication_directory / filename).stat().st_size, "prior replication receipt size differs")
    same(previous_completion["schema"], "zerorun.randomized-short-replication-summary.v1", "wrong prior completion schema")
    for field, value in {"completed": True, "protected_source_unchanged": True, "operator_authority_receipts_created": False}.items():
        same(previous_completion[field], value, "prior replication incomplete or source changed")
    protected = previous["protected_source_identity"]
    core_files = [r for r in protected["files"] if r["path"].startswith("engine/")]
    core = {"files": core_files, "sha256": sha(canonical(core_files).encode())}
    for key, expected in (("protected_source", protected), ("core_protected", core), ("engine_identity", previous["engine_identity"])):
        same(protocol[key + "_before"], expected, "state case uses different replication source")
        same(completion[key + "_after"], expected, "state case source changed")
    same(previous_completion["protected_source_after"], protected, "prior source changed")
    for key, expected in (("protected_source_identity", protected), ("core_protected_identity", core), ("engine_identity", previous["engine_identity"]), ("completed", True)):
        same(protocol["replication"][key], expected, "embedded prior replication differs")
    invocation = protocol["invocation"]
    same(invocation["child_directory"], "case", "unexpected child directory")
    args = invocation["runner_args"]
    require(len(args) == 7 and args[::2][:3] == ["--engine", "--evidence", "--output"] and args[-1] == "--execute-reviewed-lab", "unexpected runner invocation")
    same(args[1], invocation["engine"], "invoked engine differs")
    same(args[3], invocation["evidence"], "invoked evidence differs")
    require(PurePosixPath(args[5]).name == "case", "unexpected child output name")
    started = timestamp(protocol["created_utc"])
    child_started = timestamp(read_json(directory / "case/invocation.json")["utc"])
    ended = timestamp(completion["completed_utc"])
    require(timestamp(previous_completion["completed_utc"]) <= started <= child_started <= ended, "protocol/campaign/runner chronology differs")
    same(completion["case_summary_file"], file_record(directory / "case/summary.json", "case/summary.json"), "case summary hash differs")
    return {"protocol_sha256": digest(directory / "protocol.json"), "completion_sha256": digest(directory / "completion.json"),
            "replication_protocol_sha256": digest(replication_directory / "protocol.json"), "protected_source_verified": True,
            "runner_sha256": RUNNER_SHA, "wrapper_sha256": protocol["wrapper"]["sha256"]}


def analyze(directory, *, evidence=HERE / "evidence", replication_directory=None,
            engine_archive=HERE.parent / "source-final", wrapper_validator=None):
    errors, detail = [], None
    for filename in ("failure.json", "case/failure.json"):
        if (directory / filename).exists():
            errors.append({"path": filename, "receipt": read_json(directory / filename)})
    try:
        provenance = wrapper_validator(directory) if wrapper_validator else validate_wrapper(directory, replication_directory, engine_archive, evidence)
        detail = validate_case(directory / "case", evidence)
    except (ValueError, OSError, KeyError, TypeError) as error:
        errors.append({"error_type": type(error).__name__, "error": str(error)})
    result = {"schema": "zerorun.independent-state-rejoin-analysis.v1", "completed": not errors and detail is not None,
              "errors": errors, "expected_requests": 4, "selection": "one purposive real-agent-derived controlled case",
              "autonomous_agent_evaluated": False, "tvcache_compared": False, "acceptance_probability_estimated": False}
    if result["completed"]:
        result.update(detail, provenance=provenance, compact_table_markdown=compact_table(detail["rows"]))
    else:
        result["rows"] = []
        result["retained_request_directories"] = [name for name in LABELS if (directory / "case" / name).is_dir()]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replication-directory", type=Path, required=True)
    parser.add_argument("--engine-archive", type=Path, default=HERE.parent / "source-final")
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    result = analyze(options.directory, replication_directory=options.replication_directory, engine_archive=options.engine_archive)
    encoded = json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if options.check:
        same(options.output.read_text(encoding="utf-8"), encoded, "saved state analysis differs")
    else:
        with options.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
    print(json.dumps({"completed": result["completed"], "errors": result["errors"]}))
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
