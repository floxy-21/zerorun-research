"""Run the preserved six-case producer with a prospectively amended environment.

This operator driver never changes a producer prompt, test, result or boundary.
Authentication copies are private and deliberately excluded from public records.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

LEDGER_SHA = "4767391ca8f99ba3ad698bf1577b0e66c9dccc97fbd78565447ba1a4b1281997"
IMAGE_RECEIPT_SHA = "ffce2613a7b7305df307b3b811c6b8ed6c8f818af7cf0addbd4534bd34f75045"
CASES = ("eliben__pycparser-236", "joke2k__django-environ-174",
         "tobymao__sqlglot-3182", "terryyin__lizard-241",
         "eyeseast__python-frontmatter-56", "joshtemple__lkml-87")


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def invoke(root, label, command, *, timeout):
    start = utc()
    begin = time.monotonic()
    save(root / (label + ".started.json"), {"command": list(map(str, command)),
        "cwd": str(root), "started_utc": start, "timeout_seconds": timeout})
    with (root / (label + ".stdout.log")).open("xb") as out, (root / (label + ".stderr.log")).open("xb") as err:
        result = subprocess.run(command, cwd=root, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, timeout=timeout, check=False)
    record = {"label": label, "command": list(map(str, command)), "cwd": str(root),
              "started_utc": start, "finished_utc": utc(),
              "elapsed_seconds": time.monotonic() - begin, "returncode": result.returncode,
              "stdout": {"path": label + ".stdout.log", "sha256": sha(root / (label + ".stdout.log"))},
              "stderr": {"path": label + ".stderr.log", "sha256": sha(root / (label + ".stderr.log"))}}
    save(root / (label + ".command.json"), record)
    if result.returncode:
        raise RuntimeError("operator command failed: " + label)
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("output", "source", "ledger", "helper", "engine", "image-build", "codex", "node", "authentication-file", "protocol"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument("--execute-user-authorized-lab", action="store_true")
    args = p.parse_args()
    if os.name != "posix" or os.getuid() == 0 or not args.execute_user_authorized_lab:
        raise ValueError("explicit non-root Linux laboratory execution required")
    output = args.output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("new external output required")
    if sha(args.ledger) != LEDGER_SHA or sha(args.image_build / "completion.json") != IMAGE_RECEIPT_SHA:
        raise ValueError("frozen input identities differ")
    if shutil.disk_usage(output.parent).free < 20 * 1024 ** 3:
        raise ValueError("insufficient disk headroom")
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    if int(memory["MemAvailable"].split()[0]) < 768 * 1024:
        raise ValueError("insufficient available memory")
    auth = args.authentication_file.resolve(strict=True)
    if not auth.is_file() or args.authentication_file.is_symlink():
        raise ValueError("ordinary external authentication file required")
    output.mkdir(mode=0o700)
    original = output / "study"
    original.mkdir()
    for name in ("common.py", "run.py", "validate.py", "PROTOCOL.md"):
        source = args.source / name
        shutil.copyfile(source, original / name)
    shutil.copyfile(args.protocol, output / "APPLICATION_PROTOCOL.md")
    save(output / "launch.json", {"pid": os.getpid(), "started_utc": utc(),
        "working_directory": str(output), "driver_sha256": sha(__file__),
        "protocol_sha256": sha(output / "APPLICATION_PROTOCOL.md"),
        "source_files": {p.name: sha(p) for p in sorted(original.iterdir())},
        "disk_free_bytes": shutil.disk_usage(output).free,
        "available_memory_kib": int(memory["MemAvailable"].split()[0]),
        "guest_boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "selected_case_ids": list(CASES), "model_started": False,
        "scope": "new producer main, preserved protocol plus frozen environment amendment"})
    summary = {"schema": "zerorun.producer-campaign-0.5.3.v1", "started_utc": utc(),
               "cases": [], "error": None, "all_selected_accounted_for": False}
    try:
        venv = output / "exploratory-environment"
        invoke(output, "environment-create", [sys.executable, "-m", "venv", str(venv)], timeout=180)
        packages = json.loads((args.image_build / "completion.json").read_bytes())["result"]["packages"]
        wheelhouse = args.image_build / "context/wheelhouse"
        wheels = []
        for row in packages:
            wheel = wheelhouse / row["wheel"]
            if wheel.stat().st_size != row["bytes"] or sha(wheel) != row["sha256"]:
                raise ValueError("locked dependency wheel differs")
            wheels.append(str(wheel))
        python = venv / "bin/python"
        invoke(output, "environment-install", [str(python), "-I", "-B", "-m", "pip", "install",
            "--no-index", "--no-deps", "--disable-pip-version-check", *wheels], timeout=180)
        invoke(output, "environment-inventory", [str(python), "-I", "-B", "-c",
            "import sys,json,importlib.metadata as m;print(json.dumps({'python':sys.version,'packages':sorted((d.metadata['Name'],d.version) for d in m.distributions())}))"], timeout=30)
        prepared = output / "prepared"
        command = [sys.executable, "-B", str(original / "run.py"), "prepare",
            "--ledger", str(args.ledger), "--handoff-helper", str(args.helper), "--engine", str(args.engine),
            "--codex", str(args.codex), "--node", str(args.node), "--test-python", str(python), "--output", str(prepared)]
        invoke(output, "prepare", command, timeout=300)
        freeze = json.loads((prepared / "freeze.json").read_bytes())
        if tuple(row["case_id"] for row in freeze["cases"]) != CASES:
            raise ValueError("original frozen selection differs")
        freeze_sha = sha(prepared / "freeze.json")
        save(output / "execution-freeze.json", {"created_utc": utc(), "producer_freeze_sha256": freeze_sha,
             "protocol_sha256": sha(output / "APPLICATION_PROTOCOL.md"), "driver_sha256": sha(__file__),
             "packages": packages, "environment_inventory_sha256": sha(output / "environment-inventory.stdout.log"),
             "one_attempt_per_case": True, "original_reference_solution_supplied": False})
        for index, case in enumerate(CASES):
            client_home = output / ("private-auth-" + str(index))
            client_home.mkdir(mode=0o700)
            temporary_auth = client_home / "auth.json"
            shutil.copyfile(auth, temporary_auth)
            temporary_auth.chmod(0o600)
            try:
                invoke(output, f"case-{index:02d}", [sys.executable, "-B", str(original / "run.py"), "run-case",
                    "--prepared", str(prepared), "--freeze-sha256", freeze_sha, "--case-index", str(index),
                    "--codex-home", str(client_home), "--approve-model-sessions"], timeout=690)
            finally:
                # Only this newly created credential copy is removed; session logs remain private.
                temporary_auth.unlink(missing_ok=True)
            result = json.loads((prepared / f"case-{index:02d}/session.json").read_bytes())
            summary["cases"].append({"case_id": case,
                "model_invocation_attempted": result["model_invocation_attempted"],
                "boundary_pass": result["boundary_pass"], "error": result["error"],
                "session_sha256": sha(prepared / f"case-{index:02d}/session.json")})
            print(json.dumps(summary["cases"][-1]), flush=True)
            if not result["boundary_pass"]:
                raise RuntimeError("preserved producer boundary stop")
    except Exception as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        accounted = {r["case_id"] for r in summary["cases"]}
        for index, case in enumerate(CASES):
            started = output / "prepared" / f"case-{index:02d}" / "started.json"
            if case not in accounted and started.is_file():
                summary["cases"].append({"case_id": case,
                    "disposition": "started_attempt_without_reconciled_session",
                    "started_sha256": sha(started), "model_invocation_attempted": None})
                accounted.add(case)
        summary["unreached_cases"] = [case for case in CASES if case not in accounted]
        summary["all_selected_accounted_for"] = len(accounted) + len(summary["unreached_cases"]) == len(CASES)
        summary["finished_utc"] = utc()
        summary["temporary_authentication_files_absent"] = not any(output.glob("private-auth-*/auth.json"))
        save(output / "completion.json", summary)
        print(json.dumps(summary), flush=True)
    return 1 if summary["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
