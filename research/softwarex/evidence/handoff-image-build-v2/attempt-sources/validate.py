"""Read-only image provenance and unchanged V1 operation reconciliation."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re

from research.softwarex.handoff_v1 import run as h
from research.softwarex.handoff_v1 import validate as v
from . import build_image as b


def source_rows(actual, folder):
    h.require(isinstance(actual, list) and actual and len({r["path"] for r in actual}) == len(actual), "source rows absent/duplicate")
    expected = {p.name for p in folder.iterdir() if p.is_file() and p.suffix in {".py", ".md"}}
    h.require({r["path"] for r in actual} == expected, "source inventory omits or adds frozen files")
    for row in actual:
        h.bound(folder, row)


def command(directory, name):
    start, row = v.read(directory / (name + ".started.json")), v.read(directory / (name + ".json"))
    v.exact(start["argv"], row["argv"], "command argv differs")
    h.require(type(row["returncode"]) is int and row["returncode"] == 0 and row["error"] is None
              and row["stdout_truncated"] is False and row["stderr_truncated"] is False, "command was failed or truncated")
    h.require(type(row["outer_ms"]) in (float, int) and math.isfinite(row["outer_ms"]) and row["outer_ms"] >= 0, "command time invalid")
    return row


def validate_image(directory):
    directory = Path(directory)
    p, c = v.read(directory / "protocol.json"), v.read(directory / "completion.json")
    h.require(p["schema"] == "zerorun.handoff-image-build.v2" and c["schema"] == "zerorun.handoff-image-build-completion.v2", "wrong image receipt")
    h.require(c["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")), "image protocol changed")
    h.require(p["base_image"] == b.BASE_IMAGE and p["listen_address"] == "127.0.0.1"
              and p["build_network"] == "none" and p["daemon_configuration_modified"] is False
              and p["paid_services"] is False and p["dockerfile_sha256"] == h.sha(b.DOCKERFILE), "image preparation scope changed")
    source_rows(p["sources"], b.HERE)
    v.exact(p["sources"], c["sources_after"], "image builder sources changed")
    h.validate_runtime_rows(p["engine_binding"]["runtime_files"])
    h.require(c["passed"] is True and c["error"] is None and c["source_unchanged"] is True
              and c["registry_container_removed"] is True and c["image_publicly_pullable"] is False
              and c["bit_identical_recipe_rebuild_claimed"] is False, "image completion failed/claim changed")
    cmds = directory / "commands"
    rows = {name: command(cmds, name) for name in ("base-inspect", "registry-inspect", "build", "registry-create", "registry-start",
        "registry-container-inspect", "push", "pull-digest", "derived-inspect", "import-probe", "image-dependency-files", "registry-remove")}
    b.image_metadata(rows["base-inspect"]["stdout"].encode(), p["base_image"])
    registry = b.image_metadata(rows["registry-inspect"]["stdout"].encode(), p["registry_image"])
    image = b.image_metadata(rows["derived-inspect"]["stdout"].encode(), c["image"]["requested"])
    v.exact(image, c["image"], "derived image aggregation changed")
    h.require(re.fullmatch(r"127\.0\.0\.1:" + str(p["port"]) + r"/zerorun-handoff-v2@sha256:[0-9a-f]{64}", image["requested"]) is not None, "image repository not isolated loopback")
    digest = image["requested"].rsplit("@", 1)[1]
    h.require(set(re.findall(r"digest: (sha256:[0-9a-f]{64})", rows["push"]["stdout"])) == {digest}, "push/inspect digest differ")
    h.require(rows["pull-digest"]["argv"][-1] == image["requested"], "exact image digest not pulled")
    container = h.strict(rows["registry-container-inspect"]["stdout"].encode())
    h.require(len(container) == 1 and container[0]["Image"] == registry["image_id"]
              and container[0]["Id"] == c["registry_container_id"] == rows["registry-create"]["stdout"].strip()
              and container[0]["State"]["Running"] is True
              and container[0]["HostConfig"]["PortBindings"] == {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(p["port"])}]}
              and rows["registry-remove"]["argv"][-1] == c["registry_container_id"], "registry identity/binding/cleanup disagrees")
    argv = rows["build"]["argv"]
    h.require("--network=none" in argv and "--pull=false" in argv, "Docker build network/acquisition scope changed")
    dependencies = v.read(directory / "dependency-files.json")
    v.exact(dependencies["provenance"], c["dependency_provenance"], "locked dependency provenance changed")
    v.exact(h.strict(rows["image-dependency-files"]["stdout"].encode()), dependencies["source_site_files"], "image dependency bytes differ from frozen input")
    context = v.read(directory / "context-inventory.json")
    h.require(context["sha256"] == h.sha(h.encoded(context["files"])), "context inventory hash changed")
    source_site = [{"path": r["path"].removeprefix("site-packages/"), "bytes": r["bytes"], "sha256": r["sha256"]}
                   for r in context["files"] if r["path"].startswith("site-packages/")]
    v.exact(source_site, dependencies["source_site_files"], "Docker context dependency bytes changed")
    control = {r["path"]: r for r in context["files"] if not r["path"].startswith("site-packages/")}
    v.exact(control.get("Dockerfile"), {"path": "Dockerfile", "bytes": len(b.DOCKERFILE), "sha256": h.sha(b.DOCKERFILE)}, "Dockerfile context differs")
    h.require(set(control) == {"Dockerfile", "runtime-requirements.txt"}
              and control["runtime-requirements.txt"]["sha256"] == dependencies["provenance"]["runtime_requirements_sha256"], "unexpected context or dependency lock")
    probe = h.strict(rows["import-probe"]["stdout"].encode())
    h.require(probe["python"].startswith("3.12.14 ") and probe["pytest_file"].startswith("/usr/local/lib/python3.12/site-packages/"), "wrong imported runtime/location")
    h.require(type(c["setup_outer_ms"]) in (int, float) and math.isfinite(c["setup_outer_ms"]) and c["setup_outer_ms"] > 0, "image setup timing invalid")
    return {"schema": "zerorun.handoff-image-reconciliation.v2", "reconciled": True,
        "image": image, "dependency_provenance": dependencies["provenance"], "setup_outer_ms": c["setup_outer_ms"],
        "completion_sha256": h.sha(h.ordinary(directory / "completion.json")), "image_publicly_pullable": False,
        "independent_image_rebuild": False, "dependency_files": len(source_site)}


def validate_saved(directory, image_build, acquisition_base=None):
    directory, image_build = Path(directory), Path(image_build)
    image = validate_image(image_build)
    p, c = v.read(directory / "protocol.json"), v.read(directory / "completion.json")
    h.require(p["schema"] == "zerorun.image-contained-handoff-run.v2" and c["schema"] == "zerorun.image-contained-handoff-completion.v2", "wrong V2 receipt")
    h.require(c["protocol_sha256"] == h.sha(h.ordinary(directory / "protocol.json")), "V2 protocol changed")
    h.bound(image_build, p["image_build_completion"])
    h.bound(image_build, p["image_build_protocol"])
    v.exact(p["image_reconciliation"], image, "image reconciliation changed")
    source_rows(p["sources"], b.HERE)
    source_rows(p["v1_sources"], h.HERE)
    for prefix in ("sources", "v1_sources", "runtime_files"):
        v.exact(p[prefix + ("_before" if prefix == "runtime_files" else "")], c[prefix + "_after"], "source/runtime inventory changed")
    h.validate_runtime_rows(p["runtime_files_before"])
    v.exact(p["runtime_attestation_before"], c["runtime_attestation_after"], "runtime attestation changed")
    h.require(p["runtime_attestation_before"]["requested_image"] == image["image"]["requested"]
              and p["runtime_attestation_before"]["image_id"] == image["image"]["image_id"]
              and p["runtime_attestation_before"]["config_sha256"] == image["image"]["config_sha256"], "wrong execution image")
    v.exact(p["runtime_attestation_before"]["rootfs"], {"type": image["image"]["rootfs"]["Type"], "layers": image["image"]["rootfs"]["Layers"]}, "execution layers differ")
    h.require(c["error"] is None and c["source_unchanged"] is True and c["runtime_image_unchanged"] is True
              and p["model_calls"] == c["new_model_calls"] == 0
              and p["mcp_authorities_created"] is c["mcp_authorities_created"] is False, "V2 execution integrity/scope failed")
    h.bound(directory, c["inner_completion"])
    inner = v.validate_saved(directory / "run", acquisition_base)
    ic = v.read(directory / "run/completion.json")
    v.exact(c["inner_campaign_error"], ic["campaign_error"], "inner campaign failure hidden")
    v.exact(c["inner_material_correctness_stop"], ic["material_correctness_stop"], "inner correctness stop hidden")
    ip = v.read(directory / "run/protocol.json")
    h.require(ip["runtime_image"] == image["image"]["requested"], "inner image differs")
    v.exact(ip["study_sources"], p["v1_sources"], "inner harness source differs")
    return {"schema": "zerorun.image-contained-handoff-reconciliation.v2", "reconciled": True,
        "image_build": image, "controlled_handoffs": inner, "runtime_before_after_exact": True,
        "new_model_calls": 0, "natural_repeat_frequency": False, "image_setup_in_chain_ratio": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--image-build", type=Path)
    p.add_argument("--acquisition-base", type=Path)
    args = p.parse_args()
    value = validate_saved(args.directory, args.image_build, args.acquisition_base) if args.image_build else validate_image(args.directory)
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
