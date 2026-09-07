"""Offline image adapter tests: no Docker, package acquisition or models."""
import copy
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.softwarex.handoff_v1 import run as h
from . import build_image as b
from . import run as runner
from . import validate as v


def metadata():
    return [{"Os": "linux", "Architecture": "amd64", "Id": "sha256:" + "b" * 64,
        "RepoDigests": ["registry/image@sha256:" + "a" * 64], "RootFS": {"Type": "layers", "Layers": ["sha256:" + "c" * 64]},
        "Config": {"Env": ["PYTHONPATH=/workspace/src:/workspace"]}}]


def test_metadata_attests_real_digest():
    result = b.image_metadata(h.encoded(metadata()), "registry/image@sha256:" + "a" * 64)
    assert result["image_id"] == "sha256:" + "b" * 64
    assert result["config_sha256"] == h.sha(h.encoded(metadata()[0]["Config"]))


@pytest.mark.parametrize("key,value", [("Os", "windows"), ("Architecture", "arm64"), ("Id", "latest"),
    ("RepoDigests", []), ("Config", None), ("RootFS", {})])
def test_wrong_image_rejected(key, value):
    raw = metadata()
    raw[0][key] = value
    with pytest.raises(ValueError):
        b.image_metadata(h.encoded(raw), "registry/image@sha256:" + "a" * 64)


@pytest.mark.parametrize("requested", ["registry/image:latest", "sha256:" + "b" * 64, "registry/image@sha256:" + "f" * 64])
def test_configuration_id_is_not_repo_digest(requested):
    with pytest.raises(ValueError):
        b.image_metadata(h.encoded(metadata()), requested)


def test_duplicate_json_key_refused():
    with pytest.raises(ValueError):
        b.image_metadata(b'[{"Os":"linux","Os":"windows"}]', "r@sha256:" + "a" * 64)


def test_inventory_posix_sorted_and_hash_bound(tmp_path):
    (tmp_path / "Z").mkdir()
    (tmp_path / "a").write_bytes(b"two")
    (tmp_path / "Z/file").write_bytes(b"one")
    assert [r["path"] for r in b.inventory(tmp_path)] == ["Z/file", "a"]
    assert b.inventory(tmp_path)[0]["sha256"] == h.sha(b"one")


def manifest(image):
    return {"version": 2, "tasks": {"pytest-generalization": {"image": image,
        "command": ["sh", ".zerorun-env/bin/python", "-m", "pytest"], "inputs": [".zerorun-env"],
        "cacheable": False, "result_only": True, "cache_streams": False, "closure_reviewed": True,
        "outputs": [], "env": [], "unsafe_effects": [], "platform": "linux/amd64"}}}


def test_image_workspace_keeps_all_inputs_no_mutable_layer(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "tests").mkdir()
    (source / "tests/test_a.py").write_bytes(b"assert True")
    (source / "a.py").write_bytes(b"x=1")
    (source / ".git").mkdir()
    (source / ".git/config").write_bytes(b"untrusted source config")
    monkeypatch.setattr(h, "git", lambda root, args: (root / ".git").mkdir())
    monkeypatch.setattr(h, "IMAGE", "127.0.0.1:19509/zerorun-handoff-v2@sha256:" + "d" * 64)
    bench = SimpleNamespace(_frozen_pytest_manifest_bytes=lambda **kw: h.encoded(manifest(kw["runtime_image"])))
    setup, actual = runner.image_arm_workspace(bench, source, tmp_path / "arm", {"provenance": {"pinned": True}}, ["tests/test_a.py"])
    task = actual["tasks"]["handoff-tests"]
    assert task["command"] == ["/usr/local/bin/python", "-m", "pytest", "-p", "no:cacheprovider", "tests/test_a.py"]
    assert task["inputs"] == ["a.py", "tests"]
    assert task["result_only"] is task["closure_reviewed"] is task["cacheable"] is True
    assert task["env"] == task["outputs"] == task["unsafe_effects"] == []
    assert task["cache_streams"] is False
    assert not (tmp_path / "arm/.zerorun-env").exists()
    assert not (tmp_path / "arm/.git/config").exists()
    assert setup["mutable_dependency_directory_materialized"] is False


def test_reserved_mutable_dependency_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".zerorun-env").mkdir()
    monkeypatch.setattr(h, "git", lambda *a: None)
    with pytest.raises(ValueError, match="mutable dependency"):
        runner.image_arm_workspace(None, source, tmp_path / "arm", {}, ["tests.py"])


def test_command_preserves_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"hello", stderr=b""))
    assert b.command(tmp_path, "ok", ["docker", "info"]) == b"hello"
    assert v.command(tmp_path, "ok")["outer_ms"] >= 0
    monkeypatch.setattr(b.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=7, stdout=b"", stderr=b"failed"))
    with pytest.raises(ValueError):
        b.command(tmp_path, "bad", ["docker", "info"])
    assert v.v.read(tmp_path / "bad.json")["returncode"] == 7
    with pytest.raises(ValueError):
        v.command(tmp_path, "bad")


@pytest.mark.parametrize("bad_id", ["unrelated-container", "--all", "a" * 12, "a" * 64 + "\nother"])
def test_malformed_created_id_never_reaches_cleanup(tmp_path, monkeypatch, bad_id):
    engine = tmp_path / "engine"
    engine.mkdir()
    layer_root = tmp_path / "layer"
    (layer_root / "site-packages").mkdir(parents=True)
    (layer_root / "site-packages/a.py").write_bytes(b"x=1")
    (layer_root / "runtime-requirements.txt").write_bytes(b"locked")
    @contextmanager
    def layer(*args, **kwargs):
        yield {"root": layer_root, "snapshot": {}, "provenance": {}}
    monkeypatch.setattr(b, "os", SimpleNamespace(name="posix", getuid=lambda: 1000, getgid=lambda: 1000))
    monkeypatch.setattr(h, "load_engine", lambda root: (SimpleNamespace(_frozen_dependency_layer=layer), None, None, None, {}))
    monkeypatch.setattr(b.shutil, "which", lambda name: "docker")
    called = []
    registry_ref = "docker.io/library/registry@sha256:" + "e" * 64
    def invoke(output, label, argv, **kwargs):
        called.append(label)
        if label in {"base-inspect", "registry-inspect"}:
            row = metadata()
            row[0]["RepoDigests"] = [b.BASE_IMAGE if label == "base-inspect" else registry_ref]
            return h.encoded(row)
        if label == "registry-create":
            return bad_id.encode()
        return b""
    monkeypatch.setattr(b, "command", invoke)
    result = b.build(engine, tmp_path / "output", registry_ref)
    assert result["passed"] is False
    assert "container ID malformed" in result["error"]["message"]
    assert result["registry_container_id"] is None
    assert "registry-remove" not in called


@pytest.mark.parametrize("field,value", [("stdout_truncated", True), ("returncode", False), ("outer_ms", -1), ("outer_ms", True), ("error", {})])
def test_saved_command_does_not_trust_pass(tmp_path, field, value):
    h.save(tmp_path / "x.started.json", {"argv": ["docker", "info"]})
    row = {"argv": ["docker", "info"], "returncode": 0, "error": None, "outer_ms": 1,
           "stdout": "", "stderr": "", "stdout_truncated": False, "stderr_truncated": False}
    row[field] = value
    h.save(tmp_path / "x.json", row)
    with pytest.raises(ValueError):
        v.command(tmp_path, "x")


def test_dockerfile_is_network_free_copy_of_pinned_base():
    lines = b.DOCKERFILE.decode().splitlines()
    assert lines[0] == "FROM " + h.IMAGE
    assert not any(line.startswith(("RUN ", "ADD ")) for line in lines)
    assert "COPY site-packages/ /usr/local/lib/python3.12/site-packages/" in lines


def test_registry_startup_reset_is_retried_within_same_bound(monkeypatch):
    calls = []
    @contextmanager
    def response(url, timeout):
        calls.append((url, timeout))
        if len(calls) == 1:
            raise ConnectionResetError("startup fixture reset")
        yield SimpleNamespace(status=200)
    monkeypatch.setattr(b.urllib.request, "urlopen", response)
    monkeypatch.setattr(b.time, "sleep", lambda value: None)
    assert b.wait_registry_ready("http://127.0.0.1:19509/v2/") is True
    assert len(calls) == 2
    assert all(timeout == 1 for _, timeout in calls)


def test_registry_startup_stays_bounded_and_other_errors_escape(monkeypatch):
    ticks = iter([0, 16])
    monkeypatch.setattr(b.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(b.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("outside startup bound"))
    assert b.wait_registry_ready("http://127.0.0.1:19509/v2/") is False
    ticks = iter([0, 0])
    def fail(*a, **kw):
        raise ValueError("not a transient transport error")
    monkeypatch.setattr(b.urllib.request, "urlopen", fail)
    with pytest.raises(ValueError, match="not a transient"):
        b.wait_registry_ready("http://127.0.0.1:19509/v2/")


def test_original_v1_source_stays_unchanged():
    here = h.HERE
    expected = {"run.py": "b549bbc77745a99413c6a3f9976ccdb0af204e831686b2a6af761690c8e9c064",
        "validate.py": "44114982d83686b159484f69ae322e87de25a53802e0f3b69985967671463b91"}
    for name, digest in expected.items():
        assert h.sha(h.ordinary(here / name)) == digest


def image_fixture(tmp_path, monkeypatch):
    """Synthetic command records only; never represented as a Docker run."""
    cmds = tmp_path / "commands"
    cmds.mkdir()
    monkeypatch.setattr(h, "validate_runtime_rows", lambda rows: rows)
    port, cid = 19509, "c" * 64
    registry_ref = "docker.io/library/registry@sha256:" + "e" * 64
    reference = "127.0.0.1:19509/zerorun-handoff-v2@sha256:" + "a" * 64
    derived = metadata()
    derived[0]["RepoDigests"] = [reference]
    base, registry = metadata(), metadata()
    base[0]["RepoDigests"] = [b.BASE_IMAGE]
    registry[0]["RepoDigests"] = [registry_ref]
    file = {"path": "pytest/__init__.py", "bytes": 1, "sha256": h.sha(b"x")}
    provenance = {"runtime_requirements_sha256": h.sha(b"locked")}
    h.save(tmp_path / "dependency-files.json", {"source_site_files": [file], "provenance": provenance})
    context = [{"path": "Dockerfile", "bytes": len(b.DOCKERFILE), "sha256": h.sha(b.DOCKERFILE)},
               {"path": "runtime-requirements.txt", "bytes": 6, "sha256": h.sha(b"locked")},
               dict(file, path="site-packages/" + file["path"])]
    h.save(tmp_path / "context-inventory.json", {"files": context, "sha256": h.sha(h.encoded(context))})
    source = b.source_inventory()
    protocol = {"schema": "zerorun.handoff-image-build.v2", "base_image": b.BASE_IMAGE,
        "registry_image": registry_ref, "listen_address": "127.0.0.1", "port": port,
        "build_network": "none", "daemon_configuration_modified": False, "paid_services": False,
        "dockerfile_sha256": h.sha(b.DOCKERFILE), "sources": source, "engine_binding": {"runtime_files": []}}
    h.save(tmp_path / "protocol.json", protocol)
    raw = {"base-inspect": json.dumps(base), "registry-inspect": json.dumps(registry), "derived-inspect": json.dumps(derived),
        "registry-create": cid, "registry-container-inspect": json.dumps([{"Id": cid, "Image": registry[0]["Id"],
            "State": {"Running": True}, "HostConfig": {"PortBindings": {"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}}}]),
        "push": "digest: sha256:" + "a" * 64, "image-dependency-files": json.dumps([file]),
        "import-probe": json.dumps({"python": "3.12.14 fixture", "pytest": "9.1.1", "pytest_file": "/usr/local/lib/python3.12/site-packages/pytest/__init__.py"})}
    for name in ("base-inspect", "registry-inspect", "build", "registry-create", "registry-start", "registry-container-inspect",
                 "push", "pull-digest", "derived-inspect", "import-probe", "image-dependency-files", "registry-remove"):
        argv = ["docker", name]
        if name == "build":
            argv += ["--network=none", "--pull=false"]
        if name == "pull-digest":
            argv.append(reference)
        if name == "registry-remove":
            argv.append(cid)
        h.save(cmds / (name + ".started.json"), {"argv": argv})
        h.save(cmds / (name + ".json"), {"argv": argv, "returncode": 0, "error": None, "outer_ms": 1,
            "stdout_truncated": False, "stderr_truncated": False, "stdout": raw.get(name, ""), "stderr": ""})
    completion = {"schema": "zerorun.handoff-image-build-completion.v2", "protocol_sha256": h.sha(h.ordinary(tmp_path / "protocol.json")),
        "sources_after": source, "passed": True, "error": None, "source_unchanged": True, "registry_container_removed": True,
        "image_publicly_pullable": False, "bit_identical_recipe_rebuild_claimed": False,
        "image": b.image_metadata(h.encoded(derived), reference), "registry_container_id": cid,
        "dependency_provenance": provenance, "setup_outer_ms": 100}
    h.save(tmp_path / "completion.json", completion)
    return completion


def test_image_receipt_reconstructed_from_commands(tmp_path, monkeypatch):
    image_fixture(tmp_path, monkeypatch)
    summary = v.validate_image(tmp_path)
    assert summary["reconciled"] is True
    assert summary["dependency_files"] == 1
    assert summary["image_publicly_pullable"] is False


def test_frozen_source_subset_refused():
    with pytest.raises(ValueError, match="omits"):
        v.source_rows([h.record(b.HERE / "__init__.py", "__init__.py")], b.HERE)


@pytest.mark.parametrize("name,field,value", [("push", "stdout", "digest: sha256:" + "0" * 64),
    ("image-dependency-files", "stdout", "[]"), ("registry-remove", "returncode", 1),
    ("registry-container-inspect", "stdout", "[]"), ("build", "argv", ["docker", "build", "--network=host"]),
    ("import-probe", "stdout", '{"python":"3.10.0","pytest_file":"/usr/local/lib/python3.12/site-packages/pytest/__init__.py"}')])
def test_raw_image_mismatches_not_hidden_by_pass(tmp_path, monkeypatch, name, field, value):
    image_fixture(tmp_path, monkeypatch)
    path = tmp_path / "commands" / (name + ".json")
    row = v.v.read(path)
    row[field] = value
    path.write_bytes(h.encoded(row))
    with pytest.raises(ValueError):
        v.validate_image(tmp_path)
