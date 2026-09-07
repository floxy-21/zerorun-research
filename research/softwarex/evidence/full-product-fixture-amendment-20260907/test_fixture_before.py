from __future__ import annotations

import copy
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from tools import validate_release_prerequisites as release
from tools.normalize_sdist import _ArchiveMember, _read_members, _write_canonical_archive


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SHA = subprocess.check_output(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
).strip()
CANDIDATE_EPOCH = int(
    subprocess.check_output(
        ["git", "-C", str(ROOT), "show", "-s", "--format=%ct", CANDIDATE_SHA],
        text=True,
    ).strip()
)


def _write(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("mutation", ["truncate", "grow", "rewrite"])
def test_release_reader_rejects_in_place_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
) -> None:
    path = tmp_path / "release-input.json"
    original_bytes = b"{}\n"
    path.write_bytes(original_bytes)
    real_read = release.os.read
    mutated = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        if not mutated:
            mutated = True
            before = path.stat()
            if mutation == "truncate":
                replacement = b"{"
            elif mutation == "grow":
                replacement = original_bytes + b" "
            else:
                replacement = b"[]\n"
            path.write_bytes(replacement)
            os.utime(
                path,
                ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
            )
        return real_read(descriptor, size)

    monkeypatch.setattr(release.os, "read", racing_read)
    with pytest.raises(release.PrerequisiteError, match="changed while being read"):
        release._read_regular_bytes(path, label="release fixture", max_bytes=64)


def _source_payloads() -> dict[str, bytes]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_bytes()
        for path in sorted(
            (ROOT / "zerorun").rglob("*.py"),
            key=lambda item: (
                item.relative_to(ROOT).as_posix().casefold(),
                item.relative_to(ROOT).as_posix(),
            ),
        )
    }


def _test_payloads() -> dict[str, bytes]:
    return {
        path.relative_to(ROOT).as_posix(): path.read_bytes()
        for path in sorted(
            (ROOT / "tests").rglob("*.py"),
            key=lambda item: (
                item.relative_to(ROOT).as_posix().casefold(),
                item.relative_to(ROOT).as_posix(),
            ),
        )
    }


def _package_metadata() -> bytes:
    return (
        b"Metadata-Version: 2.4\n"
        b"Name: zerorun\n"
        b"Version: 0.5.1\n"
        b"Summary: Conservative deterministic test-result reuse for AI coding agents and CI\n"
        b"License-Expression: MIT\n"
        b"Requires-Python: >=3.10\n"
        b"License-File: LICENSE.txt\n\n"
    )


def _entry_points() -> bytes:
    return ("\n".join(release.EXPECTED_CONSOLE_SCRIPTS) + "\n").encode("utf-8")


def _canonical_digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _refresh_identity(payload: dict) -> None:
    unsigned = dict(payload)
    unsigned.pop("identity_sha256", None)
    payload["identity_sha256"] = _canonical_digest(unsigned)


def _test_build_provenance() -> dict:
    lockfiles = []
    for path in sorted((ROOT / "ci").glob("release-*.txt")):
        raw = path.read_bytes()
        lockfiles.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "name": path.name,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    result = {
        "source_archive": {
            "name": "source.tar",
            "size": 4096,
            "sha256": "a" * 64,
        },
        "python": {
            "implementation": "CPython",
            "version": "3.12.13",
            "cache_tag": "cpython-312",
            "executable": {
                "name": "python3.12",
                "size": 1024,
                "sha256": "b" * 64,
            },
        },
        "host": {
            "system": "Linux",
            "release": "6.11.0-test",
            "machine": "x86_64",
            "architecture": "64bit",
        },
        "build_tools": {
            "pip": "26.2.1",
            "build": "1.6.0",
            "setuptools": "84.0.0",
            "wheel": "0.48.0",
        },
        "lockfiles": lockfiles,
        "environment": {
            "SOURCE_DATE_EPOCH": str(CANDIDATE_EPOCH),
            "PYTHONHASHSEED": "0",
            "TZ": "UTC",
            "LC_ALL": "C.UTF-8",
            "build_isolation": False,
        },
        "independent_source_directories": 2,
        "byte_identical_builds": 2,
    }
    _refresh_identity(result)
    return result


def _test_installed_semantic_equivalence() -> dict:
    sources = _source_payloads()
    rows = [
        {
            "path": name.removeprefix("zerorun/"),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        for name, raw in sorted(sources.items())
    ]
    metadata = {
        "Name": ["zerorun"],
        "Version": ["0.5.1"],
        "Summary": [
            "Conservative deterministic test-result reuse for AI coding agents and CI"
        ],
        "Requires-Python": [">=3.10"],
        "License-Expression": ["MIT"],
        "Project-URL": [],
        "Requires-Dist": [],
    }
    entry_points = [
        {
            "group": "console_scripts",
            "name": "zerorun",
            "value": "zerorun.cli:main",
        },
        {
            "group": "console_scripts",
            "name": "zerorun-pytest-activate",
            "value": "zerorun.pytest_review_cli:activate_main",
        },
        {
            "group": "console_scripts",
            "name": "zerorun-pytest-prepare",
            "value": "zerorun.pytest_prepare_cli:main",
        },
        {
            "group": "console_scripts",
            "name": "zerorun-pytest-review",
            "value": "zerorun.pytest_review_cli:review_main",
        },
    ]
    semantic = {
        "version": "0.5.1",
        "module_tree": rows,
        "module_tree_sha256": _canonical_digest(rows),
        "metadata": metadata,
        "metadata_sha256": _canonical_digest(metadata),
        "entry_points": entry_points,
        "entry_points_sha256": _canonical_digest(entry_points),
    }
    result = {
        "schema": "zerorun.package-install-semantic-equivalence.v1",
        "wheel_vs_sdist_exact": True,
        "module_source_bytes_exact": True,
        "metadata_semantics_exact": True,
        "entry_points_exact": True,
        "semantic_sha256": _canonical_digest(semantic),
        "module_tree_sha256": semantic["module_tree_sha256"],
        "metadata_sha256": semantic["metadata_sha256"],
        "entry_points_sha256": semantic["entry_points_sha256"],
        "module_file_count": len(rows),
    }
    _refresh_identity(result)
    return result


def _comparison(*, node_count: int, reused_nodes: int, digest_seed: str) -> dict:
    product_collection = digest_seed * 64
    nodeid_digest = chr(ord(digest_seed) + 1) * 64
    return {
        "direct_exit_code": 0,
        "zerorun_exit_code": 0,
        "all_exit_codes_match": True,
        "exact_node_sequence_match": True,
        "product_snapshot_error": None,
        "product_collection_sha256": product_collection,
        "product_snapshot_collection_sha256": product_collection,
        "product_snapshot_nodeid_sha256": nodeid_digest,
        "product_snapshot_node_count": node_count,
        "independent_shadow_nodeid_sha256": nodeid_digest,
        "independent_shadow_node_count": node_count,
        "independent_shadow_exit_code": 0,
        "independent_shadow_complete_per_node_outcomes": True,
        "independent_shadow_all_nodes_non_failing": True,
        "reused_node_fresh_shadow_coverage": reused_nodes,
        "reused_node_fresh_shadow_denominator": reused_nodes,
        "reuse_shadow_valid": True,
        "comparison_pass": True,
        "coverage_basis": release.COMPARISON_COVERAGE_BASIS,
    }


def _build_test_wheel(path: Path) -> None:
    dist_info = "zerorun-0.5.1.dist-info"
    record_name = f"{dist_info}/RECORD"
    files = _source_payloads()
    files[f"{dist_info}/METADATA"] = _package_metadata()
    files[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: setuptools (84.0.0)\n"
        b"Root-Is-Purelib: true\nTag: py3-none-any\n\n"
    )
    files[f"{dist_info}/entry_points.txt"] = _entry_points()
    files[f"{dist_info}/top_level.txt"] = b"zerorun\n"
    files[f"{dist_info}/licenses/LICENSE.txt"] = (ROOT / "LICENSE.txt").read_bytes()
    rows: list[list[str]] = []
    for name, raw in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=")
        rows.append([name, f"sha256={digest.decode('ascii')}", str(len(raw))])
    rows.append([record_name, "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    files[record_name] = buffer.getvalue().encode("utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            archive.writestr(name, raw)


def _build_test_sdist(path: Path) -> None:
    archive_root = "zerorun-0.5.1"
    sources = _source_payloads()
    tests = _test_payloads()
    egg_info = "zerorun.egg-info"
    source_manifest = sorted(
        {
            "LICENSE.txt",
            "MANIFEST.in",
            "README.md",
            "pyproject.toml",
            *sources,
            *tests,
            f"{egg_info}/PKG-INFO",
            f"{egg_info}/SOURCES.txt",
            f"{egg_info}/dependency_links.txt",
            f"{egg_info}/entry_points.txt",
            f"{egg_info}/top_level.txt",
        }
    )
    files = {
        f"{archive_root}/PKG-INFO": _package_metadata(),
        **{
            f"{archive_root}/{name}": (ROOT / name).read_bytes()
            for name in (
                "pyproject.toml",
                "README.md",
                "LICENSE.txt",
                "MANIFEST.in",
            )
        },
        f"{archive_root}/setup.cfg": (
            b"[egg_info]\ntag_build = \ntag_date = 0\n\n"
        ),
        **{f"{archive_root}/{name}": raw for name, raw in sources.items()},
        **{f"{archive_root}/{name}": raw for name, raw in tests.items()},
        f"{archive_root}/{egg_info}/PKG-INFO": _package_metadata(),
        f"{archive_root}/{egg_info}/SOURCES.txt": (
            ("\n".join(source_manifest) + "\n").encode("utf-8")
        ),
        f"{archive_root}/{egg_info}/dependency_links.txt": b"\n",
        f"{archive_root}/{egg_info}/entry_points.txt": _entry_points(),
        f"{archive_root}/{egg_info}/top_level.txt": b"zerorun\n",
    }
    directories = {archive_root}
    for name in files:
        parent = Path(name).parent.as_posix()
        while parent != ".":
            directories.add(parent)
            if parent == archive_root:
                break
            parent = Path(parent).parent.as_posix()
    members = [
        _ArchiveMember(name=name, kind="directory", mode=0o755, data=b"")
        for name in sorted(directories)
    ]
    members.extend(
        _ArchiveMember(name=name, kind="file", mode=0o644, data=raw)
        for name, raw in sorted(files.items())
    )
    _write_canonical_archive(path, members, epoch=CANDIDATE_EPOCH)


def _refresh_package_receipt(candidate: Path, artifact: Path) -> None:
    receipt = candidate / "package-receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    for row in payload["artifacts"]:
        if row["name"] == artifact.name:
            row["size"] = artifact.stat().st_size
            row["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
            break
    else:  # pragma: no cover - fixture invariant
        raise AssertionError(f"receipt lacks {artifact.name}")
    _write(receipt, payload)


def _rewrite_wheel_with_valid_record(path: Path, files: dict[str, bytes]) -> None:
    record_names = [name for name in files if name.endswith(".dist-info/RECORD")]
    if len(record_names) != 1:
        raise AssertionError("test wheel must have exactly one RECORD")
    record_name = record_names[0]
    del files[record_name]
    rows: list[list[str]] = []
    for name, raw in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=")
        rows.append([name, f"sha256={digest.decode('ascii')}", str(len(raw))])
    rows.append([record_name, "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    files[record_name] = buffer.getvalue().encode("utf-8")
    path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            archive.writestr(name, raw)


def _fixtures(tmp_path: Path) -> dict[str, Path]:
    selection = json.loads(
        (ROOT / "commercial" / "corpus" / "selection-v1.json").read_text(
            encoding="utf-8"
        )
    )
    selection_path = tmp_path / "selection.json"
    _write(selection_path, selection)

    runtime_digest = hashlib.sha256(
        (ROOT / "ci" / "generalization-runtime-requirements.txt").read_bytes()
    ).hexdigest()
    source_files = []
    for path in sorted(
        (ROOT / "zerorun").rglob("*.py"),
        key=lambda item: (
            item.relative_to(ROOT).as_posix().casefold(),
            item.relative_to(ROOT).as_posix(),
        ),
    ):
        raw = path.read_bytes()
        source_files.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    engine_source = {
        "schema": "zerorun.engine-python-source-identity.v1",
        "files": source_files,
        "file_count": len(source_files),
        "total_bytes": sum(item["size"] for item in source_files),
    }
    engine_source["sha256"] = hashlib.sha256(
        json.dumps(
            {"schema": engine_source["schema"], "files": source_files},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    trial_path = ROOT / "tools" / "product_generalization_benchmark.py"
    trial_raw = trial_path.read_bytes()
    source_pre = {
        "schema": "zerorun.product-generalization-source-identity.v1",
        "engine_version": "0.5.1",
        "engine_python_source": engine_source,
        "trial_tool": {
            "path": "tools/product_generalization_benchmark.py",
            "size": len(trial_raw),
            "sha256": hashlib.sha256(trial_raw).hexdigest(),
        },
        "git": {
            "commit": CANDIDATE_SHA,
            "engine_python_sources_dirty": False,
            "trial_tool_dirty": False,
        },
    }
    source_pre["identity_sha256"] = hashlib.sha256(
        json.dumps(
            source_pre,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    source = {
        "stable": True,
        "pre": source_pre,
        "post": source_pre,
        "caller_expected_engine_sha": CANDIDATE_SHA,
        "caller_expected_engine_python_source_sha256": engine_source["sha256"],
        "caller_expected_trial_tool_sha256": source_pre["trial_tool"]["sha256"],
    }
    workloads = []
    for name, holdout_class in release.EXPECTED_WORKLOADS.items():
        details = release.EXPECTED_WORKLOAD_DETAILS[name]
        corpus = copy.deepcopy(release.EXPECTED_CORPUS_COMMITS[name])
        seed_material = json.dumps(
            {
                "methodology": release.PERFORMANCE_METHODOLOGY,
                "workload": name,
                "frozen_sha": details["frozen_sha"],
                "targets": details["targets"],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        seed_digest = hashlib.sha256(seed_material.encode("utf-8")).hexdigest()
        orders = ["plain-then-zerorun", "zerorun-then-plain"]
        if int(seed_digest[:2], 16) & 1:
            orders.reverse()
        transition_rows = []
        for index in range(1, 21):
            collection_digest = "a" * 64
            repetitions = [
                {
                    "trajectory": trajectory,
                    "order": orders[trajectory - 1],
                    "case": index,
                    "commit": corpus[index],
                    "previous_commit": corpus[index - 1],
                    "direct_exit_code": 0,
                    "zerorun_exit_code": 0,
                    "direct_transition_wall_ms": 50.0,
                    "zerorun_transition_wall_ms": 5.0,
                    "direct_phase_ms": {
                        "end_to_end_container_wall_including_collection_execution_and_cleanup": 50.0
                    },
                    "zerorun_phase_ms": {},
                    "zerorun_phase_ms_emitted": False,
                    "zerorun_timing_ms": {
                        "outer_wall": 5.0,
                        "product_reported_wall": 5.0,
                        "execution": 5.0,
                        "collection_overhead": 0.0,
                        "single_pass_wall": 5.0,
                        "avoided_execution_reference": 50.0,
                    },
                    "zerorun_status": "PYTEST_INCREMENTAL_PASS",
                    "reused_nodes": 1,
                    "fresh_nodes": 0,
                    "unknown_nodes": 0,
                    "total_nodes": 1,
                    "published_nodes": 0,
                    "reuse_authorized": True,
                    "zerorun_collection_sha256": collection_digest,
                    "profile_refresh_required": False,
                    "profile_refresh_fail_closed": True,
                    "profile_refresh_reasons": [],
                    "comparison": _comparison(
                        node_count=1, reused_nodes=1, digest_seed="a"
                    ),
                }
                for trajectory in (1, 2)
            ]
            transition_rows.append(
                {
                    "case": index,
                    "commit": corpus[index],
                    "previous_commit": corpus[index - 1],
                    "direct_exit_code": 0,
                    "zerorun_exit_code": 0,
                    "direct_ms": 100.0,
                    "zerorun_ms": 10.0,
                    "primary_end_to_end_plain_pytest_ms": 100.0,
                    "primary_end_to_end_zerorun_ms": 10.0,
                    "steady_state_plain_pytest_transition_ms": 100.0,
                    "steady_state_zerorun_transition_ms": 10.0,
                    "direct_runner_independent_of_zerorun_internals": True,
                    "direct_collection_separately_measured": False,
                    "direct_collection_included_in_uninstrumented_single_pass_wall": True,
                    "repetition_count_per_arm": 2,
                    "all_repetition_comparisons_pass": True,
                    "counterbalanced_repetitions": repetitions,
                    "reused_nodes": 2,
                    "fresh_nodes": 0,
                    "unknown_nodes": 0,
                    "total_nodes": 2,
                    "published_nodes": 0,
                    "reuse_authorized": True,
                    "zerorun_status": "PYTEST_INCREMENTAL_PASS",
                    "direct_collection_sha256": collection_digest,
                    "zerorun_collection_sha256": collection_digest,
                    "direct_phase_ms": {
                        "timed_transition_outer_wall_sum": 100.0,
                        "amortized_environment_and_direct_seed": 0.0,
                        "primary_end_to_end_paired_total": 100.0,
                    },
                    "zerorun_phase_ms": {},
                    "zerorun_phase_ms_emitted": False,
                    "zerorun_timing_ms": {
                        "outer_wall": 10.0,
                        "product_reported_wall": 10.0,
                        "execution": 10.0,
                        "collection_overhead": 0.0,
                        "single_pass_wall": 10.0,
                        "avoided_execution_reference": 100.0,
                        "amortized_environment_qualification_activation_and_seed": 0.0,
                        "primary_end_to_end_paired_total": 10.0,
                    },
                    "order_pair_range_ms": {"plain_pytest": 0.0, "zerorun": 0.0},
                    "reused_node_fresh_shadow_coverage": 2,
                    "reused_node_fresh_shadow_denominator": 2,
                }
            )
        cost_ledger = {component: 0.0 for component in release.PRIMARY_COST_COMPONENTS}
        cache_state = {
            "schema": "zerorun.cache-publication-state.v1",
            "sha256": "0" * 64,
            "file_count": 0,
            "directory_count": 0,
            "total_bytes": 0,
        }
        adverse_audits = [
            {
                "status": "PASS",
                "pass": True,
                "kind": "transient-changed-source-collection-failure",
                "target": details["targets"][0],
                "order": order,
                "direct_exit_code": 2,
                "zerorun_exit_code": 2,
                "independent_shadow_exit_code": 2,
                "direct_wall_ms": 1.0,
                "zerorun_wall_ms": 1.0,
                "independent_shadow_wall_ms": 1.0,
                "zerorun_stale_success": False,
                "zerorun_status": "PYTEST_INCREMENTAL_FAIL",
                "zerorun_reused_nodes": 0,
                "zerorun_published_nodes": 0,
                "zerorun_reuse_authorized": False,
                "profile_refresh_required": False,
                "profile_refresh_reasons": [],
                "fail_closed_refusal_semantics": True,
                "cache_publication_state_before": copy.deepcopy(cache_state),
                "cache_publication_state_after": copy.deepcopy(cache_state),
                "cache_publication_state_unchanged": True,
                "source_restored_to_frozen_bytes": True,
                "included_in_performance_timing": False,
            }
            for order in orders
        ]
        dependency_construction_contract = {
            "schema": release.PERFORMANCE_DEPENDENCY_LAYER_SCHEMA,
            "methodology_version": release.PERFORMANCE_METHODOLOGY,
            "runtime": {
                "image": release.PERFORMANCE_RUNTIME_IMAGE,
                "platform": "linux/amd64",
                "python_implementation": release.PERFORMANCE_RUNTIME_IMPLEMENTATION,
                "python_implementation_id": "cpython",
                "python_version": release.PERFORMANCE_RUNTIME_PYTHON_VERSION,
            },
            "requirements": {
                "path": "ci/generalization-runtime-requirements.txt",
                "size": (
                    ROOT / "ci" / "generalization-runtime-requirements.txt"
                ).stat().st_size,
                "sha256": runtime_digest,
            },
            "extra_requirements": details["extra_requirements"],
            "installer": {
                "entrypoint": ["/usr/local/bin/python", "-I", "-c"],
                "bootstrap_sha256": (
                    release.PERFORMANCE_DEPENDENCY_BOOTSTRAP_SHA256
                ),
                "arguments": release.PERFORMANCE_DEPENDENCY_INSTALL_ARGUMENTS,
                "pull_policy": "never",
                "container_user": "exact-private-staging-owner",
                "capabilities": "drop-all",
                "no_new_privileges": True,
                "resource_args": release.PERFORMANCE_RESOURCE_ARGS,
            },
            "wrapper_sha256": release.PERFORMANCE_DEPENDENCY_WRAPPER_SHA256,
            "manifest_sha256": release.PERFORMANCE_DEPENDENCY_MANIFEST_SHA256,
        }
        dependency_construction_sha256 = hashlib.sha256(
            json.dumps(
                dependency_construction_contract,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        workloads.append(
            {
                "schema": "zerorun.product-generalization.v1",
                "workload": name,
                "holdout_class": holdout_class,
                "upstream_repo": details["upstream_repo"],
                "frozen_sha": details["frozen_sha"],
                "targets": details["targets"],
                "extra_requirements": details["extra_requirements"],
                "engine_sha": CANDIDATE_SHA,
                "runtime_requirements_sha256": runtime_digest,
                "frozen_dependency_layer": {
                    "schema": release.PERFORMANCE_DEPENDENCY_LAYER_SCHEMA,
                    "construction_contract": dependency_construction_contract,
                    "construction_identity_sha256": (
                        dependency_construction_sha256
                    ),
                    "content_tree_sha256": "2" * 64,
                    "content_addressed_directory_name": "2" * 64,
                    "runtime_image": release.PERFORMANCE_RUNTIME_IMAGE,
                    "runtime_requirements_sha256": runtime_digest,
                    "extra_requirements": details["extra_requirements"],
                    "installer_bootstrap_sha256": (
                        release.PERFORMANCE_DEPENDENCY_BOOTSTRAP_SHA256
                    ),
                    "installer_arguments_sha256": (
                        release.PERFORMANCE_DEPENDENCY_INSTALL_ARGUMENTS_SHA256
                    ),
                    "wrapper_sha256": (
                        release.PERFORMANCE_DEPENDENCY_WRAPPER_SHA256
                    ),
                    "manifest_sha256": (
                        release.PERFORMANCE_DEPENDENCY_MANIFEST_SHA256
                    ),
                    "file_count": 2,
                    "directory_count": 1,
                    "total_bytes": 1,
                    "path_bytes": 1,
                    "read_only_mode_and_content_verified": True,
                    "source_hardlinks_rejected": True,
                    "build_count": 1,
                    "build_once_ms": 0.0,
                    "content_addressed": True,
                    "private_materialization_count": 2,
                    "private_materialization_method": (
                        "exclusive-byte-copy-no-hardlinks"
                    ),
                    "shared_writable_dependency_state": False,
                    "independent_result_cache_count": 2,
                    "cleanup_confirmed_before_receipt_return": True,
                    "timing_ms": {
                        "build_once": 0.0,
                        "private_materialization_and_verification_total": 0.0,
                        "total_charged_symmetrically_to_each_arm": 0.0,
                    },
                },
                "runtime_image": release.PERFORMANCE_RUNTIME_IMAGE,
                "runtime_python_implementation": (
                    release.PERFORMANCE_RUNTIME_IMPLEMENTATION
                ),
                "runtime_python_version": release.PERFORMANCE_RUNTIME_PYTHON_VERSION,
                "runtime_python_attestation": release.PERFORMANCE_RUNTIME_ATTESTATION,
                "frozen_runtime_environment": True,
                "container_resource_envelope": {
                    "docker_args": release.PERFORMANCE_RESOURCE_ARGS,
                    "identical_for_plain_pytest_and_zerorun": True,
                },
                "methodology_version": release.PERFORMANCE_METHODOLOGY,
                "trajectory_schedule": {
                    "seed_material": seed_material,
                    "seed_sha256": seed_digest,
                    "execution_order": orders,
                    "counterbalance_complete": True,
                },
                "case_count": 20,
                "primary_horizon_transitions": 20,
                "counterbalanced_trajectory_count": 2,
                "repetitions_per_transition_per_arm": 2,
                "corpus_commits": corpus,
                "candidate_sha256": "c" * 64,
                "candidate_node_count": 1,
                "candidate_reviewable_nodes": 1,
                "candidate_fresh_required_nodes": 0,
                "activation_candidate_sha256": "c" * 64,
                "review_record_sha256": "d" * 64,
                "activation_evidence_kind": "mechanical-synthetic-formative-fixture",
                "external_hmac_authority_created": False,
                "reference_gate": dict(release.REFERENCE_GATE),
                "reference_gate_metric": "end_to_end_plain_pytest_to_zerorun_speedup",
                "reference_gate_requires_both_thresholds_per_workload": True,
                "primary_metric": "end_to_end_plain_pytest_to_zerorun_speedup",
                "safety_pass": True,
                "performance_gate_pass": True,
                "timing_semantics": {
                    "direct": "primary end-to-end: uninstrumented ordinary pytest",
                    "zerorun": "primary end-to-end including qualification",
                    "shadow_oracle": "fresh shadow excluded from timing",
                    "gate_inputs_unchanged": True,
                    "compatibility_aliases_equal_primary_end_to_end": True,
                },
                "cost_ledger_ms": cost_ledger,
                "primary_cost_accounting": {
                    "schema": "zerorun.primary-end-to-end-cost-accounting.v1",
                    "required_components": release.PRIMARY_COST_COMPONENTS,
                    "all_required_components_present": True,
                    "all_components_finite_and_nonnegative": True,
                    "direct_overhead_before_horizon_amortization_ms": 0.0,
                    "zerorun_overhead_before_horizon_amortization_ms": 0.0,
                    "horizon_transitions": 20,
                    "reconciled_before_gate_evaluation": True,
                },
                "excluded_validation_wall_ms": {
                    "independent_fresh_shadow_excluded_from_primary_end_to_end": 100.0,
                    "adverse_audits_excluded_from_primary_end_to_end": 20.0,
                },
                "profile_refresh": {
                    "required_observations": 0,
                    "automatic_refreshes_performed": 0,
                    "fail_closed_violations": 0,
                    "runtime_refresh_cost_ms": 0.0,
                    "external_human_review_time_measured": False,
                },
                "trajectory_records": [
                    {
                        "trajectory": trajectory,
                        "order": orders[trajectory - 1],
                        "isolated_checkout_and_cache": True,
                        "environment_bootstrap_ms": 0.0,
                        "environment_materialization": {
                            "schema": release.PERFORMANCE_MATERIALIZATION_SCHEMA,
                            "content_tree_sha256": "2" * 64,
                            "construction_identity_sha256": (
                                dependency_construction_sha256
                            ),
                            "manifest_sha256": (
                                release.PERFORMANCE_DEPENDENCY_MANIFEST_SHA256
                            ),
                            "private_copy_ms": 0.0,
                            "verification_ms": 0.0,
                            "manifest_write_and_verify_ms": 0.0,
                            "timing_remainder_ms": 0.0,
                            "private_byte_copy": True,
                            "shared_hardlinks": False,
                            "read_only_mode_and_content_verified": True,
                            "isolated_result_cache": True,
                        },
                        "qualification_ms": 0.0,
                        "activation_ms": 0.0,
                        "direct_seed_ms": 0.0,
                        "zerorun_seed_ms": 0.0,
                        "seed_comparison": _comparison(
                            node_count=1, reused_nodes=0, digest_seed="e"
                        ),
                        "candidate_sha256": "c" * 64,
                        "candidate_node_count": 1,
                        "candidate_reviewable_nodes": 1,
                        "candidate_fresh_required_nodes": 0,
                        "activation_candidate_sha256": "c" * 64,
                        "review_record_sha256": "d" * 64,
                        "transition_count": 20,
                        "profile_refresh_required_observations": 0,
                        "profile_refresh_fail_closed_violations": 0,
                        "automatic_refreshes_performed": 0,
                        "adverse_audit": copy.deepcopy(adverse_audits[trajectory - 1]),
                    }
                    for trajectory in (1, 2)
                ],
                "adverse_audits": copy.deepcopy(adverse_audits),
                "adverse_audits_pass": True,
                "direct_total_ms": 2000.0,
                "zerorun_total_ms": 200.0,
                "end_to_end_plain_pytest_to_zerorun_speedup": 10.0,
                "same_runner_compute_efficiency": 10.0,
                "steady_state_plain_pytest_total_ms": 2000.0,
                "steady_state_zerorun_total_ms": 200.0,
                "direct_p95_ms": 100.0,
                "zerorun_p95_ms": 10.0,
                "end_to_end_p95_reduction_percent": 90.0,
                "p95_reduction_percent": 90.0,
                "p95_estimation": {
                    "estimator": "nearest-rank-on-counterbalanced-paired-transition-totals",
                    "sample_size": 20,
                    "rank": 19,
                    "independent_repository_count": 1,
                    "repetitions_per_transition_per_arm": 2,
                    "confidence_interval_reported": False,
                },
                "reused_nodes": 40,
                "fresh_nodes": 0,
                "unknown_nodes": 0,
                "total_nodes": 40,
                "published_nodes": 0,
                "reuse_rate_percent": 100.0,
                "recovery_runs": 0,
                "stale_successes": 0,
                "shadow_mismatches": 0,
                "cache_conflicts": 0,
                "direct_failures": 0,
                "zerorun_failures": 0,
                "source_identity": copy.deepcopy(source),
                "rows": transition_rows,
            }
        )
    performance = {
        "schema": release.PERFORMANCE_SCHEMA,
        "engine_sha": CANDIDATE_SHA,
        "runtime_requirements_sha256": runtime_digest,
        "activation_evidence_kind": release.PERFORMANCE_ACTIVATION_EVIDENCE_KIND,
        "protocol": release.PERFORMANCE_PROTOCOL_DISCLOSURE,
        "release_gate": release.PERFORMANCE_RELEASE_GATE_DISCLOSURE,
        "reference_gate": dict(release.REFERENCE_GATE),
        "all_safety_pass": True,
        "old_holdout_regression_pass": True,
        "new_unseen_generalization_pass": True,
        "general_5x_claim_supported_by_this_suite": True,
        "external_hmac_authority_created": False,
        "workloads": workloads,
    }
    performance_path = tmp_path / "performance.json"
    _write(performance_path, performance)

    commercial_rows = [
        {
            "selection_index": index,
            "repository": row["repository"],
            "commit": row["commit"],
            "phase": "complete",
            "status": "pass",
            "installed_skill_sha256": release.EXPECTED_MANAGED_SKILL_SHA256,
            "install": {
                "schema": "zerorun-codex-install-v5",
                "integration_ready": True,
                "skill": {"installed": True, "conflict": False},
                "mcp": {"registered": True},
            },
            "mcp": {
                "server": {"name": "zerorun", "version": "0.5.1"},
                "tool_count": 7,
                "escape_rejected": True,
                "legacy_observe_test_rejected": True,
                "doctor_mode": "observe-only",
                "stats_mode": "observe-only",
            },
        }
        for index, row in enumerate(selection["repositories"])
    ]
    commercial = {
        "schema": release.COMMERCIAL_SCHEMA,
        "engine_commit": CANDIDATE_SHA,
        "engine_version": "0.5.1",
        "codex_version": release.EXPECTED_CODEX_REPORTED_VERSION,
        "selection_sha256": selection["corpus_sha256"],
        "upstream_code_executed": False,
        "attempted_repositories": 100,
        "distinct_repositories": 100,
        "minimum_successful_integrations": 90,
        "legacy_observe_test_rejections": 100,
        "gate_pass": True,
        "counts": {"pass": 100, "safe_refusal": 0, "failure": 0},
        "rows": commercial_rows,
    }
    commercial_path = tmp_path / "commercial.json"
    _write(commercial_path, commercial)

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    wheel = candidate / "zerorun-0.5.1-py3-none-any.whl"
    sdist = candidate / "zerorun-0.5.1.tar.gz"
    _build_test_wheel(wheel)
    _build_test_sdist(sdist)
    package = {
        "schema": "zerorun.release-package.v1",
        "commit": CANDIDATE_SHA,
        "source_date_epoch": CANDIDATE_EPOCH,
        "artifacts": [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in (wheel, sdist)
        ],
        "build_provenance": _test_build_provenance(),
        "installed_semantic_equivalence": _test_installed_semantic_equivalence(),
    }
    _write(candidate / "package-receipt.json", package)
    return {
        "selection": selection_path,
        "performance": performance_path,
        "commercial": commercial_path,
        "candidate": candidate,
    }


def _validate(paths: dict[str, Path]) -> dict:
    return release.validate(
        root=ROOT,
        candidate_sha=CANDIDATE_SHA,
        selection_path=paths["selection"],
        performance_path=paths["performance"],
        commercial_path=paths["commercial"],
        candidate_dir=paths["candidate"],
        full_workflow_run_id=101,
        commercial_workflow_run_id=202,
    )


def test_exact_candidate_and_both_gate_receipts_pass(tmp_path: Path) -> None:
    result = _validate(_fixtures(tmp_path))
    assert result["release_prerequisites_pass"] is True
    assert result["candidate_sha"] == CANDIDATE_SHA
    assert result["commercial"]["attempted_repositories"] == 100
    assert len(result["performance"]["workloads"]) == 5
    assert len(result["package"]["artifacts"]) == 2


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("activation_evidence_kind", "external-release-authority"),
        ("external_hmac_authority_created", True),
        ("protocol", "external independently reviewed benchmark"),
        ("release_gate", "release-grade external evidence"),
        ("activation_evidence_kind", None),
        ("protocol", None),
        ("release_gate", None),
    ],
)
def test_aggregate_performance_receipt_cannot_relabel_synthetic_evidence(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    if replacement is None:
        payload.pop(field)
    else:
        payload[field] = replacement
    _write(paths["performance"], payload)

    with pytest.raises(
        release.PrerequisiteError,
        match="synthetic/formative evidence disclosure",
    ):
        _validate(paths)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("runtime_image", "docker.io/library/python@sha256:" + "0" * 64),
        ("runtime_python_implementation", "PyPy"),
        ("runtime_python_version", "3.12.13"),
        ("runtime_python_attestation", "claimed but not measured"),
    ],
)
def test_performance_receipts_require_exact_attested_cpython_runtime(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    payload["workloads"][0][field] = value
    _write(paths["performance"], payload)

    with pytest.raises(release.PrerequisiteError, match="frozen performance contract"):
        _validate(paths)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("shared_writable_layer", "dependency-layer provenance"),
        ("forged_content_address", "dependency-layer provenance"),
        ("second_build", "dependency-layer provenance"),
        ("hardlinked_materialization", "dependency materialization"),
        ("uncharged_build", "timing does not reconcile"),
        ("unreconciled_materialization", "timings do not reconcile"),
    ],
)
def test_release_rejects_forged_or_uncharged_dependency_layer_evidence(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    workload = payload["workloads"][0]
    layer = workload["frozen_dependency_layer"]
    if mutation == "shared_writable_layer":
        layer["shared_writable_dependency_state"] = True
    elif mutation == "forged_content_address":
        layer["content_addressed_directory_name"] = "f" * 64
    elif mutation == "second_build":
        layer["build_count"] = 2
    elif mutation == "hardlinked_materialization":
        workload["trajectory_records"][0]["environment_materialization"][
            "shared_hardlinks"
        ] = True
    elif mutation == "uncharged_build":
        layer["build_once_ms"] = 1.0
        layer["timing_ms"]["build_once"] = 1.0
    elif mutation == "unreconciled_materialization":
        layer["timing_ms"]["private_materialization_and_verification_total"] = 1.0
        layer["timing_ms"]["total_charged_symmetrically_to_each_arm"] = 1.0
        workload["cost_ledger_ms"][
            "symmetric_environment_bootstrap_charged_to_each_arm"
        ] = 1.0
        workload["primary_cost_accounting"][
            "direct_overhead_before_horizon_amortization_ms"
        ] = 1.0
        workload["primary_cost_accounting"][
            "zerorun_overhead_before_horizon_amortization_ms"
        ] = 1.0
    else:  # pragma: no cover - exhaustive test fixture guard
        raise AssertionError(mutation)
    _write(paths["performance"], payload)

    with pytest.raises(release.PrerequisiteError, match=message):
        _validate(paths)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("same_runner_compute_efficiency", 4.999999, "5x/50"),
        ("p95_reduction_percent", 49.999999, "5x/50"),
        ("performance_gate_pass", False, "5x/50"),
        ("stale_successes", 1, "5x/50"),
    ],
)
def test_per_workload_thresholds_and_safety_cannot_be_overridden_by_aggregate_flag(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    payload["workloads"][0][field] = value
    _write(paths["performance"], payload)
    with pytest.raises(release.PrerequisiteError, match=message):
        _validate(paths)


def test_wrong_sha_and_unstable_measurement_are_rejected(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    payload["workloads"][2]["source_identity"]["stable"] = False
    _write(paths["performance"], payload)
    with pytest.raises(release.PrerequisiteError, match="unstable"):
        _validate(paths)


def test_frozen_first_parent_history_cannot_be_rewritten(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    workload = payload["workloads"][0]
    forged = "f" * 40
    workload["corpus_commits"][0] = forged
    workload["rows"][0]["previous_commit"] = forged
    _write(paths["performance"], payload)
    with pytest.raises(release.PrerequisiteError, match="first-parent corpus"):
        _validate(paths)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("forged_primary_speedup", "5x/50"),
        ("forged_primary_p95", "5x/50"),
        ("omitted_qualification_cost", "cost ledger"),
        ("unreconciled_trajectory_cost", "derived from trajectory"),
        ("forged_outer_row_from_slow_observation", "timing does not reconcile"),
        ("lost_counterbalanced_observation", "paired design"),
        ("incomplete_shadow_coverage", "comparison evidence"),
        ("forged_adverse_reuse", "adverse audit"),
        ("forged_shadow_digest", "comparison evidence"),
        ("forged_activation_binding", "trajectory records"),
    ],
)
def test_release_recomputes_primary_metrics_and_rejects_omitted_cost_or_oracle(
    tmp_path: Path, mutation: str, message: str
) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["performance"].read_text(encoding="utf-8"))
    workload = payload["workloads"][0]
    if mutation == "forged_primary_speedup":
        workload["end_to_end_plain_pytest_to_zerorun_speedup"] = 99.0
        workload["same_runner_compute_efficiency"] = 99.0
    elif mutation == "forged_primary_p95":
        workload["end_to_end_p95_reduction_percent"] = 99.0
        workload["p95_reduction_percent"] = 99.0
    elif mutation == "omitted_qualification_cost":
        del workload["cost_ledger_ms"][
            "zerorun_qualification_charged_to_zerorun"
        ]
    elif mutation == "unreconciled_trajectory_cost":
        workload["trajectory_records"][0]["qualification_ms"] = 100.0
    elif mutation == "forged_outer_row_from_slow_observation":
        workload["rows"][0]["counterbalanced_repetitions"][0][
            "zerorun_transition_wall_ms"
        ] = 50.0
    elif mutation == "lost_counterbalanced_observation":
        workload["rows"][0]["counterbalanced_repetitions"].pop()
    elif mutation == "incomplete_shadow_coverage":
        workload["rows"][0]["counterbalanced_repetitions"][0]["comparison"][
            "reused_node_fresh_shadow_coverage"
        ] = 0
    elif mutation == "forged_adverse_reuse":
        workload["adverse_audits"][0]["zerorun_reused_nodes"] = 1
        workload["trajectory_records"][0]["adverse_audit"] = copy.deepcopy(
            workload["adverse_audits"][0]
        )
    elif mutation == "forged_shadow_digest":
        workload["rows"][0]["counterbalanced_repetitions"][0]["comparison"][
            "independent_shadow_nodeid_sha256"
        ] = "f" * 64
    else:
        workload["activation_candidate_sha256"] = "f" * 64
    _write(paths["performance"], payload)
    with pytest.raises(release.PrerequisiteError, match=message):
        _validate(paths)


@pytest.mark.parametrize(
    ("field_path", "replacement", "message"),
    [
        (("codex_version",), "codex-cli 999.0.0", "100-repository gate"),
        (("rows", 0, "mcp", "escape_rejected"), False, "complete Codex/MCP"),
        (("rows", 0, "install", "mcp", "registered"), False, "complete Codex/MCP"),
    ],
)
def test_commercial_aggregate_requires_exact_codex_and_full_per_row_proof(
    tmp_path: Path,
    field_path: tuple[object, ...],
    replacement: object,
    message: str,
) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["commercial"].read_text(encoding="utf-8"))
    cursor: object = payload
    for key in field_path[:-1]:
        cursor = cursor[key]  # type: ignore[index]
    cursor[field_path[-1]] = replacement  # type: ignore[index]
    _write(paths["commercial"], payload)
    with pytest.raises(release.PrerequisiteError, match=message):
        _validate(paths)


def test_missing_or_duplicated_commercial_denominator_is_rejected(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["commercial"].read_text(encoding="utf-8"))
    payload["rows"][99]["selection_index"] = 98
    _write(paths["commercial"], payload)
    with pytest.raises(release.PrerequisiteError, match="invalid selection outcome"):
        _validate(paths)


def test_release_reuses_the_strict_frozen_selection_protocol(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    payload = json.loads(paths["selection"].read_text(encoding="utf-8"))
    payload["selection_seed"] = "attacker-selected-denominator"
    unhashed = dict(payload)
    unhashed.pop("corpus_sha256", None)
    payload["corpus_sha256"] = hashlib.sha256(release._canonical(unhashed)).hexdigest()
    _write(paths["selection"], payload)
    with pytest.raises(release.PrerequisiteError, match="strict protocol"):
        _validate(paths)


def test_release_receipts_reject_duplicate_json_members(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    receipt = paths["performance"]
    raw = receipt.read_text(encoding="utf-8")
    raw = raw.replace(
        '  "all_safety_pass": true,',
        '  "all_safety_pass": false,\n  "all_safety_pass": true,',
        1,
    )
    receipt.write_text(raw, encoding="utf-8")
    with pytest.raises(release.PrerequisiteError, match="duplicate JSON member"):
        _validate(paths)


def test_candidate_package_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    (paths["candidate"] / "zerorun-0.5.1-py3-none-any.whl").write_bytes(
        b"tampered wheel"
    )
    with pytest.raises(release.PrerequisiteError, match="digest mismatch"):
        _validate(paths)


def test_candidate_package_epoch_must_equal_exact_commit_time(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    receipt = paths["candidate"] / "package-receipt.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["source_date_epoch"] = CANDIDATE_EPOCH + 1
    _write(receipt, payload)
    with pytest.raises(release.PrerequisiteError, match="release candidate"):
        _validate(paths)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "identity",
        "python",
        "build_tool",
        "environment",
        "lockfile",
        "build_count",
    ],
)
def test_candidate_package_build_provenance_is_independently_validated(
    tmp_path: Path,
    mutation: str,
) -> None:
    paths = _fixtures(tmp_path)
    receipt_path = paths["candidate"] / "package-receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload.pop("build_provenance")
    else:
        provenance = payload["build_provenance"]
        if mutation == "identity":
            provenance["identity_sha256"] = "f" * 64
        elif mutation == "python":
            provenance["python"]["version"] = "3.12.12"
            _refresh_identity(provenance)
        elif mutation == "build_tool":
            provenance["build_tools"]["setuptools"] = "83.0.0"
            _refresh_identity(provenance)
        elif mutation == "environment":
            provenance["environment"]["PYTHONHASHSEED"] = "1"
            _refresh_identity(provenance)
        elif mutation == "lockfile":
            provenance["lockfiles"][0]["sha256"] = "f" * 64
            _refresh_identity(provenance)
        else:
            provenance["byte_identical_builds"] = 1
            _refresh_identity(provenance)
    _write(receipt_path, payload)

    with pytest.raises(release.PrerequisiteError, match="package (receipt|build provenance)"):
        _validate(paths)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "identity", "wheel_sdist_flag", "semantic", "module_count"],
)
def test_candidate_installed_semantic_equivalence_is_independently_validated(
    tmp_path: Path,
    mutation: str,
) -> None:
    paths = _fixtures(tmp_path)
    receipt_path = paths["candidate"] / "package-receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload.pop("installed_semantic_equivalence")
    else:
        equivalence = payload["installed_semantic_equivalence"]
        if mutation == "identity":
            equivalence["identity_sha256"] = "f" * 64
        elif mutation == "wheel_sdist_flag":
            equivalence["wheel_vs_sdist_exact"] = False
            _refresh_identity(equivalence)
        elif mutation == "semantic":
            equivalence["semantic_sha256"] = "f" * 64
            _refresh_identity(equivalence)
        else:
            equivalence["module_file_count"] += 1
            _refresh_identity(equivalence)
    _write(receipt_path, payload)

    with pytest.raises(
        release.PrerequisiteError,
        match="package (receipt|installed semantic equivalence)",
    ):
        _validate(paths)


def test_candidate_wheel_record_is_replayed_after_outer_digest_validation(
    tmp_path: Path,
) -> None:
    paths = _fixtures(tmp_path)
    wheel = paths["candidate"] / "zerorun-0.5.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "r") as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["zerorun/__init__.py"] += b"\n# forged after build\n"
    wheel.unlink()
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(files.items()):
            archive.writestr(name, raw)
    _refresh_package_receipt(paths["candidate"], wheel)
    with pytest.raises(release.PrerequisiteError, match="RECORD mismatch"):
        _validate(paths)


def test_candidate_sdist_sources_must_equal_the_exact_checkout(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    sdist = paths["candidate"] / "zerorun-0.5.1.tar.gz"
    members = list(
        _read_members(
            sdist,
            expected_root="zerorun-0.5.1",
            canonical_epoch=CANDIDATE_EPOCH,
        )
    )
    members = [
        _ArchiveMember(
            member.name,
            member.kind,
            member.mode,
            (
                member.data + b"\n# forged after build\n"
                if member.name == "zerorun-0.5.1/zerorun/__init__.py"
                else member.data
            ),
        )
        for member in members
    ]
    sdist.unlink()
    _write_canonical_archive(sdist, members, epoch=CANDIDATE_EPOCH)
    _refresh_package_receipt(paths["candidate"], sdist)
    with pytest.raises(release.PrerequisiteError, match="sources differ"):
        _validate(paths)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("extra_pth", "member manifest"),
        ("dependency_injection", "metadata contract"),
    ],
)
def test_candidate_wheel_rejects_unreviewed_executable_or_dependency_payloads(
    tmp_path: Path, mutation: str, message: str
) -> None:
    paths = _fixtures(tmp_path)
    wheel = paths["candidate"] / "zerorun-0.5.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "r") as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    if mutation == "extra_pth":
        files["zerorun_bootstrap.pth"] = b"import os; raise RuntimeError('unexpected')\n"
    else:
        metadata = "zerorun-0.5.1.dist-info/METADATA"
        files[metadata] = files[metadata].replace(
            b"Requires-Python: >=3.10\n",
            b"Requires-Python: >=3.10\nRequires-Dist: unreviewed-package\n",
        )
    _rewrite_wheel_with_valid_record(wheel, files)
    _refresh_package_receipt(paths["candidate"], wheel)
    with pytest.raises(release.PrerequisiteError, match=message):
        _validate(paths)


def test_cli_refuses_to_overwrite_a_prerequisite_receipt(tmp_path: Path) -> None:
    paths = _fixtures(tmp_path)
    output = tmp_path / "receipt.json"
    arguments = [
        "--root",
        str(ROOT),
        "--candidate-sha",
        CANDIDATE_SHA,
        "--selection",
        str(paths["selection"]),
        "--performance-receipt",
        str(paths["performance"]),
        "--commercial-receipt",
        str(paths["commercial"]),
        "--candidate-dir",
        str(paths["candidate"]),
        "--full-workflow-run-id",
        "101",
        "--commercial-workflow-run-id",
        "202",
        "--output",
        str(output),
    ]
    assert release.main(arguments) == 0
    before = output.read_bytes()
    assert release.main(arguments) == 1
    assert output.read_bytes() == before
