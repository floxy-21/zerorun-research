"""One bounded anonymous public checkout and the documented account-free lab."""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

URL = "https://github.com/floxy-21/zerorun-research.git"
DESTINATION = Path("/home/floxy/zerorun-public-quickstart-053-20260908-v1")


def save(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")


def captured(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "base64": base64.b64encode(raw).decode("ascii")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("commit")
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.commit):
        raise ValueError("exact public commit required")
    if DESTINATION.exists() or DESTINATION.is_symlink():
        raise ValueError("new external quickstart directory required; previous attempts remain retained")
    DESTINATION.mkdir(mode=0o700)
    source = DESTINATION / "source"
    anonymous_home = DESTINATION / "anonymous-git-home"
    anonymous_home.mkdir(mode=0o700)
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_") or name in {"GH_TOKEN", "GITHUB_TOKEN", "GCM_INTERACTIVE", "SSH_ASKPASS"}:
            environment.pop(name, None)
    environment.update(HOME=str(anonymous_home), XDG_CONFIG_HOME=str(anonymous_home),
        GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
        GIT_ASKPASS="false", SSH_ASKPASS="false", GCM_INTERACTIVE="never")
    normal = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        normal.pop(name, None)
    for name in tuple(normal):
        if name.startswith(("OPENAI_", "CODEX_", "ZERORUN_VM_")) or name in {"GH_TOKEN", "GITHUB_TOKEN"}:
            normal.pop(name, None)
    normal["PYTHONDONTWRITEBYTECODE"] = "1"
    git = ["git", "-c", "credential.helper=", "-c", "core.askPass=", "-c", "http.extraHeader="]
    commands = []

    def invoke(label, argv, *, timeout=300, anonymous=False):
        print(json.dumps({"starting": label}), flush=True)
        start = time.monotonic()
        row = {"label": label, "argv": argv, "started_utc": datetime.now(timezone.utc).isoformat(),
               "returncode": None, "error": None, "anonymous_git_configuration": anonymous}
        try:
            result = subprocess.run(argv, cwd=DESTINATION, env=environment if anonymous else normal,
                stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
            row.update(returncode=result.returncode, stdout=captured(result.stdout), stderr=captured(result.stderr))
        except subprocess.TimeoutExpired as error:
            row.update(error={"type": "TimeoutExpired", "message": "command deadline exceeded"},
                       stdout=captured(error.stdout or b""), stderr=captured(error.stderr or b""))
        row.update(finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic() - start)
        save(DESTINATION / (label + ".json"), row)
        commands.append(row)
        if row["returncode"] != 0 or row["error"] is not None:
            raise ValueError("preserved command failed: " + label)
        return base64.b64decode(row["stdout"]["base64"])

    record = {"schema": "zerorun.current-public-guide-reproduction.053.v1", "passed": False,
        "repository": URL, "requested_commit": args.commit, "source": str(source),
        "destination": str(DESTINATION), "started_utc": datetime.now(timezone.utc).isoformat(),
        "clone_method": "new anonymous HTTPS clone, shallow codex/softwarex-053-finalization checkout pinned to exact public commit",
        "git_configuration_isolated": True, "credentials_supplied_to_git": False,
        "model_called": False, "payment_made": False, "real_repository_authorized": False,
        "fixture_authority_scope": "original built-in two-file synthetic laboratory fixture only",
        "independent_human_user_claimed": False, "failure": None}
    try:
        invoke("anonymous-clone", git + ["clone", "--depth", "1", "--no-checkout", "--branch", "codex/softwarex-053-finalization", URL, str(source)], anonymous=True)
        invoke("pinned-checkout", git + ["-C", str(source), "checkout", "--detach", args.commit], anonymous=True)
        head = invoke("head-before", git + ["-C", str(source), "rev-parse", "HEAD"], anonymous=True).decode().strip()
        status = invoke("status-before", git + ["-C", str(source), "status", "--porcelain=v2", "--untracked-files=all"], anonymous=True)
        if head != args.commit or status:
            raise ValueError("anonymous source checkout is not clean at requested commit")
        record.update(source_commit=head, source_clean_before=True)
        record["public_manifest_sha256"] = hashlib.sha256((source / "PUBLIC_RELEASE_MANIFEST.json").read_bytes()).hexdigest()
        if record["public_manifest_sha256"] != args.manifest_sha256:
            raise ValueError("public manifest differs from anonymous host verification")
        print(json.dumps({"anonymous_checkout_verified": True, "source_commit": head,
                          "source_clean": True, "public_manifest_sha256": record["public_manifest_sha256"]}), flush=True)
        helper = source / "research/softwarex/quickstart_053.py"
        invoke("documented-workflow", [sys.executable, "-I", "-B", str(helper),
            "--source-root", str(source), "--create-tools-env", str(DESTINATION / "tools"),
            "--installation-output", str(DESTINATION / "install.json"),
            "--output", str(DESTINATION / "check.json"), "--approve-synthetic-formative-authority"], timeout=900)
        verified = invoke("read-only-validation", [sys.executable, "-I", "-B", str(helper),
            "--source-root", str(source), "--check", str(DESTINATION / "check.json"),
            "--installation-receipt", str(DESTINATION / "install.json")], timeout=120)
        record["read_only_validation"] = json.loads(verified)
        head_after = invoke("head-after", git + ["-C", str(source), "rev-parse", "HEAD"], anonymous=True).decode().strip()
        status_after = invoke("status-after", git + ["-C", str(source), "status", "--porcelain=v2", "--untracked-files=all"], anonymous=True)
        if head_after != args.commit or status_after or record["read_only_validation"].get("passed") is not True:
            raise ValueError("public source changed or receipt validation failed")
        record.update(source_clean_after=True, passed=True)
    except Exception as error:
        record["failure"] = {"type": type(error).__name__, "message": str(error)}
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()
    record["commands"] = [{"path": row["label"] + ".json", "sha256": hashlib.sha256(
        (DESTINATION / (row["label"] + ".json")).read_bytes()).hexdigest()} for row in commands]
    save(DESTINATION / "checkout.json", record)
    print(json.dumps({"passed": record["passed"], "destination": str(DESTINATION), "failure": record["failure"]}), flush=True)
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
