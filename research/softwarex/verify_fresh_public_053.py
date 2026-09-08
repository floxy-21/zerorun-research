"""Read-only replay of the sealed fresh-public Linux conformance demonstration.

This verifies recorded author-side observations, not independent human replication,
physical-host isolation, or a new Docker/model execution. The downloadable OS
archive is not redistributed: its retained acquisition/checksum observations are
explicitly a narrower boundary than hashing the archive anew offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path, PurePosixPath

from research.softwarex import quickstart_053 as old
from research.softwarex import quickstart_053_metadata_v2 as q

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = "research/softwarex/evidence/public-fresh-linux-053-v1"
SEAL_SHA = "740c0391547516c3917e83029fe1bfd6aa0bd0ca746c30627b7f50600ea63ba7"
COMMIT = "13a3ceae84913a1ddbed412bdad9cd6fbf7e2e54"
PUBLIC_MANIFEST = "2e9d3960d252d6ee3d27f85662dc3cd6a67cbbfcaa705021d2c059001deb7651"
WHEEL = {"bytes": 244550, "sha256": "dbac896587365b010b6c1c2c5913e1f75e79da99cfdb00c407e67f3442deddb9"}
ROOTFS_SHA = "41f73e3cf5fa919b8aa5ca6b30dc48f0da2720776d7423e2a7748211456fe081"
ROOTFS_URL = "https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.1-x86_64.tar.gz"
DISTRO = "ZeroRunFresh053_20260908v1"
SOURCE = "/home/reviewer/zerorun-fresh-v1/source"
GIT = ["git", "-c", "credential.helper=", "-c", "core.askPass=", "-c", "http.extraHeader=", "-C", SOURCE]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load(path):
    raw = Path(path).read_bytes()
    require(len(raw) <= 16 * 1024 * 1024, "record envelope exceeds bound")
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("non-finite JSON constant: " + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def identity(path):
    raw = Path(path).read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def member(directory, relative):
    require(isinstance(relative, str) and "\\" not in relative, "invalid record path")
    p = PurePosixPath(relative)
    require(not p.is_absolute() and str(p) == relative and all(x not in ("", ".", "..") for x in p.parts), "unsafe record path")
    target = Path(directory).joinpath(*p.parts)
    require(target.is_file() and not any(x.is_symlink() or (hasattr(x, "is_junction") and x.is_junction()) for x in [target, *target.parents] if x != Path(directory).parent), "missing or linked record")
    require(target.resolve().is_relative_to(Path(directory).resolve()), "record escaped directory")
    return target


def seal(directory, expected_sha=None):
    directory = Path(directory)
    path = member(directory, "RECORD_MANIFEST.json")
    if expected_sha is not None:
        require(identity(path)["sha256"] == expected_sha, "sealed original record manifest differs")
    value = load(path)
    require(value.get("schema") == "zerorun.fresh-public-records.v1", "record seal schema differs")
    rows = value["files"]
    names = [row["path"] for row in rows]
    require(len(names) == len(set(names)) and "RECORD_MANIFEST.json" not in names, "duplicate/self-referencing seal")
    for row in rows:
        require(set(row) == {"path", "bytes", "sha256"} and identity(member(directory, row["path"])) == {k: row[k] for k in ("bytes", "sha256")}, "sealed payload differs: " + row["path"])
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    require(actual == set(names) | {"RECORD_MANIFEST.json"}, "record closure has missing or unlisted files")
    return len(rows)


def command(directory, name, *, argv=None, code=0):
    value = load(member(directory, name + ".json"))
    require(type(value.get("returncode")) is int and value["returncode"] == code and value.get("error") is None and value.get("timed_out", False) is False, "recorded command failed or status differs: " + name)
    elapsed = value["elapsed_seconds"]
    require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0, "invalid command duration")
    require(datetime.fromisoformat(value["finished_utc"]) >= datetime.fromisoformat(value["started_utc"]), "reversed command timestamps")
    if argv is not None:
        require(value["argv"] == argv, "recorded command argv differs: " + name)
    raw = {}
    for stream in ("stdout", "stderr"):
        row = value[stream]
        path = member(directory, row["path"])
        require(row["path"] == name + "." + stream + ".txt" and identity(path) == {k: row[k] for k in ("bytes", "sha256")}, "command stream binding differs")
        raw[stream] = path.read_bytes()
    return value, raw


def verify_host(directory, original):
    host = Path(directory)
    archive = load(host / "alpine-rootfs.tar.gz.download.json")
    require(archive["url"] == archive["final_url"] == ROOTFS_URL and archive["status"] == 200 and archive["credentials_supplied"] is False and archive["sha256"] == ROOTFS_SHA and archive["bytes"] == 3698422, "official rootfs acquisition differs")
    checksum = load(host / "alpine-rootfs.sha256.download.json")
    require(checksum["url"] == checksum["final_url"] == ROOTFS_URL + ".sha256" and checksum["status"] == 200 and checksum["credentials_supplied"] is False, "publisher checksum acquisition differs")
    require(identity(host / "alpine-rootfs.sha256") == {k: checksum[k] for k in ("bytes", "sha256")} and (host / "alpine-rootfs.sha256").read_text().split()[0] == ROOTFS_SHA, "retained publisher checksum differs")
    _, listing = command(host, "wsl-list-before", argv=["wsl.exe", "--list", "--verbose"])
    require(DISTRO not in listing["stdout"].decode("utf-16-le"), "distro already present before import")
    row, _ = command(host, "wsl-import")
    require(row["argv"] == ["wsl.exe", "--import", DISTRO, "C:\\ZeroRun-recovery-20260908\\public-fresh-reproduction-v1\\distro", "C:\\ZeroRun-recovery-20260908\\public-fresh-reproduction-v1\\alpine-rootfs.tar.gz", "--version", "2"], "fresh WSL import differs")
    require(datetime.fromisoformat(row["started_utc"]) >= datetime.fromisoformat(archive["finished_utc"]), "rootfs acquisition/import order differs")
    _, raw = command(host, "fresh-os-preflight")
    require(b"ID=alpine" in raw["stdout"] and b"VERSION_ID=3.24.1" in raw["stdout"] and b"microsoft-standard-WSL2" in raw["stdout"], "fresh OS identity differs")
    command(host, "empty-docker-state")
    require(load(host / "prerequisites.json")["passed"] is False, "original daemon lifecycle refusal absent")
    command(host, "docker-ready", code=1)
    command(host, "docker-v2-ready")
    launch = load(host / "dockerd-v2-launch.json")["argv"]
    require(launch == ["wsl.exe", "-d", DISTRO, "-u", "root", "--exec", "dockerd", "--data-root=/var/lib/docker", "--exec-root=/run/docker", "--pidfile=/run/zerorun-fresh-dockerd.pid", "--host=unix:///var/run/docker.sock", "--iptables=false", "--bridge=none"], "daemon identity differs")
    empty, raw = command(original, "empty-image-inventory", argv=["docker", "image", "ls", "--quiet"])
    require(raw["stdout"] == b"", "Docker image inventory was not empty")
    pull, _ = command(original, "public-base-pull", argv=["docker", "pull", "--platform", "linux/amd64", q.IMAGE])
    require(datetime.fromisoformat(empty["finished_utc"]) <= datetime.fromisoformat(pull["started_utc"]), "empty inventory was not observed before image pull")
    _, raw = command(original, "pulled-image-identity")
    image = q.validate_image(raw["stdout"].decode())
    return {"rootfs_sha256": ROOTFS_SHA, "rootfs_archive_rehashed_offline": False, "new_docker_empty_inventory": True, "public_image_digest": q.IMAGE, "image": image}


def verify_original(root, original):
    protocol = load(original / "protocol.json")
    require(protocol["credentials_supplied"] is False and protocol["source_commit"] == COMMIT and protocol["public_manifest_sha256"] == PUBLIC_MANIFEST and protocol["wheel_sha256"] == WHEEL["sha256"], "public source protocol differs")
    command(original, "anonymous-origin", argv=GIT + ["remote", "add", "origin", "https://github.com/floxy-21/zerorun-research.git"])
    command(original, "anonymous-public-fetch", argv=GIT + ["fetch", "--depth", "1", "origin", COMMIT])
    command(original, "pinned-checkout", argv=GIT + ["checkout", "--detach", COMMIT])
    _, head = command(original, "source-head-before")
    _, status = command(original, "source-status-before")
    require(head["stdout"].decode().strip() == COMMIT and not status["stdout"], "original source identity/cleanliness differs")
    install = old.validate_installation_receipt(root, original / "source-install.json")
    failed = old.validate_saved_receipt(root, original / "source-mcp.json")
    require(install["passed"] and install["source_commit"] == COMMIT and failed["passed"] is False and failed["stages_recorded"] == 0, "original install/refusal scope differs")
    value = load(original / "source-mcp.json")
    require(value["failure"] == {"type": "SmokeError", "message": "Git index snapshot exceeded its output limit"} and len(value["commands"]) == 5 and not value["stages"], "original pre-MCP failure differs")
    for i, row in enumerate(value["commands"]):
        require(q.metadata_command(row["command"], SOURCE) and tuple(row["command"][7:]) == q.METADATA_SUFFIXES[i], "original metadata command differs")
        decoded = old.validate_streams(row["streams"], require_success=False)
        require(row["streams"]["stdout_truncated"] is (i == 4), "original truncation boundary differs")
        if i == 4:
            require(len(decoded["stdout"]) == old.LIMIT, "original truncation limit differs")
    require(load(original / "completion.json")["public_wheel"] == WHEEL, "original public wheel observation differs")
    return install


def failure_exchange(stage, fixture_root):
    require(stage["requests"] == q.request_plan(stage["request_kind"], fixture_root), "failure request differs")
    decoded = q.validate_streams(stage["streams"])
    require(not decoded["stderr"], "failure server emitted stderr")
    responses = [q.strict_json(line) for line in decoded["stdout"].splitlines() if line.strip()]
    require(responses == stage["responses"] and len(responses) == 2 and [r.get("id") for r in responses] == [1, 2] and all(r.get("jsonrpc") == "2.0" and "error" not in r for r in responses), "failure raw RPC envelope differs")
    init = responses[0]["result"]
    require(init["serverInfo"] == {"name": "zerorun", "version": "0.5.3"} and init["protocolVersion"] == "2025-11-25", "failure server identity differs")
    wire = responses[1]["result"]
    require(wire.get("isError") is True and len(wire["content"]) == 1 and wire["content"][0]["type"] == "text", "actual failure wire flag/content absent")
    payload = wire["structuredContent"]
    require(q.strict_json(wire["content"][0]["text"]) == payload, "failure text and structured payload differ")
    require(payload["status"] == "MISS_FAILED" and type(payload["exit_code"]) is int and payload["exit_code"] == 73 and payload["verified"] is False and payload["task"] == "synthetic-lifecycle" and payload["restored_outputs"] == [] and payload["mode"] == "reuse", "failure result status differs")
    require(len(payload["cache_key"]) == 64 and all(c in "0123456789abcdef" for c in payload["cache_key"]), "failure cache identity absent")
    require(payload["stdout_tail"] == payload["stderr_tail"] == "" and payload["saved_ms"] == payload["saved_seconds"] == 0, "failure claimed diagnostics/savings differ")
    require(all(type(payload[x]) in (float, int) and math.isfinite(payload[x]) and payload[x] > 0 for x in ("execution_ms", "wall_ms")), "fresh failure timing absent")
    return payload["cache_key"]


def verify_failure(root, directory, wheel):
    value = load(directory / "completion.json")
    qualification = load(directory / "qualification.json")
    from tools import codex_agent_lifecycle as lifecycle
    require((directory / "fixture.py").read_bytes() == lifecycle.FIXTURE_PROGRAM.encode() and (directory / "original-fixture.txt").read_bytes() == lifecycle.FIXTURE_DATA.encode(), "published fixture program/original state changed")
    negative = (directory / "negative-fixture.txt").read_bytes()
    require(negative == b"zerorun deliberately unequal synthetic fixture data v1\n", "negative fixture state differs")
    require(qualification["program"] == identity(directory / "fixture.py") and qualification["original_data_sha256"] == identity(directory / "original-fixture.txt")["sha256"] and qualification["negative_data_sha256"] == hashlib.sha256(negative).hexdigest(), "two-state qualification hashes differ")
    require(qualification["command"] == ["python", "fixture.py"] and qualification["inputs"] == ["fixture.py", "fixture.txt"] and qualification["environment_forwarded"] == qualification["outputs"] == [] and qualification["network"] == "none" and qualification["image"] == q.IMAGE, "qualification exceeds fixed fixture")
    manifest = load(directory / "manifest.json")
    require(manifest == {"version": 2, "tasks": {"synthetic-lifecycle": {"cache_streams": False, "cacheable": True, "closure_reviewed": True, "command": ["python", "fixture.py"], "env": [], "image": q.IMAGE, "input_symbols": [], "inputs": ["fixture.py", "fixture.txt"], "outputs": [], "platform": "linux/amd64", "result_only": True, "unsafe_effects": []}}}, "failure fixture manifest differs")
    require(value["manifest_sha256"] == identity(directory / "manifest.json")["sha256"], "failure manifest byte identity differs")
    require(value["helper"] == identity(root / "research/softwarex/quickstart_053_metadata_v2.py"), "failure producer helper differs")
    q.assert_authorization(value["authorization"], value["manifest_sha256"], value["authority_before"], value["authority_after"])
    require(value["installed_before"] == value["installed_after"] == wheel["installed_after"], "failure installed identity differs")
    q.validate_core(value["installed_before"]["module"], value["installed_before"])
    require([(s["name"], s["request_kind"]) for s in value["stages"]] == [("fresh_failure_verify", "verify"), ("repeated_failure_default", "miss")], "failure stage selection differs")
    keys = [failure_exchange(s, value["fixture_root"]) for s in value["stages"]]
    require(keys[0] == keys[1], "repeated failure input identity differs")
    rows = []
    for row in value["commands"]:
        q.validate_streams(row["streams"])
        if row["command"][-1:] == ["mcp-server"]:
            rows.append(row)
    require(len(rows) == 2 and all(row["streams"] == stage["streams"] and row["cwd"] == value["fixture_root"] for row, stage in zip(rows, value["stages"])), "failure commands and captured exchanges differ")
    auth = [row for row in value["commands"] if "authorize" in row["command"]]
    require(len(auth) == 1 and q.strict_json(q.validate_streams(auth[0]["streams"])["stdout"]) == value["authorization"], "authorization raw response differs")
    require(value["model_called"] is False and value["real_repository_authorized"] is False and value["fixture_and_authority_stable"] is True and value["temporary_fixture_and_authority_cleaned"] is True and value["failure"] is None, "failure demonstration boundary differs")
    return 2


def verify(root, directory):
    root, directory = Path(root).resolve(), Path(directory).resolve()
    count = seal(directory, SEAL_SHA)
    folders = {name: directory / name for name in ("host-preparation", "original-attempt", "corrected-attempt", "failure-supplement")}
    for folder in folders.values():
        seal(folder)
        for path in folder.glob("*.json"):
            value = load(path)
            if "argv" in value and "returncode" in value:
                expected = 1 if path.name in ("docker-ready.json", "documented-source-install-and-mcp.json") else 0
                command(folder, path.stem, code=expected)
    host = verify_host(folders["host-preparation"], folders["original-attempt"])
    original = verify_original(root, folders["original-attempt"])
    corrected = folders["corrected-attempt"]
    for name in ("quickstart_053_metadata_v2.py", "QUICKSTART_053_METADATA_V2.md"):
        require(identity(corrected / name) == identity(root / "research/softwarex" / name), "corrective helper/guide bytes differ")
    protocol = load(corrected / "protocol.json")
    require(protocol["prior_attempt_manifest"] == identity(folders["original-attempt"] / "RECORD_MANIFEST.json") and protocol["prior_attempt_completion"] == identity(folders["original-attempt"] / "completion.json"), "earlier refusal binding differs")
    source = q.validate_receipt_pair(root, corrected / "install.json", corrected / "check.json")
    wheel_summary = q.validate_saved_receipt(root, corrected / "wheel-check.json")
    require(source["passed"] and source["installation"]["commands_recorded"] == 7 and source["check"]["stages_recorded"] == wheel_summary["stages_recorded"] == 5 and wheel_summary["passed"] and source["check"]["source_commit"] == wheel_summary["source_commit"] == COMMIT, "corrected actual conformance counts differ")
    for name in ("install.json", "check.json", "wheel-check.json"):
        require(load(corrected / name)["source_bindings"]["public_manifest_sha256"] == PUBLIC_MANIFEST, "actual source manifest differs")
    completion = load(corrected / "completion.json")
    require(completion["wheel"] == WHEEL and completion["source_validation"] == source and completion["wheel_validation"] == wheel_summary, "outer summary differs from strict replay")
    for name in ("before", "after"):
        _, h = command(corrected, "source-head-" + name)
        _, s = command(corrected, "source-status-" + name)
        require(h["stdout"].decode().strip() == COMMIT and not s["stdout"], "corrected source changed")
    wheel = load(corrected / "wheel-check.json")
    failures = verify_failure(root, folders["failure-supplement"], wheel)
    return {"schema": "zerorun.fresh-public-linux-reconciliation.v1", "passed": True,
        "source_commit": COMMIT, "public_manifest_sha256": PUBLIC_MANIFEST, "manifest_sha256": SEAL_SHA,
        "record_files": count, "original_installation": original, "original_pre_mcp_failure_preserved": True,
        "source_installation_and_mcp": source, "wheel_mcp": wheel_summary, "wheel": WHEEL,
        "source_install_commands": 7, "normal_mcp_requests": 10, "fresh_failure_requests": failures,
        "model_called": False, "independent_human_replication": False, "read_only": True,
        "scope": "Author-side new Linux userland and Docker data store on an existing shared Windows/WSL host; fixed synthetic fixture only; no timing benefit or independent human trial.", **host}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--evidence-dir", type=Path, default=Path(EVIDENCE))
    parser.add_argument("--check", action="store_true", help="Read-only verification (all invocations are read-only).")
    args = parser.parse_args()
    directory = args.evidence_dir if args.evidence_dir.is_absolute() else args.source_root / args.evidence_dir
    print(json.dumps(verify(args.source_root, directory), sort_keys=True))


if __name__ == "__main__":
    main()
