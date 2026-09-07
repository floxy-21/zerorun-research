"""Direct, non-model tests; synthetic STDIO samples are archived server results."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from research.softwarex import diagnose_mcp_authority as diagnostic
from research.softwarex import quickstart_check as q

ROOT = Path(__file__).resolve().parents[3]
DIAGNOSTIC = ROOT / "research/softwarex/evidence/live-client-v1/non-model-diagnostic.json"


def seal(value):
    value["evidence_payload_sha256"] = hashlib.sha256(q.canonical({k: v for k, v in value.items()
                                                                if k != "evidence_payload_sha256"})).hexdigest()
    return value


def streams(stdout=b"", stderr=b"", returncode=0):
    value = {"returncode": returncode, "timed_out": False}
    for name, raw in (("stdout", stdout), ("stderr", stderr)):
        value.update({name + "_base64": base64.b64encode(raw).decode(), name + "_bytes": len(raw),
                      name + "_sha256": hashlib.sha256(raw).hexdigest(), name + "_truncated": False})
    return value


def passing_check():
    original = json.loads(DIAGNOSTIC.read_bytes())
    synthetic = copy.deepcopy(original["synthetic"])
    synthetic["built_in_fixture_only"] = True
    result = {
        "schema": q.SCHEMA, "passed": True, "model_called": False, "real_repository_authorized": False,
        "external_developer_study": False, "runtime_acquisition_attempted": False,
        "automatic_retry_count": 0, "elapsed_seconds": 1.0,
        "temporary_fixture_and_authority_cleaned": True, "synthetic": synthetic,
        "source_package": original["source_package"], "installed_before": original["installed_before"],
        "installed_after": original["installed_after"], "source_before": original["source_before"],
        "source_after": original["source_after"], "stages": original["stages"],
        "helper_sha256": q.digest(ROOT / "research/softwarex/quickstart_check.py"),
        "quickstart_sha256": q.digest(ROOT / "research/softwarex/QUICKSTART_LAB.md"),
        "source_bindings": {"helpers_sha256": q.HELPERS.copy()}, "installed_launcher": "/external/bin/zerorun",
        "prerequisites": {"image": {"Os": "linux", "Architecture": "amd64", "RepoDigests": [q.IMAGE]}},
    }
    result["commands"] = [{"command": [result["installed_launcher"], "mcp-server"],
                           "streams": row["streams"], "elapsed_seconds": 0.1} for row in result["stages"]]
    return seal(result)


def passing_install():
    original = json.loads(DIAGNOSTIC.read_bytes())
    source, env, build, commit = "/public/source", "/external/tools", "/private/build-source", "a" * 40
    snapshot = {"commit": commit, "status_clean": True, "runtime_files": original["source_package"]["files"],
                "public_manifest_sha256": "b" * 64,
                "packaging_files": [{"path": name, "bytes": 1, "sha256": "c" * 64} for name in q.PACKAGING]}
    copy_rows = snapshot["packaging_files"] + [dict(row, path="src/zerorun/" + row["path"])
                                               for row in snapshot["runtime_files"]]
    commands = [
        ["git", "-C", source, "rev-parse", "HEAD"],
        ["git", "-C", source, "status", "--porcelain=v2", "--untracked-files=all"],
        ["python3", "-I", "-m", "venv", env],
        [env + "/bin/python", "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--no-input", "--no-cache-dir", build],
        [env + "/bin/zerorun", "--version"],
        ["git", "-C", source, "rev-parse", "HEAD"],
        ["git", "-C", source, "status", "--porcelain=v2", "--untracked-files=all"],
    ]
    outputs = [(commit + "\n").encode(), b"", b"", b"installed\n", b"zerorun 0.5.1\n", (commit + "\n").encode(), b""]
    return seal({"schema": "zerorun.softwarex-quickstart-install.v1", "passed": True,
        "helper_sha256": q.digest(ROOT / "research/softwarex/quickstart_check.py"), "model_called": False,
        "real_repository_authorized": False, "automatic_retry_count": 0, "source_before": snapshot,
        "source_after": copy.deepcopy(snapshot), "source_root": source, "tools_environment": env,
        "source_bindings": {"helpers_sha256": q.HELPERS.copy(), "public_manifest_sha256": "b" * 64},
        "isolated_build_copy": {"path": build, "files": copy_rows, "cleaned": True, "source_checkout_written": False},
        "python_command": env + "/bin/python", "zerorun_command": env + "/bin/zerorun", "elapsed_seconds": 3.0,
        "commands": [dict(streams(raw), command=command, output_limit_exceeded=False, elapsed_seconds=0.1)
                     for command, raw in zip(commands, outputs)]})


class QuickstartTests(unittest.TestCase):
    def saved(self, value, validator=q.validate_saved_receipt):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(q.canonical(value))
            return validator(ROOT, path)

    def test_actual_archived_streams_validate(self):
        self.assertTrue(self.saved(passing_check())["passed"])

    def test_five_stages_have_fixed_denominator(self):
        value = passing_check()
        value["stages"].pop()
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_mutated_stream_is_rejected(self):
        value = passing_check()
        value["stages"][0]["streams"]["stdout_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_different_source_after_is_rejected(self):
        value = passing_check()
        value["source_after"]["head"] = "f" * 40
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_different_installed_core_is_rejected(self):
        value = passing_check()
        value["installed_before"]["module"]["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_missing_cleanup_is_rejected(self):
        value = passing_check()
        value["temporary_fixture_and_authority_cleaned"] = False
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_adverse_record_is_preserved(self):
        value = passing_check()
        value.update(passed=False, stages=[], failure={"type": "ValueError", "message": "test failure"})
        self.assertFalse(self.saved(seal(value))["passed"])

    def test_no_failure_record_is_rejected(self):
        value = passing_check()
        value["passed"] = False
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_false_model_claim_is_rejected(self):
        value = passing_check()
        value["model_called"] = True
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_false_external_user_claim_is_rejected(self):
        value = passing_check()
        value["external_developer_study"] = True
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_changed_request_order_is_rejected(self):
        value = passing_check()
        value["stages"][0], value["stages"][1] = value["stages"][1], value["stages"][0]
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_missing_actual_server_commands_is_rejected(self):
        value = passing_check()
        value["commands"] = []
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_producer_change_is_rejected(self):
        value = passing_check()
        value["helper_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_guide_change_is_rejected(self):
        value = passing_check()
        value["quickstart_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_receipt_hash_change_is_rejected(self):
        value = passing_check()
        value["elapsed_seconds"] += 1
        with self.assertRaises(ValueError):
            self.saved(value)

    def test_valid_installation_replays(self):
        self.assertTrue(self.saved(passing_install(), q.validate_installation_receipt)["passed"])

    def test_bad_installation_command_is_rejected(self):
        value = passing_install()
        value["commands"][3]["command"].append("--editable")
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_extra_installation_command_is_rejected(self):
        value = passing_install()
        value["commands"].append(value["commands"][-1])
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_failed_pip_is_not_passing_installation(self):
        value = passing_install()
        value["commands"][3]["returncode"] = 1
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_failed_installation_has_a_receipt(self):
        value = passing_install()
        value["commands"][3]["returncode"] = 1
        value.update(passed=False, failure={"type": "ValueError", "message": "pip failed"})
        self.assertFalse(self.saved(seal(value), q.validate_installation_receipt)["passed"])

    def test_source_mutation_during_installation_is_rejected(self):
        value = passing_install()
        value["source_after"]["commit"] = "d" * 40
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_build_copy_mutation_is_rejected(self):
        value = passing_install()
        value["isolated_build_copy"]["files"][-1]["sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_wrong_version_transcript_is_rejected(self):
        value = passing_install()
        value["commands"][4].update(streams(b"zerorun 9.9.9\n"))
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_wrong_git_transcript_is_rejected(self):
        value = passing_install()
        value["commands"][0].update(streams(b"e" * 40 + b"\n"))
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            output = Path(directory) / "receipt.json"
            output.write_text("keep")
            with self.assertRaises(ValueError):
                q.validate_output(output, root)
            self.assertEqual(output.read_text(), "keep")

    def test_output_inside_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                q.validate_output(Path(directory) / "receipt.json", Path(directory))

    def test_approval_omission_writes_refusal_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            args = SimpleNamespace(source_root=root, output=Path(directory) / "receipt.json", intervention=[],
                                   installation_receipt=None, approve_synthetic_formative_authority=False)
            with patch.object(q, "load_helpers") as loader:
                result = q.check(args)
            self.assertFalse(result["passed"])
            loader.assert_not_called()
            self.assertTrue(args.output.is_file())

    def test_missing_cli_approval_exits_before_install(self):
        with patch.object(q, "create_installation") as install:
            with self.assertRaises(SystemExit):
                q.main(["--source-root", "source", "--create-tools-env", "tools",
                        "--installation-output", "install.json", "--output", "lab.json"])
            install.assert_not_called()

    def test_existing_tools_environment_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source, env = Path(directory) / "source", Path(directory) / "tools"
            source.mkdir()
            env.mkdir()
            args = SimpleNamespace(source_root=source, create_tools_env=env,
                                   installation_output=Path(directory) / "install.json")
            with self.assertRaises(ValueError):
                q.create_installation(args)

    def test_image_requires_fixed_digest(self):
        with self.assertRaises(ValueError):
            q.validate_image(json.dumps([{"Os": "linux", "Architecture": "amd64", "RepoDigests": ["image@sha256:" + "0" * 64]}]))

    def test_image_requires_supported_architecture(self):
        with self.assertRaises(ValueError):
            q.validate_image(json.dumps([{"Os": "linux", "Architecture": "arm64", "RepoDigests": [q.IMAGE]}]))

    def test_runtime_count_cannot_replace_exact_bytes(self):
        with self.assertRaises(ValueError):
            q.validate_core({"file_count": 36, "identity_sha256": "0" * 64}, {"module": {}})

    def test_bootstrap_command_captures_real_stdout(self):
        row = q.bounded_setup_command([sys.executable, "-I", "-c", "import sys; sys.stdout.buffer.write(b'checked\\n')"], environment={}, timeout=5)
        self.assertEqual(q.validate_streams(row)["stdout"], b"checked\n")

    def test_bootstrap_command_preserves_failure(self):
        row = q.bounded_setup_command([sys.executable, "-I", "-c", "raise SystemExit(7)"], environment={}, timeout=5)
        self.assertEqual(row["returncode"], 7)
        with self.assertRaises(ValueError):
            q.require_setup_success(row)

    def test_duplicate_receipt_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text('{"schema":"a","schema":"b"}')
            with self.assertRaises(ValueError):
                q.checked_envelope(path, q.SCHEMA)

    def test_recording_runner_retains_exception(self):
        rows = []
        def failed(*args, **kwargs):
            raise OSError("preserved")
        with self.assertRaises(OSError):
            q.recorded_runner(failed, lambda result: result, rows)(["test"], cwd=None)
        self.assertEqual(rows[0]["exception"]["type"], "OSError")


if __name__ == "__main__":
    unittest.main()
