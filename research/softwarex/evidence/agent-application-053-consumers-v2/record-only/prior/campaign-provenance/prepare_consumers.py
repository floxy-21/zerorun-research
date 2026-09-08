"""Bind inspected actual agent final states and exact baseline restorations."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import os
import subprocess
import tarfile

from . import validation as v

IMAGE = "127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465"
PREFIX = "/home/floxy/zerorun-agent-application-053-consumers-20260908-v1"
ORACLE_COMPLETION = "9927adcf3cc3edac593568df644abfddb8d4c65ebdadb938fee9e80f8168e445"
ORACLE_MANIFEST = "1f9247c62799c6e659435709957bd5bea6a560903fe1dc369a8327cc727f40ba"
FIXED_PATHS = {0: "pycparser/c_parser.py", 1: "environ/environ.py", 3: "lizard_languages/swift.py",
               4: "frontmatter/default_handlers.py", 5: "lkml/parser.py"}
BASIS = {
    0: "Parser changes handle pragma lists and control-flow statements. The selected unittest target reads bound tests/c_files fixtures (including legacy rU reads); all parser modules, generated tables, tests and configuration are inputs. No selected-test external communication was found.",
    1: "The production change preserves unrecognized database URL schemes. The tests save/replace/restore os.environ with a fixed fixture dictionary and read the bound environ/test_env.txt. Database and Redis URL strings are parser data; no selected-test network connection was found.",
    3: "The Swift tokenizer/parser change handles backticks, subscripts and accessor keywords. The selected tests analyze embedded Swift strings through bound lizard modules and code readers. No selected-test external communication was found.",
    4: "The JSON handler change adjusts extracted JSON metadata. The tests read bound frontmatter fixtures and create disposable temporary files inside the container; these are not declared outputs or reusable artifacts. PyYAML/toml/six dependencies are image-bound. No selected-test external communication was found.",
    5: "The parser change handles scoped spaced-hyphen values. The selected pytest target exercises fixed token/parser/tree examples through bound lkml modules and configuration. No selected-test external communication was found.",
}


def utc():
    return datetime.now(timezone.utc).isoformat()


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)


def save(path, value):
    write(path, v.canonical(value) + b"\n")


def row(path, root=None):
    raw = v.real(path).read_bytes()
    return {"path": path.relative_to(root).as_posix() if root else str(path), "bytes": len(raw), "sha256": v.sha(raw)}


def files(rows):
    result = sorted(({k: item[k] for k in ("path", "bytes", "sha256")} for item in rows if item.get("kind") == "file"), key=lambda item: item["path"])
    v.checked_rows(result)
    return result


def archive_files(path):
    """Read bounded ordinary archive files, stripping exactly one common prefix."""
    result, prefix, total = {}, None, 0
    with tarfile.open(v.real(path), "r:gz") as archive:
        members = archive.getmembers()
        v.require(len(members) <= 25000, "archive member bound exceeded")
        for member in members:
            parts = PurePosixPath(member.name).parts
            v.require(parts and not member.name.startswith("/") and all(p not in {"", ".", ".."} for p in parts)
                      and "\\" not in member.name and not member.issym() and not member.islnk(), "unsafe source archive member")
            if prefix is None:
                prefix = parts[0]
            v.require(parts[0] == prefix and (member.isfile() or member.isdir()), "archive prefix/type differs")
            if member.isdir():
                continue
            v.require(len(parts) > 1, "unprefixed archive file")
            name = "/".join(parts[1:]); v.safe_relative(name)
            v.require(name.split("/")[0] not in {".git", ".zerorun"} and name not in result, "archive control/duplicate path")
            total += member.size
            v.require(member.size <= 32 * 1024 * 1024 and total <= 256 * 1024 * 1024, "archive file/total bound exceeded")
            raw = archive.extractfile(member).read()
            v.require(len(raw) == member.size, "archive member truncated")
            result[name] = raw
    v.require(result, "empty source archive")
    return result


def raw_rows(values):
    return [{"path": name, "bytes": len(values[name]), "sha256": v.sha(values[name])} for name in sorted(values)]


def build_bundle(oracle, output):
    oracle = v.real(oracle, directory=True)
    v.require(v.sha((oracle / "completion.json").read_bytes()) == ORACLE_COMPLETION
              and v.sha((oracle / "RECORD_MANIFEST.json").read_bytes()) == ORACLE_MANIFEST, "reviewed oracle export differs")
    completion = v.strict((oracle / "completion.json").read_bytes())
    frozen = v.strict((oracle / "prepared/freeze.json").read_bytes())
    v.require([case["case_id"] for case in frozen["cases"]] == list(v.CASES), "original six-case order differs")
    bundle = {"schema": "zerorun.real-consumer-inspection-bundle.0.5.3.v1", "created_utc": utc(),
              "prefix": PREFIX, "oracle_completion_sha256": ORACLE_COMPLETION, "oracle_manifest_sha256": ORACLE_MANIFEST,
              "selected_cases": list(v.CASES), "eligible_cases": [], "cases": [],
              "ineligible_cases": [{"case_id": v.CASES[2], "reason": "Original actual final patch failed two independently executed supplied tests; NOT_VERIFIED_FIXED. No consumer authority or seed is requested for this primary outcome."}],
              "arbitrary_future_edits_qualified": False, "human_review_seconds": None,
              "automatic_retries": 0, "new_reference_source_patch_applied": False}
    for index, changed in FIXED_PATHS.items():
        case = frozen["cases"][index]; case_id = case["case_id"]
        observed = completion["cases"][index]
        v.require(observed["case_id"] == case_id and observed["error"] is None
                  and observed["classification"]["completed_verified_fix"] is True, "case lacks verified actual final fix")
        directory = oracle / "prepared" / f"case-{index:02d}"
        preparation = v.strict((directory / "preparation.json").read_bytes())
        session = v.strict((directory / "session.json").read_bytes())
        final = files(session["after"]); baseline = files(preparation["before"])
        final_index, base_index = v.checked_rows(final), v.checked_rows(baseline)
        actual_changes = sorted(name for name in final_index.keys() | base_index.keys() if final_index.get(name) != base_index.get(name))
        v.require(actual_changes == [changed], "actual production patch scope differs from inspection")
        final_path = directory / "final-source.tar.gz"
        v.require(row(final_path, directory) == session["final_source"], "actual model final archive binding differs")
        final_raw = archive_files(final_path)
        v.require(raw_rows(final_raw) == final, "actual model archive inventory differs")
        base_raw = archive_files(directory / "base-source.tar.gz")
        v.require(changed in base_raw and {"path": changed, "bytes": len(base_raw[changed]), "sha256": v.sha(base_raw[changed])} == base_index[changed], "baseline production restoration differs")
        tests = sorted(preparation["test_paths"])
        v.require(all(final_index.get(name) == base_index.get(name) for name in tests), "protected tests differ")
        command = ["/usr/local/bin/python", "-m", "pytest", "-p", "no:cacheprovider", *case["targets"]]
        inputs = sorted({name.split("/")[0] for name in final_index.keys() | base_index.keys()})
        v.require(not ({".git", ".zerorun", ".zerorun.json"} & set(inputs)), "source has preexisting authority/control manifest")
        manifest = {"version": 2, "tasks": {"agent-tests": {"command": command, "inputs": inputs, "outputs": [], "env": [],
                    "cacheable": True, "unsafe_effects": [], "cache_streams": False, "result_only": True,
                    "closure_reviewed": True, "image": IMAGE, "platform": "linux/amd64"}}}
        manifest_raw = v.canonical(manifest) + b"\n"
        manifest_row = {"path": ".zerorun.json", "bytes": len(manifest_raw), "sha256": v.sha(manifest_raw)}
        states = {}
        for name, inventory in (("final", final), ("restored", baseline)):
            inventory = sorted([*inventory, manifest_row], key=lambda item: item["path"])
            states[name] = {"files": inventory, "identity_sha256": v.identity(inventory), "eligible": True}
        qualification = {"schema": v.SCHEMA, "case_id": case_id, "root": f"{PREFIX}/workspaces/{case_id}", "task": "agent-tests",
            "manifest_sha256": v.sha(manifest_raw), "command": command, "runtime_image": IMAGE, "states": states,
            "protected_test_paths": tests, "restoration": {"changed_paths": [changed], "description": "Restore only the actual model-modified production file to its original base-commit bytes. The provided regression tests, fixtures, configuration and manifest remain identical."},
            "inspection": {"method": "AI-assisted inspection under user-authorized laboratory scope", "human_review_seconds": None,
                "basis": BASIS[index] + " Inspection covers these two exact recorded source states, the fixed target, all literal top-level inputs and the existing Python 3.10.21/pytest 8.4.2 digest image. Execution is containerized with network disabled, source inputs read-only, no forwarded host environment, no reusable output artifacts and result-only caching. This bounded assessment is not a proof of arbitrary future behavior.",
                "re_review_triggers": ["Any source, protected test, fixture or configuration byte changes", "Any command, declared input, image/dependency or installed runtime changes", "Any output, environment, effect or result-only contract changes", "New uncertainty about closure, nondeterminism or external effects"],
                "oracle_classification": observed["classification"]},
            "authorization": {"scope": "selected laboratory case and the two exact recorded states only",
                "user_authorization_reference": "2026-09-08 user-authorized real-agent application; the supervising task explicitly authorized exact external manifests after source/test-state inspection. This envelope records scope and does not create authority."}}
        v.validate_qualification(qualification)
        bundle["eligible_cases"].append(case_id)
        bundle["cases"].append({"case_index": index, "case_id": case_id, "qualification": qualification,
            "manifest_base64": base64.b64encode(manifest_raw).decode(), "restoration_base64": base64.b64encode(base_raw[changed]).decode(),
            "bindings": {name: row(directory / name, directory) for name in ("preparation.json", "session.json", "final-source.tar.gz", "base-source.tar.gz", "provided-tests.patch")}})
    save(Path(output), bundle)
    return {"selected": 6, "eligible": 5, "bundle": row(Path(output))}


def prepare(bundle_path, oracle, output):
    v.require(os.name == "posix" and os.getuid() != 0, "non-root Linux lab required")
    raw = v.real(bundle_path).read_bytes(); bundle = v.strict(raw)
    output = Path(output)
    v.require(str(output) == bundle["prefix"] == PREFIX and not output.exists(), "new exact external output required")
    oracle = v.real(oracle, directory=True)
    v.require(v.sha((oracle / "completion.json").read_bytes()) == bundle["oracle_completion_sha256"] == ORACLE_COMPLETION
              and v.sha((oracle / "RECORD_MANIFEST.json").read_bytes()) == bundle["oracle_manifest_sha256"] == ORACLE_MANIFEST, "oracle binding differs")
    output.mkdir(); records = output / "record-only"; records.mkdir()
    (output / "workspaces").mkdir(); (output / "restoration-private-source").mkdir(); (output / "trust").mkdir()
    save(records / "inspection-bundle.json", bundle)
    write(records / "prepare_consumers.py", Path(__file__).read_bytes())
    results = []
    for item in bundle["cases"]:
        q = item["qualification"]; v.validate_qualification(q)
        source = oracle / "prepared" / f"case-{item['case_index']:02d}"
        for name, binding in item["bindings"].items():
            v.require(row(source / name, source) == binding, "original producer input differs")
        directory = records / "cases" / item["case_id"]; directory.mkdir(parents=True)
        root = Path(q["root"]); v.require(root.parent == output / "workspaces", "workspace root escaped")
        root.mkdir()
        final = archive_files(source / "final-source.tar.gz")
        manifest = base64.b64decode(item["manifest_base64"], validate=True)
        v.require(v.sha(manifest) == q["manifest_sha256"], "manifest encoding differs")
        final[".zerorun.json"] = manifest
        v.require(raw_rows(final) == q["states"]["final"]["files"], "qualified final bytes differ")
        for name, data in final.items():
            write(root / name, data)
        command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", "init", "--template=", "--initial-branch=main", str(root)]
        result = subprocess.run(command, capture_output=True, timeout=30, env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"})
        write(directory / "git-init.stdout.log", result.stdout); write(directory / "git-init.stderr.log", result.stderr)
        save(directory / "git-init.json", {"argv": command, "returncode": result.returncode})
        v.require(result.returncode == 0 and v.inventory(root) == q["states"]["final"]["files"], "reconstruction changed qualified source")
        restored = base64.b64decode(item["restoration_base64"], validate=True); changed = q["restoration"]["changed_paths"][0]
        expected = v.checked_rows(q["states"]["restored"]["files"])[changed]
        v.require({"path": changed, "bytes": len(restored), "sha256": v.sha(restored)} == expected, "restoration encoding differs")
        write(output / "restoration-private-source" / item["case_id"], restored)
        save(directory / "qualification.json", q); write(directory / "manifest.json", manifest)
        save(directory / "preparation.json", {"case_id": item["case_id"], "root": str(root), "actual_model_final_archive": item["bindings"]["final-source.tar.gz"],
            "source": v.inventory(root), "restoration_file": expected, "protected_tests_unchanged": True,
            "authority_created": False, "cache_seed_created": False, "reference_patch_applied": False})
        results.append({"case_id": item["case_id"], "prepared": True})
    save(records / "preparation-completion.json", {"schema": "zerorun.real-consumer-preparation.0.5.3.v1", "completed_utc": utc(), "cases": results,
        "selected": 6, "eligible": 5, "authority_created": False, "model_calls": 0, "source": row(Path(__file__))})
    return {"prepared": len(results), "output": str(output)}


def restore(output, case_id):
    output = Path(output); directory = output / "record-only/cases" / case_id
    q = v.strict((directory / "qualification.json").read_bytes()); v.validate_qualification(q)
    root = v.real(q["root"], directory=True)
    v.require(v.inventory(root) == q["states"]["final"]["files"], "restoration does not begin in qualified final state")
    name, = q["restoration"]["changed_paths"]
    data = v.real(output / "restoration-private-source" / case_id).read_bytes()
    expected = v.checked_rows(q["states"]["restored"]["files"])[name]
    v.require({"path": name, "bytes": len(data), "sha256": v.sha(data)} == expected, "restoration source differs")
    with v.real(root / name).open("wb") as stream:
        stream.write(data)
    after = v.inventory(root)
    v.require(after == q["states"]["restored"]["files"], "restoration exceeds qualified state")
    save(directory / "restoration.json", {"completed_utc": utc(), "changed_paths": [name], "source_after": after,
        "protected_tests_unchanged": True, "reference_source_patch_applied": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="operation", required=True)
    p = sub.add_parser("bundle"); p.add_argument("--oracle", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("prepare"); p.add_argument("--bundle", type=Path, required=True); p.add_argument("--oracle", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_bundle(args.oracle, args.output) if args.operation == "bundle" else prepare(args.bundle, args.oracle, args.output)
    print(v.canonical(result).decode())


if __name__ == "__main__":
    main()
