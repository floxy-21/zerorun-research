"""Bounded, account-free Linux lab check of an external ZeroRun installation.

Only the original built-in synthetic fixture may be authorized. This is an
internal installation/conformance reproduction, not a developer or agent study.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

SCHEMA = "zerorun.softwarex-quickstart-lab.v1"
IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
CORE_IDENTITY = "0bb214d1c9b33a2e3b7a3aa81e8f2469024cb6ddaab46c3fa41a9272502a57d5"
PACKAGING = ("pyproject.toml", "README.md", "LICENSE.txt", "Licence.txt", "THIRD_PARTY_NOTICES.md")
STAGES = ("negative_doctor", "explicit_doctor", "miss", "hit", "verify")
LIMIT = 2 * 1024 * 1024
HELPERS = {
    "tools/codex_agent_lifecycle.py": "9db6ac92823ddeb07505d4e982592c28f0e5252c444c890c53e8e26718923805",
    "tools/codex_agent_integration_smoke.py": "53a3c86f88aef0cee7a63571a5c22984b17d92831cb746b434f3e6ed5744f328",
    "tools/install_verified_codex_cli.py": "dfd2eab91d114c6d10847092da338d374b8d7833020a473d9b63edb8bc13fae3",
    "tools/aggregate_codex_install_evidence.py": "a5eafbb44d36751bcf84dc353a6625a02b9dfb08e8a04a95e6b37a3bb689fc21",
    "research/softwarex/diagnose_mcp_authority.py": "a6fd6fa7802aa99798ab1f707b4ccd65d8c8e735c0f31b0d63296e6b1e1ccb00",
    "research/softwarex/run_public_lifecycle.py": "f79ad5910223213fdf8a3acd4e1117f8a1ca31d7f1ed066cdd846bac7fc914c2",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def real_path(path, *, directory=False):
    path = Path(os.path.abspath(Path(path).expanduser()))
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
            or path.resolve(strict=True) != path
            or (not stat.S_ISDIR(info.st_mode) if directory else not stat.S_ISREG(info.st_mode))):
        raise ValueError("expected a real non-linked " + ("directory: " if directory else "file: ") + str(path))
    return path


def validate_output(path, root):
    path = Path(os.path.abspath(Path(path).expanduser()))
    real_path(path.parent, directory=True)
    if path.exists() or path.is_symlink() or path.is_relative_to(root):
        raise ValueError("output must be a new file outside the public source checkout")
    return path


def source_bindings(root):
    """Check the shipped helper bytes, not historic receipts or old adapters."""
    root = real_path(root, directory=True)
    for relative, expected in HELPERS.items():
        path = real_path(root / relative)
        if path.stat().st_size > 512 * 1024 or digest(path) != expected:
            raise ValueError("public helper bytes differ: " + relative)
    manifest = real_path(root / "PUBLIC_RELEASE_MANIFEST.json")
    if manifest.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("public release manifest exceeds bound")
    return {"public_manifest_sha256": digest(manifest), "helpers_sha256": HELPERS.copy()}


def bounded_setup_command(command, *, environment, cwd=None, timeout=300):
    """Bootstrap without importing ZeroRun; cap both on-disk output streams."""
    started = time.monotonic()
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, start_new_session=os.name == "posix")
        timed_out = output_exceeded = False
        while process.poll() is None:
            timed_out = time.monotonic() - started > timeout
            output_exceeded = any(os.fstat(stream.fileno()).st_size > LIMIT for stream in (stdout, stderr))
            if timed_out or output_exceeded:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                break
            time.sleep(0.02)
        process.wait(timeout=10)
        streams = {}
        import base64
        for name, stream in (("stdout", stdout), ("stderr", stderr)):
            size = os.fstat(stream.fileno()).st_size
            stream.seek(0)
            raw = stream.read(LIMIT)
            streams.update({name + "_base64": base64.b64encode(raw).decode(), name + "_bytes": len(raw),
                name + "_sha256": hashlib.sha256(raw).hexdigest(), name + "_truncated": size > LIMIT})
        return {"command": list(map(str, command)), "returncode": process.returncode,
                "timed_out": timed_out, "output_limit_exceeded": output_exceeded,
                "elapsed_seconds": round(time.monotonic() - started, 6), **streams}


def require_setup_success(row):
    if (row["returncode"] != 0 or row["timed_out"] or row["output_limit_exceeded"]
            or row["stdout_truncated"] or row["stderr_truncated"]):
        raise ValueError("clean-install command failed, timed out, or exceeded output bound")


def create_installation(args):
    """Create one new external venv; preserve installation evidence separately."""
    import base64
    root = real_path(args.source_root, directory=True)
    output = validate_output(args.installation_output, root)
    tools_env = Path(os.path.abspath(args.create_tools_env))
    real_path(tools_env.parent, directory=True)
    if (tools_env.exists() or tools_env.is_symlink() or tools_env.is_relative_to(root)
            or root.is_relative_to(tools_env)):
        raise ValueError("the tools environment must be a new external directory")
    receipt = {"schema": "zerorun.softwarex-quickstart-install.v1", "passed": False,
        "started_utc": datetime.now(timezone.utc).isoformat(), "helper_sha256": digest(__file__),
        "source_root": str(root), "tools_environment": str(tools_env), "commands": [],
        "model_called": False, "real_repository_authorized": False,
        "role": "internal clean installation; not external developer evidence",
        "operator_reported_interventions": args.intervention, "automatic_retry_count": 0,
        "timing_scope": "venv creation and pip installation plus prerequisite/source checks; clone excluded"}
    started = time.monotonic()
    try:
        if platform.system() != "Linux":
            raise ValueError("the fresh installation mode is a Linux laboratory workflow")
        receipt["source_bindings"] = source_bindings(root)
        environment = {name: value for name, value in os.environ.items()
                       if name in {"PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
        environment.update(PYTHONNOUSERSITE="1", GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
        git = shutil.which("git")
        if not git:
            raise ValueError("Git is required before installation")
        def command(argv):
            row = bounded_setup_command(list(map(str, argv)), environment=environment)
            receipt["commands"].append(row)
            require_setup_success(row)
            return base64.b64decode(row["stdout_base64"])
        def snapshot():
            head = command([git, "-C", root, "rev-parse", "HEAD"]).decode().strip()
            status = command([git, "-C", root, "status", "--porcelain=v2", "--untracked-files=all"])
            if len(head) != 40 or any(c not in "0123456789abcdef" for c in head) or status:
                raise ValueError("installation requires a clean committed public source clone")
            files = [{"path": path.name, "bytes": path.stat().st_size, "sha256": digest(real_path(path))}
                     for path in sorted((root / "src/zerorun").glob("*.py"))]
            if len(files) != 36 or hashlib.sha256(canonical(files)).hexdigest() != CORE_IDENTITY:
                raise ValueError("source runtime file count or exact byte inventory differs")
            return {"commit": head, "status_clean": True, "runtime_files": files,
                    "packaging_files": [{"path": name, "bytes": real_path(root / name).stat().st_size,
                                         "sha256": digest(root / name)} for name in PACKAGING],
                    "public_manifest_sha256": digest(root / "PUBLIC_RELEASE_MANIFEST.json")}
        receipt["source_before"] = snapshot()
        # --isolated ignores user pip config/env; a fresh temporary HOME also avoids user caches.
        with tempfile.TemporaryDirectory(prefix="zerorun-install-home-") as setup_home:
            environment.update(HOME=setup_home, USERPROFILE=setup_home)
            build_source = Path(setup_home) / "build-source"
            (build_source / "src/zerorun").mkdir(parents=True)
            copy_rows = []
            names = list(PACKAGING) + ["src/zerorun/" + row["path"] for row in receipt["source_before"]["runtime_files"]]
            for name in names:
                original, copied = real_path(root / name), build_source / name
                shutil.copy2(original, copied)
                row = {"path": name, "bytes": copied.stat().st_size, "sha256": digest(copied)}
                if row["sha256"] != digest(original) or row["bytes"] != original.stat().st_size:
                    raise ValueError("isolated build copy differs from public source: " + name)
                copy_rows.append(row)
            receipt["isolated_build_copy"] = {"path": str(build_source), "files": copy_rows,
                                               "source_checkout_written": False}
            command([sys.executable, "-I", "-m", "venv", tools_env])
            python, zerorun = tools_env / "bin/python", tools_env / "bin/zerorun"
            command([python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                     "install", "--no-input", "--no-cache-dir", build_source])
            command([zerorun, "--version"])
        receipt["isolated_build_copy"]["cleaned"] = not build_source.exists()
        receipt["source_after"] = snapshot()
        if receipt["source_before"] != receipt["source_after"] or source_bindings(root) != receipt["source_bindings"]:
            raise ValueError("source changed while installing")
        receipt.update(passed=True, python_command=str(python), zerorun_command=str(zerorun))
    except Exception as exc:
        receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
    receipt["elapsed_seconds"] = round(time.monotonic() - started, 6)
    receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
    receipt["evidence_payload_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    with output.open("xb") as handle:
        handle.write(canonical(receipt) + b"\n")
    return receipt


def load_helpers(root):
    """Use the externally installed runtime; never add root/src to sys.path."""
    source_bindings(root)
    sys.path.insert(0, str(root))
    modules = {}
    for relative in HELPERS:
        name = relative[:-3].replace("/", ".")
        module = importlib.import_module(name)
        if Path(module.__file__).resolve(strict=True) != root / relative:
            raise ValueError("a different helper was imported: " + name)
        modules[relative] = module
    return (modules["tools/codex_agent_lifecycle.py"],
            modules["tools/codex_agent_integration_smoke.py"],
            modules["research/softwarex/diagnose_mcp_authority.py"])


def recorded_runner(runner, encode, records):
    def recorded(command, **kwargs):
        started = time.monotonic()
        row = {"command": list(map(str, command)), "cwd": str(kwargs["cwd"]) if kwargs.get("cwd") else None}
        records.append(row)
        try:
            result = runner(command, **kwargs)
            row["streams"] = encode(result)
            return result
        except Exception as exc:
            row["exception"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            raise
        finally:
            row["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return recorded


def validate_image(raw):
    rows = json.loads(raw)
    if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict)
            or rows[0].get("Os") != "linux" or rows[0].get("Architecture") != "amd64"
            or not isinstance(rows[0].get("RepoDigests"), list)
            or not any(isinstance(item, str) and item.endswith("@" + IMAGE.split("@", 1)[1])
                       for item in rows[0]["RepoDigests"])):
        raise ValueError("the already-present image does not match Linux/amd64 and the fixed digest")
    return rows[0]


def validate_core(source, installed):
    if (source.get("file_count") != 36 or source.get("identity_sha256") != CORE_IDENTITY
            or hashlib.sha256(canonical(source.get("files"))).hexdigest() != CORE_IDENTITY
            or installed.get("module", {}).get("files") != source.get("files")
            or installed["module"].get("identity_sha256") != CORE_IDENTITY):
        raise ValueError("the exact 36-file installed runtime differs from the frozen public core")


def assert_authorization(response, manifest_sha, before, after):
    expected = {"status": "AUTHORIZED", "manifest_sha256": manifest_sha,
                "pytest_profile_sha256": None, "authority_location": "external-per-user"}
    if response != expected or before.get("present") is not False or after.get("file_count") != 2:
        raise ValueError("synthetic-only authorization response or isolated authority differs")


def check(args):
    root = real_path(args.source_root, directory=True)
    output = validate_output(args.output, root)
    receipt = {"schema": SCHEMA, "started_utc": datetime.now(timezone.utc).isoformat(),
        "helper_sha256": digest(__file__), "quickstart_sha256": digest(Path(__file__).with_name("QUICKSTART_LAB.md")),
        "source_root": str(root), "passed": False, "model_called": False,
        "real_repository_authorized": False, "external_developer_study": False,
        "runtime_acquisition_attempted": False, "automatic_retry_count": 0,
        "role": "internal clean-install synthetic conformance reproduction",
        "timing_scope": "this checker only; clone and pip install are not included",
        "operator_reported_interventions": args.intervention,
        "commands": [], "stages": []}
    started, temporary = time.monotonic(), None
    try:
        if args.installation_receipt:
            install_path = real_path(args.installation_receipt)
            installation = json.loads(install_path.read_bytes())
            expected_installation = hashlib.sha256(canonical({k: v for k, v in installation.items()
                if k != "evidence_payload_sha256"})).hexdigest()
            if (installation.get("schema") != "zerorun.softwarex-quickstart-install.v1"
                    or installation.get("passed") is not True
                    or installation.get("evidence_payload_sha256") != expected_installation
                    or installation.get("python_command") != args.zerorun_python_command
                    or installation.get("zerorun_command") != args.zerorun_command
                    or installation.get("source_root") != str(root)
                    or installation.get("helper_sha256") != receipt["helper_sha256"]):
                raise ValueError("fresh-install receipt does not bind this checker/source/installation")
            receipt["installation_receipt"] = {"path": str(install_path), "sha256": digest(install_path),
                "elapsed_seconds": installation["elapsed_seconds"], "source_commit": installation["source_after"]["commit"]}
        if not args.approve_synthetic_formative_authority:
            raise ValueError("explicit --approve-synthetic-formative-authority is required")
        if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
            raise ValueError("this synthetic execution check requires Linux/amd64")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise ValueError("run this laboratory check as a non-root user")
        receipt["source_bindings"] = source_bindings(root)
        lifecycle, smoke, diagnostic = load_helpers(root)
        runner = recorded_runner(smoke._default_runner, smoke._encoded_stream, receipt["commands"])
        environment, removed = smoke._sanitize_environment(dict(os.environ))
        environment.pop("ZERORUN_TRUST_ROOT", None)
        receipt["environment"] = {"platform": platform.platform(), "python_version": platform.python_version(),
                                  "removed_variable_names": removed, "secret_values_recorded": False}
        git, docker = shutil.which("git"), shutil.which("docker")
        if not git or not docker:
            raise ValueError("Git and an operator-controlled Docker daemon are prerequisites")
        _, zerorun = smoke._resolve_executable(args.zerorun_command, name="installed ZeroRun", repository_root=root)
        receipt["installed_launcher"] = str(zerorun)
        source_before = smoke._git_snapshot(runner, git=git, root=root, environment=environment)
        receipt["source_before"] = source_before
        if source_before["status"]["bytes"] or source_before["diff_from_head"]["bytes"]:
            raise ValueError("use a clean public clone; source checkout contains changes")
        source_package = lifecycle._source_package_identity(root / "src")
        installed = lifecycle._installed_package_identity(runner, zerorun=zerorun,
            python_command=args.zerorun_python_command, source_root=root, environment=environment)
        receipt.update(source_package=source_package, installed_before=installed)
        validate_core(source_package, installed)
        for name, command in (("git", [git, "--version"]), ("zerorun", [str(zerorun), "--version"]),
                              ("docker", [docker, "version", "--format", "{{json .Server.Version}}"] )):
            result = smoke._run_small(runner, command, label=name + " prerequisite", cwd=None, environment=environment)
            receipt.setdefault("prerequisites", {})[name] = result.stdout.decode("utf-8").strip()
        # Inspect only identity/platform fields; never publish arbitrary image environment or labels.
        result = smoke._run_small(runner, [docker, "image", "inspect", IMAGE, "--format",
            '[{"Id":{{json .Id}},"RepoDigests":{{json .RepoDigests}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}]'],
            label="already-present pinned runtime", cwd=None, environment=environment)
        receipt["prerequisites"]["image"] = validate_image(result.stdout)
        with lifecycle.private_temporary_directory(Path(tempfile.gettempdir()), prefix="zerorun-quickstart-lab-") as temporary:
            temporary = real_path(temporary, directory=True)
            repository, trust, empty_home = temporary / "repository", temporary / "external-authority", temporary / "empty-home"
            empty_home.mkdir()
            env = dict(environment, HOME=str(empty_home), USERPROFILE=str(empty_home))
            for name in ("ZERORUN_TRUST_ROOT", "XDG_STATE_HOME", "LOCALAPPDATA"):
                env.pop(name, None)
            explicit = dict(env, ZERORUN_TRUST_ROOT=str(trust))
            fixture = lifecycle._create_synthetic_repository(runner, root=repository, runtime_image=IMAGE, git=git, environment=env)
            manifest_sha = digest(repository / ".zerorun.json")
            before = lifecycle._authority_tree_identity(trust)
            receipt["synthetic"] = {"path": str(repository), "manifest_sha256": manifest_sha,
                "fixture_identity": fixture, "runtime_image": IMAGE, "authority_before": before,
                "built_in_fixture_only": True, "external_authority_path": str(trust)}
            result = smoke._run_small(runner, [str(zerorun), "--manifest", str(repository / ".zerorun.json"),
                "--json", "authorize", "--manifest-sha256", manifest_sha],
                label="explicitly approved built-in fixture authority", cwd=temporary, environment=explicit)
            response = smoke._load_object(result.stdout, label="synthetic authorization")
            after = lifecycle._authority_tree_identity(trust)
            receipt["synthetic"].update(authorization=response, authority_after=after)
            assert_authorization(response, manifest_sha, before, after)
            keys = []
            for name in STAGES:
                requests = diagnostic.request_plan(name, repository)
                stamp = time.monotonic()
                result = runner([str(zerorun), "mcp-server"], cwd=repository,
                    environment=env if name == "negative_doctor" else explicit,
                    timeout_seconds=120.0, output_limit_bytes=LIMIT,
                    input_bytes=b"".join(canonical(row) + b"\n" for row in requests))
                stage = {"name": name, "requests": requests, "explicit_trust_root": name != "negative_doctor",
                         "streams": smoke._encoded_stream(result), "elapsed_seconds": round(time.monotonic() - stamp, 6)}
                receipt["stages"].append(stage)
                analyzed = diagnostic.validate_exchange(stage, repository)
                stage["responses"] = analyzed["responses"]
                if analyzed["cache_key"] is not None:
                    keys.append(analyzed["cache_key"])
                if name == "negative_doctor" and (repository / ".zerorun").exists():
                    raise ValueError("missing-authority control created unexpected cache state")
                if lifecycle._synthetic_source_identity(runner, git=git, root=repository, environment=env, runtime_image=IMAGE) != fixture:
                    raise ValueError("built-in fixture source changed")
            if len(set(keys)) != 1 or lifecycle._authority_tree_identity(trust) != after:
                raise ValueError("shared cache-key identity or authority stability failed")
            receipt["synthetic"]["unchanged_inputs_and_authority"] = True
        receipt["source_after"] = smoke._git_snapshot(runner, git=git, root=root, environment=environment)
        receipt["installed_after"] = lifecycle._installed_package_identity(runner, zerorun=zerorun,
            python_command=args.zerorun_python_command, source_root=root, environment=environment)
        if (receipt["source_after"] != source_before or receipt["installed_after"] != installed
                or source_bindings(root) != receipt["source_bindings"] or temporary.exists()):
            raise ValueError("source/installed/helper postflight identity or cleanup differs")
        receipt["passed"] = True
    except Exception as exc:
        receipt["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
    finally:
        receipt["temporary_fixture_and_authority_cleaned"] = temporary is not None and not temporary.exists()
        receipt["elapsed_seconds"] = round(time.monotonic() - started, 6)
        receipt["finished_utc"] = datetime.now(timezone.utc).isoformat()
        receipt["evidence_payload_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
        with output.open("xb") as handle:
            handle.write(canonical(receipt) + b"\n")
    return receipt


def validate_receipt(value, diagnostic):
    """Recompute archived stream evidence. No execution or authority operations."""
    expected = hashlib.sha256(canonical({k: v for k, v in value.items() if k != "evidence_payload_sha256"})).hexdigest()
    if value.get("schema") != SCHEMA or value.get("evidence_payload_sha256") != expected:
        raise ValueError("quickstart receipt schema or hash differs")
    for field in ("model_called", "real_repository_authorized", "external_developer_study", "runtime_acquisition_attempted"):
        if value.get(field) is not False:
            raise ValueError("quickstart receipt exceeds fixed scope: " + field)
    if (value.get("automatic_retry_count") != 0 or type(value.get("elapsed_seconds")) not in (int, float)
            or not math.isfinite(value["elapsed_seconds"]) or value["elapsed_seconds"] < 0):
        raise ValueError("invalid quickstart retry or elapsed-time value")
    stages = value.get("stages", [])
    if [row.get("name") for row in stages] != list(STAGES[:len(stages)]) or len(stages) > len(STAGES):
        raise ValueError("quickstart stage order or denominator differs")
    if value.get("passed") is True:
        if len(stages) != 5 or value.get("temporary_fixture_and_authority_cleaned") is not True:
            raise ValueError("incomplete quickstart cannot pass")
        validate_core(value["source_package"], value["installed_before"])
        if value["source_before"] != value["source_after"] or value["installed_before"] != value["installed_after"]:
            raise ValueError("quickstart postflight identity differs")
        synthetic = value["synthetic"]
        assert_authorization(synthetic["authorization"], synthetic["manifest_sha256"],
                             synthetic["authority_before"], synthetic["authority_after"])
        if synthetic.get("built_in_fixture_only") is not True or synthetic.get("unchanged_inputs_and_authority") is not True:
            raise ValueError("quickstart fixture scope or stability differs")
        keys = [diagnostic.validate_exchange(stage, synthetic["path"])["cache_key"] for stage in stages]
        if keys[0] is not None or len(set(keys[1:])) != 1 or keys[1] is None:
            raise ValueError("quickstart did not retain one shared cache key")
    elif value.get("passed") is not False or not isinstance(value.get("failure"), dict):
        raise ValueError("adverse quickstart has no failure record")
    return {"passed": value["passed"], "stages_recorded": len(stages), "model_called": False,
            "external_developer_study": False}


def checked_envelope(path, schema):
    path = real_path(path)
    if path.stat().st_size > 24 * 1024 * 1024:
        raise ValueError("quickstart evidence exceeds the 24-MiB receipt bound")
    def pairs(rows):
        value = {}
        for name, item in rows:
            if name in value:
                raise ValueError("duplicate JSON field in quickstart receipt")
            value[name] = item
        return value
    value = json.loads(path.read_bytes(), object_pairs_hook=pairs)
    expected = hashlib.sha256(canonical({k: v for k, v in value.items() if k != "evidence_payload_sha256"})).hexdigest()
    if value.get("schema") != schema or value.get("evidence_payload_sha256") != expected:
        raise ValueError("quickstart envelope schema or hash differs")
    return value


def validate_streams(row, *, require_success=True):
    import base64
    if (type(row.get("returncode")) is not int or type(row.get("timed_out")) is not bool
            or type(row.get("elapsed_seconds", 0)) not in (int, float)
            or not math.isfinite(row.get("elapsed_seconds", 0)) or row.get("elapsed_seconds", 0) < 0):
        raise ValueError("invalid command result or elapsed time")
    decoded = {}
    for name in ("stdout", "stderr"):
        raw = base64.b64decode(row[name + "_base64"], validate=True)
        if (len(raw) > LIMIT or type(row.get(name + "_bytes")) is not int
                or len(raw) != row[name + "_bytes"]
                or hashlib.sha256(raw).hexdigest() != row.get(name + "_sha256")
                or type(row.get(name + "_truncated")) is not bool):
            raise ValueError("recorded command output hash, length, or flags differ")
        decoded[name] = raw
    if require_success and (row["returncode"] or row["timed_out"]
            or row.get("output_limit_exceeded", False) or row["stdout_truncated"] or row["stderr_truncated"]):
        raise ValueError("passing installation has an adverse command")
    return decoded


def validate_installation_receipt(root, receipt_path):
    """Read-only replay of install provenance; independent of moving main manifest."""
    root = Path(root)
    value = checked_envelope(receipt_path, "zerorun.softwarex-quickstart-install.v1")
    producer = root / "research/softwarex/quickstart_check.py"
    if value.get("helper_sha256") != digest(producer):
        raise ValueError("installation producer differs from the published helper")
    if (value.get("model_called") is not False or value.get("real_repository_authorized") is not False
            or value.get("automatic_retry_count") != 0 or type(value.get("passed")) is not bool):
        raise ValueError("installation scope or outcome differs")
    commands = value.get("commands", [])
    for row in commands:
        validate_streams(row, require_success=value["passed"])
    if value["passed"]:
        before, after = value["source_before"], value["source_after"]
        if (before != after or before.get("status_clean") is not True
                or len(before["runtime_files"]) != 36
                or hashlib.sha256(canonical(before["runtime_files"])).hexdigest() != CORE_IDENTITY
                or value["source_bindings"].get("helpers_sha256") != HELPERS
                or before["public_manifest_sha256"] != value["source_bindings"]["public_manifest_sha256"]):
            raise ValueError("installation source identity differs")
        copied = value["isolated_build_copy"]
        expected_copy = before["packaging_files"] + [dict(row, path="src/zerorun/" + row["path"])
                                                     for row in before["runtime_files"]]
        if ([row["path"] for row in before["packaging_files"]] != list(PACKAGING)
                or copied["files"] != expected_copy or copied.get("cleaned") is not True
                or copied.get("source_checkout_written") is not False):
            raise ValueError("isolated install source copy differs or was not cleaned")
        if len(commands) != 7:
            raise ValueError("installation requires exactly seven bounded commands")
        source, env = value["source_root"], value["tools_environment"]
        python, zerorun = env + "/bin/python", env + "/bin/zerorun"
        git = commands[0]["command"][0]
        expected = [
            [git, "-C", source, "rev-parse", "HEAD"],
            [git, "-C", source, "status", "--porcelain=v2", "--untracked-files=all"],
            [commands[2]["command"][0], "-I", "-m", "venv", env],
            [python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--no-input", "--no-cache-dir", copied["path"]],
            [zerorun, "--version"],
            [git, "-C", source, "rev-parse", "HEAD"],
            [git, "-C", source, "status", "--porcelain=v2", "--untracked-files=all"],
        ]
        if [row["command"] for row in commands] != expected or value.get("python_command") != python or value.get("zerorun_command") != zerorun:
            raise ValueError("installation command sequence or installed launcher differs")
        outputs = [validate_streams(row)["stdout"] for row in commands]
        if (outputs[0].decode().strip() != before["commit"] or outputs[5] != outputs[0]
                or outputs[1] != b"" or outputs[6] != b"" or outputs[4].strip() != b"zerorun 0.5.1"):
            raise ValueError("installation recorded Git/version results differ")
    elif not isinstance(value.get("failure"), dict):
        raise ValueError("failed installation has no failure record")
    return {"passed": value["passed"], "commands_recorded": len(commands), "model_called": False,
        "source_commit": value.get("source_before", {}).get("commit"),
        "elapsed_seconds": value["elapsed_seconds"], "external_developer_study": False}


def validate_saved_receipt(root, receipt_path):
    """Read-only replay of actual STDIO evidence, exact sources and producer."""
    root = Path(root).resolve()
    value = checked_envelope(receipt_path, SCHEMA)
    if (value.get("helper_sha256") != digest(root / "research/softwarex/quickstart_check.py")
            or value.get("quickstart_sha256") != digest(root / "research/softwarex/QUICKSTART_LAB.md")):
        raise ValueError("quickstart producer or documented procedure differs")
    diagnostic_path = real_path(root / "research/softwarex/diagnose_mcp_authority.py")
    if digest(diagnostic_path) != HELPERS["research/softwarex/diagnose_mcp_authority.py"]:
        raise ValueError("STDIO evidence validator differs")
    sys.path.insert(0, str(root))
    diagnostic = importlib.import_module("research.softwarex.diagnose_mcp_authority")
    if Path(diagnostic.__file__).resolve() != diagnostic_path:
        raise ValueError("a different STDIO evidence validator was imported")
    summary = validate_receipt(value, diagnostic)
    if value["passed"]:
        if value["source_bindings"].get("helpers_sha256") != HELPERS:
            raise ValueError("quickstart immutable helper inventory differs")
        validate_image(json.dumps([value["prerequisites"]["image"]]))
        server_rows = []
        for row in value["commands"]:
            if "streams" not in row:
                raise ValueError("passing quickstart contains a subprocess exception")
            validate_streams(dict(row["streams"], elapsed_seconds=row["elapsed_seconds"]), require_success=False)
            if row["streams"]["timed_out"] or row["streams"]["stdout_truncated"] or row["streams"]["stderr_truncated"]:
                raise ValueError("passing quickstart contains a timeout or truncated command")
            if row["command"] == [value["installed_launcher"], "mcp-server"]:
                server_rows.append(row)
        if len(server_rows) != 5 or [row["streams"] for row in server_rows] != [row["streams"] for row in value["stages"]]:
            raise ValueError("actual server command streams differ from the five analyzed stages")
        if "installation_receipt" in value and value["installation_receipt"]["source_commit"] != value["source_before"]["head"]:
            raise ValueError("installation and lab check used different source commits")
    return {**summary, "source_commit": value.get("source_before", {}).get("head"),
            "elapsed_seconds": value["elapsed_seconds"], "runtime_files": value.get("source_package", {}).get("file_count", 0)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--zerorun-command")
    parser.add_argument("--zerorun-python-command")
    parser.add_argument("--create-tools-env", type=Path,
                        help="create a NEW external venv and preserve a separate installation receipt")
    parser.add_argument("--installation-output", type=Path)
    parser.add_argument("--installation-receipt", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--approve-synthetic-formative-authority", action="store_true")
    parser.add_argument("--intervention", action="append", default=[],
                        help="operator-reported prerequisite/setup intervention; repeatable; never include secrets")
    args = parser.parse_args(argv)
    if args.create_tools_env:
        if not args.installation_output or args.zerorun_command or args.zerorun_python_command or args.installation_receipt:
            parser.error("fresh-install mode requires --installation-output and no existing-install arguments")
        if not args.approve_synthetic_formative_authority:
            parser.error("review and supply the explicit built-in fixture permission before starting this workflow")
        validate_output(args.output, real_path(args.source_root, directory=True))
        if Path(os.path.abspath(args.output)) == Path(os.path.abspath(args.installation_output)):
            parser.error("installation and laboratory outputs must be different new files")
        install = create_installation(args)
        if not install["passed"]:
            print(json.dumps({"installation_receipt": str(args.installation_output), "passed": False,
                              "next_action": "inspect installation failure; keep the new environment for diagnosis"}))
            return 1
        command = [install["python_command"], "-I", "-B", str(Path(__file__).resolve()),
            "--source-root", str(args.source_root), "--zerorun-command", install["zerorun_command"],
            "--zerorun-python-command", install["python_command"], "--output", str(args.output),
            "--installation-receipt", str(args.installation_output), "--approve-synthetic-formative-authority"]
        for intervention in args.intervention:
            command.extend(["--intervention", intervention])
        return subprocess.call(command)
    if not args.zerorun_command or not args.zerorun_python_command or args.installation_output:
        parser.error("supply both installed commands, or use --create-tools-env with --installation-output")
    receipt = check(args)
    print(json.dumps({"receipt": str(args.output), "passed": receipt["passed"],
        "stages": len(receipt["stages"]), "elapsed_seconds": receipt["elapsed_seconds"],
        "next_action": "inspect archived raw results" if receipt["passed"] else "inspect failure; do not retry blindly"}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
