"""Run one installed-0.5.3 three-stage consumer per eligible actual agent fix."""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import math
import shutil
import sys
import tarfile
import time
from types import SimpleNamespace

from . import consumer as c, validation as v, seed
from . import prepare_consumers as p

SCHEMA = "zerorun.real-consumer-campaign.0.5.3.v1"
INSPECTION_BUNDLE_SHA = "441e30ec181377e9bfb4c9d1294a66e8a786b1a6b5f6329d5aaba89a9cf87be8"
WHEEL_SHA = "dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9"
CODEX = "/home/floxy/zerorun-codex-01533-node22-20260906/lib/node_modules/@openai/codex/bin/codex.js"
NODE = "/home/floxy/zerorun-node-download-20260906/node-v22.14.0-linux-x64/bin/node"


def raw_command(directory, label, argv, cwd, env, timeout=180):
    result = c.invoke(list(map(str, argv)), cwd, env, directory, label, timeout=timeout)
    v.require(c.successful(result), "operator command failed: " + label)
    return result


def bindings():
    return {**seed.sources(), "prepare_consumers.py": Path(p.__file__), "consumer_campaign.py": Path(__file__)}


def seal(records):
    rows = [c.file_row(path, records) for path in sorted(records.rglob("*")) if path.is_file()]
    c.save(records / "RECORD_MANIFEST.json", {"schema": SCHEMA, "files": rows,
        "authentication_files_included": False, "authority_state_included": False, "live_workspaces_included": False})


def run(output, wheel, skill, authentication_file):
    v.require(os.name == "posix" and os.getuid() != 0, "non-root Linux laboratory required")
    output = v.real(output, directory=True); records = v.real(output / "record-only", directory=True)
    v.require(str(output) == p.PREFIX, "campaign prefix differs")
    bundle_raw = (records / "inspection-bundle.json").read_bytes()
    v.require(v.sha(bundle_raw) == INSPECTION_BUNDLE_SHA, "independently inspected exact source bundle differs")
    bundle = v.strict(bundle_raw)
    v.require(bundle["selected_cases"] == list(v.CASES) and bundle["eligible_cases"] == [v.CASES[i] for i in p.FIXED_PATHS], "selection differs")
    v.require(v.sha(v.real(wheel).read_bytes()) == WHEEL_SHA, "final packaged053 wheel differs")
    v.require(v.sha(v.real(skill).read_bytes()) == c.SKILL_SHA, "exact053 whole-task skill differs")
    # No authentication content is inspected, hashed, recorded or exported.
    authentication_file = v.real(authentication_file)
    v.require(not authentication_file.is_relative_to(output) and not output.is_relative_to(authentication_file), "existing external authentication source required")
    protocol = {"schema": SCHEMA, "started_utc": c.utc(), "selected_cases": list(v.CASES),
        "eligible_cases": bundle["eligible_cases"], "stages": list(v.STAGES), "case_execution_order": "original selection order, sequential",
        "seconds_per_stage": c.SECONDS, "automatic_retries": 0, "model": c.MODEL, "effort": c.EFFORT,
        "wheel_sha256": WHEEL_SHA, "wheel": c.file_row(wheel), "runtime_identity": c.CORE_IDENTITY, "core_commit": c.CORE,
        "inspection_bundle_sha256": v.sha((records / "inspection-bundle.json").read_bytes()),
        "oracle_completion_sha256": p.ORACLE_COMPLETION, "oracle_manifest_sha256": p.ORACLE_MANIFEST,
        "authority_scope": "operator creates exact inspected manifest authority under existing explicit user lab authorization; models cannot create or broaden it",
        "authentication_scope": "copy only the designated existing authentication file to each new external private client home; remove that fresh copy afterward; never inspect/hash/export its contents",
        "new_reference_source_patch_applied": False, "new_model_patch_retries": 0,
        "cost_scope": "observed sequential model consumer calls and operator setup; not an independent acceptance-rate or speedup estimate",
        "sources": []}
    provenance = records / "campaign-provenance"; provenance.mkdir()
    for name, path in sorted(bindings().items()):
        c.write(provenance / name, path.read_bytes())
        protocol["sources"].append(c.file_row(provenance / name, records))
    c.write(provenance / "skill.md", skill.read_bytes())
    protocol["sources"].append(c.file_row(provenance / "skill.md", records))
    c.save(records / "campaign-protocol.json", protocol)
    result = {"schema": SCHEMA, "started_utc": c.utc(), "protocol_sha256": v.sha((records / "campaign-protocol.json").read_bytes()),
              "cases": [], "error": None, "selected": 6, "eligible": 5, "successful_application_claimed": False}
    begin = time.monotonic()
    commands = records / "installation"; commands.mkdir()
    python = output / "installed053/bin/python"; zerorun = output / "installed053/bin/zerorun"
    environment = {name: os.environ[name] for name in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL") if name in os.environ}
    environment.update(PATH="/usr/local/bin:/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    try:
        raw_command(commands, "environment-create", [sys.executable, "-B", "-m", "venv", output / "installed053"], output, environment)
        raw_command(commands, "wheel-install", [python, "-I", "-B", "-m", "pip", "install", "--no-index", "--no-deps", "--disable-pip-version-check", wheel], output, environment)
        raw_command(commands, "runtime-inventory", [python, "-I", "-B", "-c", c.PROBE], output, environment, 20)
        installed = v.strict((commands / "runtime-inventory.stdout.log").read_bytes())
        v.require(installed["version"] == "0.5.3" and len(installed["files"]) == 36 and v.identity(installed["files"]) == c.CORE_IDENTITY, "installed exact053 differs")
        c.save(commands / "receipt.json", {"wheel_sha256": WHEEL_SHA, "installed": installed, "new_external_environment": True})
        private_base = output / "private-client-homes"; private_base.mkdir(mode=0o700)
        for case_id in bundle["eligible_cases"]:
            directory = records / "cases" / case_id
            q = v.strict((directory / "qualification.json").read_bytes()); root = Path(q["root"])
            v.require(q == next(item["qualification"] for item in bundle["cases"] if item["case_id"] == case_id), "case qualification differs from inspected source bundle")
            item = {"case_id": case_id, "seeded": False, "plan_ready": False, "stages": [], "error": None}
            try:
                v.require(v.inventory(root) == q["states"]["final"]["files"], "operator source check differs")
                c.manifest_check(root, q)
                trust = output / "trust" / case_id; trust.mkdir(mode=0o700)
                authority = directory / "operator-authority"; authority.mkdir()
                env = dict(environment, ZERORUN_TRUST_ROOT=str(trust))
                raw_command(authority, "authorize", [zerorun, "--manifest", root / ".zerorun.json", "--json", "authorize", "--manifest-sha256", q["manifest_sha256"]], root, env, 30)
                authorized = v.strict((authority / "authorize.stdout.log").read_bytes())
                v.require(authorized == {"status": "AUTHORIZED", "manifest_sha256": q["manifest_sha256"], "pytest_profile_sha256": None, "authority_location": "external-per-user"}, "exact authority result differs")
                seed_directory = directory / "seed"
                seed_args = SimpleNamespace(qualification=directory / "qualification.json", zerorun_command=zerorun,
                    zerorun_python_command=python, trust_root=trust, output=seed_directory)
                v.require(seed.run(seed_args) == 0, "fresh installed MCP seed failed")
                checked_seed = seed.validate(seed_directory); v.require(checked_seed["seeded"], "independent seed reconciliation failed")
                item["seeded"] = True
                plan = directory / "plan"
                freeze_args = SimpleNamespace(qualification=directory / "qualification.json", seed_receipt=seed_directory / "receipt.json",
                    skill_file=skill, trust_root=trust, output=plan, codex_command=Path(CODEX), node_command=Path(NODE),
                    zerorun_command=zerorun, python_command=python)
                v.require(c.freeze(freeze_args) == 0, "consumer plan freeze failed")
                c.load_plan(plan); item["plan_ready"] = True
                for stage in v.STAGES:
                    if stage == "restored":
                        p.restore(output, case_id)
                    private_home = private_base / (case_id + "--" + stage); private_home.mkdir(mode=0o700)
                    auth_copy = private_home / "auth.json"
                    v.require(not auth_copy.exists() and private_home.parent == private_base, "fresh private authentication destination differs")
                    stage_result = {"stage": stage, "error": None}
                    try:
                        shutil.copyfile(authentication_file, auth_copy); os.chmod(auth_copy, 0o600)
                        c.run(SimpleNamespace(plan=plan, stage=stage, client_home=private_home))
                        stage_result["validated"] = c.validate_attempt(plan, stage)
                    except Exception as error:
                        stage_result["error"] = {"type": type(error).__name__, "message": str(error)}
                    finally:
                        # Delete only the designated newly copied file in the exact new private home.
                        v.require(private_home.parent == private_base and auth_copy == private_home / "auth.json", "private cleanup target differs")
                        if auth_copy.exists():
                            v.require(not auth_copy.is_symlink(), "private authentication copy became a link")
                            auth_copy.unlink()
                    item["stages"].append(stage_result)
                    print(v.canonical({"case_id": case_id, "stage": stage, "completed_attempt": stage_result.get("validated", {}).get("model_invoked", False), "error": stage_result["error"]}).decode(), flush=True)
            except Exception as error:
                item["error"] = {"type": type(error).__name__, "message": str(error)}
            result["cases"].append(item)
            c.save(directory / "campaign-case.json", item)
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        result.update(completed_utc=c.utc(), elapsed_seconds=time.monotonic() - begin)
        c.save(records / "campaign-completion.json", result)
        seal(records)
        with tarfile.open(output / "record-only.tar.gz", "x:gz") as archive:
            for path in sorted(records.rglob("*")):
                if path.is_file(): archive.add(path, arcname="record-only/" + path.relative_to(records).as_posix(), recursive=False)
    return result


def verify_campaign(directory):
    """Reconcile raw seeds/model stages offline; never infer success from prompts."""
    directory = v.real(directory, directory=True)
    inventory = v.strict((directory / "RECORD_MANIFEST.json").read_bytes())
    rows = inventory["files"]
    v.require(all(c.file_row(directory / v.safe_relative(row["path"]), directory) == row for row in rows), "campaign raw bytes differ")
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    v.require(len(rows) == len({row["path"] for row in rows}) and actual == {row["path"] for row in rows} | {"RECORD_MANIFEST.json"}, "campaign inventory differs")
    protocol = v.strict((directory / "campaign-protocol.json").read_bytes())
    completion = v.strict((directory / "campaign-completion.json").read_bytes())
    bundle = v.strict((directory / "inspection-bundle.json").read_bytes())
    v.require(protocol["schema"] == completion["schema"] == SCHEMA
              and completion["protocol_sha256"] == v.sha((directory / "campaign-protocol.json").read_bytes())
              and protocol["inspection_bundle_sha256"] == v.sha((directory / "inspection-bundle.json").read_bytes()) == INSPECTION_BUNDLE_SHA
              and protocol["oracle_manifest_sha256"] == bundle["oracle_manifest_sha256"] == p.ORACLE_MANIFEST
              and protocol["oracle_completion_sha256"] == bundle["oracle_completion_sha256"] == p.ORACLE_COMPLETION
              and protocol["selected_cases"] == bundle["selected_cases"] == list(v.CASES)
              and protocol["eligible_cases"] == bundle["eligible_cases"] == [v.CASES[i] for i in p.FIXED_PATHS]
              and protocol["stages"] == list(v.STAGES) and protocol["seconds_per_stage"] == c.SECONDS
              and protocol["automatic_retries"] == 0 and protocol["wheel_sha256"] == WHEEL_SHA
              and protocol["runtime_identity"] == c.CORE_IDENTITY and protocol["core_commit"] == c.CORE,
              "campaign protocol differs")
    expected = [dict(c.file_row(path), path="campaign-provenance/" + name) for name, path in sorted(bindings().items())]
    skill_path = directory / "campaign-provenance/skill.md"
    v.require(v.sha(skill_path.read_bytes()) == c.SKILL_SHA, "campaign skill differs")
    expected.append(c.file_row(skill_path, directory))
    v.require(protocol["sources"] == expected, "campaign helper source drift")
    cases, stage_counts, costs = [], {stage: {"attempted": 0, "completed": 0, "reuse": 0, "fresh_success": 0, "fresh_failure": 0, "refused": 0, "material_interpretation_failure": 0, "boundary_failures": 0, "fresh_request_satisfied": 0} for stage in v.STAGES}, {"model_seconds": 0.0, "seed_seconds": 0.0}
    saved_case_ledger = []
    for case_id in bundle["eligible_cases"]:
        case_directory = directory / "cases" / case_id
        item = {"case_id": case_id, "seed": None, "stages": [], "disposition": "NOT_COMPLETED"}
        saved = v.strict((case_directory / "campaign-case.json").read_bytes()) if (case_directory / "campaign-case.json").is_file() else None
        v.require(v.strict((case_directory / "qualification.json").read_bytes()) == next(item["qualification"] for item in bundle["cases"] if item["case_id"] == case_id), "recorded case qualification differs from inspected source bundle")
        if saved:
            v.require(saved["case_id"] == case_id, "case ledger identity differs")
            saved_case_ledger.append(saved)
        if saved and saved["seeded"]:
            item["seed"] = seed.validate(case_directory / "seed")
            costs["seed_seconds"] += v.strict((case_directory / "seed/seed.json").read_bytes())["elapsed_seconds"]
        plan = case_directory / "plan"
        if saved and saved["plan_ready"]:
            _, frozen = c.load_plan(plan)
            authority = case_directory / "operator-authority"
            authority_command = [frozen["client"]["zerorun"]["invoked"], "--manifest", frozen["root"] + "/.zerorun.json",
                "--json", "authorize", "--manifest-sha256", frozen["qualification"]["manifest_sha256"]]
            authority_row = seed.validate_command(authority, "authorize", authority_command, frozen["root"], 30)
            v.require(v.strict(seed.bound(authority, authority_row["stdout"])) == {
                "status": "AUTHORIZED", "manifest_sha256": frozen["qualification"]["manifest_sha256"],
                "pytest_profile_sha256": None, "authority_location": "external-per-user"}, "raw exact-manifest authority differs")
            v.require(item["seed"] and v.sha((plan / "seed-receipt.json").read_bytes()) == item["seed"]["receipt_sha256"], "plan is not bound to verified actual seed")
            v.require(frozen["qualification"] == v.strict((case_directory / "qualification.json").read_bytes()), "campaign qualification differs")
            for stage in v.STAGES:
                attempt = plan / "attempts" / stage
                if not (attempt / "RECORD_MANIFEST.json").is_file():
                    item["stages"].append({"stage": stage, "disposition": "NOT_ATTEMPTED_OR_UNSEALED"}); continue
                checked = c.validate_attempt(plan, stage)
                row = v.strict((attempt / "completion.json").read_bytes())
                analysis = checked["analysis"] or {}; counts = stage_counts[stage]
                if row["model_invoked"]:
                    actual_process = v.strict((attempt / "model.json").read_bytes())
                    actual_start = v.strict((attempt / "model.started.json").read_bytes())
                    v.require(actual_process == row["process"] and all(actual_start[key] == actual_process[key]
                        for key in ("command", "cwd", "started_utc", "timeout_seconds")), "raw model process metadata differs")
                    elapsed = actual_process["elapsed_seconds"]
                    v.require(type(elapsed) in (int, float) and math.isfinite(elapsed) and 0 <= elapsed <= c.SECONDS + 20,
                        "consumer duration is invalid")
                counts["boundary_failures"] += int(bool(analysis) and not analysis.get("boundary_pass", False))
                counts["fresh_request_satisfied"] += int(analysis.get("fresh_request_satisfied", False))
                counts["attempted"] += int(checked["model_invoked"])
                counts["completed"] += int(checked["process_completed_successfully"] and analysis.get("model_turn_completed", False))
                counts["reuse"] += int(analysis.get("reuse_observed", False))
                for label, category in (("fresh_success", "FRESH_SUCCESS"), ("fresh_failure", "FRESH_FAILURE"), ("refused", "REFUSED")):
                    counts[label] += int(any(result["classification"] == category for result in analysis.get("completed_mcp_results", [])))
                counts["material_interpretation_failure"] += int(analysis.get("material_interpretation_failure", False))
                if row["process"]: costs["model_seconds"] += row["process"]["elapsed_seconds"]
                item["stages"].append(dict(checked, raw_path=attempt.relative_to(directory).as_posix()))
            if (case_directory / "restoration.json").is_file():
                restored = v.strict((case_directory / "restoration.json").read_bytes())
                v.require(restored["source_after"] == frozen["qualification"]["states"]["restored"]["files"]
                          and restored["changed_paths"] == frozen["qualification"]["restoration"]["changed_paths"], "baseline production restoration differs")
            item["disposition"] = "RECORDED_STAGE_OUTCOMES_RECONCILED"
        item["operator_failure"] = saved.get("error") if saved else "No completed case record"
        cases.append(item)
    v.require(completion["cases"] == saved_case_ledger, "completion and per-case ledgers differ")
    return {"schema": SCHEMA, "read_only": True, "records_reconciled": True, "selected": 6, "producer_verified_fixes": 5, "eligible": 5,
        "cases": cases, "ineligible": bundle["ineligible_cases"], "stage_counts": stage_counts, "costs": costs,
        "error": completion["error"], "natural_language_review_required": True, "successful_application_claimed": False,
        "source_inputs": [row["path"] for row in rows] + ["RECORD_MANIFEST.json"], "record_manifest_sha256": v.sha((directory / "RECORD_MANIFEST.json").read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="mode", required=True)
    execute = sub.add_parser("run")
    for name in ("output", "wheel", "skill", "authentication-file"): execute.add_argument("--" + name, type=Path, required=True)
    check = sub.add_parser("check"); check.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = verify_campaign(args.directory) if args.mode == "check" else run(args.output, args.wheel, args.skill, args.authentication_file)
    print(v.canonical(result).decode())


if __name__ == "__main__":
    main()
