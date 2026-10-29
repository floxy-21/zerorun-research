"""Reuse the retained offline wheel recipe and genuine loopback attestation."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
import urllib.request
import uuid
import email
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from research.softwarex.handoff_image_v2 import build_image as b

BASE = "python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a"
REGISTRY = "docker.io/library/registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278"
ASYNCIO_URL = "https://files.pythonhosted.org/packages/30/05/ce271016e351fddc8399e546f6e23761967ee09c8c568bbfbecb0c150171/pytest_asyncio-1.0.0-py3-none-any.whl"
ASYNCIO_SHA = "4f024da9f1ef945e680dc68610b52550e36590a67fd31bb3b4943979a1f90ef3"


def prepare(output, *, main=False):
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    commands = output / "commands"
    commands.mkdir()
    context = output / "context"
    retained = ROOT / "research/softwarex/evidence/compatible-image-repair-v2/context"
    shutil.copytree(retained, context)
    raw = urllib.request.urlopen(ASYNCIO_URL, timeout=60).read(1024 * 1024)
    assert hashlib.sha256(raw).hexdigest() == ASYNCIO_SHA
    (context / "wheelhouse" / ASYNCIO_URL.rsplit("/", 1)[1]).write_bytes(raw)
    lock = context / "requirements.lock"
    lock.write_text(lock.read_text() + "pytest-asyncio==1.0.0 --hash=sha256:" + ASYNCIO_SHA + "\n")
    shutil.copy2(Path(__file__).with_name("q1q2_capture.py"), context / "q1q2_capture.py")
    dockerfile = (context / "Dockerfile").read_text().replace(
        "ENV PYTHONPATH=/workspace/src:/workspace", "COPY q1q2_capture.py /opt/q1q2/q1q2_capture.py\nENV PYTHONPATH=/workspace/src:/workspace:/opt/q1q2")
    (context / "Dockerfile").write_text(dockerfile)
    command = lambda label, argv, timeout=600: b.command(commands, label, argv, timeout)
    cid = None
    result = {"passed": False, "source": "retained compatible-image-repair-v2 plus hash-locked pytest-asyncio and outcome capture"}
    try:
        command("base-pull", ["docker", "pull", "--platform", "linux/amd64", BASE])
        command("registry-pull", ["docker", "pull", REGISTRY])
        if main:
            main_started = time.perf_counter()
            rows = json.loads((Path(__file__).parent / "runtime-main/wheels.json").read_text())
            for row in rows:
                data = urllib.request.urlopen(row["url"], timeout=60).read(row["bytes"] + 1)
                assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
                (context / "wheelhouse" / row["filename"]).write_bytes(data)
            requirements = {}
            for wheel in sorted((context / "wheelhouse").glob("*.whl")):
                with zipfile.ZipFile(wheel) as archive:
                    metadata_path, = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
                    metadata = email.message_from_bytes(archive.read(metadata_path))
                name = metadata["Name"].lower().replace("_", "-")
                value = metadata["Name"] + "==" + metadata["Version"] + " --hash=sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
                assert name not in requirements or requirements[name] == value
                requirements[name] = value
            lock.write_text("\n".join(requirements[k] for k in sorted(requirements)) + "\n")
            result["main_wheel_acquisition_seconds"] = time.perf_counter() - main_started
            # Upstream natsort requires en_US; retain all three upstream locales.
            # Download package bytes first, then use an offline image build.
            (context / "debs").mkdir()
            script = ("set -eu; mkdir -p /tmp/q1q2-apt-cache/partial; "
                "apt-get -o APT::Sandbox::User=root update; "
                "apt-get -o APT::Sandbox::User=root -o Dir::Cache::archives=/tmp/q1q2-apt-cache --download-only -y install locales; "
                "cp /tmp/q1q2-apt-cache/*.deb /out/debs/")
            command("locale-acquisition", ["docker", "run", "--rm", "--pull=never", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--mount", "type=bind,src=" + str(context) + ",dst=/out",
                BASE, "sh", "-c", script])
            deb_bytes = sum(p.stat().st_size for p in (context / "debs").glob("*.deb"))
            assert deb_bytes <= 50_000_000, "locale package acquisition exceeds 50 MB bound"
            dockerfile += ("COPY debs/ /opt/q1q2-debs/\n"
                "RUN DEBIAN_FRONTEND=noninteractive dpkg -i /opt/q1q2-debs/*.deb && "
                "localedef -i en_US -f UTF-8 en_US.UTF-8 && localedef -i de_DE -f UTF-8 de_DE.UTF-8 && localedef -i cs_CZ -f UTF-8 cs_CZ.UTF-8\n"
                "ENV HOME=/tmp HYPOTHESIS_STORAGE_DIRECTORY=/tmp/hypothesis\n")
            (context / "Dockerfile").write_text(dockerfile)
            result["main_runtime"] = True
            result["locale_deb_bytes"] = deb_bytes
        token = uuid.uuid4().hex
        tag = "127.0.0.1:19559/zerorun-q1q2:" + token
        command("build", ["docker", "build", "--no-cache", "--pull=false", "--network=none", "--tag", tag, str(context)])
        store = output / "registry-store"
        store.mkdir()
        cid = command("registry-create", ["docker", "create", "--name", "q1q2-registry-" + token,
            "--pull=never", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}", "--pids-limit", "128", "--memory", "256m",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", "--publish", "127.0.0.1:19559:5000",
            "--mount", "type=bind,src=" + str(store) + ",dst=/var/lib/registry", REGISTRY]).decode().strip()
        assert re.fullmatch("[a-f0-9]{64}", cid)
        command("registry-start", ["docker", "start", cid])
        assert b.wait_registry_ready("http://127.0.0.1:19559/v2/")
        pushed = command("push", ["docker", "push", tag])
        digests = set(re.findall(rb"digest: (sha256:[a-f0-9]{64})", pushed))
        assert len(digests) == 1
        image = tag.rsplit(":", 1)[0] + "@" + digests.pop().decode()
        command("pull-digest", ["docker", "pull", "--platform", "linux/amd64", image])
        meta = b.image_metadata(command("inspect", ["docker", "image", "inspect", image]), image)
        probe = "import json,sys,pytest,pytest_asyncio,q1q2_capture;print(json.dumps({'python':sys.version,'pytest':pytest.__version__,'pytest_asyncio':pytest_asyncio.__version__}))"
        runtime_probe = json.loads(command("probe", ["docker", "run", "--rm", "--network=none", "--read-only", tag, "python", "-B", "-c", probe]))
        command("pip-check", ["docker", "run", "--rm", "--network=none", "--read-only", tag, "python", "-B", "-m", "pip", "check"])
        result.update(passed=True, image=meta, context_inventory=b.inventory(context), runtime_probe=runtime_probe)
    except Exception as error:
        result["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        if cid is not None:
            command("registry-remove", ["docker", "container", "rm", "--force", cid])
        result["outer_seconds"] = time.perf_counter() - started
        (output / "completion.json").write_text(json.dumps(result, indent=2))
    return result["image"]["requested"]


if __name__ == "__main__":
    print(prepare(Path(sys.argv[1]).resolve()))
