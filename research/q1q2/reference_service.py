"""Small fresh/receipt comparator. No imports from ZeroRun.

Research service for a trusted local host and quiescent, operator-qualified
whole-tree tasks. This is not a general adversarial multi-tenant cache.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

FIXED_ENV = {"LANG": "C", "LC_ALL": "C", "PYTHONHASHSEED": "0",
             "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": "/tmp/zerorun-pycache", "TZ": "UTC"}
RESOURCE = ["--cpus", "2", "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "512"]
EXCLUDE = {".git", ".zerorun", ".zerorun.json"}


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def tree(root):
    rows = [[".", stat.S_IMODE(root.stat().st_mode), "dir"]]
    for folder, dirs, files in os.walk(root, followlinks=False):
        if Path(folder) == root:
            dirs[:] = [x for x in dirs if x not in EXCLUDE]
            files = [x for x in files if x not in EXCLUDE]
        for name in sorted(dirs + files):
            p = Path(folder) / name
            st = p.lstat()
            if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
                raise ValueError("linked/special source refused")
            if p.suffix == ".pyc" and "__pycache__" not in p.parts:
                raise ValueError("sourceless bytecode refused")
            rows.append([p.relative_to(root).as_posix(), stat.S_IMODE(st.st_mode),
                         "dir" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest()])
            now = p.lstat()
            if (st.st_ino, st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_mode) != (
                    now.st_ino, now.st_mtime_ns, now.st_ctime_ns, now.st_size, now.st_mode):
                raise ValueError("source changed during read")
    return sorted(rows)


def runtime(image):
    if "@sha256:" not in image:
        raise ValueError("digest-pinned runtime required")
    row, = json.loads(subprocess.check_output(["docker", "image", "inspect", image], timeout=60))
    if not any(x.endswith("@" + image.split("@", 1)[1]) for x in row.get("RepoDigests", [])):
        raise ValueError("runtime digest is not attested")
    if (row.get("Os"), row.get("Architecture")) != ("linux", "amd64"):
        raise ValueError("wrong runtime platform")
    return {"id": row["Id"], "config": row["Config"], "layers": row["RootFS"],
            "requested": image, "fixed_env": FIXED_ENV, "resources": RESOURCE}


def attach(name, timeout, capture=None):
    """Drain both streams with bounded tails; optional bounded research capture."""
    proc = subprocess.Popen(["docker", "start", "--attach", name], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams, errors = {}, []
    def drain(label, pipe):
        tail, total = b"", 0
        output = open(capture, "wb") if label == "stdout" and capture else None
        try:
            while chunk := pipe.read(65536):
                total += len(chunk)
                tail = (tail + chunk)[-4000:]
                if output:
                    if total <= 50_000_000:
                        output.write(chunk)
                    elif not errors:
                        errors.append(ValueError("research oracle capture exceeds 50 MB"))
        except Exception as error:
            errors.append(error)
        finally:
            pipe.close()
            if output:
                output.close()
            streams[label] = (tail, total)
    threads = [threading.Thread(target=drain, args=(label, pipe), daemon=True)
               for label, pipe in (("stdout", proc.stdout), ("stderr", proc.stderr))]
    for thread in threads:
        thread.start()
    try:
        code = proc.wait(timeout=timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        for thread in threads:
            thread.join(timeout=10)
        if any(t.is_alive() for t in threads):
            raise RuntimeError("output drain did not finish")
    if errors:
        raise errors[0]
    return code, streams


def fresh(root, task, timeout=900):
    name = "q1q2-reference-" + secrets.token_hex(8)
    command = ["docker", "create", "--name", name, "--pull", "never", "--platform", "linux/amd64",
               "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               *RESOURCE, "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
               "--tmpfs", "/workspace/.zerorun:rw,nosuid,nodev,noexec,size=1m",
               "--tmpfs", "/workspace/.git:rw,nosuid,nodev,noexec,size=1m",
               "--mount", "type=bind,src=" + str(root) + ",dst=/workspace,readonly", "--workdir", "/workspace"]
    for key, value in sorted(FIXED_ENV.items()):
        command += ["--env", key + "=" + value]
    command += [task["image"], *task["command"]]
    started = time.perf_counter()
    result = None
    try:
        created = subprocess.run(command, capture_output=True, check=True, timeout=60)
        container_id = created.stdout.decode().strip()
        if len(container_id) != 64 or any(c not in "0123456789abcdef" for c in container_id):
            raise ValueError("container identity missing")
        code, streams = attach(name, timeout, os.environ.get("Q1Q2_ORACLE_CAPTURE"))
        witness, = json.loads(subprocess.check_output(["docker", "container", "inspect", name], timeout=60))
        state = witness["State"]
        if witness["Id"] != container_id or state["Running"] or state["Status"] != "exited" or state["Error"] or state["OOMKilled"]:
            raise ValueError("container did not finish normally")
        if code != state["ExitCode"]:
            raise ValueError("CLI and container exit status differ")
        result = {"status": "FRESH_EXECUTED", "exit_code": state["ExitCode"],
                "stdout_tail": streams["stdout"][0].decode(errors="replace"),
                "stderr_tail": streams["stderr"][0].decode(errors="replace"),
                "output_bytes": {k: v[1] for k, v in streams.items()},
                "output_truncated": any(v[1] > 4000 for v in streams.values()),
                "historical": False, "container_state": state, "container_id": container_id}
        return result
    finally:
        # Even a failed CLI create may have allocated the uniquely named container.
        cleanup = subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=60)
        if cleanup.returncode and b"No such container" not in cleanup.stderr:
            raise RuntimeError("task-owned container cleanup failed: " + cleanup.stderr.decode(errors="replace")
                               + "; primary error: " + repr(sys.exc_info()[1]))
        if result is not None:
            result["execution_and_cleanup_seconds"] = time.perf_counter() - started


def request(root, arm, key_path, verify=False):
    raw = (root / ".zerorun.json").read_bytes()
    task = json.loads(raw)["tasks"]["tests"]
    if task["env"] or task["outputs"] or task["unsafe_effects"] or not task["result_only"]:
        raise ValueError("unsupported service contract")
    if arm == "fresh" or verify:
        return fresh(root, task)
    image = runtime(task["image"])
    before = tree(root)
    descriptor = {"schema": 1, "root": str(root), "tree": before,
                  "manifest_sha256": hashlib.sha256(raw).hexdigest(), "runtime": image}
    key = hashlib.sha256(encode(descriptor)).hexdigest()
    private = Path(key_path)
    if private.is_symlink() or private.stat().st_mode & 0o077 or root in private.resolve().parents:
        raise ValueError("private receipt key must be protected outside checkout")
    secret = private.read_bytes()
    if len(secret) != 32:
        raise ValueError("invalid key")
    store = root / ".zerorun" / "reference"
    store.mkdir(parents=True, exist_ok=True)
    receipt = store / (key + ".json")
    hit = None
    if receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size < 8192:
        try:
            record = json.loads(receipt.read_bytes())
            body = record["body"]
            valid = hmac.compare_digest(record["hmac"], hmac.new(secret, encode(body), "sha256").hexdigest())
            if valid and body == {"schema": 1, "key": key, "exit_code": 0}:
                hit = {"status": "HIT_REUSED", "exit_code": 0, "historical": True,
                       "stdout_tail": "", "stderr_tail": "", "cache_key": key}
        except (ValueError, KeyError, TypeError):
            pass
    if hit is None:
        with tempfile.TemporaryDirectory(prefix="q1q2-reference-snapshot-") as folder:
            snapshot = Path(folder) / "source"
            # Copy bytes and keyed modes only. copytree/copy2 can copy xattrs or
            # ACLs, which are outside both staged execution identities.
            snapshot.mkdir(mode=stat.S_IMODE(root.stat().st_mode))
            for path, mode, kind in before:
                if path == ".":
                    continue
                destination = snapshot / path
                if kind == "dir":
                    destination.mkdir()
                else:
                    shutil.copyfile(root / path, destination)
                destination.chmod(mode)
            snapshot.chmod(stat.S_IMODE(root.stat().st_mode))
            (snapshot / ".git").mkdir()
            (snapshot / ".zerorun").mkdir()
            # Match ZeroRun's execution normalization; input timestamps are not
            # keys because task-visible timestamps in staged execution are zero.
            for path in sorted(snapshot.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                os.utime(path, ns=(0, 0), follow_symlinks=False)
            os.utime(snapshot, ns=(0, 0), follow_symlinks=False)
            if tree(snapshot) != before:
                raise ValueError("snapshot differs")
            hit = fresh(snapshot, task)
            hit["status"] = "MISS_EXECUTED"
    if tree(root) != before or (root / ".zerorun.json").read_bytes() != raw:
        raise ValueError("source changed; response discarded")
    if hit["status"] == "MISS_EXECUTED" and hit["exit_code"] == 0:
        body = {"schema": 1, "key": key, "exit_code": 0}
        blob = encode({"body": body, "hmac": hmac.new(secret, encode(body), "sha256").hexdigest()})
        fd, temp = tempfile.mkstemp(dir=store, prefix=".receipt-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, receipt)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    hit["cache_key"] = key
    return hit


def main():
    arm, key_path = sys.argv[1:3]
    for line in sys.stdin:
        message = json.loads(line)
        if message.get("method") == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "q1q2-reference", "version": "1"}}
        elif message.get("method") == "tools/call":
            try:
                result = request(Path.cwd(), arm, key_path, message["params"]["arguments"].get("verify", False))
            except Exception as error:
                result = {"status": "ERROR", "error": type(error).__name__ + ": " + str(error)}
            result = {"content": [{"type": "text", "text": json.dumps(result)}]}
        else:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
