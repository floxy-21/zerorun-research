"""Prepare a genuine digest-attested dependency image via loopback registry."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from research.softwarex.handoff_v1 import run as h

HERE = Path(__file__).resolve().parent
BASE_IMAGE = h.IMAGE
DOCKERFILE = ("FROM " + BASE_IMAGE + "\n"
    "ARG SOURCE_DATE_EPOCH=0\n"
    "COPY site-packages/ /usr/local/lib/python3.12/site-packages/\n"
    "COPY runtime-requirements.txt /opt/zerorun-runtime-requirements.txt\n"
    "ENV PYTHONPATH=/workspace/src:/workspace\n"
    "LABEL org.opencontainers.image.title=ZeroRun-controlled-handoff-dependencies\n").encode()


def inventory(directory):
    rows = []
    for path in sorted(directory.rglob("*"), key=lambda p: p.relative_to(directory).as_posix()):
        h.require(not path.is_symlink(), "dependency/image source link refused")
        if path.is_file():
            rows.append(h.record(path, path.relative_to(directory).as_posix()))
    return rows


def mode_inventory(directory):
    return [{"path": p.relative_to(directory).as_posix(), "mode": stat.S_IMODE(p.stat().st_mode),
             "kind": "directory" if p.is_dir() else "file"}
            for p in sorted(directory.rglob("*"), key=lambda p: p.relative_to(directory).as_posix())]


def source_inventory():
    return [h.record(p, p.name) for p in sorted(HERE.iterdir(), key=lambda p: p.name) if p.suffix in {".py", ".md"}]


def command(output, label, argv, timeout=300, stdin=None):
    h.save(output / (label + ".started.json"), {"started_utc": h.utc(), "argv": argv})
    started = time.perf_counter()
    row = {"argv": argv, "timeout_seconds": timeout, "returncode": None, "error": None}
    try:
        result = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
        row.update(returncode=result.returncode, stdout=result.stdout[-4 * 1024 * 1024:].decode(errors="replace"),
                   stderr=result.stderr[-4 * 1024 * 1024:].decode(errors="replace"),
                   stdout_truncated=len(result.stdout) > 4 * 1024 * 1024,
                   stderr_truncated=len(result.stderr) > 4 * 1024 * 1024)
        h.require(result.returncode == 0, label + " failed: " + row["stderr"][-2000:])
        return result.stdout
    except Exception as error:
        row["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        row["outer_ms"] = (time.perf_counter() - started) * 1000
        row["completed_utc"] = h.utc()
        h.save(output / (label + ".json"), row)


def image_metadata(raw, requested):
    rows = h.strict(raw)
    h.require(isinstance(rows, list) and len(rows) == 1, "one Docker image record required")
    image = rows[0]
    h.require(image.get("Os") == "linux" and image.get("Architecture") == "amd64", "wrong image platform")
    digest = requested.rsplit("@", 1)[-1]
    h.require("@" in requested and any(r.endswith("@" + digest) for r in image.get("RepoDigests", [])), "image RepoDigests do not attest requested digest")
    h.require(re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("Id", "")) is not None, "image ID absent")
    h.require(isinstance(image.get("Config"), dict) and isinstance(image.get("RootFS"), dict)
              and isinstance(image["RootFS"].get("Layers"), list), "image configuration/layers absent")
    return {"requested": requested, "image_id": image["Id"], "repo_digests": sorted(image["RepoDigests"]),
            "rootfs": image.get("RootFS"), "config_sha256": h.sha(h.encoded(image.get("Config")))}


def wait_registry_ready(url, timeout_seconds=15):
    """Bounded startup transport polling; never retry experiment outcomes."""
    started = time.monotonic()
    while time.monotonic() - started < timeout_seconds:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionResetError):
            pass
        time.sleep(.2)
    return False


def build(engine, output, registry_image, port=19509):
    h.require(os.name == "posix", "image preparation requires Linux")
    h.require(re.fullmatch(r"(?:docker.io/)?library/registry@sha256:[0-9a-f]{64}", registry_image) is not None,
              "an already-acquired digest-pinned official registry image is required")
    h.require(type(port) is int and 1024 <= port <= 65535, "unprivileged explicit loopback port required")
    engine, output = Path(engine).resolve(strict=True), Path(output).resolve(strict=False)
    h.require(not output.exists() and output.parent.exists() and not output.is_relative_to(engine), "new external image-build directory required")
    output.mkdir()
    bench, _, _, _, engine_binding = h.load_engine(engine)
    before = source_inventory()
    h.save(output / "protocol.json", {"schema": "zerorun.handoff-image-build.v2", "started_utc": h.utc(),
        "base_image": BASE_IMAGE, "registry_image": registry_image, "listen_address": "127.0.0.1", "port": port,
        "engine_binding": engine_binding, "sources": before, "dockerfile_sha256": h.sha(DOCKERFILE),
        "build_network": "none", "daemon_configuration_modified": False, "paid_services": False,
        "registry_image_acquisition": "separately recorded by operator before this invocation"})
    docker = shutil.which("docker")
    h.require(docker is not None, "Docker unavailable")
    commands = output / "commands"
    commands.mkdir()
    registry_id, error, identity, layer_record = None, None, None, None
    started = time.perf_counter()
    try:
        base = image_metadata(command(commands, "base-inspect", [docker, "image", "inspect", BASE_IMAGE]), BASE_IMAGE)
        registry = image_metadata(command(commands, "registry-inspect", [docker, "image", "inspect", registry_image]), registry_image)
        context = output / "context"
        context.mkdir()
        starter = output / "dependency-starter"
        starter.mkdir()
        with bench._frozen_dependency_layer(starter, runtime_image=BASE_IMAGE, extra_requirements=("pretend",)) as layer:
            layer_record = dict(layer["provenance"])
            source_site = Path(layer["root"]) / "site-packages"
            source_rows = inventory(source_site)
            shutil.copytree(source_site, context / "site-packages")
            h.require(inventory(context / "site-packages") == source_rows, "copied image dependency bytes differ")
            (context / "runtime-requirements.txt").write_bytes(h.ordinary(Path(layer["root"]) / "runtime-requirements.txt"))
            (context / "Dockerfile").write_bytes(DOCKERFILE)
            h.save(output / "dependency-files.json", {"source_site_files": source_rows,
                "source_site_modes": mode_inventory(source_site), "frozen_layer_snapshot": layer["snapshot"], "provenance": layer_record})
        context_rows = inventory(context)
        h.save(output / "context-inventory.json", {"files": context_rows, "sha256": h.sha(h.encoded(context_rows))})
        token = uuid.uuid4().hex
        tag = "127.0.0.1:" + str(port) + "/zerorun-handoff-v2:" + token
        command(commands, "build", [docker, "build", "--pull=false", "--network=none", "--build-arg", "SOURCE_DATE_EPOCH=0", "--tag", tag, str(context)], timeout=600)
        store = output / "registry-store"
        store.mkdir()
        name = "zerorun-handoff-registry-" + token
        created = command(commands, "registry-create", [docker, "create", "--name", name,
            "--pull=never", "--platform", "linux/amd64", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--user", str(os.getuid()) + ":" + str(os.getgid()),
            "--pids-limit", "128", "--memory", "256m", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m",
            "--publish", "127.0.0.1:" + str(port) + ":5000",
            "--mount", "type=bind,src=" + str(store) + ",dst=/var/lib/registry", registry_image])
        candidate_id = created.decode().strip()
        h.require(re.fullmatch(r"[0-9a-f]{64}", candidate_id) is not None, "registry container ID malformed")
        registry_id = candidate_id
        command(commands, "registry-start", [docker, "start", registry_id])
        state = h.strict(command(commands, "registry-container-inspect", [docker, "container", "inspect", registry_id]))
        h.require(len(state) == 1 and state[0]["Id"] == registry_id and state[0]["State"]["Running"] is True
                  and state[0]["Image"] == registry["image_id"]
                  and state[0]["HostConfig"]["PortBindings"] == {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]},
                  "registry identity/state/loopback binding differs")
        ready = wait_registry_ready("http://127.0.0.1:" + str(port) + "/v2/")
        h.require(ready, "isolated loopback registry did not become ready")
        push = command(commands, "push", [docker, "push", tag], timeout=300)
        matches = re.findall(rb"digest: (sha256:[0-9a-f]{64})", push)
        h.require(len(set(matches)) == 1, "registry push manifest digest unavailable/ambiguous")
        reference = tag.rsplit(":", 1)[0] + "@" + matches[0].decode()
        command(commands, "pull-digest", [docker, "pull", "--platform", "linux/amd64", reference], timeout=300)
        identity = image_metadata(command(commands, "derived-inspect", [docker, "image", "inspect", reference]), reference)
        h.require(inventory(context) == context_rows, "Docker context changed")
        # No source checkout is mounted: this only proves the imported package
        # version/path inside the derived image, not issue-level compatibility.
        probe = "import json,pytest,sys;print(json.dumps({'python':sys.version,'pytest':pytest.__version__,'pytest_file':pytest.__file__}))"
        probe_raw = command(commands, "import-probe", [docker, "run", "--rm", "--pull=never", "--network=none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", reference, "python", "-B", "-c", probe], timeout=60)
        probe_result = h.strict(probe_raw)
        h.require(probe_result["python"].startswith("3.12.14 ") and probe_result["pytest_file"].startswith("/usr/local/lib/python3.12/site-packages/"), "wrong image package interpreter/location")
        check = ("import hashlib,json,pathlib,sys;root=pathlib.Path('/usr/local/lib/python3.12/site-packages');"
                 "rows=json.load(sys.stdin);out=[];"
                 "exec(\"for row in rows:\\n p=root/row['path']; raw=p.read_bytes(); out.append({'path':row['path'],'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})\");"
                 "print(json.dumps(out,sort_keys=True,separators=(',',':')))")
        copied_raw = command(commands, "image-dependency-files", [docker, "run", "--rm", "-i", "--pull=never", "--network=none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", reference, "python", "-B", "-c", check], timeout=60, stdin=h.encoded(source_rows))
        h.require(h.encoded(h.strict(copied_raw)) == h.encoded(source_rows), "image does not contain exact locked dependency files")
    except Exception as caught:
        error = {"type": type(caught).__name__, "message": str(caught)}
    finally:
        cleanup = False
        if registry_id is not None:
            try:
                command(commands, "registry-remove", [docker, "container", "rm", "--force", registry_id], timeout=30)
                cleanup = True
            except Exception as caught:
                error = error or {"type": type(caught).__name__, "message": str(caught)}
        else:
            cleanup = True
        result = {"schema": "zerorun.handoff-image-build-completion.v2", "completed_utc": h.utc(),
            "protocol_sha256": h.sha(h.ordinary(output / "protocol.json")), "passed": error is None and identity is not None and cleanup,
            "error": error, "image": identity, "dependency_provenance": layer_record,
            "registry_container_id": registry_id, "registry_container_removed": cleanup,
            "registry_store_retained_locally": registry_id is not None, "image_publicly_pullable": False,
            "setup_outer_ms": (time.perf_counter() - started) * 1000, "sources_after": source_inventory(),
            "source_unchanged": before == source_inventory(), "bit_identical_recipe_rebuild_claimed": False}
        h.save(output / "completion.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--registry-image", required=True)
    parser.add_argument("--port", type=int, default=19509)
    parser.add_argument("--approve-loopback-registry", action="store_true", required=True)
    args = parser.parse_args()
    result = build(args.engine, args.output, args.registry_image, args.port)
    print(json.dumps(result, sort_keys=True))
    return int(not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
