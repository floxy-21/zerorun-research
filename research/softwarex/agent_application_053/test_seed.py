"""Offline tamper fixtures only; these tests make no installed/MCP evidence claim."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.softwarex.agent_application_053 import seed as s
from research.softwarex.agent_application_053 import consumer as c
from research.softwarex.agent_application_053 import validation as v
from research.softwarex.agent_application_053.test_consumer import qualification, payload, ROOT, TASK
from research.softwarex.five_hour_review.current_runtime_053 import CURRENT_FILES


def wire(status="MISS_EXECUTED", version="0.5.3"):
    result = payload(status)
    return [{"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25",
            "serverInfo": {"name": "zerorun", "version": version}, "capabilities": {}}},
            {"jsonrpc": "2.0", "id": 2, "result": {"isError": False,
                "structuredContent": result, "content": [{"type": "text", "text": v.canonical(result).decode()}]}}]


def raw(value):
    return b"".join(v.canonical(row) + b"\n" for row in value)


def stage(value=None):
    stdout = raw(wire() if value is None else value)
    import base64
    streams = {"returncode": 0, "timed_out": False}
    for name, data in (("stdout", stdout), ("stderr", b"")):
        streams.update({name + "_base64": base64.b64encode(data).decode(), name + "_bytes": len(data),
                        name + "_sha256": v.sha(data), name + "_truncated": False})
    return {"name": "miss", "requests": s.requests(ROOT, TASK), "explicit_trust_root": True, "streams": streams}


def save(path, value):
    path.write_bytes(v.canonical(value) + b"\n")


def seal(directory):
    save(directory / "RECORD_MANIFEST.json", {"files": [c.file_row(path, directory) for path in sorted(directory.rglob("*"))
         if path.is_file() and path != directory / "RECORD_MANIFEST.json"]})


def command(directory, label, argv, cwd, timeout, stdout):
    start = {"command": argv, "cwd": cwd, "started_utc": "2026-09-08T00:00:00+00:00", "timeout_seconds": timeout,
             "returncode": None, "timed_out": False, "output_limit_exceeded": False, "error": None}
    save(directory / (label + ".started.json"), start)
    row = dict(start, returncode=0, elapsed_seconds=0.01, completed_utc="2026-09-08T00:00:00.010+00:00")
    for name, data in (("stdout", stdout), ("stderr", b"")):
        path = directory / (label + "." + name + ".log"); path.write_bytes(data)
        row[name] = c.file_row(path, directory); row[name + "_over_limit"] = False
    save(directory / (label + ".json"), row)
    return row


def fixture(directory):
    q = qualification()
    manifest = {"version": 2, "tasks": {TASK: {"command": q["command"], "image": q["runtime_image"],
        "inputs": ["module.py", "tests"], "platform": "linux/amd64", "cacheable": True,
        "result_only": True, "closure_reviewed": True, "cache_streams": False,
        "outputs": [], "env": [], "unsafe_effects": []}}}
    manifest_raw = v.canonical(manifest)
    q["manifest_sha256"] = v.sha(manifest_raw)
    for state in q["states"].values():
        state["files"][0].update(bytes=len(manifest_raw), sha256=v.sha(manifest_raw))
        state["identity_sha256"] = v.identity(state["files"])
    save(directory / "qualification.json", q); (directory / "manifest.json").write_bytes(manifest_raw)
    (directory / "provenance").mkdir()
    for name, path in s.sources().items(): (directory / "provenance" / name).write_bytes(path.read_bytes())
    client = {name: {"invoked": "/tmp/external-venv/bin/" + exe, "resolved": {"path": "/resolved/" + exe,
               "bytes": 12, "sha256": "c" * 64}} for name, exe in (("zerorun", "zerorun"), ("python", "python"))}
    protocol = {"schema": s.SCHEMA, "qualification_sha256": v.sha((directory / "qualification.json").read_bytes()),
        "core_commit": c.CORE, "runtime_identity": c.CORE_IDENTITY, "seconds": s.SECONDS, "automatic_retries": 0,
        "model_called": False, "authority_created": False,
        "sources": [dict(c.file_row(path), path="provenance/" + name) for name, path in s.sources().items()],
        "root": ROOT, "task": TASK, "runtime_image": q["runtime_image"], "manifest_sha256": q["manifest_sha256"],
        "client": client, "trust_root": "/tmp/external-trust", "environment": {"ZERORUN_TRUST_ROOT": "/tmp/external-trust"},
        "probe_cwd": "/tmp/external-seed/empty-workspace", "requests": s.requests(ROOT, TASK)}
    save(directory / "protocol.json", protocol)
    installed = {"version": "0.5.3", "module": "/tmp/external-venv/lib/python3.10/site-packages/zerorun",
                 "files": [dict(CURRENT_FILES[name], path=name) for name in sorted(CURRENT_FILES)]}
    for label in ("runtime-before", "runtime-after"):
        command(directory, label, [client["python"]["invoked"], "-I", "-B", "-c", c.PROBE],
                protocol["probe_cwd"], 20, v.canonical(installed))
    row = command(directory, "seed", [client["zerorun"]["invoked"], "mcp-server"], ROOT, s.SECONDS, raw(wire()))
    (directory / "requests.log").write_bytes(raw(s.requests(ROOT, TASK)))
    result = s.parse_exchange(stage(), ROOT, TASK)
    receipt = {"schema": s.SCHEMA, "protocol_sha256": v.sha((directory / "protocol.json").read_bytes()),
        "passed": True, "failure": None, "source_before": q["states"]["final"]["files"],
        "source_after": q["states"]["final"]["files"], "source_identity": q["states"]["final"]["identity_sha256"],
        "git_before": [], "git_after": [], "client_after": client, "commands": ["runtime-before", "seed", "runtime-after"],
        "installed_runtime": installed, "result": result, "cache_key": result["cache_key"]}
    save(directory / "receipt.json", receipt); seal(directory)
    return q, protocol, receipt


class SeedWireTests(unittest.TestCase):
    def test_exact_single_miss_uses_unchanged_original_parser(self):
        original = s.quickstart.validate_exchange.__code__
        self.assertEqual(s.parse_exchange(stage(), ROOT, TASK)["payload"]["status"], "MISS_EXECUTED")
        self.assertIs(s.quickstart.validate_exchange.__code__, original)
        self.assertIn("synthetic-lifecycle", original.co_consts)

    def test_hit_verify_failed_and_wrong_version_are_not_seeds(self):
        for status, version in (("HIT_REUSED", "0.5.3"), ("VERIFY_MATCH", "0.5.3"),
                                ("MISS_FAILED", "0.5.3"), ("MISS_EXECUTED", "0.5.2")):
            with self.subTest(status=status, version=version), self.assertRaises(ValueError):
                s.parse_exchange(stage(wire(status, version)), ROOT, TASK)

    def test_request_only_wrong_task_and_duplicate_call_refused(self):
        for change in (lambda x: x["requests"].pop(), lambda x: x["requests"].append(deepcopy(x["requests"][-1])),
                       lambda x: x["requests"][-1]["params"]["arguments"].update(task="other"),
                       lambda x: x["requests"][-1]["params"]["arguments"].update(verify=True)):
            value = stage(); change(value)
            with self.assertRaises(ValueError): s.parse_exchange(value, ROOT, TASK)
        with self.assertRaises(ValueError): s.parse_exchange(stage(wire()[:1]), ROOT, TASK)

    def test_raw_text_mismatch_and_wrong_result_task_refused(self):
        for change in (lambda x: x[1]["result"]["content"][0].update(text="{}"),
                       lambda x: x[1]["result"]["structuredContent"].update(task="other")):
            value = wire(); change(value)
            with self.assertRaises(ValueError): s.parse_exchange(stage(value), ROOT, TASK)

    def test_nonzero_exit_timeout_truncation_and_coherent_failed_result_refused(self):
        for key, value in (("returncode", 1), ("timed_out", True), ("stdout_truncated", True)):
            record = stage(); record["streams"][key] = value
            with self.assertRaises(ValueError): s.parse_exchange(record, ROOT, TASK)
        value = wire(); value[1]["result"]["structuredContent"]["exit_code"] = 1
        value[1]["result"]["content"][0]["text"] = v.canonical(value[1]["result"]["structuredContent"]).decode()
        with self.assertRaises(ValueError): s.parse_exchange(stage(value), ROOT, TASK)


class SeedReceiptTests(unittest.TestCase):
    def test_portable_reconciliation_never_executes(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp); fixture(directory)
            with patch.object(c, "invoke", side_effect=AssertionError("must be offline")):
                result = s.validate(directory)
            self.assertTrue(result["seeded"]); self.assertFalse(result["model_called"])

    def test_claim_alone_source_change_and_image_change_refused(self):
        for field in ("source_after", "result", "passed"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp); _, _, receipt = fixture(directory)
                receipt[field] = [] if field == "source_after" else ({} if field == "result" else False)
                save(directory / "receipt.json", receipt); seal(directory)
                with self.assertRaises(ValueError): s.validate(directory)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp); _, protocol, receipt = fixture(directory)
            protocol["runtime_image"] = "other@sha256:" + "d" * 64
            save(directory / "protocol.json", protocol)
            receipt["protocol_sha256"] = v.sha((directory / "protocol.json").read_bytes())
            save(directory / "receipt.json", receipt); seal(directory)
            with self.assertRaises(ValueError): s.validate(directory)

    def test_resealed_installed_runtime_tamper_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp); _, protocol, receipt = fixture(directory)
            receipt["installed_runtime"]["files"][0]["sha256"] = "0" * 64
            for label in ("runtime-before", "runtime-after"):
                command(directory, label, [protocol["client"]["python"]["invoked"], "-I", "-B", "-c", c.PROBE],
                        protocol["probe_cwd"], 20, v.canonical(receipt["installed_runtime"]))
            save(directory / "receipt.json", receipt); seal(directory)
            with self.assertRaises(ValueError): s.validate(directory)

    def test_raw_request_and_unlisted_file_tamper_refused(self):
        for extra in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp); fixture(directory)
                if extra: (directory / "hidden.log").write_bytes(b"extra")
                else: (directory / "requests.log").write_bytes(b"{}\n"); seal(directory)
                with self.assertRaises(ValueError): s.validate(directory)

    def test_existing_output_is_not_overwritten(self):
        # Exclusive receipt writes are also used on the real execution route.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "receipt.json"; c.save(path, {"original": True})
            with self.assertRaises(FileExistsError): c.save(path, {"original": False})
            self.assertEqual(v.strict(path.read_bytes()), {"original": True})


if __name__ == "__main__":
    unittest.main()
