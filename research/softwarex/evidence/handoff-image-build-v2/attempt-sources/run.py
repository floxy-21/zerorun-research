"""Explicit research adapter for image-contained dependencies; no OCI bypass."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

from research.softwarex.handoff_v1 import run as h
from . import build_image as b


def image_arm_workspace(bench, source, destination, layer, targets):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
    h.git(destination, ["init", "--template=", "--initial-branch=main", "."])
    h.require(not (destination / ".zerorun-env").exists(), "unexpected mutable dependency directory")
    manifest = h.strict(bench._frozen_pytest_manifest_bytes(runtime_image=h.IMAGE))
    task = manifest["tasks"].pop("pytest-generalization")
    task.update(command=["/usr/local/bin/python", "-m", "pytest", "-p", "no:cacheprovider", *targets],
                cacheable=True, inputs=sorted(p.name for p in destination.iterdir() if p.name not in h.STATE))
    manifest["tasks"] = {"handoff-tests": task}
    (destination / ".zerorun.json").write_bytes(h.encoded(manifest) + b"\n")
    return {"deployment": "image-contained locked dependencies", "runtime_image": h.IMAGE,
            "dependency_provenance": layer["provenance"], "mutable_dependency_directory_materialized": False}, manifest


def run(ledger, engine, image_build, output, execution_seconds=120, budget_seconds=7200):
    from .validate import validate_image
    h.require(os.name == "posix", "Linux laboratory required")
    engine, image_build = Path(engine).resolve(strict=True), Path(image_build).resolve(strict=True)
    output = Path(output).resolve(strict=False)
    h.require(not output.exists() and output.parent.exists() and not output.is_relative_to(engine)
              and not output.is_relative_to(image_build), "new external V2 output directory required")
    image_summary = validate_image(image_build)
    components = h.load_engine(engine)
    bench, _, oci, load_manifest, engine_binding = components
    output.mkdir()
    # This constructs only a trusted metadata manifest: no repository code runs.
    inspection_root = output / "runtime-inspection"
    inspection_root.mkdir()
    raw = bench._frozen_pytest_manifest_bytes(runtime_image=image_summary["image"]["requested"])
    (inspection_root / ".zerorun.json").write_bytes(raw)
    task = load_manifest(inspection_root / ".zerorun.json").tasks["pytest-generalization"]
    attestation = oci.inspect_runtime(task, allow_pull=False, repository_root=engine)
    h.require(attestation["image_id"] == image_summary["image"]["image_id"]
              and attestation["config_sha256"] == image_summary["image"]["config_sha256"], "runtime image binding changed")
    sources, original_sources = b.source_inventory(), h.code_inventory()
    before = [h.record(engine / r["path"], r["path"]) for r in engine_binding["runtime_files"]]
    h.validate_runtime_rows(before)
    protocol = {"schema": "zerorun.image-contained-handoff-run.v2", "started_utc": h.utc(),
        "image_build_completion": h.record(image_build / "completion.json", "completion.json"),
        "image_build_protocol": h.record(image_build / "protocol.json", "protocol.json"),
        "image_reconciliation": image_summary, "runtime_attestation_before": attestation,
        "sources": sources, "v1_sources": original_sources, "runtime_files_before": before,
        "adapter_substitutions": ["v1.IMAGE: genuine derived RepoDigest", "v1.arm_workspace: image-contained dependency source preparation",
            "v1.load_engine: previously hash-validated exact same components", "benchmark._frozen_dependency_layer: previously built image provenance, no environment materialization"],
        "unchanged_components": "ZeroRun runtime bytes, runtime attestation, eligibility, source fingerprint, all v1 cases/operations/oracles/accounting",
        "common_image_setup_ms": image_summary["setup_outer_ms"], "image_setup_in_chain_ratio": False,
        "natural_hit_frequency_study": False, "model_calls": 0, "mcp_authorities_created": False}
    h.save(output / "protocol.json", protocol)
    error, inner = None, None

    @contextmanager
    def image_layer(root, *, runtime_image, extra_requirements):
        h.require(runtime_image == image_summary["image"]["requested"] and tuple(extra_requirements) == ("pretend",), "unexpected image adapter request")
        yield {"provenance": {"schema": "zerorun.image-contained-dependency-deployment.v2",
                "image": image_summary["image"], "original_locked_layer": image_summary["dependency_provenance"],
                "preparation_precedes_run": True, "build_once_ms": image_summary["setup_outer_ms"]}}
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(h, "IMAGE", image_summary["image"]["requested"]))
            stack.enter_context(patch.object(h, "arm_workspace", image_arm_workspace))
            stack.enter_context(patch.object(h, "load_engine", lambda selected: components if selected == engine else (_ for _ in ()).throw(ValueError("different engine"))))
            stack.enter_context(patch.object(bench, "_frozen_dependency_layer", image_layer))
            inner = h.run(Path(ledger), engine, output / "run", execution_seconds, budget_seconds)
    except Exception as caught:
        error = {"type": type(caught).__name__, "message": str(caught)}
    after = [h.record(engine / r["path"], r["path"]) for r in before]
    after_attestation = oci.inspect_runtime(task, allow_pull=False, repository_root=engine)
    result = {"schema": "zerorun.image-contained-handoff-completion.v2", "completed_utc": h.utc(),
        "protocol_sha256": h.sha(h.ordinary(output / "protocol.json")), "error": error,
        "inner_campaign_error": inner["campaign_error"] if inner is not None else None,
        "inner_material_correctness_stop": inner["material_correctness_stop"] if inner is not None else None,
        "inner_completion": h.record(output / "run/completion.json", "run/completion.json") if (output / "run/completion.json").is_file() else None,
        "sources_after": b.source_inventory(), "v1_sources_after": h.code_inventory(), "runtime_files_after": after,
        "runtime_attestation_after": after_attestation,
        "source_unchanged": sources == b.source_inventory() and original_sources == h.code_inventory() and before == after,
        "runtime_image_unchanged": h.encoded(attestation) == h.encoded(after_attestation),
        "new_model_calls": 0, "mcp_authorities_created": False}
    h.save(output / "completion.json", result)
    h.require(result["source_unchanged"] and result["runtime_image_unchanged"], "source or image changed")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("ledger", "engine", "image-build", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--execution-seconds", type=int, default=120)
    p.add_argument("--budget-seconds", type=int, default=7200)
    p.add_argument("--execute-reviewed-lab", action="store_true", required=True)
    args = p.parse_args()
    result = run(args.ledger, args.engine, args.image_build, args.output, args.execution_seconds, args.budget_seconds)
    print(json.dumps(result, sort_keys=True))
    return int(result["error"] is not None or result["inner_campaign_error"] is not None
               or result["inner_material_correctness_stop"] is True)


if __name__ == "__main__":
    raise SystemExit(main())
