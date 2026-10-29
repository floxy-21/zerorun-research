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

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from research.softwarex.handoff_image_v2 import build_image as b

BASE = "python@sha256:68d914ec641a0b69267ce65184d000a2bc3a9ee2590ab702b82250ab2385735a"
REGISTRY = "docker.io/library/registry@sha256:46faa9a1ae6813194b53921a370f2f4f8c5e1aae228a89bceafef5847a6a3278"
ASYNCIO_URL = "https://files.pythonhosted.org/packages/30/05/ce271016e351fddc8399e546f6e23761967ee09c8c568bbfbecb0c150171/pytest_asyncio-1.0.0-py3-none-any.whl"
ASYNCIO_SHA = "4f024da9f1ef945e680dc68610b52550e36590a67fd31bb3b4943979a1f90ef3"


def prepare(output):
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
