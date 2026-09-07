"""Evaluate immutable producer final states without another model or gold fix."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tarfile
import time
from unittest.mock import patch

from research.softwarex.agent_handoff_v1 import run as producer
from research.softwarex.agent_handoff_v1 import validate as pv
from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1 import validate as hv

HERE = Path(__file__).resolve().parent
FILES = ("preparation.json", "started.json", "session.json", "prompt.txt", "metadata-hf.json",
         "provided-tests.patch", "base-source.tar.gz", "events.log", "stderr.log",
         "proposed-tracked.patch", "final-source.tar.gz")
MAX_BYTES = 192 * 1024 * 1024


def sources(folder):
    return [h.record(path, path.name) for path in sorted(folder.iterdir(), key=lambda p: p.name)
            if path.is_file() and path.suffix in {".py", ".md"}]


def record(path, name):
    raw = h.ordinary(path, MAX_BYTES)
    return {"path": name, "bytes": len(raw), "sha256": h.sha(raw)}


def input_inventory(root, count):
    rows = [record(root / "freeze.json", "freeze.json")]
    for index in range(count):
        for name in FILES:
            path = root / f"case-{index:02d}" / name
            if path.exists():
                rows.append(record(path, f"case-{index:02d}/" + name))
    return rows


def reconstruct(prepared, index, output):
    """Inert reconstruction: public test patch only, never the reference fix."""
    started = time.perf_counter()
    frozen = h.strict(h.ordinary(prepared / "freeze.json"))
    case = frozen["cases"][index]
    case_input = prepared / f"case-{index:02d}"
    summary = pv.validate_receipt(case_input / "session.json", directory=prepared)
    h.require(summary["model_invocation_attempted"] is True and summary["boundary_pass"] is True,
              "producer source boundary not established")
    session = h.strict(h.ordinary(case_input / "session.json"))
    prep = h.strict(h.ordinary(case_input / "preparation.json"))
    base_raw = h.bound(case_input, prep["source_archive"], h.MAX_ARCHIVE_BYTES)
    h.require(len(base_raw) == case["source_archive"]["bytes"]
              and h.sha(base_raw) == case["source_archive"]["sha256"], "base archive differs from selected immutable source")
    base = output / "base-source"
    extraction = h.extract_source(base_raw, base, case["base_commit"])
    test_patch = h.bound(case_input, prep["test_patch"])
    h.require(h.patch_paths(test_patch.decode()) == prep["test_paths"], "public test patch paths differ")
    operations = [h.git(base, ["init", "--template=", "--initial-branch=main", "."]),
                  h.git(base, ["apply", "--check", "--whitespace=nowarn", "-"], test_patch),
                  h.git(base, ["apply", "--whitespace=nowarn", "-"], test_patch)]
    base_inventory = producer.inventory(base)
    h.require(base_inventory == prep["before"], "reconstructed baseline differs from producer input")
    final_raw = h.bound(case_input, session["final_source"], MAX_BYTES)
    expected = pv.source_archive_inventory(final_raw)
    h.require(expected == sorted(session["after"], key=lambda r: r["path"]), "final archive differs from captured source")
    final = output / "source"
    final.mkdir()
    # Validate the entire archive before creating anything. No extractall, links,
    # modes, Git metadata, or member-provided permissions are accepted.
    with tarfile.open(fileobj=io.BytesIO(final_raw), mode="r:gz") as archive:
        for member in archive.getmembers():
            name = member.name[len("agent-final/"):].rstrip("/")
            path = final.joinpath(*name.split("/"))
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with path.open("xb") as stream:
                    stream.write(archive.extractfile(member).read())
                path.chmod(0o644)
    h.require(producer.inventory(final) == session["after"], "reconstructed final source inventory differs")
    result = {"case_id": case["case_id"], "producer": summary, "base_archive": prep["source_archive"],
              "final_archive": session["final_source"], "base_inventory": base_inventory,
              "final_inventory": session["after"], "base_extraction": extraction, "inert_git_operations": operations,
              "public_test_patch_sha256": h.sha(test_patch), "reference_source_patch_applied": False,
              "full_final_snapshot_used": True, "reconstruction_outer_ms": (time.perf_counter() - started) * 1000}
    h.save(output / "reconstruction.json", result)
    return base, final, result


def fresh_classification(producer_summary, baseline, final):
    base_verdict = baseline["result"]["verdict"] if baseline is not None else None
    final_verdict = final["result"]["verdict"] if final is not None else None
    final_pass = (final_verdict is not None and final_verdict["exit_code"] == 0
                  and any(row["call"] == "passed" for row in final_verdict["nodes"].values()))
    baseline_failed = base_verdict is not None and base_verdict["exit_code"] == 1
    failed = [node for node, row in base_verdict["nodes"].items()
              if row["call"] == "failed" and row["wasxfail"] is False] if baseline_failed else []
    same_nodes = (base_verdict is not None and final_verdict is not None
                  and set(base_verdict["nodes"]) == set(final_verdict["nodes"]))
    repaired = bool(final_pass and failed and same_nodes and all(
        final_verdict["nodes"][node]["call"] == "passed" and final_verdict["nodes"][node]["wasxfail"] is False
        for node in failed))
    complete = producer_summary.get("producer_completed") is True
    verified = (complete and producer_summary.get("boundary_pass") is True
                and producer_summary.get("source_patch_present") is True and repaired)
    return {"producer_completed": complete, "baseline_oracle_available": base_verdict is not None,
            "baseline_exit_code": base_verdict["exit_code"] if base_verdict is not None else None,
            "baseline_nonxfail_failed_calls": failed, "final_oracle_available": final_verdict is not None,
            "final_fresh_pass": final_pass, "same_collected_node_set": same_nodes,
            "fresh_regression_repaired": repaired, "completed_verified_fix": verified}


def saved_oracle(root, name, capture):
    try:
        return hv.oracle(root, name, capture)
    except (ValueError, KeyError, OSError, TypeError):
        return None


def cutoff_seconds(value):
    cutoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
    h.require(cutoff.tzinfo is not None, "timezone-qualified UTC cutoff required")
    return (cutoff.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()


def run(prepared, engine, output, cutoff_utc, budget_seconds=5400, image_build=None):
    h.require(os.name == "posix", "Linux laboratory required")
    h.require(type(budget_seconds) is int and 1 <= budget_seconds <= 7200, "bounded campaign budget required")
    prepared, engine = Path(prepared).resolve(strict=True), Path(engine).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    h.require(not output.exists() and output.parent.is_dir() and not output.is_relative_to(prepared)
              and not output.is_relative_to(engine), "new external companion output required")
    frozen = h.strict(h.ordinary(prepared / "freeze.json"))
    cases = producer.c.selected(frozen["ledger"])
    h.require(cases == frozen["cases"], "producer selection differs from fixed rule")
    remaining = min(float(budget_seconds), cutoff_seconds(cutoff_utc))
    h.require(remaining > 0, "operator start cutoff already reached")
    captured_inputs = input_inventory(prepared, len(cases))
    components = h.load_engine(engine)
    bench, api, oci, load_manifest, engine_binding = components
    image_summary, image_sources = None, None
    if image_build is not None:
        from research.softwarex.handoff_image_v2 import build_image as ib
        from research.softwarex.handoff_image_v2 import run as ir
        from research.softwarex.handoff_image_v2.validate import validate_image
        image_build = Path(image_build).resolve(strict=True)
        image_summary = validate_image(image_build)
        image_sources = ib.source_inventory()
    image = image_summary["image"]["requested"] if image_summary else h.IMAGE
    own, handoff_sources, producer_sources = sources(HERE), h.code_inventory(), sources(producer.HERE)
    output.mkdir()
    inspection = output / "runtime-inspection"
    inspection.mkdir()
    (inspection / ".zerorun.json").write_bytes(bench._frozen_pytest_manifest_bytes(runtime_image=image))
    task = load_manifest(inspection / ".zerorun.json").tasks["pytest-generalization"]
    attestation = oci.inspect_runtime(task, allow_pull=False, repository_root=engine)
    if image_summary:
        h.require(attestation["image_id"] == image_summary["image"]["image_id"]
                  and attestation["config_sha256"] == image_summary["image"]["config_sha256"], "reviewed image/runtime mismatch")
    protocol = {"schema": "zerorun.agent-handoff-evaluation-protocol.v1", "started_utc": h.utc(),
        "producer_freeze": record(prepared / "freeze.json", "freeze.json"), "producer_inputs": captured_inputs,
        "cases": cases, "phase": frozen["phase"], "planned_cases": 2 if frozen["phase"] == "pilot" else 6,
        "sources": own, "handoff_sources": handoff_sources, "producer_sources": producer_sources,
        "engine": engine_binding, "runtime_attestation": attestation, "runtime_image": image,
        "image_deployment": image_summary, "image_sources": image_sources,
        "execution_seconds": 120, "budget_seconds": budget_seconds, "cutoff_utc": cutoff_utc,
        "adapter_substitutions": ["make_state returns verified producer final snapshot; never gold patch",
            "symmetric runtime execution cap120s", "optional reviewed image-v2 arm/layer only"],
        "new_model_calls": 0, "mcp_authority_created": False, "natural_hit_frequency_study": False,
        "reference_patch_applied": False, "agent_and_reference_patch_denominators_combined": False}
    h.save(output / "protocol.json", protocol)
    private = output / "private-cache-authentication-NOT-FOR-PUBLICATION"
    private.mkdir(mode=0o700)
    starter = output / "dependency-starter"
    starter.mkdir()
    deadline = time.monotonic() + remaining
    rows, campaign_error, material_stop = [], None, False

    @contextmanager
    def contained_layer(root, *, runtime_image, extra_requirements):
        h.require(runtime_image == image and tuple(extra_requirements) == ("pretend",), "wrong derived layer request")
        yield {"provenance": {"schema": "zerorun.image-contained-dependency-deployment.v2",
                "image": image_summary["image"], "original_locked_layer": image_summary["dependency_provenance"],
                "preparation_precedes_run": True, "build_once_ms": image_summary["setup_outer_ms"]}}
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"ZERORUN_TRUST_ROOT": str(private), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}))
            stack.enter_context(patch.object(bench, "_PYTEST_EXECUTION_TIMEOUT_SECONDS", 120))
            stack.enter_context(patch.object(oci, "DOCKER_EXECUTION_TIMEOUT_SECONDS", 120))
            if image_summary:
                stack.enter_context(patch.object(h, "IMAGE", image))
                stack.enter_context(patch.object(h, "arm_workspace", ir.image_arm_workspace))
                stack.enter_context(patch.object(bench, "_frozen_dependency_layer", contained_layer))
            layer_started = time.perf_counter()
            layer = stack.enter_context(bench._frozen_dependency_layer(starter, runtime_image=image, extra_requirements=("pretend",)))
            h.save(output / "dependency-layer.json", {"provenance": layer["provenance"],
                "setup_outer_ms": (time.perf_counter() - layer_started) * 1000,
                "image_build_outer_ms": image_summary["setup_outer_ms"] if image_summary else None})
            for index, case in enumerate(cases):
                case_out = output / "cases" / case["case_id"]
                case_out.mkdir(parents=True)
                row = {"case_id": case["case_id"], "repo": case["repo"], "case_index": index,
                       "disposition": None, "producer": None, "error": None, "classification": None}
                try:
                    if material_stop:
                        row["disposition"] = "NOT_RUN_CORRECTNESS_STOP"
                    elif time.monotonic() >= deadline or cutoff_seconds(cutoff_utc) <= 0:
                        row["disposition"] = "NOT_RUN_BUDGET"
                    elif not (prepared / f"case-{index:02d}" / "session.json").is_file():
                        row["disposition"] = "PRODUCER_UNAVAILABLE"
                    else:
                        summary = pv.validate_receipt(prepared / f"case-{index:02d}" / "session.json", directory=prepared)
                        row["producer"] = summary
                        if summary["model_invocation_attempted"] is not True or summary["boundary_pass"] is not True:
                            row["disposition"] = "PRODUCER_NOT_EVALUABLE"
                        else:
                            base, final, reconstruction = reconstruct(prepared, index, case_out)
                            baseline_work = case_out / "baseline-workspace"
                            baseline_setup_started = time.perf_counter()
                            setup, _ = h.arm_workspace(bench, base, baseline_work, layer, case["targets"])
                            baseline_before = h.identity(baseline_work)
                            h.save(case_out / "baseline-setup.json", {"setup": setup, "before": baseline_before,
                                "setup_outer_ms": (time.perf_counter() - baseline_setup_started) * 1000})
                            try:
                                h.operation(case_out, "baseline-oracle", lambda: h.fresh_oracle(bench, baseline_work, case["targets"], case_out / "baseline-capture"))
                            except Exception:
                                pass  # The failed raw operation is retained; not proof of repair.
                            baseline_after = h.identity(baseline_work)
                            h.save(case_out / "baseline-source-after.json", baseline_after)
                            h.check_equal(baseline_before == baseline_after, "baseline source changed during independent fresh test")
                            def final_state(actual_case, ledger_base, actual_output):
                                h.require(actual_case == case and actual_output == case_out, "source adapter case changed")
                                h.require(producer.inventory(final) == reconstruction["final_inventory"], "final source drift before chain")
                                return final
                            with patch.object(h, "make_state", final_state):
                                controlled = h.run_case(case, prepared, case_out, components, layer, deadline)
                            h.save(case_out / "controlled-completion.json", controlled)
                            row["disposition"] = "COMPLETE"
                except Exception as error:
                    material_stop = isinstance(error, h.MaterialMismatch)
                    row.update(disposition="MATERIAL_CORRECTNESS_STOP" if material_stop else "INCOMPLETE_OR_UNSUPPORTED",
                               error={"type": type(error).__name__, "message": str(error)})
                baseline = saved_oracle(case_out, "baseline-oracle", "baseline-capture")
                final_oracle = saved_oracle(case_out, "compatibility-oracle", "compatibility-capture")
                row["classification"] = fresh_classification(row["producer"] or {}, baseline, final_oracle)
                h.save(case_out / "completion.json", row)
                rows.append(row)
                print(json.dumps({"case_id": row["case_id"], "disposition": row["disposition"],
                    "completed_verified_fix": row["classification"]["completed_verified_fix"]}), flush=True)
    except Exception as error:
        campaign_error = {"type": type(error).__name__, "message": str(error)}
    for index, case in list(enumerate(cases))[len(rows):]:
        row = {"case_id": case["case_id"], "repo": case["repo"], "case_index": index,
               "disposition": "NOT_RUN_CAMPAIGN_FAILURE", "producer": None, "error": None,
               "classification": fresh_classification({}, None, None)}
        h.save(output / "cases" / case["case_id"] / "completion.json", row)
        rows.append(row)
    after_runtime = [h.record(engine / row["path"], row["path"]) for row in engine_binding["runtime_files"]]
    authorities = list(private.glob("repositories/*/authorities/*.json"))
    unchanged = (own == sources(HERE) and handoff_sources == h.code_inventory()
                 and producer_sources == sources(producer.HERE) and captured_inputs == input_inventory(prepared, len(cases))
                 and after_runtime == engine_binding["runtime_files"])
    if image_summary:
        unchanged = unchanged and image_sources == ib.source_inventory()
    after_attestation = oci.inspect_runtime(task, allow_pull=False, repository_root=engine)
    result = {"schema": "zerorun.agent-handoff-evaluation-completion.v1", "completed_utc": h.utc(),
        "protocol_sha256": h.sha(h.ordinary(output / "protocol.json")), "cases": rows,
        "selected_cases": len(cases), "planned_cases": protocol["planned_cases"],
        "complete_controlled_cases": sum(row["disposition"] == "COMPLETE" for row in rows),
        "completed_verified_fixes": sum(row["classification"]["completed_verified_fix"] for row in rows),
        "final_fresh_passes": sum(row["classification"]["final_fresh_pass"] for row in rows),
        "campaign_error": campaign_error, "material_correctness_stop": material_stop,
        "runtime_after": after_runtime, "inputs_and_sources_unchanged": unchanged,
        "sources_after": sources(HERE), "handoff_sources_after": h.code_inventory(),
        "producer_sources_after": sources(producer.HERE), "producer_inputs_after": input_inventory(prepared, len(cases)),
        "image_sources_after": ib.source_inventory() if image_summary else None,
        "runtime_attestation_after": after_attestation,
        "mcp_authority_files_found": len(authorities), "new_model_calls": 0,
        "all_selected_outcomes_retained": len(rows) == len(cases), "natural_hit_frequency": False}
    h.save(output / "completion.json", result)
    h.require(unchanged and not authorities and h.encoded(attestation) == h.encoded(after_attestation),
              "companion source/input/authority/runtime boundary changed")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("prepared", "engine", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--cutoff-utc", required=True)
    p.add_argument("--budget-seconds", type=int, default=5400)
    p.add_argument("--image-build", type=Path)
    p.add_argument("--execute-reviewed-lab", action="store_true", required=True)
    args = p.parse_args()
    result = run(args.prepared, args.engine, args.output, args.cutoff_utc, args.budget_seconds, args.image_build)
    print(json.dumps({key: result[key] for key in ("selected_cases", "complete_controlled_cases", "completed_verified_fixes", "campaign_error")}, sort_keys=True))
    return int(result["campaign_error"] is not None or result["material_correctness_stop"])


if __name__ == "__main__":
    raise SystemExit(main())
