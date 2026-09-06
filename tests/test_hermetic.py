from __future__ import annotations

import errno
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from zerorun.hermetic import _action_lock, _docker_execute, hermetic_environment, task_fingerprint_v2
from zerorun.manifest import load_manifest
from zerorun.model import ConfigurationError
from zerorun.api import run_task, run_tasks
from zerorun import fingerprint as fingerprint_module
from zerorun import hermetic as hermetic_module
from zerorun.oci import (
    _force_remove_container,
    _inspect_container_exit_code,
    _run_bounded_process,
    _run_created_container,
    clear_runtime_identity_cache,
    inspect_runtime,
)
from zerorun.store import ProjectLockBusy, Store
from zerorun.workspace import StagedWorkspace


IMAGE = "example.invalid/python@sha256:" + "a" * 64
CONTAINER_ID = "b" * 64
CONTAINER_ID_BYTES = (CONTAINER_ID + "\n").encode("ascii")
RUNTIME = {
    "runtime": "docker",
    "requested_image": IMAGE,
    "requested_digest": "sha256:" + "a" * 64,
    "platform": "linux/amd64",
    "image_id": "sha256:" + "b" * 64,
    "repo_digests": [IMAGE],
    "rootfs": {"type": "layers", "layers": ["sha256:" + "c" * 64]},
    "config_sha256": "d" * 64,
}


class HermeticV2Tests(unittest.TestCase):
    def setUp(self):
        # Docker execution is mocked in this unit class; native-host rejection
        # is covered separately and the full path runs on Linux in CI.
        host = patch("zerorun.hermetic.validate_host")
        host.start()
        self.addCleanup(host.stop)

    def make_project(self, *, inputs=None, extra=None):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / ".git").mkdir()
        (root / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
        task = {
            "command": ["python", "-c", "import src; assert src.VALUE == 1"],
            "inputs": inputs or ["src.py"],
            "outputs": [],
            "cacheable": True,
            "unsafe_effects": [],
            "env": [],
            "cache_streams": False,
            "result_only": True,
            "closure_reviewed": True,
            "image": IMAGE,
            "platform": "linux/amd64",
        }
        if extra:
            task.update(extra)
        path = root / ".zerorun.json"
        path.write_text(json.dumps({"version": 2, "tasks": {"work": task}}), encoding="utf-8")
        return temporary, path

    def execute(self, manifest, task, **kwargs):
        return run_task(manifest, task, stdout=io.BytesIO(), stderr=io.BytesIO(), **kwargs)

    def make_batch_project(self):
        temporary, path = self.make_project()
        raw = json.loads(path.read_text(encoding="utf-8"))
        original = raw["tasks"]["work"]
        raw["tasks"] = {
            name: {**original, "command": ["python", "-c", f"import src; assert src.VALUE == 1; {index}"]}
            for index, name in enumerate(("first", "second", "third"), start=1)
        }
        path.write_text(json.dumps(raw), encoding="utf-8")
        return temporary, path

    def test_v2_rejects_unpinned_image(self):
        temporary, path = self.make_project(extra={"image": "python:3.12"})
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(ConfigurationError, "immutable OCI reference"):
            load_manifest(path)

    def test_v2_rejects_wrong_platform(self):
        temporary, path = self.make_project(extra={"platform": "linux/arm64"})
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(ConfigurationError, "only platform linux/amd64"):
            load_manifest(path)

    def test_v2_rejects_outputs_stream_cache_and_normalizer(self):
        for extra, message in [
            ({"outputs": ["out.txt"]}, "cannot declare outputs"),
            ({"cache_streams": True}, "cache_streams to false"),
            ({"result_normalizer": "pytest-junit-v1"}, "unsupported in hermetic v2"),
        ]:
            with self.subTest(extra=extra):
                temporary, path = self.make_project(extra=extra)
                try:
                    with self.assertRaisesRegex(ConfigurationError, message):
                        load_manifest(path)
                finally:
                    temporary.cleanup()

    def test_v2_requires_explicit_closure_review(self):
        temporary, path = self.make_project(extra={"closure_reviewed": False})
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(ConfigurationError, "closure_reviewed"):
            load_manifest(path)

    def test_v2_rejects_unsafe_effect_for_cacheable_task(self):
        temporary, path = self.make_project(extra={"unsafe_effects": ["network"]})
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(ConfigurationError, "cannot declare unsafe effects"):
            load_manifest(path)

    def test_v2_fingerprint_uses_content_not_mtime(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        _, env_fp = hermetic_environment(task)
        key1, _ = task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=env_fp)
        source = manifest.root / "src.py"
        source.touch()
        key2, _ = task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=env_fp)
        self.assertEqual(key1, key2)
        source.write_text("VALUE = 2\n", encoding="utf-8")
        key3, _ = task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=env_fp)
        self.assertNotEqual(key1, key3)

    def test_v2_fingerprint_binds_root_and_implicit_parent_modes(self):
        if os.name == "nt":
            self.skipTest("POSIX directory mode semantics are unavailable")
        temporary, path = self.make_project(inputs=["pkg/data.txt"])
        self.addCleanup(temporary.cleanup)
        root = path.parent
        (root / "pkg").mkdir()
        (root / "pkg" / "data.txt").write_text("stable\n", encoding="utf-8")
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        _, env_fp = hermetic_environment(task)
        key1, fingerprint1 = task_fingerprint_v2(
            manifest, task, RUNTIME, environment_fingerprint=env_fp
        )
        os.chmod(root / "pkg", 0o700)
        key2, _ = task_fingerprint_v2(
            manifest, task, RUNTIME, environment_fingerprint=env_fp
        )
        os.chmod(root, 0o755)
        key3, _ = task_fingerprint_v2(
            manifest, task, RUNTIME, environment_fingerprint=env_fp
        )
        self.assertNotEqual(key1, key2)
        self.assertNotEqual(key2, key3)
        self.assertEqual(
            fingerprint1["policy"]["workspace_metadata"],
            "content-and-mode-preserved-times-zeroed-v1",
        )

    def test_v2_snapshot_normalizes_unkeyed_timestamps(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        state = manifest.root / ".zerorun" / "snapshots"
        workspace = StagedWorkspace(
            state,
            manifest,
            manifest.tasks["work"],
            track_writes=False,
            create_git_mask_marker=True,
            deterministic_metadata=True,
        )
        workspace.prepare()
        self.addCleanup(workspace.cleanup)
        assert workspace.root is not None
        self.assertTrue((workspace.root / ".git").is_dir())
        self.assertTrue((workspace.root / ".zerorun").is_dir())
        self.assertEqual((workspace.root / "src.py").stat().st_mtime_ns, 0)
        self.assertEqual(workspace.root.stat().st_mtime_ns, 0)
        self.assertTrue(workspace.source_matches())

    def test_v2_rejects_unsupported_host_before_state_or_staging(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        with patch(
            "zerorun.hermetic.validate_host",
            side_effect=ConfigurationError("unsupported test host"),
        ), patch("zerorun.hermetic.StagedWorkspace.prepare") as prepare:
            with self.assertRaisesRegex(ConfigurationError, "unsupported test host"):
                self.execute(manifest, manifest.tasks["work"])
        prepare.assert_not_called()
        self.assertFalse((manifest.root / ".zerorun").exists())

    def test_snapshot_cleanup_failure_is_visible_and_preserves_target(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        workspace = StagedWorkspace(
            manifest.root / ".zerorun" / "snapshots",
            manifest,
            manifest.tasks["work"],
        )
        workspace.prepare()
        root = workspace.root
        self.assertIsNotNone(root)
        with patch("zerorun.workspace.shutil.rmtree", side_effect=OSError("busy")):
            with self.assertRaisesRegex(ConfigurationError, "cleanup failed"):
                workspace.cleanup()
        self.assertEqual(workspace.root, root)
        workspace.cleanup()
        self.assertFalse(root.exists())

    def test_v2_read_only_checkout_root_can_stage_and_cleanup(self):
        if os.name == "nt":
            self.skipTest("POSIX directory mode semantics are unavailable")
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        Store(manifest.root).ensure()
        original_mode = stat.S_IMODE(manifest.root.stat().st_mode)
        os.chmod(manifest.root, 0o555)
        try:
            with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
                "zerorun.hermetic._docker_execute",
                return_value=(0, 10.0, b"", b""),
            ):
                result = self.execute(manifest, manifest.tasks["work"])
            self.assertEqual(result.status, "MISS_EXECUTED")
            snapshots = manifest.root / ".zerorun" / "snapshots"
            self.assertEqual(list(snapshots.glob("zerorun-run-*")), [])
        finally:
            os.chmod(manifest.root, original_mode)

    def test_snapshot_cleanup_removes_nested_readonly_directories(self):
        if os.name != "posix":
            self.skipTest("POSIX directory mode semantics are required")
        from zerorun.workspace import _remove_workspace

        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        state = path.parent / ".zerorun" / "snapshots"
        root = state / "zerorun-run-readonly-regression"
        nested = root / "packages" / "licenses"
        nested.mkdir(parents=True)
        (nested / "LICENSE").write_text("owned test fixture", encoding="utf-8")
        os.chmod(nested / "LICENSE", 0o444)
        os.chmod(nested, 0o555)
        os.chmod(nested.parent, 0o555)
        _remove_workspace(state, root)
        self.assertFalse(root.exists())

    def test_cleanup_error_handler_refuses_outside_or_link_targets(self):
        from zerorun.workspace import _rmtree_onerror

        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        root = path.parent / "zerorun-run-handler-regression"
        root.mkdir()
        outside = path.parent / "outside.txt"
        outside.write_text("preserve", encoding="utf-8")
        error = PermissionError("read only")
        with patch("zerorun.workspace.os.chmod") as chmod:
            with self.assertRaises(PermissionError):
                _rmtree_onerror(os.unlink, str(outside), (PermissionError, error, None), root=root)
            chmod.assert_not_called()
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve")

    def test_readonly_whole_task_hit_does_not_stage_or_execute(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 10.0, b"", b"")
        ) as execute:
            self.assertEqual(self.execute(manifest, manifest.tasks["work"]).status, "MISS_EXECUTED")
            with patch("zerorun.hermetic.StagedWorkspace.prepare", side_effect=AssertionError("hit staged")):
                hit = self.execute(manifest, manifest.tasks["work"])
            self.assertEqual(hit.status, "HIT_REUSED")
            self.assertEqual(hit.phase_ms["snapshot_prepare"], 0.0)
            self.assertIn("readonly_hit_path", hit.phase_ms)
            self.assertEqual(execute.call_count, 1)

    def test_readonly_hit_rechecks_source_after_authenticated_lookup(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        original_lookup = hermetic_module._load_result_entry

        def mutate_after_lookup(*args, **kwargs):
            entry = original_lookup(*args, **kwargs)
            (manifest.root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
            return entry

        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 10.0, b"", b"")
        ) as execute:
            self.execute(manifest, manifest.tasks["work"])
            with patch("zerorun.hermetic._load_result_entry", side_effect=mutate_after_lookup):
                result = self.execute(manifest, manifest.tasks["work"])
            self.assertEqual(result.status, "REJECTED_INPUT_RACE")
            self.assertEqual(result.exit_code, 75)
            self.assertEqual(execute.call_count, 1)

    def test_force_and_verify_bypass_readonly_hit_optimization(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 10.0, b"", b"")
        ) as execute:
            self.execute(manifest, manifest.tasks["work"])
            with patch("zerorun.hermetic._try_readonly_whole_task_hit", side_effect=AssertionError("verification shortcut")):
                self.assertEqual(self.execute(manifest, manifest.tasks["work"], force=True).status, "FORCE_VERIFY_MATCH")
                self.assertEqual(self.execute(manifest, manifest.tasks["work"], verify=True).status, "VERIFY_MATCH")
            self.assertEqual(execute.call_count, 3)

    def test_v2_fingerprint_binds_runtime_identity(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        _, env_fp = hermetic_environment(task)
        key1, _ = task_fingerprint_v2(manifest, task, RUNTIME, environment_fingerprint=env_fp)
        changed = dict(RUNTIME)
        changed["config_sha256"] = "e" * 64
        key2, _ = task_fingerprint_v2(manifest, task, changed, environment_fingerprint=env_fp)
        self.assertNotEqual(key1, key2)

    def test_v2_rejects_missing_and_symlinked_closure_members(self):
        temporary, path = self.make_project(inputs=["missing.py"])
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        _, env_fp = hermetic_environment(manifest.tasks["work"])
        with self.assertRaisesRegex(ConfigurationError, "unsupported missing"):
            task_fingerprint_v2(manifest, manifest.tasks["work"], RUNTIME, environment_fingerprint=env_fp)

        temporary2, path2 = self.make_project(inputs=["link.py"])
        self.addCleanup(temporary2.cleanup)
        root2 = path2.parent
        try:
            (root2 / "link.py").symlink_to(root2 / "src.py")
        except NotImplementedError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        except OSError as exc:
            unavailable = {
                errno.EACCES,
                errno.EPERM,
                errno.ENOSYS,
                getattr(errno, "ENOTSUP", -1),
                getattr(errno, "EOPNOTSUPP", -1),
            }
            if exc.errno not in unavailable and getattr(exc, "winerror", None) != 1314:
                raise
            self.skipTest(f"symlink creation is unavailable: {exc}")
        manifest2 = load_manifest(path2)
        _, env_fp2 = hermetic_environment(manifest2.tasks["work"])
        with self.assertRaisesRegex(ConfigurationError, "unsupported symlink"):
            task_fingerprint_v2(manifest2, manifest2.tasks["work"], RUNTIME, environment_fingerprint=env_fp2)

    def test_v2_miss_then_exact_hit_does_not_reexecute_or_replay_streams(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        output = io.BytesIO()
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 120.0, b"fresh output\n", b"")
        ) as execute:
            first = run_task(manifest, task, stdout=output, stderr=io.BytesIO())
            before_hit = output.getvalue()
            second = run_task(manifest, task, stdout=output, stderr=io.BytesIO())
        self.assertEqual(first.status, "MISS_EXECUTED")
        self.assertEqual(second.status, "HIT_REUSED")
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(output.getvalue(), before_hit)
        metadata = json.loads((manifest.root / ".zerorun" / "cache" / first.cache_key / "metadata.json").read_text())
        self.assertEqual(metadata["schema"], 7)
        self.assertNotIn("streams", metadata)
        self.assertNotIn("outputs", metadata)

        execution_manifest = execute.call_args.args[0]
        self.assertNotEqual(execution_manifest.root, manifest.root)
        self.assertFalse(execution_manifest.root.exists())

    def test_v2_cached_hit_rechecks_live_source_after_lookup(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 100.0, b"", b"")
        ) as execute:
            seeded = self.execute(manifest, task)
            real_lookup = hermetic_module._load_result_entry

            def edit_after_lookup(*args, **kwargs):
                cached = real_lookup(*args, **kwargs)
                (manifest.root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
                return cached

            with patch(
                "zerorun.hermetic._load_result_entry", side_effect=edit_after_lookup
            ):
                raced = self.execute(manifest, task)

        self.assertEqual(seeded.status, "MISS_EXECUTED")
        self.assertEqual(raced.status, "REJECTED_INPUT_RACE")
        self.assertEqual(raced.exit_code, 75)
        self.assertEqual(execute.call_count, 1)

    def test_v2_execution_uses_snapshot_and_rejects_live_edit(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        execution_roots = []

        def execute_from_snapshot(execution_manifest, *_args, **_kwargs):
            execution_roots.append(execution_manifest.root)
            self.assertNotEqual(execution_manifest.root, manifest.root)
            self.assertEqual(
                (execution_manifest.root / "src.py").read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )
            (manifest.root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
            return 0, 100.0, b"", b""

        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", side_effect=execute_from_snapshot
        ):
            result = self.execute(manifest, task)

        self.assertEqual(result.status, "REJECTED_INPUT_RACE")
        self.assertEqual(result.exit_code, 75)
        self.assertEqual(len(execution_roots), 1)
        self.assertFalse(execution_roots[0].exists())

    def test_v2_refuses_git_metadata_in_reviewed_closure(self):
        temporary, path = self.make_project(inputs=[".git"])
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute"
        ) as execute:
            with self.assertRaisesRegex(ConfigurationError, "cannot include .git state"):
                self.execute(manifest, task)
        execute.assert_not_called()

    def test_v2_batch_preserves_exact_per_task_snapshots_and_hits(self):
        temporary, path = self.make_batch_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        tasks = tuple(manifest.tasks.values())
        streams = {task.name: io.BytesIO() for task in tasks}
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 120.0, b"", b"")
        ) as execute:
            first = run_tasks(
                manifest,
                tasks,
                stdout_by_task=streams,
                stderr_by_task=streams,
            )
            with patch(
                "zerorun.fingerprint.sha256_file", wraps=fingerprint_module.sha256_file
            ) as sha256:
                second = run_tasks(
                    manifest,
                    tasks,
                    stdout_by_task=streams,
                    stderr_by_task=streams,
                )

        self.assertEqual([row.status for row in first], ["MISS_EXECUTED"] * 3)
        self.assertEqual([row.status for row in second], ["HIT_REUSED"] * 3)
        self.assertEqual(execute.call_count, 3)
        # Each task gets its own namespace so another batch member cannot widen
        # what its command observes. Hashing remains linearly bounded.
        self.assertGreaterEqual(sha256.call_count, 6)
        self.assertLessEqual(sha256.call_count, 36)

    def test_v2_batch_stages_expanded_paths_with_literal_glob_characters(self):
        temporary, path = self.make_batch_project()
        self.addCleanup(temporary.cleanup)
        root = path.parent
        literal = root / "literal[1].py"
        literal.write_text("VALUE = 1\n", encoding="utf-8")
        raw = json.loads(path.read_text(encoding="utf-8"))
        for task in raw["tasks"].values():
            task["inputs"] = ["src.py", "literal*.py"]
        path.write_text(json.dumps(raw), encoding="utf-8")
        manifest = load_manifest(path)
        tasks = tuple(manifest.tasks.values())
        streams = {task.name: io.BytesIO() for task in tasks}

        def execute_from_snapshot(execution_manifest, *_args, **_kwargs):
            self.assertEqual(
                (execution_manifest.root / literal.name).read_text(encoding="utf-8"),
                "VALUE = 1\n",
            )
            return 0, 10.0, b"", b""

        with patch(
            "zerorun.hermetic.inspect_runtime", return_value=RUNTIME
        ), patch(
            "zerorun.hermetic._docker_execute",
            side_effect=execute_from_snapshot,
        ):
            results = run_tasks(
                manifest,
                tasks,
                stdout_by_task=streams,
                stderr_by_task=streams,
            )

        self.assertEqual([row.status for row in results], ["MISS_EXECUTED"] * 3)

    def test_v2_batch_never_widens_one_task_with_another_tasks_inputs(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        root = path.parent
        (root / "a.txt").write_text("a\n", encoding="utf-8")
        (root / "b.txt").write_text("b\n", encoding="utf-8")
        base = json.loads(path.read_text(encoding="utf-8"))["tasks"]["work"]
        payload = {
            "version": 2,
            "tasks": {
                "a": {
                    **base,
                    "inputs": ["a.txt"],
                    "command": ["python", "-c", "import pathlib; raise SystemExit(not pathlib.Path('b.txt').exists())"],
                },
                "b": {
                    **base,
                    "inputs": ["b.txt"],
                    "command": ["python", "-c", "pass"],
                },
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        manifest = load_manifest(path)
        tasks = tuple(manifest.tasks.values())
        streams = {task.name: io.BytesIO() for task in tasks}

        def execute_from_exact_view(execution_manifest, task, *_args, **_kwargs):
            if task.name == "a":
                return (
                    0 if (execution_manifest.root / "b.txt").exists() else 1,
                    10.0,
                    b"",
                    b"",
                )
            return 0, 10.0, b"", b""

        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", side_effect=execute_from_exact_view
        ) as execute:
            batch = run_tasks(
                manifest,
                tasks,
                stdout_by_task=streams,
                stderr_by_task=streams,
            )
            standalone = self.execute(manifest, manifest.tasks["a"])

        self.assertEqual([row.status for row in batch], ["MISS_FAILED", "MISS_EXECUTED"])
        self.assertEqual(standalone.status, "MISS_FAILED")
        self.assertEqual(execute.call_count, 3)

    def test_v2_batch_validates_every_stream_before_first_execution(self):
        temporary, path = self.make_batch_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        tasks = tuple(manifest.tasks.values())
        incomplete = {tasks[0].name: io.BytesIO()}
        complete = {task.name: io.BytesIO() for task in tasks}
        with patch("zerorun.hermetic_batch.run_hermetic_task") as execute:
            with self.assertRaisesRegex(ConfigurationError, "no stream"):
                run_tasks(
                    manifest,
                    tasks,
                    stdout_by_task=incomplete,
                    stderr_by_task=complete,
                )
        execute.assert_not_called()

    def test_v2_batch_never_returns_hits_after_a_mid_request_source_edit(self):
        temporary, path = self.make_batch_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        tasks = tuple(manifest.tasks.values())
        streams = {task.name: io.BytesIO() for task in tasks}
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 120.0, b"", b"")
        ):
            seeded = run_tasks(
                manifest,
                tasks,
                stdout_by_task=streams,
                stderr_by_task=streams,
            )
        self.assertEqual([row.status for row in seeded], ["MISS_EXECUTED"] * 3)

        real_lookup = hermetic_module._load_result_entry
        edited = False

        def edit_after_lookup(*args, **kwargs):
            nonlocal edited
            cached = real_lookup(*args, **kwargs)
            if not edited:
                (manifest.root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
                edited = True
            return cached

        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._load_result_entry", side_effect=edit_after_lookup
        ), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 120.0, b"", b"")
        ) as execute:
            raced = run_tasks(
                manifest,
                tasks,
                stdout_by_task=streams,
                stderr_by_task=streams,
            )

        self.assertEqual(
            [row.status for row in raced],
            ["REJECTED_INPUT_RACE", "MISS_EXECUTED", "MISS_EXECUTED"],
        )
        self.assertEqual([row.exit_code for row in raced], [75, 0, 0])
        self.assertEqual(execute.call_count, 2)

    def test_v2_edit_forces_fresh_execution(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 100.0, b"", b"")
        ) as execute:
            first = self.execute(manifest, task)
            (manifest.root / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
            second = self.execute(manifest, task)
        self.assertEqual(first.status, "MISS_EXECUTED")
        self.assertEqual(second.status, "MISS_EXECUTED")
        self.assertNotEqual(first.cache_key, second.cache_key)
        self.assertEqual(execute.call_count, 2)

    def test_v2_command_source_edit_forces_fresh_execution(self):
        temporary, path = self.make_project(
            extra={"command": ["python", "check.py"]}
        )
        self.addCleanup(temporary.cleanup)
        root = path.parent
        (root / "check.py").write_text("print('one')\n", encoding="utf-8")
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", return_value=(0, 100.0, b"", b"")
        ) as execute:
            first = self.execute(manifest, task)
            second = self.execute(manifest, task)
            (root / "check.py").write_text("print('two')\n", encoding="utf-8")
            third = self.execute(manifest, task)
        self.assertEqual(
            [first.status, second.status, third.status],
            ["MISS_EXECUTED", "HIT_REUSED", "MISS_EXECUTED"],
        )
        self.assertNotEqual(first.cache_key, third.cache_key)
        self.assertEqual(execute.call_count, 2)

    def test_v2_verify_failure_quarantines_cached_success(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute", side_effect=[(0, 100.0, b"", b""), (1, 80.0, b"", b"failed")]
        ):
            first = self.execute(manifest, task)
            verified = self.execute(manifest, task, verify=True)
        self.assertEqual(first.status, "MISS_EXECUTED")
        self.assertEqual(verified.status, "VERIFY_MISMATCH")
        self.assertEqual(verified.exit_code, 86)
        self.assertTrue((manifest.root / ".zerorun" / "cache" / first.cache_key / "QUARANTINED").is_file())

    def test_v2_infrastructure_timeout_never_quarantines_cached_success(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        with patch("zerorun.hermetic.inspect_runtime", return_value=RUNTIME), patch(
            "zerorun.hermetic._docker_execute",
            side_effect=[
                (0, 100.0, b"", b""),
                ConfigurationError("hermetic container execution timed out"),
            ],
        ):
            first = self.execute(manifest, task)
            with self.assertRaisesRegex(ConfigurationError, "timed out"):
                self.execute(manifest, task, verify=True)
        entry = manifest.root / ".zerorun" / "cache" / first.cache_key
        self.assertTrue(entry.is_dir())
        self.assertFalse((entry / "QUARANTINED").exists())

    def test_v2_action_locks_are_per_key(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        store = Store(manifest.root)
        key_a = "a" * 64
        key_b = "b" * 64
        with _action_lock(store, key_a, timeout_seconds=0):
            with _action_lock(store, key_b, timeout_seconds=0):
                self.assertTrue(True)
            with self.assertRaises(ProjectLockBusy):
                with _action_lock(store, key_a, timeout_seconds=0):
                    pass

    def test_v2_runtime_attestation_is_refreshed_for_every_call(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        raw = {
            "Os": "linux",
            "Architecture": "amd64",
            "RepoDigests": [IMAGE],
            "RootFS": {"Type": "layers", "Layers": ["sha256:" + "c" * 64]},
            "Config": {"Env": ["LANG=C"]},
            "Id": "sha256:" + "b" * 64,
        }
        clear_runtime_identity_cache()
        self.addCleanup(clear_runtime_identity_cache)
        with patch("zerorun.oci.validate_host"), patch(
            "zerorun.oci._docker_path", return_value="/usr/bin/docker"
        ), patch("zerorun.oci._inspect", return_value=raw) as inspect:
            first = inspect_runtime(task)
            second = inspect_runtime(task)
        self.assertEqual(first, second)
        self.assertEqual(inspect.call_count, 2)

    def test_v2_runtime_inspection_uses_bounded_trusted_client_environment(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        raw = {
            "Os": "linux",
            "Architecture": "amd64",
            "RepoDigests": [IMAGE],
            "RootFS": {"Type": "layers", "Layers": ["sha256:" + "c" * 64]},
            "Config": {"Env": ["LANG=C"]},
            "Id": "sha256:" + "b" * 64,
        }
        completed = __import__("subprocess").CompletedProcess(
            [],
            0,
            json.dumps([raw]).encode("utf-8"),
            b"",
        )
        clear_runtime_identity_cache()
        self.addCleanup(clear_runtime_identity_cache)
        with patch.dict(
            os.environ,
            {"DOCKER_HOST": "tcp://container-only:2376"},
            clear=False,
        ), patch("zerorun.oci.validate_host"), patch(
            "zerorun.oci._docker_path", return_value="/usr/bin/docker"
        ), patch(
            "zerorun.oci._run_bounded_process", return_value=(completed, False)
        ) as run:
            identity = inspect_runtime(
                task,
                allow_pull=False,
                use_cache=False,
                repository_root=manifest.root,
            )
        self.assertEqual(identity["requested_image"], IMAGE)
        self.assertEqual(
            run.call_args.kwargs["environment"].get("DOCKER_HOST"),
            "tcp://container-only:2376",
        )
        self.assertEqual(run.call_args.kwargs["output_limit_bytes"], 8 * 1024 * 1024)

    def test_v2_rejects_task_environment_reserved_by_docker_client(self):
        temporary, path = self.make_project(extra={"env": ["DOCKER_HOST"]})
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        with patch("zerorun.oci.validate_host"), patch(
            "zerorun.oci._run_bounded_process"
        ) as run:
            with self.assertRaisesRegex(ConfigurationError, "reserved by the Docker client"):
                inspect_runtime(manifest.tasks["work"], repository_root=manifest.root)
        run.assert_not_called()

    def test_v2_runtime_refuses_repository_controlled_docker_without_execution(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        shim = manifest.root / "docker"
        shim.write_text("untrusted\n", encoding="utf-8")
        clear_runtime_identity_cache()
        self.addCleanup(clear_runtime_identity_cache)
        with patch("zerorun.oci.validate_host"), patch(
            "zerorun.oci.shutil.which", return_value=str(shim)
        ), patch("zerorun.oci._run_bounded_process") as run:
            with self.assertRaisesRegex(
                ConfigurationError, "repository-controlled Docker executable"
            ):
                inspect_runtime(
                    task,
                    use_cache=False,
                    repository_root=manifest.root,
                )
        run.assert_not_called()

    def test_v2_cached_runtime_identity_still_revalidates_docker_launcher(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        metadata = {
            "Os": "linux",
            "Architecture": "amd64",
            "RepoDigests": [IMAGE],
            "RootFS": {"Type": "layers", "Layers": ["sha256:" + "c" * 64]},
            "Config": {},
            "Id": RUNTIME["image_id"],
        }
        completed = __import__("subprocess").CompletedProcess(
            [], 0, json.dumps([metadata]).encode("utf-8"), b""
        )
        clear_runtime_identity_cache()
        self.addCleanup(clear_runtime_identity_cache)
        with patch("zerorun.oci.validate_host"), patch(
            "zerorun.oci._docker_path", return_value="/usr/bin/docker"
        ) as docker_path, patch(
            "zerorun.oci._run_bounded_process", return_value=(completed, False)
        ) as run:
            inspect_runtime(task, repository_root=manifest.root)
            docker_path.side_effect = ConfigurationError(
                "refusing a repository-controlled Docker executable"
            )
            with self.assertRaisesRegex(
                ConfigurationError, "repository-controlled Docker executable"
            ):
                inspect_runtime(task, repository_root=manifest.root)

        self.assertEqual(docker_path.call_count, 2)
        self.assertEqual(run.call_count, 1)

    def test_v2_docker_command_enforces_boundary(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        completed = __import__("subprocess").CompletedProcess(
            [], 0, CONTAINER_ID_BYTES, b""
        )
        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", return_value=(completed, False)
        ) as run, patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ) as inspect, patch(
            "zerorun.oci._remove_created_container", return_value=True
        ) as remove:
            code, _, _, _ = _docker_execute(manifest, task, RUNTIME, {})
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        argv = run.call_args_list[0].args[0]
        joined = " ".join(argv)
        self.assertIn("--pull never", joined)
        self.assertIn("--platform linux/amd64", joined)
        self.assertIn("--network none", joined)
        self.assertIn("--read-only", argv)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("--cpus 2", joined)
        self.assertIn("--memory 2g", joined)
        self.assertIn("--memory-swap 2g", joined)
        self.assertIn("no-new-privileges", joined)
        self.assertIn("dst=/workspace,readonly", joined)
        self.assertIn("/workspace/.zerorun:rw,nosuid,nodev,noexec", joined)
        self.assertIn("/workspace/.git:rw,nosuid,nodev,noexec", joined)
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["/usr/bin/docker", "start", "--attach", CONTAINER_ID],
        )
        inspect.assert_called_once_with(
            "/usr/bin/docker",
            CONTAINER_ID,
            environment=run.call_args_list[0].kwargs["environment"],
        )
        remove.assert_called_once_with(
            "/usr/bin/docker",
            CONTAINER_ID,
            environment=run.call_args_list[0].kwargs["environment"],
        )

    def test_v2_create_failure_is_infrastructure_not_task_result(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        failed = __import__("subprocess").CompletedProcess([], 125, b"", b"daemon error")
        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", return_value=(failed, False)
        ), patch(
            "zerorun.oci._inspect_container_exit_code"
        ) as inspect, patch(
            "zerorun.oci._force_remove_container", return_value=True
        ) as force_remove:
            with self.assertRaisesRegex(ConfigurationError, "container creation failed"):
                _docker_execute(manifest, manifest.tasks["work"], RUNTIME, {})
        inspect.assert_not_called()
        self.assertRegex(
            force_remove.call_args.args[1],
            r"^zerorun-task-[0-9a-f]{24}$",
        )

    def test_v2_start_transport_mismatch_is_infrastructure_failure(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        subprocess_module = __import__("subprocess")
        created = subprocess_module.CompletedProcess([], 0, CONTAINER_ID_BYTES, b"")
        transport_failure = subprocess_module.CompletedProcess([], 1, b"", b"transport")
        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process",
            side_effect=[(created, False), (transport_failure, False)],
        ), patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ), patch("zerorun.oci._remove_created_container", return_value=True):
            with self.assertRaisesRegex(ConfigurationError, "did not match"):
                _docker_execute(manifest, manifest.tasks["work"], RUNTIME, {})

    def test_v2_attested_nonzero_container_exit_is_a_task_failure(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        subprocess_module = __import__("subprocess")
        created = subprocess_module.CompletedProcess([], 0, CONTAINER_ID_BYTES, b"")
        task_failure = subprocess_module.CompletedProcess([], 1, b"", b"assertion")
        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process",
            side_effect=[(created, False), (task_failure, False)],
        ), patch(
            "zerorun.oci._inspect_container_exit_code", return_value=1
        ), patch("zerorun.oci._remove_created_container", return_value=True):
            code, _, _, stderr = _docker_execute(
                manifest, manifest.tasks["work"], RUNTIME, {}
            )
        self.assertEqual(code, 1)
        self.assertEqual(stderr, b"assertion")

    def test_v2_runtime_state_error_is_not_an_attested_task_exit(self):
        state = {
            "Status": "exited",
            "Running": False,
            "ExitCode": 1,
            "Error": "OCI runtime failed before task launch",
        }
        completed = __import__("subprocess").CompletedProcess(
            [], 0, json.dumps(state).encode("utf-8"), b""
        )
        with patch(
            "zerorun.oci._run_bounded_process", return_value=(completed, False)
        ):
            with self.assertRaisesRegex(
                ConfigurationError,
                "did not attest.*OCI runtime failed before task launch",
            ):
                _inspect_container_exit_code(
                    "/usr/bin/docker",
                    "zerorun-task-" + "a" * 24,
                    environment={"PATH": "/usr/bin"},
                )

    def test_v2_interrupt_during_final_cleanup_retries_exact_id(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        completed = __import__("subprocess").CompletedProcess(
            [], 0, CONTAINER_ID_BYTES, b""
        )
        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", return_value=(completed, False)
        ), patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ), patch(
            "zerorun.oci._remove_created_container", side_effect=KeyboardInterrupt
        ), patch(
            "zerorun.oci._force_remove_container", return_value=True
        ) as force_remove:
            with self.assertRaises(KeyboardInterrupt):
                _docker_execute(manifest, manifest.tasks["work"], RUNTIME, {})
        self.assertEqual(force_remove.call_count, 1)
        self.assertEqual(force_remove.call_args.args[1], CONTAINER_ID)

    def test_v2_container_values_never_become_docker_client_environment(self):
        temporary, path = self.make_project(extra={"env": ["TOKEN"]})
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        created = __import__("subprocess").CompletedProcess(
            [], 0, CONTAINER_ID_BYTES, b""
        )
        started = __import__("subprocess").CompletedProcess([], 0, b"task", b"")
        transported = []
        transported_paths = []

        def fake_run(command, **_kwargs):
            if len(command) > 1 and command[1] == "create":
                environment_file = Path(command[command.index("--env-file") + 1])
                transported.append(environment_file.read_text(encoding="utf-8"))
                transported_paths.append(environment_file)
                return created, False
            self.assertEqual(command, ["/usr/bin/docker", "start", "--attach", CONTAINER_ID])
            self.assertFalse(transported_paths[0].exists())
            return started, False

        with patch.dict(
            os.environ,
            {"DOCKER_HOST": "tcp://trusted-host:2376", "TOKEN": "container-secret"},
            clear=False,
        ), patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", side_effect=fake_run
        ) as run, patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ), patch("zerorun.oci._remove_created_container", return_value=True):
            environment, _ = hermetic_environment(task)
            code, _, _, _ = _docker_execute(manifest, task, RUNTIME, environment)

        self.assertEqual(code, 0)
        create_argv = run.call_args_list[0].args[0]
        client_environment = run.call_args_list[0].kwargs["environment"]
        self.assertIn("--env-file", create_argv)
        self.assertNotIn("container-secret", " ".join(create_argv))
        self.assertEqual(len(transported), 1)
        self.assertFalse(transported_paths[0].exists())
        self.assertIn("TOKEN=container-secret\n", transported[0])
        self.assertEqual(
            client_environment.get("DOCKER_HOST"), "tcp://trusted-host:2376"
        )
        self.assertNotIn("TOKEN", client_environment)
        self.assertNotIn("LANG", client_environment)

    def test_v2_timeout_force_removes_exact_container_id(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        completed = __import__("subprocess").CompletedProcess(
            [], 0, CONTAINER_ID_BYTES, b""
        )
        timed_out = __import__("subprocess").CompletedProcess([], 124, b"tail", b"error")

        def fake_run(command, **kwargs):
            if command[1] == "create":
                return completed, False
            kwargs["on_timeout"](True)
            return timed_out, True

        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", side_effect=fake_run
        ) as run, patch(
            "zerorun.oci._force_remove_container", return_value=True
        ) as remove, patch(
            "zerorun.oci._remove_created_container", return_value=True
        ):
            with self.assertRaisesRegex(
                ConfigurationError, "900 second execution boundary"
            ) as raised:
                _docker_execute(manifest, task, RUNTIME, {})

        self.assertNotIn("cleanup could not be confirmed", str(raised.exception))
        launch = run.call_args_list[0].args[0]
        name = launch[launch.index("--name") + 1]
        self.assertRegex(name, r"^zerorun-task-[0-9a-f]{24}$")
        self.assertEqual(remove.call_args.args, ("/usr/bin/docker", CONTAINER_ID))

    def test_v2_timeout_surfaces_unconfirmed_container_cleanup(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        task = manifest.tasks["work"]
        timed_out = __import__("subprocess").CompletedProcess([], 124, b"", b"")

        def fake_run(command, **kwargs):
            kwargs["on_timeout"](False)
            return timed_out, True

        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", side_effect=fake_run
        ), patch("zerorun.oci._force_remove_container", return_value=True), patch(
            "zerorun.oci._remove_created_container", return_value=True
        ):
            with self.assertRaisesRegex(
                ConfigurationError, "cleanup could not be confirmed"
            ) as raised:
                _docker_execute(manifest, task, RUNTIME, {})

        self.assertIn("zerorun-task-", str(raised.exception))

    def test_created_container_failed_normal_remove_force_retries_exact_id(self):
        subprocess_module = __import__("subprocess")
        name = "zerorun-task-" + "c" * 24
        command = [
            "/usr/bin/docker",
            "create",
            "--name",
            name,
            IMAGE,
        ]
        created = subprocess_module.CompletedProcess(
            command, 0, CONTAINER_ID_BYTES, b""
        )
        started = subprocess_module.CompletedProcess([], 0, b"task", b"")
        with patch(
            "zerorun.oci._run_bounded_process",
            side_effect=[(created, False), (started, False)],
        ), patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ), patch(
            "zerorun.oci._remove_created_container", return_value=False
        ) as remove, patch(
            "zerorun.oci._force_remove_container", return_value=True
        ) as force_remove:
            result = _run_created_container(
                command,
                docker="/usr/bin/docker",
                container_name=name,
                cwd=Path.cwd(),
                environment={"PATH": "/usr/bin"},
                execution_timeout_seconds=17,
                operation_label="unit",
            )

        self.assertEqual(result.returncode, 0)
        remove.assert_called_once_with(
            "/usr/bin/docker",
            CONTAINER_ID,
            environment={"PATH": "/usr/bin"},
        )
        force_remove.assert_called_once_with(
            "/usr/bin/docker",
            CONTAINER_ID,
            environment={"PATH": "/usr/bin"},
        )

    def test_created_container_reports_failed_exact_id_force_retry(self):
        subprocess_module = __import__("subprocess")
        name = "zerorun-task-" + "d" * 24
        command = [
            "/usr/bin/docker",
            "create",
            "--name",
            name,
            IMAGE,
        ]
        created = subprocess_module.CompletedProcess(
            command, 0, CONTAINER_ID_BYTES, b""
        )
        started = subprocess_module.CompletedProcess([], 0, b"task", b"")
        with patch(
            "zerorun.oci._run_bounded_process",
            side_effect=[(created, False), (started, False)],
        ), patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ), patch(
            "zerorun.oci._remove_created_container", return_value=False
        ), patch(
            "zerorun.oci._force_remove_container", return_value=False
        ):
            with self.assertRaisesRegex(
                ConfigurationError,
                rf"{name} \(id {CONTAINER_ID}\)",
            ):
                _run_created_container(
                    command,
                    docker="/usr/bin/docker",
                    container_name=name,
                    cwd=Path.cwd(),
                    environment={"PATH": "/usr/bin"},
                    execution_timeout_seconds=17,
                    operation_label="unit",
                )

    def test_created_container_uncertain_create_accepts_repeated_absence(self):
        subprocess_module = __import__("subprocess")
        name = "zerorun-task-" + "e" * 24
        command = [
            "/usr/bin/docker",
            "create",
            "--name",
            name,
            IMAGE,
        ]
        timed_out = subprocess_module.CompletedProcess([], 124, b"", b"")

        def time_out_create(_command, **kwargs):
            kwargs["on_timeout"](True)
            return timed_out, True

        with patch(
            "zerorun.oci._run_bounded_process", side_effect=time_out_create
        ), patch(
            "zerorun.oci._force_remove_container", return_value=True
        ) as force_remove, patch(
            "zerorun.oci._remove_created_container"
        ) as remove:
            with self.assertRaisesRegex(
                ConfigurationError,
                "container creation exceeded the 60 second execution boundary",
            ) as raised:
                _run_created_container(
                    command,
                    docker="/usr/bin/docker",
                    container_name=name,
                    cwd=Path.cwd(),
                    environment={"PATH": "/usr/bin"},
                    execution_timeout_seconds=999,
                    operation_label="unit",
                )

        self.assertNotIn("cleanup could not be confirmed", str(raised.exception))
        self.assertEqual(force_remove.call_count, 1)
        self.assertTrue(all(call.args[1] == name for call in force_remove.call_args_list))
        remove.assert_not_called()

    def test_created_container_rejects_invalid_create_id_and_cleans_name(self):
        subprocess_module = __import__("subprocess")
        name = "zerorun-task-" + "f" * 24
        command = [
            "/usr/bin/docker",
            "create",
            "--name",
            name,
            IMAGE,
        ]
        malformed = subprocess_module.CompletedProcess(command, 0, b"short-id\n", b"")
        with patch(
            "zerorun.oci._run_bounded_process", return_value=(malformed, False)
        ) as run, patch(
            "zerorun.oci._force_remove_container", return_value=True
        ) as force_remove, patch(
            "zerorun.oci._inspect_container_exit_code"
        ) as inspect:
            with self.assertRaisesRegex(
                ConfigurationError, "valid immutable container ID"
            ):
                _run_created_container(
                    command,
                    docker="/usr/bin/docker",
                    container_name=name,
                    cwd=Path.cwd(),
                    environment={"PATH": "/usr/bin"},
                    execution_timeout_seconds=17,
                    operation_label="unit",
                )

        self.assertEqual(run.call_count, 1)
        force_remove.assert_called_once_with(
            "/usr/bin/docker", name, environment={"PATH": "/usr/bin"}
        )
        inspect.assert_not_called()

    def test_created_container_interrupt_notes_unconfirmed_exact_id(self):
        subprocess_module = __import__("subprocess")
        name = "zerorun-task-" + "1" * 24
        command = [
            "/usr/bin/docker",
            "create",
            "--name",
            name,
            IMAGE,
        ]
        created = subprocess_module.CompletedProcess(
            command, 0, CONTAINER_ID_BYTES, b""
        )
        calls = 0

        def interrupt_start(_command, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return created, False
            kwargs["on_timeout"](True)
            raise KeyboardInterrupt

        with patch(
            "zerorun.oci._run_bounded_process", side_effect=interrupt_start
        ), patch(
            "zerorun.oci._force_remove_container", return_value=False
        ) as force_remove:
            with self.assertRaises(KeyboardInterrupt) as raised:
                _run_created_container(
                    command,
                    docker="/usr/bin/docker",
                    container_name=name,
                    cwd=Path.cwd(),
                    environment={"PATH": "/usr/bin"},
                    execution_timeout_seconds=17,
                    operation_label="unit",
                )

        surfaced = " ".join(getattr(raised.exception, "__notes__", ()))
        surfaced += " " + repr(raised.exception.args)
        self.assertIn("could not confirm cleanup", surfaced)
        self.assertIn(name, surfaced)
        self.assertIn(CONTAINER_ID, surfaced)
        self.assertEqual(force_remove.call_count, 2)

    def test_created_container_env_file_is_removed_when_create_raises(self):
        name = "zerorun-task-" + "2" * 24
        command = [
            "/usr/bin/docker",
            "create",
            "--name",
            name,
            "--env-file",
            "",
            IMAGE,
        ]
        environment_path = None

        def fail_create(launch, **_kwargs):
            nonlocal environment_path
            environment_path = Path(launch[5])
            self.assertTrue(environment_path.exists())
            if os.name != "nt":
                self.assertEqual(
                    stat.S_IMODE(environment_path.stat().st_mode),
                    0o600,
                )
            raise KeyboardInterrupt

        def confirm_cleanup_after_secret_removal(*_args, **_kwargs):
            self.assertIsNotNone(environment_path)
            self.assertFalse(environment_path.exists())
            return True

        with patch(
            "zerorun.oci._run_bounded_process", side_effect=fail_create
        ), patch(
            "zerorun.oci._force_remove_container",
            side_effect=confirm_cleanup_after_secret_removal,
        ):
            with self.assertRaises(KeyboardInterrupt):
                _run_created_container(
                    command,
                    docker="/usr/bin/docker",
                    container_name=name,
                    cwd=Path.cwd(),
                    environment={"PATH": "/usr/bin"},
                    execution_timeout_seconds=17,
                    operation_label="unit",
                    container_environment=b"TOKEN=secret\n",
                    environment_repository_root=Path.cwd(),
                    environment_file_index=5,
                )

        self.assertIsNotNone(environment_path)
        self.assertFalse(environment_path.exists())

    def test_container_cleanup_requires_success_or_explicit_not_found(self):
        subprocess_module = __import__("subprocess")
        absent = subprocess_module.CompletedProcess(
            [], 1, b"", b"Error: No such container: exact-name"
        )
        removed = subprocess_module.CompletedProcess([], 0, b"", b"")
        denied = subprocess_module.CompletedProcess([], 1, b"", b"permission denied")
        name = "zerorun-task-" + "a" * 24

        cases = (
            ([(absent, False)] * 3, True, 3),
            ([(removed, False), (absent, False), (absent, False), (absent, False)], True, 4),
            ([(absent, False), (removed, False), (absent, False), (absent, False), (absent, False)], True, 5),
            ([(denied, False)], False, 1),
            ([(absent, True)], False, 1),
        )
        for responses, expected, calls in cases:
            with self.subTest(expected=expected, calls=calls), patch(
                "zerorun.oci._run_bounded_process", side_effect=responses
            ) as run, patch("zerorun.oci.time.sleep"):
                confirmed = _force_remove_container(
                    "/usr/bin/docker",
                    name,
                    environment={"PATH": "/usr/bin"},
                )
            self.assertEqual(confirmed, expected)
            self.assertEqual(run.call_count, calls)
            self.assertEqual(
                run.call_args.args[0],
                ["/usr/bin/docker", "rm", "-f", name],
            )

    def test_bounded_process_drains_both_streams_and_retains_tails(self):
        script = (
            "import sys\n"
            "sys.stdout.buffer.write(b'A' * 200000 + b'OUT-END')\n"
            "sys.stdout.buffer.flush()\n"
            "sys.stderr.buffer.write(b'B' * 200000 + b'ERR-END')\n"
            "sys.stderr.buffer.flush()\n"
        )
        process, timed_out = _run_bounded_process(
            [sys.executable, "-c", script],
            cwd=None,
            environment=dict(os.environ),
            timeout_seconds=10,
            output_limit_bytes=4096,
        )
        self.assertFalse(timed_out)
        self.assertEqual(process.returncode, 0)
        self.assertLessEqual(len(process.stdout), 4096)
        self.assertLessEqual(len(process.stderr), 4096)
        self.assertIn(b"output truncated", process.stdout)
        self.assertIn(b"output truncated", process.stderr)
        self.assertTrue(process.stdout.endswith(b"OUT-END"))
        self.assertTrue(process.stderr.endswith(b"ERR-END"))

    def test_bounded_process_supplies_and_closes_explicit_stdin(self):
        payload = (b'{"jsonrpc":"2.0"}\n' * 1024) + b"FINAL\n"
        script = (
            "import hashlib, sys\n"
            "data = sys.stdin.buffer.read()\n"
            "sys.stdout.write(str(len(data)) + '\\n')\n"
            "sys.stderr.write(hashlib.sha256(data).hexdigest() + '\\n')\n"
        )
        process, timed_out = _run_bounded_process(
            [sys.executable, "-c", script],
            cwd=None,
            environment=dict(os.environ),
            timeout_seconds=10,
            output_limit_bytes=4096,
            input_bytes=payload,
        )

        self.assertFalse(timed_out)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(process.stdout.strip(), str(len(payload)).encode("ascii"))
        self.assertEqual(
            process.stderr.strip(),
            __import__("hashlib").sha256(payload).hexdigest().encode("ascii"),
        )

    def test_bounded_process_timeout_terminates_descendants(self):
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        process, timed_out = _run_bounded_process(
            [sys.executable, "-c", script],
            cwd=None,
            environment=dict(os.environ),
            timeout_seconds=0.25,
            output_limit_bytes=4096,
        )

        self.assertTrue(timed_out)
        self.assertEqual(process.returncode, 124)
        child_pid = int(process.stdout.strip())
        if os.name == "nt":
            import ctypes

            process_query_limited_information = 0x1000
            still_active = 259

            def child_is_running() -> bool:
                handle = ctypes.windll.kernel32.OpenProcess(
                    process_query_limited_information, False, child_pid
                )
                if not handle:
                    return False
                try:
                    exit_code = ctypes.c_ulong()
                    if not ctypes.windll.kernel32.GetExitCodeProcess(
                        handle, ctypes.byref(exit_code)
                    ):
                        return False
                    return exit_code.value == still_active
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)

            deadline = __import__("time").monotonic() + 2
            while child_is_running() and __import__("time").monotonic() < deadline:
                __import__("time").sleep(0.02)
            self.assertFalse(child_is_running())
            return
        proc_stat = Path(f"/proc/{child_pid}/stat")
        deadline = __import__("time").monotonic() + 2
        while proc_stat.exists() and __import__("time").monotonic() < deadline:
            fields = proc_stat.read_text(encoding="utf-8").split()
            if len(fields) >= 3 and fields[2] == "Z":
                break
            __import__("time").sleep(0.02)
        if proc_stat.exists():
            self.assertEqual(proc_stat.read_text(encoding="utf-8").split()[2], "Z")

    def test_bounded_process_leader_exit_cannot_leave_background_child(self):
        script = (
            "import subprocess, sys\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n"
            "print(child.pid, flush=True)\n"
        )
        started = __import__("time").monotonic()
        process, timed_out = _run_bounded_process(
            [sys.executable, "-c", script],
            cwd=None,
            environment=dict(os.environ),
            timeout_seconds=3,
            output_limit_bytes=4096,
        )
        elapsed = __import__("time").monotonic() - started
        self.assertTrue(timed_out)
        self.assertEqual(process.returncode, 124)
        self.assertLess(elapsed, 5)
        self.assertRegex(process.stdout.strip(), rb"^\d+$")

    def test_bounded_process_interrupt_cleans_and_reraises(self):
        fake = MagicMock()
        fake.stdout = io.BytesIO()
        fake.stderr = io.BytesIO()
        fake.stdin = None
        fake.pid = 12345
        fake.wait.side_effect = [KeyboardInterrupt(), None]
        fake.poll.return_value = 0
        fake.returncode = 0
        callbacks = []
        with patch("zerorun.oci.subprocess.Popen", return_value=fake), patch(
            "zerorun.oci._windows_create_kill_on_close_job", return_value=123
        ), patch(
            "zerorun.oci._windows_resume_main_thread", return_value=True
        ), patch(
            "zerorun.oci._windows_close_handle"
        ), patch("zerorun.oci._terminate_process_tree") as terminate:
            with self.assertRaises(KeyboardInterrupt):
                _run_bounded_process(
                    ["trusted-command"],
                    cwd=None,
                    environment={},
                    timeout_seconds=10,
                    on_timeout=callbacks.append,
                )
        terminate.assert_called_once_with(
            fake, include_descendants_if_exited=True
        )
        self.assertEqual(callbacks, [True])

    def test_bounded_process_reader_setup_failure_still_kills_and_reaps(self):
        fake = MagicMock()
        fake.stdout = io.BytesIO()
        fake.stderr = io.BytesIO()
        fake.stdin = None
        fake.pid = 12345
        fake.wait.return_value = None
        fake.poll.return_value = 0
        callbacks = []
        with patch("zerorun.oci.subprocess.Popen", return_value=fake), patch(
            "zerorun.oci._windows_create_kill_on_close_job", return_value=123
        ), patch(
            "zerorun.oci._windows_resume_main_thread", return_value=True
        ), patch(
            "zerorun.oci._windows_close_handle"
        ), patch(
            "zerorun.oci.threading.Thread.start",
            side_effect=RuntimeError("reader setup failed"),
        ), patch("zerorun.oci._terminate_process_tree") as terminate:
            with self.assertRaisesRegex(RuntimeError, "reader setup failed"):
                _run_bounded_process(
                    ["trusted-command"],
                    cwd=None,
                    environment={},
                    timeout_seconds=10,
                    on_timeout=callbacks.append,
                )

        terminate.assert_called_once_with(
            fake, include_descendants_if_exited=True
        )
        fake.wait.assert_called_once_with(timeout=5)
        self.assertEqual(callbacks, [True])

    def test_bounded_process_posix_launch_still_uses_new_session(self):
        fake = MagicMock()
        fake.stdout = io.BytesIO()
        fake.stderr = io.BytesIO()
        fake.stdin = None
        fake.pid = 12345
        fake.wait.return_value = None
        fake.poll.return_value = 0
        fake.returncode = 0
        with patch("zerorun.oci.os.name", "posix"), patch(
            "zerorun.oci.subprocess.Popen", return_value=fake
        ) as popen, patch(
            "zerorun.oci._process_group_is_alive", return_value=False
        ), patch(
            "zerorun.oci._windows_create_kill_on_close_job"
        ) as create_job, patch(
            "zerorun.oci._windows_resume_main_thread"
        ) as resume:
            process, timed_out = _run_bounded_process(
                ["trusted-command"],
                cwd=None,
                environment={},
                timeout_seconds=10,
            )

        self.assertFalse(timed_out)
        self.assertEqual(process.returncode, 0)
        self.assertIs(popen.call_args.kwargs["start_new_session"], True)
        self.assertNotIn("creationflags", popen.call_args.kwargs)
        create_job.assert_not_called()
        resume.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object containment only")
    def test_bounded_process_job_assignment_failure_never_runs_uncontained(self):
        captured = []
        callbacks = []

        def refuse_job(process):
            captured.append(process)
            return None

        with patch(
            "zerorun.oci._windows_create_kill_on_close_job",
            side_effect=refuse_job,
        ), patch("zerorun.oci._windows_resume_main_thread") as resume:
            with self.assertRaisesRegex(OSError, "Windows Job Object"):
                _run_bounded_process(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=None,
                    environment=dict(os.environ),
                    timeout_seconds=10,
                    on_timeout=callbacks.append,
                )

        self.assertEqual(len(captured), 1)
        process = captured[0]
        try:
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdout.closed)
            self.assertTrue(process.stderr.closed)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        resume.assert_not_called()
        self.assertEqual(callbacks, [True])

    @unittest.skipUnless(os.name == "nt", "Windows Job Object containment only")
    def test_bounded_process_resume_failure_closes_job_and_reaps(self):
        fake = MagicMock()
        fake.stdout = io.BytesIO()
        fake.stderr = io.BytesIO()
        fake.stdin = io.BytesIO()
        fake.pid = 12345
        fake.wait.return_value = None
        fake.poll.return_value = 0
        with patch("zerorun.oci.subprocess.Popen", return_value=fake) as popen, patch(
            "zerorun.oci._windows_create_kill_on_close_job", return_value=456
        ), patch(
            "zerorun.oci._windows_resume_main_thread", return_value=False
        ), patch(
            "zerorun.oci._windows_close_handle"
        ) as close_job, patch(
            "zerorun.oci._terminate_process_tree"
        ) as terminate:
            with self.assertRaisesRegex(OSError, "resume"):
                _run_bounded_process(
                    ["trusted-command"],
                    cwd=None,
                    environment={},
                    timeout_seconds=10,
                    input_bytes=b"secret",
                )

        flags = popen.call_args.kwargs["creationflags"]
        self.assertTrue(
            flags
            & getattr(__import__("subprocess"), "CREATE_SUSPENDED", 0x00000004)
        )
        close_job.assert_called_once_with(456)
        terminate.assert_called_once_with(
            fake, include_descendants_if_exited=True
        )
        fake.wait.assert_called_once_with(timeout=5)
        self.assertTrue(fake.stdin.closed)
        self.assertTrue(fake.stdout.closed)
        self.assertTrue(fake.stderr.closed)

    def test_v2_linked_worktree_git_file_is_masked(self):
        temporary, path = self.make_project()
        self.addCleanup(temporary.cleanup)
        manifest = load_manifest(path)
        (manifest.root / ".git").rmdir()
        git_directory = Path(tempfile.mkdtemp(prefix="zerorun-worktree-git-"))
        self.addCleanup(shutil.rmtree, git_directory, True)
        (manifest.root / ".git").write_text(
            f"gitdir: {git_directory}\n",
            encoding="utf-8",
        )
        completed = __import__("subprocess").CompletedProcess(
            [], 0, CONTAINER_ID_BYTES, b""
        )
        with patch("zerorun.oci._docker_path", return_value="/usr/bin/docker"), patch(
            "zerorun.oci._run_bounded_process", return_value=(completed, False)
        ) as run, patch(
            "zerorun.oci._inspect_container_exit_code", return_value=0
        ), patch("zerorun.oci._remove_created_container", return_value=True):
            _docker_execute(manifest, manifest.tasks["work"], RUNTIME, {})
        joined = " ".join(run.call_args_list[0].args[0])
        self.assertIn(
            "type=bind,src=/dev/null,dst=/workspace/.git,readonly",
            joined,
        )


if __name__ == "__main__":
    unittest.main()
