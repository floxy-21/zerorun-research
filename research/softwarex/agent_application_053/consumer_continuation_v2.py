"""Execute previously unattempted consumers after an exact-byte reader repair."""
from __future__ import annotations

import argparse
import base64
from pathlib import Path
import os
import shutil
import tarfile
import time
from types import SimpleNamespace

from . import consumer as original
from . import consumer_v2 as c
from . import consumer_campaign as previous
from . import validation as v, seed

SCHEMA = "zerorun.real-consumer-continuation.0.5.3.v2"
PREFIX = "/home/floxy/zerorun-agent-application-053-consumers-20260908-v2"
PRIOR_SHA = "4faa24a3b2b40d5e93e80ee30cdd2b8c55f87d81dde6059af3b8255c3f28ab34"


def verify_prior(directory):
    v.require(v.sha((directory / "RECORD_MANIFEST.json").read_bytes()) == PRIOR_SHA, "original preparation export differs")
    checked = previous.verify_campaign(directory)
    completion = v.strict((directory / "campaign-completion.json").read_bytes())
    v.require(checked["error"] is None and len(completion["cases"]) == 5 and all(row["seeded"] is True
        and row["plan_ready"] is False and row["stages"] == []
        and row["error"] == {"type": "ValueError", "message": "prospective task prompt differs"}
        for row in completion["cases"]), "prior outcome is not the exact preconsumer reader refusal")
    v.require(all(count["attempted"] == 0 for count in checked["stage_counts"].values()), "prior model attempt cannot be retried")
    v.require(not any(directory.glob("cases/*/plan/attempts/*/completion.json")), "original model attempt record exists")
    return checked


def source_paths():
    return {**previous.bindings(), "consumer_v2.py": Path(c.__file__), "consumer_continuation_v2.py": Path(__file__)}


def restore(case, qualification, bundle_item):
    root = Path(qualification["root"])
    v.require(v.inventory(root) == qualification["states"]["final"]["files"], "restoration begins outside exact final state")
    name, = qualification["restoration"]["changed_paths"]
    data = base64.b64decode(bundle_item["restoration_base64"], validate=True)
    expected = v.checked_rows(qualification["states"]["restored"]["files"])[name]
    v.require({"path": name, "bytes": len(data), "sha256": v.sha(data)} == expected, "reviewed baseline production bytes differ")
    with v.real(root / name).open("wb") as stream:
        stream.write(data)
    after = v.inventory(root)
    v.require(after == qualification["states"]["restored"]["files"], "restoration exceeded qualified exact state")
    c.save(case / "restoration.json", {"completed_utc": c.utc(), "changed_paths": [name], "source_after": after,
        "protected_tests_unchanged": True, "reference_source_patch_applied": False})


def run(prior, output, authentication_file):
    v.require(os.name == "posix" and os.getuid() != 0, "non-root Linux laboratory required")
    prior = v.real(prior, directory=True); checked = verify_prior(prior)
    output = Path(output); v.require(str(output) == PREFIX and not output.exists(), "new exact continuation output required")
    authentication_file = v.real(authentication_file)
    v.require(not authentication_file.is_relative_to(output), "authentication must remain external")
    output.mkdir(); records = output / "record-only"; records.mkdir()
    shutil.copytree(prior, records / "prior")
    verify_prior(records / "prior")
    bundle = v.strict((prior / "inspection-bundle.json").read_bytes())
    provenance = records / "provenance"; provenance.mkdir()
    sources = []
    for name, path in sorted(source_paths().items()):
        c.write(provenance / name, path.read_bytes()); sources.append(c.file_row(provenance / name, records))
    protocol = {"schema": SCHEMA, "started_utc": c.utc(), "prior_manifest_sha256": PRIOR_SHA,
        "sources": sources, "selected_cases": list(v.CASES), "eligible_cases": bundle["eligible_cases"], "stages": list(v.STAGES),
        "runtime_identity": c.CORE_IDENTITY, "core_commit": c.CORE, "seconds_per_stage": c.SECONDS,
        "case_execution_order": "original selection order, sequential", "new_seeds": 0, "new_authority": 0,
        "model_retries": 0, "preserved_preconsumer_failures": 5, "original_skill_bytes_normalized": False,
        "correction": "Versioned reader compares exact UTF-8 prompt bytes; original CRLF skill, prompts, raw results and helpers remain unchanged. No original model invocation exists to retry."}
    c.save(records / "protocol.json", protocol)
    result = {"schema": SCHEMA, "protocol_sha256": v.sha((records / "protocol.json").read_bytes()),
        "cases": [], "error": None, "successful_application_claimed": False}
    private_base = output / "private-client-homes"; private_base.mkdir(mode=0o700)
    begin = time.monotonic()
    try:
        for bundle_item in bundle["cases"]:
            case_id = bundle_item["case_id"]; q = bundle_item["qualification"]
            directory = records / "cases" / case_id; directory.mkdir(parents=True)
            prior_case = records / "prior/cases" / case_id
            old = v.strict((prior_case / "plan/plan.json").read_bytes())
            item = {"case_id": case_id, "plan_ready": False, "stages": [], "error": None}
            try:
                verified_seed = seed.validate(prior_case / "seed")
                v.require(verified_seed["source_identity"] == q["states"]["final"]["identity_sha256"]
                    and old["qualification"] == q and v.inventory(Path(q["root"])) == q["states"]["final"]["files"], "original seed/source/qualification differs")
                client = old["client"]; plan = directory / "plan"
                args = SimpleNamespace(qualification=prior_case / "qualification.json", seed_receipt=prior_case / "seed/receipt.json",
                    skill_file=prior_case / "plan/skill.md", trust_root=Path(old["trust_root"]), output=plan,
                    **{name + "_command": Path(client[name]["invoked"]) for name in ("codex", "node", "zerorun", "python")})
                v.require(c.freeze(args) == 0, "versioned consumer freeze failed")
                c.load_plan(plan)
                for stage in v.STAGES:
                    v.require((plan / "prompts" / (stage + ".md")).read_bytes() == (prior_case / "plan/prompts" / (stage + ".md")).read_bytes(), "original prompt bytes changed")
                item["plan_ready"] = True
                for stage in v.STAGES:
                    if stage == "restored": restore(directory, q, bundle_item)
                    private_home = private_base / (case_id + "--" + stage); private_home.mkdir(mode=0o700)
                    auth_copy = private_home / "auth.json"
                    stage_result = {"stage": stage, "error": None}
                    try:
                        v.require(not auth_copy.exists(), "private authentication destination already exists")
                        shutil.copyfile(authentication_file, auth_copy); os.chmod(auth_copy, 0o600)
                        c.run(SimpleNamespace(plan=plan, stage=stage, client_home=private_home))
                        stage_result["validated"] = c.validate_attempt(plan, stage)
                    except Exception as error:
                        stage_result["error"] = {"type": type(error).__name__, "message": str(error)}
                    finally:
                        v.require(private_home.parent == private_base and auth_copy == private_home / "auth.json", "fresh private cleanup target differs")
                        if auth_copy.exists():
                            v.require(not auth_copy.is_symlink(), "fresh authentication copy became a link")
                            auth_copy.unlink()
                    item["stages"].append(stage_result)
                    print(v.canonical({"case_id": case_id, "stage": stage, "error": stage_result["error"],
                        "model_invoked": stage_result.get("validated", {}).get("model_invoked", False)}).decode(), flush=True)
            except Exception as error:
                item["error"] = {"type": type(error).__name__, "message": str(error)}
            result["cases"].append(item); c.save(directory / "case-completion.json", item)
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        result.update(completed_utc=c.utc(), elapsed_seconds=time.monotonic() - begin)
        c.save(records / "completion.json", result)
        previous.seal(records)
        with tarfile.open(output / "record-only.tar.gz", "x:gz") as archive:
            for path in sorted(records.rglob("*")):
                if path.is_file(): archive.add(path, arcname="record-only/" + path.relative_to(records).as_posix(), recursive=False)
    return result


def verify_campaign(directory):
    directory = v.real(directory, directory=True)
    manifest = v.strict((directory / "RECORD_MANIFEST.json").read_bytes())
    rows = manifest["files"]
    v.require(len(rows) == len({r["path"] for r in rows}) and all(c.file_row(directory / v.safe_relative(r["path"]), directory) == r for r in rows), "continuation raw bytes differ")
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    v.require(actual == {r["path"] for r in rows} | {"RECORD_MANIFEST.json"}, "continuation inventory differs")
    prior = verify_prior(directory / "prior")
    protocol = v.strict((directory / "protocol.json").read_bytes()); completion = v.strict((directory / "completion.json").read_bytes())
    expected = [dict(c.file_row(path), path="provenance/" + name) for name, path in sorted(source_paths().items())]
    v.require(protocol["schema"] == completion["schema"] == SCHEMA and protocol["prior_manifest_sha256"] == PRIOR_SHA
        and completion["protocol_sha256"] == v.sha((directory / "protocol.json").read_bytes()) and protocol["sources"] == expected
        and protocol["stages"] == list(v.STAGES) and protocol["seconds_per_stage"] == c.SECONDS
        and protocol["new_seeds"] == protocol["new_authority"] == protocol["model_retries"] == 0
        and protocol["runtime_identity"] == c.CORE_IDENTITY and protocol["core_commit"] == c.CORE, "continuation protocol differs")
    bundle = v.strict((directory / "prior/inspection-bundle.json").read_bytes())
    v.require(protocol["selected_cases"] == list(v.CASES) and protocol["eligible_cases"] == bundle["eligible_cases"], "continuation selection differs")
    cases, ledger = [], []
    counts = {stage: {"attempted": 0, "completed": 0, "reuse": 0, "fresh_success": 0, "fresh_failure": 0, "refused": 0,
        "material_interpretation_failure": 0, "boundary_failures": 0, "fresh_request_satisfied": 0} for stage in v.STAGES}
    costs = {"model_seconds": 0.0, "seed_seconds": prior["costs"]["seed_seconds"]}
    for bundle_item in bundle["cases"]:
        case_id = bundle_item["case_id"]; q = bundle_item["qualification"]
        case = directory / "cases" / case_id; prior_case = directory / "prior/cases" / case_id
        saved = v.strict((case / "case-completion.json").read_bytes())
        v.require(saved["case_id"] == case_id, "continuation case order differs"); ledger.append(saved)
        item = {"case_id": case_id, "seed": seed.validate(prior_case / "seed"), "stages": [], "operator_failure": saved["error"]}
        if saved["plan_ready"]:
            _, plan = c.load_plan(case / "plan")
            v.require(plan["qualification"] == q and v.sha((case / "plan/seed-receipt.json").read_bytes()) == item["seed"]["receipt_sha256"], "continuation source/seed binding differs")
            for stage in v.STAGES:
                v.require((case / "plan/prompts" / (stage + ".md")).read_bytes() == (prior_case / "plan/prompts" / (stage + ".md")).read_bytes(), "continuation prompt bytes changed")
                attempt = case / "plan/attempts" / stage
                if not (attempt / "RECORD_MANIFEST.json").is_file():
                    item["stages"].append({"stage": stage, "disposition": "NOT_ATTEMPTED_OR_UNSEALED"}); continue
                checked = c.validate_attempt(case / "plan", stage); row = v.strict((attempt / "completion.json").read_bytes())
                analysis = checked["analysis"] or {}; total = counts[stage]
                if row["model_invoked"]:
                    process = v.strict((attempt / "model.json").read_bytes()); started = v.strict((attempt / "model.started.json").read_bytes())
                    v.require(row["process"] == process and all(started[k] == process[k] for k in ("command", "cwd", "started_utc", "timeout_seconds")), "continuation raw process differs")
                    elapsed = process["elapsed_seconds"]
                    v.require(type(elapsed) in (int, float) and 0 <= elapsed <= c.SECONDS + 20, "continuation duration invalid")
                    costs["model_seconds"] += elapsed
                total["attempted"] += int(checked["model_invoked"])
                total["completed"] += int(checked["process_completed_successfully"] and analysis.get("model_turn_completed", False))
                total["reuse"] += int(analysis.get("reuse_observed", False))
                total["boundary_failures"] += int(bool(analysis) and not analysis.get("boundary_pass", False))
                total["fresh_request_satisfied"] += int(analysis.get("fresh_request_satisfied", False))
                total["material_interpretation_failure"] += int(analysis.get("material_interpretation_failure", False))
                for label, category in (("fresh_success", "FRESH_SUCCESS"), ("fresh_failure", "FRESH_FAILURE"), ("refused", "REFUSED")):
                    total[label] += int(any(r["classification"] == category for r in analysis.get("completed_mcp_results", [])))
                item["stages"].append(dict(checked, raw_path=attempt.relative_to(directory).as_posix()))
            if (case / "restoration.json").is_file():
                restored = v.strict((case / "restoration.json").read_bytes())
                v.require(restored["source_after"] == q["states"]["restored"]["files"] and restored["changed_paths"] == q["restoration"]["changed_paths"], "continuation restoration differs")
        cases.append(item)
    v.require(completion["cases"] == ledger, "continuation completion and case ledgers differ")
    return {"schema": SCHEMA, "read_only": True, "records_reconciled": True, "selected": 6, "producer_verified_fixes": 5, "eligible": 5,
        "cases": cases, "ineligible": prior["ineligible"], "stage_counts": counts, "costs": costs, "error": completion["error"],
        "natural_language_review_required": True, "successful_application_claimed": False, "prior_preparation": prior,
        "record_manifest_sha256": v.sha((directory / "RECORD_MANIFEST.json").read_bytes()),
        "source_inputs": [r["path"] for r in rows] + ["RECORD_MANIFEST.json"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="mode", required=True)
    execute = sub.add_parser("run")
    for name in ("prior", "output", "authentication-file"): execute.add_argument("--" + name, type=Path, required=True)
    check = sub.add_parser("check"); check.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = verify_campaign(args.directory) if args.mode == "check" else run(args.prior, args.output, args.authentication_file)
    print(v.canonical(result).decode())


if __name__ == "__main__":
    main()
