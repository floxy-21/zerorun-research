"""Bounded paired handoffs on public issue-derived states; never a model study."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
IMAGE = "docker.io/library/python@sha256:9c47360a2a0355e2da18516d0b1c2126ec22c195d2185e97347c9d98398c5bef"
CORE = "86f42289c2f59a74b1f642f0b20d1a26b3d55e57"
HELPER_SHA = "96588c65ee674597e4c459651345b9e89ac92cbdc0c85baaa3de4f57f6a44359"
SEED = "zerorun-public-issue-handoff-v1"
STATE = {".git", ".zerorun", ".zerorun.json"}
RESERVED = STATE | {".zerorun-env", "authorities", "private-cache-authentication-NOT-FOR-PUBLICATION"}
MAX_SOURCE_BYTES = 160 * 1024 * 1024
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def bad(value):
        raise ValueError("nonfinite JSON number")
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=bad)
    encoded(value)  # Also rejects overflowing JSON floats.
    return value


def literal(value):
    require(isinstance(value, str) and value and not any(c in value for c in "\\:\x00\r\n"), "unsafe relative path")
    parts = value.split("/")
    require(not PurePosixPath(value).is_absolute() and all(p not in {"", ".", ".."} for p in parts), "noncanonical path")
    return parts


def ordinary(path, limit=MAX_RECORD_BYTES):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and not getattr(info, "st_file_attributes", 0) & 0x400 and info.st_size <= limit,
            "bounded ordinary file required: " + str(path))
    raw = path.read_bytes()
    after = path.lstat()
    require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "file changed while reading")
    return raw


def record(path, relative=None):
    raw = ordinary(path)
    return {"path": relative or path.name, "bytes": len(raw), "sha256": sha(raw)}


def save(path, value):
    raw = json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def bound(base, row, limit=MAX_RECORD_BYTES):
    require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}, "file binding fields differ")
    path = base.joinpath(*literal(row["path"]))
    cursor = base
    for part in literal(row["path"]):
        cursor /= part
        require(not cursor.is_symlink(), "linked evidence path")
    raw = ordinary(path, limit)
    require(type(row["bytes"]) is int and len(raw) == row["bytes"] and sha(raw) == row["sha256"], "file binding mismatch")
    return raw


def order(case_id, block):
    require(block in (0, 1), "two blocks only")
    first = sha((SEED + "\0" + case_id).encode())[0] in "01234567"
    return ["fresh", "zerorun"] if first != bool(block) else ["zerorun", "fresh"]


def validate_ledger(ledger):
    require(ledger.get("schema") == "zerorun.handoff-selection.v1" and ledger.get("phase") in {"pilot", "main"}, "invalid ledger schema/phase")
    cases = ledger.get("cases")
    require(isinstance(cases, list) and 0 < len(cases) <= (2 if ledger["phase"] == "pilot" else 30), "case-count bound")
    ids = set()
    for case in cases:
        ident = case.get("case_id")
        require(isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", ident) and ident not in ids, "invalid/duplicate case ID")
        ids.add(ident)
        require(isinstance(case.get("repo"), str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", case["repo"]), "invalid repository")
        if case.get("disposition") == "UNAVAILABLE_ACQUISITION":
            require(isinstance(case.get("error"), str) and case["error"], "unavailable case needs reason")
            continue
        require(re.fullmatch(r"[0-9a-f]{40}", case.get("base_commit", "")) is not None, "invalid base commit")
        targets = case.get("targets")
        require(isinstance(targets, list) and 0 < len(targets) <= 16 and len(set(targets)) == len(targets), "reviewed target count invalid")
        for target in targets:
            parts = literal(target)
            require(parts[0] not in RESERVED and target.endswith(".py") and not target.startswith("-"), "target must be a literal Python source path")
    return cases


def metadata_row(raw, case):
    response = strict(raw)
    require(response.get("partial") is False, "partial metadata refused")
    rows = [r for r in response.get("rows", []) if r.get("row", {}).get("instance_id") == case["case_id"]]
    require(len(rows) == 1 and rows[0].get("truncated_cells") == [], "metadata absent, duplicate or truncated")
    row = rows[0]["row"]
    require(row.get("repo") == case["repo"] and row.get("base_commit") == case["base_commit"], "metadata case identity mismatch")
    require(isinstance(row.get("license_name"), str) and row["license_name"].strip(), "upstream license unidentified")
    require(isinstance(row.get("patch"), str) and row["patch"].strip()
            and isinstance(row.get("test_patch"), str), "reference/test patch absent")
    return row


def patch_paths(text):
    require(isinstance(text, str) and "\x00" not in text and len(text.encode()) <= 2 * 1024 * 1024, "patch bound")
    paths = []
    for line in text.splitlines():
        if line.startswith("diff --git "):
            match = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
            require(match is not None and match[1] == match[2], "renamed/quoted patch paths unsupported")
            name = match[1]
            require(literal(name)[0] not in RESERVED, "patch touches reserved laboratory state")
            paths.append(name)
        if line.startswith(("GIT binary patch", "Binary files", "rename ", "copy ", "old mode ")):
            raise ValueError("binary/rename/mode patch unsupported")
        if line.startswith(("new file mode ", "new mode ", "deleted file mode ")):
            require(line.rsplit(" ", 1)[-1] in {"100644", "100755"}, "patch contains link/special mode")
        if line.startswith(("--- ", "+++ ")):
            value = line[4:]
            if value != "/dev/null":
                require(value.startswith(("a/", "b/")), "unexpected unified patch path")
                require(literal(value[2:])[0] not in RESERVED, "patch header touches reserved state")
    require(not text.strip() or paths, "unsupported patch format")
    require(len(paths) == len(set(paths)), "duplicate diff paths")
    return paths


def extract_source(raw, destination, commit):
    require(len(raw) <= MAX_ARCHIVE_BYTES, "compressed source archive too large")
    rows, total, prefix = {}, 0, None
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for count, member in enumerate(archive, 1):
            require(count <= 20000, "source member count exceeded")
            parts = literal(member.name.rstrip("/"))
            prefix = prefix or parts[0]
            require(parts[0] == prefix and prefix.endswith(commit), "archive prefix/commit mismatch")
            require(member.isfile() or member.isdir(), "links/special source members refused")
            if len(parts) == 1:
                require(member.isdir(), "archive root is not directory")
                continue
            name = "/".join(parts[1:])
            require(parts[1] not in RESERVED and name not in rows, "reserved/duplicate source member")
            if member.isdir():
                rows[name] = None
            else:
                total += member.size
                require(0 <= member.size <= 32 * 1024 * 1024 and total <= MAX_SOURCE_BYTES, "uncompressed source byte bound")
                rows[name] = archive.extractfile(member).read(member.size + 1)
                require(len(rows[name]) == member.size, "archive declared/actual member size mismatch")
    require(rows, "empty source archive")
    destination.mkdir()
    for name, data in sorted(rows.items()):
        path = destination.joinpath(*literal(name))
        if data is None:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o644)
    return {"members": len(rows), "uncompressed_bytes": total, "source_archive_sha256": sha(raw)}


def identity(root):
    rows = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in STATE:
            continue
        info = path.lstat()
        require(not path.is_symlink() and not getattr(info, "st_file_attributes", 0) & 0x400, "source link refused")
        if path.is_dir():
            rows.append({"path": relative, "kind": "directory"})
        else:
            raw = ordinary(path)
            rows.append({"path": relative, "kind": "file", "bytes": len(raw), "sha256": sha(raw)})
    return {"sha256": sha(encoded(rows)), "rows": rows, "excluded_engine_state": sorted(STATE)}


def git(root, args, raw=None):
    executable = shutil.which("git")
    require(executable is not None, "Git unavailable")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GCM_"))}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    command = [executable, "-c", "core.hooksPath=/dev/null", *args]
    result = subprocess.run(command, cwd=root, env=env, input=raw, capture_output=True, timeout=30)
    row = {"argv": command, "returncode": result.returncode,
           "stdout": result.stdout.decode(errors="replace"), "stderr": result.stderr.decode(errors="replace")}
    require(result.returncode == 0, "inert Git source operation failed: " + row["stderr"][-2000:])
    return row


def make_state(case, ledger_base, output):
    row = metadata_row(bound(ledger_base, case["metadata"]), case)
    root = output / "source"
    extraction = extract_source(bound(ledger_base, case["source_archive"], MAX_ARCHIVE_BYTES), root, case["base_commit"])
    git_records = [git(root, ["init", "--template=", "--initial-branch=main", "."])]
    patch_records = []
    for key in ("test_patch", "patch"):
        text = row[key]
        paths = patch_paths(text)
        if text.strip():
            raw = text.encode()
            git_records.append(git(root, ["apply", "--check", "--whitespace=nowarn", "-"], raw))
            git_records.append(git(root, ["apply", "--whitespace=nowarn", "-"], raw))
        patch_records.append({"kind": key, "sha256": sha(text.encode()), "paths": paths})
    test_paths = set(patch_records[0]["paths"])
    require(set(case["targets"]) <= test_paths, "reviewed targets must occur in benchmark test_patch")
    require(all((root / t).is_file() for t in case["targets"]), "target absent after patches")
    source = identity(root)
    save(output / "source-preparation.json", {"case_id": case["case_id"], "extraction": extraction,
         "patches": patch_records, "git_operations": git_records, "identity": source,
         "metadata_license": row["license_name"], "original_environment_reproduced": False,
         "reference_patch_used": True, "agent_edits_claimed": False})
    return root


class Tail(io.RawIOBase):
    def __init__(self, maximum=1024 * 1024):
        self.maximum, self.total, self.raw = maximum, 0, b""
    def write(self, value):
        self.total += len(value)
        self.raw = (self.raw + value)[-self.maximum:]
        return len(value)
    def value(self):
        return {"text": self.raw.decode(errors="replace"), "captured_bytes": len(self.raw),
                "emitted_bytes": self.total, "truncated": self.total > self.maximum}


def outcome(capture):
    require(capture.get("complete_per_node_outcomes") is True, "oracle outcomes incomplete")
    nodes, values = capture["nodeids"], capture["outcomes"]
    require(nodes and len(nodes) == len(values) == len(set(nodes)), "oracle node identity absent/duplicate")
    require(type(capture["exit_code"]) is int, "oracle exit type invalid")
    return {"exit_code": capture["exit_code"], "nodes": dict(zip(nodes, values))}


def fresh_oracle(bench, root, targets, output):
    output.mkdir()
    support = output / "support"
    support.mkdir(mode=0o755)
    plugin = support / "benchmark_shadow_plugin.py"
    plugin.write_text(bench._SHADOW_PLUGIN, encoding="utf-8")
    target = output / "raw-outcomes.json"
    target.touch(exist_ok=False)
    target.chmod(0o666)
    result = bench._plain_pytest_container(root, targets=targets, runtime_image=IMAGE, support=support, shadow_output=target)
    raw = ordinary(target)
    capture = bench._validate_shadow_evidence(strict(raw))
    require(capture["exit_code"] == result["exit_code"], "oracle Docker/capture exit disagree")
    return {"runner": result, "capture": capture, "verdict": outcome(capture),
            "raw_outcomes": record(target, "raw-outcomes.json"), "plugin_sha256": sha(plugin.read_bytes())}


def operation(output, name, function):
    save(output / (name + ".started.json"), {"operation": name, "started_utc": utc()})
    started = time.perf_counter()
    row = {"operation": name, "result": None, "error": None}
    try:
        row["result"] = function()
    except Exception as error:
        row["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        row["outer_ms"] = (time.perf_counter() - started) * 1000
        row["completed_utc"] = utc()
        save(output / (name + ".json"), row)
    return row


def load_engine(engine):
    manifest_raw = ordinary(engine / "PUBLIC_RELEASE_MANIFEST.json", 8 * 1024 * 1024)
    manifest = strict(manifest_raw)
    require(manifest["frozen_core_commit"] == CORE, "unexpected runtime core")
    rows = [r for r in manifest["files"] if r["path"].startswith("src/zerorun/") and r["path"].endswith(".py")]
    require(len(rows) == len({r["path"] for r in rows}) == 36, "runtime inventory denominator")
    for row in rows:
        bound(engine, {k: row[k] for k in ("path", "bytes", "sha256")})
    helper = engine / "tools/product_generalization_benchmark.py"
    require(sha(ordinary(helper)) == HELPER_SHA, "frozen benchmark helper changed")
    sys.path[:0] = [str(engine / "src"), str(engine)]
    spec = importlib.util.spec_from_file_location("handoff_bound_benchmark", helper)
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    import zerorun
    from zerorun import api, oci
    from zerorun.manifest import load_manifest
    require(Path(zerorun.__file__).resolve() == (engine / "src/zerorun/__init__.py").resolve(), "runtime import escaped bound source")
    return bench, api, oci, load_manifest, {"public_manifest_sha256": sha(manifest_raw),
            "runtime_core_commit": CORE, "runtime_files": [{k: r[k] for k in ("path", "bytes", "sha256")} for r in rows],
            "helper": record(helper, "tools/product_generalization_benchmark.py"),
            "requirements": record(engine / "ci/generalization-runtime-requirements.txt", "ci/generalization-runtime-requirements.txt")}


def arm_workspace(bench, source, destination, layer, targets):
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git"))
    git(destination, ["init", "--template=", "--initial-branch=main", "."])
    setup = bench._materialize_frozen_pytest_environment(destination, dependency_layer=layer,
                runtime_image=IMAGE, extra_requirements=("pretend",))
    manifest_path = destination / ".zerorun.json"
    manifest = strict(manifest_path.read_bytes())
    task = manifest["tasks"].pop("pytest-generalization")
    task.update(cacheable=True, inputs=sorted(p.name for p in destination.iterdir() if p.name not in STATE))
    task["command"] += targets
    manifest["tasks"] = {"handoff-tests": task}
    manifest_path.write_bytes(encoded(manifest) + b"\n")
    return setup, manifest


def product(api, loaded, verify=False):
    stdout, stderr = Tail(), Tail()
    result = api.run_task(loaded, loaded.tasks["handoff-tests"], verify=verify,
                          stdout=stdout, stderr=stderr, lock_timeout_seconds=15)
    return {**result.as_dict(), "stdout": stdout.value(), "stderr": stderr.value()}


class MaterialMismatch(ValueError):
    pass


def check_equal(condition, message):
    if not condition:
        raise MaterialMismatch(message)


def summarize_block(rows):
    require(set(rows) == {"fresh", "zerorun"}, "paired arms missing")
    totals = {arm: sum(rows[arm][s]["outer_ms"] for s in ("producer", "consumer")) for arm in rows}
    require(all(math.isfinite(v) and v > 0 for v in totals.values()), "invalid outer timings")
    downstream = {arm: rows[arm]["consumer"]["outer_ms"] for arm in rows}
    inclusive = {arm: totals[arm] + rows[arm]["setup_outer_ms"] for arm in rows}
    return {"chain_ms": totals, "consumer_ms": downstream, "setup_inclusive_chain_ms": inclusive,
            "chain_saved_fraction": 1 - totals["zerorun"] / totals["fresh"],
            "consumer_saved_fraction": 1 - downstream["zerorun"] / downstream["fresh"],
            "setup_inclusive_saved_fraction": 1 - inclusive["zerorun"] / inclusive["fresh"],
            "setup_inclusive_scope": "per-arm source/environment preparation; common once-only dependency build and acquisition remain separate",
            "fresh_diagnostics_ms": rows["zerorun"]["diagnostics"]["outer_ms"],
            "oracle_ms": sum(rows[a]["oracle"]["outer_ms"] for a in rows),
            "cache_hit_observed": rows["zerorun"]["consumer"]["result"]["status"] == "HIT_REUSED"}


def run_case(case, ledger_base, output, components, layer, deadline):
    bench, api, _, load_manifest = components[:4]
    source = make_state(case, ledger_base, output)
    # A separate compatibility workspace is not a cache seed for either arm.
    preflight = output / "preflight-workspace"
    setup, _ = arm_workspace(bench, source, preflight, layer, case["targets"])
    save(output / "preflight-setup.json", setup)
    pre = operation(output, "compatibility-oracle", lambda: fresh_oracle(bench, preflight, case["targets"], output / "compatibility-capture"))
    require(pre["result"]["verdict"]["exit_code"] == 0, "repaired-state compatibility oracle failed")
    expected = pre["result"]["verdict"]
    require(any(v.get("call") == "passed" for v in expected["nodes"].values()), "compatibility target executed no passing test calls")
    blocks = []
    for block in (0, 1):
        require(time.monotonic() < deadline, "campaign budget ended before paired block")
        block_root = output / ("block-" + str(block))
        block_root.mkdir()
        rows = {}
        for arm in order(case["case_id"], block):
            require(time.monotonic() < deadline, "campaign budget ended before arm")
            arm_root = block_root / arm
            arm_root.mkdir()
            work = arm_root / "workspace"
            started = time.perf_counter()
            setup, manifest = arm_workspace(bench, source, work, layer, case["targets"])
            setup_ms = (time.perf_counter() - started) * 1000
            before = identity(work)
            save(arm_root / "setup.json", {"setup": setup, "manifest": manifest, "setup_outer_ms": setup_ms, "before": before})
            loaded = load_manifest(work / ".zerorun.json")
            invoke = (lambda: bench._direct_once(work, targets=case["targets"], runtime_image=IMAGE)) if arm == "fresh" else (lambda: product(api, loaded))
            producer = operation(arm_root, "producer", invoke)
            consumer = operation(arm_root, "consumer", invoke)
            oracle = operation(arm_root, "oracle", lambda: fresh_oracle(bench, work, case["targets"], arm_root / "oracle-capture"))
            check_equal(oracle["result"]["verdict"] == expected, "fresh oracle node/outcome mismatch")
            check_equal(producer["result"]["exit_code"] == consumer["result"]["exit_code"] == expected["exit_code"], "chain/fresh status disagreement")
            if arm == "zerorun":
                require(producer["result"]["status"] == "MISS_EXECUTED", "cold chain unavailable: producer did not begin with fresh cacheable execution")
                if consumer["result"]["status"] == "HIT_REUSED":
                    check_equal(producer["result"]["cache_key"] == consumer["result"]["cache_key"], "reused identity mismatch")
                diagnostics = operation(arm_root, "diagnostics", lambda: product(api, loaded, verify=True))
                check_equal(diagnostics["result"]["exit_code"] == expected["exit_code"], "fresh diagnostics/fresh oracle status disagree")
                require(diagnostics["result"]["status"] in {"VERIFY_MATCH", "MISS_EXECUTED"}, "fresh diagnostics route unavailable")
            else:
                diagnostics = None
            after = identity(work)
            save(arm_root / "source-after.json", after)
            check_equal(before == after, "source/environment changed during chain")
            row = {"arm": arm, "producer": producer, "consumer": consumer, "oracle": oracle,
                   "diagnostics": diagnostics, "setup_outer_ms": setup_ms,
                   "source_sha256": before["sha256"], "source_unchanged": True}
            save(arm_root / "summary.json", row)
            rows[arm] = row
        check_equal(rows["fresh"]["source_sha256"] == rows["zerorun"]["source_sha256"], "paired source identities differ")
        summary = {"block": block, "order": order(case["case_id"], block), "arms": rows, "measurements": summarize_block(rows)}
        save(block_root / "summary.json", summary)
        blocks.append(summary)
    return {"case_id": case["case_id"], "repo": case["repo"], "disposition": "COMPLETE",
            "blocks": blocks, "all_blocks_complete": True, "agent_session": False}


def code_inventory():
    return [record(p, p.name) for p in sorted(HERE.iterdir(), key=lambda p: p.name) if p.suffix in {".py", ".md"}]


def run(ledger_path, engine, output, execution_seconds=120, budget_seconds=7200):
    require(os.name == "posix", "Linux laboratory required")
    require(type(execution_seconds) is int and 1 <= execution_seconds <= 120, "execution limit outside fixed bound")
    require(type(budget_seconds) is int and 1 <= budget_seconds <= 14400, "campaign limit outside fixed bound")
    ledger_path, engine = ledger_path.resolve(strict=True), engine.resolve(strict=True)
    output = output.resolve(strict=False)
    require(not output.exists() and output.parent.exists() and not output.is_relative_to(engine), "new external output path required")
    ledger_raw = ordinary(ledger_path)
    ledger = strict(ledger_raw)
    cases = validate_ledger(ledger)
    output.mkdir()
    components = load_engine(engine)
    bench, api, oci, _, engine_identity = components
    sources = code_inventory()
    protocol = {"schema": "zerorun.controlled-handoff-run.v1", "started_utc": utc(),
        "selection": ledger, "selection_file": record(ledger_path), "selection_sha256": sha(ledger_raw),
        "engine": engine_identity, "study_sources": sources, "phase": ledger["phase"],
        "planned_cases": 2 if ledger["phase"] == "pilot" else 30, "planned_repositories": 10 if ledger["phase"] == "main" else None,
        "selected_cases": len(cases), "blocks_per_case": 2, "runtime_image": IMAGE,
        "execution_seconds": execution_seconds, "budget_seconds": budget_seconds,
        "execution_parameter_override": {"method": "invocation-local Python attribute replacement in research adapter",
            "bench._PYTEST_EXECUTION_TIMEOUT_SECONDS": [bench._PYTEST_EXECUTION_TIMEOUT_SECONDS, execution_seconds],
            "zerorun.oci.DOCKER_EXECUTION_TIMEOUT_SECONDS": [oci.DOCKER_EXECUTION_TIMEOUT_SECONDS, execution_seconds],
            "semantics": "symmetric execution time cap only; source bytes, cache identity, admission and result semantics unchanged"},
        "model_calls": 0, "mcp_authorities_created": False, "natural_hit_frequency_study": False}
    save(output / "protocol.json", protocol)
    deadline = time.monotonic() + budget_seconds
    private = output / "private-cache-authentication-NOT-FOR-PUBLICATION"
    private.mkdir(mode=0o700)
    starter = output / "dependency-starter"
    starter.mkdir()
    outcomes, campaign_error, material_stop = [], None, False
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"ZERORUN_TRUST_ROOT": str(private), "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}))
            stack.enter_context(patch.object(bench, "_PYTEST_EXECUTION_TIMEOUT_SECONDS", execution_seconds))
            stack.enter_context(patch.object(oci, "DOCKER_EXECUTION_TIMEOUT_SECONDS", execution_seconds))
            layer = stack.enter_context(bench._frozen_dependency_layer(starter, runtime_image=IMAGE, extra_requirements=("pretend",)))
            save(output / "dependency-layer.json", layer["provenance"])
            for case in cases:
                outcome_row = {"case_id": case["case_id"], "repo": case["repo"], "disposition": None}
                case_out = output / "cases" / case["case_id"]
                case_out.mkdir(parents=True)
                try:
                    if material_stop:
                        outcome_row["disposition"] = "NOT_RUN_CORRECTNESS_STOP"
                    elif time.monotonic() >= deadline:
                        outcome_row["disposition"] = "NOT_RUN_BUDGET"
                    elif case.get("disposition") == "UNAVAILABLE_ACQUISITION":
                        outcome_row.update(disposition="UNAVAILABLE_ACQUISITION", error=case["error"])
                    else:
                        outcome_row = run_case(case, ledger_path.parent, case_out, components, layer, deadline)
                except Exception as error:
                    material_stop = isinstance(error, MaterialMismatch)
                    outcome_row.update(disposition="MATERIAL_CORRECTNESS_STOP" if material_stop else "INCOMPLETE_OR_UNSUPPORTED",
                                       error={"type": type(error).__name__, "message": str(error)})
                save(case_out / "completion.json", outcome_row)
                outcomes.append(outcome_row)
                print(json.dumps({"case_id": case["case_id"], "disposition": outcome_row["disposition"], "case_index": len(outcomes), "selected": len(cases)}), flush=True)
    except Exception as error:
        campaign_error = {"type": type(error).__name__, "message": str(error)}
    for case in cases[len(outcomes):]:
        row = {"case_id": case["case_id"], "repo": case["repo"], "disposition": "NOT_RUN_CAMPAIGN_FAILURE"}
        save(output / "cases" / case["case_id"] / "completion.json", row)
        outcomes.append(row)
    current = code_inventory()
    authorities = list(private.glob("repositories/*/authorities/*.json"))
    unchanged = current == sources
    runtime_unchanged = all(record(engine / r["path"], r["path"]) == r for r in engine_identity["runtime_files"])
    result = {"schema": "zerorun.controlled-handoff-completion.v1", "completed_utc": utc(),
        "protocol_sha256": sha(ordinary(output / "protocol.json")), "phase": ledger["phase"],
        "cases": outcomes, "selected_cases": len(cases), "complete_cases": sum(c["disposition"] == "COMPLETE" for c in outcomes),
        "case_denominator_complete": len(cases) == (2 if ledger["phase"] == "pilot" else 30),
        "campaign_error": campaign_error, "material_correctness_stop": material_stop,
        "study_source_unchanged": unchanged, "runtime_source_unchanged": runtime_unchanged,
        "mcp_authority_files_found": len(authorities), "new_model_calls": 0,
        "all_assigned_outcomes_retained": len(outcomes) == len(cases), "acceptance_probability_estimated": False}
    save(output / "completion.json", result)
    require(unchanged and runtime_unchanged and not authorities, "source/authority boundary changed")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execution-seconds", type=int, default=120)
    parser.add_argument("--budget-seconds", type=int, default=7200)
    parser.add_argument("--execute-reviewed-lab", action="store_true", required=True)
    args = parser.parse_args(argv)
    result = run(args.ledger, args.engine, args.output, args.execution_seconds, args.budget_seconds)
    print(json.dumps({k: result[k] for k in ("complete_cases", "selected_cases", "material_correctness_stop", "campaign_error")}))
    return int(result["material_correctness_stop"] or result["campaign_error"] is not None)


if __name__ == "__main__":
    raise SystemExit(main())
