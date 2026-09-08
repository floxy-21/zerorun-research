"""One explicitly outcome-informed repeated-consumer follow-up; no runtime edits.

Uses the frozen public handoff helpers through PYTHONPATH. --check performs
only read-only reconstruction; ordinary invocation runs the six fixed blocks.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import math
import os
from pathlib import Path
import shutil
import tarfile
import time
from unittest.mock import patch

from research.softwarex.handoff_v1 import run as h, validate as v
from research.softwarex.handoff_image_v2.run import image_arm_workspace

CASE = "joshtemple__lkml-85"
COUNTS = (1, 2, 4)
PLAN = tuple((n, b) for n in COUNTS for b in (0, 1))
IMAGE = "127.0.0.1:19559/zerorun-compatible-v2@sha256:78039048251ecf889454509c6ef203f922d9dae9fa3b570a4ade3a9d4b552465"
LEDGER_SHA = "4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997"
V6_MANIFEST_SHA = "6cc44af207e90630762b3dcb788d5331287419a861d7d20fbaba63c5ccb1a5a1"
V6_COMPLETION_SHA = "849ee0c1c36500eac41c79666ed9a59c271a32d5a617be7a49ff7adae284d260"
IMAGE_COMPLETION_SHA = "ffce2613a7b7305df307b3b811c6b8ed6c8f818af7cf0addbd4534bd34f75045"
ENGINE_COMMIT = "ebf2884df12573d63f45813200e0675288d12096"
HARNESS_COMMIT = "0528905a52b74df78aa4e5a09219df34620282dd"
AMENDMENT_SHA = "56c85471d42402bea5ca0a81a6b9a400daa7b4238848d6c5c80ed8fde309978f"
IMAGE_HELPER_SHA = "f07ff59ef31ad0aea4b80eb0ab2c6c8103012ef8eea655548147b782b005e4dc"
EXCLUDED = {"source", "workspace", "compatibility-workspace", "private-cache-authentication-NOT-FOR-PUBLICATION",
            "runtime-inspection", ".git", ".zerorun", "__pycache__", "record-only"}


def selected_case(v6):
    """Bind original records before applying the disclosed selection rule."""
    v6 = Path(v6)
    raw = h.ordinary(v6 / "RECORD_MANIFEST.json")
    h.require(h.sha(raw) == V6_MANIFEST_SHA, "V6 selection source manifest changed")
    rows = h.strict(raw)["files"]
    h.require(len(rows) == len({r["path"] for r in rows}) == 1381, "V6 selection inventory differs")
    for row in rows:
        h.bound(v6, row)
    v.validate_saved(v6 / "run")
    completion_raw = h.ordinary(v6 / "run/completion.json")
    h.require(h.sha(completion_raw) == V6_COMPLETION_SHA, "V6 selection completion changed")
    return selection_from_completion(h.strict(completion_raw))


def selection_from_completion(completion):
    scores = []
    for case in completion["cases"]:
        h.require(case["disposition"] == "COMPLETE" and len(case["blocks"]) == 2, "V6 incomplete case hidden")
        values = [b["arms"]["zerorun"]["producer"]["result"]["phase_ms"]["snapshot_prepare"] for b in case["blocks"]]
        h.require(all(type(n) in (int, float) and math.isfinite(n) and n >= 0 for n in values), "invalid selection phase clocks")
        scores.append({"case_id": case["case_id"], "cold_snapshot_prepare_ms": sum(values), "block_ms": values})
    scores.sort(key=lambda row: (-row["cold_snapshot_prepare_ms"], row["case_id"]))
    h.require(scores[0]["case_id"] == CASE, "predeclared follow-up selection changed")
    return {"rule": "largest two-block sum of V6 cold-producer snapshot_prepare; case ID tie-break",
        "outcome_informed": True, "selected": scores[0], "all_case_scores": scores,
        "v6_manifest_sha256": V6_MANIFEST_SHA, "v6_completion_sha256": V6_COMPLETION_SHA}


def measurements(arms, count):
    h.require(set(arms) == {"fresh", "zerorun"} and count in COUNTS, "paired conditions differ")
    for arm in arms.values():
        h.require(len(arm["consumers"]) == count, "consumer denominator differs")
    producer = {name: arm["producer"]["outer_ms"] for name, arm in arms.items()}
    consumers = {name: [op["outer_ms"] for op in arm["consumers"]] for name, arm in arms.items()}
    chain = {name: producer[name] + sum(consumers[name]) for name in arms}
    setup = {name: arms[name]["setup_outer_ms"] for name in arms}
    h.require(all(type(ms) in (int, float) and math.isfinite(ms) and ms > 0
                  for ms in [*producer.values(), *(x for values in consumers.values() for x in values)])
              and all(type(ms) in (int, float) and math.isfinite(ms) and ms >= 0 for ms in setup.values()),
              "invalid operation clocks")
    inclusive = {name: chain[name] + setup[name] for name in arms}
    return {"consumer_requests": count, "producer_ms": producer, "consumer_ms": consumers,
        "chain_ms": chain, "setup_ms": setup, "setup_inclusive_chain_ms": inclusive,
        "chain_saved_fraction": 1 - chain["zerorun"] / chain["fresh"],
        "setup_inclusive_saved_fraction": 1 - inclusive["zerorun"] / inclusive["fresh"],
        "oracle_ms": {name: arm["oracle"]["outer_ms"] for name, arm in arms.items()},
        "fresh_diagnostics_ms": arms["zerorun"]["diagnostics"]["outer_ms"],
        "consumer_statuses": [op["result"]["status"] for op in arms["zerorun"]["consumers"]],
        "cache_hits": sum(op["result"]["status"] == "HIT_REUSED" for op in arms["zerorun"]["consumers"])}


def block_order(count, block):
    return h.order(CASE + ":consumers=" + str(count), block)


def same_measurements(actual, expected):
    """Permit final-bit sum differences between Python 3.10 and newer hosts."""
    if type(actual) is float and type(expected) is float:
        h.require(math.isfinite(actual) and math.isfinite(expected)
                  and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-7), "measurement clock differs")
    elif isinstance(actual, dict) and isinstance(expected, dict):
        h.require(set(actual) == set(expected), "measurement fields differ")
        for key in actual: same_measurements(actual[key], expected[key])
    elif isinstance(actual, list) and isinstance(expected, list):
        h.require(len(actual) == len(expected), "measurement count differs")
        for left, right in zip(actual, expected): same_measurements(left, right)
    else:
        h.require(type(actual) is type(expected) and actual == expected, "measurement value differs")


def check_results(producer, consumers, diagnostic, expected, arm):
    h.check_equal(all(type(op["result"]["exit_code"]) is int and op["result"]["exit_code"] == expected["exit_code"] for op in [producer, *consumers]),
                  "consumer chain disagrees with fresh oracle")
    if arm == "zerorun":
        h.require(producer["result"]["status"] == "MISS_EXECUTED", "producer cache was not cold")
        for op in consumers:
            h.require(op["result"]["status"] in {"HIT_REUSED", "MISS_EXECUTED"}, "unsupported consumer disposition")
            if op["result"]["status"] == "HIT_REUSED":
                h.check_equal(op["result"]["cache_key"] == producer["result"]["cache_key"], "reused key changed")
        h.check_equal(type(diagnostic["result"]["exit_code"]) is int
                      and diagnostic["result"]["exit_code"] == expected["exit_code"], "fresh diagnostics disagree")
        h.require(diagnostic["result"]["status"] in {"VERIFY_MATCH", "MISS_EXECUTED"}, "diagnostics were not fresh")


def validate_block(directory, count, block, expected):
    arms = {}
    for name in ("fresh", "zerorun"):
        base = directory / name
        setup = v.read(base / "setup.json")
        h.require({p.name for p in base.glob("consumer-*.json")} == {
            f"consumer-{i:02d}" + suffix for i in range(count) for suffix in (".json", ".started.json")},
            "extra or missing consumer operation record")
        producer = v.operation(base, "producer")
        consumers = [v.operation(base, f"consumer-{i:02d}") for i in range(count)]
        oracle = v.oracle(base, "oracle", "oracle-capture")
        v.exact(oracle["result"]["verdict"], expected, "arm fresh oracle differs")
        diagnostic = v.operation(base, "diagnostics") if name == "zerorun" else None
        check_results(producer, consumers, diagnostic, expected, name)
        before = setup["before"]
        h.require(before["sha256"] == h.sha(h.encoded(before["rows"])), "source inventory hash differs")
        v.exact(before, v.read(base / "source-after.json"), "source changed")
        row = {"arm": name, "producer": producer, "consumers": consumers, "oracle": oracle,
            "diagnostics": diagnostic, "setup_outer_ms": setup["setup_outer_ms"], "source_sha256": before["sha256"]}
        v.exact(row, v.read(base / "summary.json"), "arm aggregation differs")
        arms[name] = row
    h.require(arms["fresh"]["source_sha256"] == arms["zerorun"]["source_sha256"], "paired inputs differ")
    actual = {"consumer_requests": count, "block": block, "order": block_order(count, block),
              "arms": arms, "measurements": measurements(arms, count)}
    saved = v.read(directory / "summary.json")
    v.exact({k: x for k, x in actual.items() if k != "measurements"},
            {k: x for k, x in saved.items() if k != "measurements"}, "block aggregation differs")
    same_measurements(actual["measurements"], saved["measurements"])
    return actual


def validate(directory):
    directory = Path(directory)
    p, c = v.read(directory / "protocol.json"), v.read(directory / "completion.json")
    h.require(p["schema"] == "zerorun.exploratory-amortization.v1"
              and c["schema"] == "zerorun.exploratory-amortization-completion.v1"
              and c["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")), "wrong follow-up binding")
    h.require(p["case_id"] == CASE and p["consumer_counts"] == list(COUNTS)
              and p["blocks_per_count"] == 2 and p["execution_seconds"] == 120
              and p["guard_seconds"] == 1200 and p["image"]["requested"] == IMAGE
              and p["selection"]["v6_manifest_sha256"] == V6_MANIFEST_SHA
              and p["model_calls"] == 0 and p["outcome_informed_selection"] is True,
              "follow-up scope changed")
    for name in ("driver", "amendment", "image_completion", "ledger", "prior_v6_completion", "selected_metadata", "selected_source_archive"):
        h.bound(directory, p[name])
    h.require(p["ledger"]["sha256"] == LEDGER_SHA and p["prior_v6_completion"]["sha256"] == V6_COMPLETION_SHA
              and p["image_completion"]["sha256"] == IMAGE_COMPLETION_SHA
              and p["amendment"]["sha256"] == AMENDMENT_SHA, "frozen input bindings changed")
    v.exact(p["selection"], selection_from_completion(v.read(directory / p["prior_v6_completion"]["path"])),
            "outcome-informed selection rule not reconstructed")
    case = next(c for c in h.validate_ledger(v.read(directory / p["ledger"]["path"])) if c["case_id"] == CASE)
    v.exact(p["targets"], case["targets"], "selected targets changed")
    for key, original in (("selected_metadata", "metadata"), ("selected_source_archive", "source_archive")):
        v.exact({k: p[key][k] for k in ("bytes", "sha256")},
                {k: case[original][k] for k in ("bytes", "sha256")}, "selected case input changed")
    metadata = h.metadata_row(h.bound(directory, p["selected_metadata"]), case)
    preparation = v.read(directory / "source-preparation.json")
    h.require(preparation["case_id"] == CASE and preparation["reference_patch_used"] is True
              and preparation["agent_edits_claimed"] is False
              and preparation["extraction"]["source_archive_sha256"] == case["source_archive"]["sha256"],
              "source preparation scope changed")
    v.exact(preparation["patches"], [{"kind": kind, "paths": h.patch_paths(metadata[kind]), "sha256": h.sha(metadata[kind].encode())}
                                   for kind in ("test_patch", "patch")], "supplied patches changed")
    h.require(h.sha(h.ordinary(directory / "driver.py")) == h.sha(h.ordinary(Path(__file__))), "follow-up checker/driver source differs")
    for row in p["study_sources"]:
        h.bound(h.HERE, row)
    h.require(p["image_helper"]["sha256"] == IMAGE_HELPER_SHA, "image adapter source pin changed")
    h.bound(h.HERE.parent / "handoff_image_v2", p["image_helper"])
    h.validate_runtime_rows(p["engine"]["runtime_files"])
    v.exact(p["runtime_attestation_before"], c["runtime_attestation_after"], "runtime image changed")
    h.require(c["sources_unchanged"] is True and c["runtime_source_unchanged"] is True
              and c["image_helper_unchanged"] is True and c["public_checkouts_clean_after"] is True, "source drift")
    expected = v.oracle(directory, "compatibility-oracle", "compatibility-capture")["result"]["verdict"]
    h.require(expected["exit_code"] == 0 and any(x["call"] == "passed" for x in expected["nodes"].values()), "compatibility failed")
    complete = []
    h.require(len(c["conditions"]) == len(PLAN), "planned conditions missing")
    for row, (count, block) in zip(c["conditions"], PLAN):
        h.require(row["consumer_requests"] == count and row["block"] == block, "condition order/denominator differs")
        if row["disposition"] == "COMPLETE":
            complete.append(validate_block(directory / f"n-{count}/block-{block}", count, block, expected))
        else:
            h.require(row["disposition"] in {"INCOMPLETE", "NOT_RUN_GUARD", "NOT_RUN_CORRECTNESS_STOP", "MATERIAL_CORRECTNESS_STOP"}, "unknown condition disposition")
    h.require(c["material_correctness_stop"] is any(r["disposition"] == "MATERIAL_CORRECTNESS_STOP" for r in c["conditions"]),
              "material stop was hidden or fabricated")
    h.require(len(complete) != 6 or c["campaign_error"] is None, "complete campaign hides an error")
    return {"schema": "zerorun.exploratory-amortization-reconciliation.v1", "reconciled": True,
        "conditions": c["conditions"], "complete_blocks": len(complete), "all_six_blocks_complete": len(complete) == 6,
        "blocks": [{"consumer_requests": b["consumer_requests"], "block": b["block"], "measurements": b["measurements"]} for b in complete],
        "material_correctness_stop": c["material_correctness_stop"], "shared_source_preparation_ms": p["shared_source_preparation_ms"],
        "historical_image_preparation_ms": p["historical_image_preparation_ms"],
        "host_continuity_certified": False, "independent_human_replication": False,
        "scope": "One outcome-informed case; identified status versus fresh execution services, not independent subjects or agent productivity; no pooling with V6."}


def execute(ledger, engine, v6, image_completion, amendment, output):
    h.require(os.name == "posix" and os.getuid() != 0, "non-root Linux laboratory required")
    engine, output = Path(engine).resolve(strict=True), Path(output).resolve(strict=False)
    harness = h.HERE.parents[2]
    h.require(not output.exists() and output.parent.is_dir()
              and all(not output.is_relative_to(p.resolve()) for p in (engine, harness, ledger.parent, Path(v6), image_completion.parent)),
              "new external output required")
    h.require(h.sha(h.ordinary(amendment)) == AMENDMENT_SHA, "prospective amendment differs")
    image_helper = h.record(h.HERE.parent / "handoff_image_v2/run.py", "run.py")
    h.require(image_helper["sha256"] == IMAGE_HELPER_SHA, "image adapter bytes changed")
    selection = selected_case(v6)
    ledger_raw = h.ordinary(ledger)
    h.require(h.sha(ledger_raw) == LEDGER_SHA, "original selected ledger changed")
    cases = h.validate_ledger(h.strict(ledger_raw)); case = next(c for c in cases if c["case_id"] == CASE)
    image_raw = h.ordinary(image_completion)
    h.require(h.sha(image_raw) == IMAGE_COMPLETION_SHA, "compatible-image completion changed")
    image_receipt = h.strict(image_raw); image = image_receipt["result"]["image"]
    h.require(image["requested"] == IMAGE and image_receipt["passed"] is True, "wrong deployment image")
    components = h.load_engine(engine); bench, api, oci, load_manifest, engine_binding = components
    binding = h.git(engine, ["rev-parse", "HEAD"])
    h.require(binding["stdout"].strip() == ENGINE_COMMIT and not h.git(engine, ["status", "--porcelain=v1", "--untracked-files=all"])["stdout"], "engine checkout identity/cleanliness differs")
    h.require(h.git(harness, ["rev-parse", "HEAD"])["stdout"].strip() == HARNESS_COMMIT
              and not h.git(harness, ["status", "--porcelain=v1", "--untracked-files=all"])["stdout"], "harness checkout identity/cleanliness differs")
    output.mkdir(); sources = h.code_inventory()
    for path, name in ((Path(__file__), "driver.py"), (amendment, "amendment.md"), (image_completion, "image-completion.json"), (ledger, "ledger.json"),
                       (Path(v6) / "run/completion.json", "prior-v6-completion.json"),
                       (ledger.parent / case["metadata"]["path"], "selected-metadata.json"),
                       (ledger.parent / case["source_archive"]["path"], "selected-source.tar.gz")):
        (output / name).write_bytes(h.ordinary(path))
    inspection = output / "runtime-inspection"; inspection.mkdir()
    (inspection / ".zerorun.json").write_bytes(bench._frozen_pytest_manifest_bytes(runtime_image=IMAGE))
    task = load_manifest(inspection / ".zerorun.json").tasks["pytest-generalization"]
    before = oci.inspect_runtime(task, allow_pull=False, repository_root=engine)
    h.require(before["image_id"] == image["image_id"] and before["config_sha256"] == image["config_sha256"], "actual image differs")
    started = time.monotonic(); source = h.make_state(case, ledger.parent, output)
    preparation_ms = (time.monotonic() - started) * 1000
    protocol = {"schema": "zerorun.exploratory-amortization.v1", "started_utc": h.utc(),
        "case_id": CASE, "targets": case["targets"], "consumer_counts": list(COUNTS), "blocks_per_count": 2,
        "execution_seconds": 120, "guard_seconds": 1200, "selection": selection, "outcome_informed_selection": True,
        "model_calls": 0, "image": image, "runtime_attestation_before": before, "engine": engine_binding,
        "study_sources": sources, "shared_source_preparation_ms": preparation_ms,
        "image_helper": image_helper,
        "historical_image_preparation_ms": image_receipt["setup_outer_seconds"] * 1000,
        **{key: h.record(output / name, name) for key, name in (("driver", "driver.py"), ("amendment", "amendment.md"), ("image_completion", "image-completion.json"),
            ("ledger", "ledger.json"), ("prior_v6_completion", "prior-v6-completion.json"), ("selected_metadata", "selected-metadata.json"),
            ("selected_source_archive", "selected-source.tar.gz"))}}
    h.save(output / "protocol.json", protocol)
    private = output / "private-cache-authentication-NOT-FOR-PUBLICATION"; private.mkdir(mode=0o700)
    layer = {"provenance": {"image": IMAGE, "historical_image_completion_sha256": IMAGE_COMPLETION_SHA}}
    conditions, stopped, error = [], False, None
    deadline = time.monotonic() + 1200
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(h, "IMAGE", IMAGE))
            stack.enter_context(patch.object(bench, "_PYTEST_EXECUTION_TIMEOUT_SECONDS", 120))
            stack.enter_context(patch.object(oci, "DOCKER_EXECUTION_TIMEOUT_SECONDS", 120))
            stack.enter_context(patch.dict(os.environ, {"ZERORUN_TRUST_ROOT": str(private), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}))
            compat = output / "compatibility-workspace"
            image_arm_workspace(bench, source, compat, layer, case["targets"])
            pre = h.operation(output, "compatibility-oracle", lambda: h.fresh_oracle(bench, compat, case["targets"], output / "compatibility-capture"))
            expected = pre["result"]["verdict"]
            h.require(expected["exit_code"] == 0, "fresh compatibility failed")
            for count, block in PLAN:
                row = {"consumer_requests": count, "block": block}
                try:
                    if stopped: row["disposition"] = "NOT_RUN_CORRECTNESS_STOP"; conditions.append(row); continue
                    if time.monotonic() >= deadline: row["disposition"] = "NOT_RUN_GUARD"; conditions.append(row); continue
                    block_root = output / f"n-{count}/block-{block}"; block_root.mkdir(parents=True)
                    arms = {}
                    for name in block_order(count, block):
                        h.require(time.monotonic() < deadline, "operational guard expired")
                        base = block_root / name; base.mkdir(); work = base / "workspace"
                        tick = time.perf_counter(); setup, _ = image_arm_workspace(bench, source, work, layer, case["targets"])
                        setup_ms = (time.perf_counter() - tick) * 1000; identity = h.identity(work)
                        h.save(base / "setup.json", {"setup": setup, "setup_outer_ms": setup_ms, "before": identity})
                        loaded = load_manifest(work / ".zerorun.json")
                        invoke = (lambda: bench._direct_once(work, targets=case["targets"], runtime_image=IMAGE)) if name == "fresh" else (lambda: h.product(api, loaded))
                        producer = h.operation(base, "producer", invoke)
                        consumers = []
                        for i in range(count):
                            h.require(time.monotonic() < deadline, "operational guard expired")
                            consumers.append(h.operation(base, f"consumer-{i:02d}", invoke))
                        oracle = h.operation(base, "oracle", lambda: h.fresh_oracle(bench, work, case["targets"], base / "oracle-capture"))
                        h.check_equal(oracle["result"]["verdict"] == expected, "fresh full-target oracle mismatch")
                        diagnostic = h.operation(base, "diagnostics", lambda: h.product(api, loaded, verify=True)) if name == "zerorun" else None
                        check_results(producer, consumers, diagnostic, expected, name)
                        after = h.identity(work); h.save(base / "source-after.json", after)
                        h.check_equal(identity == after, "input source changed during request chain")
                        arm = {"arm": name, "producer": producer, "consumers": consumers, "oracle": oracle,
                            "diagnostics": diagnostic, "setup_outer_ms": setup_ms, "source_sha256": identity["sha256"]}
                        h.save(base / "summary.json", arm); arms[name] = arm
                    h.check_equal(arms["fresh"]["source_sha256"] == arms["zerorun"]["source_sha256"], "paired source identity differs")
                    h.save(block_root / "summary.json", {"consumer_requests": count, "block": block, "order": block_order(count, block),
                        "arms": arms, "measurements": measurements(arms, count)})
                    row["disposition"] = "COMPLETE"
                except Exception as caught:
                    stopped = isinstance(caught, h.MaterialMismatch)
                    row.update(disposition="MATERIAL_CORRECTNESS_STOP" if stopped else "INCOMPLETE",
                               error={"type": type(caught).__name__, "message": str(caught)})
                conditions.append(row)
    except Exception as caught:
        error = {"type": type(caught).__name__, "message": str(caught)}
    finally:
        for count, block in PLAN[len(conditions):]:
            conditions.append({"consumer_requests": count, "block": block, "disposition": "INCOMPLETE", "error": error})
        after, runtime_after, final_error, sources_after = None, None, None, None
        image_helper_after, checkouts_clean = None, False
        try:
            after = oci.inspect_runtime(task, allow_pull=False, repository_root=engine)
            runtime_after = [h.record(engine / row["path"], row["path"]) for row in engine_binding["runtime_files"]]
            sources_after = h.code_inventory()
            image_helper_after = h.record(h.HERE.parent / "handoff_image_v2/run.py", "run.py")
            checkouts_clean = all(not h.git(path, ["status", "--porcelain=v1", "--untracked-files=all"])["stdout"] for path in (engine, harness))
        except Exception as caught:
            final_error = {"type": type(caught).__name__, "message": str(caught)}
        h.save(output / "completion.json", {"schema": "zerorun.exploratory-amortization-completion.v1",
            "completed_utc": h.utc(), "protocol_sha256": h.sha(h.ordinary(output / "protocol.json")),
            "conditions": conditions, "campaign_error": error or final_error, "material_correctness_stop": stopped,
            "runtime_attestation_after": after, "sources_unchanged": sources == sources_after,
            "image_helper_unchanged": image_helper == image_helper_after, "public_checkouts_clean_after": checkouts_clean,
            "runtime_source_unchanged": runtime_after == engine_binding["runtime_files"]})
        export(output)
    return validate(output)


def export(output):
    bundle = output / "record-only"; bundle.mkdir()
    for current, dirs, files in os.walk(output, followlinks=False):
        dirs[:] = [name for name in dirs if name not in EXCLUDED]
        for name in files:
            path = Path(current) / name
            h.require(not path.is_symlink(), "linked record export")
            dest = bundle / path.relative_to(output); dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(h.ordinary(path))
    h.save(bundle / "RECORD_MANIFEST.json", {"files": [h.record(p, p.relative_to(bundle).as_posix())
        for p in sorted(bundle.rglob("*")) if p.is_file()]})
    with tarfile.open(output / "record-only.tar.gz", "x:gz") as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_file(): archive.add(path, arcname="record-only/" + path.relative_to(bundle).as_posix(), recursive=False)
    h.save(output / "transfer.json", h.record(output / "record-only.tar.gz"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=Path)
    for name in ("engine", "acquisition", "v6-records", "image-completion", "protocol", "output"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.check:
        result = validate(args.check)
    else:
        h.require(all(getattr(args, name.replace("-", "_")) for name in ("engine", "acquisition", "v6-records", "image-completion", "protocol", "output")), "all frozen input paths required")
        newly_created_output = not args.output.exists()
        try:
            result = execute(args.acquisition / "main.json", args.engine, args.v6_records, args.image_completion, args.protocol, args.output)
        except Exception as caught:
            # Preserve preparation failures before the main protocol was
            # written, including every captured Git/OCI/source record.
            if newly_created_output and args.output.is_dir() and not (args.output / "record-only").exists():
                h.save(args.output / "preflight-failure.json", {"schema": "zerorun.exploratory-amortization-preflight-failure.v1",
                    "completed_utc": h.utc(), "error": {"type": type(caught).__name__, "message": str(caught)},
                    "conditions": [{"consumer_requests": n, "block": b, "disposition": "INCOMPLETE"} for n, b in PLAN]})
                export(args.output)
            raise
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    raise SystemExit(0 if result["all_six_blocks_complete"] and not result["material_correctness_stop"] else 2)


if __name__ == "__main__":
    main()
