"""Versioned lab checks using explicitly adapted test fixtures, not new evidence."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from research.softwarex import diagnose_mcp_authority_052 as historical_diagnostic
from research.softwarex import quickstart_053 as diagnostic
from research.softwarex import quickstart_052 as historical
from research.softwarex import quickstart_053 as q
from research.softwarex.tests.test_quickstart_check import ROOT, passing_check, passing_install, seal, streams
from research.softwarex.tests.test_quickstart_052 import current_check as check_052


# Exact explicit-CRLF git archive e5194d3; remaining32 files retain historical bytes.
CHANGED = {'__init__.py': {'path': '__init__.py', 'bytes': 56, 'sha256': '728048b1b2007ee0a0751c2356e468f6a3268ce776c098454cc6a30d21bcdc34'}, 'codex.py': {'path': 'codex.py', 'bytes': 38591, 'sha256': '3a875a0db0d0a146bc0032ee1a48bb3e5720f44ba0fd99524d6a3e8d5c84639b'}, 'mcp.py': {'path': 'mcp.py', 'bytes': 50523, 'sha256': 'cf32d47dc40e914c6f2e411548dca4525005b0928821c035018fdc76b0cd63f4'}, 'model.py': {'path': 'model.py', 'bytes': 8758, 'sha256': 'e8b88ba2fa8cb77ee16a6659c6b119324b7ae4f92e11734bf6af99556e503159'}}


def current_rows(rows):
    return [copy.deepcopy(CHANGED.get(row["path"], row)) for row in rows]


def current_install():
    value = passing_install()
    value["schema"] = "zerorun.softwarex-quickstart-install-0.5.3.v1"
    value["helper_sha256"] = q.digest(ROOT / "research/softwarex/quickstart_053.py")
    for field in ("source_before", "source_after"):
        value[field]["runtime_files"] = current_rows(value[field]["runtime_files"])
    value["source_bindings"]["helpers_sha256"] = q.HELPERS.copy()
    before = value["source_before"]
    value["isolated_build_copy"]["files"] = before["packaging_files"] + [
        dict(row, path="src/zerorun/" + row["path"]) for row in before["runtime_files"]]
    value["commands"][4].update(streams(b"zerorun 0.5.3\n"))
    return seal(value)


def current_check():
    """Adapt archived samples for unit tests; never publish these as runtime evidence."""
    value = passing_check()
    value["schema"] = q.SCHEMA
    value["helper_sha256"] = q.digest(ROOT / "research/softwarex/quickstart_053.py")
    value["quickstart_sha256"] = q.digest(ROOT / "research/softwarex/QUICKSTART_053.md")
    value["source_bindings"]["helpers_sha256"] = q.HELPERS.copy()
    for package in (value["source_package"], value["installed_before"]["module"], value["installed_after"]["module"]):
        package["files"] = current_rows(package["files"])
        package["bytes"] = sum(row["bytes"] for row in package["files"])
        package["identity_sha256"] = q.CORE_IDENTITY
    for stage in value["stages"]:
        responses = stage["responses"]
        responses[0]["result"]["serverInfo"]["version"] = "0.5.3"
        raw = b"".join(q.canonical(row) + b"\n" for row in responses)
        stderr = base64.b64decode(stage["streams"]["stderr_base64"])
        stage["streams"] = streams(raw, stderr)
    value["commands"] = [{"command": [value["installed_launcher"], "mcp-server"],
        "streams": row["streams"], "elapsed_seconds": 0.1} for row in value["stages"]]
    return seal(value)


class CurrentQuickstartTests(unittest.TestCase):
    def test_expanded_manifest_has_an_exact_bounded_metadata_limit(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(q, "HELPERS", {}):
            root = Path(directory)
            manifest = root / "PUBLIC_RELEASE_MANIFEST.json"
            for size in (4_453_585, 8 * 1024 * 1024):
                with self.subTest(accepted_bytes=size):
                    manifest.write_bytes(b'{"padding":"' + b" " * (size - 14) + b'"}')
                    self.assertEqual(manifest.stat().st_size, size)
                    self.assertEqual(q.source_bindings(root)["public_manifest_sha256"], q.digest(manifest))
            with manifest.open("ab") as stream:
                stream.write(b" ")
            with self.assertRaisesRegex(ValueError, "manifest exceeds bound"):
                q.source_bindings(root)
        self.assertEqual(q.LIMIT, 2 * 1024 * 1024)

    @staticmethod
    def replace_stage(value, index, mutate):
        stage = value["stages"][index]
        mutate(stage["responses"])
        result = stage["responses"][1]["result"]
        result["content"][0]["text"] = q.canonical(result["structuredContent"]).decode()
        raw = b"".join(q.canonical(row) + b"\n" for row in stage["responses"])
        stage["streams"] = streams(raw)
        value["commands"][index]["streams"] = stage["streams"]
        return seal(value)

    def saved(self, value, validator=q.validate_saved_receipt):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_bytes(q.canonical(value))
            return validator(ROOT, path)

    def test_reviewed_runtime_has_one_exact_identity(self):
        value = current_check()
        self.assertEqual(len(value["source_package"]["files"]), 36)
        self.assertEqual(hashlib.sha256(q.canonical(value["source_package"]["files"])).hexdigest(), q.CORE_IDENTITY)
        self.assertEqual(q.REVIEWED_CORE_COMMIT, "e5194d340a09bccb667d3021a6a4a9a9a054123b")
        q.validate_core(value["source_package"], value["installed_before"])
        self.assertTrue(self.saved(value)["passed"])
        self.assertTrue(self.saved(current_install(), q.validate_installation_receipt)["passed"])

    def test_historical_and_current_runtime_identities_are_not_interchangeable(self):
        for validator, fixture in ((historical.validate_core, current_check()), (q.validate_core, passing_check())):
            with self.subTest(validator=validator.__module__):
                with self.assertRaises(ValueError):
                    validator(fixture["source_package"], fixture["installed_before"])

    def test_exact_052_runtime_is_rejected_by_053(self):
        old = check_052()
        historical.validate_core(old["source_package"], old["installed_before"])
        with self.assertRaises(ValueError):
            q.validate_core(old["source_package"], old["installed_before"])

    def test_wrong_server_version_rejects_even_with_coherent_raw_hashes(self):
        for version in ("0.5.1", "0.5.2", "0.5.4"):
            with self.subTest(version=version):
                value = self.replace_stage(current_check(), 0,
                    lambda rows: rows[0]["result"]["serverInfo"].update(version=version))
                with self.assertRaises(ValueError):
                    self.saved(value)

    def test_failed_or_unverified_results_cannot_be_relabelled_as_success(self):
        for index, change in ((3, {"exit_code": 1}), (4, {"verified": False}),
                              (4, {"status": "HIT_REUSED"})):
            with self.subTest(index=index, change=change):
                value = self.replace_stage(current_check(), index,
                    lambda rows: rows[1]["result"]["structuredContent"].update(change))
                with self.assertRaises(ValueError):
                    self.saved(value)

    def test_missing_authority_stage_requires_actual_refusal(self):
        value = self.replace_stage(current_check(), 0,
            lambda rows: rows[1]["result"]["structuredContent"].update(manifest_authorized=True))
        with self.assertRaises(ValueError):
            self.saved(value)

    def test_success_stages_require_the_same_cache_key(self):
        value = self.replace_stage(current_check(), 3,
            lambda rows: rows[1]["result"]["structuredContent"].update(cache_key="f" * 64))
        with self.assertRaises(ValueError):
            self.saved(value)

    def test_historical_request_helper_pins_are_preserved(self):
        # Product-workspace tools are newer than the explicitly archived public
        # helpers. The actual public install checks every exported helper byte.
        self.assertEqual(q.HELPERS, historical.HELPERS)
        for relative, expected in q.HELPERS.items():
            if relative.startswith("research/softwarex/"):
                with self.subTest(relative=relative):
                    self.assertEqual(q.digest(ROOT / relative), expected)

    def test_coherent_wrong_runtime_hash_still_refuses(self):
        value = current_check()
        for package in (value["source_package"], value["installed_before"]["module"], value["installed_after"]["module"]):
            package["files"][0]["sha256"] = "0" * 64
            package["identity_sha256"] = hashlib.sha256(q.canonical(package["files"])).hexdigest()
        with self.assertRaises(ValueError):
            self.saved(seal(value))

    def test_exact_server_version_and_historical_exchange_are_separate(self):
        value = current_check()
        for stage in value["stages"]:
            diagnostic.validate_exchange(stage, value["synthetic"]["path"])
            with self.assertRaises(ValueError):
                historical_diagnostic.validate_exchange(stage, value["synthetic"]["path"])
        old = passing_check()
        with self.assertRaises(ValueError):
            diagnostic.validate_exchange(old["stages"][0], old["synthetic"]["path"])

    def test_current_fixture_scope_stream_and_denominator_refusals(self):
        mutations = (
            lambda v: v.update(model_called=True),
            lambda v: v.update(real_repository_authorized=True),
            lambda v: v.update(runtime_acquisition_attempted=True),
            lambda v: v.update(automatic_retry_count=1),
            lambda v: v["synthetic"].update(built_in_fixture_only=False),
            lambda v: v["stages"].pop(),
            lambda v: v["stages"][0]["streams"].update(stdout_sha256="0" * 64),
            lambda v: v["stages"][2]["streams"].update(returncode=1),
            lambda v: v.update(temporary_fixture_and_authority_cleaned=False),
            lambda v: v["source_after"].update(head="f" * 40),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutations.index(mutation)):
                value = current_check()
                mutation(value)
                with self.assertRaises(ValueError):
                    self.saved(seal(value))

    def test_wrong_installed_version_refuses_with_other_bytes_valid(self):
        value = current_install()
        value["commands"][4].update(streams(b"zerorun 0.5.1\n"))
        with self.assertRaises(ValueError):
            self.saved(seal(value), q.validate_installation_receipt)

    def make_pair(self, directory):
        install = current_install()
        install_path = Path(directory) / "install.json"
        install_path.write_bytes(q.canonical(install))
        check = current_check()
        check["source_before"]["head"] = install["source_before"]["commit"]
        check["source_after"]["head"] = install["source_before"]["commit"]
        check["installation_receipt"] = {"sha256": q.digest(install_path),
            "source_commit": install["source_before"]["commit"], "elapsed_seconds": install["elapsed_seconds"]}
        check_path = Path(directory) / "check.json"
        check_path.write_bytes(q.canonical(seal(check)))
        return install_path, check_path, check

    def test_read_only_cli_replays_pair_without_runtime_or_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            install, check, _ = self.make_pair(directory)
            with patch.object(q, "create_installation", side_effect=AssertionError("installation called")), \
                    patch.object(q, "check", side_effect=AssertionError("execution called")), \
                    patch.object(q, "load_helpers", side_effect=AssertionError("runtime helper loaded")), \
                    patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(q.main(["--source-root", str(ROOT), "--check", str(check),
                    "--installation-receipt", str(install)]), 0)
                self.assertTrue(json.loads(output.getvalue())["read_only"])

    def test_pair_rejects_different_file_hash_or_source_commit(self):
        for field in ("sha256", "source_commit"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                install, path, value = self.make_pair(directory)
                value["installation_receipt"][field] = "f" * (64 if field == "sha256" else 40)
                path.write_bytes(q.canonical(seal(value)))
                with self.assertRaises(ValueError):
                    q.validate_receipt_pair(ROOT, install, path)

    def test_read_only_cli_rejects_execution_options(self):
        for argument in ("--approve-synthetic-formative-authority", "--output=somewhere", "--create-tools-env=somewhere"):
            with self.subTest(argument=argument), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    q.main(["--source-root", str(ROOT), "--check", "check.json",
                        "--installation-receipt", "install.json", argument])


if __name__ == "__main__":
    unittest.main()
